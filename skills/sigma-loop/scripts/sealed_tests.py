"""Seal record and the sealed-pytest-v1 red-on-base proof (#847, slice 2 of #558). Library only.

`seal()` takes the breaker's files ALREADY on disk under `tests/acceptance/<stem>/`, re-validates them
through the public `breaker.validate_output`, extracts the BASE commit (`git archive`, read-only against
the repo) into a temp dir, overlays the sealed files, runs them with pytest and demands every test
FAIL there (red on base) unless it carries a recorded exemption. It appends one witness row per red and
writes `.sdlc/state/sealed/<stem>.json` LAST and atomically, so a crash earlier leaves nothing sealed and
a re-run succeeds. `check()` re-hashes the files read-only for the later commit and PR gates (slice 3).

Idempotence key = file sha256s + plan_hash + base sha + route + exemptions. An identical re-run returns
the existing record untouched (no run, no write, no new witness rows); any difference writes a new
record with `generation` + 1 (slice 4 enforces the cap). Concurrent seals of one goal: last writer wins.

Exemptions match by prefix (`file::test` also covers `file::test[...]`), deliberately conservative: an
exemption on a parametrized test needs ONE exemption per passing-on-base case (`file::test[a]`), because
the base-name form covers every case and is refused `exemption-stale` as soon as any case is red.

pytest runs as `<python> -m pytest -q -rA --tb=short --color=no -p no:cacheprovider --rootdir=<tree>
-o addopts=`. `-o addopts=` OVERRIDES the repo's own `addopts` (a `-n auto` or `--cov` there would change
the output this module parses); `PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, `PYTHONPATH`, `PYTHONHOME` and
`VIRTUAL_ENV` are scrubbed from the env, `HOME` is a distinct subdirectory of the same temp dir.

Stated limits: sealed tests are model-authored code run with the operator's privileges and no network or
syscall sandbox (same trust as `verify.command`); local evidence only, host protection is the tamper
ceiling; a SIGKILL can leave a `sigma-sealed-*` dir in $TMPDIR (the OS temp reaper is the lever);
`atomic_write_text` has no directory fsync. Known limit: witness rows are appended BEFORE the seal record,
so a crash between them leaves orphan sealed-pytest-v1 rows (hash=None, so they can never count STRONG)
and the re-run appends duplicates of them; no behaviour change, the record is still the only authority. Per-seal time on real breaker output is unmeasured.
Host-agnostic: Python plus git, no hook.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

_MODULES = {}


def _load(name):
    """Sibling module by path (same skill), cached so repeated calls do not re-exec it."""
    if name not in _MODULES:
        spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULES[name] = module
    return _MODULES[name]


SCHEMA = "sigma-sealed/1"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_DROP_ENV = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTEST_ADDOPTS", "PYTEST_PLUGINS")
_TAIL = 1024 * 1024
#: Disk guard multiplier: archive + extracted tree + headroom. A derivation, not a measurement.
_DISK_FACTOR = 3


class SealRefused(ValueError):
    """A seal refusal. `.clause` is one of collection-error, pass-on-base, not-red, exemption-reason,
    exemption-stale, exemption-unknown, all-exempt, unexpected-entry, bad-files, bad-plan-hash,
    base-unresolved, runner-unusable, timeout, disk, no-tests-in-file, evidence-unpersisted,
    bad-route, main-checkout, record, tree-unreadable."""

    def __init__(self, clause, detail):
        super().__init__("sealed_tests: REFUSED [%s]: %s" % (clause, detail))
        self.clause = clause


def seal_path(sdlc_dir, stem):
    reason = _load("state").unsafe_goal_reason(stem)
    if reason or not isinstance(stem, str) or not stem:
        raise SealRefused("bad-files", "unsafe goal stem %r: %s" % (stem, reason))
    return pathlib.Path(sdlc_dir) / "state" / "sealed" / (stem + ".json")


def _rel(stem):
    return "tests/acceptance/%s" % stem


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def is_debris_name(rel_parts):
    """The ONE name rule for cache debris, as path parts relative to the sealed dir: anything under
    `.pytest_cache/`, or `__pycache__/<n>.pyc` directly under a `__pycache__` dir. `__pycache__/sub/x.pyc`
    and `__pycache__/x.txt` are NOT debris. Pure: no filesystem, no git; `is_debris` (working tree) and
    `check_tree` (committed tree) each add their own file-type test and then ask this."""
    parts = tuple(rel_parts)
    if parts and parts[0] == ".pytest_cache":
        return True
    return len(parts) == 2 and parts[0] == "__pycache__" and parts[1].endswith(".pyc")


def is_debris(d, name):
    """Cache debris a verify run leaves inside the sealed dir, which is neither sealed nor unexpected.
    Strict rule: a REAL (non-symlink) `.pytest_cache` directory, or a REAL `__pycache__` directory holding
    only regular `*.pyc` files. A symlink, a non-pyc file or a subdirectory inside `__pycache__` makes the
    whole entry unexpected. lstat only; nothing is followed; the name rule is `is_debris_name`."""
    import stat as _stat
    p = pathlib.Path(d) / name
    try:
        if not _stat.S_ISDIR(os.lstat(p).st_mode):
            return False
        if name == ".pytest_cache":
            return is_debris_name((name,))
        if name != "__pycache__":
            return False
        return all(_stat.S_ISREG(os.lstat(p / n).st_mode) and is_debris_name((name, n)) for n in os.listdir(p))
    except OSError:
        return False


def _scan(root, stem):
    """-> ({relpath: bytes}, [unexpected entry names]); lstat only, nothing is followed."""
    d = pathlib.Path(root) / _rel(stem)
    files, odd = {}, []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return files, odd
    import stat as _stat
    for name in names:
        if is_debris(d, name):
            continue
        mode = os.lstat(d / name).st_mode
        ok = _stat.S_ISREG(mode) and name.endswith(".py") and name not in ("conftest.py", "__init__.py")
        if ok and mode & 0o111:       # the gates demand 0644 (`check` flags the exec bit as differs): refuse HERE
            odd.append(name + " (executable bit set: chmod 644)")
        elif not ok:
            odd.append(name)
        else:
            files[_rel(stem) + "/" + name] = (d / name).read_bytes()
    return files, odd


def _git(root, *args, timeout=None):
    env = _load("bounded_run").unattended_env()
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise SealRefused("timeout", "git %s exceeded the %ss budget" % (args[0], timeout))
    except OSError as exc:
        raise SealRefused("base-unresolved", "git could not run: %s" % exc)


def _default_run(argv, cwd, timeout, env):
    return _load("bounded_run").run_group(argv, cwd, timeout, env=env, merge=True, tail_bytes=_TAIL)


def _covers(exempt, node):
    return node == exempt or node.startswith(exempt + "::") or node.startswith(exempt + "[")


def _key(rec):
    return (rec.get("files") and {k: v.get("sha256") for k, v in rec["files"].items()}, rec.get("plan_hash"),
            (rec.get("base") or {}).get("sha"), rec.get("route"),
            sorted(json.dumps(e, sort_keys=True) for e in rec.get("exemptions") or []))


def _existing(path):
    if path.is_symlink():          # a record behind a link is not the record this goal sealed
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(rec, dict) and rec.get("schema") == SCHEMA and isinstance(rec.get("files"), dict)
    return rec if ok else None


def _safe_extract(tar, dest):
    base = os.path.realpath(dest)
    for m in tar.getmembers():
        target = os.path.realpath(os.path.join(dest, m.name))
        if not (target == base or target.startswith(base + os.sep)):
            raise SealRefused("base-unresolved", "base archive member escapes the tree: %r" % m.name)
        if m.issym() or m.islnk():
            link = m.linkname
            if os.path.isabs(link) or ".." in link.split("/"):
                raise SealRefused("base-unresolved", "base archive link escapes the tree: %r" % m.name)
    if hasattr(tarfile, "data_filter"):
        tar.extractall(dest, filter="data")
    else:
        for m in tar.getmembers():
            if not (m.isreg() or m.isdir() or m.issym() or m.islnk()):
                raise SealRefused("base-unresolved", "base archive member is a device/FIFO/special file: %r" % m.name)
        tar.extractall(dest)


def _step(run, argv, cwd, deadline, env, what):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SealRefused("timeout", "the budget ran out before %s" % what)
    r = run(argv, cwd, remaining, env)
    bounded = _load("bounded_run")
    if r.outcome == bounded.TIMEOUT:
        raise SealRefused("timeout", "%s exceeded the budget" % what)
    if r.outcome in (bounded.REFUSED, bounded.ERROR, bounded.STOPPED, bounded.UNTRUSTED, bounded.NO_COMMAND):
        raise SealRefused("runner-unusable", "%s: %s %s" % (what, r.outcome, r.detail))
    return r


def seal(root, sdlc_dir, stem, *, base_ref, plan_hash, route, timeout, exemptions=None, python=None,
         run=None, now=None, free_bytes=None):
    """Seal `tests/acceptance/<stem>/` against `base_ref`. -> the record dict; raises SealRefused."""
    state, witness, breaker, rg, bounded = (_load(n) for n in ("state", "witness", "breaker", "red_green",
                                                               "bounded_run"))
    sdlc_dir = os.path.abspath(sdlc_dir)
    path = seal_path(sdlc_dir, stem)
    try:   # before any side effect: a witness row appended then a failed record write is unrecoverable
        json.dumps({"route": route, "exemptions": dict(exemptions or {})}, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise SealRefused("bad-route", "route/exemptions are not JSON-serialisable: %s" % exc)
    if not (isinstance(plan_hash, str) and _HEX64.fullmatch(plan_hash)):
        raise SealRefused("bad-plan-hash", "plan_hash must be 64 lowercase hex characters")
    try:
        budget = bounded.seconds(timeout)
    except ValueError as exc:
        raise SealRefused("timeout", str(exc))
    deadline = time.monotonic() + budget
    exemptions = dict(exemptions or {})
    for node, reason in exemptions.items():
        if not (isinstance(reason, str) and reason.strip()):
            raise SealRefused("exemption-reason", "exemption for %s needs a non-empty reason" % node)
    exemptions = {n: r.strip() for n, r in exemptions.items()}
    # files: lstat scan, then the breaker's own contract on the rebuilt text
    files, odd = _scan(root, stem)
    if odd:
        raise SealRefused("unexpected-entry", "not a plain sealed test file in %s/: %s" % (_rel(stem), ", ".join(odd)))
    if not files:
        raise SealRefused("bad-files", "no sealed test files in %s/" % _rel(stem))
    blocks = []
    for rel, data in files.items():
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise SealRefused("bad-files", "%s is not valid UTF-8" % rel)
        blocks.append("=== FILE: %s ===\n%s\n=== END FILE ===\n" % (rel, text[:-1] if text.endswith("\n") else text))
    ok, findings = breaker.validate_output("".join(blocks), stem)
    if not ok:
        raise SealRefused("bad-files", "; ".join(findings))
    hashes = {rel: {"sha256": _sha256(d), "bytes": len(d)} for rel, d in files.items()}
    # base: safe_ref returns an inert sentinel (never raises), so refuse explicitly
    ref = state.safe_ref("base_ref", base_ref)
    if not isinstance(ref, str) or not ref or ref == state.INERT_REF:
        raise SealRefused("base-unresolved", "base ref %r is missing or unsafe" % (base_ref,))
    proc = _git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}", timeout=budget)
    sha = proc.stdout.strip()
    if proc.returncode or not _SHA.fullmatch(sha):
        raise SealRefused("base-unresolved", "%r does not resolve to a commit" % ref)
    plan = [{"test": n, "reason": r} for n, r in sorted(exemptions.items())]
    prior = _existing(path)
    wanted = {"files": hashes, "plan_hash": plan_hash, "base": {"sha": sha}, "route": route, "exemptions": plan}
    if prior is not None and _key(prior) == _key(wanted):
        return prior
    generation = prior["generation"] + 1 if prior and isinstance(prior.get("generation"), int) else 1
    # disk guard, derived from the machine
    listing = _git(root, "ls-tree", "-r", "-l", sha, timeout=budget)
    if listing.returncode:
        raise SealRefused("disk", "git ls-tree failed (rc %s), so the base size is unknown: %s"
                          % (listing.returncode, (listing.stderr or "").strip()[:200]))
    total = sum(int(ln.split()[3]) for ln in listing.stdout.splitlines()
                if len(ln.split()) >= 4 and ln.split()[3].isdigit())
    free = free_bytes if free_bytes is not None else shutil.disk_usage(tempfile.gettempdir()).free
    if free < _DISK_FACTOR * total:
        raise SealRefused("disk", "%d bytes free; extracting the base needs about %d (3x %d)"
                          % (free, _DISK_FACTOR * total, total))
    run = run or _default_run
    python = python or sys.executable
    with tempfile.TemporaryDirectory(prefix="sigma-sealed-") as tmp:
        tmp = os.path.realpath(tmp)   # a symlinked $TMPDIR (macOS /var) makes pytest print ../../ node ids
        tree = os.path.join(tmp, "tree")
        tar_path = os.path.join(tmp, "base.tar")
        arch = _git(root, "archive", "--format=tar", "-o", tar_path, sha, timeout=max(deadline - time.monotonic(), 1))
        if arch.returncode:
            raise SealRefused("base-unresolved", "git archive failed: %s" % arch.stderr.strip()[:200])
        with tarfile.open(tar_path) as tar:
            _safe_extract(tar, tree)
        dest = os.path.join(tree, *_rel(stem).split("/"))
        if os.path.lexists(dest):
            raise SealRefused("unexpected-entry", "the base commit already contains %s/" % _rel(stem))
        shutil.copytree(os.path.join(str(root), *_rel(stem).split("/")), dest, symlinks=True, dirs_exist_ok=True,
                        ignore=lambda src, names: [n for n in names if is_debris(src, n)])
        for rel, meta in hashes.items():
            copied = pathlib.Path(tree, *rel.split("/"))
            if copied.is_symlink() or not copied.is_file() or _sha256(copied.read_bytes()) != meta["sha256"]:
                raise SealRefused("bad-files", "%s changed while it was being sealed" % rel)
        env = {k: v for k, v in bounded.unattended_env().items() if k not in _DROP_ENV}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["COLUMNS"] = "240"        # pytest truncates the FAILED reason to the terminal width
        env["HOME"] = tempfile.mkdtemp(prefix="home-", dir=tmp)
        base_args = [python, "-m", "pytest"]
        ver = _step(run, base_args + ["--version"], tree, deadline, env, "pytest --version")
        if ver.code != 0:
            raise SealRefused("runner-unusable", "%s -m pytest --version exited %s" % (python, ver.code))
        match = re.search(r"pytest\s+(\S+)", ver.out or "")
        version = match[1] if match else "unknown"
        common = ["-q", "--color=no", "-p", "no:cacheprovider", "--rootdir=" + tree, "-o", "addopts="]
        col = _step(run, base_args + common + ["--collect-only", _rel(stem)], tree, deadline, env, "collection")
        nodes = sorted({ln.strip() for ln in (col.out or "").splitlines()
                        if "::" in ln and ln.strip().startswith(_rel(stem) + "/")})
        verdict = rg.classify_sealed(col.code, col.out, None, "", nodes)
        if verdict["collection_errors"]:
            raise SealRefused("collection-error", "collection failed in: " + ", ".join(verdict["collection_errors"]))
        if verdict["error"]:
            raise SealRefused("collection-error", verdict["error"])
        empty = [rel for rel in hashes if not any(n.startswith(rel + "::") for n in nodes)]
        if empty:
            raise SealRefused("no-tests-in-file", "sealed file contributes zero collected tests: " + ", ".join(empty))
        unknown = [e for e in exemptions if not any(_covers(e, n) for n in nodes)]
        if unknown:
            raise SealRefused("exemption-unknown", "exemption names no collected test: " + ", ".join(sorted(unknown)))
        if all(any(_covers(e, n) for e in exemptions) for n in nodes):
            raise SealRefused("all-exempt", "every collected test is exempt; zero red proves nothing")
        out = _step(run, base_args + ["-rA", "--tb=short"] + common + nodes, tree, deadline, env, "the red run")
        verdict = rg.classify_sealed(col.code, col.out, out.code, out.out, nodes)
        if verdict["error"]:
            raise SealRefused("runner-unusable", verdict["error"])
    exempt = lambda n: any(_covers(e, n) for e in exemptions)  # noqa: E731
    bad = [n for n in verdict["other"]]
    if bad:
        raise SealRefused("not-red", "neither red nor passed (skipped, xfail, setup error or absent): " + ", ".join(bad))
    stale = sorted(e for e in exemptions if any(_covers(e, n) for n in verdict["red"]))
    if stale:
        raise SealRefused("exemption-stale", "exempted test actually fails on base: " + ", ".join(stale))
    passing = [n for n in verdict["passed"] if not exempt(n)]
    if passing:
        raise SealRefused("pass-on-base", "sealed test passes on base: " + ", ".join(passing))
    stamp = float(now) if now is not None else time.time()
    red = []
    for node, (kind, reason) in sorted(verdict["red"].items()):
        file_hash = hashes[node.split("::")[0]]["sha256"]
        # hash=None: a sealed row must never be able to count STRONG in witness.verdict (cross-contamination)
        row = witness.record(sdlc_dir, stem, node, kind, None, detail=reason or "FAILED",
                             provenance=rg.SEALED_PROVENANCE, file_hash=file_hash, observed_at=stamp, now=stamp)
        if row is None:
            raise SealRefused("evidence-unpersisted", "could not append the witness row for %s" % node)
        canon = json.dumps({"test": node, "file_hash": file_hash, "observed_at": stamp,
                            "provenance": rg.SEALED_PROVENANCE}, sort_keys=True)
        red.append({"test": node, "evidence_id": _sha256(canon.encode())[:16], "kind": kind, "reason": reason})
    record = {"schema": SCHEMA, "goal": stem, "sealed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp)),
              "generation": generation, "plan_hash": plan_hash, "route": route,
              "base": {"ref": ref, "sha": sha}, "dir": _rel(stem) + "/", "files": hashes, "red": red,
              "exemptions": plan,
              "proof": {"provenance": rg.SEALED_PROVENANCE, "python": python, "pytest": version, "exit": out.code}}
    try:
        state.refuse_symlinks(sdlc_dir, path, create_parents=True)
        state.atomic_write_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n")   # LAST
    except OSError as exc:
        raise SealRefused("evidence-unpersisted", "could not write the seal record: %s" % exc)
    return record


def check(root, sdlc_dir, stem):
    """Read-only. -> {"sealed": bool, "problems": [{"path", "kind"}]}, kind missing|differs|unexpected|record.
    An absent, unparseable, foreign or symlinked record is `sealed: False` with a `record` problem, never "ok".

    CONTRACT (#850): a sealed file with ANY executable bit is `differs`, so the working-tree compare here
    and the committed-tree compare in `check_tree` (git mode must be exactly 100644) report the same set.
    The obligation to produce 0644 files sits at SEAL time: `_scan` refuses an executable file, so a 0755
    file never gets sealed in the first place. Limit: on a filesystem that reports every file as executable
    (some mounted volumes) `chmod 644` cannot clear it and sealing refuses loudly there."""
    sdlc_dir = os.path.abspath(sdlc_dir)
    path = seal_path(sdlc_dir, stem)
    rec = _existing(path)
    if rec is None:
        return {"sealed": False, "problems": [{"path": str(path), "kind": "record"}]}
    prefix = _rel(stem) + "/"
    problems = []
    try:  # the sealed directory itself must be a real directory, not a symlink/file standing in for it
        import stat as _stat
        if not _stat.S_ISDIR(os.lstat(pathlib.Path(root, *prefix.rstrip("/").split("/"))).st_mode):
            problems.append({"path": prefix.rstrip("/"), "kind": "differs"})
    except OSError:
        pass  # absent: the per-file `missing` problems say so
    for rel, meta in sorted(rec["files"].items()):
        if not (isinstance(rel, str) and rel.startswith(prefix) and ".." not in rel.split("/")
                and isinstance(meta, dict)):
            problems.append({"path": str(rel), "kind": "record"})
            continue
        full = pathlib.Path(root, *rel.split("/"))
        try:
            import stat as _stat
            mode = os.lstat(full).st_mode
        except OSError:
            problems.append({"path": rel, "kind": "missing"})
            continue
        try:
            same = (_stat.S_ISREG(mode) and not mode & 0o111
                    and _sha256(full.read_bytes()) == meta.get("sha256"))
        except OSError:
            same = False
        if not same:
            problems.append({"path": rel, "kind": "differs"})
    try:
        present = sorted(os.listdir(pathlib.Path(root, *prefix.rstrip("/").split("/"))))
    except OSError:
        present = []
    droot = pathlib.Path(root, *prefix.rstrip("/").split("/"))
    for name in present:
        if prefix + name not in rec["files"] and not is_debris(droot, name):
            problems.append({"path": prefix + name, "kind": "unexpected"})
    return {"sealed": not problems, "problems": problems}


def is_sealed(sdlc_dir, stem):
    """Activation test for the commit/PR gates: does a seal record EXIST for this goal. Zero git calls, zero
    pytest. `abspath` first, so a relative `.sdlc` means the same thing for every caller; an unsafe stem
    is False (work.py already refuses unsafe stems at `record_path`). Existence only: a corrupt record is
    still "sealed" here, and `check`/`check_tree` then report the `record` problem."""
    try:
        return os.path.lexists(seal_path(os.path.abspath(sdlc_dir), stem))
    except SealRefused:
        return False


def _git_bytes(root, *args, timeout=None):
    """`git -C root ...` with BYTES stdout (text mode, or a `.strip()`, would alter a blob's trailing
    newline and so its sha256)."""
    env = _load("bounded_run").unattended_env()
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise SealRefused("timeout", "git %s exceeded the %ss budget" % (args[0], timeout))
    except OSError as exc:
        raise SealRefused("tree-unreadable", "git could not run: %s" % exc)


_FILTER_ATTRS = ("text", "eol", "filter", "ident", "working-tree-encoding")


def _filter_explanation(root, rel):
    """"" unless a text/eol/filter attribute or core.autocrlf is in play for `rel`: then the sentence that
    explains a blob-vs-working-bytes mismatch. Consulted ONLY after a mismatch, so a repo with
    `* text=auto` and LF content is never refused for having attributes. Limits: `check-attr` reads
    `.gitattributes` from the worktree/index, not from the compared rev, and a differing
    `core.attributesFile` on another machine is not seen."""
    try:
        attrs = _git_bytes(root, "check-attr", "-z", *_FILTER_ATTRS, "--", rel, timeout=30)
        fields = attrs.stdout.decode("utf-8", "replace").split("\0")
        set_attrs = [fields[i + 1] for i in range(0, len(fields) - 2, 3) if fields[i + 2] != "unspecified"]
        crlf = _git_bytes(root, "config", "--get", "core.autocrlf", timeout=30).stdout.strip()
    except (SealRefused, OSError):
        return ""
    if not (set_attrs or crlf):
        return ""
    return ("stored blob differs from the working bytes; text/eol/filter attributes or core.autocrlf may be "
            "converting it; lever: set `-text` for the sealed dir in .gitattributes, or park the goal")


def check_tree(root, sdlc_dir, stem, rev="HEAD", *, timeout=120):
    """Read-only, like `check`, but of the COMMITTED tree at `rev` (default HEAD), not the working tree.
    -> {"sealed": bool, "problems": [{"path", "kind"[, "why"]}]}, the same kinds as `check`.

    One `git ls-tree -r -l -z <rev> -- tests/acceptance/<stem>`: every recorded file must be a regular
    100644 blob (a symlink, gitlink or 100755 is `differs`; the mode is read BEFORE the size, because a
    gitlink's size column is `-`), whose size equals the record's (a mismatch is `differs` without reading
    the blob, which bounds memory) and whose bytes, read with `git cat-file blob` as BYTES, hash to the
    recorded sha256. Extra paths are `unexpected` unless `is_debris_name` says debris and the entry is a
    regular blob. A tree entry AT the sealed directory path (symlink or blob) is `differs` on that path.
    Only a mismatch consults `check-attr`/`core.autocrlf` (see `_filter_explanation`) to explain it."""
    sdlc_dir = os.path.abspath(sdlc_dir)
    rec = _existing(seal_path(sdlc_dir, stem))
    if rec is None:
        return {"sealed": False, "problems": [{"path": str(seal_path(sdlc_dir, stem)), "kind": "record"}]}
    prefix = _rel(stem) + "/"
    done = _git_bytes(root, "ls-tree", "-r", "-l", "-z", rev, "--", prefix.rstrip("/"), timeout=timeout)
    if done.returncode:
        raise SealRefused("tree-unreadable", "git ls-tree %s failed (rc %s): %s"
                          % (rev, done.returncode, done.stderr.decode("utf-8", "replace").strip()[:200]))
    entries = {}
    for row in done.stdout.decode("utf-8", "replace").split("\0"):
        head, _, path = row.partition("\t")
        fields = head.split()
        if len(fields) == 4 and path:
            entries[path] = (fields[0], fields[2], fields[3])      # mode, oid, size (`-` for a gitlink)
    problems = []
    if prefix.rstrip("/") in entries:                              # the dir path is itself a symlink or blob
        problems.append({"path": prefix.rstrip("/"), "kind": "differs"})
    for rel, meta in sorted(rec["files"].items()):
        if not (isinstance(rel, str) and rel.startswith(prefix) and ".." not in rel.split("/")
                and isinstance(meta, dict)):
            problems.append({"path": str(rel), "kind": "record"})
            continue
        if rel not in entries:
            problems.append({"path": rel, "kind": "missing"})
            continue
        mode, oid, size = entries[rel]
        if mode != "100644":                                       # BEFORE the size parse: a gitlink's is `-`
            problems.append({"path": rel, "kind": "differs"})
            continue
        want_bytes = meta.get("bytes")
        if isinstance(want_bytes, int) and size.isdigit() and int(size) != want_bytes:
            problems.append({"path": rel, "kind": "differs"})      # no blob read
        else:
            blob = _git_bytes(root, "cat-file", "blob", oid, timeout=timeout)
            if blob.returncode == 0 and _sha256(blob.stdout) == meta.get("sha256"):
                continue
            problems.append({"path": rel, "kind": "differs"})
        why = _filter_explanation(root, rel)
        if why:
            problems[-1]["why"] = why
    for path, (mode, _oid, _size) in sorted(entries.items()):
        if path in rec["files"] or path == prefix.rstrip("/"):
            continue
        if mode in ("100644", "100755") and path.startswith(prefix) \
                and is_debris_name(path[len(prefix):].split("/")):
            continue
        problems.append({"path": path, "kind": "unexpected"})
    return {"sealed": not problems, "problems": problems}


def _usable(python):
    return isinstance(python, str) and bool(python) and os.path.isfile(python) and os.access(python, os.X_OK)


def run_green(root, sdlc_dir, stem, *, timeout, run=None):
    """Run the sealed tests in the goal worktree `root` and -> {"green": bool, "failing": [{"test","why"}],
    "detail": str}. NEVER raises for a verdict; raises SealRefused only for runner-unusable, timeout,
    main-checkout or record. Green means: pytest exited 0, no collection error, no FAILED node, nothing
    SKIPPED/XFAIL/XPASS/ERROR/absent (`red_green.classify_sealed`'s `other`), and every test the seal
    recorded red is now PASSED (so a deleted test is not green). Exempt-on-base nodes must pass too.

    Refuses `main-checkout` unless `bounded_run.checkout_kind(root)` says "linked" (git unable to say
    refuses too), so project code never runs in the human's checkout. `root` is realpathed first: a symlinked
    path made pytest print `../../` node ids and would read as a deleted test. ONE deadline covers
    `--version`, collection and the run (`_step` draws on it). The interpreter is the recorded
    `proof.python`, else `sys.executable`, and a fallback is printed in `detail`; red (sealed earlier) and
    green (now) may therefore come from different interpreters. Same env scrub as `seal()`, plus
    `PYTHONDONTWRITEBYTECODE=1`, a throwaway `PYTHONPYCACHEPREFIX` (no in-tree bytecode is read) and `-p no:cacheprovider` so a green run leaves no debris. Sealed tests are
    model-authored code run with the operator's privileges and no sandbox; the caller gates that on the
    operator's shell-policy opt-in."""
    rg, bounded = _load("red_green"), _load("bounded_run")
    sdlc_dir = os.path.abspath(sdlc_dir)
    rec = _existing(seal_path(sdlc_dir, stem))
    if rec is None:
        raise SealRefused("record", "no usable seal record for goal %s" % stem)
    root = os.path.realpath(root)
    kind = bounded.checkout_kind(root)
    if kind != "linked":
        raise SealRefused("main-checkout", "%s is %s, not a linked goal worktree: the sealed tests are not run "
                          "there" % (root, "the main checkout" if kind == "main" else "not a git checkout"))
    try:
        budget = bounded.seconds(timeout)
    except ValueError as exc:
        raise SealRefused("timeout", str(exc))
    deadline = time.monotonic() + budget
    run = run or _default_run
    recorded = (rec.get("proof") or {}).get("python")
    candidates = [p for p in dict.fromkeys([recorded, sys.executable]) if _usable(p)]
    env = {k: v for k, v in bounded.unattended_env().items() if k not in _DROP_ENV}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COLUMNS"] = "240"
    detail = []
    with tempfile.TemporaryDirectory(prefix="sigma-sealed-") as tmp:
        env["HOME"] = tempfile.mkdtemp(prefix="home-", dir=os.path.realpath(tmp))
        # a planted in-tree __pycache__ (pytest's assertion-rewrite cache) must never be read: redirect it
        env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="pycache-", dir=os.path.realpath(tmp))
        python = None
        for cand in candidates:
            ver = _step(run, [cand, "-m", "pytest", "--version"], root, deadline, env, "pytest --version")
            if ver.code == 0:
                python = cand
                break
        if python is None:
            raise SealRefused("runner-unusable", "neither the recorded interpreter (%r) nor %s runs "
                              "`-m pytest --version`" % (recorded, sys.executable))
        if python != recorded:
            detail.append("interpreter fallback: %s -> %s" % (recorded, python))
        base_args = [python, "-m", "pytest"]
        common = ["-q", "--color=no", "-p", "no:cacheprovider", "--rootdir=" + root, "-o", "addopts="]
        col = _step(run, base_args + common + ["--collect-only", _rel(stem)], root, deadline, env, "collection")
        nodes = sorted({ln.strip() for ln in (col.out or "").splitlines()
                        if "::" in ln and ln.strip().startswith(_rel(stem) + "/")})
        verdict = rg.classify_sealed(col.code, col.out, None, "", nodes)
        if verdict["collection_errors"] or verdict["error"]:
            failing = [{"test": f, "why": "collection error"} for f in verdict["collection_errors"]]
            return {"green": False, "failing": failing, "detail": "; ".join(detail + [verdict["error"]]).strip("; ")}
        out = _step(run, base_args + ["-rA", "--tb=short"] + common + nodes, root, deadline, env, "the green run")
        verdict = rg.classify_sealed(col.code, col.out, out.code, out.out, nodes)
    if verdict["error"]:
        return {"green": False, "failing": [], "detail": "; ".join(detail + [verdict["error"]])}
    failing = [{"test": n, "why": (kind_reason[1] or kind_reason[0])} for n, kind_reason in sorted(verdict["red"].items())]
    failing += [{"test": n, "why": "not passed (skipped, xfail, xpass, setup error or absent)"}
                for n in verdict["other"]]
    passed = set(verdict["passed"])
    failing += [{"test": r["test"], "why": "recorded test did not pass (deleted or not collected)"}
                for r in rec.get("red") or [] if isinstance(r, dict) and r.get("test") not in passed
                and r.get("test") not in verdict["red"] and r.get("test") not in verdict["other"]]
    detail.append("%d passed, %d not passed" % (len(passed), len(failing)))
    return {"green": not failing, "failing": failing, "detail": "; ".join(detail)}


def read_record(sdlc_dir, stem):
    """The parsed seal record, or None when it is absent, unparseable, foreign or a symlink."""
    return _existing(seal_path(os.path.abspath(sdlc_dir), stem))


def source_commit(root, path, sha256, cap=200, budget=None):
    """-> the newest commit whose blob at `path` hashes to `sha256`, else None. The lookup behind every
    `differs`/`missing` remedy, because HEAD itself may be the tampered commit (a raw `git commit` caught
    at `pr`), where `restore --source=HEAD` rewrites the same wrong bytes, and a file missing at HEAD errors.

    ONE command: `git log --format=%H --raw --no-abbrev --no-renames -n <cap> -- <path>` lists the commits
    that touched the path, newest first, with the new blob oid on each raw line (a rename or copy INTO the
    path shows as an add, so path-limited history does not hide it). Each distinct oid is hashed once
    (`git cat-file blob`, bytes). `budget` is a shared {"left": n} counter of blob probes: the caller threads
    ONE per refusal so N problem paths cannot multiply the work; exhausted -> None and `budget["left"] == 0`.
    Limit: merge commits show no raw line of their own and are not probed."""
    if budget is None:
        budget = {"left": cap}
    done = _git_bytes(root, "log", "--format=%H", "--raw", "--no-abbrev", "--no-renames", "-n", str(cap),
                      "--", path, timeout=120)
    if done.returncode:
        return None
    commit, seen = None, {}
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        if _SHA.fullmatch(line.strip()):
            commit = line.strip()
        elif line.startswith(":") and commit:
            head = line[1:].split("\t")[0].split()
            if len(head) < 5 or head[4].startswith("D"):
                continue
            oid = head[3]
            if oid not in seen:
                if budget["left"] <= 0:
                    return None
                budget["left"] -= 1
                blob = _git_bytes(root, "cat-file", "blob", oid, timeout=120)
                seen[oid] = blob.returncode == 0 and _sha256(blob.stdout) == sha256
            if seen[oid]:
                return commit
    return None


def remedy(problem, root, rec, budget=None):
    """The one-sentence, SATISFIABLE fix for a `check`/`check_tree` problem, per kind. `root` is the goal
    worktree. missing/differs -> restore from the commit `source_commit` finds (never an assumed HEAD);
    none found -> the sealed bytes were never committed (or are past the probe window) and only the park
    lever is named; unexpected -> delete it; mode-only -> `chmod 644`; record -> park."""
    import shlex
    kind, path = problem.get("kind"), str(problem.get("path"))
    park = "park the goal (record parked with the reason)"
    wt, q = shlex.quote(str(root)), shlex.quote(path)
    if kind == "record":
        return "the seal record is unusable and the loop cannot rewrite it; " + park
    files = (rec or {}).get("files") or {}
    if kind == "differs" and path not in files and any(f.startswith(path + "/") for f in files):
        return ("replace %s with the real sealed directory: remove the symlink or file (`git -C %s rm -f -- %s`), "
                "then restore each sealed file from its source commit" % (path, wt, q))
    if kind == "unexpected" or (kind == "differs" and path not in files):
        return ("delete %s (`git -C %s rm -f -- %s` if it is tracked, else remove the file); restore cannot "
                "remove it" % (path, wt, q))
    meta = files.get(path) or {}
    full = pathlib.Path(root, *path.split("/"))
    try:
        import stat as _stat
        mode = os.lstat(full).st_mode
        if _stat.S_ISREG(mode) and mode & 0o111 and _sha256(full.read_bytes()) == meta.get("sha256"):
            return "only the executable bit differs: `chmod 644 %s`, then commit again" % q
    except OSError:
        pass
    budget = budget if budget is not None else {"left": 200}
    found = source_commit(root, path, meta.get("sha256"), budget=budget)
    if found:
        return "restore it: `git -C %s restore --source=%s --staged --worktree -- %s`" % (wt, found, q)
    if budget["left"] <= 0:
        return "the sealed bytes were not found within the probe budget; the only lever is to " + park
    return "the sealed bytes were never committed (or are older than the probe window); the only lever is to " + park

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
    base-unresolved, runner-unusable, timeout, disk, no-tests-in-file, evidence-unpersisted, bad-route."""

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


def is_debris(d, name):
    """Cache debris a verify run leaves inside the sealed dir, which is neither sealed nor unexpected.
    Strict rule: a REAL (non-symlink) `.pytest_cache` directory, or a REAL `__pycache__` directory holding
    only regular `*.pyc` files. A symlink, a non-pyc file or a subdirectory inside `__pycache__` makes the
    whole entry unexpected. lstat only; nothing is followed."""
    import stat as _stat
    p = pathlib.Path(d) / name
    try:
        if not _stat.S_ISDIR(os.lstat(p).st_mode):
            return False
        if name == ".pytest_cache":
            return True
        if name != "__pycache__":
            return False
        return all(_stat.S_ISREG(os.lstat(p / n).st_mode) and n.endswith(".pyc") for n in os.listdir(p))
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
        if not ok:
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
    An absent, unparseable or foreign record is `sealed: False` with a `record` problem, never "ok"."""
    sdlc_dir = os.path.abspath(sdlc_dir)
    path = seal_path(sdlc_dir, stem)
    rec = _existing(path)
    if rec is None:
        return {"sealed": False, "problems": [{"path": str(path), "kind": "record"}]}
    prefix = _rel(stem) + "/"
    problems = []
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
            same = _stat.S_ISREG(mode) and _sha256(full.read_bytes()) == meta.get("sha256")
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

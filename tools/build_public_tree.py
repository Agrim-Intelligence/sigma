#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the tree of a fresh one-commit public repository from ONE commit of this repository
(issue 397): deterministic, scanned, with its report outside every repository.

USAGE
    python3 tools/build_public_tree.py COMMIT --out DIR [--source DIR] [--patterns FILE]
        [--sdlc exclude|include] [--exclude PREFIX ...]
        [--review-level clean|landed|pr-merged|owner-merged] [--repo OWNER/NAME]
        [--remote NAME] [--base BRANCH] [--report-dir DIR] [--scan-timeout S]

WHAT IT DOES. It plans the export from `git ls-tree` of COMMIT (minus `.sdlc/` by default and every
`--exclude` prefix), refuses every structural problem (a symlink, a gitlink, a bad or colliding
path, a secret file name, a binary, non-UTF-8 or oversized blob), checks the requested review level,
derives the planned tree twice (git in a private object directory, and Python), writes the files to
`OUT.partial-<pid>`, re-derives the tree from disk, makes a one-commit repository there with a fixed
identity and the source's committer date, runs four scans (its own content rules, `leak_refs.py
tree`, `exposure_scan.py tracked` and the export's own `leak_scan.py`), writes its report and only
then renames the export into place.

EXIT. 0 = VERIFIED (the export is at OUT). 1 = REJECTED (the export is at `OUT.rejected` with no
branch and no export commit: the commit is pruned, so its id cannot be pushed; the files stay on
disk for inspection; the report says why). 2 = a refusal (one stderr line
`build_public_tree: REFUSED [<code>] <detail>`, nothing on stdout, no export, no report) or
NOT-VERIFIED (a scan crashed or timed out: the export is at `OUT.rejected`, the report is written).

SAFETY. The patterns file, OUT and the report directory must all resolve outside every git work
tree. The source repository is never written. Patterns and matched values are never printed: every
printed line and every string value of the report passes the patterns once more and any match
becomes `[redacted: pattern P]`. Network only through `gh api` GETs, and only for the REST review
levels. See docs/public-snapshot.md.
"""
import argparse
import ast
import datetime
import errno
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter

TOOLS_ROOT = pathlib.Path(__file__).resolve().parents[1]

SCHEMA = "sigma.public-tree-report/v1"
DISPOSITIONS_SCHEMA = "sigma.public-tree-dispositions/v1"
DISPOSITIONS_PATH = "docs/launch/public-tree-dispositions.json"
EXPOSURE_ALLOWLIST_PATH = "docs/launch/exposure-allowlist.json"
DEFINITION_PATH = "docs/launch/definition.json"
PLUGIN_JSON_PATH = ".claude-plugin/plugin.json"
DOCTOR_PATH = "skills/agrim-doctor/scripts/doctor.py"
SDLC_DEFAULT = "exclude"
AUTHOR_NAME = "sigma-public-snapshot"
AUTHOR_EMAIL = "noreply@users.noreply.github.com"
MAX_BLOB_BYTES = 2 * 1024 * 1024
REPORT_SUFFIX_TRIES = 100
DEFAULT_REPORT_DIR = "~/.sigma-ops/public-tree"
ENV_PATTERNS = "SIGMA_LEAK_PATTERNS"
LEVELS = ("clean", "landed", "pr-merged", "owner-merged")
CONTENT_RULES = ("private-key-header", "owner-placeholder")
STDERR_CUT = 200
#: The running copies whose bytes are compared with the commit's (step 7).
TOOL_PATHS = ("tools/build_public_tree.py", "tools/leak_refs.py", "tools/readiness/exposure_scan.py",
              "skills/agrim-loop/scripts/scrub.py")
LEAK_SCAN_LABEL = "commit-time scan; key-body rule known incomplete, #433"
NOT_COVERED = (
    "header-less private key bodies (#433)",
    "exposure_scan.py de-duplicates its rules by name, so a second rule of one name never runs",
    "file names are checked against the private patterns only",
    "leak_scan.py's origin-owner rule is skipped (the export has no origin)",
    "names absent from the patterns file",
    "destination GitHub state beyond what verify_public_repo.py counts",
    "an unpinned disposition suppresses every match of its rule anywhere in its file, so a new real "
    "value of that rule in that file would ship (e-mails are not covered by leak_scan either); see "
    "unpinned_dispositions",
)

#: Content rules over every exported blob, one finding per matching line. Built from fragments so
#: this file never holds what they look for.
KEY_HEADER = re.compile("-" * 5 + r"BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?" + "-" * 5 + "|" + "-" * 4
                        + r" BEGIN SSH2 ENCRYPTED PRIVATE KEY " + "-" * 4 + "|"
                        + r"PuTTY-User-Key-File-\d+:")
OWNER_PLACEHOLDER = re.compile(re.escape("<" + "OWNER:"))
#: `tools/leak_scan.py`'s `_SECRET_FILE`, same source (a test pins the two patterns equal).
SECRET_FILE = re.compile(
    r"(?i)^(?:id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?|.+\.(?:pem|key|p12|pfx|jks|keystore|ppk)"
    r"|[._]netrc|\.pgpass|credentials\.json|service[-_]?account.*\.json"
    r"|\.env(?:\.(?!(?:example|sample|template|dist|defaults)$)[^/]+)?)$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]+$")

# ------------------------------------------------------------------------------ copied from tools/leak_refs.py

_NOT_A_REPO = "fatal: not a git repository (or any of the parent directories)"
#: Removed from every git child's environment: each can point git at another repository, or stop
#: its discovery early, so a path inside a work tree would read as outside one.
_GIT_SCRUB = ("GIT_DIR", "GIT_WORK_TREE", "GIT_CEILING_DIRECTORIES", "GIT_INDEX_FILE",
              "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
              "GIT_NAMESPACE")
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")


class Refused(Exception):
    """A refusal: `.code` (stable, tests rely on it) and `.detail` (one line, redacted on print)."""

    def __init__(self, code, detail=""):
        Exception.__init__(self, code)
        self.code, self.detail = code, detail


def _real_run(args, input_text=None, timeout=60):
    """-> (rc, stdout, stderr). A timeout is rc 124, a binary that cannot be run rc 127. git children
    get `_GIT_SCRUB` removed, `GIT_DISCOVERY_ACROSS_FILESYSTEM=1` and `LC_ALL=C`."""
    args = [str(a) for a in args]
    env = None
    if args and args[0] == "git":
        env = {k: v for k, v in os.environ.items() if k not in _GIT_SCRUB}
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"
        env["LC_ALL"] = "C"
    feed = {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env, **feed)
    except subprocess.TimeoutExpired:
        return 124, "", "%s: timed out after %ss" % (args[0], timeout)
    except OSError:
        return 127, "", "%s: cannot be run (is it installed?)" % args[0]
    return proc.returncode, proc.stdout, proc.stderr


def parse_patterns(text):
    """-> [(line_number, compiled)]. Refuses a pattern that does not compile or that matches the
    empty string (by line number only), and a file with no pattern at all (a vacuous green)."""
    patterns = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            rx = re.compile(stripped, re.IGNORECASE)
        except (re.error, OverflowError, RecursionError):
            raise Refused("pattern-compile", "patterns line %d does not compile" % number)
        if rx.search("") is not None:
            raise Refused("pattern-matches-empty", "patterns line %d matches the empty string" % number)
        patterns.append((number, rx))
    if not patterns:
        raise Refused("zero-patterns", "the patterns file holds no pattern, only blank or # lines")
    return patterns


def _safe(line, patterns):
    """The belt behind the output rule: any pattern match left in a printed line is redacted."""
    for number, rx in patterns or ():
        line = rx.sub(lambda m, n=number: ("[redacted: pattern %d]" % n) if m.end() > m.start()
                      else m.group(0), line)
    return line


# ------------------------------------------------------------------------------ small helpers

def _platform_is_windows():
    return os.name == "nt"


def _refuse_inside_work_tree(path, code, run):
    """Refuse `path` inside ANY git work tree with `code`, failing closed: the only accepted answer
    is git's own `not a git repository (or any of the parent directories)`."""
    try:
        probe = pathlib.Path(os.path.abspath(str(path))).resolve()
        while not probe.is_dir() and probe.parent != probe:
            probe = probe.parent
    except (OSError, RuntimeError):
        raise Refused("work-tree-unknown", "cannot resolve a path to check it is outside every work tree")
    rc, _out, err = run(["git", "-C", str(probe), "rev-parse", "--show-toplevel"])
    if rc == 0:
        raise Refused(code, "the path resolves inside a git work tree; keep it outside every repository")
    if rc == 128 and (err or "").startswith(_NOT_A_REPO):
        return
    raise Refused("work-tree-unknown", "cannot tell whether a path is inside a git work tree (git exit %d)" % rc)


def _git_env():
    """The scrubbed environment `_real_run` gives a git child."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_SCRUB}
    env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"
    env["LC_ALL"] = "C"
    return env


def _run_bytes(args, input_bytes=None, timeout=60, env=None):
    """-> (rc, stdout_bytes, stderr_text): no decoding of stdout, so a non-UTF-8 path name reaches
    `[bad-path]` instead of being replaced. Not injectable; git only."""
    argv = [str(a) for a in args]
    feed = {"input": input_bytes} if input_bytes is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout,
                              env=env if env is not None else _git_env(), **feed)
    except subprocess.TimeoutExpired:
        return 124, b"", "%s: timed out after %ss" % (argv[0], timeout)
    except OSError:
        return 127, b"", "%s: cannot be run (is it installed?)" % argv[0]
    return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")


def _git(src, *rest, **kw):
    """Text git in the source repository; any failure refuses `git-failed`."""
    rc, out, _err = _real_run(["git", "-C", str(src)] + list(rest), None, kw.get("timeout", 60))
    if rc != 0 and not kw.get("allow"):
        raise Refused("git-failed", "git %s exited %d" % (rest[0], rc))
    return rc, out


def _gitb(cwd, rest, input_bytes=None, env=None, timeout=600):
    rc, out, _err = _run_bytes(["git", "-C", str(cwd)] + list(rest), input_bytes, timeout, env)
    if rc != 0:
        verb = next((w for i, w in enumerate(rest) if not w.startswith("-") and (i == 0 or rest[i - 1] != "-c")), "")
        raise Refused("git-failed", "git %s exited %d" % (verb, rc))
    return out


def _tilde(path):
    home = os.path.abspath(os.path.expanduser("~"))
    full = os.path.abspath(str(path))
    if home not in ("", os.sep) and full.startswith(home.rstrip(os.sep) + os.sep):
        return "~" + os.sep + full[len(home.rstrip(os.sep)) + 1:]
    return full


def _under(path, prefixes):
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _make_private_dir(path):
    path = pathlib.Path(path)
    if not path.is_dir():
        os.makedirs(str(path), mode=0o700, exist_ok=True)
        os.chmod(str(path), 0o700)


def _redact(value, patterns):
    """`_safe` over every `str` VALUE (dict values, list items, recursively); keys and non-str values
    are untouched, so the schema cannot change and the JSON stays valid."""
    if isinstance(value, str):
        return _safe(value, patterns)
    if isinstance(value, dict):
        return dict((k, _redact(v, patterns)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return [_redact(v, patterns) for v in value]
    return value


# ------------------------------------------------------------------------------ object ids

def blob_id(data):
    """git's blob id of `data` (bytes)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def tree_id(entries):
    """git's tree id of `entries` [(path, mode, blob_hex)], modes `100644`/`100755`: names sorted as
    UTF-8 bytes, a subtree compared as `name + "/"`, subtree mode `40000`."""
    root = {}
    for path, mode, oid in entries:
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (mode, oid)

    def write(node):
        items = []
        for name, value in node.items():
            raw = name.encode("utf-8", "surrogateescape")
            if isinstance(value, dict):
                items.append((raw + b"/", b"40000", raw, bytes.fromhex(write(value))))
            else:
                items.append((raw, value[0].encode("ascii"), raw, bytes.fromhex(value[1])))
        items.sort(key=lambda item: item[0])
        body = b"".join(mode + b" " + name + b"\0" + oid for _key, mode, name, oid in items)
        return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()

    return write(root)


def disk_tree(root):
    """The tree id of the files under `root`, from disk: no link is followed; a symlink, an empty
    directory or a non-regular file refuses `[tree-mismatch]`."""
    root = str(root)
    entries = []
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in dirs:
            if os.path.islink(os.path.join(base, name)):
                raise Refused("tree-mismatch", "the export holds a symbolic link")
        if base != root and not dirs and not names:
            raise Refused("tree-mismatch", "the export holds an empty directory")
        for name in names:
            full = os.path.join(base, name)
            info = os.lstat(full)
            if not stat.S_ISREG(info.st_mode):
                raise Refused("tree-mismatch", "the export holds a file that is not a regular file")
            digest = hashlib.sha1(b"blob %d\0" % info.st_size)
            with open(full, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 16), b""):
                    digest.update(chunk)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            entries.append((rel, "100755" if info.st_mode & 0o100 else "100644", digest.hexdigest()))
    return tree_id(entries)


# ------------------------------------------------------------------------------ materialise

def _read_exact(stream, size):
    chunks, left = [], size
    while left:
        chunk = stream.read(left)
        if not chunk:
            raise Refused("git-failed", "git cat-file ended early")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def _references_rx(excluded):
    if not excluded:
        return None
    alternatives = sorted(set(excluded), key=lambda p: (-len(p), p))
    return re.compile("|".join(re.escape(p) for p in alternatives))


def materialise(src_root, entries, dest, excluded=()):
    """Write `entries` [(path, mode, blob_hex)] of the source repository at `src_root` under `dest`
    through ONE `git cat-file --batch`, request-response, memory bounded by one blob. Refuses
    `[oversize]` (before reading), `[binary]` and `[non-utf8]`. Files are created 0600 with O_EXCL
    and then chmod-ed 0644/0755 (never the umask's choice). -> {files, bytes, modes, findings,
    references_remaining}: findings are the content rules' hits, one per matching line;
    references_remaining maps each excluded path to the number of exported lines naming it."""
    dest = str(dest)
    os.makedirs(dest, mode=0o700, exist_ok=True)
    refs_rx = _references_rx([str(p) for p in excluded])
    references = Counter()
    findings, modes, total = [], Counter(), 0
    proc = subprocess.Popen(["git", "-C", str(src_root), "cat-file", "--batch"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_git_env())
    try:
        for path, mode, oid in sorted(entries, key=lambda e: e[0].encode("utf-8", "surrogateescape")):
            proc.stdin.write(oid.encode("ascii") + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().decode("ascii", "replace").split()
            if len(header) != 3 or header[0] != oid or header[1] != "blob":
                raise Refused("git-failed", "git cat-file could not read a planned blob")
            size = int(header[2])
            if size > MAX_BLOB_BYTES:
                raise Refused("oversize", "a planned blob is %d bytes, over the %d-byte cap" % (size, MAX_BLOB_BYTES))
            data = _read_exact(proc.stdout, size + 1)[:size]
            if b"\0" in data:
                raise Refused("binary", "a planned blob holds a NUL byte")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise Refused("non-utf8", "a planned blob is not UTF-8")
            for number, line in enumerate(text.split("\n"), 1):
                for rule, rx in (("private-key-header", KEY_HEADER), ("owner-placeholder", OWNER_PLACEHOLDER)):
                    if rx.search(line):
                        findings.append({"rule": rule, "path": path, "line": number, "blob": oid})
                if refs_rx is not None:
                    for named in set(m.group(0) for m in refs_rx.finditer(line)):
                        references[named] += 1
            target = os.path.join(dest, *path.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
            finally:
                os.close(fd)
            os.chmod(target, 0o755 if mode == "100755" else 0o644)
            modes[mode] += 1
            total += size
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    return {"files": sum(modes.values()), "bytes": total, "modes": dict(sorted(modes.items())),
            "findings": findings, "references_remaining": dict(references)}


# ------------------------------------------------------------------------------ report files

def _write_new(final, data):
    """Create `final` once: a 0600 tmp written and fsync-ed, `os.link`-ed to the final name, the tmp
    removed. -> False when `final` already exists (never overwritten)."""
    tmp = "%s.tmp-%d" % (final, os.getpid())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        os.unlink(tmp)          # this pid's own leftover: never another run's file
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(tmp, final)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return False
        raise
    finally:
        os.unlink(tmp)
    return True


def _json_bytes(report):
    return (json.dumps(report, indent=2) + "\n").encode("utf-8")


def write_report(report_dir, stem, report, patterns=None):
    """-> (json_path, md_path): `<stem>.json`, else `<stem>-1.json` ... `<stem>-99.json`, the `.md`
    twin taking the chosen name; created once each, 0600, never overwriting. All tries taken refuses
    `[report-exists]`."""
    report_dir = pathlib.Path(report_dir)
    _make_private_dir(report_dir)
    body = _json_bytes(report)
    md = _safe(render_md(report), patterns).encode("utf-8")
    for k in range(REPORT_SUFFIX_TRIES):
        name = stem if k == 0 else "%s-%d" % (stem, k)
        json_path, md_path = report_dir / (name + ".json"), report_dir / (name + ".md")
        if os.path.lexists(str(md_path)):
            continue
        if not _write_new(str(json_path), body):
            continue
        if not _write_new(str(md_path), md):
            os.unlink(str(json_path))
            continue
        return str(json_path), str(md_path)
    raise Refused("report-exists", "every report name for this run is taken")


def finalise_report(json_path, md_path, report, patterns=None):
    """The ONE in-place rewrite: this run's own two files, each by tmp + `os.replace`."""
    for path, data in ((json_path, _json_bytes(report)), (md_path, _safe(render_md(report), patterns).encode("utf-8"))):
        tmp = "%s.tmp-%d" % (path, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)


def _rename_export(partial, dest):
    os.rename(str(partial), str(dest))


def render_md(report):
    """The `.md` twin: the same facts as the JSON, for a human."""
    lines = ["# Public tree build report", ""]
    src, export, review = report.get("source", {}), report.get("export", {}), report.get("review", {})
    lines.append("- Verdict: %s (finalise: %s)" % (report.get("verdict"), report.get("finalise")))
    lines.append("- Generated at: %s" % report.get("generated_at"))
    lines.append("- Source commit: %s (tree %s, plugin version %s)" % (
        src.get("commit"), src.get("tree"), src.get("plugin_version")))
    lines.append("- Export: %s; tree %s; commit %s; %s file(s), %s byte(s)" % (
        export.get("out"), export.get("export_tree"), export.get("commit"), export.get("files"),
        export.get("bytes")))
    lines.append("- Export commit identity: %s, date %s, message: %s" % (
        export.get("author"), export.get("date"), export.get("message")))
    lines += ["", "## Review", ""]
    lines.append("- Requested level: %s; reached: %s" % (review.get("requested"), review.get("reached")))
    for name in LEVELS:
        level = (review.get("levels") or {}).get(name) or {}
        if not level.get("checked"):
            lines.append("- %s: not checked" % name)
            continue
        facts = level.get("facts") or {}
        text = "- %s: %s" % (name, "ok" if level.get("ok") else "failed")
        if name == "landed":
            text += "; %s commit(s) past it on %s; the builder does not fetch, so this distance is as of " \
                    "the last fetch" % (facts.get("commits_past"), facts.get("ref"))
        if name == "pr-merged":
            text += "; pull request %s into %s, opened by %s, merged by %s" % (
                facts.get("pull"), facts.get("base"), facts.get("opened_by"), facts.get("merged_by"))
        if name == "owner-merged":
            text += "; the merger's role: %s" % facts.get("role")
        lines.append(text)
    if review.get("independent") is False:
        lines.append("- one account opened and merged: not independent review")
    elif review.get("independent") is True:
        lines.append("- the pull request was opened and merged by different accounts")
    protection = review.get("branch_protection")
    lines.append("- Branch protection on the base branch, as measured: %s" % (
        protection if protection else "not measured (the review level is below pr-merged)"))
    sdlc = report.get("sdlc", {})
    lines += ["", "## .sdlc/ and exclusions", ""]
    lines.append("- .sdlc/ mode: %s (from the %s)" % (sdlc.get("mode"), "default" if sdlc.get("source") == "default" else "flag"))
    if sdlc.get("source") == "default":
        lines.append("- The .sdlc/ choice came from the builder's default (exclude), not from an explicit owner "
                     "choice; pass --sdlc exclude or --sdlc include to record one.")
    for item in report.get("exclusions") or []:
        lines.append("- excluded %s (%s): %s file(s), %s byte(s), %s exported line(s) still name an "
                     "excluded file" % (item.get("prefix"), item.get("origin"), item.get("files"),
                                        item.get("bytes"), item.get("references_remaining")))
    pats = report.get("patterns", {})
    lines += ["", "## Scans", ""]
    lines.append("- Patterns: %s pattern(s), from the %s" % (pats.get("count"), pats.get("source")))
    scans = report.get("scans", {})
    builder = scans.get("builder", {})
    lines.append("- builder: exit %s, %d finding(s)" % (builder.get("exit"), len(builder.get("findings") or [])))
    for item in builder.get("findings") or []:
        lines.append("  - %s %s line %s" % (item.get("rule"), item.get("path"), item.get("line")))
    slug = scans.get("doctor_slug", {})
    lines.append("- doctor slug: %s" % slug.get("status"))
    refs = scans.get("leak_refs", {})
    lines.append("- leak_refs: exit %s, %s hit(s) over %s file(s) and %s commit(s)" % (
        refs.get("exit"), refs.get("hits"), refs.get("files"), refs.get("commits")))
    for loc in refs.get("locations") or []:
        lines.append("  - %s" % loc)
    exp = scans.get("exposure", {})
    lines.append("- exposure: exit %s, %d finding(s), %d stale allowlist entr(ies)" % (
        exp.get("exit"), len(exp.get("findings") or []), len(exp.get("stale") or [])))
    for item in exp.get("findings") or []:
        lines.append("  - %s %s line %s" % (item.get("rule"), item.get("path"), item.get("line")))
    for item in exp.get("stale") or []:
        lines.append("  - stale %s %s" % (item.get("rule"), item.get("path")))
    scan = scans.get("leak_scan", {})
    lines.append("- leak_scan: %s, exit %s (%s)" % (scan.get("status"), scan.get("exit"), scan.get("label")))
    for item in scan.get("findings") or []:
        lines.append("  - %s %s line %s" % (item.get("rule"), item.get("path"), item.get("line")))
    disp = report.get("dispositions", {})
    lines += ["", "## Dispositions", ""]
    lines.append("- exposure allowlist: %s" % json.dumps(disp.get("exposure_allowlist")))
    lines.append("- public-tree dispositions: %s" % json.dumps(disp.get("public_tree")))
    for item in disp.get("unpinned_dispositions") or []:
        lines.append("  - unpinned (whole file): %s %s %s" % (item.get("source"), item.get("path"), item.get("rule")))
    lines += ["", "## Not covered", ""]
    lines += ["- " + item for item in report.get("not_covered") or []]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------ the plan

def _parse_ls_tree(raw):
    """`ls-tree -r -z --long` bytes -> [(path_bytes, mode, kind, oid, size)]."""
    rows = []
    for row in raw.split(b"\0"):
        if not row:
            continue
        meta, _tab, path = row.partition(b"\t")
        fields = meta.decode("ascii", "replace").split()
        if len(fields) != 4:
            raise Refused("git-failed", "git ls-tree printed an unreadable row")
        mode, kind, oid, size = fields
        rows.append((path, mode, kind, oid, int(size) if size.isdigit() else 0))
    return rows


def _norm_exclude(value):
    if not value or value.startswith("/"):
        raise Refused("bad-exclude", "an --exclude value must be a relative path prefix")
    norm = value[:-1] if value.endswith("/") else value
    if not norm or any(part in ("", ".", "..") for part in norm.split("/")):
        raise Refused("bad-exclude", "an --exclude value must be relative, with no empty, . or .. part")
    return norm


def _check_structure(remaining):
    """Steps 5's refusals over the remaining entries, in the documented order."""
    for index, (_raw, mode, _kind, _oid, _size) in enumerate(remaining, 1):
        if mode == "120000":
            raise Refused("symlink", "planned entry %d is a symbolic link" % index)
        if mode == "160000":
            raise Refused("gitlink", "planned entry %d is a gitlink (submodule)" % index)
        if mode not in ("100644", "100755"):
            raise Refused("bad-mode", "planned entry %d has mode %s" % (index, mode))
    paths = []
    for index, (raw, _mode, _kind, _oid, _size) in enumerate(remaining, 1):
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused("bad-path", "planned entry %d is not strict UTF-8" % index)
        parts = path.split("/")
        if (path.startswith("/") or any(part in ("", ".", "..") for part in parts)
                or any(part.casefold() == ".git" for part in parts)
                or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in path)):
            raise Refused("bad-path", "planned entry %d has a path git or a checkout cannot hold safely" % index)
        paths.append(path)
    for path in paths:
        if SECRET_FILE.match(path.rsplit("/", 1)[-1]):
            raise Refused("secret-file-name", "%s has a secret file's name" % path)
    seen = {}
    for path in paths:
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            key = "/".join(parts[:i])
            folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", key).casefold())
            if seen.setdefault(folded, key) != key:
                raise Refused("case-collision", "two planned paths differ only by case or Unicode normalisation")
    return paths


# ------------------------------------------------------------------------------ review levels

def _gh_call(run, endpoint):
    return run(["gh", "api", endpoint])


def _gh_json(run, endpoint, patterns):
    rc, out, err = _gh_call(run, endpoint)
    if rc != 0:
        first = next((line for line in (err or "").splitlines() if line.strip()), "")
        first = _safe(first, patterns)[:STDERR_CUT]
        raise Refused("gh-failed", "gh api %s exited %d%s" % (endpoint.split("?")[0], rc, (": " + first) if first else ""))
    try:
        return json.loads(out)
    except ValueError:
        raise Refused("gh-failed", "gh api %s printed no JSON" % endpoint.split("?")[0])


def _review(args, src, commit, run, patterns):
    want = LEVELS.index(args.review_level)
    levels = dict((name, {"checked": False, "ok": False, "facts": {}}) for name in LEVELS)
    levels["clean"] = {"checked": True, "ok": True, "facts": {"head": commit, "status": "clean"}}
    review = {"requested": args.review_level, "reached": "clean", "levels": levels,
              "independent": None, "branch_protection": None}
    if want >= 1:
        ref = "refs/remotes/%s/%s" % (args.remote, args.base)
        rc, _out = _git(src, "rev-parse", "--verify", "-q", "--end-of-options", ref + "^{commit}", allow=True)
        if rc != 0:
            raise Refused("no-remote-ref", "%s is missing; the builder does not fetch" % ref)
        rc, _out = _git(src, "merge-base", "--is-ancestor", commit, ref, allow=True)
        if rc == 1:
            raise Refused("not-landed", "the commit is not on %s" % ref)
        if rc != 0:
            raise Refused("git-failed", "git merge-base exited %d" % rc)
        _rc, out = _git(src, "rev-list", "--count", "%s..%s" % (commit, ref))
        levels["landed"] = {"checked": True, "ok": True, "facts": {
            "ref": ref, "commits_past": int(out.strip() or 0),
            "fetch": "the builder does not fetch; this is the local remote-tracking ref as of the last fetch"}}
        review["reached"] = "landed"
    if want >= 2:
        if not args.repo:
            raise Refused("repo-required", "--review-level %s reads GitHub: pass --repo OWNER/NAME" % args.review_level)
        repo = args.repo
        info = _gh_json(run, "repos/%s" % repo, patterns)
        if not isinstance(info, dict) or info.get("private") is not True:
            raise Refused("review-repo-public", "the review repository is not private; after a takeover the old "
                          "name is the PUBLIC repository")
        pulls = _gh_json(run, "repos/%s/commits/%s/pulls?per_page=100" % (repo, commit), patterns)
        match = [p for p in (pulls if isinstance(pulls, list) else [])
                 if isinstance(p, dict) and p.get("merged_at") and p.get("merge_commit_sha") == commit
                 and isinstance(p.get("base"), dict) and p["base"].get("ref") == args.base]
        if not match or not isinstance(match[0].get("number"), int):
            raise Refused("not-pr-merged", "no merged pull request into %s has this commit as its merge "
                          "commit (a direct push answers none)" % args.base)
        number = match[0]["number"]
        pull = _gh_json(run, "repos/%s/pulls/%d" % (repo, number), patterns)
        pull = pull if isinstance(pull, dict) else {}
        opened = (pull.get("user") or {}).get("login") if isinstance(pull.get("user"), dict) else None
        merged = (pull.get("merged_by") or {}).get("login") if isinstance(pull.get("merged_by"), dict) else None
        review["independent"] = bool(opened and merged and opened.lower() != merged.lower())
        rc, _out, err = _gh_call(run, "repos/%s/branches/%s/protection" % (repo, args.base))
        review["branch_protection"] = "present" if rc == 0 else ("none" if "404" in (err or "") else "unreadable")
        levels["pr-merged"] = {"checked": True, "ok": True, "facts": {
            "pull": number, "base": args.base, "opened_by": opened, "merged_by": merged}}
        review["reached"] = "pr-merged"
        if want >= 3:
            if not merged or not _LOGIN.match(merged):
                raise Refused("not-owner-merged", "the pull request names no merger")
            perm = _gh_json(run, "repos/%s/collaborators/%s/permission" % (repo, merged), patterns)
            perm = perm if isinstance(perm, dict) else {}
            role = perm.get("role_name")
            ok = role in ("admin", "maintain") if role is not None else perm.get("permission") == "admin"
            if not ok:
                raise Refused("not-owner-merged", "the merger's role is not admin or maintain")
            levels["owner-merged"] = {"checked": True, "ok": True, "facts": {
                "role": role if role is not None else perm.get("permission")}}
            review["reached"] = "owner-merged"
    return review


# ------------------------------------------------------------------------------ dispositions

def _commit_blob(src, oid):
    if not oid:
        return None
    return _gitb(src, ["cat-file", "blob", oid], timeout=120)


def _load_dispositions(raw):
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused("dispositions-malformed", "%s is not JSON" % DISPOSITIONS_PATH)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("schema") != DISPOSITIONS_SCHEMA or not isinstance(entries, list):
        raise Refused("dispositions-malformed", "%s needs schema %s and an entries list" % (DISPOSITIONS_PATH, DISPOSITIONS_SCHEMA))
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry.get("path")
                or entry.get("rule") not in CONTENT_RULES or not isinstance(entry.get("reason"), str)
                or not entry.get("reason").strip()
                or ("blob" in entry and not (isinstance(entry["blob"], str) and _HEX40.match(entry["blob"].lower())))):
            raise Refused("dispositions-malformed", "a %s entry needs path, a known rule, a reason and an "
                          "optional 40-hex blob" % DISPOSITIONS_PATH)
    return entries


def _load_allowlist(raw):
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused("dispositions-malformed", "%s is not JSON" % EXPOSURE_ALLOWLIST_PATH)
    if not isinstance(data, list) or any(
            not isinstance(x, dict) or not x.get("path") or not x.get("rule") or not x.get("reason")
            or ("blob" in x and (not isinstance(x["blob"], str) or not _OBJECT_ID.match(x["blob"])))
            for x in data):
        raise Refused("dispositions-malformed", "%s entries need path, rule and reason" % EXPOSURE_ALLOWLIST_PATH)
    return data


def _disposition_matches(entry, finding):
    return (entry["path"] == finding["path"] and entry["rule"] == finding["rule"]
            and ("blob" not in entry or entry["blob"].lower() == (finding.get("blob") or "").lower()))


def _doctor_slug(partial, definition_raw, blobs):
    doctor = os.path.join(str(partial), *DOCTOR_PATH.split("/"))
    if not os.path.isfile(doctor):
        return {"status": "not-applicable", "doctor": None, "definition": None}, []
    slug, line = None, 0
    try:
        with open(doctor, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "_MARKETPLACE_REPO" and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                slug, line = node.value.value, node.lineno
    except (OSError, SyntaxError, ValueError):
        slug = None
    definition = None
    if definition_raw is not None:
        try:
            value = json.loads(definition_raw.decode("utf-8"))
            definition = value.get("public_repo") if isinstance(value, dict) else None
        except (UnicodeDecodeError, ValueError):
            definition = None
    finding = {"path": DOCTOR_PATH, "line": line, "blob": blobs.get(DOCTOR_PATH)}
    if slug is None or not isinstance(definition, str):
        return ({"status": "unverifiable", "doctor": slug, "definition": definition if isinstance(definition, str) else None},
                [dict(finding, rule="doctor-slug-unverifiable")])
    if slug != definition:
        return {"status": "mismatch", "doctor": slug, "definition": definition}, [dict(finding, rule="doctor-slug-mismatch")]
    return {"status": "equal", "doctor": slug, "definition": definition}, []


# ------------------------------------------------------------------------------ the export repository

def _export_repository(partial, plan, planned, ct, message):
    """Step 13: a one-commit repository in `partial` with the fixed identity; never `git add`, never
    `git archive`, never a hook-running command. -> the commit id."""
    env = _git_env()
    env.update({"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    base = ["-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", "-c", "core.logAllRefUpdates=false"]
    template = tempfile.mkdtemp(prefix="build_public_tree-template-")
    try:
        _gitb(partial, base + ["-c", "init.defaultBranch=main", "init", "-q", "--template=" + template], env=env)
    finally:
        shutil.rmtree(template, ignore_errors=True)
    paths = [path for path, _mode, _oid in plan]
    if paths:
        ids = _gitb(partial, base + ["hash-object", "-w", "--no-filters", "--stdin-paths"],
                    input_bytes=("\n".join(paths) + "\n").encode("utf-8"), env=env).decode("ascii", "replace").split()
        if ids != [oid for _path, _mode, oid in plan]:
            raise Refused("tree-mismatch", "a written file hashes to another blob than planned")
        info = b"".join(("%s %s\t" % (mode, oid)).encode("ascii") + path.encode("utf-8") + b"\0"
                        for path, mode, oid in plan)
        _gitb(partial, base + ["update-index", "--add", "-z", "--index-info"], input_bytes=info, env=env)
    tree = _gitb(partial, base + ["write-tree"], env=env).decode("ascii", "replace").strip()
    if tree != planned:
        raise Refused("tree-mismatch", "the export's index writes another tree than planned")
    ident = dict(env, GIT_AUTHOR_NAME=AUTHOR_NAME, GIT_AUTHOR_EMAIL=AUTHOR_EMAIL, GIT_AUTHOR_DATE="%s +0000" % ct,
                 GIT_COMMITTER_NAME=AUTHOR_NAME, GIT_COMMITTER_EMAIL=AUTHOR_EMAIL,
                 GIT_COMMITTER_DATE="%s +0000" % ct)
    commit = _gitb(partial, base + ["commit-tree", tree, "-m", message], env=ident).decode("ascii", "replace").strip()
    if not _HEX40.match(commit):
        raise Refused("git-failed", "git commit-tree printed no commit id")
    _gitb(partial, base + ["update-ref", "refs/heads/main", commit], env=env)
    _gitb(partial, base + ["symbolic-ref", "HEAD", "refs/heads/main"], env=env)
    status = _gitb(partial, base + ["status", "--porcelain=v1", "--untracked-files=all", "--ignored"], env=env)
    if status.strip():
        raise Refused("tree-mismatch", "the export repository is not clean after its commit")
    listed = [p.decode("utf-8", "surrogateescape") for p in _gitb(partial, base + ["ls-files", "-z"], env=env).split(b"\0") if p]
    if sorted(listed) != sorted(paths):
        raise Refused("tree-mismatch", "the export's index lists other files than planned")
    head_tree = _gitb(partial, base + ["rev-parse", "HEAD^{tree}"], env=env).decode("ascii", "replace").strip()
    if head_tree != planned:
        raise Refused("tree-mismatch", "the export's commit holds another tree than planned")
    return commit


def _unpublish_rejected(partial, run, commit):
    """A REJECTED or NOT-VERIFIED export keeps NO branch and NO export commit, in the EXPORT
    repository only (never the source): delete `refs/heads/main`, expire every reflog, prune the
    now-unreachable commit, then prove `commit` no longer exists. HEAD stays `ref: refs/heads/main`,
    now unborn. The files stay on disk for inspection (and the index keeps their blobs); the id in
    the report and on stdout names a commit that is gone, so it cannot be pushed. No reflog is
    written, and every call but the injected `run` sees no global or system git configuration."""
    rc, _out, _err = run(["git", "-C", str(partial), "-c", "core.logAllRefUpdates=false",
                          "-c", "core.hooksPath=/dev/null", "update-ref", "-d", "refs/heads/main"])
    if rc != 0:
        raise Refused("unpublish-failed", "could not remove the rejected export's branch (git exit %d)" % rc)
    env = _git_env()
    env.update({"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    base = ["-c", "core.logAllRefUpdates=false", "-c", "core.hooksPath=/dev/null", "-c", "gc.auto=0"]
    try:
        _gitb(partial, base + ["reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all"], env=env)
        _gitb(partial, base + ["gc", "--prune=now", "--quiet"], env=env)
    except Refused as exc:
        raise Refused("unpublish-failed", "could not prune the rejected export commit (%s)" % exc.detail)
    rc, _raw, _err = _run_bytes(["git", "-C", str(partial), "cat-file", "-e", commit], env=env)
    if rc == 0:
        raise Refused("unpublish-failed", "the rejected export commit is still retrievable after pruning")
    rc, out, _err = _real_run(["git", "-C", str(partial), "for-each-ref"])
    if rc != 0 or out.strip():
        raise Refused("unpublish-failed", "the rejected export still lists a ref")


# ------------------------------------------------------------------------------ scans

def _child(cmd, cwd, timeout):
    """A scanner child: scrubbed git environment, no bytecode written, bounded by `timeout`."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_SCRUB}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return proc.returncode, proc.stdout


def _scan_leak_refs(partial, patterns_path, workdir, timeout):
    script = TOOLS_ROOT / "tools" / "leak_refs.py"
    rc, out = _child([sys.executable, "-B", str(script), "tree", "--root", str(partial),
                      "--patterns", str(patterns_path)], workdir, timeout)
    result = {"exit": rc, "hits": None, "files": None, "commits": None, "locations": []}
    hits = re.search(r"^hits: (\d+)", out, re.M)
    scanned = re.search(r"^scanned: (\d+) file\(s\), (\d+) commit\(s\)", out, re.M)
    readable = (rc in (0, 1) and hits is not None and scanned is not None
                and (int(hits.group(1)) == 0) == (rc == 0))
    if hits:
        result["hits"] = int(hits.group(1))
    if scanned:
        result["files"], result["commits"] = int(scanned.group(1)), int(scanned.group(2))
    result["locations"] = [line for line in out.splitlines() if line.startswith(("tree ", "commit "))]
    return result, readable


def _scan_exposure(partial, patterns_path, workdir, allow, timeout):
    script = TOOLS_ROOT / "tools" / "readiness" / "exposure_scan.py"
    allow_path = os.path.join(str(workdir), "allow.json")
    out_path = os.path.join(str(workdir), "exposure.json")
    with open(allow_path, "w", encoding="utf-8") as handle:
        json.dump(allow, handle)
    rc, _out = _child([sys.executable, "-B", str(script), "tracked", str(partial), "--patterns", str(patterns_path),
                       "--allowlist", allow_path, "--json", out_path], workdir, timeout)
    result = {"exit": rc, "findings": [], "stale": [], "skipped": None, "legacy_issue_references": None}
    data = None
    if rc in (0, 1):
        try:
            with open(out_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = None
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list) \
            or not isinstance(data.get("stale_allowlist"), list):
        return result, False
    result["findings"] = [{"rule": f.get("rule"), "path": f.get("path"), "line": f.get("line"), "blob": f.get("blob")}
                          for f in data["findings"] if isinstance(f, dict)]
    result["stale"] = [{"rule": s.get("rule"), "path": s.get("path")} for s in data["stale_allowlist"] if isinstance(s, dict)]
    result["skipped"] = data.get("skipped")
    result["legacy_issue_references"] = (data.get("counts") or {}).get("legacy_issue_references")
    readable = (rc == 1) == bool(result["findings"] or result["stale"])
    return result, readable


def _scan_leak_scan(partial, timeout):
    script = os.path.join(str(partial), "tools", "leak_scan.py")
    result = {"status": "absent", "exit": None, "findings": [], "label": LEAK_SCAN_LABEL}
    if not os.path.isfile(script):
        return result, True
    rc, out = _child([sys.executable, "-B", os.path.join("tools", "leak_scan.py")], partial, timeout)
    result["exit"] = rc
    summary = re.search(r"^leak_scan: (\d+) finding\(s\)", out, re.M)
    for line in out.splitlines():
        if line.startswith("leak_scan: "):
            break
        found = re.match(r"^(.+):(\d+): (\S+)$", line)
        if found:
            result["findings"].append({"rule": found.group(3), "path": found.group(1), "line": int(found.group(2))})
    readable = rc in (0, 1) and summary is not None and (int(summary.group(1)) == 0) == (rc == 0)
    result["status"] = "ran" if readable else "failed"
    return result, readable


# ------------------------------------------------------------------------------ main

class _State(object):
    """What the error path must clean: the partial export and the private temp dirs."""

    def __init__(self):
        self.patterns, self.partial, self.temps = None, None, []


def _parser():
    parser = argparse.ArgumentParser(
        prog="build_public_tree.py",
        usage="%(prog)s COMMIT --out DIR [--source DIR] [--patterns FILE] [--sdlc {exclude,include}]\n"
              "       [--exclude PREFIX ...] [--review-level {clean,landed,pr-merged,owner-merged}]\n"
              "       [--repo OWNER/NAME] [--remote NAME] [--base BRANCH] [--report-dir DIR] [--scan-timeout S]",
        description="Build the tree of a fresh one-commit public repository from one commit, deterministic "
                    "and scanned, with its report outside every repository. Exit 0 VERIFIED, 1 REJECTED, "
                    "2 refusal or NOT-VERIFIED.")
    # COMMIT and --out are required; checked after unknown flags, so a typo is named as one.
    parser.add_argument("commit", nargs="?", metavar="COMMIT", help="the commit to export (required)")
    parser.add_argument("--out", metavar="DIR",
                        help="where the export lands (OUT, or OUT.rejected); outside every work tree")
    parser.add_argument("--source", metavar="DIR", help="the source repository (default: the cwd's top level)")
    parser.add_argument("--patterns", metavar="FILE",
                        help="private patterns file (else $%s); required, outside every work tree" % ENV_PATTERNS)
    parser.add_argument("--sdlc", choices=("exclude", "include"),
                        help="ship .sdlc/ or not (default: %s, recorded as the default)" % SDLC_DEFAULT)
    parser.add_argument("--exclude", action="append", default=[], metavar="PREFIX",
                        help="leave out a path prefix (repeatable)")
    parser.add_argument("--review-level", choices=LEVELS, default="pr-merged",
                        help="cumulative review proof required (default: pr-merged)")
    parser.add_argument("--repo", metavar="OWNER/NAME", help="the private review repository (REST levels)")
    parser.add_argument("--remote", default="origin", metavar="NAME", help="remote of the landed ref (default: origin)")
    parser.add_argument("--base", default="main", metavar="BRANCH", help="base branch (default: main)")
    parser.add_argument("--report-dir", metavar="DIR",
                        help="report directory (default: %s); outside every work tree" % DEFAULT_REPORT_DIR)
    parser.add_argument("--scan-timeout", type=float, default=900.0, metavar="S",
                        help="seconds each scan may take before the build is NOT-VERIFIED (default: 900)")
    return parser


def _refusal(code, detail, patterns):
    line = "build_public_tree: REFUSED [%s] %s" % (code, " ".join(str(detail).split()))
    print(_safe(line.rstrip(), patterns), file=sys.stderr)


def main(argv, run=None):
    run = run if run is not None else _real_run
    state = _State()
    try:
        return _main(argv, run, state)
    except Refused as exc:
        _drop_partial(state)
        _refusal(exc.code, exc.detail, state.patterns)
        return 2
    except Exception as exc:  # an unexpected failure is a refusal too, never a traceback and a 1
        _drop_partial(state)
        _refusal("internal", type(exc).__name__, state.patterns)
        return 2
    finally:
        for path in state.temps:
            shutil.rmtree(path, ignore_errors=True)


def _drop_partial(state):
    if state.partial is not None and os.path.lexists(str(state.partial)):
        shutil.rmtree(str(state.partial), ignore_errors=True)
    state.partial = None


def _main(argv, run, state):
    started = time.monotonic()
    timings = {}

    def lap(name, since):
        timings[name] = round(time.monotonic() - since, 3)
        return time.monotonic()

    # 1. platform, arguments
    if _platform_is_windows():
        raise Refused("windows", "this builder needs POSIX file modes and paths; not supported on Windows")
    parser = _parser()
    try:
        args, extra = parser.parse_known_args([str(a) for a in argv[1:]])
        if extra:
            parser.error("unrecognized arguments: %s" % " ".join(extra))
        missing = [name for name, value in (("COMMIT", args.commit), ("--out", args.out)) if not value]
        if missing:
            parser.error("the following arguments are required: %s" % ", ".join(missing))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if args.repo is not None and not _REPO_RE.match(args.repo):
        raise Refused("bad-slug", "--repo must be OWNER/NAME")
    if not _BRANCH.match(args.base) or not _BRANCH.match(args.remote):
        raise Refused("bad-ref", "--base and --remote must be plain ref names")
    if not args.scan_timeout > 0:
        raise Refused("bad-timeout", "--scan-timeout must be positive")
    now = datetime.datetime.now(datetime.timezone.utc)
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 2. patterns
    source = args.patterns or os.environ.get(ENV_PATTERNS) or ""
    if not source:
        raise Refused("no-patterns-source", "pass --patterns FILE or set %s; there is no default path" % ENV_PATTERNS)
    patterns_path = pathlib.Path(os.path.abspath(os.path.expanduser(source)))
    _refuse_inside_work_tree(patterns_path, "patterns-inside-work-tree", run)
    try:
        text = patterns_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise Refused("patterns-unreadable", "cannot read the patterns file")
    state.patterns = parse_patterns(text)
    patterns_path = patterns_path.resolve()

    # 3. where things land
    out = pathlib.Path(os.path.abspath(os.path.expanduser(args.out)))
    rejected = out.parent / (out.name + ".rejected")
    if os.path.lexists(str(out)) or os.path.lexists(str(rejected)):
        raise Refused("out-exists", "OUT or OUT.rejected already exists; a build never overwrites one")
    _refuse_inside_work_tree(out, "out-inside-work-tree", run)
    report_dir = pathlib.Path(os.path.abspath(os.path.expanduser(args.report_dir or DEFAULT_REPORT_DIR)))
    _refuse_inside_work_tree(report_dir, "report-dir-inside-work-tree", run)
    _make_private_dir(out.parent)
    _make_private_dir(report_dir)
    mark = lap("arguments", started)

    # 4. source and commit
    rc, top, _err = _real_run(["git", "-C", os.path.abspath(args.source or os.getcwd()), "rev-parse", "--show-toplevel"])
    if rc != 0 or not top.strip():
        raise Refused("not-a-repo", "the source is not a git repository")
    src = pathlib.Path(top.strip()).resolve()
    rc, out_text = _git(src, "rev-parse", "--verify", "-q", "--end-of-options", args.commit + "^{commit}", allow=True)
    commit = out_text.strip()
    if rc != 0 or not _HEX40.match(commit):
        raise Refused("bad-commit", "COMMIT does not name a commit in the source repository")
    _rc, fmt = _git(src, "rev-parse", "--show-object-format")
    if fmt.strip() != "sha1":
        raise Refused("object-format", "only sha1 repositories are supported")
    _rc, source_tree = _git(src, "rev-parse", commit + "^{tree}")
    _rc, ct = _git(src, "log", "-1", "--format=%ct", commit)
    ct = ct.strip()

    # 5. the plan
    rows = _parse_ls_tree(_gitb(src, ["ls-tree", "-r", "-z", "--long", "--full-tree", commit]))
    full = dict((raw.decode("utf-8", "surrogateescape"), oid) for raw, _m, _k, oid, _s in rows)
    decoded = [(raw.decode("utf-8", "surrogateescape"), row) for raw, row in ((r[0], r) for r in rows)]
    prefixes = []
    sdlc_mode = args.sdlc or SDLC_DEFAULT
    if sdlc_mode == "exclude" and any(_under(path, [".sdlc"]) for path, _row in decoded):
        prefixes.append((".sdlc", "sdlc"))
    for value in args.exclude:
        norm = _norm_exclude(value)
        if not any(_under(path, [norm]) for path, _row in decoded):
            raise Refused("exclude-matches-nothing", "--exclude %s matches no file of the commit" % norm)
        prefixes.append((norm, "flag"))
    names = [p for p, _o in prefixes]
    excluded_rows = [(path, row) for path, row in decoded if _under(path, names)]
    remaining = [row for path, row in decoded if not _under(path, names)]
    paths = _check_structure(remaining)
    plan = [(path, row[1], row[3]) for path, row in zip(paths, remaining)]
    mark = lap("plan", mark)

    # 6. clean
    _rc, head = _git(src, "rev-parse", "HEAD")
    if head.strip() != commit:
        raise Refused("head-not-commit", "the source's HEAD is not COMMIT; check it out first")
    _rc, status = _git(src, "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all")
    if status.strip():
        raise Refused("dirty", "the source work tree has changes or untracked files")

    # 7. provenance of the running tools
    tools = {}
    for rel in TOOL_PATHS:
        running = TOOLS_ROOT / rel
        if not running.is_file():
            raise Refused("tool-missing", "the running copy of %s is missing" % rel)
        mine = blob_id(running.read_bytes())
        theirs = full.get(rel)
        status_word = "absent" if theirs is None else ("match" if theirs == mine else "differs")
        if status_word == "differs":
            raise Refused("tool-mismatch", "the running %s differs from the commit's copy" % rel)
        tools[rel] = {"blob": mine, "status": status_word}
    mark = lap("clean-and-provenance", mark)

    # 8. review
    review = _review(args, src, commit, run, state.patterns)
    mark = lap("review", mark)

    # 9. dispositions and the exposure allowlist, from the COMMIT
    pt_raw = _commit_blob(src, full.get(DISPOSITIONS_PATH))
    pt_entries = _load_dispositions(pt_raw) if pt_raw is not None else []
    allow_raw = _commit_blob(src, full.get(EXPOSURE_ALLOWLIST_PATH))
    allow_entries = _load_allowlist(allow_raw) if allow_raw is not None else []
    definition_raw = _commit_blob(src, full.get(DEFINITION_PATH))
    plugin_raw = _commit_blob(src, full.get(PLUGIN_JSON_PATH))
    plugin_version = None
    if plugin_raw is not None:
        try:
            value = json.loads(plugin_raw.decode("utf-8"))
            plugin_version = value.get("version") if isinstance(value, dict) else None
        except (UnicodeDecodeError, ValueError):
            plugin_version = None
    plugin_version = plugin_version if isinstance(plugin_version, str) else None
    pt_live = [e for e in pt_entries if not _under(e["path"], names)]
    allow_live = [e for e in allow_entries if not _under(e["path"], names)]

    # 10. the planned tree, twice
    tmp = tempfile.mkdtemp(prefix="build_public_tree-")
    state.temps.append(tmp)
    _rc, common = _git(src, "rev-parse", "--path-format=absolute", "--git-common-dir")
    objects = os.path.join(tmp, "objects")
    os.mkdir(objects, 0o700)
    env = _git_env()
    env.update({"GIT_INDEX_FILE": os.path.join(tmp, "index"), "GIT_OBJECT_DIRECTORY": objects,
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.path.join(common.strip(), "objects")})
    info = b"".join(("%s %s\t" % (mode, oid)).encode("ascii") + path.encode("utf-8") + b"\0"
                    for path, mode, oid in plan)
    if info:
        _gitb(src, ["update-index", "--add", "-z", "--index-info"], input_bytes=info, env=env)
    planned_git = _gitb(src, ["write-tree"], env=env).decode("ascii", "replace").strip()
    planned_py = tree_id(plan)
    if planned_git != planned_py:
        raise Refused("tree-mismatch", "git and Python plan different trees")
    mark = lap("planned-tree", mark)

    # 11-12. materialise and re-derive from disk
    partial = out.parent / ("%s.partial-%d" % (out.name, os.getpid()))
    if os.path.lexists(str(partial)):
        raise Refused("out-exists", "a partial export of this pid already exists; delete it and re-run")
    os.mkdir(str(partial), 0o700)
    state.partial = partial
    excluded_paths = [path for path, _row in excluded_rows]
    mat = materialise(src, plan, partial, excluded_paths)
    actual_disk = disk_tree(partial)
    if actual_disk != planned_py:
        raise Refused("tree-mismatch", "the files on disk are not the planned tree")
    mark = lap("materialise", mark)

    # 13. the export repository
    message = "Initial public snapshot (Sigma %s)" % (plugin_version or "unknown")
    export_commit = _export_repository(partial, plan, planned_py, ct, message)
    mark = lap("export-repository", mark)

    # 14. scans, all of them
    blobs = dict((path, oid) for path, _mode, oid in plan)
    used = set()
    builder_findings = []
    for finding in mat["findings"]:
        hit = [i for i, e in enumerate(pt_live) if _disposition_matches(e, finding)]
        if hit:
            used.update(hit)
        else:
            builder_findings.append(finding)
    for index, entry in enumerate(pt_live):
        if index not in used:
            builder_findings.append({"rule": "stale-disposition", "path": entry["path"], "line": 0,
                                     "blob": entry.get("blob")})
    doctor_slug, slug_findings = _doctor_slug(partial, definition_raw, blobs)
    builder_findings += slug_findings
    scan_dir = tempfile.mkdtemp(prefix="build_public_tree-scan-")
    state.temps.append(scan_dir)
    try:
        leak_refs, refs_ok = _scan_leak_refs(partial.resolve(), patterns_path, scan_dir, args.scan_timeout)
        exposure, exposure_ok = _scan_exposure(partial.resolve(), patterns_path, scan_dir, allow_live, args.scan_timeout)
        leak_scan, scan_ok = _scan_leak_scan(partial, args.scan_timeout)
    finally:
        shutil.rmtree(scan_dir, ignore_errors=True)
    mark = lap("scans", mark)
    failed = [name for name, ok, result in (("leak_refs", refs_ok, leak_refs), ("exposure", exposure_ok, exposure),
                                            ("leak_scan", scan_ok, leak_scan)) if not ok]
    if failed:
        verdict = "NOT-VERIFIED"
    elif builder_findings or leak_refs["exit"] or exposure["exit"] or leak_scan["exit"]:
        verdict = "REJECTED"
    else:
        verdict = "VERIFIED"

    # 15. finalise
    if verdict != "VERIFIED":
        _unpublish_rejected(partial, run, export_commit)
    dest = out if verdict == "VERIFIED" else rejected
    stale_keys = set((s["path"], s["rule"]) for s in exposure["stale"])
    unpinned = [{"source": "exposure-allowlist", "path": e["path"], "rule": e["rule"]}
                for e in allow_live if "blob" not in e and (e["path"], e["rule"]) not in stale_keys
                and exposure_ok]
    unpinned += [{"source": "public-tree", "path": e["path"], "rule": e["rule"]}
                 for i, e in enumerate(pt_live) if i in used and "blob" not in e]
    exclusions = []
    for prefix, origin in prefixes:
        mine = [(path, row) for path, row in excluded_rows if _under(path, [prefix])]
        exclusions.append({"prefix": prefix, "origin": origin, "files": len(mine),
                           "bytes": sum(row[4] for _p, row in mine),
                           "references_remaining": sum(mat["references_remaining"].get(path, 0) for path, _r in mine)})
    timings["total"] = round(time.monotonic() - started, 3)
    report = {
        "schema": SCHEMA,
        "verdict": "NOT-VERIFIED" if verdict == "VERIFIED" else verdict,
        "finalise": "pending" if verdict == "VERIFIED" else "done",
        "generated_at": generated_at,
        "source": {"commit": commit, "tree": source_tree.strip(), "root": _tilde(src), "plugin_version": plugin_version},
        "tools": tools,
        "review": review,
        "sdlc": {"mode": sdlc_mode, "source": "flag" if args.sdlc else "default"},
        "exclusions": exclusions,
        "patterns": {"source": "flag" if args.patterns else "env", "count": len(state.patterns)},
        "export": {"out": _tilde(dest), "files": mat["files"], "bytes": mat["bytes"], "modes": mat["modes"],
                   "planned_tree_git": planned_git, "planned_tree_python": planned_py,
                   "actual_tree_disk": actual_disk, "export_tree": planned_py, "commit": export_commit,
                   "message": message, "author": "%s <%s>" % (AUTHOR_NAME, AUTHOR_EMAIL), "date": "%s +0000" % ct},
        "dispositions": {
            "exposure_allowlist": {"entries": len(allow_entries), "moot": len(allow_entries) - len(allow_live)},
            "public_tree": {"present": pt_raw is not None, "entries": len(pt_entries),
                            "moot": len(pt_entries) - len(pt_live)},
            "unpinned_dispositions": unpinned},
        "scans": {"builder": {"exit": 1 if builder_findings else 0, "findings": builder_findings},
                  "doctor_slug": doctor_slug, "leak_refs": leak_refs, "exposure": exposure, "leak_scan": leak_scan},
        "not_covered": list(NOT_COVERED),
        "timings": timings,
    }
    report = _redact(report, state.patterns)
    stem = "%s-%s-%d" % (commit[:12], now.strftime("%Y%m%dT%H%M%SZ"), os.getpid())
    json_path, md_path = write_report(report_dir, stem, report, state.patterns)
    _rename_export(partial, dest)
    state.partial = None
    if verdict == "VERIFIED":
        report["verdict"], report["finalise"] = "VERIFIED", "done"
        finalise_report(json_path, md_path, report, state.patterns)
    if verdict == "NOT-VERIFIED":
        first = failed[0]
        result = {"leak_refs": leak_refs, "exposure": exposure, "leak_scan": leak_scan}[first]
        _refusal("scan-failed", "%s exited %s; report: %s" % (first, result["exit"], _tilde(json_path)), state.patterns)
        return 2
    scans_line = "builder=%d leak_refs=%s/%s exposure=%s/%d+%d leak_scan=%s" % (
        len(builder_findings), leak_refs["exit"], leak_refs["hits"], exposure["exit"], len(exposure["findings"]),
        len(exposure["stale"]), "absent" if leak_scan["status"] == "absent" else leak_scan["exit"])
    for line in ("verdict: %s" % verdict, "commit: %s" % commit, "tree: %s" % planned_py,
                 "export-commit: %s" % export_commit, "out: %s" % _tilde(dest), "report: %s" % _tilde(json_path),
                 "scans: %s" % scans_line):
        print(_safe(line, state.patterns))
    return 0 if verdict == "VERIFIED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

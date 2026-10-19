#!/usr/bin/env python3
"""Merge per-repository system-graph fragments into one merged graph (stage 1: the merge core).

    python3 sysgraph_merge.py merge <fragments-dir> --out <dir> [--max-bytes N]
    python3 sysgraph_merge.py -h | --help

Nothing is written unless the operator names ``--out``. There is no default. The command is
stdlib only, runs no subprocess, opens no socket, and runs on the system python3 (3.9).

Fragment shape (assumed; see DEFAULT D2). A fragment is ``<fragments-dir>/<name>.json`` holding an
object with top-level ``sysgraph_version`` ("MAJOR.MINOR"), ``repo`` (non-empty string),
``commit_time`` (``YYYY-MM-DDTHH:MM:SSZ``, the committer time of the analysed commit), ``commit``
(40 lowercase hex), ``nodes`` (list of objects each with a non-empty string ``id``) and ``edges``
(list of objects each with string ``type``, ``from`` and ``to``). ``commit_time`` and ``commit``
are an ASSUMED addition: neither #663 nor #673 defines them, so if #673 names them differently the
change is the two field names below. Other top-level keys are ignored and never copied.

Layout (DEFAULT D4): flat and non-recursive, ``*.json`` in the fragments directory, processed in
sorted file-name order. The listing order the operating system returns is never trusted.

Selection: per ``repo`` the fragment with the greatest ``(commit_time, commit)`` wins; an exact
tie of both goes to the smaller file name and is reported (``tie:`` line). The same node ``id`` in
two repositories keeps the entry from the fragment with the greater ``(commit_time, commit)``, an
exact tie again to the smaller file name (DEFAULT D5). Stage 1 is a union: edges are kept, not
deduplicated, sorted by ``(type, from, to)`` then by their canonical JSON. Resolution is a later slice.

Version rule (DEFAULT D3): a private copy of the #663 reader rule, because a sibling script is not
importable here. ``^[0-9]{1,3}\\.[0-9]{1,3}$``, length <= 7, no leading zero in either part, major
in ``SUPPORTED_MAJORS``. A later goal collapses the two copies behind one shared bad-version table.
The length clause cannot fail once the pattern holds (3+1+3 = 7); it is kept as the stated rule.

Bad fragments (DEFAULT D1). Classification precedence, first hit wins: not a regular file / over the
size limit / unreadable / not UTF-8 / not JSON (NaN, Infinity and out-of-range floats included) /
not an object -> CORRUPT; missing or non-string or empty ``repo`` -> CORRUPT; bad version ->
REFUSED VERSION; bad ``commit_time`` (pattern and a real calendar date) or ``commit`` -> CORRUPT;
``nodes`` / ``edges`` not lists or an entry malformed -> CORRUPT. Each is excluded and named in
the report, never raised, so mixed types cannot cause a TypeError. A bad fragment never falls back
to an older fragment: when the bad file has a readable string ``repo`` (it is ATTRIBUTABLE) that
repository is excluded as a whole (``excluded repository:`` line) because serving older data for
a repository whose newest file is unreadable would be a silent stale answer. BOUNDARY: a file with
no readable repo (unreadable, non-JSON, not an object, no string repo) is UNATTRIBUTABLE and cannot
suppress an older valid fragment of any repository; it is only named. Lever: fix or remove the named
file and rerun. Exit 0 if at least one repository contributed, else exit 1 and nothing is written.

Output (fixed names, so "never write outside --out" is structural):
``merged.json``   {"sysgraph_version": "1.0", "merged_from": [{repo, commit, commit_time, file}...
                   sorted by repo], "nodes": [...sorted by id], "edges": [...]};
                   ``json.dumps(sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\\n"``.
``unresolved.json`` {"unresolved": []}; a skeleton, entries land in a later slice.
Both files are mode 0644. stdout report (no clock, no temp paths):
``merge: N fragment file(s) read; R repositories contributed; S superseded; K skipped corrupt; V refused version``
then ``skipped corrupt:`` / ``refused version:`` / ``excluded repository:`` / ``tie:`` lines, then
``wrote: <out>/merged.json, <out>/unresolved.json``. File and repo names are printed with control
characters replaced by ``?``. Exit: 0 ok; 1 no repository contributed; 2 usage or IO.

Safety: the per-file size limit is DERIVED from the machine (page size * physical pages // 32),
overridden by ``--max-bytes``; if the machine value is unavailable and no flag is given the command
refuses loudly (exit 2). The file is lstat-ed first: symlinks, FIFOs and other non-regular files are
skipped as corrupt and never opened; an oversized file is refused without being read; at most
limit+1 bytes are read. ``--out`` that equals or lies inside the fragments directory (a rerun would
read its own output), that is itself a symlink, or whose targets are symlinks or directories is
refused before any write. A residual a caller can hit: JSON nested deeper than the interpreter's
recursion limit that survives parsing makes serialisation fail; that exits 2 and writes nothing.

Atomicity and recovery. Everything is staged in memory first, so a data error never leaves a partial
``--out``. Each target is then written by same-directory temp file + fsync + chmod + ``os.replace``
and the directory is fsynced best-effort (where the platform allows it). Order: ``unresolved.json``
then ``merged.json`` (consumers key off merged.json). A crash or error between the two leaves the
OLD merged.json beside a NEW unresolved.json; rerunning fixes it. A hard crash (power loss, SIGKILL)
inside the write window can orphan one ``.<random>.tmp`` file in ``--out``; it is harmless and
deleting it is the lever. A failed run leaves the previous outputs untouched. Reruns on the same
input are idempotent and byte-identical. Rollback is deleting this file and its table rows. It writes
only to the caller-named ``--out``, never the root checkout.

Scalability (MEASURED on this host, see the P5 record: macOS arm64, Python 3.9.6, 2026-10-18):
each repository has 2 fragments of 1,000 nodes + 1,000 edges (~125 KB each); the winners are half the input.
  1x  =   10 repos,    20 files,   2.4 MB input: wall 0.12 s, peak RSS  39 MB
  10x =  100 repos,   200 files,  24.7 MB input: wall 0.98 s, peak RSS 266 MB
  100x = 1000 repos, 2000 files, 253.0 MB input: wall 10.0 s, peak RSS 2.5 GB
(single run each, subprocess wall clock, 25.8 GB host; peak RSS is the child high-water mark, so
about 20 bytes of RSS per input byte of winning fragments: parsed JSON objects, the node table, the
sorted lists and the serialised output are all held at once. At 100x that is a real ceiling: a machine
with less than ~4 GB free should not merge 1000 such repositories in one process. Larger sizes and
other hosts were not measured.)
Ceilings: memory is O(repositories x winning-fragment size), because only the winning parsed
fragment per repository is held; time is one pass of file reads plus an O(n log n) sort; the reads
are serial (single process), so wall time grows with total bytes. The per-file cap is machine-derived.
"""
import datetime
import json
import os
import re
import stat
import sys
import tempfile

SUPPORTED_MAJORS = (1,)
OUTPUT_VERSION = "1.0"
_VERSION_RE = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}")
_TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_MERGED = "merged.json"
_UNRESOLVED = "unresolved.json"

USAGE = (
    "usage: sysgraph_merge.py merge <fragments-dir> --out <dir> [--max-bytes N]\n"
    "       sysgraph_merge.py -h | --help\n"
    "\n"
    "Merge flat <fragments-dir>/*.json system-graph fragments into <dir>/merged.json and\n"
    "<dir>/unresolved.json. The latest fragment per repo wins. Writes nothing unless --out is given.\n"
    "  --out <dir>       output directory (required; never inside <fragments-dir>)\n"
    "  --max-bytes N     per-file size limit (default: derived from physical memory)\n"
    "Exit: 0 ok, 1 no repository contributed, 2 usage or IO error.\n"
)


class _Usage(Exception):
    pass


def _clean(value, limit=200):
    """Printable, single-line rendering of untrusted text for the report."""
    text = "".join("?" if (ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F or ord(c) in (0x2028, 0x2029))
                   else c for c in str(value))
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _list_dir(path):
    return os.listdir(path)


def default_max_bytes():
    """Per-file size limit derived from the machine; None when it cannot be derived."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 32
    except (AttributeError, ValueError, OSError):
        return None


def _bad_float(text):
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("non-finite number")
    return value


def _bad_constant(name):
    raise ValueError("non-finite constant " + name)


def _version_problem(version):
    """None if supported, else (is_refusal_reason_text)."""
    if not isinstance(version, str):
        return "sysgraph_version %s is not a string" % _clean(repr(version), 40)
    shown = '"%s"' % _clean(version, 40)
    parts = version.split(".")
    if (not _VERSION_RE.fullmatch(version) or len(version) > 7
            or any(len(p) > 1 and p[0] == "0" for p in parts)):
        return "sysgraph_version %s malformed (want MAJOR.MINOR, no leading zeros)" % shown
    if int(parts[0]) not in SUPPORTED_MAJORS:
        return "sysgraph_version %s unsupported major %d (supported: %s)" % (
            shown, int(parts[0]), ", ".join(str(m) for m in SUPPORTED_MAJORS))
    return None


def _valid_time(value):
    if not isinstance(value, str) or not _TIME_RE.fullmatch(value):
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _read_file(path, limit):
    """Return (text, None) or (None, reason). Never opens a non-regular file, never reads past limit+1."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        return None, "unreadable: %s" % _clean(exc.strerror or exc)
    if not stat.S_ISREG(st.st_mode):
        return None, "not a regular file (symlink, pipe or directory)"
    if st.st_size > limit:
        return None, "over the size limit (%d > %d bytes)" % (st.st_size, limit)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        return None, "unreadable: %s" % _clean(exc.strerror or exc)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, "not a regular file (symlink, pipe or directory)"
        chunks, total = [], 0
        while total <= limit:
            chunk = os.read(fd, min(1 << 20, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError as exc:
        return None, "unreadable: %s" % _clean(exc.strerror or exc)
    finally:
        os.close(fd)
    if total > limit:
        return None, "over the size limit (read more than %d bytes)" % limit
    try:
        return b"".join(chunks).decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "not UTF-8"


def _entries_problem(nodes, edges):
    if not isinstance(nodes, list):
        return "nodes is not a list"
    if not isinstance(edges, list):
        return "edges is not a list"
    for i, node in enumerate(nodes):
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            return "nodes[%d] is not an object with a non-empty string id" % i
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict) or not all(isinstance(edge.get(k), str)
                                                 for k in ("type", "from", "to")):
            return "edges[%d] is not an object with string type, from and to" % i
    return None


def _classify(path, name, limit):
    """('ok', frag) | ('corrupt', reason, repo_or_None) | ('refused', reason, repo)."""
    text, reason = _read_file(path, limit)
    if reason:
        return ("corrupt", reason, None)
    try:
        obj = json.loads(text, parse_constant=_bad_constant, parse_float=_bad_float)
    except RecursionError:
        return ("corrupt", "JSON nested too deeply", None)
    except ValueError as exc:
        return ("corrupt", "not valid JSON: %s" % _clean(exc, 80), None)
    if not isinstance(obj, dict):
        return ("corrupt", "top level is not an object", None)
    repo = obj.get("repo")
    if not isinstance(repo, str) or not repo:
        return ("corrupt", "repo is missing or not a non-empty string", None)
    problem = _version_problem(obj.get("sysgraph_version")) if "sysgraph_version" in obj \
        else "sysgraph_version is missing"
    if problem:
        return ("refused", problem, repo)
    if not _valid_time(obj.get("commit_time")):
        return ("corrupt", "commit_time is missing or not a real YYYY-MM-DDTHH:MM:SSZ time", repo)
    commit = obj.get("commit")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        return ("corrupt", "commit is missing or not 40 lowercase hex", repo)
    problem = _entries_problem(obj.get("nodes"), obj.get("edges"))
    if problem:
        return ("corrupt", problem, repo)
    if "\\u" in text:
        try:
            json.dumps(obj, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            return ("corrupt", "contains a lone surrogate escape", repo)
    return ("ok", {"repo": repo, "commit": commit, "commit_time": obj["commit_time"],
                   "file": name, "nodes": obj["nodes"], "edges": obj["edges"]})


def _key(frag):
    return (frag["commit_time"], frag["commit"])


def _beats(cand, cur):
    if _key(cand) != _key(cur):
        return _key(cand) > _key(cur)
    return cand["file"] < cur["file"]


def _select(fragments_dir, limit):
    """Read every fragment once; return the selection state."""
    names = sorted(n for n in _list_dir(fragments_dir) if n.endswith(".json"))
    state = {"read": len(names), "winner": {}, "valid": {}, "tied": {},
             "corrupt": [], "refused": [], "poison": {}}
    for name in names:
        res = _classify(os.path.join(fragments_dir, name), name, limit)
        if res[0] == "ok":
            f = res[1]
            repo = f["repo"]
            state["valid"][repo] = state["valid"].get(repo, 0) + 1
            cur = state["winner"].get(repo)
            if cur is None or _key(f) > _key(cur):
                state["winner"][repo] = f
                state["tied"][repo] = [name]
            elif _key(f) == _key(cur):
                state["tied"][repo].append(name)
                if name < cur["file"]:
                    state["winner"][repo] = f
            continue
        bucket = state["corrupt"] if res[0] == "corrupt" else state["refused"]
        bucket.append((name, res[1]))
        if res[2] is not None:
            state["poison"].setdefault(res[2], []).append(name)
    return state


def _merge_nodes(winners):
    best = {}
    for f in winners:
        for node in f["nodes"]:
            cur = best.get(node["id"])
            if cur is None or _beats(f, cur[0]):
                best[node["id"]] = (f, node)
    return [best[i][1] for i in sorted(best)]


def _edge_key(edge):
    return (edge["type"], edge["from"], edge["to"],
            json.dumps(edge, sort_keys=True, ensure_ascii=False, allow_nan=False))


def _stage(winners):
    merged = {
        "sysgraph_version": OUTPUT_VERSION,
        "merged_from": [{"repo": f["repo"], "commit": f["commit"],
                         "commit_time": f["commit_time"], "file": f["file"]} for f in winners],
        "nodes": _merge_nodes(winners),
        "edges": sorted((e for f in winners for e in f["edges"]), key=_edge_key),
    }
    dump = {"sort_keys": True, "indent": 2, "ensure_ascii": False, "allow_nan": False}
    return {_UNRESOLVED: (json.dumps({"unresolved": []}, **dump) + "\n").encode("utf-8"),
            _MERGED: (json.dumps(merged, **dump) + "\n").encode("utf-8")}


def _fsync_dir(directory):
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path, data):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(directory)


def _inside(child, parent):
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def _check_out(out, fragments_dir):
    """Return an error string or None; runs before any read or write."""
    if os.path.islink(out):
        return "--out %s is a symlink; name a real directory" % _clean(out)
    out_real = os.path.realpath(out)
    if _inside(out_real, os.path.realpath(fragments_dir)):
        return "--out %s equals or is inside the fragments directory" % _clean(out)
    for name in (_MERGED, _UNRESOLVED):
        target = os.path.join(out_real, name)
        try:
            st = os.lstat(target)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            return "%s exists and is not a regular file (symlink or directory); refusing" % _clean(target)
    if os.path.exists(out_real) and not os.path.isdir(out_real):
        return "--out %s exists and is not a directory" % _clean(out)
    return None


def _parse(argv):
    if len(argv) < 2 or argv[0] != "merge":
        raise _Usage("expected: merge <fragments-dir> --out <dir> [--max-bytes N]")
    fragments, out, max_bytes, i = None, None, None, 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--out" or arg == "--max-bytes":
            if i + 1 >= len(argv):
                raise _Usage("%s needs a value" % arg)
            if arg == "--out":
                out = argv[i + 1]
            else:
                try:
                    max_bytes = int(argv[i + 1])
                except ValueError:
                    raise _Usage("--max-bytes needs a positive integer")
                if max_bytes <= 0:
                    raise _Usage("--max-bytes needs a positive integer")
            i += 2
        elif arg.startswith("-"):
            raise _Usage("unknown flag %s" % _clean(arg))
        elif fragments is None:
            fragments = arg
            i += 1
        else:
            raise _Usage("unexpected argument %s" % _clean(arg))
    if fragments is None or out is None:
        raise _Usage("merge needs <fragments-dir> and --out <dir>")
    return fragments, out, max_bytes


def _report(state, winners, out):
    n = len(winners)
    superseded = sum(state["valid"][f["repo"]] for f in winners) - n
    lines = ["merge: %d fragment file(s) read; %d %s contributed; %d superseded; "
             "%d skipped corrupt; %d refused version" % (
                 state["read"], n, "repository" if n == 1 else "repositories", superseded,
                 len(state["corrupt"]), len(state["refused"]))]
    lines += ["skipped corrupt: %s: %s" % (_clean(f), r) for f, r in state["corrupt"]]
    lines += ["refused version: %s: %s" % (_clean(f), r) for f, r in state["refused"]]
    for repo in sorted(state["poison"]):
        if repo in state["valid"]:
            lines.append("excluded repository: %s: %s was skipped or refused, so no older fragment "
                         "is used" % (_clean(repo), ", ".join(_clean(x) for x in state["poison"][repo])))
    for f in winners:
        tied = sorted(state["tied"][f["repo"]])
        if len(tied) > 1:
            lines.append("tie: %s: %s have identical (commit_time, commit); chose %s" % (
                _clean(f["repo"]), ", ".join(_clean(t) for t in tied), _clean(f["file"])))
    if out:
        lines.append("wrote: %s, %s" % (out + "/" + _MERGED, out + "/" + _UNRESOLVED))
    else:
        lines.append("no repository contributed; wrote nothing")
    return "\n".join(lines) + "\n"


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        sys.stdout.write(USAGE)
        return 0
    try:
        fragments, out, max_bytes = _parse(argv)
    except _Usage as exc:
        sys.stderr.write("sysgraph_merge: %s\n%s" % (exc, USAGE))
        return 2
    if max_bytes is None:
        max_bytes = default_max_bytes()
        if max_bytes is None:
            sys.stderr.write("sysgraph_merge: cannot derive a size limit on this platform; "
                             "pass --max-bytes N\n")
            return 2
    if not os.path.isdir(fragments):
        sys.stderr.write("sysgraph_merge: fragments directory %s is missing or not a directory\n"
                         % _clean(fragments))
        return 2
    problem = _check_out(out, fragments)
    if problem:
        sys.stderr.write("sysgraph_merge: %s\n" % problem)
        return 2
    try:
        state = _select(fragments, max_bytes)
    except OSError as exc:
        sys.stderr.write("sysgraph_merge: cannot list %s: %s\n" % (_clean(fragments), _clean(exc)))
        return 2
    winners = [state["winner"][r] for r in sorted(state["winner"]) if r not in state["poison"]]
    if not winners:
        sys.stdout.write(_report(state, winners, None))
        return 1
    try:
        staged = _stage(winners)
    except (ValueError, RecursionError, UnicodeEncodeError) as exc:
        sys.stderr.write("sysgraph_merge: cannot serialise the merged graph: %s\n" % _clean(exc))
        return 2
    out_real = os.path.realpath(out)
    try:
        os.makedirs(out_real, exist_ok=True)
        for name in (_UNRESOLVED, _MERGED):
            _atomic_write(os.path.join(out_real, name), staged[name])
    except OSError as exc:
        sys.stderr.write("sysgraph_merge: write failed: %s\n" % _clean(exc))
        return 2
    sys.stdout.write(_report(state, winners, out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

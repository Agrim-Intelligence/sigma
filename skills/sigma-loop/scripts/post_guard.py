# SPDX-License-Identifier: MIT
"""Opt-in guard: refuse text bound for GitHub or a commit when it matches the owner's private-reference
patterns (#375). `tools/leak_refs.py` finds references already posted; this stops a NEW one at the
loop's posting chokepoints (`sources.GitHubSource._run`, `work._run`) and two call-site hooks
(`upstream._file_upstream`, `feature_propagate._write_remote`).

OPT-IN. Nothing happens unless `SIGMA_LEAK_PATTERNS` is set (there is no default path). When set, the
patterns load LAZILY, on the first WRITE verb only, through `leak_refs.load_patterns` (so a file inside a
git work tree, an unreadable file or a bad line refuses). A read verb never loads them. FAIL CLOSED: a
write whose patterns cannot be loaded, or whose body comes from stdin (unreadable here), is refused.

OUTPUT. `PostRefused` (a RuntimeError, so every existing except path sees an ordinary failure) names
the flag, the text's line and the pattern's line number, never the matched text, never a `#N` or SHA.

NOT COVERED (named): `sync.py`/`ledger.py` ops-branch commits, `verify_merge --fill` (its text is the
commit messages, which `git commit` guards), `gh api --input -`, labels and milestones, any post made
outside the loop's code. Cost per write: O(len(text) x patterns); the stdlib regex has no timeout.
"""
import base64
import importlib.util
import os
import pathlib

ENV = "SIGMA_LEAK_PATTERNS"
_ROOT = pathlib.Path(__file__).resolve().parents[3]
_GH_WRITES = {"issue": {"comment", "create", "edit", "close"},
              "pr": {"create", "edit", "comment", "close", "review", "merge"},
              "release": {"create", "edit"}}
_TEXT = {"--body": 1, "-b": 1, "--title": 1, "-t": 1, "--comment": 1, "-c": 1, "--subject": 1,
         "--message": 1, "-m": 1}
_FILE = {"--body-file", "-F", "--file"}
_FIELD = {"-f", "--raw-field", "-F", "--field"}
_SHORT_TEXT = "mbtc"
_GLOBAL_VALUED = {"-R", "--repo", "-C", "--hostname"}
_cache = {}


class PostRefused(RuntimeError):
    pass


def _leak_refs():
    path = _ROOT / "tools" / "leak_refs.py"
    if not path.is_file():
        raise PostRefused("post_guard: REFUSED [no-leak-refs] %s is set but tools/leak_refs.py is absent" % ENV)
    spec = importlib.util.spec_from_file_location("_post_guard_leak_refs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patterns(source):
    path = os.path.expanduser(source)
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        key = (path, None, None)
    if key in _cache:
        return _cache[key]
    lr = _leak_refs()
    try:
        pats = lr.load_patterns(source, lr.Ctx(lr._real_run, lambda _s: None))
    except lr.Refused as exc:
        raise PostRefused("post_guard: REFUSED [%s] %s" % (exc.code, exc.detail))
    _cache[key] = (lr, pats)
    return _cache[key]


def _verb(toks):
    """-> (tool, rest) with leading global flags (`gh -R o/r`, `git -C dir`) skipped, or (None, None)."""
    tool = "gh"
    if toks and toks[0] in ("gh", "git"):
        tool, toks = toks[0], toks[1:]
    i = 0
    while i < len(toks) and toks[i].startswith("-"):
        i += 1 if "=" in toks[i] or toks[i] not in _GLOBAL_VALUED else 2
    return tool, toks[i:]


def _read(label, src):
    if src is None or src == "-":
        raise PostRefused("post_guard: REFUSED [stdin-body] %s - reads stdin, which the guard "
                          "cannot inspect; pass the text with --body" % label)
    try:
        return pathlib.Path(src).read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise PostRefused("post_guard: REFUSED [body-file-unreadable] %s cannot be read" % label)


def _api(toks):
    """-> [(label, text)] when this `gh api` call writes, else None. A write is an explicit POST, PUT,
    PATCH or DELETE, a body-bearing call with no method (gh defaults it to POST), or a graphql
    mutation; a GET and a graphql query are reads and are never inspected."""
    method, fields, files, graphql = "", [], [], toks[1:2] == ["graphql"]
    i = 1
    while i < len(toks):
        flag, eq, val = toks[i].partition("=") if toks[i].startswith("--") else (toks[i], "", "")
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        got = val if eq else nxt
        step = 1 if eq else 2
        if flag in ("-X", "--method") and got:
            method = got.upper()
        elif flag in _FIELD and got:
            k, _, v = got.partition("=")
            if v.startswith("@"):
                v = _read(flag + " " + k, v[1:])
            elif k == "content":
                try:
                    v = base64.b64decode(v).decode("utf-8", "replace")
                except Exception:       # noqa: BLE001 - undecodable stays as the raw string
                    pass
            fields.append(("%s %s" % (flag, k), v))
        elif flag == "--input":
            files.append((flag, _read(flag, got)))
        else:
            step = 1
        i += step
    if graphql:
        return fields if any("mutation" in v.lower() for _l, v in fields) else None
    if method in ("POST", "PUT", "PATCH", "DELETE") or (not method and (fields or files)):
        return fields + files
    return None


def _texts(argv):
    """-> [(label, text)] for a write verb, or None for anything else (a read, an unknown verb)."""
    tool, toks = _verb([str(a) for a in argv])
    if tool == "git":
        if "commit" not in toks:
            return None
    elif toks[:1] == ["api"]:
        return _api(toks)
    elif not (len(toks) > 1 and toks[1] in _GH_WRITES.get(toks[0], ())):
        return None
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        flag, eq, val = t.partition("=") if t.startswith("--") else (t, "", "")
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if flag in _FILE:
            out.append((flag, _read(flag, val if eq else nxt)))
            i += 1 if eq else 2
        elif flag in _TEXT and (eq or nxt is not None):
            out.append((flag, val if eq else nxt))
            i += 1 if eq else 2
        elif len(t) > 2 and t[0] == "-" and t[1] != "-":
            # a short-flag cluster: `-am MSG`, `-mMSG`, `-m=MSG`, `-bMSG`. Over-scanning is safe.
            k = next((j for j, ch in enumerate(t[1:], 1) if ch in _SHORT_TEXT), 0)
            if k and k + 1 < len(t):
                out.append((t[:2] if k == 1 else "-" + t[k], t[k + 1:].lstrip("=")))
            elif k and nxt is not None:
                out.append(("-" + t[k], nxt))
                i += 1
            i += 1
        else:
            i += 1
    return out


def check(argv):
    """Raise PostRefused when `argv` (with or without a leading gh/git) would post a pattern match."""
    source = os.environ.get(ENV)
    if not source:
        return
    items = _texts(argv)
    if not items:
        return
    lr, pats = _patterns(source)
    found = []
    for label, text in items:
        for line, number, _span in lr._dedupe(lr.find_hits(text, pats)):
            found.append("%s line %d pattern %d" % (label, line, number))
    if found:
        raise PostRefused("post_guard: REFUSED [leak] %s; the text was not sent (patterns come from %s)"
                          % ("; ".join(found[:5]), ENV))

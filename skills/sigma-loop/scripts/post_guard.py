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
import unicodedata

ENV = "SIGMA_LEAK_PATTERNS"
_ROOT = pathlib.Path(__file__).resolve().parents[3]
_GH_WRITES = {"issue": {"comment", "create", "edit", "close", "reopen", "lock", "unlock"},
              "pr": {"create", "edit", "comment", "close", "reopen", "review", "merge", "ready"},
              "release": {"create", "edit"}}
_GIT_WRITES = {"commit", "tag", "merge", "notes"}
#: Long flags that carry text, a text FILE, or another value (so its value is not read as a verb).
_LONG_TEXT = {"--body", "--title", "--comment", "--subject", "--message", "--notes"}
_LONG_FILE = {"--body-file", "--file", "--notes-file"}
_LONG_VALUED = _LONG_TEXT | _LONG_FILE | {"--method", "--field", "--raw-field", "--input", "--header",
                                         "--repo", "--hostname", "--label", "--assignee", "--base",
                                         "--head", "--jq", "--template", "--milestone", "--reason"}
#: Short flags, as gh and git parse them (pflag / git's parse-options): a bundle such as `-am MSG` or
#: `-mMSG` or `-fbody=X` is read letter by letter and the first VALUED letter takes the rest of the
#: bundle (or the next argument) as its value. TEXT letters always take the next argument.
_SHORT_TEXT = {"gh": "btcnm", "git": "m"}
_SHORT_VALUED = {"gh": "fFXHRalepdqjwsiou", "git": "CcFStu"}
_GIT_GLOBAL_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
_GH_GLOBAL_VALUED = {"-R", "--repo", "--hostname"}
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


def _tool_and_args(argv):
    toks = [str(a) for a in argv]
    tool = "gh"
    if toks:
        base = os.path.basename(toks[0]).lower()
        if base in ("gh", "gh.exe", "git", "git.exe"):
            tool, toks = base.split(".")[0], toks[1:]
    glob = _GIT_GLOBAL_VALUED if tool == "git" else _GH_GLOBAL_VALUED
    i = 0
    while i < len(toks) and toks[i].startswith("-"):
        i += 1 if "=" in toks[i] or toks[i] not in glob else 2
    return tool, toks[i:]


def _pairs(tool, toks):
    """-> (pairs, positionals): pairs are (flag, value|None) in order; a long flag is `--k=v` or
    `--k v`; a short bundle is read the way pflag reads it."""
    pairs, pos, i = [], [], 0
    text, valued = _SHORT_TEXT[tool], _SHORT_VALUED[tool]
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t.startswith("--") and len(t) > 2:
            flag, eq, val = t.partition("=")
            if eq:
                pairs.append((flag, val))
            elif flag in _LONG_VALUED and nxt is not None:
                pairs.append((flag, nxt))
                i += 1
            else:
                pairs.append((flag, None))
        elif t.startswith("-") and len(t) > 1:
            for j, c in enumerate(t[1:], 1):
                if c in text or c in valued:
                    rest = t[j + 1:]
                    if rest:
                        pairs.append(("-" + c, rest[1:] if rest.startswith("=") else rest))
                    elif nxt is not None and (c in text or c in "fFXH" or not nxt.startswith("-")):
                        pairs.append(("-" + c, nxt))
                        i += 1
                    else:
                        pairs.append(("-" + c, None))
                    break
        else:
            pos.append(t)
        i += 1
    return pairs, pos


def _read(label, src):
    if src is None or src == "-":
        raise PostRefused("post_guard: REFUSED [stdin-body] %s - reads stdin, which the guard "
                          "cannot inspect; pass the text with --body" % label)
    try:
        return pathlib.Path(src).read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise PostRefused("post_guard: REFUSED [body-file-unreadable] %s cannot be read" % label)


def _api(pairs, pos):
    """-> [(label, text)] when this `gh api` call writes, else None. A write is an explicit POST, PUT,
    PATCH or DELETE, a body-bearing call with no method (gh defaults it to POST), or a graphql
    mutation (in a field or in an --input file); a GET and a graphql query are reads."""
    method, items = "", []
    for flag, val in pairs:
        if flag in ("-X", "--method") and val:
            method = val.upper()
        elif flag in ("-f", "-F", "--field", "--raw-field") and val:
            k, _, v = val.partition("=")
            if v.startswith("@") and flag in ("-F", "--field"):
                v = _read(flag + " " + k, v[1:])
            elif k == "content":
                try:
                    v = base64.b64decode(v).decode("utf-8", "replace")
                except Exception:       # noqa: BLE001 - undecodable stays as the raw string
                    pass
            items.append(("%s %s" % (flag, k), v))
        elif flag == "--input":
            items.append((flag, _read(flag, val)))
    if pos[:2] == ["api", "graphql"] or pos[:1] == ["graphql"]:
        return items if any("mutation" in v.lower() for _l, v in items) else None
    has_body = any(f == "--input" or f in ("-f", "-F", "--field", "--raw-field") for f, _v in pairs)
    if method in ("POST", "PUT", "PATCH", "DELETE") or (not method and has_body):
        return items
    return None


def _texts(argv):
    """-> [(label, text)] for a write verb, or None for anything else (a read, an unknown verb)."""
    tool, toks = _tool_and_args(argv)
    if tool == "git":
        if not toks or toks[0] not in _GIT_WRITES:
            return None
    elif toks[:1] == ["api"]:
        pairs, pos = _pairs("gh", toks)
        return _api(pairs, pos)
    elif not (len(toks) > 1 and toks[1] in _GH_WRITES.get(toks[0], ())):
        return None
    pairs, pos = _pairs(tool, toks)
    out = []
    for flag, val in pairs:
        if val is None:
            continue
        if flag in _LONG_FILE or flag == "-F":
            out.append((flag, _read(flag, val)))
        elif flag in _LONG_TEXT or flag[1:] in _SHORT_TEXT[tool] and not flag.startswith("--"):
            out.append((flag, val))
    if tool == "gh" and toks[0] == "release":
        out.extend(("release argument", p) for p in pos[2:])    # tag and title are positional
    return out


_STRIP = {"Cf", "Cc"}


def _norm(text):
    """NFKC, then drop format and control characters (zero-width space and joiners, BOM, soft hyphen)
    except line breaks and tabs, so a fullwidth or zero-width-split slug still matches."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(c for c in text if c in "\n\t\r" or unicodedata.category(c) not in _STRIP)


def check(argv):
    """Raise PostRefused when `argv` (with or without a leading gh/git) would post a pattern match."""
    source = os.environ.get(ENV, "").strip()    # empty or blank counts as unset
    if not source:
        return
    items = _texts(argv)
    if not items:
        return
    lr, pats = _patterns(source)
    found = []
    for label, text in items:
        hits = lr.find_hits(text, pats) + lr.find_hits(_norm(text), pats)
        for line, number, _span in lr._dedupe(sorted(hits)):
            found.append("%s line %d pattern %d" % (label, line, number))
    if found:
        raise PostRefused("post_guard: REFUSED [leak] %s; the text was not sent (patterns come from %s)"
                          % ("; ".join(found[:5]), ENV))

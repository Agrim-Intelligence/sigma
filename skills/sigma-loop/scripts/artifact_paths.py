"""Keep absolute local paths out of the artifacts a goal commits (#2243).

THE PROBLEM. A goal's plan (`.sdlc/plans/<goal>.md`), research dossier (`.sdlc/research/<goal>.md`) and
acceptance record (`.sdlc/acceptance/<goal>.md`) are committed in the goal's PR. A phase agent writes them from
what it sees, and what it sees is absolute: the worktree path, the plugin's script paths, a tool's printed
output. On a public repository that publishes the operator's user name and directory layout in history that
cannot be rewritten.

THE MECHANISM, in Sigma's own Python (a hook could only ever be an accelerator). `work.py commit` is the one
place every goal's artifacts pass before they can be staged, so it calls `scrub_goal` just before `git add -A`.

WHAT COUNTS AS A LEAK. A home-directory path: `/home/<name>/...`, its macOS twin (the same shape under the
macOS users directory), `/root/...`, or the Windows equivalent with either slash. A bare `~/...` names nobody and is left alone, as is an
ordinary URL path or any other absolute path: this is deliberately NOT `loop._redact_detail`'s breadth, which
would turn `GET /v1/orders/me` in a plan into `[path]`.

WHAT A LEAK BECOMES.
  * under this repository's root or the goal's worktree root -> the repo-relative path (`.sdlc/work/1`);
  * a plugin script path (`.../.claude/plugins/.../skills/x`) -> `<plugin>/skills/x`;
  * any other home path -> `[path]` (the placeholder `_redact_detail` already uses).

SCOPE. Only this goal's plan and research files in the worktree being committed (the same resolver the
reviewer brief uses), plus the main-checkout copy when it was byte-identical to the worktree one, so the two
copies keep agreeing. Never source files, never another goal's artifacts.

WHERE IT REFUSES INSTEAD OF REWRITING (never a silent commit of a leak):
  * the acceptance record: its criteria are append-only against the issue, so a rewrite would read as tamper;
  * a plan whose exact bytes are already bound elsewhere under `.sdlc/state` (a recorded plan review, a seal):
    rewriting would invalidate that record, so a person edits the plan and re-reviews it;
  * a file that cannot be read as UTF-8 or written.
Each refusal names the file and line.

NOT CLAIMED: a raw `git commit` bypasses `work.py commit`; history already published is not rewritten.
"""
import hashlib
import importlib.util
import pathlib
import re

#: The three artifact directories under `.sdlc/`, and whether a leak in each is rewritten or refused.
SUBDIRS = (("plans", True), ("research", True), ("acceptance", False))

_TAIL = r"[^\s'\"`<>)\]|,;]*"
#: One clause per path shape so each can be broken, and seen red, on its own.
_POSIX = r"(?<![\w.:/~-])/(?:" + "Us" + r"ers|home)/[^/\s'\"`<>)\]|,;]+" + r"(?:/" + _TAIL + r")?"
_ROOT = r"(?<![\w.:/~-])/root(?:/" + _TAIL + r")"
_WINDOWS = r"(?<![\w])[A-Za-z]:[\\/]" + "Us" + r"ers[\\/][^\\/\s'\"`<>)\]|,;]+" + r"(?:[\\/]" + _TAIL + r")?"
_LEAK_RE = re.compile("(?:" + "|".join((_POSIX, _ROOT, _WINDOWS)) + ")")
_PLUGIN_RE = re.compile(r"[\\/]\.claude[\\/]plugins[\\/].*?[\\/]((?:skills|hooks)[\\/].*)$")
_TRIM = ".:!?"
PLACEHOLDER = "[path]"


def _norm(p):
    return str(p).replace("\\", "/").rstrip("/")


def _roots(paths):
    out = []
    for p in paths:
        if not p:
            continue
        for v in (str(p), str(pathlib.Path(p).resolve())):
            v = _norm(v)
            if v and v not in out:
                out.append(v)
    return sorted(out, key=len, reverse=True)


def _replacement(token, roots):
    trail = ""
    while token and token[-1] in _TRIM:
        trail = token[-1] + trail
        token = token[:-1]
    norm = _norm(token)
    for root in roots:
        if norm == root:
            return "." + trail
        if norm.startswith(root + "/"):
            return norm[len(root) + 1:] + trail
    m = _PLUGIN_RE.search(token)
    if m:
        return "<plugin>/" + m.group(1).replace("\\", "/") + trail
    return PLACEHOLDER + trail


def _regex(rts):
    """The leak pattern plus one clause per known root: this repository's own root and the goal's worktree are
    leaks wherever they live (`/srv/ci/repo`, a container's `/workspace`), not only under a home directory."""
    if not rts:
        return _LEAK_RE
    own = "|".join(r"(?<![\w.:/~-])" + re.escape(r) + r"(?:/" + _TAIL + r")?(?![\w-])" for r in rts)
    return re.compile("(?:" + _LEAK_RE.pattern + "|" + own + ")")


def scrub_text(text, roots=()):
    """(new_text, count): every home path rewritten. Idempotent: the output holds no home path."""
    rts = _roots(roots)
    n = [0]

    def sub(m):
        n[0] += 1
        return _replacement(m.group(0), rts)
    return _regex(rts).sub(sub, text), n[0]


def find_leaks(text, roots=()):
    """[(line_number, token)] for every home path (or known-root path) in the text."""
    rx = _regex(_roots(roots))
    return [(i, m.group(0)) for i, line in enumerate(text.splitlines(), 1) for m in rx.finditer(line)]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bound_elsewhere(sdlc_dir, digest):
    """True when the plan's exact sha256 appears in a file under `<sdlc>/state` (a review record, a seal)."""
    base = pathlib.Path(sdlc_dir) / "state"
    if not base.is_dir():
        return False
    needle = digest.encode()
    for f in base.rglob("*"):
        try:
            if f.is_file() and f.stat().st_size <= 2_000_000 and needle in f.read_bytes():
                return True
        except OSError:
            continue
    return False


def scrub_goal(sdlc_dir, worktree, goal):
    """Scrub this goal's artifacts in `worktree`. Returns (note, refusal): `note` is one line when anything was
    rewritten ("" otherwise), `refusal` is a REFUSED line (or "") and means nothing may be committed."""
    rc = _load("review_context")
    name = pathlib.Path(sdlc_dir).name
    wt_sdlc = pathlib.Path(worktree) / name
    roots = (worktree, pathlib.Path(sdlc_dir).resolve().parent)
    files = paths = 0
    for sub, rewrite in SUBDIRS:
        wt = rc.phase_doc_file(wt_sdlc, goal, sub)
        if not wt:
            continue
        try:
            raw = pathlib.Path(wt).read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return "", f"REFUSED: cannot check {wt} for absolute local paths ({exc}) (nothing committed)"
        leaks = find_leaks(text, roots)
        if not leaks:
            continue
        line, token = leaks[0]
        where = f"{wt}:{line}"
        if not rewrite:
            return "", (f"REFUSED: {where} holds an absolute local path ({len(leaks)} in all, first {token!r}); the "
                        f"acceptance record is append-only so Sigma will not rewrite it: replace it with a "
                        f"repo-relative path or `[path]` by hand (nothing committed)")
        if sub == "plans" and _bound_elsewhere(sdlc_dir, hashlib.sha256(raw).hexdigest()):
            return "", (f"REFUSED: {where} holds an absolute local path ({len(leaks)} in all, first {token!r}) and the "
                        f"plan's exact bytes are recorded in a review or seal; rewriting would invalidate that "
                        f"record: edit the plan to repo-relative paths and re-run its review (nothing committed)")
        new, count = scrub_text(text, roots)
        try:
            pathlib.Path(wt).write_bytes(new.encode("utf-8"))
            main = rc.phase_doc_file(sdlc_dir, goal, sub)
            if main and pathlib.Path(main).resolve() != pathlib.Path(wt).resolve() \
                    and pathlib.Path(main).read_bytes() == raw:
                pathlib.Path(main).write_bytes(new.encode("utf-8"))
        except OSError as exc:
            return "", f"REFUSED: cannot rewrite {where} ({exc}) (nothing committed)"
        files += 1
        paths += count
    if not files:
        return "", ""
    return f"scrubbed {paths} absolute local path(s) from {files} artifact file(s) before commit", ""

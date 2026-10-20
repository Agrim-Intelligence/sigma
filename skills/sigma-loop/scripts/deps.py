"""Read every issue reference in a goal's ticket, and turn a prose dependency into a marker (#1885).

The pick gate and the dependency map read an edge only from an explicit form
(`blocker_scan.EXPLICIT_TRIGGERS`: `**Blocked by:** #N`, `depends on #N`, `follow-up to #N`, ...), and
the map reads the BODY only. A ticket can still say what it waits for in words no pattern should be
trusted with ("this can only start once the zone work in #12 is merged"), or say it in a comment the
map never reads. Widening the pattern is the wrong fix: the residue is documented in `blocker_scan`
and a phantom edge writes to an unrelated issue. So this module hands the WHOLE ticket to the agent:

  report  every `#N` in the title, the body and every comment, each classified by what the machinery
          already does with it, with the sentence it sits in. The agent reads each one and decides.
  mark    writes the one form everything reads, `**Blocked by:** #N`, into the goal's BODY through
          `source.append_to_body` (which refuses a cycle before writing and inserts the edge into the
          stored map), and leaves a note saying why. Idempotent: an edge the body already carries is
          `ALREADY`, and nothing is written. The referenced number must be an OPEN issue or pull request
          (one read); a closed, missing or unreadable one is `REFUSED`, so a typo never becomes an edge.
          A checkpoint (#1904, label `sdlc:checkpoint`) is an open issue like any other, so a wait on it is
          written the same way; the answer says the issue is a checkpoint.

The judgment is the agent's, and it costs one read of the ticket per goal (one issue read plus its
comment pages through `gh_api`), paid once at pick time. What makes it safe is that the agent's
decision lands as the canonical marker, which every reader treats the same way, instead of as a
new inference rule inside the scanners.

`read_as` values, per referenced `#N` (never the goal itself, never `owner/repo#N`):
  edge          an explicit form in the title or body: the gate and the map already hold it
  comment-edge  an explicit form only in a comment: the map never reads it, and the gate reads it only
                in the newest 20 comments' first 500 characters, and never a follow-up form there
  weak          a weak word (`needs`, `after`, `requires`, `waiting on`) before it, or a loose phrasing
                `chain_waits` knows ("only once #N", "hold on #N", "#N is a blocker", ...): advisory only
  mention       any other reference: read by nothing

GitHub mode only: a local-goals source prints `UNSUPPORTED` and exits 2. Snippets go to stdout for
the agent and are never stored; they pass through `scrub.scrub` first.

Usage:
  deps.py report <sdlc_dir> <goal>
  deps.py mark <sdlc_dir> <goal> <ref> <reason...>

Scale: `report` is one issue read plus ceil(comments/100) comment pages, whatever the board size;
`mark` adds one goal read, one read of the referenced issue, one body write and one comment.
"""
import importlib.util
import json
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


blocker_scan = _load("blocker_scan")
_scrub = _load("scrub")
chain_waits = _load("chain_waits")

_REF_RE = re.compile(r"#(\d+)\b")
SNIPPET_CHARS = 160
SNIPPETS_PER_REF = 2
_RANK = {"edge": 0, "comment-edge": 1, "weak": 2, "mention": 3}
USAGE = "usage: deps.py report <sdlc_dir> <goal> | deps.py mark <sdlc_dir> <goal> <ref> <reason...>"


def _snippet(text, start, end):
    a = max(0, start - SNIPPET_CHARS // 2)
    b = min(len(text), end + SNIPPET_CHARS // 2)
    return _scrub.scrub(" ".join(text[a:b].split()))


def _is_foreign(text, start):
    """`owner/repo#N` or `repo#N`: the character before `#` belongs to a name (same rule as
    `blocker_scan.is_cross_repo`, which takes a trigger match rather than a bare reference)."""
    i = start - 1
    return i >= 0 and (text[i].isalnum() or text[i] in "_/-.")


def analyse(goal, title, body, comments):
    """Pure. `comments` is a list of comment body strings. -> list of
    `{"ref", "read_as", "where", "snippets"}`, ordered by `read_as` (edge first), then ref."""
    goal = str(goal).lstrip("#")
    texts = [("body", blocker_scan.strip_unpark_qa((title or "") + "\n" + (body or "")))]
    texts += [("comment", blocker_scan.strip_unpark_qa(c or "")) for c in comments or []]
    refs = {}
    for where, text in texts:
        explicit = {m.group(2) for m in blocker_scan._EXPLICIT_BLOCK_RE.finditer(text)
                    if not blocker_scan.is_cross_repo(text, m)}
        weak = ({m.group(2) for m in blocker_scan._BLOCK_RE.finditer(text)
                 if not blocker_scan.is_cross_repo(text, m)} | chain_waits.loose_refs(text)) - explicit   # #1902
        for m in _REF_RE.finditer(text):
            ref = m.group(1)
            if ref == goal or _is_foreign(text, m.start()):
                continue
            kind = ("edge" if where == "body" else "comment-edge") if ref in explicit else (
                "weak" if ref in weak else "mention")
            rec = refs.setdefault(ref, {"ref": ref, "read_as": kind, "where": [], "snippets": []})
            if _RANK[kind] < _RANK[rec["read_as"]]:
                rec["read_as"] = kind
            if where not in rec["where"]:
                rec["where"].append(where)
            if len(rec["snippets"]) < SNIPPETS_PER_REF:
                rec["snippets"].append(_snippet(text, m.start(), m.end()))
    return sorted(refs.values(), key=lambda r: (_RANK[r["read_as"]], int(r["ref"])))


def _read(source, goal):
    data = source._read_issue(goal, ["title", "body", "state", "comments"], comment_limit=None)
    comments = [c.get("body") or "" for c in (data.get("comments") or []) if isinstance(c, dict)]
    return data.get("title") or "", data.get("body") or "", comments


def report(source, goal):
    title, body, comments = _read(source, goal)
    refs = analyse(goal, title, body, comments)
    counts = {k: sum(1 for r in refs if r["read_as"] == k) for k in _RANK}
    return {"goal": str(goal).lstrip("#"), "comments_read": len(comments), "counts": counts, "refs": refs}


def _plain(reason):
    """A reason with every `#` removed, so the note can never itself mint an edge at check time."""
    return " ".join(str(reason or "").replace("#", "issue ").split())[:300]


def mark(source, goal, ref, reason):
    """-> ("MARKED"|"ALREADY"|"REFUSED", detail). Writes nothing unless the result is MARKED."""
    goal, ref = str(goal).lstrip("#"), str(ref).lstrip("#")
    if not ref.isdigit() or ref == goal:
        return "REFUSED", "the reference must be another issue's number"
    _title, body, _comments = _read(source, goal)
    if any(r["ref"] == ref for r in blocker_scan.extract_refs(body, self_ref=goal)):
        return "ALREADY", "the body already carries an explicit edge to issue %s" % ref
    try:
        data = source._read_issue(ref, ["state", "labels"])
        state = str(data.get("state") or "").upper()
        names = {(l.get("name") if isinstance(l, dict) else str(l)) for l in (data.get("labels") or [])}
    except Exception as exc:                       # noqa: BLE001 - unreadable is a refusal, never a guess
        return "REFUSED", "issue %s could not be read (%s)" % (ref, type(exc).__name__)
    if state != "OPEN":
        return "REFUSED", "issue %s is not open (state %s)" % (ref, state or "unknown")
    try:
        source.append_to_body(goal, "**Blocked by:** #%s" % ref)
    except ValueError as exc:                      # dag_insert.CycleRefused is a ValueError
        return "REFUSED", str(exc)
    note = "Dependency marker added to the body for issue %s, read from the ticket's prose." % ref
    if _plain(reason):
        note += " Reason: " + _plain(reason)
    try:
        source.note(goal, note)
    except Exception as exc:                       # noqa: BLE001 - the marker is the record; the note is narration
        return "MARKED", "marker written; the explanatory note failed (%s)" % type(exc).__name__
    checkpoint = getattr(source, "checkpoint_label", None)
    if checkpoint and checkpoint in names:          # #1904: a wait on a checkpoint is an ordinary edge
        return "MARKED", ("issue %s is a checkpoint and now holds this goal until its unit lands or a person "
                          "closes it" % ref)
    return "MARKED", "issue %s now blocks this goal" % ref


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) < 4 or argv[1] not in ("report", "mark") or (argv[1] == "mark" and len(argv) < 5):
        print(USAGE, file=sys.stderr)
        return 2
    state, sources = _load("state"), _load("sources")
    config = state.load_config(argv[2])
    source = sources.get_source(argv[2], config)
    if not hasattr(source, "_read_issue") or not hasattr(source, "append_to_body"):
        print("UNSUPPORTED deps.py reads GitHub issues only; this project uses local goal files")
        return 2
    if argv[1] == "report":
        print(json.dumps(report(source, argv[3]), ensure_ascii=False, sort_keys=True))
        return 0
    verdict, detail = mark(source, argv[3], argv[4], " ".join(argv[5:]))
    print("%s %s" % (verdict, detail))
    return 0 if verdict in ("MARKED", "ALREADY") else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

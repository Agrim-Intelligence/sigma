"""chain_waits.py -- waits that no edge reader sees, shown to the person instead of trusted (#1902).

The gate, the map and the label drain read a dependency only from an explicit form in the title or body
(`blocker_scan.EXPLICIT_TRIGGERS`). Real tickets say more: "Blocked by: #N added" in a comment,
"hold on #N before any paid run", "only once #A, #B and #C are closed", "blocked on #N and #M". A chain
planned from edges alone starts on work that cannot proceed. Widening the shared vocabulary is the wrong fix
(a phantom edge parks a goal for good and writes to an unrelated issue, see `blocker_scan`), so Chain's plan
READS these texts itself and reports what it found. It never turns any of it into an edge:

  unconfirmed wait  written in a COMMENT by the issue's assignee or an OWNER/MEMBER/COLLABORATOR (the
                    gate's own trust set, `sources.TRUSTED_ASSOCIATIONS`): an explicit form, or a loose
                    phrasing below. The sheet lists every one in ONE block, with the two ways to settle it:
                    `deps.py mark` (writes the marker every reader trusts) or the existing dismissal marker.
  weak mention      a loose phrasing in the BODY, or follow-up wording in a comment (advisory, as
                    `blocker_scan` says): shown, counted, never asked and never blocking.

Why not an edge, even from a trusted author: an edge makes Chain label, comment on and arm the referenced
issue, the map does not store comment text, and only the person can tell "Blocked by #N" about THIS goal
from a remark about another one. After `deps.py mark` the body carries the line and every reader agrees.

Comments from anyone else are ignored and counted (the gate does the same, #709). Sigma's own comments
(a marker, or a fixed head below) are skipped; a park or fail prefix is stripped, as the gate does.

Loose forms (closed set; each is a SHAPE with a direction, none is a bare mention):
  list          `#M` / `#K` after an explicit or loose reference: "blocked on #N and #M", "#A, #B and #C"
  far           blocked by|on|until / depends on|upon, then up to 100 characters (clause punctuation allowed)
  only-once     "only once|when|after ... #N"          ("starts only once #N has landed")
  land-before   "#N ... must land|be merged ... before"  ("#N must land on main before this change")
  plug-into     "plug|hook into ... #N"                  ("these plug into #N's port")
  hold          "hold on|off|until|for ... #N"           ("hold on #N before any paid run")
  is-blocker    "#N is (now) a blocker"
  waiting-for   "waiting|wait for ... #N"
  follow-up     a follow-up form in a comment (advisory; never an unconfirmed wait)

Bounds: at most MAX_READS members are read (an issue GET plus the pages of its newest COMMENT_LIMIT comments,
so at most 3 requests each; none when the issue is known to have no comment); each comment is read to
COMMENT_CHARS and each body to BODY_CHARS; at most MAX_ITEMS findings are kept. Whatever was not read, or
cut, is named in `waits_read` and the plan is INCOMPLETE rather than silently thinner. Not measured: network
time; the call counts are arithmetic from the code and the counting fake.
"""
import importlib.util
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


blocker_scan = _load("blocker_scan")
legacy = blocker_scan.legacy

MAX_READS = 100
COMMENT_LIMIT = 100
COMMENT_CHARS = 2000
BODY_CHARS = 20000
MAX_ITEMS = 300
MAX_MORE = 20
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
#: A comment that starts like this is Sigma's own fixed text, not a person's remark.
SIGMA_HEADS = ("Sigma is arming", "Auto-unparked by Sigma", "Auto-resumed by Sigma", "Reconciled by Sigma",
               "Backlog cross-check:", "Promoted to", "Returned to", "Parked via /sigma-promote",
               "Dependency marker added")
#: Sigma's park and fail prefixes contain "needs"; they are stripped, the reason after them is kept.
OFFBOARD_PREFIXES = ("Parked by Sigma — needs human review: ",
                     "Failed in the Sigma loop — needs a fix (not a decision): ")

_I = re.IGNORECASE
_SIGMA_MARKER = re.compile(legacy.MARKER_PREFIX_RE + ":", _I)     # a `sigma:` marker: Sigma wrote this comment
_WIN = r"[^\n#,;.!?]{0,40}?"
#: forward forms: a trigger, a short window, then the first reference (group 1).
_FORWARD = {
    "far": re.compile(r"\b(?:blocked[ \t]+(?:by|on|until)|depends[ \t]+(?:on|upon))\b[^\n#]{0,100}?#(\d+)\b", _I),
    "only-once": re.compile(r"\bonly[ \t]+(?:once|when|after)\b" + _WIN + r"#(\d+)\b", _I),
    "plug-into": re.compile(r"\b(?:plugs?|plugged|hooks?|hooked)[ \t]+(?:in)?to\b" + _WIN + r"#(\d+)\b", _I),
    "hold": re.compile(r"\bhold[ \t]+(?:on|off|until|for)\b" + _WIN + r"#(\d+)\b", _I),
    "waiting-for": re.compile(r"\bwait(?:ing)?[ \t]+for\b" + _WIN + r"#(\d+)\b", _I),
}
#: reference-first forms: the reference (group 1) comes before the words that give the direction.
_REF_FIRST = {
    "land-before": re.compile(r"#(\d+)\b[^\n#.!?;]{0,60}?\b(?:must|has[ \t]+to|needs[ \t]+to|should)[ \t]+"
                              r"(?:first[ \t]+)?(?:land|be[ \t]+(?:merged|closed|done|landed|resolved|finished))"
                              r"\b[^\n#.!?;]{0,30}?\bbefore\b", _I),
    "is-blocker": re.compile(r"#(\d+)\b[^\n#.!?;]{0,30}?\b(?:is|are)[ \t]+(?:(?:now|also|still)[ \t]+)*"
                             r"(?:(?:a|the)[ \t]+)?(?:hard[ \t]+)?blockers?\b", _I),
}
#: the tail of a list: ", #M", ", and #M", " and #M", " & #M" -- tight, one reference per step.
_MORE = re.compile(r"[ \t]*(?:,[ \t]*(?:and[ \t]+)?|and[ \t]+|&[ \t]*)#(\d+)\b", _I)
FORMS = ("explicit", "list", "follow-up") + tuple(_FORWARD) + tuple(_REF_FIRST)


def _foreign(text, ref_start):
    """`owner/repo#N`: the character before `#` belongs to a name (the rule `blocker_scan.is_cross_repo` has)."""
    i = ref_start - 2
    return i >= 0 and (text[i].isalnum() or text[i] in "_/-.")


def _tail(text, pos):
    """Every reference in the list that continues at `pos`, at most MAX_MORE."""
    out = []
    while len(out) < MAX_MORE:
        m = _MORE.match(text, pos)
        if not m:
            break
        out.append(m.group(1))
        pos = m.end()
    return out


def find(text, explicit=True):
    """Pure. -> ordered `[(ref, form)]`, ref-and-form deduplicated, for every reading this module has over
    `text`. `explicit` False leaves out the explicit forms (a body already holds those as edges)."""
    text = blocker_scan.strip_unpark_qa(text or "")
    found, plain = [], set()
    for m in blocker_scan._EXPLICIT_BLOCK_RE.finditer(text):
        if _foreign(text, m.start(2)):
            continue
        if not blocker_scan.is_explicit(text, m, 0):             # a follow-up form: advisory only
            found.append((m.group(2), "follow-up"))
            continue
        plain.add(m.group(2))
        if explicit:
            found.append((m.group(2), "explicit"))
        found += [(r, "list") for r in _tail(text, m.end())]
    for form, rx in _FORWARD.items():
        for m in rx.finditer(text):
            if _foreign(text, m.start(1)) or (form == "far" and m.group(1) in plain):
                continue                                  # `far` only adds what the explicit window missed
            found.append((m.group(1), form))
            found += [(r, "list") for r in _tail(text, m.end())]
    for form, rx in _REF_FIRST.items():
        for m in rx.finditer(text):
            if not _foreign(text, m.start(1)):
                found.append((m.group(1), form))
    out, seen = [], set()
    for ref, form in found:
        if (ref, form) not in seen:
            seen.add((ref, form))
            out.append((ref, form))
    return out


def loose_refs(text):
    """Pure. The references `text` mentions in a loose or advisory way (everything `find` reads except the
    explicit forms): what `deps.py report` shows as `weak`."""
    return {ref for ref, form in find(text, explicit=False)}


def _dismissed(backlog_check, bodies):
    """References a trusted person dismissed as `blocked-by` (the marker `backlog_check.py dismiss-text` prints)."""
    return {ref for b in bodies for kind, ref in backlog_check._DISMISS_RE.findall(b or "")
            if kind == "blocked-by"}


def _clean(body):
    """-> the comment text to scan, or None when it is Sigma's own fixed text."""
    body = body or ""
    if _SIGMA_MARKER.search(body):
        return None
    head = body.lstrip()
    for p in OFFBOARD_PREFIXES:
        if head.startswith(p):
            return head[len(p):COMMENT_CHARS + len(p)]
    if any(head.startswith(h) for h in SIGMA_HEADS):
        return None
    return head[:COMMENT_CHARS]


def _role(comment, assignees):
    """-> "assignee" | "owner" | "member" | "collaborator" | None for a comment nobody should trust."""
    login = str(((comment.get("user") or {}).get("login")) or "").lower()
    if login and login in assignees:
        return "assignee"
    assoc = comment.get("author_association")
    return assoc.lower() if assoc in TRUSTED else None


def _assignees(issue):
    names = [a.get("login") for a in (issue.get("assignees") or []) if isinstance(a, dict)]
    names.append((issue.get("assignee") or {}).get("login") if isinstance(issue.get("assignee"), dict) else None)
    return {str(n).lower() for n in names if n}


def _read(ro, repo, number, issue):
    """-> (issue with its body and assignees, newest-first comment dicts, maybe_older). A known-empty issue costs
    no request; otherwise the issue and the pages of its newest COMMENT_LIMIT comments (at most 3 requests)."""
    if issue is not None and isinstance(issue.get("comments"), int) and issue["comments"] == 0:
        return issue, [], False
    gh_api = _load("gh_api")
    full = gh_api.view_issue(ro, number, repo or None, comments=COMMENT_LIMIT)
    got = [c for c in (full.get("comments") or []) if isinstance(c, dict)]
    return full, list(reversed(got)), len(got) >= COMMENT_LIMIT


def collect(ro, repo, numbers, issues=None, have=None, state_of=None):
    """Read the bodies and newest comments of `numbers` (at most MAX_READS) and report what they wait on that no
    edge says. `issues` maps number -> the issue already read (skips a request for a known-empty one);
    `have` maps number -> blockers already known as edges (a ledger edge the body does not carry);
    `state_of(ref)` -> "open" | "closed" | "unknown". -> {"unconfirmed", "weak", "read"}."""
    issues, have = issues or {}, have or {}
    state_of = state_of or (lambda ref: "unknown")
    backlog_check = _load("backlog_check")
    members = {int(n) for n in numbers}
    items, not_read, older, ignored, dropped = {}, [], [], 0, 0
    todo = sorted(members)
    skipped = todo[MAX_READS:]
    for n in todo[:MAX_READS]:
        try:
            issue, comments, maybe_older = _read(ro, repo, n, issues.get(n))
        except Exception:                      # noqa: BLE001 - say so rather than guess
            not_read.append(n)
            continue
        if maybe_older:
            older.append(n)
        assignees = _assignees(issue)
        body = (issue.get("body") or "")[:BODY_CHARS]
        known = {int(i["ref"]) for i in blocker_scan.extract_refs(body, self_ref=n)}
        known |= {int(b) for b in have.get(n, ())}
        trusted, texts = [], []
        for c in comments:
            role = _role(c, assignees)
            if role is None:
                ignored += 1
                continue
            trusted.append(str(c.get("body") or ""))
            text = _clean(c.get("body"))
            if text is not None:
                texts.append((role, text))
        gone = {int(r) for r in _dismissed(backlog_check, trusted)}
        sources = [("body", None, body)] + [("comment", role, t) for role, t in texts]
        for where, role, text in sources:
            for ref, form in find(text, explicit=(where == "comment")):
                r = int(ref)
                if r == n or r in known or r in gone:
                    continue
                kind = "unconfirmed" if where == "comment" and form != "follow-up" else "weak"
                item = {"goal": n, "ref": r, "kind": kind, "form": form, "where": where, "by": role,
                        "state": state_of(r), "in_chain": r in members}
                cur = items.get((n, r))
                if cur is None or (cur["kind"] == "weak" and kind == "unconfirmed"):
                    if cur is None and len(items) >= MAX_ITEMS:
                        dropped += 1
                        continue
                    items[(n, r)] = item
    rows = [items[k] for k in sorted(items)]
    return {"unconfirmed": [i for i in rows if i["kind"] == "unconfirmed"],
            "weak": [i for i in rows if i["kind"] == "weak"],
            "read": {"members_read": len(todo[:MAX_READS]) - len(not_read), "not_read": sorted(not_read + skipped),
                     "maybe_older_comments": older, "untrusted_ignored": ignored, "dropped": dropped}}


def attach(out, ro, repo, numbers, issues=None, have=None, state_of=None):
    """Put the findings on the plan dict `out` and make the plan INCOMPLETE when anything was not read or cut."""
    res = collect(ro, repo, numbers, issues, have, state_of)
    out["unconfirmed_waits"], out["weak_waits"], out["waits_read"] = res["unconfirmed"], res["weak"], res["read"]
    r = res["read"]
    out["complete"] = bool(out.get("complete")) and not r["not_read"] and not r["dropped"]


# --- text for the plan and the sheet ---------------------------------------------------------------------

def _ids(nums):
    return ", ".join("#%d" % n for n in nums)


def _how(i):
    where = "in a comment by the %s" % i["by"] if i["where"] == "comment" else "in the body"
    return "%s (%s; %s; %s)" % ("#%d" % i["ref"], i["form"], where,
                                "%s, %s" % (i["state"], "in the chain" if i["in_chain"] else "not in the chain"))


def plan_lines(p):
    """The plan printout's lines for the three fields; empty when a record predates them."""
    un, weak, rd = p.get("unconfirmed_waits"), p.get("weak_waits"), p.get("waits_read")
    if un is None or rd is None:
        return []
    out = ["unconfirmed waits (found only in comments; none is an edge): %d" % len(un)]
    out += ["  #%d may wait on %s" % (i["goal"], _how(i)) for i in un]
    out.append("weak mentions in loose wording (never block): %d" % len(weak or []))
    out += ["  #%d ~ %s" % (i["goal"], _how(i)) for i in weak or []]
    out += _read_lines(rd)
    return out


def _read_lines(rd):
    out = []
    if rd.get("not_read"):
        out.append("comments NOT read for %s; the plan is incomplete" % _ids(rd["not_read"]))
    if rd.get("dropped"):
        out.append("%d finding(s) over the cap of %d were dropped; the plan is incomplete" % (rd["dropped"], MAX_ITEMS))
    if rd.get("maybe_older_comments"):
        out.append("only the newest %d comments were read for %s" % (COMMENT_LIMIT, _ids(rd["maybe_older_comments"])))
    if rd.get("untrusted_ignored"):
        out.append("%d comment(s) from people who are neither assignee nor owner/member/collaborator were ignored"
                   % rd["untrusted_ignored"])
    return out


def sheet_block(rec):
    """The ONE block of the sheet: every unconfirmed wait together, then the weak mentions, then how to settle
    them, led by a blank line. Empty when the record has none of it. Pure: reads only the record."""
    un, weak, rd = rec.get("unconfirmed_waits") or [], rec.get("weak_waits") or [], rec.get("waits_read") or {}
    notes = _read_lines(rd)
    if not un and not weak and not notes:
        return []
    out = ["Unconfirmed waits (%d): written only in comments or loose wording; none blocks anything yet:" % len(un)]
    out += ["  #%d may wait on %s" % (i["goal"], _how(i)) for i in un] or ["  none"]
    if un:
        out += ["  Confirm each real one: python3 skills/sigma-loop/scripts/deps.py mark <sdlc_dir> <goal> <ref> <reason...>",
                "  Dismiss a wrong one: python3 skills/sigma-loop/scripts/backlog_check.py dismiss-text blocked-by <ref>,",
                "  then post that text with python3 skills/sigma-loop/scripts/loop.py note <sdlc_dir> <goal> \"<text>\"",
                "  Then run chain.py plan again; a confirmed wait is an edge from then on."]
    if weak:
        out.append("Weak mentions (never block, not asked): " + "; ".join("#%d ~ %s" % (i["goal"], _how(i)) for i in weak))
    out += ["  " + n for n in notes]
    return [""] + out

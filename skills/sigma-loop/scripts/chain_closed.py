#!/usr/bin/env python3
"""chain_closed.py -- a closed prerequisite that did not meet its goal (#1906)

Chain treats a closed prerequisite as resolved. Sometimes it was closed without meeting its goal and the real gate
moved to a new issue nobody linked: an open follow-up of it, or an owner question left unanswered in its last
comments. This module finds those and returns an ADVISORY warning per closed prerequisite. It adds no edge and
writes nothing; the chain sheet turns each into a question (keep waiting, or proceed).

Two signals, both read with GETs only:
  follow-up   an open issue carrying the follow-up label (`sdlc:followup`) whose body has `Follow-up to|of|on|from #P`
              (the same tight forms `blocker_scan.FOLLOW_UP_TRIGGERS` reads as an edge). One bounded list read
              (at most `MAX_FOLLOWUP_ISSUES` issues) serves every closed prerequisite of the plan. A follow-up
              that was never labelled is not seen: the label is stamped on every issue Sigma files.
  question    the NEWEST comment of the closed prerequisite carries an owner-question marker line and nothing was
              posted after it. The markers are `Owner question:`, `Question for the owner:` and `Needs the owner's
              answer|decision|call:` at the start of a line (a bullet or bold is allowed). One read of its last
              `QUESTION_COMMENTS` comments (at most two pages).

BOUNDED. At most `limit` closed prerequisites are inspected per plan (lowest number first), at most three reads
each plus the one list; the rest are returned as `skipped` and the plan prints how many. A limit of 0 turns the
check off. A read that fails
costs only that signal: the prerequisite is listed under `unread` and the plan goes on (an advisory check never
refuses a plan).

Numbers only in the result: issue numbers and booleans, never a title, a body or a comment.
"""
import importlib.util
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_LIMIT = 10
MAX_FOLLOWUP_ISSUES = 200
MAX_FOLLOWUPS_SHOWN = 5
QUESTION_COMMENTS = 5
FOLLOWUP_LABEL = "sdlc:followup"
_QUESTION_RE = re.compile(
    r"(?im)^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?(?:owner question|question for (?:the )?owner"
    r"|needs (?:the )?owner(?:'s)? (?:answer|decision|call))(?:\*\*)?[ \t]*:")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def has_open_question(comments):
    """True when the newest of `comments` (dicts with `body` and `created_at`) carries an owner-question marker."""
    rows = [c for c in comments or [] if isinstance(c, dict)]
    if not rows:
        return False
    newest = max(rows, key=lambda c: str(c.get("created_at") or ""))
    return bool(_QUESTION_RE.search(str(newest.get("body") or "")))


def followups_of(issues, wanted):
    """-> {prerequisite number: sorted follow-up numbers} for the open `issues` whose body names a wanted number
    with a follow-up form."""
    blocker_scan = _load("blocker_scan")
    out = {}
    for it in issues:
        n = it.get("number")
        if not isinstance(n, int) or str(it.get("state") or "open").lower() != "open":
            continue
        for ref in blocker_scan.extract_refs(it.get("body") or "", self_ref=n):
            if ref["phrase"].startswith("follow") and int(ref["ref"]) in wanted:
                out.setdefault(int(ref["ref"]), set()).add(n)
    return {p: sorted(fs) for p, fs in out.items()}


def scan(ro, repo, closed_by, limit=DEFAULT_LIMIT, followup_label=FOLLOWUP_LABEL):
    """`closed_by` maps each closed prerequisite to the sorted numbers that wait on it. `ro` is a GET-only runner.
    -> {"warnings": [{"prerequisite", "dependents", "followups", "question"}], "inspected": int,
        "skipped": [prerequisite numbers not inspected], "unread": [prerequisites a read failed for]}.
    Never raises."""
    out = {"warnings": [], "inspected": 0, "skipped": [], "unread": []}
    if limit <= 0:              # `chain.max_closed_checks: 0` turns the check off: no read, nothing to report
        return out
    order = sorted(closed_by)
    take, out["skipped"] = order[:limit], order[limit:]
    out["inspected"] = len(take)
    if not take:
        return out
    gh_api, sources = _load("gh_api"), _load("sources")
    kids, list_ok = {}, True
    try:
        listed = sources.fetch_issues_rest(ro, repo, [followup_label], MAX_FOLLOWUP_ISSUES)
        kids = followups_of(listed, set(take))
    except Exception:           # noqa: BLE001 - advisory: this half is lost, the plan is not
        list_ok = False
    for p in take:
        question, ok = False, True
        try:
            issue = gh_api.view_issue(ro, p, repo or None, comments=QUESTION_COMMENTS)
            question = has_open_question(issue.get("comments"))
        except Exception:       # noqa: BLE001
            ok = False
        if not (ok and list_ok):
            out["unread"].append(p)
        if kids.get(p) or question:
            out["warnings"].append({"prerequisite": p, "dependents": sorted(closed_by[p]),
                                    "followups": kids.get(p, [])[:MAX_FOLLOWUPS_SHOWN], "question": question})
    return out

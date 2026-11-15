#!/usr/bin/env python3
"""#2223: release the goals a closed goal was holding.

When goal N closes, the open goals that name it and carry `sdlc:blocked` stay blocked until a full
`/sigma-loop` pick sweeps them, and a `--land` fleet never runs that sweep. This verb is the scoped
answer: for each closed goal it finds the open `sdlc:blocked` goals naming it, re-runs the sweep's OWN
decision code (`auto_unpark.compute_unpark_actions`, nothing is re-parsed here) and drops `sdlc:blocked`
from those whose every dependency is closed.

    python3 release_dependents.py <sdlc_dir> <goal> [<goal>...] [--all] [--apply] [--cap N]

A dry run unless `--apply` (the sweep CLI's posture). `--all` names no goal: every open blocked goal is
checked (the watcher duty and the doctor read). What it deliberately does LESS than the sweep:

  * it only removes `sdlc:blocked`: never adds a label, never touches `sdlc:in-progress`, never resumes
    an `sdlc:parked` goal (a park is a person's; `/sigma-unpark` and the full sweep stay its routes);
  * the stored dependency map may NOMINATE candidates, and its stale "still open" entry for N itself is
    ignored (`map_closed`): right after N closes the map still stores N as open until its next refresh. It
    still HOLDS a goal on any OTHER open blocker it records (the sweep's own rule, `_map_still_waiting`), so a
    map-only edge the goal's text does not name is never released past;
  * every read leg fails closed. A failed list, issue read, comment read, ref-state read or closed-goal
    read releases nothing for the goal it touched (comments go through the STRICT reader: the sweep's own
    reader degrades a failed read to "no comments", which would hide a `sigma:keep-parked` marker).

Candidates: a fresh map (`dag_pick._entry`, the picker's own freshness test) gives the reverse edges with no
list call; otherwise ONE bounded listing (`CEILING` rows plus a probe row, newest first, so past the
ceiling the OLDEST blocked goals are the ones not read: said in the output, never hidden).

Mode: the unattended callers (the `record done` tail, the watcher tick) are gated by
`discovery.auto_unpark.mode` and the REST reserve; the CLI is the operator's own opt-in and ungated.
Refuses when the human `blocked_label` equals the machine `goal_blocked_label`. REST only, through the
source's own helpers (no direct `gh` text here). Unmeasured on a real repository: see docs/cloud-sessions.md.
"""
import importlib.util
import json
import pathlib
import re
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


auto_unpark = _load("auto_unpark")
dag_pick = _load("dag_pick")
gh_calls = _load("gh_calls")
sources = auto_unpark.sources
triage = auto_unpark.triage
blocker_scan = auto_unpark.blocker_scan
backlog_check = auto_unpark.backlog_check
mirror = auto_unpark.mirror
gh_quota = sources.gh_quota

#: One REST page of blocked goals (`sources._REST_PAGE_SIZE`); the probe row makes it 101 so "1+ not checked"
#: is exact. Raised only by an operator's `--cap`.
CEILING = 100
STATE_NAME = "release-dependents.last.json"
_FIELDS = ["number", "title", "body", "labels", "state", "updatedAt"]

#: Fixed wording, NONE of `blocker_scan`'s trigger phrases (a comment is re-scanned for blocker refs, so a
#: note that said "blocked by #N" would manufacture the very edge it records). A test runs the scanner over it.
_NOTE = ("Sigma released this goal: #{closed} is closed and every dependency named here is closed. "
         "Only the sdlc:blocked label was removed.")
#: The `--all` pass names no closed goal, so it cannot say which one. Same rule: no `blocker_scan` phrase, no `#N`.
_NOTE_ALL = ("Sigma released this goal: every dependency named here is closed. "
             "Only the sdlc:blocked label was removed.")


# ------------------------------------------------------------------------------------ the one filter

def release_candidates(source, issues):
    """The ONE rule for what this verb may touch: carries the blocked label and NOT the parked label. The
    doctor row calls this same function, so it can never promise a release the verb would not make."""
    out = []
    for it in issues or []:
        names = auto_unpark._label_names(it)
        if source.goal_blocked_label in names and source.parked_label not in names:
            out.append(it)
    return out


# ------------------------------------------------------------------------------------ strict comments

class _StrictComments(auto_unpark._CommentReads):
    """`auto_unpark._CommentReads` over `fetch_comments_strict`. The sweep's reader fails open to `[]`
    (`backlog_check._fetch_scrubbed_comments`), so a comment outage would hide a keep-parked marker, a
    declared gate or a comment-only blocker. Here a failed read records the goal in `failed` and returns
    `[]`; the caller drops every action for a failed goal. Re-applies what the sweep's reader applies: only
    OWNER/MEMBER/COLLABORATOR prose counts (#709), and each kept body is scrubbed. Never persists (the
    sweep owns the cache file)."""

    def __init__(self, sdlc_dir, source):
        super().__init__(sdlc_dir, persist=False)
        self.source, self.failed = source, set()

    def get(self, config, it, goal_doc, run=None):
        n = str(it.get("number"))
        if n in self.memo:
            return self.memo[n]
        try:
            data = self.source.fetch_comments_strict(n)
            kept = []
            for c in data["comments"]:
                if c.get("authorAssociation") not in sources.TRUSTED_ASSOCIATIONS:
                    print("sigma: ignoring a comment on %s from %s (association %s): only OWNER/MEMBER/"
                          "COLLABORATOR comments feed blocker detection (#709)" % (
                              n, (c.get("author") or {}).get("login") or "an unknown author",
                              c.get("authorAssociation") or "none"), file=sys.stderr)
                    continue
                kept.append(backlog_check.scrub(c.get("body") or ""))
        except Exception as exc:                    # noqa: BLE001 - one bad goal may not cost the others
            self.failed.add(n)
            print("release-dependents: could not read the comments of #%s (%s): left blocked" % (n, exc),
                  file=sys.stderr)
            kept = []
        self.memo[n] = kept
        return kept


# ------------------------------------------------------------------------------------ the core

def _new_result(apply, closed):
    return {"status": "ok", "reason": "", "apply": bool(apply), "closed": list(closed), "checked": 0,
            "released": [], "would_release": [], "held": 0, "incomplete": [], "not_open": [],
            "not_checked": "", "ceiling_hit": False, "unread": 0, "cap": CEILING, "failed": [],
            "strip_failed": []}


def _blocked_only(source, actions):
    """The sweep's actions, rebuilt to remove ONLY `goal_blocked_label` (its `remove` also lists
    `in_progress_label`, and its `add` re-adds `goal_label`: a blocked goal keeps its membership). `detail`
    is regenerated, the sweep's was built from the unfiltered sets. A swap that would not remove the
    blocked label is dropped, and the card move stays only for a swap that stayed."""
    keep, swapped = [], set()
    for a in actions:
        if a["action"] == "swap-label":
            if source.goal_blocked_label not in (a.get("remove") or []):
                continue
            remove = [source.goal_blocked_label]
            keep.append({**a, "add": [], "remove": remove, "detail": triage._swap_detail([], remove)})
            swapped.add(a["issue"])
        elif a["action"] == "set-status" and a["issue"] in swapped:
            keep.append(a)
    return keep


def _confirm_closed(source, goals, res, open_cache):
    """N must itself read closed, by THIS verb's strict read: `compute_unpark_actions` reads N only when a
    candidate's text names it, and a map-nominated candidate may not. -> {number: label names}."""
    confirmed = {}
    for g in goals:
        n = str(g).lstrip("#")
        try:
            d = source._read_issue(n, ["state", "stateReason", "labels"])
            closed = blocker_scan.closed_state((d.get("state") or "").upper(), d.get("stateReason") or "")
        except Exception as exc:                    # noqa: BLE001 - unreadable is not closed
            print("release-dependents: could not read #%s (%s): releasing nothing for it" % (n, exc),
                  file=sys.stderr)
            res["incomplete"].append(n)
            continue
        if not closed:
            res["not_open"].append(n)
            continue
        confirmed[n] = auto_unpark._label_names({"labels": d.get("labels")})
        open_cache[n] = False                       # read once, here
    return confirmed


def _gather(sdlc_dir, config, source, confirmed, all_blocked, cap, now, res):
    """-> (rows, numbers_to_read, via_map). `rows` are full issue dicts (the listing path); `numbers_to_read`
    are map-nominated numbers read one at a time inside the loop (so a deadline and a failed read are
    per goal). None when the listing failed."""
    if not all_blocked:
        m, graph, _why = dag_pick._entry(str(sdlc_dir), config, {}, now)
        if m is not None and graph is not None:
            nums = []
            for g in confirmed:
                for d in graph.dependents.get(int(g), ()):
                    node = graph.nodes.get(d)
                    if node is not None and node.life == "blocked" and str(d) not in nums:
                        nums.append(str(d))
            return [], nums, True
    try:
        rows = source._list_issues(_FIELDS, labels=[source.goal_blocked_label], state="open", cap=cap + 1)
    except Exception as exc:                        # noqa: BLE001 - nothing is known, so nothing is released
        print("release-dependents: could not list the blocked goals (%s): releasing nothing" % exc,
              file=sys.stderr)
        return None, [], False
    rows = [r for r in rows or [] if isinstance(r, dict) and "number" in r]
    if len(rows) > cap:
        res["ceiling_hit"] = True
        rows = rows[:cap]
    res["cap"] = cap
    rows = release_candidates(source, rows)
    if not all_blocked:                             # comment-only edges are left to --all (documented)
        pats = [re.compile(r"(?<![\w#])#%s(?!\d)" % re.escape(n)) for n in confirmed]
        rows = [r for r in rows if any(p.search((r.get("title") or "") + "\n" + (r.get("body") or ""))
                                       for p in pats)]
    return rows, [], False


def release(sdlc_dir, config, closed_goals=(), apply=False, run=None, source=None, now=None,
            unattended=False, deadline=None, all_blocked=False, cap=CEILING, clock=None, progress=None):
    """Release the open `sdlc:blocked` goals whose every dependency is closed. -> the result dict (see
    `_new_result`; `render` is its one-line form). `unattended` applies the mode and REST-reserve gates.
    `deadline` is an absolute `clock()` value checked BETWEEN goals; the rest are reported `not_checked`.
    `progress(res)` runs after every goal, so a caller can write kill-safe state."""
    clock = clock or time.time
    now = time.time() if now is None else now
    closed = [str(g).lstrip("#") for g in closed_goals]
    res = _new_result(apply, closed)
    res["cap"] = cap
    if not mirror.is_github_mode(config):
        return {**res, "status": "skipped", "reason": "not github mode"}
    if unattended:
        if sources._auto_unpark(config) == "off":
            return {**res, "status": "skipped", "reason": "discovery.auto_unpark.mode is off"}
        why = gh_quota.rest_deferral(config)
        if why:
            return {**res, "status": "deferred", "reason": why}
    if source is None:
        source = sources.GitHubSource(config, run=run, sdlc_dir=sdlc_dir)
    if source.blocked_label == source.goal_blocked_label:
        return {**res, "status": "refused", "reason": "the human blocked_label equals the machine "
                "goal_blocked_label (%s): fix the labels first (/sigma-doctor)" % source.goal_blocked_label}
    open_cache = {}
    confirmed = {} if all_blocked else _confirm_closed(source, closed, res, open_cache)
    if not all_blocked and not confirmed:
        res["status"] = "incomplete" if res["incomplete"] else "ok"
        return res
    rows, numbers, via_map = _gather(sdlc_dir, config, source, confirmed, all_blocked, cap, now, res)
    if rows is None:
        return {**res, "status": "incomplete", "reason": "the blocked-goal listing failed"}
    reads = _StrictComments(str(sdlc_dir), source)
    todo = [("row", r) for r in rows] + [("num", n) for n in numbers]
    for i, (kind, item) in enumerate(todo):
        if deadline is not None and clock() >= deadline:
            res["unread"] = len(todo) - i
            break
        res["checked"] += 1
        n = str(item["number"]) if kind == "row" else item
        try:
            it = item if kind == "row" else {**source._read_issue(n, _FIELDS), "number": int(n)}
            if str(it.get("state") or "OPEN").upper() != "OPEN":
                res["held"] += 1                    # closed since the map was built: not ours to relabel
                continue
            if not release_candidates(source, [it]):
                res["held"] += 1
                continue
            actions, _resolved = auto_unpark.compute_unpark_actions(
                str(sdlc_dir), config, source, [it], run=run, open_cache=open_cache, reads=reads,
                map_closed=frozenset(int(g) for g in confirmed))
        except Exception as exc:                    # noqa: BLE001 - this goal only
            print("release-dependents: #%s could not be read (%s): left blocked" % (n, exc), file=sys.stderr)
            res["incomplete"].append(n)
            continue
        if n in reads.failed:
            res["incomplete"].append(n)             # its actions are dropped, whatever compute returned
            continue
        kept = _blocked_only(source, actions)
        if not kept:
            res["held"] += 1
            continue
        done = triage.apply_actions(source, kept, apply)
        swap = next(a for a in done if a["action"] == "swap-label")
        if swap["result"] == "would":
            res["would_release"].append(n)
        elif swap["result"] == "done":
            res["released"].append(n)
            _audit(source, n, confirmed, res)
        else:
            res["failed"].append(n)
        if progress:
            progress(res)
    if apply:
        for g, names in confirmed.items():          # N is a closed goal, so it no longer blocks anything
            if source.blocking_label in names:
                try:
                    source._issue_remove_label(g, source.blocking_label)
                except Exception as exc:            # noqa: BLE001 - reported, never retried here
                    print("release-dependents: could not strip %s from #%s (%s)" % (
                        source.blocking_label, g, exc), file=sys.stderr)
                    res["strip_failed"].append(g)
    parts = []
    if res["ceiling_hit"]:
        parts.append("1+ not checked")
    if res["unread"]:
        parts.append("%d not checked (time budget)" % res["unread"])
    res["not_checked"] = "; ".join(parts)
    if res["incomplete"] or res["failed"]:
        res["status"] = "incomplete"
    return res


def _audit(source, n, confirmed, res):
    """One note on the released goal. A failed note is reported, never retried, and does not undo the
    release. A hook and the watcher can race and post it twice: cosmetic."""
    try:
        source.note(n, _NOTE.format(closed=", #".join(confirmed)) if confirmed else _NOTE_ALL)
    except Exception as exc:                        # noqa: BLE001
        print("release-dependents: could not post the note on #%s (%s)" % (n, exc), file=sys.stderr)


def render(res):
    """The one line a caller relays."""
    tag = "release-dependents " + (", ".join("#" + g for g in res["closed"]) or "--all")
    if res["status"] in ("refused", "skipped", "deferred"):
        return "%s: %s (%s)" % (tag, res["status"], res["reason"])
    verb = "released" if res["apply"] else "would release"
    names = res["released"] if res["apply"] else res["would_release"]
    bits = ["%s %d%s" % (verb, len(names), " (%s)" % ", ".join("#" + n for n in names) if names else ""),
            "left blocked %d" % res["held"]]
    if res["not_open"]:
        bits.append("%s not closed: nothing released for it" % ", ".join("#" + n for n in res["not_open"]))
    if res["incomplete"]:
        bits.append("incomplete %s (a read failed, left blocked)" % ", ".join("#" + n for n in res["incomplete"]))
    if res["failed"]:
        bits.append("write failed %s" % ", ".join("#" + n for n in res["failed"]))
    line = "%s: %s" % (tag, ", ".join(bits))
    if res["ceiling_hit"]:
        line += ("; %d blocked goals read, 1+ not checked (ceiling %d; newest-first listing, so the OLDEST "
                 "blocked goals are the ones dropped; run --all, --cap N or /sigma-doctor)" % (res["cap"], res["cap"]))
    if res["unread"]:
        line += "; %d not checked (time budget spent; the watcher continues)" % res["unread"]
    return line


# ------------------------------------------------------------------------------------ the state file

def state_path(sdlc_dir):
    return auto_unpark._state_path(sdlc_dir, STATE_NAME)


def write_state(sdlc_dir, record):
    """Atomic; a failure costs one stale stamp, never the caller. Written on failure too, so a dead pipeline
    shows as AGE in the doctor, not as zero errors."""
    try:
        auto_unpark._write_json_atomic(state_path(sdlc_dir), record)
    except Exception:                               # noqa: BLE001
        pass


def read_state(sdlc_dir):
    try:
        data = json.loads(state_path(sdlc_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:                               # noqa: BLE001
        return {}


# ------------------------------------------------------------------------------------ the CLI

USAGE = "usage: release_dependents.py <sdlc_dir> <goal> [<goal>...] [--all] [--apply] [--cap N]"


def main(argv, run=None, source=None):
    args = list(argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2
    sdlc_dir, rest = args[0], args[1:]
    apply, all_blocked, cap, goals = False, False, CEILING, []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--apply":
            apply = True
        elif a == "--all":
            all_blocked = True
        elif a == "--cap" and i + 1 < len(rest) and rest[i + 1].isdigit() and int(rest[i + 1]) > 0:
            cap = int(rest[i + 1])
            i += 1
        elif a.lstrip("#").isdigit():
            goals.append(a.lstrip("#"))
        else:
            print(USAGE, file=sys.stderr)
            return 2
        i += 1
    if not goals and not all_blocked:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        config = json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except Exception:                               # noqa: BLE001
        config = {}
    with gh_calls.component("release_dependents"):
        res = release(sdlc_dir, config, goals, apply=apply, run=run, source=source, all_blocked=all_blocked,
                      cap=cap)
    print(render(res))
    if res["status"] == "refused":
        return 2
    return 1 if res["status"] == "incomplete" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

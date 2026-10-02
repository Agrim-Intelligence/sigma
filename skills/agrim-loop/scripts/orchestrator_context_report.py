#!/usr/bin/env python3
"""Cross-goal, cross-session orchestrator context/cost report (#2531 — that goal's research dossier is
the full operational spec; this docstring is the short version). Answers, from real transcripts
already on disk: how large did the ORCHESTRATOR's own context get, per goal, across a multi-goal
/agrim-loop run — the measurement #2521's own retro flagged as never produced.

NOT a phase_report.py subcommand: that module is scoped to one phase's cost at a phase boundary;
this is a cross-goal, cross-session report, read anytime, about a different subject (the
orchestrator's own accumulated context, not one phase's spend). Stdlib-only; the core never
imports the private side (tests/test_import_boundary.py; phase_report.py's module docstring is the
precedent this follows).

TRANSCRIPT SHAPE REUSED, UNCHANGED, FROM phase_report.py: ~/.claude/projects/<slug>/<session>.jsonl
(orchestrator's own top-level transcript) and ~/.claude/projects/<slug>/<session>/subagents/
agent-<id>.jsonl (one dispatched subagent). NEW, not in phase_report.py: every dispatched
subagent's sibling agent-<id>.meta.json, carrying spawnDepth/parentAgentId/toolUseId/description —
Claude-Code-Desktop-harness-specific, unverified on a bare `claude -p` subprocess (see
the #2531 research dossier's Constraints). Absence of this shape degrades to an honest `unavailable`,
never a guess.

DEDUP BY message.id (never the JSONL line's own `uuid` — that is per-CONTENT-BLOCK, not per-API-
call): group raw assistant-type lines by message.id; ts = MIN across the group; usage = the LAST
line's usage IN FILE ORDER. This is NOT "assume duplicates are identical" — it is the same
documented GRAIN rule a downstream transcript reader uses ("usage updates PROGRESSIVELY...
only the last is the complete, correct total"), which stays correct even where a future duplicate
group's usage genuinely differs across lines, not just on the byte-identical samples measured on
this host so far.

RAW-LINE READING IS AN ADDITIVE REUSE OF phase_report.iter_assistant_turns, NOT A FRESH PARSING
SHELL (deviates from this goal's own Plan §1a, which proposed a fresh ~25-line function because
that helper discarded message.id -- Plan-Review refinement 3 asked to check whether an additive
field could avoid the duplication instead, before defaulting to a fresh function). Checked before
changing it: `iter_assistant_turns` has exactly one production caller in this repo
(`price_transcript`, same file, reads only 'ts'/'model'/'usage' by key) and two tests in
tests/test_phase_report.py (both assert specific keys/values, never an exact key-set) — so adding
a 'message_id' key to its yielded dict is backward compatible, confirmed by running that file's
suite unchanged before and after. See phase_report.py's own `iter_assistant_turns` docstring for
the matching note on that side. `iter_raw_assistant_calls` below is therefore a thin pass-through.

PEAK CONTEXT (one scope): max over that scope's deduped calls of `input_tokens +
cache_read_input_tokens + cache_creation_input_tokens` — the full prompt size actually processed
that turn, never summed across calls (context is already cumulative per-turn; summing
double/triple-counts the same history). VOLUME (one scope): sum over deduped calls of
input+output+cache_read+cache_creation. Both definitions are the #2531 research dossier's own, verbatim
— see that file for the full reasoning, including the three wrong-but-plausible alternatives it
measured and rejected (summing context, trusting isSidechain, reusing price_transcript unmodified).

Never fabricates a number: a missing session, an empty transcript, or an unpriced model all print
an honest reason (`unavailable`, `(partial — N of M unpriced)`), matching phase_report.py's own
module-wide rule.
"""
import json
import os
import pathlib
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

_HERE = pathlib.Path(__file__).resolve().parent
#: This checkout's own root (four parents from __file__: scripts -> agrim-loop -> skills -> repo
#: root). phase_report.py carried the identical expression until #2573/S1-G9 gave the core its own
#: rate card beside the skill and deleted it there; this one is the last of the pair — reused for
#: `resolve_session_id`'s repo-scoped fallback (see that function; Plan-Review refinement 1).
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent


_MODULE_CACHE = {}    # name -> loaded module; see _load's own docstring (code review finding)


def _load(name):
    """Identical idiom to phase_report.py's own _load() -- see that module's docstring for why
    this is copied, not imported, across sibling scripts in this directory. MEMOIZED (module-level
    cache, keyed by name): this is called from many hot-path functions in this module
    (iter_raw_assistant_calls, peak_context, volume_totals, priced_totals, goal_slot_dispatch_ts,
    build_report) -- without caching, every single one of those calls re-executed
    phase_report.py's ENTIRE module body from scratch (spec_from_file_location +
    module_from_spec + exec_module), a real, avoidable, named cost at scale (SCALABILITY,
    AGENTS.md): code review measured ~185-745+ redundant full-module executions at N=20 goals.
    Cached here so a sibling module is loaded at most once per process, never once per call."""
    if name in _MODULE_CACHE:
        return _MODULE_CACHE[name]
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    _MODULE_CACHE[name] = m
    return m


def _home(home=None):
    return pathlib.Path(home) if home is not None else pathlib.Path.home()


def iter_raw_assistant_calls(path):
    """Yield {'ts': norm_ts(...), 'model': str, 'message_id': str|None, 'usage': dict} for each
    RAW type=='assistant' line -- one entry PER LINE, no grouping (dedup_calls does that below).
    A thin pass-through over phase_report.iter_assistant_turns, which now also yields
    'message_id' (see module docstring) -- kept as this module's own named entry point (not a
    bare re-export) so callers here read one stable name regardless of where the parsing actually
    lives, and so this module's own tests exercise it directly."""
    pr = _load("phase_report")
    yield from pr.iter_assistant_turns(path)


def dedup_calls(raw_calls):
    """Group by message_id (None never grouped -- each such line stays its own call, since a
    missing id cannot be safely merged with an unrelated line). ts = MIN across the group;
    usage/model = the LAST call's, IN INPUT ORDER (the same GRAIN rule a downstream transcript
    reader uses -- Plan §1b: usage updates progressively across a multi-block turn, so the last
    line is the complete, correct total, never assumed identical to the first). Returns a list
    shaped {'ts', 'model', 'usage'} -- exactly phase_report.price_turn's own expected `turn`
    argument, unmodified.

    message_id is sanitized via phase_report.as_str (#2531, third fix round) -- THIS is the single
    most universal function in the module (every scope routes through it), so it does not merely
    trust that iter_assistant_turns already yields a clean str|None (which it now does -- see that
    function's own docstring): a non-hashable message_id (a JSON list/dict, however it got here --
    iter_raw_assistant_calls's real pipeline, or a hand-built call dict passed directly, as several
    of this file's own tests already do) used to crash `key not in groups`/`groups[key] = []` with
    TypeError('unhashable type'). A wrong-typed-but-hashable id (an int, a float, a bool) is folded
    into the SAME 'own call, never merged' bucket as None, for the same reason None gets it: an id
    that isn't a real message.id string cannot be safely trusted to identify a group.

    No separate `order` list (simplified, fifth fix round, confirmed safe before removing it --
    this repo's own runtime is Python 3.9.6): a plain `dict` has guaranteed insertion-ordering
    since 3.7, and `groups` here only ever gains a new key the first time it is seen (via
    `setdefault`), so `groups.items()` already iterates in exactly the same first-seen order the
    old, separately-tracked `order` list existed only to reproduce -- a second structure carrying
    information the first one already had.

    `ts = MIN across the group` deliberately KEPT, not changed to the LAST member's ts to match
    usage/model (#2531, SEVENTH fix round -- see _filter_calls's own docstring for the bug this
    round fixes and why it is NOT here): a group's raw lines can span more than one goal-slot's own
    dispatch instant when a single orchestrator turn dispatches several goal-slots at once (several
    `Agent` tool_use blocks, one message.id) -- verified this is real, not hypothetical, against
    dd8a2b2a-adb2-47af-9710-ac54e9f9f443.jsonl lines 1727/1729/1731 (#2543/#2544/#2531's own real
    dispatch, one turn, 36 seconds start to finish). MIN coincides EXACTLY with the group's own
    EARLIEST goal-slot's dispatch instant, which is exactly as real and exactly as attributable a
    boundary as the LAST member's would be -- neither is "more correct" for a turn whose cost is
    genuinely joint across every goal-slot it dispatched (AGENTS.md RELIABILITY: "a shared counter's
    delta is not attribution"; this module never fabricates a per-goal split of it). Switching to
    LAST would only move which ONE goal-slot absorbs that shared cost, not fix anything, while
    unnecessarily touching this function's own separately-tested `ts` contract
    (test_dedup_calls_uses_min_ts_across_a_group). The real defect was never the choice of MIN vs
    LAST here -- it was _filter_calls comparing that ts (whichever end of the group it names)
    against a goal-slot's own boundary at mismatched granularity, which excluded the merged call
    from EVERY window, not just the "wrong" one. Fixed there; this function is unchanged."""
    groups = {}   # message_id (or a unique per-line sentinel for None/malformed) -> calls, in the
                   # order each key is first seen (relies on dict insertion-ordering -- see above)
    pr = _load("phase_report")
    for i, call in enumerate(raw_calls):
        mid = pr.as_str(call["message_id"])
        key = mid if mid is not None else ("__none__", i)
        groups.setdefault(key, []).append(call)
    out = []
    for members in groups.values():
        out.append({
            "ts": min(m["ts"] for m in members),
            "model": members[-1]["model"],
            "usage": members[-1]["usage"],
        })
    return out


def peak_context(calls):
    """max(input + cache_read + cache_creation) over calls; 0 for empty. NEVER summed across
    calls -- the #2531 research dossier's own 'Peak context' definition, verbatim. `calls` must
    already be deduped (see dedup_calls) -- taking this max over raw, undeduped lines can be
    corrupted by an earlier duplicate content-block line reporting a higher instantaneous value
    than the final, complete one (see the mutation control,
    test_removing_dedup_inflates_peak_and_volume_to_the_wrong_over_counted_values)."""
    pr = _load("phase_report")
    peaks = []
    for c in calls:
        u = c["usage"]
        peaks.append((pr.usage_value(u, ("input_tokens",)) or 0)
                      + (pr.usage_value(u, ("cache_read_input_tokens",)) or 0)
                      + (pr.usage_value(u, ("cache_creation_input_tokens",)) or 0))
    return max(peaks) if peaks else 0


def volume_totals(calls):
    """sum(input+output+cache_read+cache_creation) over calls -- the raw-shape 'total volume'
    figure, separate from priced_totals's dollar figure. `calls` must already be deduped (see
    dedup_calls) -- summing raw, undeduped lines double/triple-counts one logical call's usage
    once per content-block line it happened to stream across."""
    pr = _load("phase_report")
    total = 0
    for c in calls:
        u = c["usage"]
        total += ((pr.usage_value(u, ("input_tokens",)) or 0)
                  + (pr.usage_value(u, ("output_tokens",)) or 0)
                  + (pr.usage_value(u, ("cache_read_input_tokens",)) or 0)
                  + (pr.usage_value(u, ("cache_creation_input_tokens",)) or 0))
    return total


def priced_totals(calls, rates):
    """Run every call through phase_report.price_turn UNMODIFIED; sum non-None results; count
    unpriced calls. Mirrors price_transcript's own cost_usd/unpriced_turns shape so a reader
    already familiar with phase_report.py's own output reads this the same way. Returns
    (cost_usd_or_None, unpriced_count)."""
    pr = _load("phase_report")
    total = 0.0
    priced = 0
    unpriced = 0
    for c in calls:
        cost = pr.price_turn(c, rates)
        if cost is None:
            unpriced += 1
        else:
            total += cost
            priced += 1
    return (round(total, 6) if priced > 0 else None), unpriced


# --------------------------------------------------------------------------- subagent tree

#: TWO SEPARATE regexes, tried in explicit priority order by goal_number below -- NOT one combined
#: `A|B|C` alternation (that was the fourth-round bug: re.search on a combined alternation always
#: returns the LEFTMOST match, trying alternatives in listed order only as a TIEBREAK AT THE SAME
#: STARTING POSITION -- it cannot express "prefer a later, more specific alternative over an
#: earlier, less specific one". A prior version of this file folded all three shapes into one
#: `_GOAL_NUM_RE` with the goal-slot-specific branch listed second; a confounding bare "#N" sitting
#: at position 0 (e.g. a quoted parent placed BEFORE the goal-slot's own marker, "#100 #200
#: goal-slot -- full SDLC to merge") matched the start-anchored branch immediately at position 0
#: and won, even though a later, far more specific "#<N> goal-slot" match existed in the same
#: string (code review, fourth fix round -- classify_tree({"description": "#100 #200 goal-slot --
#: full SDLC to merge"}) used to attach goal 100, not 200). The pre-existing quoted-parent test
#: below happened to still pass, because THAT confounder ("Story #100: #2531 goal-slot") is not
#: itself anchored to string start, so the start-anchored branch failed at position 0 and the scan
#: reached the real marker -- masking this bug for any shape where the confounder is not itself
#: the first token.
#:
#: Tried FIRST, anywhere in the string: the goal-slot's own specific "#<N> goal-slot" marker --
#: this always wins over either fallback below when present, regardless of position, because it is
#: the most specific of the three shapes (see classify_tree's own docstring: "the goal-slot's own
#: description has a goal number").
_GOAL_SLOT_NUM_RE = re.compile(r"#(\d+)(?=\s+goal-slot)")
#: Tried SECOND, only when no goal-slot marker exists anywhere in the string (see goal_number).
#: Branch 1 matches a number anchored to the very START of the string (docs/output-contract.md
#: section 4's own dispatch-label convention, "#<N> P<n> NAME -- description", e.g. "#2531 P5
#: IMPLEMENT -- orchestrator context report" -- this goal's own real phase description; also covers
#: the goal-slot shape when it already happens to sit at string start, "#<N> goal-slot -- ...",
#: which _GOAL_SLOT_NUM_RE above already catches first in that case). Branch 2 matches a number
#: immediately preceded by "goal " (the older phase shape, "...goal #2531...").
_GOAL_NUM_RE = re.compile(r"^#(\d+)|goal\s+#(\d+)")

#: The goal-slot dispatch prompt's own literal wording (skills/agrim-loop's own dispatch code),
#: matched here as a SOFT, CONVENTION-BASED substring check -- there is NO enforced contract
#: pinning this exact wording (no test anywhere ties it to the dispatch prompt template, and
#: nothing prevents that prompt's own wording from drifting independently of this module).
#: Top Risk 5 (plan #2531, Plan-Review refinement 5): a future change to the dispatch
#: description's phrasing would silently UNDER-COUNT goals here (classify_tree simply drops a
#: spawnDepth==1 entry that doesn't match, treating it as "not a goal-slot" -- see classify_tree
#: below), never error or warn. Deliberately left this way for this goal (no drift guard shipped),
#: but documented explicitly so a future reader does not mistake this for a pinned contract.
_GOAL_SLOT_RE = re.compile(r"#\d+\s+goal-slot")


def walk_subagent_tree(session_dir):
    """{agent_id: {**meta.json contents, 'agent_id': id}} for every subagents/agent-<id>.meta.json
    under session_dir. Missing/malformed subagents dir -> {} (never raises; caller treats empty as
    'no subagent data', not an error)."""
    subagents_dir = pathlib.Path(session_dir) / "subagents"
    tree = {}
    if not subagents_dir.is_dir():
        return tree
    for meta_path in subagents_dir.glob("agent-*.meta.json"):
        agent_id = meta_path.name[len("agent-"):-len(".meta.json")]
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            tree[agent_id] = {**meta, "agent_id": agent_id}
    return tree


def goal_number(description):
    """The #<digits> anchored to one of three real observed shapes in a .meta.json 'description'
    string, or None: '#2531 goal-slot -- ...' (the goal-slot's own marker -- tried FIRST, ANYWHERE
    in the string, via _GOAL_SLOT_NUM_RE, and always wins over either fallback below when present,
    regardless of position -- see that regex's own comment for why this must be a separate,
    explicitly higher-priority pass rather than one branch inside a combined alternation, and for
    the fourth-round bug this fixes: a confounding bare "#N" at string start used to beat a later,
    more specific "#<N> goal-slot" marker); '#2531 P5 IMPLEMENT -- ...' (string-start, tried only
    when no goal-slot marker exists anywhere); or 'Research goal #2531 ...' (preceded by "goal ",
    same fallback tier). A goal-slot description that also quotes a different #N -- a parent
    Epic/Story reference, anywhere before OR after its own "#N goal-slot" marker -- still resolves
    to the goal-slot's own number, never whichever #N-shaped substring happens to appear first
    (code review finding, first fix round, generalized to "regardless of position" fourth round).

    GUARDS `isinstance(description, str)`, not just truthiness (found while exhaustively
    re-checking this module for Bug 3's own class, second fix round -- a meta.json 'description'
    field is externally-parsed JSON like any other field here, and a non-string-but-truthy value
    (a number, a list, ...) used to reach `_GOAL_SLOT_NUM_RE.search(description)` and crash with
    TypeError('expected string or bytes-like object'). Confirmed live via classify_tree's own
    phase-branch, this function's direct caller for that branch, which passes
    meta.get('description') straight through with no coercion at all).

    Now routed through phase_report.as_str (#2531, third fix round) instead of its own inline
    isinstance-plus-truthiness check -- same guarantee, but ONE shared implementation instead of
    one more hand-rolled copy for a future reader to keep in sync with every other copy of the
    same check scattered across this module and phase_report.py."""
    pr = _load("phase_report")
    description = pr.as_str(description)
    if description is None:
        return None
    m = _GOAL_SLOT_NUM_RE.search(description)
    if m:
        return int(m.group(1))
    m = _GOAL_NUM_RE.search(description)
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def classify_tree(tree):
    """{agent_id: meta} -> {'goal_slots': {...}, 'phases': {...}}, each entry gaining a 'goal' key.
    goal-slot: spawnDepth==1 AND description matches '#<N> goal-slot' (see _GOAL_SLOT_RE's own
    comment above -- a soft convention match, not an enforced contract). phase: spawnDepth==2 AND
    parentAgentId in goal_slots AND the PHASE's OWN description has a goal number (never
    inherited from the parent). Anything matching neither is dropped, not guessed into a bucket.

    Both `description` and `parentAgentId` are guarded via phase_report.get_str (#2531; was an
    inline `isinstance(..., str)` check, second fix round, now centralized into the shared
    accessor -- see that helper's own docstring for why: a meta.json field can legally be any JSON
    type, not just the string this code otherwise assumes). Confirmed live before the ORIGINAL fix:
    a non-string truthy `description` reached `_GOAL_SLOT_RE.search` and crashed with TypeError,
    and a non-string `parentAgentId` (e.g. a JSON list or dict, both unhashable) crashed the
    `in goal_slots` membership test with TypeError('unhashable type: ...')."""
    pr = _load("phase_report")
    goal_slots = {}
    for agent_id, meta in tree.items():
        if meta.get("spawnDepth") != 1:
            continue
        desc = pr.get_str(meta, "description")
        if desc is None or not _GOAL_SLOT_RE.search(desc):
            continue
        n = goal_number(desc)
        if n is not None:
            goal_slots[agent_id] = {**meta, "goal": n}
    phases = {}
    for agent_id, meta in tree.items():
        if meta.get("spawnDepth") != 2:
            continue
        parent = pr.get_str(meta, "parentAgentId")
        if parent is None or parent not in goal_slots:
            continue
        n = goal_number(meta.get("description"))
        if n is not None:
            phases[agent_id] = {**meta, "goal": n}
    return {"goal_slots": goal_slots, "phases": phases}


# --------------------------------------------------------------------------- dispatch correlation


def _norm_ts_precise(iso_ts):
    """Like phase_report.norm_ts (same 'T'->' ' and trailing 'Z' handling) but preserves
    sub-second precision instead of truncating it -- used ONLY for ordering/windowing goal-slot
    DISPATCH instants against each other (goal_slot_dispatch_ts, goal_windows), where two
    dispatches landing in the same whole SECOND must still sort and bound correctly (code review
    Bug 2: norm_ts's whole-second truncation made two same-second dispatches collide into an
    identical since_ts, and the tie then fell to filesystem/glob iteration order, not real
    dispatch order -- silently giving one goal a permanently-empty attribution window while the
    other absorbed both goals' calls). Deliberately NOT used for ordinary call ts values fed to
    phase_report.price_turn/select_rate -- those stay whole-second, matching the rate card's own
    'YYYY-MM-DD HH:MM:SS' granularity and phase_report.py's own unmodified pricing behavior (out
    of scope for this fix -- see plan #2531 Decision 2 and this module's own docstring on
    reusing phase_report.py unmodified). See _filter_calls's own docstring (NAMED, BOUNDED
    RESIDUAL LIMITATION) for the one residual, narrower, documented consequence of that precision
    difference.

    ASSUMES `iso_ts` IS ALREADY A STRING -- its one caller, _dispatch_ts_for_ids, must guard
    `isinstance(ts, str)` itself before calling this, matching this module's own established
    convention of guarding a JSON-parsed value at its use site rather than inside every leaf
    helper (code review Bug 3, second fix round: that call site used to check only truthiness
    (`if ts else None`), which let a non-string-but-truthy JSON value -- a number, a bool, a
    non-empty list/dict -- straight through to this function's `.strip()` and crash with
    AttributeError; a JSON `null` was already safe under the old truthiness check, but a JSON
    number, exactly the reviewer's own repro shape, was not)."""
    s = iso_ts.strip()
    if s.endswith("Z"):
        s = s[:-1]
    return s.replace("T", " ")


def _dispatch_ts_for_ids(top_level_path, tool_use_ids):
    """ONE pass over top_level_path resolving {tool_use_id: precise dispatch ts} for every id in
    `tool_use_ids` whose own tool_use block is found in it. The batched core both
    goal_slot_dispatch_ts (single-id convenience wrapper, below) and goal_windows now share, so
    resolving N goal-slots' dispatch timestamps costs ONE full top-level-transcript scan, not N
    (code review finding, second fix round -- SCALABILITY, AGENTS.md: goal_windows used to call
    goal_slot_dispatch_ts once PER goal-slot, each doing its own independent open()+line-scan of
    the SAME file -- loop-invariant work re-done N times, the identical class build_report's own
    comment above `top_level_calls` names for the orchestrator-window side of the same
    redundancy; together the two made build_report re-read/re-parse the top-level transcript
    ~2N+1 times for N goal-slots).

    Same guards, same 'skip the malformed line, never crash' convention as the single-id function
    this replaces internally (isinstance checks on obj/message/content/block -- code review Bug 1;
    `isinstance(ts, str)` before treating a timestamp as usable -- code review Bug 3, second fix
    round). An id never found, or found only with a malformed timestamp, is simply absent from --
    or maps to None in -- the returned dict, never fabricated, matching the single-id function's
    own None-on-not-found contract exactly. `tool_use_ids` may contain None/duplicate/falsy
    entries; they are dropped up front and never looked up.

    THREE independent crash sites folded into one fix here (#2531, third fix round -- all in this
    one function, the highest-density cluster this round's sweep found):
      (1) building `wanted` itself used to crash if any `tool_use_id` was UNHASHABLE (a JSON list
          or dict from a malformed `toolUseId` field) -- `{tid for tid in tool_use_ids if tid}`
          raises TypeError('unhashable type') the moment it tries to add such a value to the set;
      (2) even a HASHABLE-but-non-string survivor (an int, a float, a bool) used to crash the very
          next line, `tid in raw_line` -- `in` against a `str` requires a `str` on the left, and
          raises TypeError('requires string as left operand') for anything else;
      (3) `bid = block.get('id')` (a tool_use block's OWN id, from a DIFFERENT dict than
          `tool_use_ids` -- the transcript line just parsed) had no guard at all, so an unhashable
          `bid` crashed `bid in wanted`/`bid not in found` the same way (1) did.
    All three are the identical "any JSON type can legally sit behind this key" root cause,
    fixed the identical way: `phase_report.as_str`/`get_dict`/`get_list`/`get_str` keep every
    id/container that flows through this function to the type (`str`, hashable) it must be to be
    used as a `dict`/`set` key or compared to one, folding every wrong-typed value into the same
    "not found" degradation a genuinely-absent id already had -- never fabricated, never a crash."""
    pr = _load("phase_report")
    wanted = {tid for tid in tool_use_ids if pr.as_str(tid)}
    found = {}
    if not wanted:
        return found
    try:
        with open(top_level_path, "r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                if not any(tid in raw_line for tid in wanted):   # cheap pre-filter, same idea
                    continue
                try:
                    obj = json.loads(raw_line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "assistant":
                    continue
                message = pr.get_dict(obj, "message")
                if message is None:
                    continue
                content = pr.get_list(message, "content")
                if content is None:
                    continue
                for block in content:
                    if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                        continue
                    bid = pr.get_str(block, "id")
                    if bid in wanted and bid not in found:
                        ts = pr.get_str(obj, "timestamp")
                        found[bid] = _norm_ts_precise(ts) if ts else None
    except OSError:
        return found
    return found


def goal_slot_dispatch_ts(top_level_path, tool_use_id):
    """The orchestrator's own top-level-transcript line whose message.content contains a
    tool_use block with id == tool_use_id -> that line's own precise (sub-second, see
    _norm_ts_precise) timestamp, or None if never found (e.g. aged out of retention) or found with
    a malformed (non-string) timestamp -- code review Bug 3, second fix round. NEVER the matching
    tool_result's timestamp -- a background dispatch's tool_result is a near-instant ack, not
    completion (Plan §1c, verified live against this machine's own orchestrator transcript: a
    tool_use at :54.234Z, its tool_result ack at :54.253Z, only 19ms apart).

    A thin single-id convenience wrapper over _dispatch_ts_for_ids (second fix round -- see that
    function's own docstring for the guards, including Bug 1's isinstance checks on
    obj/message/content/block, and the redundant-scan history this fixes); kept as its own named,
    independently-tested entry point since it already has direct callers/tests that only need one
    id and should not have to build a set for it.

    `tool_use_id` is sanitized via phase_report.as_str (#2531, fourth fix round) before it is ever
    used as a dict key -- this function's own trailing `.get(tool_use_id)` used to read the RAW
    parameter directly, unlike every peer in this module (_dispatch_ts_for_ids's own `wanted` set,
    goal_windows's own `dispatch_ts.get()`, both sanitized). `dict.get()` requires a HASHABLE key
    regardless of the dict's size -- even the empty dict _dispatch_ts_for_ids correctly returns for
    an unhashable id does not save this call, since Python hashes the key before ever consulting
    the dict's contents, so an unhashable tool_use_id (a JSON list/dict) crashed here with
    TypeError('unhashable type'). Confirmed dead from the real CLI/build_report path today (only
    reachable through this function's own direct callers/tests -- goal_windows never calls this
    function; it uses _dispatch_ts_for_ids directly, with its own independent guard), fixed anyway
    for consistency with the rest of this module's now-structural guard discipline.

    RE-CONFIRMED still true, SEVENTH fix round (non-blocking finding (1)): re-grepped the whole
    repo for `goal_slot_dispatch_ts` -- every non-definition, non-docstring hit is still one of
    this function's own tests in tests/test_orchestrator_context_report.py; no production caller
    was added or removed since the note above was written. Left as documented, not removed -- it is
    an independently-tested, correctly-behaved public entry point (a thin single-id convenience
    wrapper a future caller could reach for instead of building a one-element tuple for
    _dispatch_ts_for_ids), not unreachable dead weight that active callers depend on some OTHER
    function to replace."""
    pr = _load("phase_report")
    tool_use_id = pr.as_str(tool_use_id)
    if tool_use_id is None:
        return None
    return _dispatch_ts_for_ids(top_level_path, (tool_use_id,)).get(tool_use_id)


def goal_windows(session_dir, top_level_path, classified=None):
    """Every goal-slot in classify_tree(walk_subagent_tree(session_dir)), each with its own
    since_ts (_dispatch_ts_for_ids, now full sub-second precision -- see _norm_ts_precise) and
    until_ts (the NEXT goal-slot's since_ts by real dispatch order, or None for the last/open
    one). Sorted by since_ts, tool_use_id as an explicit, deterministic TIEBREAK (code review Bug
    2): sub-second precision makes two dispatches colliding at the identical instant vanishingly
    rare, but not impossible, and a residual tie must still be well-defined rather than falling
    back to filesystem/glob iteration order (which is what walk_subagent_tree's dict insertion
    order silently depended on before this fix -- confirmed to actually flip the sort outcome on
    this host, see test_build_report_attributes_a_later_call_to_the_correct_goal_when_dispatches_
    share_a_second's own docstring). tool_use_id is chosen because it is already available on
    every row with no extra parsing, and it is stable/repeatable across runs, unlike glob order.
    An unresolvable since_ts sorts last and is still reported (never dropped, never fabricated) --
    'goal', 'agent_id', 'since_ts', 'until_ts', 'tool_use_id' keys.

    Resolves every goal-slot's dispatch ts in ONE _dispatch_ts_for_ids call (second fix round --
    was one goal_slot_dispatch_ts call PER goal-slot, each its own full-file scan; see that
    function's own docstring).

    `classified` (#2531, third fix round -- non-blocking finding (a)): an already-computed
    classify_tree(walk_subagent_tree(session_dir)) result, when the caller has one, so build_report
    does not walk+classify the SAME subagents/ tree twice in --detail mode (once here, once more
    for its own per-phase loop) -- the identical redundancy class already hoisted for the top-level
    transcript read (see build_report's own comment on `top_level_calls`). Every existing direct
    caller/test keeps working unchanged: default None still computes it internally, exactly as
    before.

    `tool_use_id` is sanitized via phase_report.get_str before it is ever used as a dict key or a
    sort-key field (#2531, third fix round): `meta.get('toolUseId')` can legally be any JSON type.
    Two independent crash sites, both now closed:
      - `dispatch_ts.get(tool_use_id)` requires a HASHABLE key -- an unhashable toolUseId (a JSON
        list/dict) crashed here with TypeError('unhashable type'), even after _dispatch_ts_for_ids
        itself was hardened, because THIS is a separate dict lookup on a DIFFERENT (raw) value;
      - the sort key's `r['tool_use_id'] or ''` compares whatever survives across ALL rows -- once
        any two rows carry different non-string types (or a string against a non-string) in a tied
        position, Python's tuple comparison crashes with TypeError('not supported between
        instances of ...'). This is not a rare path: a malformed toolUseId typically also means an
        unresolvable since_ts (None), and rows with since_ts=None are exactly the ones that tie and
        fall through to this tiebreak, on every real multi-goal-slot session with one malformed
        entry -- not a hypothetical edge case. get_str keeps `tool_use_id` uniformly str|None, so
        both the dict lookup and the sort are always well-typed.

    SIXTH fix round -- a third, structurally distinct shape of the same underlying problem (round
    2: two RESOLVED dispatches collided at the same whole second, fixed by _norm_ts_precise above;
    round 5: an UNRESOLVED goal's own row fabricated the whole session's totals, fixed by the
    g_since-is-None guard in build_report). This round's reviewer repro: #100/#200/#300 dispatch in
    that real chronological order, #200's own dispatch unresolvable; #100's until_ts (computed two
    lines below as "the next row's since_ts") silently became #300's since_ts, since #200 sorts
    LAST here regardless of its TRUE position (every unresolved row sorts after every resolved one
    -- `since_ts is None` is the first, dominant element of the sort key above) -- so #100's window
    silently absorbed #200's real, chronologically-intervening activity, with nothing in the output
    signaling it happened.

    Read fresh over the WHOLE state space (every relative ordering of resolved/unresolved rows, not
    just the reported 3-goal shape), this function's OWN until_ts computation turns out to be
    PROVABLY THE BEST ANSWER ACHIEVABLE from its own inputs, not a bug in itself, and changing it
    cannot fix the contamination: for any resolved row R, "the next row after R, skipping over any
    row with no resolved since_ts of its own" and "the next row after R in THIS function's actual
    sort" are the SAME row, always -- because every unresolved row already sorts strictly after
    EVERY resolved row (see the dominant `since_ts is None` sort key above), an unresolved row can
    only ever be encountered after every resolved row has already been passed, never inserted
    between two resolved rows the way "skip past it" might suggest. Verified both by hand and
    empirically (a scratch repro run against this exact function, sixth fix round, across 7
    orderings -- 3/4/7-goal, unresolved first/middle/last/sandwiching/multiple) before writing this
    paragraph, not merely asserted: no reordering of this function's own sort or boundary arithmetic
    changes a single computed until_ts value.

    The defect is therefore NOT in this function, and NOT fixable here: an unresolved row's own
    since_ts is None, which carries ZERO positional information -- its TRUE dispatch could fall
    before the first resolved row, between any two resolved rows, or after the last, and nothing in
    this function's inputs can ever distinguish those possibilities from one another. So whenever
    ANY row in the set this function returns has since_ts=None, NO resolved row's [since_ts,
    until_ts) can be proven free of that unresolved row's real activity -- not just its two visually
    "adjacent" neighbors in sorted order, EVERY resolved row, because "adjacency" itself is a fact
    about sort order, not about the unknowable true timeline. The honest fix is therefore not a
    different number here, it is a caveat attached to the number that already is the best one
    achievable -- computed and attached one layer up, in build_report (see its own `unresolved_goals`
    / `boundary_partial` comment), which alone has visibility into the FULL, report-wide set of rows
    this function returns. Not restated here: this function keeps returning plain, uncaveated rows,
    exactly as before this round, so every existing direct caller/test of goal_windows itself is
    unaffected by this round's fix."""
    pr = _load("phase_report")
    if classified is None:
        classified = classify_tree(walk_subagent_tree(session_dir))
    dispatch_ts = _dispatch_ts_for_ids(
        top_level_path, (meta.get("toolUseId") for meta in classified["goal_slots"].values()))
    rows = []
    for agent_id, meta in classified["goal_slots"].items():
        tool_use_id = pr.get_str(meta, "toolUseId")
        since_ts = dispatch_ts.get(tool_use_id) if tool_use_id else None
        rows.append({"goal": meta["goal"], "agent_id": agent_id, "since_ts": since_ts,
                     "tool_use_id": tool_use_id})
    rows.sort(key=lambda r: (r["since_ts"] is None, r["since_ts"] or "", r["tool_use_id"] or ""))
    for i, row in enumerate(rows):
        row["until_ts"] = rows[i + 1]["since_ts"] if i + 1 < len(rows) else None
    return rows


# --------------------------------------------------------------------------- report assembly


def _scope_totals(calls, rates):
    cost_usd, unpriced = priced_totals(calls, rates)
    return {"calls": len(calls), "peak_context": peak_context(calls),
            "volume_total": volume_totals(calls), "cost_usd": cost_usd,
            "unpriced_calls": unpriced}


def _floor_to_whole_second(ts):
    """Strip a trailing '.NNN...' fractional-second suffix from an already-normalized
    'YYYY-MM-DD HH:MM:SS[.fff]' string (goal_windows's own _norm_ts_precise shape); None passes
    through unchanged. (#2531, SEVENTH fix round.) The counterpart, at whole-second granularity, of
    phase_report.norm_ts's own truncation of a RAW ISO-8601 timestamp -- see _filter_calls's own
    docstring for why flooring the BOUNDARY down to match a call's own already-whole-second `ts`
    (never the reverse: a call's own ts is never given false extra precision it does not have) is
    the only direction that adds no fabricated information to either side of the comparison."""
    return ts.split(".", 1)[0] if ts is not None else None


def _filter_calls(calls, since_ts, until_ts):
    """Filter an ALREADY-deduped calls list to [since_ts, until_ts) -- either bound is optional
    (None disables that side; both None returns `calls` entirely unfiltered). Split out (second
    fix round) so build_report can read+dedup the top-level transcript ONCE and reuse the SAME
    deduped list for the overall scope and every goal-slot's own orchestrator_window, instead of
    re-reading the file per window (see build_report's own comment on `top_level_calls`). Always
    returns a NEW list built by a comprehension -- `calls` itself is never mutated or sliced in
    place -- so filtering one window can never leak into, or corrupt, another caller's own
    filtered result. Because both-None is a real, silent no-op (not an error), a caller with a
    bound that is None because it is genuinely UNRESOLVED, not because it was deliberately omitted,
    must decide that BEFORE calling this -- see build_report's own orchestrator_window guard
    (fifth fix round) for a caller that must not skip that decision.

    GRANULARITY-CONSISTENT COMPARISON (#2531, SEVENTH fix round -- replaces the bug this same
    paragraph used to mischaracterize as bounded and hypothetical; see below for what was actually
    true): `since_ts`/`until_ts` can carry sub-second precision when they come from goal_windows
    (_norm_ts_precise, code review Bug 2 fix), but `c["ts"]` here is ALWAYS whole-second
    (phase_report.iter_assistant_turns/norm_ts, unchanged by design and out of scope to change here
    -- phase_report.price_turn/select_rate key off this SAME field for rate-card lookups, at the
    rate card's own whole-second granularity; see _norm_ts_precise's own docstring). Comparing a
    whole-second string against a sub-second one with a bare `>=`/`<` compares mismatched units: a
    call whose true instant exactly EQUALS a dispatch boundary (the common case -- the call IS that
    boundary's own defining tool_use line) truncates to a STRICTLY SMALLER string than the
    sub-second boundary it should be inclusive-equal to ("2026-09-18 10:00:54" < "2026-09-18
    10:00:54.234" even though both name the same real second), so it always sorted as "before" its
    own window and was silently excluded -- every time a call sat ON a boundary, not just near a
    collision. Both `since_ts` and `until_ts` are now FLOORED to whole-second (`_floor_to_whole_
    second`, a local copy used only for this comparison -- the sub-second values goal_windows/
    build_report return to their own callers, e.g. a row's own displayed `dispatch_ts`/`until_ts`,
    are untouched) before comparing, so both sides share one granularity and the comparison is
    exact rather than approximate.

    WHAT WAS ACTUALLY TRUE, vs. this paragraph's own prior claim ('bounded to at most ~1 second...
    only when two dispatches genuinely collide within that second (today: never observed)') --
    corrected here because both halves were false, verified directly against this session's own
    real data (dd8a2b2a-adb2-47af-9710-ac54e9f9f443.jsonl lines 1727/1729/1731, #2543/#2544/#2531's
    real dispatch) before writing this paragraph, not assumed: the old claim modeled the failure as
    two ADJACENT dispatches colliding within one second. It missed a much larger, entirely
    different path to the identical symptom -- dedup_calls (see that function's own docstring)
    collapses several raw lines sharing one message.id into ONE merged call whenever a single
    orchestrator turn dispatches several goal-slots at once (several `Agent` tool_use blocks, one
    turn -- exactly `/agrim-loop`'s own real batch-dispatch shape, and exactly what happened for
    #2543/#2544/#2531 in the session above). That merged call's ts (dedup_calls's own MIN across
    the group) then sat ON the group's OWN earliest goal-slot's dispatch boundary and, unfloored,
    sorted before it -- and before every LATER goal-slot's boundary too, since MIN never advances.
    Unfixed, the merged call vanished from every affected goal's own orchestrator_window entirely
    (not just the nearest one), while staying visible in the whole-session total -- a loss spanning
    the FULL range between the first and last dispatch sharing that message.id (36 seconds in the
    real data above), unbounded by any single "~1 second" figure, and observed TODAY on a real,
    live session, not a hypothetical never-yet-seen collision.

    REMAINING, NOW GENUINELY BOUNDED, RESIDUAL: two or more goal-slots dispatched within the exact
    SAME whole second still cannot have their calls told apart by this function alone -- flooring
    makes the EARLIER one's own window zero-width (`[X, X)`, since its `until_ts` floors to the
    same value as its own `since_ts`), so any call sharing that same floored second is attributed
    to the LATEST dispatch sharing it, deterministically -- never lost, never double-counted, never
    silently misattributed to the wrong side, just not SPLIT between the colliding dispatches (see
    test_build_report_attributes_a_later_call_to_the_correct_goal_when_dispatches_share_a_second).
    This is a real simplification (which of several same-second dispatches a same-second call
    belongs to is genuinely undecidable from whole-second data) with a fixed, named shape -- not an
    approximation with an unbounded tail like the one it replaces."""
    since_floor = _floor_to_whole_second(since_ts)
    until_floor = _floor_to_whole_second(until_ts)
    return [c for c in calls
            if (since_floor is None or c["ts"] >= since_floor)
            and (until_floor is None or c["ts"] < until_floor)]


def build_report(sdlc_dir, session_id, since=None, until=None, goal=None, detail=False,
                  home=None, rates=None):
    """Assemble Tasks 1-4 into one report dict: {'source': 'ok', 'session', 'since', 'until',
    'orchestrator': <scope totals>, 'goals': [<per-goal-slot row>, ...]} or
    {'source': 'unavailable', 'reason': '...'} when no top-level transcript is found for
    session_id at all. Never fabricates a number (Global constraints) -- a goal-slot row with no
    resolvable dispatch_ts, a session with zero subagents, or an unpriced model each degrade
    honestly rather than crash or guess."""
    pr = _load("phase_report")
    rate_rows = rates if rates is not None else pr.load_rate_rows()
    session_dir = pr.find_claude_session_dir(session_id, home=home)
    top_level = pr.find_claude_main_transcript(session_id, home=home)
    if top_level is None:
        return {"source": "unavailable",
                "reason": f"no top-level transcript found for session {session_id!r}"}
    since_norm = pr.norm_ts(since) if since else None
    until_norm = pr.norm_ts(until) if until else None
    # Hoisted OUT of the per-goal-slot loop below (code review finding, second fix round -- the
    # identical class of loop-invariant redundancy already hoisted for classify_tree() further
    # down, just not applied to the top-level transcript too): reading and deduping the top-level
    # transcript depends only on `top_level`, never on which goal-slot's window is being computed,
    # so doing it once per goal-slot (via the old per-window `_windowed_calls(top_level, ...)`
    # helper -- since removed as confirmed-dead code, fifth fix round; see _filter_calls's own
    # docstring for the residual precision note it carried) PLUS once more for the overall scope
    # was ~2N+1 full re-reads/re-parses of the SAME file for N goal-slots. Parsed+deduped ONCE
    # here; the overall scope and every goal-slot's own
    # orchestrator_window now filter this SAME immutable list in memory via _filter_calls, which
    # never mutates its input and always returns a fresh list -- so one goal-slot's window can
    # never leak into or corrupt another's (see
    # test_build_report_gives_each_goal_slot_its_own_correct_window_after_the_read_is_shared and
    # the open()-counting control,
    # test_build_report_top_level_transcript_reads_stay_constant_as_goal_slots_grow).
    top_level_calls = dedup_calls(list(iter_raw_assistant_calls(top_level)))
    orch_calls = _filter_calls(top_level_calls, since_norm, until_norm)
    report = {"session": session_id, "since": since_norm, "until": until_norm,
              "orchestrator": _scope_totals(orch_calls, rate_rows), "goals": [], "source": "ok"}
    if session_dir is None:
        return report                       # orchestrator-only: no subagents/ tree to walk
    # Hoisted OUT of the per-window loop below (code review finding): walking every meta.json and
    # re-classifying the whole tree is loop-INVARIANT (depends only on session_dir, never on
    # `window`), so doing it once per goal-slot instead of once total was an undisclosed O(N x
    # subagent_count) cost in --detail mode -- effectively O(N^2), since subagent_count itself
    # grows with N. Computed once here and reused for every row.
    #
    # THIRD fix round (non-blocking finding (a)): this used to be computed a SECOND time (guarded
    # by `if detail else None`) even though goal_windows() below ALSO calls
    # classify_tree(walk_subagent_tree(session_dir)) internally -- so a --detail report walked and
    # re-parsed the ENTIRE subagents/ tree twice per report, the identical redundancy class already
    # fixed for the top-level transcript (`top_level_calls`, above). Computed exactly ONCE here now
    # and passed into goal_windows(classified=...) instead of letting it recompute its own; a
    # non-detail report costs the same as before (goal_windows always needed this tree regardless
    # of `detail`), and a --detail report drops from 2 walks to 1.
    classified = classify_tree(walk_subagent_tree(session_dir))
    windows = goal_windows(session_dir, top_level, classified=classified)
    # SEVENTH fix round (non-blocking finding (2)): --detail's own per-goal-slot phase loop below
    # used to scan ALL of classified["phases"] (every phase in the WHOLE session) once per
    # goal-slot, filtering by `parentAgentId == window["agent_id"]` each time -- O(goal_slots x
    # total_phases), the identical loop-invariant-redundancy class already hoisted twice above in
    # this same function (`top_level_calls`, second fix round; this `classified` tree itself, third
    # fix round). Grouped by parent ONCE here instead -- O(goal_slots + total_phases) -- computed
    # only when `detail` is requested, so a non-detail report keeps paying exactly nothing for it,
    # unchanged. `dict.setdefault(...).append(...)` preserves `classified["phases"].items()`'s own
    # iteration order within each parent's own list, so which phase appears before which (for a
    # shared parent) is byte-identical to before this change, not just the same SET of phases.
    phases_by_parent = None
    if detail:
        phases_by_parent = {}
        for agent_id, meta in classified["phases"].items():
            phases_by_parent.setdefault(meta.get("parentAgentId"), []).append((agent_id, meta))
    # SIXTH fix round -- see goal_windows's own docstring (SIXTH fix round paragraph) for the full
    # proof this relies on. Computed ONCE here, over the FULL window set, BEFORE the `--goal`
    # display filter two lines below can narrow it: "does at least one goal-slot in this report
    # have an unresolvable dispatch timestamp" is a fact about the whole report, not about any one
    # row, and must not change depending on which single goal a caller later asks to display (a
    # `--goal 100` request still needs to know #200 exists and is unresolved, even though #200
    # itself is never displayed). A goal-slot excluded from display by an explicit --since/--until
    # (the `continue` guards below) still counts here if it is itself unresolved -- that CLI filter
    # only controls what is shown, it does not shrink the set of goal-slots whose true, unknown
    # dispatch time could fall inside a DISPLAYED goal's window.
    unresolved_goals = sorted({w["goal"] for w in windows if w["since_ts"] is None})
    for window in windows:
        if goal is not None and window["goal"] != goal:
            continue
        g_since = window["since_ts"]
        g_until = window["until_ts"]
        # Symmetric fail-closed handling (code review finding): an unresolvable dispatch ts
        # (g_since is None) used to be excluded under --since but INCLUDED under --until -- the
        # same uncertain fact treated two different ways depending on which flag happened to be
        # set. Both now exclude on uncertainty, matching this module's own never-fabricate/never-
        # guess rule (Global constraints) applied to "is this goal in the requested range" too.
        if since_norm and (g_since is None or g_since < since_norm):
            continue
        if until_norm and (g_since is None or g_since >= until_norm):
            continue
        row = {"goal": window["goal"], "agent_id": window["agent_id"],
               "dispatch_ts": g_since, "until_ts": g_until}
        subagent_path = pr.find_claude_agent_transcript(session_id, window["agent_id"], home=home)
        if subagent_path is not None:
            row["subagent"] = _scope_totals(dedup_calls(list(iter_raw_assistant_calls(subagent_path))),
                                             rate_rows)
        else:
            row["subagent"] = {"source": "unavailable", "reason": "no subagent transcript found"}
        # Guarded (code review, fifth fix round) to match the subagent/phase rows' own
        # unavailable/reason convention immediately above and below this line. UNGUARDED, this
        # called _filter_calls(top_level_calls, g_since, g_until) unconditionally -- and
        # _filter_calls(calls, None, None) applies NO filtering at all (see its own docstring: both
        # bounds are optional), so it returned the ENTIRE deduped top_level_calls list. g_since is
        # None here exactly when this goal-slot's own dispatch instant could not be resolved
        # (goal_windows's own honest-None contract) -- reachable at this point in the loop only
        # under a fail-OPEN request window (no --since/--until at all, i.e. this tool's own DEFAULT
        # invocation): the two `continue` guards just above already exclude a since_ts=None row
        # under any EXPLICIT --since/--until, which is exactly why every pre-existing windowed-call
        # test passed without ever reaching this line in that state. Unguarded, a goal-slot in this
        # state got orchestrator_window silently set to the WHOLE SESSION's totals instead of an
        # honest `unavailable` -- and when two or more goal-slots in the same report shared this
        # same unresolved state, each independently received the SAME whole-session figures, so
        # summing "per goal" totals across goals double/multi-counted real activity that happened
        # once. g_until is never non-None here while g_since is None (goal_windows sorts every
        # unresolved-since_ts row after every resolved one, so such a row's own until_ts -- the
        # NEXT row's since_ts -- is always itself None too), but this guard checks g_since alone,
        # deliberately, so it stays correct even if that sort invariant ever changes.
        if g_since is None:
            row["orchestrator_window"] = {"source": "unavailable",
                                           "reason": "no resolvable dispatch timestamp"}
        else:
            row["orchestrator_window"] = _scope_totals(
                _filter_calls(top_level_calls, g_since, g_until), rate_rows)
            # SIXTH fix round -- the third shape of the unresolved-dispatch-ts problem (round 2:
            # same-second collision; round 5: the unresolved goal's OWN row fabricated the whole
            # session's totals, fixed above via the g_since-is-None branch). Reviewer's repro:
            # #100/#200(unresolvable)/#300 dispatch in that real order; #100's window here silently
            # absorbed #200's own real, chronologically-intervening activity, because #100's
            # until_ts (from goal_windows) is "the next REsolved row's since_ts" -- #300's -- with
            # nothing signaling that an unresolved goal-slot's real dispatch could have landed
            # anywhere inside [g_since, g_until) instead. goal_windows's own docstring (SIXTH fix
            # round paragraph) proves this number cannot be made more precise by changing that
            # function's sort/boundary logic -- an unresolved since_ts carries ZERO positional
            # information, so it could fall before the first resolved row, between any two, or
            # after the last, and nothing here can rule any of those out. So EVERY resolved goal's
            # own orchestrator_window in this report is marked, not just #100's or #300's (this
            # function's own two visually-"adjacent" rows in the reviewer's 3-goal repro) --
            # "adjacency" in sorted order is a fact about the sort, not about the true, unknown
            # timeline, and a report with 2+ unresolved goal-slots or 4+ total goal-slots has no
            # well-defined notion of "adjacent" for this purpose anyway (verified across 7 real
            # orderings, sixth fix round scratch repro, including one where the contaminated row
            # and the unresolved row are two goals apart with an unaffected resolved row between
            # them -- that middle row is STILL marked, because the guarantee it needs ["#150 did
            # not dispatch inside MY window"] is exactly as unprovable for it as for its neighbors).
            # The raw calls/peak_context/volume_total figures are deliberately left UNCHANGED, not
            # zeroed or blanked to 'unavailable' -- they remain the best available estimate (and are
            # exactly correct whenever the unresolved sibling's true dispatch happens to fall
            # outside this window, which this module has no way to confirm either way); only an
            # honest caveat is added, matching this module's own established 'partial' vocabulary
            # (_cost_field's unpriced-calls qualifier) applied to a new, different source of
            # imprecision (a boundary that cannot be proven, not a call that cannot be priced).
            if unresolved_goals:
                row["orchestrator_window"]["boundary_partial"] = True
                row["orchestrator_window"]["boundary_partial_goals"] = unresolved_goals
                row["orchestrator_window"]["boundary_partial_reason"] = (
                    "goal(s) " + ", ".join(f"#{n}" for n in unresolved_goals) + " in this same "
                    "report have no resolvable dispatch timestamp; this window's true boundary "
                    "against them cannot be proven, so it may include or exclude their real "
                    "activity")
        if detail:
            row["phases"] = []
            # phases_by_parent (SEVENTH fix round, see its own comment above `classified`'s
            # assignment): grouped ONCE before this loop, O(1)-average lookup here instead of an
            # O(total_phases) scan of classified["phases"] on every one of the `windows` iterations.
            for agent_id, meta in phases_by_parent.get(window["agent_id"], []):
                phase_path = pr.find_claude_agent_transcript(session_id, agent_id, home=home)
                # get_str, not a bare .get() (#2531, third fix round): 'description' is one more
                # meta.json field that can legally be any JSON type. It never crashed here (an
                # f-string / json.dumps both tolerate any type), but a malformed value would have
                # shown a raw Python repr (e.g. "phase [1, 2, 3]  calls=...") instead of degrading
                # the same honest way every OTHER malformed field in this row already does -- see
                # render_text's matching `.get('description') or '?'` fallback below.
                phase_row = {"agent_id": agent_id, "description": pr.get_str(meta, "description")}
                if phase_path is not None:
                    phase_row.update(_scope_totals(
                        dedup_calls(list(iter_raw_assistant_calls(phase_path))), rate_rows))
                else:
                    # "reason" added (code review finding) to match the subagent row's own
                    # convention above -- render_text can only show a reason it was actually given.
                    phase_row["source"] = "unavailable"
                    phase_row["reason"] = "no phase transcript found"
                row["phases"].append(phase_row)
        report["goals"].append(row)
    return report


# --------------------------------------------------------------------------- CLI

_VALUE_FLAGS = ("--session", "--since", "--until", "--goal", "--format", "--home")


def _flags(argv):
    """Copied from phase_report.py's own _flags() -- see that module's docstring for why this is
    duplicated, not imported, across sibling scripts."""
    out = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            name, eq, value = token[2:].partition("=")
            if eq:
                if " " not in name:
                    out[name] = value
            elif f"--{name}" in _VALUE_FLAGS and i + 1 < len(argv):
                out[name] = argv[i + 1]
                i += 2
                continue
            else:
                out[name] = "true"
        i += 1
    return out


def _session_id():
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "")


def _cwd_within_repo(cwd_value, repo_root):
    """True if `cwd_value` (a transcript line's own recorded `cwd`) IS repo_root or is nested
    under it -- covers both the main checkout and any `.sdlc/work/<goal>` goal worktree, since
    this repo's own convention nests goal worktrees INSIDE the main checkout. A worktree
    discovered via `git worktree list` (a downstream transcript reader's own precedent for
    the identical "a worktree's slug differs from its parent repo's" problem) is not handled here
    -- that would spawn a subprocess, which this module's own Global constraints forbid outright
    ('No new subprocess... anywhere in this module'); this module's fallback is scoped only to
    the case this repo's own on-disk layout already gives us for free (a worktree path IS a
    subpath of repo_root).

    GUARDS `isinstance(cwd_value, str)` (#2531, third fix round -- reachable through
    resolve_session_id's no-`--session`-given auto-fallback, i.e. this tool's own DEFAULT
    invocation path). A truthy non-string `cwd` (a JSON list/dict/number) used to crash
    `pathlib.Path(cwd_value)` with TypeError('argument should be a str or an os.PathLike object')
    -- NOT one of the three exception types this function's own `except` clause already caught
    (OSError/RuntimeError/ValueError), so it propagated uncaught. Guarded here at the function's
    own boundary (not only at its one call site, `_session_file_belongs_to_repo`, which ALSO now
    passes it a pre-sanitized value via `get_str`) so this stays safe for any future caller too."""
    if not isinstance(cwd_value, str) or not cwd_value:
        return False
    try:
        p = pathlib.Path(cwd_value).resolve()
        root = pathlib.Path(repo_root).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return p == root or root in p.parents


def _session_file_belongs_to_repo(path, repo_root, max_lines=50):
    """Bounded-prefix scan (never the whole file -- SCALABILITY, Global constraints) of a
    candidate top-level session file's first `max_lines` lines for a `cwd` field, matched via
    `_cwd_within_repo`. Real Claude Code transcripts carry `cwd` from very early in the file
    (verified directly against this machine's own orchestrator transcript: the third line) --
    a file with no cwd-bearing line in that prefix is treated as NOT belonging: fail closed,
    never a guess, matching this module's own never-fabricate rule applied to matching a session
    to a repo, not just to pricing a turn.

    `cwd` now read via phase_report.get_str, not a bare `.get()` (#2531, third fix round) --
    belt-and-suspenders with _cwd_within_repo's own new guard (see that function's docstring):
    this call site never even constructs a non-string value to pass it in the first place."""
    pr = _load("phase_report")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, raw_line in enumerate(fh):
                if i >= max_lines:
                    break
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                cwd = pr.get_str(obj, "cwd")
                if cwd:
                    return _cwd_within_repo(cwd, repo_root)
    except OSError:
        return False
    return False


def resolve_session_id(explicit, home=None, repo_root=None):
    """explicit (a real id, or None/'auto') -> the session id to use, or None if nothing is
    resolvable. Order: (1) an explicit non-'auto' value wins outright; (2) CLAUDE_CODE_SESSION_ID,
    via this module's own `_session_id()` (#2531, SEVENTH fix round -- corrected here: this used to
    claim `_session_id()` "reuses... phase_report._session_id()'s own existing convention", but the
    code calls THIS module's own locally-duplicated identical one-liner, never phase_report's --
    verified by reading both: byte-identical bodies, `os.environ.get("CLAUDE_CODE_SESSION_ID",
    "")`, but two separate function objects. Calling phase_report's own copy directly was NOT the
    fix, and is not one here either: `phase_report._session_id` is underscore-prefixed/private, and
    this module has a consistent, zero-exception, explicitly-documented convention of never calling
    a sibling module's private helpers -- only duplicating them, exactly like `_load`'s own
    docstring already establishes for `_flags`/`_home` (verified: no `pr._<anything>` call exists
    anywhere in this file). `_session_id()` here already follows that SAME precedent; the docstring
    was simply describing it wrong, as reuse instead of a deliberate, convention-consistent
    duplicate); (3) the
    most-recently-modified top-level <uuid>.jsonl under ~/.claude/projects/*/ AMONG CANDIDATES
    THAT BELONG TO `repo_root` (default: this checkout's own _REPO_ROOT) -- a heuristic fallback
    for a script run by hand outside a live session, scoped and documented as a heuristic, not a
    guarantee.

    REPO-SCOPED, not a bare machine-wide glob (Plan-Review refinement 1): the original draft of
    this fallback picked the single newest *.jsonl across EVERY project directory on the machine,
    with no filter for which repo it belonged to -- on a multi-repo dev machine that can silently
    return an unrelated repo's session as "the" session, a misattribution, not an honest
    `unavailable`. `find_claude_session_dir` (phase_report.py) does not itself derive a project-
    directory slug to reuse here (its own docstring: "deliberately NOT re-deriving Claude Code's
    own cwd-to-slug transform") -- checked directly before writing this, since the alternative
    (blindly re-deriving a slug transform by hand) is exactly the kind of fragile, unverified
    guess AGENTS.md's RELIABILITY property warns against. This instead reuses this codebase's
    OTHER, already-shipped precedent for the identical problem: a downstream transcript reader
    matches each line's own real `cwd` field rather than a directory slug, specifically because a
    worktree session's slug differs from its parent repo's (its own module docstring, point 1).
    `_cwd_within_repo`/`_session_file_belongs_to_repo` do the read-only half of that same idea
    (no `git worktree list` subprocess -- disallowed by this module's own no-subprocess rule --
    so this checks prefix-membership under repo_root directly, which already covers this repo's
    own `.sdlc/work/<goal>` worktree convention since those live INSIDE repo_root).

    SCALABILITY (#2531, third fix round -- non-blocking finding (b), AGENTS.md: 'named as ceilings
    with a number attached, not left to be discovered by a customer'): the fallback below is
    O(total .jsonl files across every directory under ~/.claude/projects/), each opened for a
    BOUNDED <=50-line prefix read (_session_file_belongs_to_repo's own `max_lines` cap keeps the
    PER-FILE cost fixed) -- but the FILE COUNT itself is unbounded: nothing in this codebase prunes
    ~/.claude/projects/, so it only grows as a machine accumulates sessions across every repo ever
    worked in, on every host, indefinitely. Measured on this machine, right now: 83 project
    directories, 327 top-level session files (`find ~/.claude/projects -maxdepth 1 -type d`,
    `find ~/.claude/projects -maxdepth 2 -name '*.jsonl'`) -- so today's worst case here is ~327
    bounded file opens, not one unbounded scan; on a multi-year, multi-repo dev machine this climbs
    into the thousands. This is the tool's own DEFAULT invocation path (no `--session` given), so
    it is not a cold or rarely-hit corner. No safe cheap fix was applied: the obvious shortcuts
    (stop after the first N files by arbitrary iteration order, or by mtime without checking
    membership) both risk silently returning the WRONG repo's session instead of the right one --
    a correctness regression, not a performance one, for what is already a best-effort heuristic
    fallback (see this function's own 'not a guarantee' framing above) -- worse than staying slow
    but correct. Left as a documented, measured ceiling rather than an undocumented one; a real fix
    (an index, or a cache keyed by repo_root) is future work, not this round's."""
    if explicit and explicit != "auto":
        return explicit
    env = _session_id()
    if env:
        return env
    projects = _home(home) / ".claude" / "projects"
    if not projects.is_dir():
        return None
    root = repo_root if repo_root is not None else _REPO_ROOT
    candidates = []
    for proj_dir in projects.iterdir():
        if not proj_dir.is_dir():
            continue
        for f in proj_dir.glob("*.jsonl"):
            if _session_file_belongs_to_repo(f, root):
                candidates.append(f)
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return newest.stem


def render_text(report):
    lines = []
    if report["source"] != "ok":
        return [f"unavailable: {report.get('reason', 'unknown reason')}"]
    lines.append(f"Session {report['session']} "
                 f"(since {report['since'] or 'start'}, until {report['until'] or 'open'})")
    lines.append("")
    o = report["orchestrator"]
    lines.append(f"ORCHESTRATOR  calls={o['calls']}  peak_context={o['peak_context']:,}  "
                 f"volume={o['volume_total']:,}  {_cost_field(o)}")
    for row in report["goals"]:
        lines.append("")
        lines.append(f"#{row['goal']} goal-slot (agent {row['agent_id']})  "
                     f"dispatched {row['dispatch_ts'] or 'unavailable'}  "
                     f"until: {row['until_ts'] or '(open)'}")
        for label, key in (("subagent", "subagent"), ("orchestrator-window", "orchestrator_window")):
            s = row.get(key, {})
            if s.get("source") == "unavailable":
                lines.append(f"  {label:<20} unavailable ({s.get('reason', 'unknown')})")
            else:
                # boundary_partial (#2531, sixth fix round) is only ever set on
                # 'orchestrator_window' (see build_report's own comment) -- 'subagent' reads one
                # agent's own transcript file directly, never windowed against a sibling goal-slot's
                # dispatch, so this key is always absent there and the suffix never fires for it.
                boundary_note = (f"  [PARTIAL: {s['boundary_partial_reason']}]"
                                  if s.get("boundary_partial") else "")
                lines.append(f"  {label:<20} calls={s['calls']}  peak_context={s['peak_context']:,}  "
                             f"volume={s['volume_total']:,}  {_cost_field(s)}{boundary_note}")
        for phase in row.get("phases", []):
            # '.get('description') or '?'', not '.get('description', '?')' (#2531, third fix
            # round): build_report now stores None (via get_str) for a malformed 'description'
            # rather than a raw, possibly non-string value -- and the key is then PRESENT with
            # value None, so the ", '?'" default (which only fires when the key is MISSING
            # entirely) would print the literal word "None" instead of the same honest '?'
            # placeholder a missing description already gets.
            if phase.get("source") == "unavailable":
                # Now shows the reason too (code review finding), matching the subagent/
                # orchestrator-window branch above -- it used to print the bare word
                # "unavailable" and drop the reason on the floor even when build_report supplied
                # one.
                lines.append(f"    phase {phase.get('description') or '?'}  "
                             f"unavailable ({phase.get('reason', 'unknown')})")
                continue
            lines.append(f"    phase {phase.get('description') or '?'} (agent {phase['agent_id']})  "
                         f"calls={phase['calls']}  peak_context={phase['peak_context']:,}  "
                         f"volume={phase['volume_total']:,}  {_cost_field(phase)}")
    return lines


def _cost_field(scope):
    """Mirrors phase_report.end_lines's own honest-degradation phrasing (partial-unpriced
    qualifier, 'cost: unavailable on this host (...)') -- a deliberate style match, not a shared
    string constant, since this is a separate report tool, not a phase-boundary banner.

    Distinguishes a genuinely EMPTY scope from an UNPRICED one (code review finding, visible in
    this goal's own Real-run results: #2544's real orchestrator-window had calls=0 and printed
    'model not in rate card', which is misleading -- there was no model to look up at all, because
    there were no calls). priced_totals returns cost_usd=None in BOTH cases (0 calls -> priced=0,
    same as every call failing to price), so scope['calls'] is the only signal that tells them
    apart."""
    cost = scope.get("cost_usd")
    if cost is None:
        if not scope.get("calls"):
            return "cost: unavailable on this host (no calls in this scope)"
        return "cost: unavailable on this host (model not in rate card)"
    unpriced = scope.get("unpriced_calls", 0)
    partial = f" (partial — {unpriced} of {scope['calls']} calls unpriced)" if unpriced else ""
    return f"${cost:.2f}{partial}"


def cmd_report(argv):
    if len(argv) < 1:
        print("usage: orchestrator_context_report.py report <sdlc_dir> [--session ID|auto] "
              "[--since ISO] [--until ISO] [--goal N] [--detail] [--format text|json] [--home PATH]",
              file=sys.stderr)
        return 2
    sdlc_dir = argv[0]
    flags = _flags(argv[1:])
    repo_root = pathlib.Path(sdlc_dir).resolve().parent
    session_id = resolve_session_id(flags.get("session"), home=flags.get("home"),
                                     repo_root=repo_root)
    if session_id is None:
        print("orchestrator_context_report.py: no session id given, CLAUDE_CODE_SESSION_ID unset, "
              "and no session transcript found under ~/.claude/projects/ to fall back to",
              file=sys.stderr)
        return 1
    try:
        goal = int(flags["goal"]) if flags.get("goal") else None
    except ValueError:
        # Code review finding: this used to raise an uncaught ValueError straight out of int(),
        # a raw traceback instead of a clear CLI usage error.
        print(f"orchestrator_context_report.py: --goal must be an integer, got {flags['goal']!r}",
              file=sys.stderr)
        return 2
    report = build_report(sdlc_dir, session_id, since=flags.get("since"), until=flags.get("until"),
                           goal=goal, detail="detail" in flags, home=flags.get("home"))
    fmt = flags.get("format", "text")
    if fmt == "json":
        print(json.dumps(report, indent=2))
    else:
        for line in render_text(report):
            print(line)
    return 0 if report["source"] == "ok" else 1


USAGE = "usage: orchestrator_context_report.py report <sdlc_dir> [flags]"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) < 2 or argv[1] != "report":
        print(USAGE, file=sys.stderr)
        return 2
    return cmd_report(argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))

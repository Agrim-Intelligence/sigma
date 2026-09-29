#!/usr/bin/env python3
"""Phase-boundary console visibility (#1626): which model a phase is about to run on, and what
that phase cost once it finishes. Two phase-boundary verbs: `start <sdlc_dir> <goal> <phase>
--model M` prints a banner and stamps a per-goal marker; `end <sdlc_dir> <goal> <phase>
[--agent-id ID]` sums the phase's own transcript, prices it off the core's own rate card at
skills/agrim-loop/rates/anthropic_list_prices.csv (read as a plain CSV file — the core never imports
the private side, tests/test_import_boundary.py), prints a cost line, and records the ledger's
existing `phase` event with real tokens_in/tokens_out.

A third verb, `step <sdlc_dir> <goal> <phase> --num N [--total M] --brief B [--model M] [--title T]`,
announces one plan step WITHIN a phase `start` already announced. Whatever executes that phase's plan
calls it: the same session that called `start` when the phase runs inline, or the dispatched phase
subagent. It prints a markerless `STEP N[/M]` banner whose tier names its source (`step_tier`), and
WRITES NOTHING. Not the marker: `end` reads `ts_start` from the marker and trusts that `start` wrote
it, so a step that created or rewrote one could crash `end` or make it measure from the wrong start.
Not the ledger: a step is not a phase, and the ledger's phase vocabulary
(`ledger.PHASE_KINDS`, which `PHASE_TOKENS` below is pinned to) is closed.

HOST SUPPORT, PROBED NOT ASSUMED (see #1626's own issue body for the raw probe):
  - Claude Code: per-turn `model` + `usage` in ~/.claude/projects/<slug>/<session>/subagents/
    agent-<id>.jsonl (a dispatched phase subagent — the primary, most precise path) or in
    ~/.claude/projects/<slug>/<session>.jsonl (the caller's own top-level session, windowed by
    the phase marker's start time — the fallback for a phase that ran inline, with no dispatched
    subagent). Both are fully implemented AND verified end-to-end on this machine against real
    transcripts.
  - Codex: the caller's own ~/.codex/sessions/<date>/rollout-*<session-id>.jsonl carries
    `token_usage_record.payload.usage` request deltas (also duplicated in `event_msg` token_count
    events) and `turn_context.payload.model`. Select the exact session by Codex thread/session ID,
    never sum the whole rollout store. Codex cost remains unavailable because the bundled rate
    card contains Anthropic prices only; observed token counts and models are still reported.
  - Cursor / anything else: no known per-turn token store exists at all (probed: cursorDiskKV has
    zero token/usage keys). Resolves to the same tested `unavailable` path as "no source found".

Never fabricates a number: a rate-card gap, an unknown model, a partially-priced transcript, or a
missing transcript source all print an honest line — `cost: unavailable on this host (<reason>)`,
or a `(partial — N of M turns unpriced)` qualifier — never `$0.00`, never a silent omission.

PRICING IS TURN-LEVEL ALL-OR-NOTHING, the same aggregation rule a downstream cost reader uses
(any one of a turn's 7 rate-kinds failing to price poisons that WHOLE turn's cost to
absent, never a partial per-kind sum masquerading as complete — see `price_turn`'s own docstring).
An earlier draft of this module got this wrong (summed per-kind independently, so a single
confirmed-zero kind could mark an entirely unpriced transcript as costing $0.00) — caught by a
fresh, author-blind plan-review on 2026-08-25 before any of this shipped.

`end` IS A BLOCK B, CONSTRUCTED BY `render.py`; `start` IS NOT, AND THAT ASYMMETRY IS THE POINT
(#2112). `cmd_end` assembles the contract's closed Block B fact set (`end_facts`) and shells out to
`skills/agrim-loop/scripts/render.py` -- never imports it (design D-6). An `end` is a real boundary:
it has a phase it just left and a measured cost, which is what Block B is for, and hand-shaping it
was this repo's one in-code deviation from the contract these lines are the machine half of.

`cmd_start` prints its own two lines and always will. A start has NO from-phase -- this module is
called per PHASE, not per transition -- so `<from> → <to>` has nothing to put on its left, and the
draft that forced it in opened every start of every phase with `previous phase unknown (no verdict
at this boundary)`: 51 characters, most prominent position, no information, forever. A start
announces a state and carries no measurement; it is an announcement, not an event. `start_lines`
holds the argument, and the `⚪` stays with it.

DO NOT NARRATE THE NOT-KNOWING. That is the rule both halves come from, and it is #2100's `✅`
finding applied to words: an absence is not a fact worth a clause. `end_facts` therefore names the
phase the standing SDLC actually puts next rather than declaring an ignorance the same sentence
would resolve, and passes a null `next_action` rather than inventing one.

WHAT DID NOT CHANGE IS EVERY NUMBER AND EVERY DEGRADED WORDING: `end_measurements` is the single
source of all of them, and both `end_lines` (the fallback) and `end_facts` call it.

A REFUSAL FROM THE RENDERER DOES NOT LOSE THE BANNER. `render_block` returns None on any refusal,
missing interpreter or timeout, prints that refusal to stderr verbatim, and `cmd_end` falls back to
`end_lines` -- the pre-#2112 shape, unchanged. A measurement that never reaches the console is
worse than one in an unconstructed line, and the fallback is loud, not quiet.

THE BANNER IS THE MACHINE HALF OF `docs/output-contract.md` (#2100). That contract's own test is
"from a single glance the user knows which goal, which SDLC phase, and what is happening right now"
— and until #2100 these two lines printed a goal ID with no title, no phase token, and no elapsed
time, so learning that goal 1983 is "Resolve reviewer independence per host" meant going to look it
up. The contract's legible half (the model-written status blocks) is prose a model may skip; THIS
half fires on every host, from Sigma's own Python, which is why the legibility belongs here and
not in a Claude Code hook (Cursor has no hooks at all — AGENTS.md, "Host-agnostic or it does not
ship"). See `PHASE_TOKENS`, `resolve_title` and `end_lines` for the three parts:

  - the `P<n> NAME` phase token and the `next: <phase>` position come from `ledger.PHASE_KINDS`
    itself, sibling-pinned BOTH ways to that vocabulary and to the contract's own §2 table. Note
    what the banner does NOT say: `end` receives no verdict, so it prints no §2 liveness marker
    and names the next phase as a POSITION on the map, never as a promise about where this run is
    going -- see `MARK_START` for the measurement that made an earlier `✅` here a blocking defect;
  - the goal TITLE is resolved from what is ALREADY on this machine — an explicit `--title` the
    orchestrator already holds, else `mirror.py`'s local board-mirror snapshot, else the local goal
    file's frontmatter. NO network call and no subprocess is ever added to the phase-boundary path;
    `tests/test_phase_report.py::test_title_resolution_spawns_no_subprocess_and_opens_no_socket` is
    the executed control, and nothing resolvable prints the BARE id — never a blank, never a guess;
  - ELAPSED wall time comes from the marker `start` already stamps (`ts_start_epoch`), so `end`
    needs no clock source of its own; an unreadable marker prints `elapsed: unavailable`, the same
    manner as the pre-existing `cost: unavailable on this host (<reason>)` line.

MARKER LIFECYCLE. `start` stamps one marker per GOAL, so each `start` overwrites the last; `end`
and `step` only ever read it. Neither CONSUMES it — `end` stays re-runnable against the same marker
(see its `interval_ms` comment, and the Codex-ceiling retry that depends on it) — so the file is
unlinked by `loop.py`'s `_record()` when the goal reaches a terminal outcome, beside the
`agent_end()` cleanup it mirrors. #2658: before that, nothing pruned them at all (63 had piled up
by 2026-09-24, the oldest 30 days old) and a marker left open by an ABANDONED phase was then used
best-effort by the next `end` — windowing that phase's cost from the wrong instant. `end` now drops
a marker whose `phase` is not the one being ended and reports unmeasured instead (#2658). A marker
leaked by a goal that never reached `_record` was NOT inert even after that fix, though: a SAME-
phase marker left by a crashed writer was still trusted at any age by whichever process next called
`end` for that phase (#2667). `start` now stamps `--pid "$PPID"`, plus `CODEX_THREAD_ID` and
`CLAUDE_CODE_SESSION_ID` read automatically, and `end` keeps the model gates but drops the start
time unless that identity matches — at any age when it does, because a marker has no heartbeat and
age alone would resurrect #1391. A dispatched Claude subagent's or a validated Codex child's own
tokens stay exact regardless, since neither ever needed the marker's window. The fallback, when
either side carries no pid, is a dead writer (`ledger.pid_alive`) or the lease TTL; what that
fallback still lets through is listed at `stale_marker_reason`'s own docstring.
"""
import calendar
import csv
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time

try:                    # portable output: matches every other script in this repo
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

_HERE = pathlib.Path(__file__).resolve().parent
#: The core's OWN rate card, beside this skill. A downstream reader ships a separate copy of the same data
#: and the two are independent on purpose (#2573/S1-G9): server billing may diverge from list
#: price, so neither side is generated from the other and nothing keeps them equal.
DEFAULT_RATES_CSV = _HERE.parent / "rates" / "anthropic_list_prices.csv"


def _load(name):
    """Sibling-module loader — the same `importlib.util.spec_from_file_location` idiom every
    script in this directory uses (`loop.py`'s own `_load()`, `actionlog.py`'s own `_load()`).
    Never a package import: `tests/test_import_boundary.py` explicitly exempts this pattern (it
    produces no `Import`/`ImportFrom` AST node), and it is how this module reaches `ledger.py`/
    `state.py`/`work.py` without ever importing the private side or importing a sibling script as
    a package."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- pricing


#: rate_kind -> (path into a turn's `usage` dict, per_request). The same per-kind unpivot a
#: downstream cost reader uses, exactly (same 7 kinds, same semantics) but
#: reads a live transcript's `message.usage` shape instead of a downstream store's ingested columns.
RATE_KIND_USAGE = {
    "input": (("input_tokens",), False),
    "output": (("output_tokens",), False),
    "cache_read": (("cache_read_input_tokens",), False),
    "cache_write_5m": (("cache_creation", "ephemeral_5m_input_tokens"), False),
    "cache_write_1h": (("cache_creation", "ephemeral_1h_input_tokens"), False),
    "web_search": (("server_tool_use", "web_search_requests"), True),
    "web_fetch": (("server_tool_use", "web_fetch_requests"), True),
}


def norm_ts(iso_ts):
    """'2026-08-24T18:16:45.403Z' (or without millis) -> '2026-08-24 18:16:45' — matches
    anthropic_list_prices.csv's 'YYYY-MM-DD HH:MM:SS' shape so both can be compared lexically.
    ISO-8601-with-zero-padded-fixed-width fields sort identically to chronological order, so no
    datetime parsing is needed."""
    s = iso_ts.strip()
    if s.endswith("Z"):
        s = s[:-1]
    s = s.replace("T", " ")
    if "." in s:
        s = s.split(".", 1)[0]
    return s


def load_rate_rows(csv_path=None):
    """Read the rate card as plain CSV (the core never imports the private side — the CSV is a data
    file, not a package). Empty usd_per_mtok/usd_per_request/effective_to become None, never empty string or
    0.0 — an empty rate column means "not priced this way", not "priced at zero".

    An ABSENT card returns `[]` rather than raising: `collect_phase_usage` already promises "Never
    raises", and a `FileNotFoundError` here cost the caller the whole of Block B *and* the phase's
    ledger event with its measured tokens (#2573/S1-G9). `tests/test_phase_report.py`'s
    `_assert_rate_card_shape` is what still notices a card that failed to ship."""
    path = pathlib.Path(csv_path) if csv_path is not None else DEFAULT_RATES_CSV
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            rows.append({
                "model": raw["model"],
                "rate_kind": raw["rate_kind"],
                "usd_per_mtok": float(raw["usd_per_mtok"]) if raw.get("usd_per_mtok") else None,
                "usd_per_request": float(raw["usd_per_request"]) if raw.get("usd_per_request") else None,
                "effective_from": raw["effective_from"],
                "effective_to": raw["effective_to"] or None,
            })
    return rows


def select_rate(rows, model, rate_kind, ts_norm):
    """The same rate-selection rule a downstream cost reader uses: exact (model, rate_kind) match,
    effective_from <= ts_norm AND (effective_to IS NULL OR ts_norm < effective_to) — deliberately
    NOT a literal BETWEEN (that matches zero rows once effective_to is open-ended, the common
    case) — then the row with the LATEST effective_from wins when more than one vintage covers the
    same timestamp. Returns None when no row covers this (model, rate_kind, ts) at all — an honest
    "no rate", never a guess."""
    covering = [
        r for r in rows
        if r["model"] == model and r["rate_kind"] == rate_kind
        and r["effective_from"] <= ts_norm
        and (r["effective_to"] is None or ts_norm < r["effective_to"])
    ]
    if not covering:
        return None
    return max(covering, key=lambda r: r["effective_from"])


def price_units(units, rate, per_request):
    """The same per-kind pricing rule a downstream cost reader uses, verbatim semantics:
    units == 0 -> 0.0 (confirmed zero activity costs zero, regardless of rate coverage);
    units is None -> None (never observed, absent);
    rate is None, or both rate columns None -> None (no coverage, absent — never guessed);
    per_request -> units * usd_per_request (no /1e6 — issue #783's split, this repo's own fix for
    a request-count being miscalibrated into a per-Mtok column);
    else -> units / 1_000_000 * usd_per_mtok."""
    if units == 0:
        return 0.0
    if units is None or rate is None:
        return None
    if per_request:
        if rate["usd_per_request"] is None:
            return None
        return units * rate["usd_per_request"]
    if rate["usd_per_mtok"] is None:
        return None
    return units / 1_000_000.0 * rate["usd_per_mtok"]


#: The anchor for "1 budget-token" = "1 cost-equivalent input token at claude-sonnet-5's list
#: input rate, at the time the phase ran." Any ONE stable reference works; sonnet-5 is chosen
#: because it is this repo's own default/median-cost tier (agrim-loop's `model_predict.py`
#: resolves to it absent a stronger signal), not because it is privileged in any other sense.
#: Fixed and SINGLE across every phase and every model actually used, so budget-tokens stay
#: comparable to each other regardless of which real model did the work — an opus-5 phase that
#: burns $12 counts the SAME number of budget-tokens as a sonnet-5 phase that burns $12, which is
#: the whole point of "cost-equivalent": the ceiling tracks real spend, not raw token volume.
REFERENCE_MODEL = "claude-sonnet-5"
REFERENCE_RATE_KIND = "input"


def cost_equivalent_tokens(cost_usd, rate_rows, ts_norm):
    """cost_usd -> a token count anchored to REFERENCE_MODEL's own `input` rate AT ts_norm (so a
    rate-card cutover, e.g. sonnet-5's 2026-09-01 intro->standard price change, is honored rather
    than silently pinned to whichever vintage happened to be current when this code was written).
    Returns None — never a guess — when cost_usd is None (nothing priced) or the reference rate
    itself has no coverage at ts_norm (a gap in the rate card, not this phase's fault). The caller
    (`cmd_end`) must skip incrementing the budget counter, not fabricate a number, on None."""
    if cost_usd is None:
        return None
    rate = select_rate(rate_rows, REFERENCE_MODEL, REFERENCE_RATE_KIND, ts_norm)
    if rate is None or rate.get("usd_per_mtok") is None:
        return None
    return round(cost_usd / (rate["usd_per_mtok"] / 1_000_000.0))


# --------------------------------------------------------------------------- transcript parsing

_SYNTHETIC_MODEL = "<synthetic>"


#: THE shared safe-accessor sweep (#2531, third fix round). Two prior rounds each patched exactly
#: the one unguarded-JSON-type crash a reviewer's repro handed them -- a non-string `timestamp` at
#: one call site, then a second `timestamp` site, then a non-string `description`, then a
#: non-hashable `parentAgentId` -- and each time an independent re-review found MORE of the
#: identical bug class elsewhere in the same module, because every fix was a spot-patch (one
#: hand-rolled `isinstance(..., str)`/`isinstance(..., dict)` check, at one call site) rather than
#: a structural guarantee. `json.loads()` of a transcript line or a `.meta.json` file can legally
#: put ANY JSON type behind ANY key -- nothing upstream enforces the shape a field's NAME implies
#: -- so a wrong-typed value reaching a `str`-only op (`.strip()`, a regex `.search()`), a `dict`-
#: only op (`.get()`), or a plain Python `dict`/`set` key (which requires HASHABLE, not just a
#: particular type) is exactly this bug class, regardless of which specific field or call site it
#: shows up at next. `as_str`/`get_str`/`get_dict`/`get_list` below are the fix: ONE correct,
#: never-raising implementation per shape, reused everywhere that shape is needed (this module's
#: own `iter_assistant_turns`, and orchestrator_context_report.py via this module's existing
#: `_load()` sibling-reuse convention -- see `usage_value`, `price_turn`, `norm_ts`, etc. for the
#: precedent of a public, non-underscore function being this codebase's established cross-file
#: sharing surface, as opposed to an underscore-prefixed one like `_flags`, which this directory's
#: own convention DUPLICATES per sibling script instead of sharing). Modeled directly on this
#: module's own pre-existing `usage_value` (walk a container, coerce, `None` on anything that
#: doesn't fit, never raise) -- this is that same idea widened from "walk a dict path to an int"
#: to "read one string/dict/list-typed field safely".
def as_str(value):
    """`value` if it is a non-empty `str`, else `None`. The single-VALUE counterpart to `get_str`
    below -- for something already pulled out of its container (a loop variable, a generator
    element) rather than a fresh `dict`-plus-`key` pair. Checks TYPE and truthiness TOGETHER, not
    truthiness alone -- matches this module's own pre-existing `goal_number`-style guard
    (orchestrator_context_report.py, before this round centralized it here): an empty string is
    exactly as unusable as a missing value at every real call site (a blank message id, timestamp,
    or tool-use id is never a valid one), and folding both into one `None` keeps every caller's
    downstream check to a single `if x:`."""
    return value if isinstance(value, str) and value else None


def get_str(d, key):
    """`d.get(key)` if it is a non-empty `str`, else `None` -- including when `d` itself is not a
    `dict`. Never raises, matching `usage_value`'s own established style."""
    return as_str(d.get(key)) if isinstance(d, dict) else None


def get_dict(d, key):
    """`d.get(key)` if it is a `dict`, else `None` -- including when `d` itself is not a `dict`.
    Same shared-accessor reasoning as `get_str`, for a nested-JSON-object field (`message`)."""
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    return v if isinstance(v, dict) else None


def get_list(d, key):
    """`d.get(key)` if it is a `list`, else `None` -- including when `d` itself is not a `dict`.
    Same shared-accessor reasoning as `get_str`/`get_dict`, for a JSON-array field (`message.
    content`, the list of content blocks)."""
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    return v if isinstance(v, list) else None


def usage_value(usage, path):
    """Walk a nested dict by `path`; return int(value), or None if a key is missing/None, the value
    is not coercible, is non-finite (inf/nan; `json.loads` accepts `Infinity`/`NaN`/`1e400`), or
    negative — an unpriceable count, never a silent 0 or a negative cost (#2558). Never raises on a
    malformed usage dict — a phase's cost is best-effort, not load-bearing."""
    node = usage
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if node is None:
        return None
    try:
        value = int(node)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value >= 0 else None


def _merge_usage_into(existing, new):
    """Deep-merge `new` (a raw `message.usage` dict) into `existing` in place: a leaf overwrites
    only when the new value is not `None` ("latest non-null value for each usage counter"); a
    nested dict (e.g. `cache_creation`) merges key-by-key instead of replacing the whole sub-dict,
    so a later snapshot that only reports `ephemeral_1h_input_tokens` cannot erase an earlier
    snapshot's `ephemeral_5m_input_tokens`. Generic over whatever keys a real usage object
    carries — not limited to RATE_KIND_USAGE's 7 known paths — so a consumer reading a field
    RATE_KIND_USAGE doesn't track (orchestrator_context_report.py's `peak_context`/`volume_totals`
    read the flat `cache_creation_input_tokens` legacy field, never one of RATE_KIND_USAGE's own
    nested paths) still sees it merged correctly."""
    for key, value in new.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(existing.get(key), dict):
            _merge_usage_into(existing[key], value)
        else:
            existing[key] = value


def iter_assistant_turns(path):
    """Yield {'ts': norm_ts(...), 'model': str, 'message_id': str|None, 'usage': dict} for each
    RAW real assistant turn in a Claude Code transcript JSONL file — one entry PER LINE, no
    grouping by `message.id` (a caller that needs turns grouped dedupes its own way: see
    `_dedup_turns_by_message_id` below for `price_transcript`'s own use, and
    orchestrator_context_report.py's `dedup_calls` for its different, independently-justified
    "last full snapshot wins" semantics — the two are deliberately not the same merge rule, so
    this function stays a single, shared, undeduped source both build on rather than picking a
    merge rule that would only suit one of them). A line counts only when type=='assistant' AND
    message.role=='assistant' (the same assistant-line filter a downstream transcript
    reader uses) — `<synthetic>`-model lines (locally-synthesized error
    placeholders) are excluded, matching that same module. A malformed line is skipped, never
    fatal — one bad line must not blind the whole phase's accounting.

    'message_id' (#2531: orchestrator_context_report.py's own `iter_raw_assistant_calls` is a thin
    pass-through over this function and needs it) is sanitized via `get_str`, so a non-hashable raw
    `message.id` (a JSON list/dict) never reaches dict-key use in a downstream `dedup_calls`/
    `_dedup_turns_by_message_id` call: it becomes `None`, and a `None` id is never grouped with
    another line — each stays its own turn.

    `ts`/`model` go through `get_str`/`get_dict`, not a bare `.get()` plus a truthiness check:
    `timestamp` was previously only truthiness-guarded (`if not ts: continue`), so a TRUTHY
    non-string value (a JSON number, bool, or non-empty list/dict) reached `norm_ts`'s `.strip()`
    and crashed with AttributeError — reachable from this module's own `price_transcript`/`cmd_end`
    path and, additively, from orchestrator_context_report.py's `iter_raw_assistant_calls`."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "assistant":
                    continue
                message = get_dict(obj, "message")
                if message is None or message.get("role") != "assistant":
                    continue
                model = get_str(message, "model")
                if not model or model == _SYNTHETIC_MODEL:
                    continue
                ts = get_str(obj, "timestamp")
                if not ts:
                    continue
                yield {"ts": norm_ts(ts), "model": model, "message_id": get_str(message, "id"),
                       "usage": message.get("usage") or {}}
    except OSError:
        return


def _dedup_turns_by_message_id(turns):
    """Group raw `iter_assistant_turns` entries sharing a real string `message_id` into one turn
    per id — Claude writes progressive usage snapshots under one id, and summing every one of them
    independently would double/triple-count the same turn's cost (#2515). Per group: earliest
    timestamp, last model, latest non-null value for each usage counter via `_merge_usage_into`
    (never a whole-snapshot replacement — an earlier snapshot's field absent from the last one,
    e.g. `server_tool_use`, must survive; see `test_claude_progressive_usage_lines_count_once_by_
    message_id`). A missing/non-string id is never grouped with another line, same rule
    orchestrator_context_report.py's `dedup_calls` uses ("None never grouped") — each such turn
    passes through unchanged, on its own."""
    groups = {}
    order = []
    no_id_seq = 0
    for turn in turns:
        mid = turn.get("message_id")
        if mid is None:
            no_id_seq += 1
            key = ("__no_id__", no_id_seq)
        else:
            key = mid
        entry = groups.get(key)
        if entry is None:
            entry = {"ts": turn["ts"], "model": turn["model"], "message_id": mid, "usage": {}}
            groups[key] = entry
            order.append(key)
        else:
            entry["ts"] = min(entry["ts"], turn["ts"])
            entry["model"] = turn["model"]
        _merge_usage_into(entry["usage"], turn["usage"])
    return [groups[key] for key in order]


def price_turn(turn, rates):
    """Price one turn across all 7 rate_kinds. Returns None the MOMENT ANY kind fails to price —
    this is the same REAL aggregation rule a downstream cost reader uses: if ANY kind's cost is
    unknown the turn's cost is unknown, otherwise it is the sum — an all-or-nothing poison per turn, not a per-kind NULL-guard-then-SUM. (Plan-review, 2026-08-25,
    BLOCKING 1: an earlier draft summed every kind independently and let a single confirmed-zero
    kind mark the WHOLE turn "priced" even when the model had zero real rate-card coverage —
    fabricating a $0.00 for a genuinely unpriceable transcript. This is the fix.)

    The two per_request kinds (`web_search`, `web_fetch`) are OPTIONAL tool invocations: a turn
    that never called that tool has NO `server_tool_use` key in its usage dict at all (confirmed
    against a real transcript on this machine — the key is absent, not zero, on an ordinary turn).
    Treating "tool never invoked" as a confirmed zero (not an unknown/poisoning gap) is what
    "never observed vs confirmed zero" actually means in practice for a request-count kind — so a
    MISSING per_request usage path defaults to `0` here, never `None`. The 5 token-based kinds
    carry no such ambiguity (input/output/cache_* are always populated on a real Anthropic usage
    object) and keep the strict None-on-missing rule."""
    model = turn["model"]
    ts = turn["ts"]
    usage = turn["usage"]
    total = 0.0
    for rate_kind, (path_tuple, per_request) in RATE_KIND_USAGE.items():
        units = usage_value(usage, path_tuple)
        if units is None and per_request:
            units = 0                      # tool never invoked this turn -- a confirmed zero
        rate = select_rate(rates, model, rate_kind, ts)
        priced = price_units(units, rate, per_request)
        if priced is None:
            return None
        total += priced
    return total


def price_transcript(path, rates=None, since_ts=None):
    """Sum every fully-priced turn's cost across every real assistant turn in `path` (each turn
    priced by ITS OWN observed model, never the predicted tier, and its OWN timestamp, so a
    transcript straddling a price-vintage cutover is priced correctly on each side). Returns None
    if the file is missing, empty, or has zero real assistant turns AT OR AFTER `since_ts` (when
    given) — the caller's signal to try the next host or print `unavailable`.

    `since_ts` (an ISO-8601 string, same shape `norm_ts` accepts) restricts to turns at or after
    that point — used for the INLINE-phase path, where `path` is the orchestrator's own top-level
    session transcript spanning the whole goal, not a phase-dedicated subagent file, and the phase
    marker's own `ts_start` is the lower bound that isolates this one phase's turns from every
    other phase already run in the same session.

    `cost_usd` sums ONLY fully-priced turns (see `price_turn`) and is `None` only when EVERY turn
    failed to price (the model is entirely unknown to the rate card). `unpriced_turns` reports how
    many turns were excluded from that sum, so a caller can print an honest PARTIAL total rather
    than a total that silently looks complete — never omit that count once it is nonzero."""
    rate_rows = rates if rates is not None else load_rate_rows()
    since_norm = norm_ts(since_ts) if since_ts else None
    tokens_in = 0
    tokens_out = 0
    models = []
    turns = 0
    priced_turns = 0
    unpriced_turns = 0
    cost_usd = 0.0
    first_turn_ts = None
    raw_turns = (t for t in iter_assistant_turns(path)
                if since_norm is None or t["ts"] >= since_norm)
    for turn in _dedup_turns_by_message_id(raw_turns):
        turns += 1
        if first_turn_ts is None:
            first_turn_ts = turn["ts"]
        model = turn["model"]
        if model not in models:
            models.append(model)
        usage = turn["usage"]
        tokens_in += usage_value(usage, ("input_tokens",)) or 0
        tokens_out += usage_value(usage, ("output_tokens",)) or 0
        turn_cost = price_turn(turn, rate_rows)
        if turn_cost is None:
            unpriced_turns += 1
        else:
            cost_usd += turn_cost
            priced_turns += 1
    if turns == 0:
        return None
    priced_cost_usd = round(cost_usd, 6) if priced_turns > 0 else None
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": priced_cost_usd,
        "cost_equivalent_tokens": (cost_equivalent_tokens(priced_cost_usd, rate_rows, first_turn_ts)
                                   if unpriced_turns == 0 else None),
        "unpriced_turns": unpriced_turns,
        "turns": turns,
        "models": models,
    }


# --------------------------------------------------------------------------- host discovery


def _home(home=None):
    return pathlib.Path(home) if home is not None else pathlib.Path.home()


def _unsafe_id_reason(value):
    """Shared path-component validator for `session_id`/`agent_id` before either reaches a
    filesystem join. Reuses `state.unsafe_goal_reason` — despite its name, that function's own
    docstring is explicit that it is "THE shared validator for every .../state/.../<stem(goal)>...
    path across this plugin", the same generic (reject '/', '\\', ':', '..') check `loop.py`'s
    `_unsafe_thread_reason` already applies to a different untrusted value (`thread`) for
    identical reasons; there is no goal-specific logic in it to NOT fit `session_id`/`agent_id`.

    PR review, 2026-08-25 (BLOCKING, caught post-PR, not by either earlier review): neither
    `find_claude_session_dir`, `find_claude_agent_transcript`, nor `find_claude_main_transcript`
    validated `session_id`/`agent_id` before joining them into a path with pathlib's `/` operator
    — which RE-PARSES a string argument for separator characters, so a value containing `/` or
    `..` does not stay one path component, it becomes additional segments capable of escaping
    `~/.claude/projects/` entirely. Reproduced by execution: a crafted `CLAUDE_CODE_SESSION_ID`
    made `phase_report.py end` read a file completely outside that tree and write its fabricated
    token counts into the goal's real `phase` ledger event. `marker_path` (below) already guards
    `goal` this exact way — this closes the same gap for the two host-discovery parameters, the
    third and fourth call sites of the identical bug shape AGENTS.md's own standing rule on
    untrusted values reaching a filesystem path already names two prior instances of (#486/#487)."""
    state = _load("state")
    return state.unsafe_goal_reason(value)


def find_claude_session_dir(session_id, home=None):
    """Search ~/.claude/projects/*/<session_id> — deliberately NOT re-deriving Claude Code's own
    cwd-to-slug transform (replace '/' and '.' with '-'): the session id is already a globally
    unique UUID, so searching for a directory named exactly that is simpler and cannot drift if
    the slug algorithm ever changes."""
    if _unsafe_id_reason(session_id):
        return None
    projects = _home(home) / ".claude" / "projects"
    if not projects.is_dir():
        return None
    for proj_dir in projects.iterdir():
        candidate = proj_dir / session_id
        if candidate.is_dir():
            return candidate
    return None


def find_claude_agent_transcript(session_id, agent_id, home=None):
    if _unsafe_id_reason(agent_id):
        return None
    session_dir = find_claude_session_dir(session_id, home=home)   # re-validates session_id too
    if session_dir is None:
        return None
    candidate = session_dir / "subagents" / f"agent-{agent_id}.jsonl"
    return candidate if candidate.is_file() else None


def find_claude_main_transcript(session_id, home=None):
    """The orchestrator's own top-level transcript — a sibling FILE of the session directory
    `find_claude_session_dir` returns: `<slug>/<session_id>.jsonl` next to `<slug>/<session_id>/`.
    Used for the INLINE-phase fallback (no dispatched subagent, so no agent-id to look up
    directly) — verified against this machine's real `~/.claude/projects/` layout during
    plan-review, 2026-08-25."""
    if _unsafe_id_reason(session_id):
        return None
    projects = _home(home) / ".claude" / "projects"
    if not projects.is_dir():
        return None
    for proj_dir in projects.iterdir():
        candidate = proj_dir / f"{session_id}.jsonl"
        if candidate.is_file():
            return candidate
    return None


def _codex_rollout(session_id, home=None):
    """Find one exact rollout, using a UUIDv7's creation date for the normal bounded lookup.

    Codex places rollouts under a local-calendar date, which can differ by one day from the UUID's
    UTC date. Only UUIDv7 IDs permit this bounded lookup; older ID forms return unavailable rather
    than scan an unbounded session history. Ambiguous restored copies are refused.
    """
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return None
    sessions = _home(home) / ".codex" / "sessions"
    if not sessions.is_dir():
        return None
    pattern = f"rollout-*{session_id}.jsonl"
    matches = []
    uuid7 = re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", session_id)
    if not uuid7:
        return None
    millis = _uuid7_millis(session_id)
    try:
        day = datetime.datetime.fromtimestamp(millis / 1000, datetime.timezone.utc).date()
    except (OverflowError, ValueError):
        return None
    for offset in (-1, 0, 1):
        local_day = day + datetime.timedelta(days=offset)
        folder = sessions / local_day.strftime("%Y/%m/%d")
        matches.extend(path for path in folder.glob(pattern)
                       if path.stem == f"rollout-{session_id}"
                       or path.stem.endswith(f"-{session_id}"))
    return matches[0] if len(matches) == 1 else None


_CODEX_AGENT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


def _codex_agent_id(value):
    """A canonical rollout UUID, or None. Task labels and parent session IDs are not agent IDs."""
    if not isinstance(value, str) or not _CODEX_AGENT_ID.fullmatch(value.lower()):
        return None
    return value.lower()


def _codex_usage_pair(usage):
    """Return a real request's token pair, never a cumulative turn or thread total."""
    if not isinstance(usage, dict):
        return None
    tin, tout = usage.get("input_tokens"), usage.get("output_tokens")
    if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
               for value in (tin, tout)):
        return None
    return tin, tout


def find_codex_token_totals(since_ts, home=None, session_id=None):
    """Count one Codex rollout's request deltas at or after the phase start.

    Modern rollouts write both `token_usage_record.payload.usage` and `event_msg` token_count for
    each request. Choose one format, but refuse mismatched paired streams rather than silently
    undercount a partial write. Legacy top-level `token_count` remains a third choice.
    `turn_token_usage`, `thread_token_usage`, and
    `total_token_usage` are cumulative and must never be summed. A request without an observed
    model is counted in usage but flagged as `unknown_model_turns` for model verification.
    """
    rollout = _codex_rollout(session_id, home=home)
    if rollout is None:
        return None
    since_norm = norm_ts(since_ts)
    streams = {"record": [0, 0, 0, [], 0], "event": [0, 0, 0, [], 0],
               "legacy": [0, 0, 0, [], 0]}
    current_model = None
    previous_event = None
    try:
        with open(rollout, "r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                try:
                    obj = json.loads(raw_line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                if obj.get("type") == "turn_context":
                    model = payload.get("model")
                    if isinstance(model, str) and model:
                        current_model = model
                    continue
                ts = obj.get("timestamp")
                if not isinstance(ts, str) or norm_ts(ts) < since_norm:
                    continue
                kind = None
                usage = None
                if obj.get("type") == "token_usage_record":
                    kind, usage = "record", payload.get("usage")
                elif obj.get("type") == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info") or {}
                    if isinstance(info, dict):
                        kind, usage = "event", info.get("last_token_usage")
                elif obj.get("type") == "token_count":
                    kind, usage = "legacy", payload
                if kind is None:
                    continue
                pair = _codex_usage_pair(usage)
                if pair is None:
                    continue
                if kind == "event":
                    # Codex may re-emit the last request while the cumulative total is
                    # unchanged. The same request-sized pair with an advanced total is a
                    # new request, so only discard an identical pair AND total.
                    cumulative = _codex_usage_pair(info.get("total_token_usage"))
                    fingerprint = (pair, cumulative) if cumulative is not None else None
                    if fingerprint is not None and fingerprint == previous_event:
                        continue
                    previous_event = fingerprint
                stream = streams[kind]
                stream[0] += pair[0]
                stream[1] += pair[1]
                stream[2] += 1
                if current_model and current_model not in stream[3]:
                    stream[3].append(current_model)
                if not current_model:
                    stream[4] += 1
    except OSError:
        return None
    if streams["record"][2] and streams["event"][2] and streams["record"][:3] != streams["event"][:3]:
        return None
    for kind in ("record", "event", "legacy"):
        tin, tout, count, models, unknown_models = streams[kind]
        if count:
            totals = {"tokens_in": tin, "tokens_out": tout, "models": models}
            if unknown_models:
                totals["unknown_model_turns"] = unknown_models
            return totals
    return None


def collect_phase_usage(session_id, agent_id, since_ts, home=None, rates=None,
                        codex_session_id=None):
    """Always returns a dict with a `source` key in {"claude-code", "claude-code-inline", "codex",
    "unavailable"}. Tries, in order:
    (1) `agent_id` given — the EXACT, dispatched-subagent Claude Code transcript. The fully
        verified, most-precise path.
    (2) `agent_id` absent — the INLINE fallback: the caller's own top-level session transcript,
        windowed to turns at/after `since_ts` (the phase marker's `ts_start`). Reported as
        `claude-code-inline` so a reader can tell which precision priced the result — both write
        the same real `tokens_in`/`tokens_out` to the ledger either way.
    (3) Codex's exact agent or current thread/session rollout — observed tokens and model, with
        cost unavailable because the bundled rate card has no OpenAI rates.
    (4) unavailable, naming the specific reason — a nested caller with no top-level transcript of
        its own, or no known transcript source at all (the Cursor case).
    Never raises — a phase's cost line is best-effort and must never break the caller's run."""
    if agent_id:
        transcript = find_claude_agent_transcript(session_id, agent_id, home=home)
        if transcript is not None:
            priced = price_transcript(transcript, rates=rates)
            if priced is not None:
                return {"source": "claude-code", **priced}
    else:
        main_transcript = find_claude_main_transcript(session_id, home=home)
        if main_transcript is not None:
            priced = price_transcript(main_transcript, rates=rates, since_ts=since_ts)
            if priced is not None:
                return {"source": "claude-code-inline", **priced}
    # A live Claude session takes precedence even when a shell inherited unrelated Codex IDs.
    # Missing Claude usage must degrade honestly, never borrow a Codex rollout.
    codex_target = (agent_id or codex_session_id) if not session_id else None
    codex_totals = find_codex_token_totals(since_ts, home=home, session_id=codex_target)
    if codex_totals is not None:
        return {"source": "codex", "cost_usd": None, **codex_totals}
    if codex_target:
        return {"source": "unavailable", "reason": "Codex rollout missing, unsupported, or "
                "incomplete; phase tokens cannot be attributed safely"}
    if not agent_id:
        return {"source": "unavailable",
                "reason": "phase ran inline within a nested session; no top-level transcript to "
                          "window (dispatch phases as subagents, or run as a top-level session, "
                          "for cost attribution)"}
    return {"source": "unavailable",
            "reason": f"no per-turn usage source found for agent {agent_id!r} "
                      f"(checked Claude Code transcripts and Codex sessions)"}


# --------------------------------------------------------------------------- legible banner (#2100)


#: `ledger.PHASE_KINDS`, in ITS order, paired with the `P<n> NAME` phase token
#: `docs/output-contract.md` §2 defines. SIBLING PIN, checked BOTH ways so neither side can drift:
#:   - `test_phase_tokens_cover_ledger_phase_kinds_in_order` pins the KEYS to `ledger.PHASE_KINDS`
#:     (same members, same order) -- an eighth phase kind cannot land without a token here;
#:   - `test_phase_tokens_match_the_output_contract_table` parses §2's own markdown table out of
#:     `docs/output-contract.md` and pins the VALUES to it -- so the machine-printed banner and the
#:     contract a model is told to follow cannot say different things about the same phase.
#: The ORDER is load-bearing twice over: it IS the SDLC flow, so `next_phase_token` is simply "the
#: one after this", with no second list to keep in step.
PHASE_TOKENS = (
    ("goal", "P1 GOAL"),
    ("research", "P2 RESEARCH"),
    ("plan", "P3 PLAN"),
    ("plan_review", "P4 PLAN-REVIEW"),
    ("implement", "P5 IMPLEMENT"),
    ("review", "P6 REVIEW"),
    ("retro", "P7 RETRO"),
)

#: One coloured square per phase, keyed like `PHASE_TOKENS`, printed before a phase token so a stream
#: of banners scans by colour. Squares, never circles: §2's circles are liveness markers with
#: meanings, and a badge names a phase, never a state -- so no badge is a marker, and none is red,
#: which would read as `🔴` blocked. Pinned both ways to the `Badge` column of
#: `docs/output-contract.md` §2 by `test_phase_badges_match_the_output_contract_table`.
PHASE_BADGES = {
    "goal": "⬜",
    "research": "🟨",
    "plan": "🟧",
    "plan_review": "🟫",
    "implement": "🟦",
    "review": "🟪",
    "retro": "🟩",
}

#: `⚪` (§2 "queued -- claimed, not started") is the ONLY liveness marker this module prints, and
#: only on `start`, where SKILL.md fires it BEFORE the phase's subagent is dispatched. "Starting"
#: claims nothing, so it cannot be wrong. The squares `PHASE_BADGES` puts in front of `PHASE END`,
#: `STEP` and every phase token are not markers: they name a phase and claim nothing about it.
#:
#: `PHASE END` DELIBERATELY CARRIES NO MARKER (PR review of #2102, BLOCKING). An earlier draft of
#: this change printed `✅` there, and that was a success claim this module is structurally unable
#: to back: `cmd_end` takes NO verdict argument -- SKILL.md calls it whenever the subagent returns,
#: pass or block, and the console print is unconditional -- so a module constant would have stamped
#: "done" on every blocked phase. Measured against ~1,564 live gate facts, a green check on
#: P4 PLAN-REVIEW would have been wrong 88% of the time (6 pass / 16 block / 29 warn), and about a
#: quarter of the time across all verdict-bearing gates. The pre-#2100 line made no outcome claim
#: at all; making it claim success on the majority of plan-reviews would have inverted this goal's
#: whole point, and is the "a green status file is not freshness" defect class AGENTS.md's
#: RELIABILITY property names outright. The words `PHASE END` already carry the meaning.
#:
#: A `--verdict` flag was considered and rejected: it pushes a new argument onto every call site in
#: two SKILL.md files for a boundary that genuinely does not know the outcome, and a flag every
#: caller can forget defaults straight back to the same lie.
#: Pinned by `test_phase_end_never_claims_an_outcome_it_cannot_know`.
MARK_START = "⚪"

#: A title is a banner field, not a document: one line, and capped so a 200-character issue title
#: cannot wrap the console and bury the phase token underneath it.
TITLE_MAX = 88

#: `mirror.MIRROR_REL`, duplicated rather than imported -- the sibling scripts in this directory
#: deliberately do not import each other's constants (see `_load`'s own docstring), and importing
#: `mirror` for one string would drag `scrub` + `blocker_scan` onto the phase-boundary path.
#: `tests/test_phase_report.py::test_mirror_rel_matches_mirror_module` pins the two together.
MIRROR_REL = "state/board-mirror.ndjson"

#: The two claims `render.TIER_LABEL_PREDICTED` / `render.TIER_LABEL_AGENT_SOURCED` make, each in
#: ITS OWN WORD rather than in full. Both must stay DISTINGUISHABLE and honest (D-1: the tier is
#: predicted, not observed; D-2: `model_choice` sits in `actionlog.AGENT_KINDS`, so an agent wrote
#: it and no code derived or verified it) -- #2111 requires that, and requires nothing about
#: length. Printing both in full put 53 characters of caveat on a line whose whole job is to be
#: scanned (#2112, review), so each is carried by the shortest token that still makes its own
#: claim: `predicted` sits in the field NAME, `agent-set` in the parenthesis after the value.
#:
#: These are not a second vocabulary. Each is the LEADING TOKEN of `render.py`'s own label, which
#: `test_the_short_tier_labels_are_the_renderers_own_leading_tokens` pins in both directions -- so
#: the short form cannot drift into saying something the long form does not, and a reword on either
#: side goes red. `render.check_tier_labels` still refuses at runtime for Block A's structured
#: field; this line is free text, which is why the pin plus
#: `tests/test_phase_report.py::test_the_start_line_keeps_both_tier_claims_distinct` stands in.
TIER_LABEL_PREDICTED = "predicted"
TIER_LABEL_AGENT_SOURCED = "agent-set"

#: `render.PHASE_LAST` -- the enumerated `to_phase` for `P7 RETRO`, which has no successor.
#: Duplicated and pinned like the rest. `render.PHASE_UNKNOWN` is deliberately NOT here: this
#: module never emits it (see `end_facts`).
PHASE_LAST = "last"

#: `render.py`, invoked as a SUBPROCESS (design D-6, #2111): `log.py` must not import
#: `actionlog.py` and `tests/test_import_boundary.py` forbids the private side importing `skills/`;
#: shelling out satisfies both, and it is how SKILL.md already invokes this very module. A sibling
#: FILE, resolved from `__file__`, so a copied `skills/` tree still finds it.
RENDER_SCRIPT = _HERE / "render.py"

#: A banner must never break a run, and it must never be the thing that hangs one either. The
#: renderer is a stdlib-only, no-IO script that returns in milliseconds; anything past this is a
#: sick host, and the fallback banner is better than a stalled phase boundary.
RENDER_TIMEOUT_S = 20

#: #233: the board colour each phase's option gets when the loop CREATES the Phase field -- the
#: nearest GitHub single-select colour to its `PHASE_BADGES` square. Never RED (a badge is not
#: `🔴`), so 🟫 plan-review, which GitHub has no brown for, is PINK. Keyed like `PHASE_TOKENS`.
PHASE_BOARD_COLORS = {"goal": "GRAY", "research": "YELLOW", "plan": "ORANGE",
                      "plan_review": "PINK", "implement": "BLUE", "review": "PURPLE",
                      "retro": "GREEN"}

#: #233: the board write at a phase start is bounded so a sick `gh` can never hang a phase: each
#: call times out, and the whole write stops issuing calls past its budget (fail-open either way).
BOARD_CALL_TIMEOUT_S = 20
BOARD_BUDGET_S = 45
BOARD_RETRY_BASE = 0.5
#: The `gh` runner for the board write; None -> the bounded real one. Tests put a fake here.
_BOARD_RUN = None


def phase_token(phase):
    """`research` -> `P2 RESEARCH`. An unknown phase returns itself rather than raising: the CLI
    already rejects one at the boundary (`_check_phase`), so this is only reachable by a direct
    caller, and a banner must never be the thing that breaks a run."""
    for kind, token in PHASE_TOKENS:
        if kind == phase:
            return token
    return str(phase)


def phase_label(phase):
    """`implement` -> `🟦 P5 IMPLEMENT`: the phase token with its §2 badge in front. An unknown
    phase gets no badge and prints exactly as `phase_token` returns it -- a colour is never
    invented for a phase that does not exist."""
    badge = PHASE_BADGES.get(phase)
    return f"{badge} {phase_token(phase)}" if badge else phase_token(phase)


def next_phase_token(phase):
    """The token of the phase that FOLLOWS `phase`, or None for the last one (`retro`) and for an
    unknown phase. This is the "what comes next" the issue asks for -- the flow made legible to a
    reader who does not know the SDLC by heart."""
    kinds = [kind for kind, _ in PHASE_TOKENS]
    if phase not in kinds:
        return None
    index = kinds.index(phase)
    return PHASE_TOKENS[index + 1][1] if index + 1 < len(PHASE_TOKENS) else None


def next_phase_kind(phase):
    """The KIND of the phase that follows `phase` (`plan`), or None for `retro` and for an unknown
    phase -- `next_phase_token`'s sibling, returning what `render.py` accepts as a `to_phase`
    rather than the rendered token. One forward walk of `PHASE_TOKENS`, no second list."""
    kinds = [kind for kind, _ in PHASE_TOKENS]
    if phase not in kinds:
        return None
    return kinds[kinds.index(phase) + 1] if kinds.index(phase) + 1 < len(kinds) else None


def goal_ref(goal):
    """`#2100` for a GitHub issue number; the bare stem for a local goal file. `#0004-vision-first-
    onramp` would be a lie -- in local mode there is no issue to reference -- so the `#` is added
    only when the stem is a plain number."""
    stem = _load("work").stem(goal)
    return f"#{stem}" if stem.isdigit() else stem


def clean_title(raw):
    """One line, collapsed whitespace, capped at `TITLE_MAX`. Returns "" for anything empty or
    unusable, and "" is the caller's cue to print the BARE id -- never a blank sitting where a
    title should be, never a placeholder, never a guess."""
    text = " ".join(str(raw or "").split())
    if not text:
        return ""
    return text if len(text) <= TITLE_MAX else text[:TITLE_MAX - 1].rstrip() + "…"


def title_from_mirror(sdlc_dir, goal):
    """The goal's title out of `mirror.py`'s local board mirror -- the gitignored NDJSON snapshot of
    the GitHub backlog that is ALREADY on disk, written by the pick that claimed this goal. READ,
    never fetched: this is the whole reason the phase-boundary path stays network-free.

    Bounded by `mirror._OPEN_LIMIT` (200 open + 200 recently-closed records), so the scan is O(400
    lines) at any backlog size, not O(backlog) -- and each line is JSON-parsed only after a cheap
    substring pre-filter for the number, so the common case parses one record, not four hundred.

    Returns "" for every miss, and the misses are ordinary, not exceptional: local (non-github)
    mode never writes a mirror; a mirror older than its TTL has not been refreshed yet; and a goal
    filed since the last pick is simply not in it. Every one of those prints the bare id."""
    try:
        path = pathlib.Path(sdlc_dir) / MIRROR_REL
        if not path.is_file():
            return ""
        stem = _load("work").stem(goal)
        if not stem.isdigit():
            return ""                       # the mirror is keyed by ISSUE NUMBER; a local goal
        number = int(stem)                  # file stem can never match one
        needle = str(number)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                if needle not in raw_line:
                    continue                # cheap pre-filter: no JSON parse for a non-candidate
                try:
                    record = json.loads(raw_line)
                except ValueError:
                    continue                # one malformed line never blinds the whole lookup
                if isinstance(record, dict) and record.get("number") == number:
                    return clean_title(record.get("title"))
    except (OSError, TypeError, ValueError):
        return ""
    return ""


def title_from_goal_file(sdlc_dir, goal):
    """Local (non-github) mode: `.sdlc/goals/<stem>.md`'s own `title:` frontmatter -- again a file
    already on disk, read with the same zero-dep `frontmatter` parser every other script uses.
    Returns "" when the file, the fence, or the key is absent."""
    try:
        stem = _load("work").stem(goal)
        if _load("state").unsafe_goal_reason(stem):
            return ""                       # same path-component guard `marker_path` applies
        path = pathlib.Path(sdlc_dir) / "goals" / f"{stem}.md"
        if not path.is_file():
            return ""
        return clean_title(_load("frontmatter").get(path.read_text(encoding="utf-8"), "title"))
    except (OSError, TypeError, ValueError):
        return ""


def resolve_title(sdlc_dir, goal, given=None):
    """The goal's human title for a banner, assembled ONLY from what is already on this machine.

    In order: an explicit `--title` (the orchestrator holds the title already -- `loop.py precheck`
    fetched it once at pick time -- so passing it costs nothing), then the local board mirror, then
    the local goal file. NO network call, NO subprocess, and no `_load("sources")`: the guard for
    that is executed, not asserted in prose -- see this module's docstring.

    Never raises. Returns "" when nothing on this host knows the title, and the caller then prints
    the bare id. That degradation is the honest one the issue asks for, and it is the SAME manner
    as the pre-existing `cost: unavailable on this host (<reason>)` path: state what is known,
    print nothing where nothing is known, and never fabricate the difference."""
    try:
        explicit = clean_title(given)
        if explicit:
            return explicit
        from_mirror = title_from_mirror(sdlc_dir, goal)
        if from_mirror:
            return from_mirror
        return title_from_goal_file(sdlc_dir, goal)
    except Exception:                       # noqa: BLE001 - a banner must never break a run
        return ""


def marker_started_at(marker):
    """Epoch seconds for a marker's phase start, or None.

    `ts_start_epoch` (#2100) is the exact float `write_marker` stamped. A marker written by a
    pre-#2100 install has only the ISO `ts_start`, which is parsed back as UTC via
    `calendar.timegm` -- never `time.mktime`, which would read a `Z`-suffixed stamp as LOCAL time
    and shift every elapsed figure by the machine's UTC offset (an hours-wrong "8m11s" is worse
    than no figure at all). Neither readable -> None, and the caller prints an honest
    `elapsed: unavailable`, never a zero."""
    if not isinstance(marker, dict):
        return None
    epoch = marker.get("ts_start_epoch")
    if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
        return float(epoch)
    raw = marker.get("ts_start")
    if not raw:
        return None
    try:
        return float(calendar.timegm(time.strptime(norm_ts(raw), "%Y-%m-%d %H:%M:%S")))
    except (TypeError, ValueError):
        return None


def format_elapsed(seconds):
    """Wall time in the unit a human reads faster than a token count: `41s`, `8m11s`, `2h05m`.
    None (or a negative interval -- a clock that moved backwards) returns None, so the caller
    prints `elapsed: unavailable` rather than a fabricated `0s`."""
    if seconds is None:
        return None
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return None
    if total < 0:
        return None
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def _head_line(mark, label, ref, title):
    """`⚪ PHASE START · #2100 <title>`, or `🟨 PHASE END · #2100 <title>` when `mark` is a phase
    badge -- and exactly `⚪ PHASE START · #2100` when no title is known. `PHASE START`/`PHASE END`
    stay in the line VERBATIM: they were the only stable, greppable token these banners had, and
    #2100 is a legibility change, not a rename."""
    head = f"{mark} {label} · {ref}" if mark else f"{label} · {ref}"
    return f"{head} {title}" if title else head


def _safe_host_model(value):
    """A host model ID is one short banner field, never a second terminal line or separator."""
    return (isinstance(value, str) and 0 < len(value) <= 256
            and all(char.isprintable() and char != "·" for char in value))


_CODEX_HOST_MODELS = frozenset(("gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol",
                                "gpt-5.6-terra", "gpt-6-astra"))


def _host_model_label(value):
    return value if _safe_host_model(value) else "unrecorded"


def _tail(phase):
    """Flow POSITION, not destination: `next: 🟦 P5 IMPLEMENT` reads as the map it is, where
    `→ P5 IMPLEMENT` read as a promise about where this run is going. It is a static forward walk
    of `PHASE_TOKENS` and knows nothing about the verdict, so on a blocked P4 the loop actually
    returns to P3 -- naming the successor as a destination would have been a second false claim on
    the same line the `✅` was removed from (PR review of #2102)."""
    nxt = next_phase_token(phase)
    if not nxt:
        return " · last phase"
    kind = next(k for k, token in PHASE_TOKENS if token == nxt)
    return f" · next: {phase_label(kind)}"


def start_measurements(model, host_model=""):
    """The tier+host-model half of the `start` line, with both of D-1's and D-2's claims present
    and distinct, plus the requested host model (#2537, shipped independently on `main` while this
    branch was stale -- restored here rather than dropped): `requested host model: <id> ·
    predicted model tier: <tier> (agent-set)`. `predicted` IS D-1's claim; `(agent-set)` is D-2's,
    the one thing the field name does not already say. `_host_model_label` sanitizes the host-model
    half exactly as it always did."""
    return (f"requested host model: {_host_model_label(host_model)} "
            f"· {TIER_LABEL_PREDICTED} model tier: {model or 'unspecified'} "
            f"({TIER_LABEL_AGENT_SOURCED})")


def end_measurements(elapsed, result, totals=None, no_elapsed="no phase-start marker"):
    """Everything `end` MEASURED, as the ` · `-separated fields both output shapes carry. ONE
    function, so the constructed Block B and the unconstructed fallback banner cannot say the same
    numbers differently -- a wording corrected in one and not the other is exactly the drift #2112
    exists to remove, and it would be invisible until the renderer refused.

    `totals` is an optional `timing_store.totals` dict -- the GOAL's cumulative working time, not
    this phase's (#2381, shipped independently on `main` while this branch was stale -- restored
    here as a parameter rather than dropped). It defaults to None so every existing caller, and
    every assertion pinning these lines byte for byte, is untouched; a goal with nothing recorded
    prints nothing here rather than a zero. Appended LAST, after every other measured field, so it
    lands right before whatever the caller appends of its own (`_tail`'s ` · next: <phase>` for the
    unconstructed fallback; nothing further for the render.py-constructed `fact`, since
    `render_event`'s own `to_phase` already carries that half).

    `totals` is an optional `timing_store.totals` dict — the GOAL's cumulative working time, not
    this phase's. It defaults to None so every existing caller, and every assertion pinning these
    lines byte for byte, is untouched; a goal with nothing recorded prints nothing here rather
    than a zero. The field is deliberately LABELLED `goal so far`: it rides a per-phase banner
    while carrying goal-cumulative numbers, and an unlabelled duration there would be read as this
    phase's own.

    Every honest-degradation string the pre-#2100 line printed survives VERBATIM -- `cost:
    unavailable on this host (`, the `(partial — N of M turns unpriced)` qualifier, and the
    `(inline, windowed)` precision marker. `docs/output-contract.md`'s Block B quotes the first of
    those and tells a model to reproduce it, so rewording it here would silently break the prose
    half of the same contract this change exists to serve.

    `no_elapsed` (#2667) names WHY there is no elapsed time, inside the unchanged `elapsed:
    unavailable (...)` shape: the pre-existing default, `"no phase-start marker"`, for a phase
    nobody `start`ed; `cmd_end` passes `"stale phase-start marker"` when the SAME-phase marker
    that exists failed its identity check (`stale_marker_reason`) -- never the false `phase ran
    inline within a nested session` `collect_phase_usage` would otherwise print, because `_collect`
    degrades to `unavailable` before that function is ever called on a stale marker."""
    fields = [elapsed if elapsed else f"elapsed: unavailable ({no_elapsed})"]
    source = result.get("source")
    if source in ("claude-code", "claude-code-inline"):
        cost = result.get("cost_usd")
        if cost is not None:
            unpriced = result.get("unpriced_turns", 0)
            partial = f" (partial — {unpriced} of {result['turns']} turns unpriced)" if unpriced else ""
            fields.append(f"${cost:.2f}{partial}")
        else:
            # Only the parenthetical varies, as it already does across the branches below: the
            # `cost: unavailable on this host (` prefix is quoted verbatim by
            # docs/output-contract.md §3. Known ceiling (#2573/S1-G9): this re-`stat`s the module
            # constant rather than learning from the load, so it would be wrong for a caller that
            # passed its own `csv_path`. No such caller exists — `collect_phase_usage` passes
            # neither `rates=` nor `csv_path=`.
            reason = ("model not in rate card" if DEFAULT_RATES_CSV.exists()
                      else "rate card missing from this install")
            fields.append(f"cost: unavailable on this host ({reason})")
        fields.append(f"tokens {result['tokens_in']:,} in, {result['tokens_out']:,} out")
        models = ",".join(result.get("models") or []) or "unknown"
        precision = "" if source == "claude-code" else " (inline, windowed)"
        fields.append(f"{models}{precision}")
    elif source == "codex":
        fields.append("cost: unavailable on this host (Codex model absent from bundled rate card)")
        fields.append(f"tokens {result['tokens_in']:,} in, {result['tokens_out']:,} out")
        fields.append(",".join(result.get("models") or []) or "model: unavailable")
    else:
        fields.append(f"cost: unavailable on this host ({result.get('reason', 'unknown reason')})")
    if totals and totals.get("recorded"):
        active = format_elapsed((totals.get("active_ms") or 0) / 1000.0)
        effort = format_elapsed((totals.get("effort_ms") or 0) / 1000.0)
        if active and effort:
            fields.append(f"goal so far: active {active} · effort {effort}")
    return " · ".join(fields)


def start_lines(ref, phase, model, title, host_model=""):
    """The two `start` lines, printed as they always were.

    A PHASE START IS NOT A BLOCK B, AND #2112 TRIED TO MAKE IT ONE (review). Block B's shape is
    `<from> → <to>`; a start has no from-phase, because this module is called per PHASE, not per
    transition. The draft that forced it in opened every start of every phase with
    `previous phase unknown (no verdict at this boundary)` -- 51 characters, in the line's most
    prominent position, saying nothing, forever. A start announces a state (`⚪` -- §2's "claimed,
    not started", which is exactly true when SKILL.md fires this before dispatch) and carries no
    measurement, so it is neither a boundary nor an EVENT. It stays its own two lines, and the
    `⚪` stays with it, because Block B's no-marker rule governs a block this line is not in.

    The rule that came out of it is worth more than the shape: do not narrate the not-knowing.
    `end_facts` is where it applies next. Leads with the phase's own badge (`phase_label`, #2485,
    shipped independently on `main` while this branch was stale), which names the phase and claims
    nothing about it -- the same reasoning `end_lines`' own leading badge below carries."""
    return [
        _head_line(MARK_START, "PHASE START", ref, title),
        f"   {phase_label(phase)} · {start_measurements(model, host_model)}",
    ]


def end_lines(ref, phase, title, elapsed, result, totals=None,
             no_elapsed="no phase-start marker"):
    """The two `end` lines — the UNCONSTRUCTED FALLBACK, as `start_lines` is. `result` is a
    `collect_phase_usage` dict; `elapsed` a `format_elapsed` string or None. `totals` threads
    straight through to `end_measurements` (#2381, restored -- see that function's own docstring),
    as does `no_elapsed` (#2667, see `end_measurements`'s own docstring).

    Carries NO §2 liveness marker, by design -- there is no `verdict` parameter here because this
    module cannot know one. See `MARK_START`'s note for the measurement that made that blocking. It
    leads with the phase's badge instead (`PHASE_BADGES`, #2485, restored), which names the phase
    and claims nothing about it -- both here and in the body line's own `phase_label`, matching
    main's shipped shape byte for byte."""
    return [
        _head_line(PHASE_BADGES.get(phase, ""), "PHASE END", ref, title),
        "   " + phase_label(phase) + " · "
        + end_measurements(elapsed, result, totals, no_elapsed) + _tail(phase),
    ]


# --------------------------------------------------------------------------- Block B (#2112)


def end_facts(ref, phase, title, elapsed, result, totals=None,
             no_elapsed="no phase-start marker"):
    """`end` as Block B -- `render.EVENT_FIELDS`, complete. An `end` IS a boundary: it has a phase
    it just left and a measurement to carry, which is exactly what Block B is for, and it is the
    only one of this module's two verbs that is (see `start_lines`). `totals` threads straight
    through to `end_measurements`, so the constructed Block B carries the same `goal so far` field
    the unconstructed fallback does (#2381) -- see that function's own docstring.

    `to_phase` IS THE PHASE THAT FOLLOWS, and the enumerated `"last"` on `P7 RETRO`. Both are
    `_tail`'s pre-#2112 wording restored -- `next: P3 PLAN` became `→ P3 PLAN`, `last phase` stayed
    `last phase`. The draft this replaces passed `"unknown"` here and then named the successor in
    the very next clause ("The standing SDLC puts P3 PLAN after this one"): it declared an
    ignorance the same sentence resolved, which is worse than either half alone. The standing SDLC
    genuinely knows what comes next; what nobody at this boundary knows is the VERDICT, and the
    fix for that was never a word on this line -- it is that no `✅` is stamped here at all (#2100),
    which `render.EVENT_FIELDS` makes structural by carrying no `marker` key.

    `next_action` is `None`. This module has no next action of its own to announce, and
    `render.render_event` writes nothing for a null one. Do not narrate the not-knowing.

    `url` is always `null`: a URL would have to be fetched, and #2100's rule for this path is local
    sources only, so the ref renders bold-and-unlinked, which §3 permits when no URL exists.
    `artifact` is `null` because a phase boundary produces no artifact of its own."""
    return {
        "ref": ref,
        "url": None,
        "title": title or None,          # "" is the honest "no source on this host knew it"
        "from_phase": phase,
        "to_phase": next_phase_kind(phase) or PHASE_LAST,
        "artifact": None,
        "fact": end_measurements(elapsed, result, totals, no_elapsed),
        "next_action": None,
    }


def render_block(block, facts):
    """The constructed block from `render.py`, or None -- and None is never silent.

    SHELL-OUT, NEVER IMPORT (design D-6): the only arrangement satisfying both of this repo's
    import bans, and the same gesture SKILL.md uses to invoke this module. `render.py` loads no
    sibling script of its own, so no `sys.path` surgery is needed on either side.

    A REFUSAL MUST NOT LOSE THE BANNER. `render.py` refuses rather than degrades, and its refusal
    is the right answer for a malformed line -- but at THIS call site the alternative to an ugly
    line is a measurement that never reaches the console at all, which is strictly worse: the cost,
    the tokens and the elapsed time were already paid for. So a refusal returns None, the caller
    prints the pre-#2112 banner, and the refusal itself goes to STDERR verbatim, where it stays
    visible and greppable. The fallback is therefore rare and loud, never a quiet second format.

    Never raises: a missing interpreter, an unreadable script, a timeout and a refusal all take the
    same exit. A banner must never break a run."""
    try:
        proc = subprocess.run(
            [sys.executable, str(RENDER_SCRIPT), block, "--json", json.dumps(facts)],
            capture_output=True, text=True, timeout=RENDER_TIMEOUT_S)
    except Exception as exc:                # noqa: BLE001 - a banner must never break a run
        print(f"phase_report: could not run {RENDER_SCRIPT.name} ({exc.__class__.__name__}: "
              f"{exc}); printing the unconstructed banner", file=sys.stderr)
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr or "").strip() or f"exit {proc.returncode} with an empty stdout"
        print(f"phase_report: {detail}; printing the unconstructed banner", file=sys.stderr)
        return None
    return proc.stdout.rstrip("\n")


def emit(block_facts, fallback_lines):
    """Print the constructed block, or the fallback banner when the renderer would not build one.
    Exactly one of the two reaches stdout, always -- there is no path here that prints nothing.
    `cmd_end` is the only caller: `cmd_start` prints `start_lines` directly, because a start is not
    a Block B at all (see `start_lines`)."""
    block = render_block("event", block_facts)
    for line in ([block] if block is not None else fallback_lines):
        print(line)


# --------------------------------------------------------------------------- marker file


def marker_path(sdlc_dir, goal):
    work = _load("work")
    state = _load("state")
    stem = work.stem(goal)
    reason = state.unsafe_goal_reason(stem)
    if reason:
        raise ValueError(f"unsafe goal {goal!r} for the phase marker: {reason}")
    return pathlib.Path(sdlc_dir) / "state" / "phase" / f"{stem}.json"


def write_marker(sdlc_dir, goal, phase, model, title="", now=None, expect_agent_id=False,
                 host_model="", requested_model="", pid=None, codex_thread_id=None,
                 claude_session_id=None):
    """#2100 added two fields, both so that `end` needs NO lookup of its own:
    `title` (already resolved at `start`, from local sources only) and `ts_start_epoch` (the exact
    float behind the ISO `ts_start`, so elapsed wall time is a subtraction rather than a parse).
    `ts_start` is unchanged and still written, so a marker stays readable by anything that had
    been reading it.

    #2667 adds three more, EACH WRITTEN ONLY WHEN PRESENT, so a pid-less/thread-less/session-less
    marker keeps today's exact shape: `pid` (the caller's `--pid`), `codex_thread_id` (Codex only
    -- see `_caller_codex_thread`), and `claude_session_id` (`_session_id()`, Claude Code only).
    `end` compares all three against its OWN caller's identity before trusting a same-phase
    marker's start time at any age (`stale_marker_reason`)."""
    started = float(now if now is not None else time.time())
    entry = {
        "phase": phase,
        "model": model or "",
        "host_model": host_model or "",
        # Presentation only. Claude's Task selector is an alias, not the full observed model ID;
        # putting it in host_model would trigger the strict Codex equality gate at `end`.
        "requested_model": requested_model or "",
        "title": clean_title(title),
        "ts_start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "ts_start_epoch": started,
        "goal": str(goal),
    }
    if expect_agent_id:
        entry["expect_agent_id"] = True
    if pid is not None:
        entry["pid"] = pid
    if codex_thread_id is not None:
        entry["codex_thread_id"] = codex_thread_id
    if claude_session_id is not None:
        entry["claude_session_id"] = claude_session_id
    path = marker_path(sdlc_dir, goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, sort_keys=True))
    return entry


def read_marker(sdlc_dir, goal):
    path = marker_path(sdlc_dir, goal)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def step_tier(model_flag, marker, phase):
    """The STEP line's tier, naming where it came from. A step can run below its phase's ceiling
    (`predict.py resolve-step` routes mechanical steps cheaper), so a `--model` given to `step` is
    that step's own and reads `(step)`. Without one, the tier `start` recorded for THIS phase reads
    `(phase)` -- `unspecified` when `start` was given none, the word `start_lines` uses. With no
    marker for this phase it is `unrecorded`: another phase's tier is never borrowed."""
    if model_flag:
        return f"{model_flag} (step)"
    if marker is not None and marker.get("phase") == phase:
        return f"{marker.get('model') or 'unspecified'} (phase)"
    return "unrecorded"


def step_host_model(host_model_flag, phase_model_flag, marker, phase):
    """Only explicit dispatch provenance may identify a step's requested host model."""
    if host_model_flag:
        return host_model_flag
    if phase_model_flag and marker is not None and marker.get("phase") == phase:
        return marker.get("requested_model") or marker.get("host_model") or "unrecorded"
    return "unrecorded"


def step_lines(ref, phase, tier, title, step_num, step_total, brief, host_model=""):
    """The three `step` lines, pure and testable without the CLI: `start_lines`' shape, labelled
    `STEP 2/7` (or `STEP 2` with no total) so it can never be mistaken for a phase boundary. Like
    `PHASE END` it carries no §2 marker and leads with its phase's badge instead -- `MARK_START`
    stays the only marker this module prints. The brief sits last on its own line, so a ` · `
    inside it has no neighbouring field to forge."""
    label = f"STEP {step_num}/{step_total}" if step_total else f"STEP {step_num}"
    return [
        _head_line(PHASE_BADGES.get(phase, ""), label, ref, title),
        f"   {phase_label(phase)} · {brief}",
        (f"   requested host model: {_host_model_label(host_model)}"
         f" · predicted model tier: {tier}"),
    ]


# --------------------------------------------------------------------------- CLI


_VALUE_FLAGS = ("--model", "--host-model", "--requested-model", "--agent-id", "--title",
                "--num", "--total", "--brief", "--pid")


def _flags(argv):
    """Local copy of loop.py's own `_flags` idiom (hand-rolled `--name value` scanner, including
    its #541 fix — code review, 2026-08-25: an earlier draft's docstring claimed this parity
    without the code fully matching it) — sibling scripts in this directory deliberately do not
    import each other's private helpers (see actionlog.py's own module docstring for the
    precedent). `--name=value` is unambiguous for any flag, but only when `name` carries no
    whitespace — a name is never legitimately whitespace-bearing, so that shape is always leaked
    prose from a value the parser failed to consume, dropped rather than kept as a nonsense key
    (loop.py's own #541 fix, same reasoning). Known value-taking flags (`_VALUE_FLAGS`) consume
    the next token unconditionally, so a value that itself starts with `--` is never misread as a
    second, garbage flag."""
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


def _caller_pid(flags):
    """`--pid` (#2667) is optional, so legacy callers and pid-less markers keep working. When
    given it must be a positive integer -- the same stance `loop.py agent-start` takes on a bad
    `--pid` (`loop.py:5133-5141`). Returns `(pid, error)`; `error` is a ready-to-print message.
    `start` refuses on it (it has measured nothing yet); `end` warns and falls back instead (it is
    the boundary that already paid for its measurement -- see the departures section of
    `.sdlc/plans/2667.md`)."""
    raw = flags.get("pid")
    if raw is None:
        return None, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, f"--pid must be a positive integer, got {raw!r}"
    if value <= 0:
        return None, f"--pid must be a positive integer, got {raw!r}"
    return value, None


def _caller_codex_thread():
    """The caller's Codex thread id, or `None` on Claude Code (#2667, D1). Read from the
    environment on BOTH `start` and `end`, so no second flag is documented for it -- one Codex
    desktop app pid does not merge its own separate tasks (`loop.py:2488-2489`), which is why the
    thread half of identity matters beside the pid half."""
    if _session_id():
        return None
    return _codex_agent_id(os.environ.get("CODEX_THREAD_ID"))


def _fallback_ttl_seconds(sdlc_dir):
    """`ledger.lease.ttl_hours` (D1), read LAZILY -- only `stale_marker_reason`'s fallback branch
    (either side of an `end` has no pid) ever calls this, so an ordinary identity-matched `end`
    never touches config.json at all. A missing/malformed config.json, or a malformed
    `ttl_hours`, prints ONE stderr warning and falls back to `ledger.DEFAULT_LEASE_TTL_HOURS`
    rather than raising -- the staleness banner must never be lost to a traceback over a lease
    setting (finding 9)."""
    ledger = _load("ledger")
    state = _load("state")
    try:
        config = state.load_config(sdlc_dir)
    except Exception as exc:                # noqa: BLE001 - a banner must never break a run
        print(f"phase_report: warning: could not read ledger.lease.ttl_hours ({exc}); using the "
              f"default {ledger.DEFAULT_LEASE_TTL_HOURS}h lease", file=sys.stderr)
        return ledger.DEFAULT_LEASE_TTL_HOURS * 3600.0
    try:
        return ledger.lease_ttl_seconds(config)
    except Exception as exc:                # noqa: BLE001 - a banner must never break a run
        print(f"phase_report: warning: malformed ledger.lease.ttl_hours ({exc}); using the "
              f"default {ledger.DEFAULT_LEASE_TTL_HOURS}h lease", file=sys.stderr)
        return ledger.DEFAULT_LEASE_TTL_HOURS * 3600.0


def _uuid7_millis(value):
    """The UUIDv7 creation timestamp, in epoch milliseconds -- the top 48 bits of the UUID's raw
    hex. Shared by `_codex_rollout`'s own bounded-day lookup and `_codex_child_since`'s billing
    bound, so the two parsers cannot drift apart."""
    return int(value.replace("-", "")[:12], 16)


def _codex_child_since(child_id):
    """A validated Codex child's own creation time, floored to the second, in the same
    `%Y-%m-%dT%H:%M:%SZ` shape a marker's `ts_start` carries -- the lower bound `end` passes
    INSTEAD of a stale marker's dead window (D2), so nothing of the crashed writer's window ever
    reaches the read. `child_id` is already a canonical UUIDv7 by the time this is called
    (`_codex_agent_id` validated it against `_CODEX_AGENT_ID`), so this never raises."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_uuid7_millis(child_id) / 1000.0))


def stale_marker_reason(marker, pid, codex_thread_id, ttl_seconds, now=None,
                        claude_session_id=None):
    """`None` (trusted, at ANY age) or a reason string (stale) for a SAME-phase marker `end` is
    about to trust (#2667). Decides by IDENTITY, not by age: a marker has no heartbeat, so a
    genuinely long-running phase and a dead one look identical from the outside, and reintroducing
    an age-based TTL on a MATCHING identity would resurrect #1391 (a long phase misread as dead).

    0. Claude session, checked FIRST, in either branch below. If both the marker and the caller
       carry a session id and they differ, the marker is stale. This rule can only ADD staleness
       -- it never makes an otherwise-untrusted marker trusted -- because a differing session is
       exactly the case a same-pid match cannot see any other way (a container restart handing a
       fresh process the dead one's own pid; `claude --resume` under a live pid it does not own).
    1. BOTH sides have a pid: the pids must match, and so must the Codex thread (including "one
       side carries one and the other does not") -- when both hold, the marker is TRUSTED AT ANY
       AGE and no TTL is consulted. Otherwise it is stale (a different writer, or a different
       Codex task multiplexed under the app's one desktop pid).
    2. FALLBACK, when either side has no pid: a thread mismatch (both sides carry one) is stale;
       otherwise a dead writer (`ledger.pid_alive` false) is stale; otherwise the marker's age
       against the lease TTL decides (`ttl_seconds() is None` means "never expire" -- config
       `ledger.lease.ttl_hours: 0`); otherwise trusted.

    NOT COVERED, measured (`.sdlc/plans/2667.md`'s own coverage table names every row): a pid-less
    marker inside the lease still misbills its window in full when `ledger.lease.ttl_hours: 0`,
    and misbills the window even under the default TTL when the crash-then-resume happens within
    it; the pre-#2667 `end` gesture (no `--pid`) against a marker whose pid is ALIVE AGAIN (pid
    reuse) still misbills when the marker carries no session id or the SAME one; an identity MATCH
    by pid reuse where the session id also matches (or neither side carries one, e.g. Codex or
    Cursor) is trusted at any age -- the container-restart shape where Docker hands a fresh host
    process the dead one's own pid; one live process that abandons a phase and calls `end` for it
    again without a fresh `start` cannot be told apart from one genuinely long phase, by design,
    because there is no heartbeat; and `/agrim-time`'s derived fallback
    (`time_report.derived_totals`) still pairs a resumed `phase`/`end` event with the dead
    attempt's own `phase`/`start` when a goal has no stored interval and the journal is on.

    Code review, 2026-09-25: an EXACT read's (`--agent-id`) own retry-idempotency (rev 3.1) keeps
    the SAME epoch+agent-id attempt key across the trusted-to-stale boundary specifically so a
    RETRY of an attempt already credited in THIS run is never double-counted -- it does NOT mean
    that attempt is credited into every run that later reads it. When the attempt's own real epoch
    predates the CURRENT run (`state.start_run` reset `run_token_credits`/`run_codex_token_credits`
    to empty after this attempt's one and only `end` already landed in an OLDER run, so there is no
    trusted end left to retry inside the new one), `record_phase_end`'s `old_run` guard -- the same
    guard that stops a retry from double-crediting -- ALSO stops that attempt from ever being
    credited into the new run's own cursor: `run_tokens`/`run_codex_raw_tokens` measured, stay at
    the new run's own 0 even though the `phase`/`end` (and, for Claude, `spend`) journal events are
    still written and a configured `budget.max_codex_raw_tokens` ceiling reads `credit` (the
    marker) as satisfied and exits 0 rather than refusing. So: WITHIN one run, an exact read is
    credited exactly once, retries included; ACROSS a run boundary, the current run's budget simply
    does not see a prior run's attempt at all -- it is priced in the ledger, not in the cursor a
    ceiling actually gates.

    Also measured (2026-09-25): a WINDOWED (no `--agent-id`) stale retry after a fresh trusted end
    writes its own tokenless `phase`/`end` journal event under the `\0stale`-scoped key (by design,
    D3) -- and `doctor.py`'s budget-enforcement row (`_budget_enforcement_state`) counts `phase`/
    `end` events per run in its denominator without distinguishing "one phase, ended twice" from
    "two phases, one unpriced". One trusted end followed by one stale retry measured turns a
    `READY -- 1/1` reading into `PARTIAL -- 1/2`, even though the one real measurement stayed fully
    priced and credited; the operator sees an apparent regression in `/agrim-doctor` with nothing
    actually wrong. Not fixed here.

    Where `$PPID` (or the session id) is NOT STABLE on a host, EVERY same-phase `end` reads stale:
    nothing misbills, and tokens stay exact on the Claude subagent and Codex-child paths, but
    elapsed time and the timing-store row are lost for EVERY phase -- including those exact paths
    -- and time tracking goes silent (a default config's `/agrim-time` then reads "not recorded",
    where a stable host would have shown a real duration); the only remaining signal is this
    module's own stderr warning at each `end`. With `budget.max_codex_raw_tokens` configured, an
    inline Codex THREAD end (no dispatched child) that reads stale also EXITS 2 every time -- an
    unstable `$PPID` on that one path HALTS the run rather than merely losing its clock."""
    if not isinstance(marker, dict):
        return None
    marker_session = marker.get("claude_session_id")
    if marker_session and claude_session_id and marker_session != claude_session_id:
        return "started by another Claude Code session"

    marker_pid = marker.get("pid")
    marker_thread = marker.get("codex_thread_id")
    if marker_pid is not None and pid is not None:
        if marker_pid != pid:
            return f"started by process {marker_pid}, ended by process {pid}"
        if marker_thread != codex_thread_id:
            return "started by another Codex task under the same process"
        return None

    if marker_thread and codex_thread_id and marker_thread != codex_thread_id:
        return "started by another Codex task"

    ledger = _load("ledger")
    if marker_pid is not None and not ledger.pid_alive(marker_pid):
        return f"its writer, process {marker_pid}, is gone"

    ttl = ttl_seconds() if callable(ttl_seconds) else ttl_seconds
    if ttl is not None:
        started = marker_started_at(marker)
        if started is not None:
            age = (now if now is not None else time.time()) - started
            if age >= ttl:
                return (f"{format_elapsed(age)} old with no owner pid to confirm it, past the "
                        f"{int(ttl // 3600)}h lease")
    return None


def _codex_session_id():
    # A Codex subagent inherits its parent's CODEX_SESSION_ID but writes its own rollout under
    # CODEX_THREAD_ID. Inline work at the top level uses the latter as well.
    return os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID", "")


def _check_phase(phase):
    """Validate `phase` against the same vocabulary `loop.py emit ... phase --phase <value>` has
    always enforced (`ledger.PHASE_KINDS`) -- `ledger.append()` deliberately does NOT enforce this
    itself (ledger.py: "documented but deliberately NOT enforced by append() itself"); enforcement
    belongs at the CLI boundary, the same place `loop.py`'s own emit dispatch and `record
    --retro-grade` already put it. Code review, 2026-08-25: an earlier draft of this script skipped
    this check entirely, silently letting a typo (`pln_review`) fragment the phase vocabulary in
    the ledger with no error. Returns an error string, or None when `phase` is valid."""
    ledger = _load("ledger")
    if phase not in ledger.PHASE_KINDS:
        return f"unknown phase {phase!r} (expected one of {', '.join(ledger.PHASE_KINDS)})"
    return None


def cmd_start(argv):
    if len(argv) < 3:
        print("usage: phase_report.py start <sdlc_dir> <goal> <phase> --model M "
              "[--title T] [--expect-agent-id] [--pid PID] [--host-model HOST_ID | "
              "--requested-model SELECTOR]",
              file=sys.stderr)
        return 2
    sdlc_dir, goal, phase = argv[0], argv[1], argv[2]
    err = _check_phase(phase)
    if err:
        print(f"phase_report.py: {err}", file=sys.stderr)
        return 2
    flags = _flags(argv[3:])
    pid, pid_err = _caller_pid(flags)
    if pid_err:
        # `start` REFUSES rather than falls back (departure 3): it has measured nothing yet, so
        # there is no banner or ledger event a refusal here could be dropping.
        print(f"phase_report.py: {pid_err}", file=sys.stderr)
        return 2
    model = flags.get("model", "")
    host_model = flags.get("host-model", "")
    requested_model = flags.get("requested-model", "")
    if host_model and not _safe_host_model(host_model):
        print("phase_report.py: --host-model must be a single printable field of at most 256 "
              "characters", file=sys.stderr)
        return 2
    if host_model and host_model not in _CODEX_HOST_MODELS:
        print("phase_report.py: --host-model must be an approved exact Codex model ID",
              file=sys.stderr)
        return 2
    if requested_model and not _safe_host_model(requested_model):
        print("phase_report.py: --requested-model must be a single printable field of at most 256 "
              "characters", file=sys.stderr)
        return 2
    if host_model and requested_model:
        print("phase_report.py: --host-model and --requested-model are mutually exclusive",
              file=sys.stderr)
        return 2
    if flags.get("expect-agent-id") == "true" and _codex_session_id() and not _session_id() \
            and not host_model:
        print("phase_report.py: Codex dispatched phases require --host-model with the actual "
              "model ID passed to the subagent", file=sys.stderr)
        return 2
    title = resolve_title(sdlc_dir, goal, flags.get("title"))
    write_marker(sdlc_dir, goal, phase, model, title=title,
                 expect_agent_id=flags.get("expect-agent-id") == "true",
                 host_model=host_model, requested_model=requested_model,
                 pid=pid, codex_thread_id=_caller_codex_thread(),
                 claude_session_id=_session_id() or None)
    ledger = _load("ledger")
    ledger.safe_append(sdlc_dir, "phase", goal, stream=ledger.EVENTS, phase=phase, state="start")
    # NOT routed through `render.py`: a start is an announcement, not a boundary -- `start_lines`
    # carries the whole argument.
    for line in start_lines(goal_ref(goal), phase, model, title,
                            host_model or requested_model):
        print(line)
    sys.stdout.flush()
    # #233: AFTER the marker, the ledger event and the banner, so no board outcome can change what
    # this boundary records, prints or returns.
    mirror_phase_to_board(sdlc_dir, goal, phase)
    return 0


#: #233 review: how long a KILLED `gh` may take to release its pipes before the runner gives up on
#: it. Bounded on every platform: a grandchild that escaped the kill (or a Windows tree `taskkill`
#: could not reach) can hold stdout open, and an unbounded reap would hang the boundary anyway.
BOARD_REAP_S = 5
#: The `gh` executable the bounded runner spawns (tests point it at a local stub).
BOARD_GH = "gh"


class BoardCallTimeout(RuntimeError):
    """A board `gh` call overran its timeout and was killed. `no_retry`: `sources.GitHubSource._run`
    must not retry it as transient -- the boundary skips its board write instead (one warning)."""
    no_retry = True


def _on_windows():
    return os.name == "nt"


def _kill_tree(proc):
    """Kill a hung `gh` AND everything it spawned: its whole process group on POSIX (it was started
    in a session of its own), `taskkill /T /F` on Windows -- the same split `run_with_timeout.py`
    makes. Never raises; the tree may already be gone."""
    try:
        if _on_windows():
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           timeout=BOARD_REAP_S)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _bounded_gh(budget_s=None, call_timeout_s=None):
    """A `gh` runner for `sources.GitHubSource` that cannot hang a boundary: every call has a
    timeout, a call that overruns is killed with its whole process tree and ends the write (every
    later call raises at once, `BoardCallTimeout`, never retried), and once the budget is spent every
    further call raises at once too."""
    budget = BOARD_BUDGET_S if budget_s is None else budget_s
    deadline = time.monotonic() + budget
    timeout = BOARD_CALL_TIMEOUT_S if call_timeout_s is None else call_timeout_s
    killed = []

    def run(args):
        if killed:
            raise BoardCallTimeout("an earlier board call was killed after %ss; no further calls "
                                   "this boundary" % killed[0])
        left = deadline - time.monotonic()
        if left <= 0:
            raise RuntimeError("board write budget (%ss) spent" % budget)
        wait = min(timeout, left)
        spawn = ({"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}
                 if _on_windows() else {"start_new_session": True})
        env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1")
        proc = subprocess.Popen([BOARD_GH, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", env=env, **spawn)
        try:
            out, err = proc.communicate(timeout=wait)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.communicate(timeout=BOARD_REAP_S)
            except Exception:
                pass                              # a pipe still held open: abandon it, never hang
            killed.append("%g" % wait)
            raise BoardCallTimeout("gh %s gave no answer within %gs and was killed"
                                   % (" ".join(map(str, args[:2])), wait))
        if proc.returncode != 0:
            exc = RuntimeError("gh %s failed: %s" % (" ".join(map(str, args[:2])),
                                                     (err or "").strip()[:300]))
            exc.hint = (err or "").strip()[:300]
            raise exc
        return out
    return run


def mirror_phase_to_board(sdlc_dir, goal, phase, run=None):
    """#233: write the phase the loop is ENTERING onto the goal's board card (and mirror its
    Priority). One write per boundary, from Sigma's own Python on every host. A no-op unless the
    config is github mode with `project.enabled` and a pinned `project.number`; fail-open always
    -- returns True/False, never raises, and at most one stderr line (`GitHubSource._warn_field`)."""
    try:
        cfg = _load("state").load_config(sdlc_dir)
        disc = cfg.get("discovery") or {}
        project = ((disc.get("github") or {}).get("project")) or {}
        if disc.get("source") != "github" or not project.get("enabled") \
                or not project.get("number") or not str(_load("work").stem(goal)).isdigit():
            return False
        sources = _load("sources")
        source = sources.GitHubSource(cfg, run=run or _BOARD_RUN or _bounded_gh(),
                                      sdlc_dir=str(sdlc_dir))
        source._RETRY_BASE = BOARD_RETRY_BASE
        vocabulary = [(token, PHASE_BOARD_COLORS.get(kind, "GRAY")) for kind, token in PHASE_TOKENS]
        return bool(source.set_board_phase(_load("work").stem(goal), phase_token(phase), vocabulary))
    except Exception as exc:                                  # the board is a mirror, never a gate
        try:
            print("sigma: board Phase/Priority not written - %s. The goal continues unaffected."
                  % str(exc)[:300], file=sys.stderr)
        except Exception:
            pass
        return False


def _recorded_attempt_kinds(ledger, sdlc_dir, attempt_id):
    """Find a retry's durable journal record without loading the whole ledger into memory.

    Only called after the budget cursor says this attempt was already credited. A crash between
    the cursor publish and either event append can then repair just the missing event. Scanning
    is O(total event bytes) on this exceptional retry path and O(1) memory; ordinary phase ends
    never pay for it.

    BOTH destinations are read, UNIONED, never routed (#2574/S1-G3). The journal writes to
    `.sdlc/events/`; `.sdlc/ledger/events/` holds one release of legacy history. The crash this
    repairs can itself straddle the move — the `phase` event written by the old destination, the
    `spend` event by the new one — and a routed read would then re-emit a twin for an event that
    is already durable. The gate is `journal_on`, so an org lock is honoured the same way the
    writer honours it.
    """
    try:
        config = _load("state").load_config(sdlc_dir)
        if not ledger.journal_on(sdlc_dir, config):
            return set()
        paths = list(ledger.local_events_dir(sdlc_dir).glob("*.jsonl")) \
            + list(ledger.entries_dir(sdlc_dir, stream=ledger.EVENTS).glob("*.jsonl"))
    except (OSError, ValueError):
        return set()
    found = set()
    for path in paths:
        try:
            with path.open(encoding="utf-8-sig", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("attempt_id") != attempt_id:
                        continue
                    if event.get("kind") == "phase" and event.get("state") == "end":
                        found.add("phase")
                    elif event.get("kind") == "spend":
                        found.add("spend")
                    if found == {"phase", "spend"}:
                        return found
        except OSError:
            continue
    return found


def _record_end_usage(sdlc_dir, goal, phase, marker, result, agent_id=None,
                      interval_ms=None, stale=False):
    """Credit and reconcile one phase end under one cross-process event lock.

    `stale` (#2667): true when `end`'s identity check found this same-phase marker untrustworthy.
    EXACT reads -- `agent_id` given: a dispatched Claude subagent, or a validated Codex child --
    never touch the attempt key or the epoch passed to `state.record_phase_end`, because their
    measurement does not depend on the marker's window at all. That is rev 3.1's retry-idempotency
    fix (plan-review round 3, finding 1): with the stale suffix applied unconditionally (crossing
    the identity boundary on retry -- trusted first, stale after), a retried exact `end` DOUBLE-
    CREDITED (measured on this fix's own mutation control, Task 5: a $12.00 exact-subagent phase
    ended trusted then twice more stale gave 2 `spend` events at 1200 cents each, where exactly
    one is correct) -- HEAD's own de-dup (by epoch + agent_id) and its old-run journal scan (a
    phase started before this run cannot charge its budget, and a re-ended attempt from an OLDER
    run is caught by `_recorded_attempt_kinds` instead of being double-written) both depend on the
    REAL epoch
    reaching `record_phase_end` unchanged, so the exact paths keep it. Only the WINDOWED paths (no
    agent_id) need a distinct key: `\0stale` marks the attempt so a retried end can never be
    mistaken for the crashed writer's own end (see the plan's departures section), and
    `time.time()` replaces the dead epoch so the run comparison uses the CURRENT run's own instant
    instead of a start time that may belong to a run already gone."""
    state = _load("state")
    have_identity_marker = (marker is not None and marker.get("phase") == phase
                           and marker.get("ts_start_epoch") is not None)
    exact = bool(agent_id) and have_identity_marker
    windowed_stale = stale and not exact
    if have_identity_marker:
        attempt = f"{goal}\0{phase}\0{marker['ts_start_epoch']}\0{agent_id or ''}"
        if windowed_stale:
            attempt += "\0stale"
        record_started_at = time.time() if windowed_stale else marker["ts_start_epoch"]
    else:
        attempt = None
        record_started_at = None
    with state.phase_end_lock(sdlc_dir, attempt or f"{goal}\0{phase}"):
        measured = result["source"] in ("claude-code", "claude-code-inline", "codex")
        ledger = _load("ledger")
        budget_tokens = result.get("cost_equivalent_tokens")
        codex_raw_tokens = (result["tokens_in"] + result["tokens_out"]
                            if result["source"] == "codex" else None)
        # The marker-to-end interval is measured once in cmd_end. Omit it when no matching
        # marker exists; zero would claim an instantaneous phase that was not measured.
        timing = {"ms": interval_ms} if interval_ms is not None else {}
        attempt_id = state.phase_attempt_key(attempt) if attempt is not None else None
        if attempt is not None:
            new_end, _, old_run = state.record_phase_end(
                sdlc_dir, attempt, record_started_at, budget_tokens,
                codex_raw_tokens=codex_raw_tokens)
            recorded = (_recorded_attempt_kinds(ledger, sdlc_dir, attempt_id)
                        if not new_end or old_run else set())
        else:
            # A missing/mismatched marker cannot identify a retry. Preserve best-effort credit,
            # alongside cmd_end's warning that its transcript window may be inaccurate.
            if budget_tokens is not None:
                state.add_tokens(sdlc_dir, budget_tokens)
            recorded = set()

        if "phase" not in recorded and measured:
            ledger.safe_append(
                sdlc_dir, "phase", goal, stream=ledger.EVENTS, phase=phase, state="end",
                tokens_in=str(result["tokens_in"]), tokens_out=str(result["tokens_out"]),
                attempt_id=attempt_id, **timing,
            )
        elif "phase" not in recorded:
            ledger.safe_append(sdlc_dir, "phase", goal, stream=ledger.EVENTS, phase=phase,
                               state="end", attempt_id=attempt_id, **timing)

        # #1686: the `phase` event above carries real tokens_in/tokens_out but never the already-built
        # `spend` kind (EVENT_FIELDS["spend"] = ("phase", "model", "tokens_in", "tokens_out",
        # "cost_cents")) -- the one kind that also carries the observed model and a priced dollar
        # figure. Written directly via `ledger.safe_append`, not through `loop.py spend`'s CLI verb:
        # a priced current-run phase was already credited above with its dedupe key.
        #
        # Only written once a real dollar cost is known (`cost_usd is not None`): Codex is never
        # priced (no per-turn model -- see `collect_phase_usage`'s own docstring) and an `unavailable`
        # result has no model or cost to report at all -- writing either would fabricate an empty
        # model or a $0.00 that was never measured, exactly what this module's "never fabricate" rule
        # forbids.
        cost_usd = result.get("cost_usd")
        if cost_usd is not None and not result.get("unpriced_turns") and "spend" not in recorded:
            ledger.safe_append(
                sdlc_dir, "spend", goal, stream=ledger.EVENTS, phase=phase,
                model=",".join(result.get("models") or []),
                tokens_in=str(result["tokens_in"]), tokens_out=str(result["tokens_out"]),
                cost_cents=str(round(cost_usd * 100)), attempt_id=attempt_id,
            )

        # #2515 (Decision 2): budget credit is local and independent of `journal.enabled`; an
        # unpriced phase never falls back to raw tokens_in + tokens_out ("skip, never guess").
        # A crash after the cursor publish can omit a ledger event; a retry scans and repairs it
        # under the same attempt lock without charging the budget again.



def cmd_end(argv):
    if len(argv) < 3:
        print("usage: phase_report.py end <sdlc_dir> <goal> <phase> [--agent-id ID] "
              "[--pid PID] [--title T]",
              file=sys.stderr)
        return 2
    sdlc_dir, goal, phase = argv[0], argv[1], argv[2]
    err = _check_phase(phase)
    if err:
        print(f"phase_report.py: {err}", file=sys.stderr)
        return 2
    flags = _flags(argv[3:])
    agent_id = flags.get("agent-id")

    # A malformed `--pid` WARNS and falls back rather than refusing (departure 3): unlike
    # `start`, `end` is the boundary that has already paid for its measurement, and refusing here
    # would drop the banner and the `phase` ledger event outright. Falling back to "no identity"
    # still catches a dead writer through the fallback branch of `stale_marker_reason`.
    caller_pid, pid_err = _caller_pid(flags)
    if pid_err:
        print(f"phase_report: warning: {pid_err}; treating this end's identity as absent "
              f"(falling back to the dead-writer/lease check rather than refusing)",
              file=sys.stderr)
        caller_pid = None
    codex_thread = _caller_codex_thread()
    claude_session = _session_id() or None

    marker = read_marker(sdlc_dir, goal)
    if marker is not None and marker.get("phase") != phase:
        # #2658: a marker for a DIFFERENT phase is dropped here, on the spot, so everything below
        # takes the identical path a missing marker takes. It carries no lower bound for THIS
        # phase that is better than "now" -- the phase being ended started at some unknown instant
        # after that other phase did -- so every field it could contribute is a fabrication.
        #
        # Keeping it "best-effort" was a real defect, not a cosmetic one. `interval_ms` below was
        # guarded by a `phase_matches` flag, but `since_ts` never was: the stale `ts_start` windowed
        # `collect_phase_usage`, so every turn since the abandoned phase began was billed to this
        # one -- and STORED, in the `phase`/`spend` ledger events and in `STATE.md`'s `run_tokens`
        # budget cursor. Measured live against a 30-day-old `retro` marker on goal #2577, ending
        # `review`: `720h00m · $9.01 · tokens 901,000 out`, `run_tokens: 4507000`, and the dead
        # phase's own title on the live phase's banner. Markers are never pruned by `end` (it stays
        # re-runnable, see the `interval_ms` comment below), and before #2658 nothing pruned them
        # at all, so this is reached by ordinary staleness rather than by caller error.
        #
        # The warning's PREFIX is unchanged (`marker phase 'X' != 'Y'` -- what two tests pin, and
        # what `step` still prints for the same condition); only its tail moved, from a
        # "best-effort" it no longer attempts to what actually happens. Exit stays 0: a stale
        # marker must not halt a run, and the measurement is now honestly absent rather than
        # quietly wrong. The missing-marker warning below then prints too, and both are true --
        # there is no marker for THIS phase, and `start` is the remedy.
        print(f"phase_report: warning: marker phase {marker.get('phase')!r} != {phase!r} "
              f"(stale marker ignored; this phase is reported unmeasured)", file=sys.stderr)
        marker = None

    # #2667: a marker for the SAME phase is no longer trusted on age alone. `window` is the
    # marker this `end` may use for its TIME fields (since_ts/started_at/interval_ms/the run
    # comparison); `marker` itself stays intact either way, because the GATES (expect_agent_id,
    # host_model, tier) must still fire on a wrong model even when the window is thrown out
    # (D2) -- see `stale_marker_reason` for the identity rule and its own "NOT COVERED" note.
    stale_reason = None
    if marker is None:
        # Code review, 2026-08-25: without a marker, `since_ts` has nothing better than "now" to
        # fall back to, which windows out every real turn in an inline transcript that DOES exist
        # -- collect_phase_usage then has no way to tell "no source" from "source found, window
        # was just empty" apart, and reports the former. SKILL.md always calls `start` before
        # `end`, so this is a caller-error path, not a normal one; surfacing it on stderr (rather
        # than only via a possibly-misleading "no transcript" reason on stdout) is the fix.
        print(f"phase_report: warning: no phase-start marker found for {phase!r} on goal {goal!r} "
              f"(call phase_report.py start first) — an inline phase cannot be windowed "
              f"accurately without it; proceeding best-effort", file=sys.stderr)
        window = None
    else:
        stale_reason = stale_marker_reason(marker, caller_pid, codex_thread,
                                           lambda: _fallback_ttl_seconds(sdlc_dir),
                                           claude_session_id=claude_session)
        if stale_reason:
            print(f"phase_report: warning: the {phase!r} marker for goal {goal!r} is stale "
                  f"({stale_reason}); its start time is ignored for this end -- a dispatched "
                  f"subagent's or a validated Codex child's own tokens still price exactly -- "
                  f"call phase_report.py start again when resuming a phase", file=sys.stderr)
            window = None
        else:
            window = marker
    no_elapsed = "stale phase-start marker" if stale_reason else "no phase-start marker"

    # The title `start` already resolved and stamped, so `end` performs NO lookup in the common
    # case; an explicit --title still wins, and a marker written before #2100 (no `title` key)
    # falls through to the same local-only resolution `start` uses. Nothing here reaches a network.
    # Read off `marker`, not `window`: a stale marker still names the same goal's title (D2).
    title = resolve_title(sdlc_dir, goal,
                          flags.get("title") or (marker or {}).get("title"))
    started_at = marker_started_at(window) if window is not None else None
    # ONE subtraction, used twice: the banner below rounds it to whole seconds, the timing store
    # keeps its milliseconds. Two `time.time()` calls would let a phase print `0s` while recording
    # a duration measured at a later instant.
    elapsed_seconds = (time.time() - started_at) if started_at is not None else None
    elapsed = format_elapsed(elapsed_seconds)

    # The working-time record. UNCONDITIONAL by design — it precedes the journal-gated ledger
    # writes below and reads no config of its own, because every event store in the kit is opt-in
    # and a timing feature that needs one switched on measures nothing on a stock install. It also
    # runs BEFORE `collect_phase_usage`, so a failure to read a transcript never costs the
    # duration.
    #
    # Omitted, never zeroed, when the interval is not this phase's to claim — `window is None`,
    # which now covers THREE cases: no marker at all, a marker for a different phase (#2658,
    # dropped up at the read), and (#2667) a same-phase marker that failed its identity check.
    # `started` carries the start epoch as the idempotency key a repeated `end` is de-duplicated
    # on, since this command never consumes the marker — the goal's terminal `loop.py record` does.
    interval_ms = (int(round(elapsed_seconds * 1000))
                   if (elapsed_seconds is not None and elapsed_seconds >= 0 and window is not None)
                   else None)
    # Bound once for both uses below. NOT named `timing`: the ledger write further down already
    # binds `timing` to its kwargs dict, and a first draft of this line reused the name — the
    # totals read then hit a dict, the fail-open swallow hid it, and the banner's `goal so far`
    # field silently vanished from every phase end until one CLI test noticed.
    store = _load("timing_store")
    if interval_ms is not None:
        # `started` in MILLISECONDS — the idempotency key `_deduplicate` collapses a repeated end
        # on; whole seconds would let two same-phase starts inside one second collide (N3).
        store.safe_append(sdlc_dir, goal, "phase", phase, interval_ms,
                          started=int(round(started_at * 1000)))

    since_ts = window["ts_start"] if window is not None else time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _collect(session_id, aid, codex_session_id, since=None):
        """Wraps `collect_phase_usage` (D2): a stale marker degrades every WINDOWED read to an
        honest `unavailable`, EXCEPT the two paths that never needed the marker's dead window at
        all -- the Claude subagent transcript (`session_id` and `aid` together: read WHOLE, never
        windowed, see `collect_phase_usage`'s own docstring) and a validated Codex child bounded
        by its OWN creation time (`since` passed explicitly by the caller below, never derived
        from the stale marker)."""
        exempt = bool(session_id and aid) or since is not None
        if stale_reason and not exempt:
            return {"source": "unavailable",
                    "reason": f"stale phase-start marker: {stale_reason}"}
        return collect_phase_usage(session_id, aid, since if since is not None else since_ts,
                                   codex_session_id=codex_session_id)

    codex_id = _codex_session_id()
    child_since = None
    if (marker or {}).get("expect_agent_id") and codex_id and not _session_id():
        child_id = _codex_agent_id(agent_id)
        if child_id is None:
            result = {"source": "unavailable", "reason": "dispatched Codex phase needs a valid "
                      "agent ID from the child; run phase_report.py codex-agent-id there"}
        elif child_id == _codex_agent_id(codex_id):
            result = {"source": "unavailable", "reason": "dispatched Codex phase agent ID "
                      "matches the orchestrator, not a child rollout"}
        else:
            # #2667/D2: bounded by the CHILD's own creation time when stale, never the dead
            # marker's window -- nothing of the crashed writer's window reaches this read.
            child_since = _codex_child_since(child_id) if stale_reason else None
            result = _collect(_session_id(), child_id, codex_id, since=child_since)
    else:
        result = _collect(_session_id(), agent_id, codex_id)

    codex_budget = False
    if codex_id and not _session_id():
        config = _load("state").load_config(sdlc_dir)
        codex_budget = bool((config.get("budget") or {}).get("max_codex_raw_tokens"))
    # `credit`: the marker-shaped object `uncredited_codex_budget` may trust for the ceiling
    # gate. `window` when the marker was trusted; else `marker` itself when a validated Codex
    # child was measured EXACTLY despite the marker reading stale (its tokens are real, so the
    # ceiling can still be enforced) -- never `marker` for any other stale Codex end, which is
    # exactly the "cannot be enforced ... without a matching start marker" refusal below.
    credit = window if window is not None else (
        marker if (child_since is not None and result.get("source") == "codex") else None)
    uncredited_codex_budget = bool(codex_budget and
                                   (credit is None or credit.get("ts_start_epoch") is None or
                                    result.get("source") != "codex"))

    expected_model = (marker or {}).get("host_model")
    observed_models = result.get("models") or []
    model_mismatch = bool(expected_model and
                          (result.get("source") != "codex" or
                           not observed_models or
                           result.get("unknown_model_turns", 0) > 0 or
                           any(model != expected_model for model in observed_models)))
    # Claude accepts tier aliases at dispatch, while its transcript records full model IDs.
    # Check only when an actual Claude transcript is available; cloud/no-transcript installs
    # retain their existing best-effort path rather than failing on an unobservable model.
    tier = (marker or {}).get("model", "").lower()
    if (not expected_model and tier in ("haiku", "sonnet", "opus", "fable")
            and result.get("source") == "claude-code"
            and observed_models):
        model_mismatch = any(tier not in model.lower() for model in observed_models)

    # A phase-end event records a boundary, not a successful gate verdict. Wrong-model measured
    # usage is still a real end and consumed budget. An unmeasured Codex attempt under a configured
    # ceiling is left untouched so a retry of this same marker can recover its measured event.
    if not uncredited_codex_budget:
        _record_end_usage(sdlc_dir, goal, phase, marker, result, agent_id,
                          interval_ms=interval_ms, stale=bool(stale_reason))

    # Read AFTER this phase's own interval was written above, or every banner would be one phase
    # stale. Fail-open: a totals read that cannot complete costs the extra field, never the banner.
    try:
        goal_totals = store.totals(sdlc_dir, goal)
    except Exception as exc:                # noqa: BLE001 - a reporting extra never breaks `end`
        # Fail-open, but never SILENT: one stderr line, the way `safe_append` reports a skipped
        # write. A silent swallow here hid a name-collision bug for a whole review pass.
        print(f"phase_report: goal totals unavailable (non-fatal): {exc}", file=sys.stderr)
        goal_totals = None

    # #2515 (Decision 2): the budget cursor is already fed the real, measured cost-equivalent
    # token count above, inside `_record_end_usage` -- via `state.record_phase_end`'s own
    # idempotent `run_token_credits` guard when a phase-attempt marker identifies this end (the
    # common case), or its `state.add_tokens` fallback when no marker can (the untracked case).
    # A second, unconditional `add_tokens` call here would double-credit the SAME measured spend
    # on every ordinary phase end -- confirmed live (run_tokens landed at 12_000_000 instead of
    # the correct 6_000_000 for a single $12.00 phase) before this comment replaced that call.

    ref = goal_ref(goal)
    emit(end_facts(ref, phase, title, elapsed, result, totals=goal_totals, no_elapsed=no_elapsed),
         end_lines(ref, phase, title, elapsed, result, totals=goal_totals, no_elapsed=no_elapsed))
    if model_mismatch:
        print(f"phase_report.py: phase model unverified/mismatched: expected "
              f"{expected_model or tier!r}, observed {observed_models or 'unavailable'}; "
              "do not treat this phase as model-compliant", file=sys.stderr)
        return 2
    if uncredited_codex_budget:
        print("phase_report.py: Codex raw-token ceiling cannot be enforced for this phase "
              "without a matching start marker and measured child rollout; stop this run",
              file=sys.stderr)
        return 2
    return 0


def cmd_step(argv):
    if len(argv) < 3:
        print("usage: phase_report.py step <sdlc_dir> <goal> <phase> --num N [--total M] "
              "--brief B [--model M] [--phase-model | --host-model HOST_ID] [--title T]",
              file=sys.stderr)
        return 2
    sdlc_dir, goal, phase = argv[0], argv[1], argv[2]
    err = _check_phase(phase)
    if err:
        print(f"phase_report.py: {err}", file=sys.stderr)
        return 2
    flags = _flags(argv[3:])
    if flags.get("host-model") and not _safe_host_model(flags["host-model"]):
        print("phase_report.py: --host-model must be a single printable field of at most 256 "
              "characters", file=sys.stderr)
        return 2
    if flags.get("phase-model") == "true" and (flags.get("host-model") or flags.get("model")):
        print("phase_report.py: --phase-model cannot accompany a step model override; "
              "use --model with --host-model for a separate dispatch", file=sys.stderr)
        return 2
    if flags.get("host-model") and not flags.get("model"):
        print("phase_report.py: a separately dispatched step needs both --host-model and --model",
              file=sys.stderr)
        return 2

    raw_num = flags.get("num")
    if not raw_num:
        print("phase_report.py: step requires --num N", file=sys.stderr)
        return 2
    try:
        step_num = int(raw_num)
    except ValueError:
        print(f"phase_report.py: --num must be a positive integer, got {raw_num!r}", file=sys.stderr)
        return 2
    if step_num < 1:
        print(f"phase_report.py: --num must be a positive integer, got {step_num}", file=sys.stderr)
        return 2

    step_total = None
    raw_total = flags.get("total")
    if raw_total is not None:
        try:
            step_total = int(raw_total)
        except ValueError:
            print(f"phase_report.py: --total must be a positive integer, got {raw_total!r}",
                  file=sys.stderr)
            return 2
        if step_total < 1:
            print(f"phase_report.py: --total must be a positive integer, got {step_total}",
                  file=sys.stderr)
            return 2
        if step_num > step_total:
            print(f"phase_report.py: --num ({step_num}) cannot exceed --total ({step_total})",
                  file=sys.stderr)
            return 2

    brief = clean_title(flags.get("brief"))
    if not brief:
        print("phase_report.py: --brief must be non-empty", file=sys.stderr)
        return 2

    marker = read_marker(sdlc_dir, goal)
    if marker is None:
        print(f"phase_report: warning: no phase-start marker found for {phase!r} on goal {goal!r} "
              f"(call phase_report.py start first) -- proceeding best-effort", file=sys.stderr)
    elif marker.get("phase") != phase:
        print(f"phase_report: warning: marker phase {marker.get('phase')!r} != {phase!r} "
              f"(proceeding anyway, best-effort)", file=sys.stderr)

    tier = step_tier(flags.get("model"), marker, phase)
    host_model = step_host_model(flags.get("host-model"),
                                 flags.get("phase-model") == "true", marker, phase)
    title = resolve_title(sdlc_dir, goal, flags.get("title") or (marker or {}).get("title"))
    for line in step_lines(goal_ref(goal), phase, tier, title, step_num, step_total, brief,
                           host_model):
        print(line)
    return 0


def cmd_codex_agent_id(argv):
    """Child-side handoff of its own rollout ID; never substitute the inherited parent session."""
    if argv:
        print("usage: phase_report.py codex-agent-id", file=sys.stderr)
        return 2
    agent_id = _codex_agent_id(os.environ.get("CODEX_THREAD_ID"))
    if agent_id is None:
        print("phase_report.py: CODEX_THREAD_ID is missing or invalid; cannot identify this "
              "Codex agent rollout", file=sys.stderr)
        return 2
    print(agent_id)
    return 0


USAGE = ("usage: phase_report.py start|end|step <sdlc_dir> <goal> <phase> [flags] "
         "| codex-agent-id")


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    verb = argv[1]
    rest = argv[2:]
    if verb == "start":
        return cmd_start(rest)
    if verb == "end":
        return cmd_end(rest)
    if verb == "step":
        return cmd_step(rest)
    if verb == "codex-agent-id":
        return cmd_codex_agent_id(rest)
    print(f"phase_report.py: unknown verb {verb!r} (expected start, end, step, or codex-agent-id)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    # The script-time floor: this run's wall time, recorded to the session resolved at exit.
    sys.exit(_load("timing_store").timed_main(main, sys.argv, "phase_report"))

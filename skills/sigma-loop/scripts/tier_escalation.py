#!/usr/bin/env python3
"""#2828: a review send-back that the current model tier cannot converge is escalated ONE tier up,
never parked for "budget" or "tier too small".

THE BUG. A goal was routed to `haiku`, plan-review sent its plan back, and the worker parked the goal
for a human ("token budget constraints in this haiku-tier slot"). Nothing in the kit said what to do
when a tier proves too small for a revision, so the worker invented a park. A park means "needs a
human decision"; a tier that is too small needs a bigger tier, which the loop can pick itself.

THE RULE, as code rather than prose, so every host gets the same answer:

  - the ladder is `haiku -> sonnet -> opus`. `fable` is never a target: it is the creative tier and
    the most expensive one (#2564), not a "more capable at revising a plan" tier. A `fable` goal is
    already at the top.
  - the next rung is allowed only when its price rank is at or below the repo's price ceiling
    (`model_selection_max_tier`, default `opus`) -- the same ceiling the router honours.
  - `model_selection` not `"auto"` -> phases are not tier-routed, so there is no tier to raise.
  - bounded: the ladder has two rungs, and the tier escalated from is the HIGHER of the caller's
    `<current-tier>` and this goal's recorded escalation FLOOR (`effective_tier`), so a caller that
    keeps passing its original `haiku` -- a resumed session, an orchestrator that lost context --
    reaches the ceiling instead of escalating forever. The floor is CONTROL STATE, not the journal:
    `<sdlc>/state/escalation/<goal>.json`, written on every escalation whatever `action_log`,
    `ledger` or `journal` say (`journal` and `ledger` are off by default; `action_log` ships on
    from `/sigma-init` and is off when its key is absent). The action log's own escalation
    rows are consulted too, as a second source. The floor is ALWAYS clamped to the current price
    ceiling when read, so a floor saved under a higher cap can never run a goal above a cap the
    operator has since lowered.
  - growth: one file of under 100 bytes per goal that was ever escalated, never pruned on purpose --
    it is the goal's memory across resumes and reopenings, the same lifetime as that goal's
    `state/log/` trace. At 10x/100x goals it is 10x/100x tiny files, each read by exact path, never
    scanned. One writer per goal (a goal has one slot) is what orders writes; `write_floor` also
    refuses a later, lower write, though its read-then-rename is not a lock, so two truly
    simultaneous first writers could still leave the lower tier.

WHY A COPY OF THE CEILING VOCABULARY, NOT AN IMPORT. `skills/sigma-model/scripts/predict.py` owns
the price order and the ceiling's normalization. Skills do not import each other's Python, so this
module mirrors the three constants and `ceiling()` mirrors `predict.max_tier` exactly;
tests/test_tier_escalation.py::test_the_ceiling_vocabulary_matches_the_router_it_mirrors pins the
copy against the original, so a change there turns CI red instead of silently disagreeing here.

WHAT THIS CANNOT DO. It decides and records; it cannot make a host dispatch at the tier it names.
Claude's Task tool and Codex's host model ID can; a host with no per-subagent model override
(Cursor) cannot, and there the answer is advisory -- see skills/sigma-loop/references/running.md."""
#: Mirrors predict.py's `_TIER_PRICE_ORDER`, `_MAX_TIER_DEFAULT`, `_MAX_TIER_KEY` (pinned by test).
PRICE_ORDER = ("haiku", "sonnet", "opus", "fable")
MAX_TIER_DEFAULT = "opus"
MAX_TIER_KEY = "model_selection_max_tier"

#: The escalation ladder. Deliberately stops at opus -- see the module docstring on `fable`.
LADDER = ("haiku", "sonnet", "opus")

#: Which review sent the work back -> the phase the re-dispatched fix runs as. A fix after a code or
#: PR review is still the implement phase (references/running.md: "never a new phase name").
AFTER_PHASE = {"plan-review": "plan", "code-review": "implement", "pr-review": "implement"}

ESCALATE, CEILING, OFF = "ESCALATE", "CEILING", "OFF"

#: Reasoning effort per tier, the mapping references/running.md gives for a phase dispatch.
EFFORT = {"haiku": "low", "sonnet": "medium", "opus": "high", "fable": "high"}

#: The prefix every escalation's `model_choice` signal starts with (see `signal`).
SIGNAL_PREFIX = "escalated: "


def ceiling(cfg):
    """The normalized price ceiling from a parsed config mapping. Mirrors `predict.max_tier`: junk
    (a list, a number, a typo) keeps the DEFAULT ceiling rather than uncapping."""
    try:
        raw = cfg.get(MAX_TIER_KEY)
    except AttributeError:
        return MAX_TIER_DEFAULT
    if not isinstance(raw, str):
        return MAX_TIER_DEFAULT
    tier = raw.strip().lower()
    return tier if tier in PRICE_ORDER else MAX_TIER_DEFAULT


def next_tier(current, cap):
    """The tier one rung above `current`, or None when there is none within `cap`.
    Raises ValueError for a `current` that is not a known tier."""
    if current not in PRICE_ORDER:
        raise ValueError(f"unknown tier {current!r} (expected one of {', '.join(PRICE_ORDER)})")
    if current not in LADDER or current == LADDER[-1]:
        return None
    nxt = LADDER[LADDER.index(current) + 1]
    return nxt if PRICE_ORDER.index(nxt) <= PRICE_ORDER.index(cap) else None


def signal(after, current):
    """The `model_choice` signal an escalation is recorded with -- WHY this tier."""
    return f"{SIGNAL_PREFIX}{after} send-back at {current}"


def recorded_tier(entries):
    """The tier of the newest escalation in one goal's action-log entries, or None. Only rows this
    verb wrote count (a `model_choice` whose signal carries `SIGNAL_PREFIX`); a pick-time or
    per-step `model_choice` is a prediction, not an escalation, and must not raise the floor."""
    for e in reversed(entries or []):
        if (isinstance(e, dict) and e.get("kind") == "model_choice"
                and str(e.get("signal") or "").startswith(SIGNAL_PREFIX)
                and e.get("model") in PRICE_ORDER):
            return e["model"]
    return None


def effective_tier(current, entries, floor=None):
    """The highest-priced of the caller's tier, the newest action-log escalation, and the floor."""
    best = current
    for rec in (recorded_tier(entries), floor if floor in PRICE_ORDER else None):
        if rec is not None and PRICE_ORDER.index(rec) > PRICE_ORDER.index(best):
            best = rec
    return best


def from_tier(config, current, entries=(), floor=None):
    """The tier a send-back escalates FROM: `effective_tier`, clamped to the price ceiling."""
    cap = ceiling(config)
    tier = effective_tier(current, entries, floor)
    return cap if PRICE_ORDER.index(tier) > PRICE_ORDER.index(cap) else tier


def shown_tier(config, entries=(), floor=None):
    """What `--show` prints: the goal's escalated ceiling, clamped, or None (no escalation, or
    `model_selection` not "auto" -- then there is no tier-driven dispatch to raise at all)."""
    if (config.get("model_selection") or "off") != "auto":
        return None
    if recorded_tier(entries) is None and floor not in PRICE_ORDER:
        return None
    return from_tier(config, "haiku", entries, floor)


#: Mirrors `discovery.py`'s `LANES` (#390) -- not imported, same "skills don't import each other's
#: Python" boundary `PRICE_ORDER` above already crosses for `predict.py`'s vocabulary. Pinned
#: against the original by test_the_lane_vocabulary_matches_the_researcher_it_mirrors, exactly like
#: the ceiling constants above are pinned against predict.py's.
LANES = ("small", "medium", "large")

#: The `model_choice` signal prefix a lane re-resolve records with (#390) -- so `recorded_tier`
#: (which looks only for `SIGNAL_PREFIX`, "escalated: ") never mistakes a lane cap for a genuine
#: review-triggered escalation, and a lane re-resolve can never itself raise the escalation floor.
LANE_SIGNAL_PREFIX = "lane:"


def lane_signal(lane):
    """The `model_choice` signal a lane re-resolve is recorded with -- WHY this tier (#390)."""
    return f"{LANE_SIGNAL_PREFIX}{lane}"


def lane_tier(config, lane, current, entries=(), floor=None):
    """#390: the tier P3+ actually runs at, re-resolved from Research's MEASURED lane rather than
    only the pre-P2 text signal `current` carries.

      - small  -> `current` capped at sonnet. A CAP, not a floor: a title-only haiku match (below
        sonnet already) is left alone -- a lane never pushes a goal UP, only narrows an
        overconfident text match down.
      - medium -> sonnet outright, whatever `current` says. Matches `predict.py`'s own `_DEFAULT`
        -- the ceremony a goal this size earns regardless of what its wording guessed.
      - large  -> opus outright, regardless of wording -- the issue's own red-first control: a
        structural, multi-module or new-source-of-truth goal earns the top tier even when nothing
        in its text says so.

    `entries`/`floor` are the SAME escalation-floor inputs `from_tier`/`shown_tier` read -- a
    genuine review-triggered escalation recorded before this call (e.g. research re-run after a
    park/resume) must still win over a lane cap, never be silently downgraded by one. This is
    DELIBERATELY NOT `from_tier(config, current, entries, floor)`: that helper's own
    `effective_tier` starts its max at `current`, so with NO recorded escalation at all
    `from_tier(config, "opus", (), None)` still returns `"opus"` -- combining with THAT would
    re-inject the very un-capped text tier this lane rule exists to correct (caught in plan
    review, #390). Instead the escalation side of the max is computed from escalation MEMORY
    ALONE, baselining `effective_tier` at the lowest tier so only a genuine `recorded_tier`/
    `floor` can raise it:

        esc = effective_tier(PRICE_ORDER[0], entries, floor)

    The final tier is the higher-priced of the lane result and this escalation-only `esc`, clamped
    to the price ceiling last (the lane side, e.g. a `large`-lane `opus`, may still exceed a
    lowered ceiling even though `esc` was already clamped by construction).

    Raises `ValueError` for an unrecognised `lane` or `current`, mirroring `next_tier`/`decide`."""
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane!r} (expected one of {', '.join(LANES)})")
    if current not in PRICE_ORDER:
        raise ValueError(f"unknown tier {current!r} (expected one of {', '.join(PRICE_ORDER)})")
    cap = ceiling(config)
    if lane == "small":
        lane_capped = current if PRICE_ORDER.index(current) <= PRICE_ORDER.index("sonnet") \
            else "sonnet"
    elif lane == "medium":
        lane_capped = "sonnet"
    else:                                                           # "large"
        lane_capped = "opus"
    esc = effective_tier(PRICE_ORDER[0], entries, floor)
    esc = cap if PRICE_ORDER.index(esc) > PRICE_ORDER.index(cap) else esc
    best = lane_capped if PRICE_ORDER.index(lane_capped) > PRICE_ORDER.index(esc) else esc
    return cap if PRICE_ORDER.index(best) > PRICE_ORDER.index(cap) else best


def read_floor(path):
    """The recorded escalation floor at `path`, or None. Unreadable/malformed reads as no floor --
    the worst case is one extra rung re-requested, never a crash on the way to a revision."""
    import json
    try:
        tier = json.loads(path.read_text(encoding="utf-8")).get("tier")
    except Exception:                               # noqa: BLE001 - see docstring
        return None
    return tier if tier in PRICE_ORDER else None


def write_floor(path, tier, after, frm):
    """Persist the floor atomically (temp file + rename), so a crash mid-write leaves the previous
    floor or none, never a torn file. Refuses to lower or rewrite an existing floor. Raises OSError; the caller decides what that means."""
    import json, os
    prior = read_floor(path)
    if prior is not None and PRICE_ORDER.index(prior) >= PRICE_ORDER.index(tier):
        return                                      # never lower (or rewrite) a floor
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"tier": tier, "signal": signal(after, frm)}) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def decide(config, current, after, entries=(), floor=None):
    """(verdict, tier, message) for a send-back at `current`. Pure: reads only the given config,
    the goal's action-log `entries` and its recorded `floor`. Raises ValueError on an unknown tier
    or `after`."""
    if after not in AFTER_PHASE:
        raise ValueError(f"unknown --after {after!r} (expected one of {', '.join(AFTER_PHASE)})")
    if current not in PRICE_ORDER:
        raise ValueError(f"unknown tier {current!r} (expected one of {', '.join(PRICE_ORDER)})")
    if (config.get("model_selection") or "off") != "auto":
        return (OFF, current, 'model_selection is not "auto": phases are not tier-routed, so '
                "there is no tier to raise. Keep the existing fix/re-review cycle at this model; "
                "park or fail only if that does not converge.")
    current = from_tier(config, current, entries, floor)
    cap = ceiling(config)
    nxt = next_tier(current, cap)
    if nxt is None:
        return (CEILING, current, f"no tier above {current} within the price ceiling "
                f"({MAX_TIER_KEY}={cap}). Keep the existing fix/re-review cycle at {current}; park or "
                f"fail only if that does not converge.")
    return (ESCALATE, nxt, f"re-dispatch the {AFTER_PHASE[after]} phase fresh at {nxt} "
            f"(effort {EFFORT[nxt]}); it is this goal's ceiling from here on. Not a park.")


def record(sdlc_dir, goal, config, current, nxt, after, ledger, actionlog):
    """Write the escalation to the ledger EVENTS stream and the local action log. Both writes are
    the existing fail-open `safe_append`s, so a recording problem never changes the answer.
    The action-log row is written as actor `agent`: the tier it escalates FROM is agent-supplied,
    and `model_choice` is an agent-set kind everywhere it is rendered (log.py, render.py)."""
    why = signal(after, current)
    ledger.safe_append(sdlc_dir, "model_choice", goal, config=config, stream=ledger.EVENTS,
                       model=nxt, signal=why)
    actionlog.safe_append(sdlc_dir, goal, "model_choice", actor="agent", model=nxt,
                          phase=AFTER_PHASE[after], signal=why)


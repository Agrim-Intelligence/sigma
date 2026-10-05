#!/usr/bin/env python3
"""dossier.py (#1824) -- the engine behind `sigma-dossier`, the Business-stage entry point.

`docs/dossier-pipeline.md` §4 (Stage 0): a very high-level idea in, a strict-structure Q&A
out, one `story`-labelled ticket (the Dossier) -- never `sdlc:goal`, self-assigned automatically,
with a terminal decision the skill itself asks: file and stop, or continue to Product.

Q&A SHAPE, AND WHY IT DIFFERS FROM `unpark.py`'S. The design this came from named two house
precedents and asked for "strict predefined structure", read as closer to `skills/sigma-loop/scripts/unpark.py`'s
bank-based, persisted pattern than to `sigma-brainstorm`'s free-form, unpersisted one -- so this module
mirrors unpark's shape: an engine-owned bank (not agent-improvised prose), `{answers, questions}`
persisted to a comment AND a fenced managed block in the issue body, a reserved closing decision
question asked last. One deliberate simplification: unpark's bank is KEYED by `reason_class` with
per-slot `when` conditions, because different PARKS need different questions. A dossier has no
equivalent axis -- Stage 0 asks the identical fixed set of every idea, which is what "strict
predefined structure" means here. So `QUESTIONS` below is a flat, ordered list with no keying and no
conditioning, not a smaller copy of a feature this module is missing.

WHERE THIS DIFFERS FROM `unpark.py` STRUCTURALLY, AND WHY. Unpark edits an EXISTING issue across
possibly-many sessions, so it resumes an interrupted interview from the issue's own body
(`_recorded_answers`). A dossier's issue does not exist until `file()` succeeds -- there is nothing
to resume before that point, so an interrupted interview just starts over next time. That is a
genuinely smaller surface, not a missing feature.

WHY `_resolve_source` EXISTS RATHER THAN CALLING `sources.get_source` DIRECTLY. `get_source` picks
`GitHubSource`/`LocalSource` from `discovery.source` but takes no `run` parameter, so a caller that
wants to inject a fake `gh` runner for a test has to duplicate its branching. `unpark.py` sidesteps
this by hardcoding `GitHubSource(config, run=run)` -- it only ever runs in GitHub discovery mode. This
module supports BOTH modes (a dossier is useful even in a local-goals install: `create_dependency`'s
local counterpart already has a documented, matching "queued, not `sdlc:goal`-equivalent" contract via
`status: proposed`), so `_resolve_source` reproduces `get_source`'s own precedence and threads `run`
through the one branch that can use it.

BOUNDED FOLLOW-UPS (#1916). The bank is fixed, and a fixed bank cannot reach the semantics of one
domain -- the real E2E run produced a Dossier that was sufficient for intent and not sufficient to
design from, every gap in one place. The answer is NOT a bigger bank (that stops being a strict
predefined structure, and the third gap it hit was two answers contradicting each other, which no
extra slot catches). It is a short tail on the RECORD: at most `MAX_FOLLOWUPS` answered follow-ups,
plus an uncapped record of what Stage 0 could not settle, both as ordinary `{answers, questions}`
entries with a prefixed id -- so the persisted shape, and its interchangeability with unpark's, is
untouched.

    python3 dossier.py bank
    python3 dossier.py followups
    python3 dossier.py file <sdlc_dir> --answers <file.json> --decision stop|continue [--dry-run]
"""
import json, pathlib, sys, importlib.util

_HERE = pathlib.Path(__file__).resolve().parent


def _load_loop_script(name):
    """Cross-load a script from the sibling `sigma-loop` skill (skills/sigma-dossier/scripts/ ->
    skills/sigma-loop/scripts/<name>.py) -- the established, narrow, named exception to "don't reach
    across skill directories" (see `skills/sigma-scope/scripts/compile_plan.py`'s own
    `_load_loop_script`, `skills/sigma-define/scripts/define.py`'s copy of the same, and
    `skills/sigma-scope/scripts/brainstorm.py`'s `_load`): `sources.py` is where
    `create_dependency`'s issue-creation machinery already lives, and reimplementing it here would
    be exactly the hardened-sibling-divergence bug class this plugin's own docs already warn
    against."""
    path = _HERE.parent.parent / "sigma-loop" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sources = _load_loop_script("sources")
triage = _load_loop_script("triage")

#: The label the Dossier ticket carries. NOT in `_ensure_labels`'s bootstrap table (contract §10):
#: `create_dependency`'s own idempotent label-create mints it at attach instead, so a fresh adopter
#: with no pre-created label still gets a working first run.
STORY_LABEL = "story"

#: A dossier-scoped fence, deliberately NOT added to `blocker_scan.py`'s shared
#: `UNPARK_QA_START`/`END` registry: a Dossier ticket is never `sdlc:goal`-labelled, so
#: `backlog_check`'s blocker scan never runs over its body in the first place (that scan only ever
#: walks goal issues). The fence still avoids every `_BLOCK_RE` trigger word in its own heading, as a
#: matter of hygiene in case some future Spec/Epic ever quotes this block verbatim into a body that
#: IS scanned.
DOSSIER_QA_START = "<!-- sigma:dossier-qa:start -->"
DOSSIER_QA_END = "<!-- sigma:dossier-qa:end -->"

QA_HEADING = "## Dossier"

#: The fixed intake bank, in ask order. A FLAT list, not slotted by any classifier -- see the module
#: docstring for why that is the deliberate reading of "strict predefined structure", not a smaller
#: copy of `unpark.QUESTIONS`. Composition rules match `unpark.py`'s own (also restated in
#: `sigma-unpark/SKILL.md`): one sentence, name the concrete thing, no Sigma vocabulary, ask for a
#: fact, offer real options only where there is a real closed set.
QUESTIONS = [
    {"id": "title", "ask": "In a few words, what should we call this?"},
    {"id": "problem",
     "ask": "What problem or opportunity is this about, in one or two sentences?"},
    {"id": "why_now", "ask": "Why does this matter now, rather than later or never?"},
    {"id": "who", "ask": "Who is this for -- which users, team, or system feels the problem?"},
    {"id": "outcome", "ask": "What does success look like if this ships?"},
    {"id": "constraints",
     "ask": "Any known constraints -- a deadline, a budget, a system it must fit? Say \"none\" if "
            "there aren't any."},
    {"id": "non_goals",
     "ask": "Anything explicitly OUT of scope, that this should not try to solve? Say \"none\" if "
            "there isn't anything."},
]

#: Asked LAST, always -- the decision the rest of the bank was gathered for. Its id, `next_step`, is
#: distinct from every bank id above by construction (`test_bank_is_flat_and_ordered` pins this), the
#: same collision `unpark.py`'s own `CLOSING_QUESTION` comment warns a future entry away from.
CLOSING_QUESTION = {
    "id": "next_step",
    "ask": "File this and stop here, or continue straight into Product next?",
    "options": ["file and stop", "continue to Product"],
}

#: Every id an answer file must carry for `file()` to proceed -- the bank plus the closing decision.
REQUIRED_IDS = [q["id"] for q in QUESTIONS] + [CLOSING_QUESTION["id"]]

#: `next_step`'s two literal option strings, mapped to the canonical `--decision` tokens `file()`
#: actually takes. Kept separate from the free-text answer recorded in the block (mirroring
#: `unpark.py`'s own split between the recorded `resume` answer and its canonical `--decision` flag)
#: so the persisted record reads in the operator's own words while the code branches on a fixed pair.
DECISIONS = {"file and stop": "stop", "continue to Product": "continue"}

#: #1916: THE BOUNDED TAIL. A fixed bank cannot reach a domain-specific semantic decision -- the E2E
#: validation run on a real idea produced a Dossier sufficient for intent, constraints and non-goals
#: and NOT sufficient to design from, with every gap in one place: what a recurrence counts from, and
#: how a series stops. Neither has a slot here, and neither should: they are semantics of ONE domain,
#: and a bank that grew a slot per domain would stop being a fixed structure. So the bank stays
#: exactly as it was and the RECORD may carry a short tail -- ordinary `{answers, questions}` entries
#: with a prefixed id, which `render_block` already accepted (see its own comment) and which keeps
#: the file interchangeable with `unpark.py`'s, the reason that shape was chosen in the first place.
#:
#: Two prefixes, because the two halves earn different treatment:
FOLLOWUP_PREFIX = "followup_"     #: asked and ANSWERED -- ordinary intake content, arrived at late
OPEN_PREFIX = "open_"             #: could not be settled at Stage 0 -- a doubt, addressed to Stage 1

#: The cap, and it applies to the ANSWERED half only. Bounding both halves with one number would make
#: refusal the consequence of recording a doubt: an agent standing at the cap deletes an `open_` entry
#: to get the record filed, which is exactly the silent loss this mechanism exists to stop. So
#: `file()` refuses above this many `followup_*` -- the interview is what must stay short -- and never
#: refuses an `open_*`, surfacing them instead (a count in `detail`, their own heading in the block,
#: and `goal-design`'s Doubts downstream). What is enforced and what is merely reported is stated
#: here, in SKILL.md, and in the CHANGELOG, because a guarantee nobody can locate is not one.
MAX_FOLLOWUPS = 3

#: When a follow-up is warranted, as engine-owned DATA for the same reason the bank is: the skill
#: should not improvise the test it applies. Worded from what the E2E run actually hit. The third is
#: NOT a missing-slot case at all -- it is two answers already given that disagree, which no larger
#: bank would have caught either, and it is the one the issue's own diagnosis folded in with the
#: other two.
FOLLOWUP_TRIGGERS = [
    "an unstated anchor -- the answer names behaviour whose reference point is never given (what a "
    "repeat counts from, what a share is a share of, when a window starts)",
    "no stated end -- something starts, recurs or is granted, and no answer says how it stops, is "
    "cancelled, or is undone",
    "two answers that disagree -- an example given under one slot contradicts a limit given under "
    "another",
]

#: Sub-headings for the tail in the rendered block. Free of every `blocker_scan.TRIGGERS` word, on
#: the same hygiene grounds `DOSSIER_QA_START`'s own comment already states for the fence.
FOLLOWUP_HEADING = "### Follow-ups"
OPEN_HEADING = "### Open questions"


def followups():
    """The follow-up policy the SKILL fetches and applies -- the cap, the two id prefixes, and the
    triggers that warrant asking. Copies, for the same reason `bank()` returns copies."""
    return {"max": MAX_FOLLOWUPS, "answered_prefix": FOLLOWUP_PREFIX,
            "unresolved_prefix": OPEN_PREFIX, "triggers": list(FOLLOWUP_TRIGGERS)}


def _extras(answers):
    """Every answered id outside the fixed bank, in the order the caller wrote them."""
    return [key for key in (answers or {}) if key not in REQUIRED_IDS]


def _split_extras(extras):
    """Classify the tail: `(answered, unresolved, unrecognized)`. The third bucket is why the cap can
    be trusted -- a mistyped prefix is not quietly a fourth kind that nothing counts."""
    answered = [k for k in extras if k.startswith(FOLLOWUP_PREFIX)]
    unresolved = [k for k in extras if k.startswith(OPEN_PREFIX)]
    known = set(answered) | set(unresolved)
    return answered, unresolved, [k for k in extras if k not in known]


def bank():
    """The fixed intake bank plus the closing decision, in ask order -- what the SKILL fetches and
    walks, rather than hardcoding its own wording. Keeping the questions as engine-owned DATA (not
    agent-improvised prose) is the whole point of preferring this shape over free-form Q&A; a copy of
    each dict, so a caller mutating one entry (e.g. filling in `{ref}`-style templating, which this
    bank has none of) never mutates the module's own bank."""
    return [dict(q) for q in QUESTIONS] + [dict(CLOSING_QUESTION)]


def _title(answers):
    raw = " ".join(str((answers or {}).get("title") or "").split())
    return raw or "Untitled dossier"


def render_block(answers, questions_asked=()):
    """The fenced record, identical in the body and in the comment so the two can never disagree --
    same contract as `unpark.render_block`. Answers are rendered in BANK ORDER (not dict insertion
    order): unlike unpark's block, which is appended to an issue that already has other content, this
    block typically **is** the entire issue body, so it reads as a coherent one-pager rather than in
    whatever order a caller happened to build the dict.

    `next_step`'s answer is included too (mirroring unpark's own inclusion of its `resume` answer) --
    the decision is part of the record, and a reader six weeks later should see what was decided
    without hunting the issue timeline for whichever comment mentioned it.

    The #1916 tail is rendered after the bank, grouped BY KIND under its own heading -- answered
    follow-ups, then what could not be settled -- and not in the order they were asked (they are
    asked before the closing `next_step` question, and read better after the fixed record than
    interleaved with it). Grouping is what makes the second group usable downstream: `goal-design`
    lifts it into Doubts, and a heading is how a human finds it in the body."""
    answers = answers or {}
    asked = {q["id"]: q["ask"] for q in (questions_asked or [])}
    ordered_ids = [q["id"] for q in QUESTIONS] + [CLOSING_QUESTION["id"]]
    lines = [DOSSIER_QA_START, QA_HEADING, ""]

    def _emit(key, value):
        if key in asked:
            lines.append("_%s_" % asked[key])
        lines.append("- **%s** — %s" % (key, " ".join(str(value).split())))

    for key in ordered_ids:
        if key in answers:
            _emit(key, answers[key])
    answered, unresolved, unrecognized = _split_extras(_extras(answers))
    for heading, group in ((FOLLOWUP_HEADING, answered), (OPEN_HEADING, unresolved)):
        if not group:
            continue
        lines += ["", heading, ""]
        for key in group:
            _emit(key, answers[key])
    # An id matching neither prefix is still recorded, unheaded, after both groups. `file()` REFUSES
    # one (it would otherwise be a fourth, uncounted kind), so this is the honest handling of a block
    # that was written some other way: the renderer never silently drops something somebody answered,
    # and the strict gate is the filer.
    for key in unrecognized:
        _emit(key, answers[key])
    lines += ["", DOSSIER_QA_END]
    return "\n".join(lines)


def _resolve_source(sdlc_dir, config, source=None, run=None):
    """`sources.get_source`'s own precedence, with `run` threaded through the GitHub branch for test
    injection -- see the module docstring for why `get_source` itself can't be called here when a
    fake runner is in play."""
    if source is not None:
        return source
    disc = ((config or {}).get("discovery") or {}).get("source") or "local-goals"
    if disc == "github":
        return sources.GitHubSource(config, run=run, sdlc_dir=sdlc_dir)
    return sources.LocalSource(sdlc_dir, config)


def file(sdlc_dir, config, answers, questions_asked=(), decision=None, source=None, run=None,
         apply=True):
    """Create the Dossier ticket: `story`-labelled, never `sdlc:goal`, self-assigned -- no prompt.

    `decision` is the CANONICAL token (`"stop"` or `"continue"`), not the free-text option string --
    the caller (the SKILL) is responsible for mapping the `next_step` answer through `DECISIONS`
    before calling this. This function does not act on the decision beyond recording it and shaping
    the returned `detail` string: what happens on `"continue"` (probing for a Product-stage skill and
    possibly handing off) is SKILL.md-level judgment over the LOCAL INSTALL's own layout, not
    something this engine can decide from `.sdlc` state alone.

    Returns `{"outcome", ...}`:
      `"failed"` -- bad decision token, a missing required answer, a `next_step` answer that
                    disagrees with `decision`, or no usable backlog source. No `gh`/local-write
                    call is attempted in any of these cases.
      `"would"`  -- `apply=False` (`--dry-run`): reports what would be created, creates nothing.
      `"filed"`  -- the ticket was created. `number`, `title`, `decision` are all present.

    #1916's tail is validated here, before any write: an id outside the bank must carry one of the
    two prefixes (an unclassified extra would dodge the cap), must be non-blank, and must have a
    matching entry in `questions_asked` (an answer whose question nobody recorded is not a record).
    Only `FOLLOWUP_PREFIX` is capped -- see `MAX_FOLLOWUPS` for why the other half never is.
    """
    if decision not in ("stop", "continue"):
        return {"outcome": "failed",
                "detail": "decision must be stop or continue (got %r)" % (decision,)}
    missing = [qid for qid in REQUIRED_IDS if not str((answers or {}).get(qid, "")).strip()]
    if missing:
        return {"outcome": "failed",
                "detail": "missing required answers: %s" % ", ".join(missing)}
    # REFUSE rather than persist a self-contradictory record (AGENTS.md SAFETY: refuse loudly
    # rather than proceed weakly). The caller maps `next_step`'s free-text answer through
    # `DECISIONS` before calling `file()` -- if it got that mapping wrong, the issue's own body
    # would otherwise say one thing ("continue to Product") while the code did another (stopped),
    # with nothing to catch the mismatch. Only checked when the recorded answer is one of the two
    # known option strings; an unrecognized value can't be cross-checked and is left to the
    # missing-answer rule above (already non-blank, so it passes that check and reaches `gh` as
    # free text -- the same forgiving handling every other open-ended answer gets).
    recorded_next_step = str((answers or {}).get("next_step") or "").strip()
    expected = DECISIONS.get(recorded_next_step)
    if expected is not None and expected != decision:
        return {"outcome": "failed",
                "detail": ("decision=%r does not match the recorded next_step answer %r "
                          "(expected %r)") % (decision, recorded_next_step, expected)}

    # #1916's tail, validated here so every host gets the same answer -- Cursor has no hooks, so a
    # bound stated only in SKILL.md would be a bound only on Claude Code.
    extras = _extras(answers)
    answered, unresolved, unrecognized = _split_extras(extras)
    if unrecognized:
        return {"outcome": "failed",
                "detail": "answer ids outside the bank must start with %r or %r: %s"
                          % (FOLLOWUP_PREFIX, OPEN_PREFIX, ", ".join(unrecognized))}
    blank = [qid for qid in extras if not str(answers.get(qid, "")).strip()]
    if blank:
        return {"outcome": "failed",
                "detail": "blank follow-up answers: %s" % ", ".join(blank)}
    asked_ids = {q.get("id") for q in (questions_asked or [])}
    unasked = [qid for qid in extras if qid not in asked_ids]
    if unasked:
        return {"outcome": "failed",
                "detail": ("follow-ups with no recorded question: %s (record the ask alongside the "
                           "answer -- the bank's own wording is recoverable, an improvised one is "
                           "not)") % ", ".join(unasked)}
    if len(answered) > MAX_FOLLOWUPS:
        return {"outcome": "failed",
                "detail": ("at most %d answered follow-ups (%r), got %d: %s -- an idea needing more "
                           "is more than one Dossier"
                           % (MAX_FOLLOWUPS, FOLLOWUP_PREFIX, len(answered), ", ".join(answered)))}

    try:
        source = _resolve_source(sdlc_dir, config, source=source, run=run)
    except Exception as exc:                                        # noqa: BLE001
        return {"outcome": "failed", "detail": "no backlog source: %s" % exc}
    if source is None or not hasattr(source, "create_dependency"):
        return {"outcome": "failed", "detail": "backlog source cannot open issues"}

    title = _title(answers)
    block = render_block(answers, questions_asked)

    if not apply:
        detail = ("create %r labelled %r, self-assigned, decision=%s"
                  % (title, STORY_LABEL, decision))
        if unresolved:
            detail += ", %d unresolved: %s" % (len(unresolved), ", ".join(unresolved))
        return {"outcome": "would", "detail": detail}

    try:
        number = source.create_dependency(title, block, "@me", labels=[STORY_LABEL],
                                          goal_label=False)
    except Exception as exc:                                        # noqa: BLE001
        return {"outcome": "failed", "detail": "could not create the dossier issue: %s" % exc}
    if number is None:
        return {"outcome": "failed", "detail": "gh did not return an issue number"}

    _comment(source, number, block)

    if decision == "stop":
        detail = "filed #%s and stopped, as asked" % number
    else:
        detail = ("filed #%s -- ready to continue to Product; if a Product-stage skill (goal-design, "
                   "#1825) is not installed yet, it stays filed and self-assigned until one is" % number)
    # Only when there is something to say: a dossier with no tail returns exactly the dict it
    # returned before #1916, which `test_file_result_is_unchanged_when_nothing_was_followed_up`
    # pins. Unresolved questions are surfaced rather than capped -- this is where the caller reads
    # what it must carry into `goal-design`'s Doubts.
    if unresolved:
        detail += (" -- %d question(s) left unresolved for design: %s"
                   % (len(unresolved), ", ".join(unresolved)))
    return {"outcome": "filed", "number": str(number), "title": title, "decision": decision,
            "detail": detail}


def _comment(source, number, text):
    """Best-effort follow-up comment carrying the same block as the body -- matches `unpark._comment`
    exactly. Never lets a comment failure erase a dossier issue that was already created; the body
    already carries the identical record, so nothing is actually lost."""
    if not hasattr(source, "_run") or not hasattr(source, "_repo_args"):
        return                                        # LocalSource has neither -- nothing to post
    try:
        source._run(["issue", "comment", str(number), *source._repo_args(), "--body", text])
    except Exception:                                  # noqa: BLE001 - audit trail is best-effort
        pass


_USAGE = ("usage: dossier.py bank\n"
          "       dossier.py followups\n"
          "       dossier.py file <sdlc_dir> --answers <file.json> --decision stop|continue "
          "[--dry-run]")


def _value(tail, name):
    if name in tail:
        i = tail.index(name)
        return tail[i + 1] if i + 1 < len(tail) else None
    return None


def main(argv, run=None):
    if argv[1:] in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if len(argv) >= 2 and argv[1] == "bank":
        print(json.dumps(bank(), indent=2))
        return 0
    if len(argv) >= 2 and argv[1] == "followups":
        print(json.dumps(followups(), indent=2))
        return 0
    if len(argv) >= 3 and argv[1] == "file":
        sdlc_dir, tail = argv[2], argv[3:]
        answers_path, decision = _value(tail, "--answers"), _value(tail, "--decision")
        if not answers_path or not decision:
            print(_USAGE, file=sys.stderr)
            return 2
        try:
            payload = json.loads(pathlib.Path(answers_path).read_text(encoding="utf-8"))
        except Exception as exc:                        # noqa: BLE001
            print("dossier.py file: could not read --answers %r: %s" % (answers_path, exc),
                  file=sys.stderr)
            return 2
        answers = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            print("dossier.py file: --answers must be a JSON object with an \"answers\" object",
                  file=sys.stderr)
            return 2
        result = file(sdlc_dir, triage._config(sdlc_dir), answers,
                     questions_asked=(payload.get("questions") or []), decision=decision, run=run,
                     apply="--dry-run" not in tail)
        print(json.dumps(result, indent=2))
        return 1 if result["outcome"] == "failed" else 0
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    # The script-time floor: this run's wall time, recorded to the session resolved at exit.
    sys.exit(_load_loop_script("timing_store").timed_main(main, sys.argv, "dossier"))

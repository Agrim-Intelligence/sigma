"""The model-choice rationale predict.py computes and discards (issue #880).

`predict()` returns a bare tier. The `re.search` match that produced it is dropped on the floor at
`predict.py:117`, so nothing anywhere records WHY a goal ran on a given model. `match.group(0)` is
the literal text that triggered the choice -- "migrat", "secur" -- which is the most defensible
rationale available: not a paraphrase of the rule, the actual substring, checkable by a reader
against the goal text.

SCOPE, after plan review. An earlier plan proposed a new `model_choice` ledger EVENT kind. That was
wrong: `skills/agrim-loop/scripts/actionlog.py:112` already ships a `model_choice` kind, already
carries `(model, effort, phase)`, and `skills/agrim-loop/SKILL.md:103` already instructs logging it at
exactly the point the new event would have been written. A second record under the same name would
have double-recorded the tier in two streams and dropped `effort`/`phase`. The real gap is one
field: `signal`.

DETERMINISTIC SOURCE, AGENT TRANSPORT -- deliberately the same posture `--model` already has here.
`signal` is computed by regex and printed by `predict.py`; the agent passes it through to
`loop.py log`. Making it un-forgeable would mean redesigning a shipped mechanism that already
accepts the tier itself on the same terms, which is out of scope for this goal.
"""
import pathlib
import sys

SKILLS = pathlib.Path(__file__).resolve().parents[1] / "skills"
sys.path.insert(0, str(SKILLS / "agrim-model" / "scripts"))
sys.path.insert(0, str(SKILLS / "agrim-loop" / "scripts"))

import actionlog  # noqa: E402
import predict as P  # noqa: E402


def test_reason_reports_the_literal_trigger_text():
    tier, signal = P.predict_with_reason("add a database migration for orders")
    assert tier == "opus"
    assert signal == "migrat", f"expected the literal trigger, got {signal!r}"


def test_reason_is_none_when_nothing_matches_and_the_default_applies():
    """"ordinary work" fires no pattern -- verified, not assumed. `None` is the honest answer
    there; a manufactured reason beside a defaulted tier would be worse than none.

    (An earlier draft used "update the changelog wording slightly", which plan review showed is
    vacuous: `changelog` is itself a haiku pattern, so the condition never held.)
    """
    tier, signal = P.predict_with_reason("ordinary work")
    assert tier == P._DEFAULT
    assert signal is None


def test_upward_conflict_reports_the_signal_that_WON():
    """_PATTERNS is ordered high-to-low, so "fix the typo in the security module" resolves to opus.
    The recorded signal must be the winning one -- a rationale naming "typo" beside a tier of
    "opus" is actively misleading, worse than recording nothing."""
    tier, signal = P.predict_with_reason("fix the typo in the security module")
    assert tier == "opus"
    assert "typo" not in (signal or "")


def test_predict_is_unchanged_by_the_new_function():
    """The existing entry point keeps its exact signature and return type -- every current caller
    (resolve, resolve-step, the CLI verbs) is untouched."""
    for text in ("add a database migration", "fix a typo", "write the vision narrative", "ordinary work"):
        assert P.predict(text) == P.predict_with_reason(text)[0]


def test_actionlog_model_choice_carries_signal():
    """The one-field gap. `model_choice` already exists with (model, effort, phase); this adds the
    WHY alongside the WHAT, on the record that already ships."""
    assert actionlog.AGENT_FIELDS["model_choice"] == ("model", "effort", "phase", "signal")


def test_signal_is_not_added_to_any_other_kind():
    """Negative control: the field lands on exactly one kind, so a typo in the mapping cannot
    silently widen the vocabulary."""
    carriers = [k for k, fields in actionlog.ALL_FIELDS.items() if "signal" in fields]
    assert carriers == ["model_choice"], f"signal leaked onto {carriers}"

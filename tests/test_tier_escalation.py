"""#2828: a review send-back the current model tier cannot converge is escalated one tier up, never
parked for "budget" or "tier too small".

The decision is CODE (`tier_escalation.py`, reached through `loop.py escalate`), so it is the same
answer on every host; the SKILL.md prose only tells an agent to run it. These tests drive the CLI
verb the docs give, not only the Python functions behind it, and the last group executes the exact
gesture the docs print — a guard whose documented invocation cannot fail is decoration (AGENTS.md).
"""
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest
from journal_events import journal_events

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sigma-loop" / "scripts"
LOOP_PY = S / "loop.py"


def _mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _mod("ledger", S / "ledger.py")
actionlog = _mod("actionlog", S / "actionlog.py")

RECORDING = {"ledger": {"enabled": True, "actor": "dana"}, "journal": {"enabled": True},
             "action_log": {"enabled": True}}


def _sdlc(tmp_path, **config):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config))
    (d / "state" / "STATE.md").write_text(
        "# Loop State\niteration: 0\nrun_iteration: 0\nlast_run: none\n")
    return str(d)


def _auto(tmp_path, **extra):
    return _sdlc(tmp_path, model_selection="auto", **{**RECORDING, **extra})


def _escalate(sdlc_dir, goal, tier, *extra):
    if not any(a.startswith("--after") for a in extra) and "--no-after" not in extra:
        extra = (*extra, "--after", "plan-review")
    extra = tuple(a for a in extra if a != "--no-after")
    r = subprocess.run([sys.executable, str(LOOP_PY), "escalate", sdlc_dir, goal, tier, *extra],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def _events(sdlc_dir):
    return [e for e in journal_events(ledger, sdlc_dir) if e.get("kind") == "model_choice"]


def _trace(sdlc_dir, goal):
    return [e for e in actionlog.read_goal(sdlc_dir, goal) if e.get("kind") == "model_choice"]


# ----------------------------------------------------------------------------- the ladder

@pytest.mark.parametrize("current,expected", [("haiku", "sonnet"), ("sonnet", "opus")])
def test_a_send_back_below_the_ceiling_escalates_one_tier(tmp_path, current, expected):
    d = _auto(tmp_path)
    code, out, err = _escalate(d, "7", current)
    assert code == 0, (out, err)
    assert out.split()[:2] == ["ESCALATE", expected], out


@pytest.mark.parametrize("cap,current", [
    ("opus", "opus"),        # the default ceiling, already reached
    ("sonnet", "sonnet"),    # a lowered ceiling
    ("haiku", "haiku"),      # a ceiling at the bottom rung: nothing to escalate to
    ("fable", "opus"),       # fable is never an escalation target, even when the repo allows it
    ("fable", "fable"),      # a creative-tier goal is already at the top
])
def test_at_the_ceiling_the_verb_says_ceiling_and_writes_nothing(tmp_path, cap, current):
    d = _auto(tmp_path, model_selection_max_tier=cap)
    code, out, err = _escalate(d, "7", current)
    assert code == 3, (out, err)
    assert out.split()[:2] == ["CEILING", current], out
    assert _events(d) == [] and _trace(d, "7") == []


def test_a_lowered_ceiling_still_allows_the_step_beneath_it(tmp_path):
    d = _auto(tmp_path, model_selection_max_tier="sonnet")
    code, out, _ = _escalate(d, "7", "haiku")
    assert code == 0 and out.split()[:2] == ["ESCALATE", "sonnet"], out


def test_the_default_ceiling_is_opus_so_fable_is_unreachable(tmp_path):
    d = _auto(tmp_path)                                   # no model_selection_max_tier at all
    code, out, _ = _escalate(d, "7", "opus")
    assert code == 3 and out.startswith("CEILING opus"), out


@pytest.mark.parametrize("junk", [["fable"], "banana", 3, ""])
def test_a_junk_ceiling_falls_back_to_the_default_never_uncaps(tmp_path, junk):
    d = _auto(tmp_path, model_selection_max_tier=junk)
    assert _escalate(d, "7", "sonnet")[1].startswith("ESCALATE opus")
    assert _escalate(d, "7", "opus")[1].startswith("CEILING opus")


def test_a_hand_edited_ceiling_is_normalized(tmp_path):
    d = _auto(tmp_path, model_selection_max_tier=" SONNET ")
    assert _escalate(d, "7", "sonnet")[1].startswith("CEILING sonnet")


# ----------------------------------------------------------------------------- recording

def test_an_escalation_is_recorded_in_the_ledger_and_the_action_log(tmp_path):
    d = _auto(tmp_path)
    code, out, err = _escalate(d, "7", "haiku")
    assert code == 0, (out, err)
    [ev] = _events(d)
    assert ev["goal"] == "7"
    assert ev["model"] == "sonnet"
    assert ev["signal"] == "escalated: plan-review send-back at haiku"
    [tr] = _trace(d, "7")
    assert tr["model"] == "sonnet" and tr["phase"] == "plan"
    assert tr["signal"] == "escalated: plan-review send-back at haiku"


def test_a_code_review_send_back_is_recorded_against_the_implement_phase(tmp_path):
    d = _auto(tmp_path)
    code, out, _ = _escalate(d, "7", "sonnet", "--after", "code-review")
    assert code == 0 and out.startswith("ESCALATE opus"), out
    [tr] = _trace(d, "7")
    assert tr["phase"] == "implement"
    assert tr["signal"] == "escalated: code-review send-back at sonnet"


def test_recording_is_best_effort_and_never_changes_the_answer(tmp_path):
    d = _sdlc(tmp_path, model_selection="auto")          # ledger, journal, action log all off
    code, out, _ = _escalate(d, "7", "haiku")
    assert code == 0 and out.startswith("ESCALATE sonnet"), out
    assert _events(d) == [] and _trace(d, "7") == []


def test_escalate_prints_the_effort_for_the_new_tier(tmp_path):
    d = _auto(tmp_path)
    assert _escalate(d, "7", "haiku")[1].split()[:3] == ["ESCALATE", "sonnet", "effort=medium"]
    assert _escalate(d, "8", "sonnet")[1].split()[:3] == ["ESCALATE", "opus", "effort=high"]


def test_a_caller_repeating_its_original_tier_still_climbs_to_the_ceiling(tmp_path):
    """The bound is not the caller's honesty: the newest recorded escalation is the floor."""
    d = _auto(tmp_path)
    assert _escalate(d, "7", "haiku")[1].startswith("ESCALATE sonnet")
    code, out, _ = _escalate(d, "7", "haiku")
    assert code == 0 and out.startswith("ESCALATE opus"), out
    assert "send-back at sonnet" in out
    code, out, _ = _escalate(d, "7", "haiku")
    assert code == 3 and out.startswith("CEILING opus"), out
    assert len(_events(d)) == 2 and len(_trace(d, "7")) == 2
    assert _escalate(d, "8", "haiku")[1].startswith("ESCALATE sonnet")   # per goal, not global


def test_the_bound_holds_with_every_recording_surface_off(tmp_path):
    """No ledger, no journal, no action log (the strictest case: `/sigma-init` ships the action log
    on). The floor is control state, so a caller repeating `haiku` still reaches CEILING instead of
    looping sonnet forever."""
    d = _sdlc(tmp_path, model_selection="auto")
    answers = [_escalate(d, "7", "haiku")[1].split()[:2] for _ in range(3)]
    assert answers == [["ESCALATE", "sonnet"], ["ESCALATE", "opus"], ["CEILING", "opus"]], answers


def _show(sdlc_dir, goal):
    r = subprocess.run([sys.executable, str(LOOP_PY), "escalate", sdlc_dir, goal, "--show"],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.strip()


def test_show_reports_the_goals_escalated_ceiling_for_later_phases(tmp_path):
    d = _sdlc(tmp_path, model_selection="auto")
    assert _show(d, "7") == (0, "none")
    _escalate(d, "7", "haiku")
    assert _show(d, "7") == (0, "sonnet")
    _escalate(d, "7", "sonnet")
    assert _show(d, "7") == (0, "opus")
    assert _show(d, "8") == (0, "none")


def test_a_floor_saved_under_a_higher_cap_never_beats_a_lowered_cap(tmp_path):
    d = _sdlc(tmp_path, model_selection="auto")
    _escalate(d, "7", "haiku"); _escalate(d, "7", "sonnet")
    assert _show(d, "7") == (0, "opus")
    (pathlib.Path(d) / "config.json").write_text(
        json.dumps({"model_selection": "auto", "model_selection_max_tier": "sonnet"}))
    assert _show(d, "7") == (0, "sonnet")
    code, out, _ = _escalate(d, "7", "haiku")
    assert code == 3 and out.startswith("CEILING sonnet"), out
    assert "opus" not in out.split("--")[1].split("(")[0], out


def test_show_is_none_when_selection_is_not_auto(tmp_path):
    d = _sdlc(tmp_path, model_selection="auto")
    _escalate(d, "7", "haiku")
    (pathlib.Path(d) / "config.json").write_text(json.dumps({"model_selection": "off"}))
    assert _show(d, "7") == (0, "none")


def test_a_floor_is_never_lowered(tmp_path):
    te = _mod("tier_escalation", S / "tier_escalation.py")
    f = tmp_path / "7.json"
    te.write_floor(f, "opus", "plan-review", "sonnet")
    te.write_floor(f, "sonnet", "plan-review", "haiku")      # a slower, older writer
    assert te.read_floor(f) == "opus"


def test_a_trailing_after_says_it_needs_a_value(tmp_path):
    d = _auto(tmp_path)
    r = subprocess.run([sys.executable, str(LOOP_PY), "escalate", d, "7", "haiku", "--after"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "needs a value" in r.stderr, r


def test_a_torn_floor_file_reads_as_no_floor_not_a_crash(tmp_path):
    d = _sdlc(tmp_path, model_selection="auto")
    f = pathlib.Path(d) / "state" / "escalation" / "7.json"
    f.parent.mkdir(parents=True)
    f.write_text("{not json")
    assert _escalate(d, "7", "haiku")[1].startswith("ESCALATE sonnet")
    assert _show(d, "7") == (0, "sonnet")


@pytest.mark.parametrize("goal", ["../../etc/x", "a/../../b"])
def test_an_unsafe_goal_is_refused_before_anything_is_written(tmp_path, goal):
    d = _sdlc(tmp_path, model_selection="auto")
    assert _escalate(d, goal, "haiku")[0] == 2
    assert not (pathlib.Path(d) / "state" / "escalation").exists()


def test_a_pick_time_model_choice_is_not_mistaken_for_an_escalation(tmp_path):
    d = _auto(tmp_path)
    actionlog.append(d, "7", "model_choice", "agent", model="opus", signal="migrat")
    assert _escalate(d, "7", "haiku")[1].startswith("ESCALATE sonnet")


def test_the_escalation_row_is_agent_set(tmp_path):
    d = _auto(tmp_path)
    _escalate(d, "7", "haiku")
    [tr] = _trace(d, "7")
    assert tr.get("actor") == "agent", tr


@pytest.mark.parametrize("cfg,tier", [({"model_selection": "auto"}, "opus"), ({}, "haiku")])
def test_ceiling_and_off_keep_the_fix_cycle_rather_than_parking_at_once(tmp_path, cfg, tier):
    d = _sdlc(tmp_path, **cfg)
    out = _escalate(d, "7", tier)[1]
    assert "does not converge" in out and "fix/re-review cycle" in out, out


# ----------------------------------------------------------------------------- off + refusals

@pytest.mark.parametrize("cfg", [{}, {"model_selection": "off"}, {"model_selection": "AUTO?"}])
def test_without_auto_selection_there_is_no_tier_to_raise(tmp_path, cfg):
    d = _sdlc(tmp_path, **{**RECORDING, **cfg})
    code, out, _ = _escalate(d, "7", "haiku")
    assert code == 3 and out.startswith("OFF"), out
    assert _events(d) == [] and _trace(d, "7") == []


@pytest.mark.parametrize("args", [("gpt",), ("haiku", "--after", "retro"), ("haiku", "--bogus", "x"),
                                  ("haiku", "--no-after")])
def test_bad_input_is_refused_and_writes_nothing(tmp_path, args):
    d = _auto(tmp_path)
    code, out, err = _escalate(d, "7", *args)
    assert code == 2, (out, err)
    assert _events(d) == [] and _trace(d, "7") == []


# ----------------------------------------------------------------------------- codex mapping

#: A `model_host_overrides.codex` entry the resolver refuses (not an approved Codex model ID).
_BAD_CODEX = {"model_host_overrides": {"codex": {"sonnet": {"model": "not-a-codex-model",
                                                            "effort": "low"}}}}


def test_a_bad_codex_host_mapping_is_refused_before_anything_is_recorded(tmp_path):
    """Sigma validates the Codex mapping BEFORE it records a model_choice (predict.resolve, the
    pick-time hook), so a bad override never leaves an audit row for a tier Codex would refuse. The
    verb does the same: exit 2, nothing written, no floor either."""
    d = _auto(tmp_path, **_BAD_CODEX)
    code, out, err = _escalate(d, "7", "haiku")
    assert code == 2 and out == "" and "Codex host mapping refused" in err, (code, out, err)
    assert _events(d) == [] and _trace(d, "7") == []
    assert not (pathlib.Path(d) / "state" / "escalation").exists()
    assert _show(d, "7") == (0, "none")


def test_a_valid_codex_override_does_not_block_an_escalation(tmp_path):
    d = _auto(tmp_path, model_host_overrides={"codex": {"sonnet": {"model": "gpt-5.5",
                                                                   "effort": "low"}}})
    code, out, _ = _escalate(d, "7", "haiku")
    assert code == 0 and out.startswith("ESCALATE sonnet"), out
    assert len(_events(d)) == 1


def test_ceiling_and_off_answer_without_consulting_the_codex_mapping(tmp_path):
    """Only an ESCALATE names a tier to dispatch at, so only it is checked against the mapping."""
    code, out, _ = _escalate(_auto(tmp_path / "a", **_BAD_CODEX), "7", "opus")
    assert code == 3 and out.startswith("CEILING opus"), out
    code, out, _ = _escalate(_sdlc(tmp_path / "b", **_BAD_CODEX), "7", "haiku")
    assert code == 3 and out.startswith("OFF"), out


def test_a_resolver_that_cannot_run_is_a_refusal_not_a_pass(monkeypatch, tmp_path):
    """Fails CLOSED: the seam is one subprocess, so a timeout or a missing interpreter must read as
    a problem, never as `None` (which would let an unvalidated tier be written and recorded)."""
    lp = _mod("loop_for_283", LOOP_PY)
    for exc in (subprocess.TimeoutExpired("predict.py", 15), FileNotFoundError("no python")):
        def boom(*a, _exc=exc, **k):
            raise _exc
        monkeypatch.setattr(lp.subprocess, "run", boom)
        assert lp._codex_mapping_problem(str(tmp_path), "sonnet"), exc


# ----------------------------------------------------------------------------- drift pin

def test_the_ceiling_vocabulary_matches_the_router_it_mirrors():
    """`tier_escalation` must not import `predict.py` (skills do not import each other's Python),
    so it keeps its own copy of the price order and the ceiling default. This pins the copy: if the
    router's ceiling ever changes, this goes red instead of the two silently disagreeing."""
    te = _mod("tier_escalation", S / "tier_escalation.py")
    predict = _mod("predict_for_2828", ROOT / "skills" / "sigma-model" / "scripts" / "predict.py")
    assert te.PRICE_ORDER == predict._TIER_PRICE_ORDER
    assert te.MAX_TIER_DEFAULT == predict._MAX_TIER_DEFAULT
    assert te.MAX_TIER_KEY == predict._MAX_TIER_KEY
    for raw in (None, "fable", " SONNET ", "banana", ["fable"], 3, ""):
        assert te.ceiling({te.MAX_TIER_KEY: raw}) == predict.max_tier({predict._MAX_TIER_KEY: raw})


# ----------------------------------------------------------------------------- the docs' gesture

SKILL = (ROOT / "skills" / "sigma-loop" / "SKILL.md").read_text(encoding="utf-8")
RUNNING = (ROOT / "skills" / "sigma-loop" / "references" / "running.md").read_text(encoding="utf-8")
PLAN_REVIEW = (ROOT / "skills" / "sigma-plan-review" / "SKILL.md").read_text(encoding="utf-8")
PICKING = (ROOT / "skills" / "sigma-loop" / "references" / "picking.md").read_text(encoding="utf-8")

#: The gesture as the docs print it: `python3 "${CLAUDE_SKILL_DIR}/<...>loop.py" escalate .sdlc
#: "$goal" <tier> [--after <gate>]`, up to the closing backtick.
_GESTURE = re.compile(r'python3 "\$\{CLAUDE_SKILL_DIR\}/([^"`]*loop\.py)" escalate ([^`]+)`')


def _documented_gestures(text):
    found = [" ".join(m.group(2).split()) for m in _GESTURE.finditer(text)]
    return [g for g in found if not g.endswith("--show")]      # the read-back runs in its own test


_DOCS = {"sigma-loop/SKILL.md": SKILL, "sigma-loop/references/running.md": RUNNING,
         "sigma-plan-review/SKILL.md": PLAN_REVIEW, "sigma-loop/references/picking.md": PICKING}


@pytest.mark.parametrize("name", sorted(_DOCS))
def test_the_documented_gesture_runs_and_escalates(tmp_path, name):
    text = _DOCS[name]
    gestures = _documented_gestures(text)
    assert gestures, f"{name} no longer prints the `loop.py escalate` gesture"
    skill_dir = ROOT / "skills" / name.split("/")[0]            # `${CLAUDE_SKILL_DIR}` for that doc
    for m in _GESTURE.finditer(text):
        assert (skill_dir / m.group(1)).resolve() == LOOP_PY.resolve(), (name, m.group(1))
    d = _auto(tmp_path)
    for g in gestures:
        argv = g.replace('"$goal"', "7").replace('"<goal>"', "7").replace("<tier>", "haiku")
        argv = argv.replace(".sdlc", d, 1).split()
        assert "--after" in argv, (name, g)          # the documented gesture must not rely on a default
        r = subprocess.run([sys.executable, str(LOOP_PY), "escalate", *argv],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and r.stdout.startswith("ESCALATE sonnet"), (name, g, r)


def test_the_park_list_says_a_small_tier_is_not_a_park_reason():
    park = SKILL[SKILL.index("Park instead of forcing through"):SKILL.index("**3a.")]
    assert "not a park reason" in park
    assert "escalate" in park


def test_the_park_list_escalates_before_any_park_not_on_the_first_send_back():
    """Sigma-only (plan-review round 1, #283). The predecessor's trigger is "before any park after a
    review send-back (at the latest, the second at one tier)". A shortened SKILL.md paragraph that
    says "after a review send-back" would escalate on the FIRST one and run every later phase of a
    default sonnet goal a rung up, so the timing is pinned here in the park list AND in running.md."""
    park = " ".join(SKILL[SKILL.index("Park instead of forcing through"):SKILL.index("**3a.")].split())
    assert "Before any park after a review send-back" in park, park
    assert "(at the latest, the 2nd send-back at one tier)" in park, park
    running = " ".join(RUNNING.split())
    assert ("before any park or fail after a review send-back, and at the latest on the second "
            "send-back at the same tier") in running


def test_every_goal_ceiling_rule_names_escalation_as_its_one_exception():
    """The incident ran in a goal SLOT told "never above the tier you handed it". A ceiling rule
    that does not name the escalation contradicts it, which is how the park got invented."""
    slot = PICKING[PICKING.index("never above the tier you handed it"):][:600]
    assert "escalate" in slot, slot
    step = RUNNING[RUNNING.index("Never run a step ABOVE the goal ceiling"):][:300]
    assert "escalate" in step, step


_SHOW = re.compile(r'python3 "\$\{CLAUDE_SKILL_DIR\}/[^"`]*loop\.py" escalate (\S+) "\$goal" --show`')


def test_the_documented_show_gesture_runs(tmp_path):
    """A later phase reads its ceiling back with the gesture running.md prints; run THAT."""
    assert _SHOW.search(RUNNING), "running.md no longer prints the `escalate ... --show` gesture"
    d = _sdlc(tmp_path, model_selection="auto")
    _escalate(d, "7", "haiku")
    r = subprocess.run([sys.executable, str(LOOP_PY), "escalate", d, "7", "--show"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == "sonnet", r


def test_the_running_doc_does_not_tell_ceiling_or_off_to_park_at_once():
    rule = RUNNING[RUNNING.index("CEILING <T>"):RUNNING.index("The ladder has two rungs")]
    assert "does not converge" in rule or "does not resolve" in rule, rule


def test_off_does_not_promise_the_session_model(tmp_path):
    """Sigma: `off` maps a Codex dispatch to the ordinary-work model, never the parent session, so
    neither the OFF answer nor the doc bullet may say phases run at the session model."""
    out = _escalate(_sdlc(tmp_path), "7", "haiku")[1]
    assert out.startswith("OFF") and "session model" not in out, out
    bullet = RUNNING[RUNNING.index("- `OFF` (exit 3)"):][:400]
    assert "ordinary-work" in bullet and "run at the session model" not in bullet, bullet

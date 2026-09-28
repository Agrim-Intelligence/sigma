"""#2032: goal_design.py -- the engine behind `agrim-goal-design`'s sweep-round budget.

Before this issue "the budget is three sweep rounds" was a number stated only in `SKILL.md` prose;
nothing in Python counted a round and nothing checked what a design pass claimed it ran under. A
bare `goal_design.rounds` config key with no reader would have been exactly that same silent
half-guarantee with an extra name on it (AGENTS.md's SAFETY property forbids this) -- so the fix is
this module: `sweep_budget()` is engine-owned data behind a CLI verb, mirroring
`skills/agrim-dossier/scripts/dossier.py`'s own `followups()`. `agrim-goal-design` calls it to obey
the ceiling; `agrim-goal-review` calls it again, independently, to check the artifact's `**Budget**`
field was not written from memory.

The bool-trap control (`True == 1` and `isinstance(True, int)` are both true in Python) is run for
real as a manual break-it-and-see-it-fail exercise (`AGENTS.md`: "run the control, or the check is
decoration") -- `test_a_stray_true_is_rejected_not_silently_accepted_as_one` is the permanent
regression pin for what that control proved.
"""
import importlib.util
import json
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"
G = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-goal-design" / "scripts"


def _mod(name, base=S):
    spec = importlib.util.spec_from_file_location(name, base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


goal_design = _mod("goal_design", base=G)
state = _mod("state")


def _config(rounds=None):
    if rounds is None:
        return {"goal_design": {"enabled": True, "mode": "full"}}
    return {"goal_design": {"enabled": True, "mode": "full", "rounds": rounds}}


# --------------------------------------------------------------- full mode: absent -> unchanged

def test_full_mode_absent_rounds_resolves_to_the_pre_2032_default():
    result = goal_design.sweep_budget({}, "full", "medium")
    assert result["rounds"] == 3 == goal_design.DEFAULT_FULL_ROUNDS
    assert result["mode"] == "full"
    assert result["rounds_config_ignored"] is False


def test_full_mode_absent_goal_design_block_entirely_resolves_to_the_default():
    # `{}` is the whole config -- no `goal_design` key at all, not just no `rounds` under it.
    result = goal_design.sweep_budget({"discovery": {"source": "local-goals"}}, "full", "small")
    assert result["rounds"] == goal_design.DEFAULT_FULL_ROUNDS


# --------------------------------------------------------------- full mode: a real override

def test_full_mode_a_valid_override_raises_the_ceiling():
    result = goal_design.sweep_budget(_config(rounds=15), "full", "large")
    assert result["rounds"] == 15
    assert result["rounds_config_ignored"] is False


def test_full_mode_a_valid_override_of_one_is_honoured():
    # 1 is a legitimate operator choice (lower than the default), not just a "raise" path.
    result = goal_design.sweep_budget(_config(rounds=1), "full", "medium")
    assert result["rounds"] == 1


# --------------------------------------------------------------- full mode: malformed input falls back

def test_a_stray_true_is_rejected_not_silently_accepted_as_one():
    """THE CONTROL (AGENTS.md: "run the control, or the check is decoration"). `True == 1` and
    `isinstance(True, int)` both hold in Python -- manually removing the `not isinstance(raw,
    bool)` guard from `sweep_budget` and re-running this exact input was SEEN to turn this green
    into a `result["rounds"] is True` failure before the guard was restored. This is the permanent
    pin of that finding."""
    result = goal_design.sweep_budget(_config(rounds=True), "full", "medium")
    assert result["rounds"] == 3
    assert result["rounds"] is not True


def test_a_stray_false_is_also_rejected():
    result = goal_design.sweep_budget(_config(rounds=False), "full", "medium")
    assert result["rounds"] == 3


def test_zero_is_not_a_positive_integer():
    result = goal_design.sweep_budget(_config(rounds=0), "full", "medium")
    assert result["rounds"] == 3


def test_a_negative_round_count_falls_back_to_the_default():
    result = goal_design.sweep_budget(_config(rounds=-5), "full", "medium")
    assert result["rounds"] == 3


def test_a_float_round_count_falls_back_to_the_default():
    result = goal_design.sweep_budget(_config(rounds=2.5), "full", "medium")
    assert result["rounds"] == 3


def test_a_string_round_count_falls_back_to_the_default():
    result = goal_design.sweep_budget(_config(rounds="15"), "full", "medium")
    assert result["rounds"] == 3


def test_a_null_round_count_falls_back_to_the_default():
    result = goal_design.sweep_budget(_config(rounds=None), "full", "medium")
    assert result["rounds"] == 3


# --------------------------------------------------------------- lane mode: fixed, not configurable

def test_lane_mode_small_is_one_round():
    result = goal_design.sweep_budget({}, "lane", "small")
    assert result["rounds"] == 1


def test_lane_mode_medium_is_two_rounds():
    result = goal_design.sweep_budget({}, "lane", "medium")
    assert result["rounds"] == 2


def test_lane_mode_large_is_three_rounds():
    result = goal_design.sweep_budget({}, "lane", "large")
    assert result["rounds"] == 3


def test_lane_mode_an_unrecognized_lane_falls_back_to_the_default_lane():
    result = goal_design.sweep_budget({}, "lane", "gigantic")
    assert result["lane"] == "medium" == goal_design.discovery.DEFAULT_LANE
    assert result["rounds"] == goal_design.LANE_ROUNDS["medium"]


def test_lane_mode_rounds_key_is_inert_and_reported_as_ignored():
    result = goal_design.sweep_budget(_config(rounds=15), "lane", "small")
    # The fixed lane budget wins, not the config override.
    assert result["rounds"] == 1
    assert result["rounds_config_ignored"] is True


def test_lane_mode_with_no_rounds_key_is_not_reported_as_ignored():
    # `rounds_config_ignored` means "a value was present and set aside", not "lane mode is active".
    result = goal_design.sweep_budget({}, "lane", "small")
    assert result["rounds_config_ignored"] is False


def test_lane_mode_prints_the_escape_hatch_on_stderr(capsys):
    goal_design.sweep_budget(_config(rounds=15), "lane", "small")
    err = capsys.readouterr().err
    assert "mode: full" in err
    assert "ignored" in err.lower()


def test_lane_mode_says_nothing_on_stderr_when_there_is_nothing_to_ignore(capsys):
    goal_design.sweep_budget({}, "lane", "small")
    err = capsys.readouterr().err
    assert err == ""


# --------------------------------------------------------------------------- CLI

def test_main_sweep_budget_prints_the_same_data_as_the_function(tmp_path):
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    (sdlc_dir / "config.json").write_text(json.dumps(_config(rounds=9)))
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = goal_design.main(["goal_design.py", "sweep-budget", str(sdlc_dir),
                                  "--mode", "full", "--lane", "large"])
    assert code == 0
    printed = json.loads(buf.getvalue())
    assert printed == goal_design.sweep_budget(_config(rounds=9), "full", "large")
    assert printed["rounds"] == 9


def test_main_sweep_budget_defaults_mode_and_lane_when_omitted(tmp_path):
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    (sdlc_dir / "config.json").write_text(json.dumps({}))
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = goal_design.main(["goal_design.py", "sweep-budget", str(sdlc_dir)])
    assert code == 0
    printed = json.loads(buf.getvalue())
    assert printed["mode"] == "full"
    assert printed["lane"] == goal_design.discovery.DEFAULT_LANE
    assert printed["rounds"] == goal_design.DEFAULT_FULL_ROUNDS


def test_main_sweep_budget_with_no_config_json_at_all_still_resolves(tmp_path):
    """`state.load_config` raises `ConfigMissing` where `.sdlc/config.json` does not exist at all
    -- SKILL.md §2 already reads that exception as "there is no configured value, so the answer
    is `full`" for `mode` resolution; this verb applies the identical reading to `rounds`, in
    code, rather than leaving a bare traceback for the calling skill to interpret."""
    sdlc_dir = tmp_path / ".sdlc_does_not_exist"
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = goal_design.main(["goal_design.py", "sweep-budget", str(sdlc_dir),
                                  "--mode", "full", "--lane", "medium"])
    assert code == 0
    printed = json.loads(buf.getvalue())
    assert printed["rounds"] == goal_design.DEFAULT_FULL_ROUNDS


def test_main_sweep_budget_rejects_an_unrecognized_mode(tmp_path):
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    (sdlc_dir / "config.json").write_text(json.dumps({}))
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        code = goal_design.main(["goal_design.py", "sweep-budget", str(sdlc_dir),
                                  "--mode", "bogus"])
    assert code == 2
    assert "full or lane" in buf.getvalue()


def test_main_usage_error_on_an_unknown_verb():
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        code = goal_design.main(["goal_design.py", "not-a-real-verb"])
    assert code == 2
    assert "usage" in buf.getvalue()


def test_state_config_missing_is_reused_not_reimplemented():
    """The engine deliberately does not define its own missing-config exception -- it catches
    `state.ConfigMissing` (the same type every other CLI verb in the kit funnels through) by
    NAME, not a module-local reimplementation. Two separately-loaded copies of `state.py` produce
    two distinct class objects (this test's own `state` vs. `goal_design`'s internal one), so the
    check is structural rather than an identity comparison that would be false by construction."""
    assert goal_design.state.ConfigMissing.__name__ == state.ConfigMissing.__name__ == "ConfigMissing"
    assert issubclass(goal_design.state.ConfigMissing, Exception)

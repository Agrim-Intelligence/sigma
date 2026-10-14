"""#1427: the manual gesture `feature_rebase.py upkeep <sdlc_dir> <unit>` (no goal) runs the pass with NO goal. It used to hand
the pass the placeholder "-" as a goal, so a finding was filed through a bogus issue "#-" (park, label and board steps on it, a
404, and "from goal `-`" in the duplicate comment)."""
import json

import test_feature_rebase as R


def _goal_seen(tmp_path, argv_tail):
    m = R._mod()
    seen = []
    m.upkeep = lambda sdlc_dir, config, goal, unit, **kw: seen.append((goal, unit)) or {"outcome": m.CURRENT}
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(json.dumps({"work": {"enabled": True, "base": "main"}}), encoding="utf-8")
    assert m.main(["feature_rebase.py", "upkeep", str(sdlc)] + argv_tail) == 0
    return seen


def test_no_goal_argument_runs_the_pass_with_no_goal(tmp_path):
    assert _goal_seen(tmp_path, ["voice"]) == [(None, "voice")]


def test_a_literal_dash_is_no_goal(tmp_path):
    assert _goal_seen(tmp_path, ["voice", "-"]) == [(None, "voice")]


def test_a_real_goal_is_passed_through(tmp_path):
    assert _goal_seen(tmp_path, ["voice", "7"]) == [("7", "voice")]

"""#237: the end-to-end onboarding control runs in CI, in both modes, and is seen red.

`tools/onboarding_control.py` follows the README Quickstart TEXT (parsed, not restated) through
`/agrim-init` to one goal `done`: local-goals mode against a fresh repository with no remote, and
github mode against the stateful fake `gh` from tests/test_public_bootstrap_control.py and a local
bare origin. Secretless, no model session, no network.

THE CONTROLS (AGENTS.md: a test never seen red proves nothing), each run here on every CI leg:
  * the empty-verify-command default reintroduced in a SCRATCH COPY of the plugin
    (`verify_detect.write_verify` writing enforce ON + command "" again, its guard removed) turns
    the control red AT `record done` -- the demo goal, which carries its own verify_command, still
    reaches done; the control's own goal does not.
  * a drifted README turns it red: a renamed verb in the confirm gesture fails the run at that
    gesture; a renamed init script or a dropped `/agrim-loop` line fails the README parse.
  * every assertion in `check_local` / `check_github` is broken once, alone, against the
    observations of a real green run, and seen false.
POSIX only: the tool refuses on Windows (a make target, a #! fake gh), so this module skips there.
"""
import copy
import importlib.util
import os
import pathlib
import re
import shutil

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "onboarding_control.py"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("make") or not shutil.which("git"),
    reason="POSIX-only control (needs make + git; the fake gh is a #! script)")


def _tool():
    spec = importlib.util.spec_from_file_location("onboarding_control_237", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


oc = _tool()
README_TEXT = (ROOT / "README.md").read_text(encoding="utf-8")


def _scratch_copy(dest):
    """The plugin surface the control runs (skills, hooks, manifest, README, the fake's source)."""
    for rel in ("skills", "hooks", ".claude-plugin"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / "tests").mkdir()
    shutil.copy2(ROOT / oc.FAKE_GH_SOURCE, dest / oc.FAKE_GH_SOURCE)
    shutil.copy2(ROOT / "README.md", dest / "README.md")
    return dest


@pytest.fixture(scope="module")
def local_run(tmp_path_factory):
    return oc.run_local(ROOT, README_TEXT, tmp_path_factory.mktemp("local"))


@pytest.fixture(scope="module")
def github_run(tmp_path_factory):
    return oc.run_github(ROOT, README_TEXT, tmp_path_factory.mktemp("github"))


# ------------------------------------------------------------------------------ green runs

def test_readme_quickstart_parses_into_the_gestures_the_control_runs():
    qs = oc.parse_quickstart(README_TEXT)
    assert qs["init_script"] == "skills/agrim-init/scripts/init_flow.py"
    assert "--demo" in qs["init_flags"]
    assert "confirm .sdlc <n> <id>" in qs["verify_confirm"]
    assert any("marketplace add <SIGMA_REPO>" in l for l in qs["claude_install"])


def test_local_goals_mode_reaches_done_from_the_readme(local_run):
    assert local_run["ok"], local_run
    goals = {g["work"]: g for g in local_run["goals"]}
    assert set(goals) == {oc.DEMO_FILE, oc.WORK_FILE}
    mine = goals[oc.WORK_FILE]
    assert mine["status"] == "done"
    assert mine["evidence"]["command"] == "make test" and mine["evidence"]["exit"] == 0
    assert "cost" in mine["cost_line"]                    # phase_report's honest line, recorded
    assert all(s.get("seconds") is not None for s in local_run["steps"])


def test_github_mode_is_done_only_after_the_pr_merged(github_run):
    assert github_run["ok"], github_run
    obs = github_run["observations"]
    assert obs["early_done_rc"] == 4 and obs["issue_final"] == "closed"
    calls = github_run["gh_calls"]
    for key in ("issue create", "pr merge", "pr comment", "api repos/{owner}/{repo}/pulls"):
        assert key in calls, calls                         # the gh calls are recorded, by kind
    assert obs["unhandled"] == ""


# ------------------------------------------------------------------------------ controls

def test_control_empty_verify_default_goes_red_at_record_done(tmp_path):
    sigma = _scratch_copy(tmp_path / "sigma")
    vd = sigma / "skills" / "agrim-init" / "scripts" / "verify_detect.py"
    src = vd.read_text(encoding="utf-8")
    good = ('    verify["command"] = command\n    verify["enforce"] = bool(command)\n')
    assert good in src
    src = src.replace(good, '    verify["command"] = ""\n    verify["enforce"] = True\n')
    src = src.replace('    if verify["enforce"] and not verify["command"]:', '    if False:')
    vd.write_text(src, encoding="utf-8")
    run = oc.run_local(sigma, README_TEXT, tmp_path / "w")
    assert run["ok"] is False
    assert run["failed_step"] == "record done", run["failed_step"]
    demo, mine = run["goals"]
    assert demo["work"] == oc.DEMO_FILE and demo["status"] == "done"
    assert mine["work"] == oc.WORK_FILE and mine["record_rc"] == 4 and mine["verify_rc"] == 3
    assert "REFUSED" in mine["record_err"] and mine["status"] != "done"


def test_control_readme_drift_in_the_confirm_verb_goes_red_at_that_gesture(tmp_path):
    drifted = README_TEXT.replace("verify_detect.py confirm .sdlc <n> <id>",
                                  "verify_detect.py accept .sdlc <n> <id>")
    assert drifted != README_TEXT
    run = oc.run_local(ROOT, drifted, tmp_path)
    assert run["ok"] is False and run["failed_step"] == "verify confirm (README gesture)"


@pytest.mark.parametrize("old,new,why", [
    ("skills/agrim-init/scripts/init_flow.py", "skills/agrim-init/scripts/init.py", "does not ship"),
    ("/agrim-loop            #", "/agrim-run             #", "no `/agrim-loop` line"),
    ("/agrim-init --demo     #", "/agrim-start --demo    #", "no `/agrim-init` line"),
    ("claude plugin install sigma@sigma", "claude plugin add sigma@sigma", "claude plugin"),
])
def test_control_readme_drift_fails_the_parse(old, new, why):
    assert old in README_TEXT, old
    with pytest.raises(oc.Red) as exc:
        oc.parse_quickstart(README_TEXT.replace(old, new))
    assert exc.value.step == "readme" and why in exc.value.detail


def _broken(check, obs, name, mutate):
    bad = copy.deepcopy(obs)
    mutate(bad)
    results = {a["name"]: a["ok"] for a in check(bad)}
    assert results.pop(name) is False, name
    assert all(results.values()), results                 # broke THAT assertion, and only it


LOCAL_BREAKS = {
    "record done exited 0": lambda o: o.update(record_rc=4),
    "goal frontmatter status is done": lambda o: o.update(status="in-progress"),
    "verify evidence exists and passed": lambda o: o["evidence"].update(verify_state="fail"),
    "verify evidence ran a non-empty command": lambda o: o["evidence"].update(command=""),
    "the loop made no gh call in local-goals mode": lambda o: o.update(gh_calls=[["issue", "list"]]),
}

GITHUB_BREAKS = {
    "the review gate ran (a sigma:block parked the merge)": lambda o: o.update(blocked_merge="clean"),
    "merge passed the review gate once approved": lambda o: o.update(merge="PARK: review"),
    "record done REFUSED while the PR is open": lambda o: o.update(early_done_rc=0),
    "issue still open before the PR merged": lambda o: o.update(issue_before_merge="closed"),
    "PR open before the human merge": lambda o: o.update(pr_before_merge="MERGED"),
    "reconcile recorded done only after the merge": lambda o: o.update(reconcile=""),
    "issue closed after done": lambda o: o.update(issue_final="open"),
    "PR merged": lambda o: o.update(pr_final="OPEN"),
    "the change is on origin/main": lambda o: o.update(remote_file=None),
    "verify evidence passed in the goal worktree": lambda o: o["evidence"].update(verify_state="fail"),
    "every gh call was one the fake models": lambda o: o.update(unhandled='{"argv": ["x"]}\n'),
}


def test_every_local_assertion_is_seen_red_once(local_run):
    obs = next(g for g in local_run["goals"] if g["work"] == oc.WORK_FILE)
    obs = dict(obs, gh_calls=[])
    assert all(a["ok"] for a in oc.check_local(obs))
    assert set(LOCAL_BREAKS) == {a["name"] for a in oc.check_local(obs)}
    for name, mutate in LOCAL_BREAKS.items():
        _broken(oc.check_local, obs, name, mutate)


def test_every_github_assertion_is_seen_red_once(github_run):
    obs = github_run["observations"]
    assert all(a["ok"] for a in oc.check_github(obs))
    assert set(GITHUB_BREAKS) == {a["name"] for a in oc.check_github(obs)}
    for name, mutate in GITHUB_BREAKS.items():
        _broken(oc.check_github, obs, name, mutate)


def test_the_gh_summary_folds_numbers_and_flags(tmp_path):
    calls = [["api", "-X", "PATCH", "repos/a/b/issues/12", "-f", "state=closed"],
             ["api", "repos/a/b/pulls?head=a:x"], ["pr", "view", "3", "--json", "state"]]
    assert oc._summarise_gh(calls) == {"api repos/a/b/issues/N": 1, "api repos/a/b/pulls": 1,
                                       "pr view": 1}
    (tmp_path / ".claude" / "plugins").mkdir(parents=True)
    empty = oc._surface_hash(str(tmp_path))
    (tmp_path / ".claude" / "plugins" / "installed_plugins.json").write_text("{}", encoding="utf-8")
    assert re.fullmatch(r"[0-9a-f]{16}", empty) and oc._surface_hash(str(tmp_path)) != empty

"""#237: the end-to-end onboarding control runs in CI, in both modes and both variants, and is seen red.

`tools/onboarding_control.py` follows the README Quickstart TEXT (parsed, not restated) through
`/sigma-init` to one goal `done`: local-goals mode against a fresh repository with no remote, and
github mode against the stateful fake `gh` from tests/test_public_bootstrap_control.py and a local
bare origin; each in the `confirm` variant (a Makefile target, confirmed by the README gesture) and
the `no-command` variant (nothing to confirm, the verify question left open -- so the default init
SCAFFOLDS is what `record done` sees). Secretless, no model session, no network.

The green runs come from the DOCUMENTED gesture, `python3 tools/onboarding_control.py`, run as a
subprocess and parsed from its own stdout (AGENTS.md: run the control on the gesture the docs give).

THE CONTROLS (AGENTS.md: a test never seen red proves nothing), each run here on every CI leg:
  * the ORIGINAL bug, exactly as the PR #306 review reproduced it -- `config.json.tmpl` back to
    `"enforce": true` with an empty command and BOTH scaffold rewrites (init_flow.py, sdlc_init.py)
    removed, `verify_detect.write_verify` intact -- through the CLI gesture `--mode both --sigma
    <scratch>`: exit 1, local/no-command RED at `record done` (exit 4) and github/no-command RED at
    its enforce-off assertion, its merge parked naming the verify-command fix. The confirm
    variants stay green: their confirm overwrites the default, which is why the variant exists.
  * #312: the merge gate's `verify_required` guard reverted in a scratch copy (every merge demands
    evidence again) turns github/no-command -- which now runs the REAL work-ON path, no
    `--local-only` -- red through the CLI gesture, while github (confirm) stays green.
  * `verify_detect.write_verify` itself regressed (enforce ON + "" again, its guard removed) turns
    the confirm variant red AT `record done`.
  * every `[ask]` flag renamed in init_flow.py turns the control red at init ("unanswerable
    [ask]"); an unknown question id, a line without the machine-readable part, and a renamed flag
    are each red in the pure parser too.
  * README drift turns it red: a renamed verb in the confirm gesture fails the run at that gesture;
    a renamed init script, a dropped `/sigma-loop` line, a drifted install line (claude, codex,
    in-session `/plugin`), or an init flag the README shows that init_flow.py does not accept,
    fails the README parse.
  * a README command that is not `python3 <sigma script> ...`, or carries shell syntax, is refused
    before anything runs (a marker file proves nothing was executed).
  * every assertion in `check_local` / `check_github` / `check_no_command` /
    `check_github_no_command` is broken once, alone,
    against the observations of a real green run, and seen false.
  * main(): exit 0 green, 1 red, 2 for a missing README (a message, not a traceback).
POSIX only: the tool refuses on Windows (a make target, a #! fake gh), so this module skips there.
"""
import copy
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

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
INIT_FLOW = pathlib.Path("skills") / "sigma-init" / "scripts" / "init_flow.py"


def _scratch_copy(dest):
    """The plugin surface the control runs (skills, hooks, evals, manifest, README, the fake's
    source) -- evals/ because the README's `python3 <installed-sigma>/evals/run.py` is usage-checked."""
    for rel in ("skills", "hooks", "evals", ".claude-plugin"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / "tests").mkdir()
    shutil.copy2(ROOT / oc.FAKE_GH_SOURCE, dest / oc.FAKE_GH_SOURCE)
    shutil.copy2(ROOT / "README.md", dest / "README.md")
    (dest / "docs" / "launch").mkdir(parents=True)          # the control reads public_repo from here (#524)
    shutil.copy2(ROOT / "docs" / "launch" / "definition.json", dest / "docs" / "launch" / "definition.json")
    return dest


def _mutate(path, old, new):
    src = path.read_text(encoding="utf-8")
    assert src.count(old) == 1, (path, old)
    path.write_text(src.replace(old, new), encoding="utf-8")


def _cli(*args, workdir=None):
    """The documented gesture, as a subprocess: -> (rc, {name: 'GREEN' | 'RED at <step>'}, JSON)."""
    argv = [sys.executable, str(TOOL), *args] + (["--workdir", str(workdir)] if workdir else [])
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    lines = dict(re.findall(r"(?m)^onboarding-control: (\S+): (GREEN|RED at [^(]+?) \(", proc.stdout))
    blob = json.loads(proc.stdout[proc.stdout.index("\n{") + 1:]) if "\n{" in proc.stdout else None
    return proc.returncode, lines, blob, proc


@pytest.fixture(scope="module")
def cli_run(tmp_path_factory):
    rc, lines, blob, proc = _cli(workdir=tmp_path_factory.mktemp("cli"))
    assert rc == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    assert lines == {"readme-usage": "GREEN", "local": "GREEN", "github": "GREEN",
                     "local/no-command": "GREEN", "github/no-command": "GREEN"}, lines
    return blob


@pytest.fixture(scope="module")
def local_run(cli_run):
    return cli_run["modes"]["local"]


@pytest.fixture(scope="module")
def github_run(cli_run):
    return cli_run["modes"]["github"]


# ------------------------------------------------------------------------------ green runs

def test_readme_quickstart_parses_into_the_gestures_the_control_runs():
    qs = oc.parse_quickstart(README_TEXT)
    assert qs["init_script"] == "skills/sigma-init/scripts/init_flow.py"
    assert "--demo" in qs["init_flags"]
    assert "confirm .sdlc <n> <id>" in qs["verify_confirm"]
    assert qs["claude_install"] == ["claude plugin marketplace add https://github.com/Agrim-Intelligence/sigmaloop",
                                    "claude plugin install sigmaloop@sigmaloop"]
    assert qs["codex_install"] == ["codex plugin marketplace add https://github.com/Agrim-Intelligence/sigma", "codex plugin add sigmaloop@sigmaloop"]
    assert qs["session_install"] == ["/plugin marketplace add https://github.com/Agrim-Intelligence/sigmaloop", "/plugin install sigmaloop@sigmaloop"]
    assert {"--mode", "--verify", "--board", "--ledger", "--local-only"} <= set(qs["init_readme_flags"])
    assert set(qs["init_readme_flags"]) <= oc.init_flow_flags(ROOT)
    # #277: the init subsections' gestures, every one copyable from the user's repository root
    assert len(qs["gestures"]) == 6 and qs["verify_confirm"] in qs["gestures"], qs["gestures"]
    assert all(g.startswith("python3 <installed-sigma>/skills/") for g in qs["gestures"]), qs["gestures"]
    assert any("use-remote .sdlc <remote>" in g for g in qs["gestures"])       # the table's, too


def test_local_goals_mode_reaches_done_from_the_readme(local_run):
    assert local_run["ok"], local_run
    goals = {g["work"]: g for g in local_run["goals"]}
    assert set(goals) == {oc.DEMO_FILE, oc.WORK_FILE}
    mine = goals[oc.WORK_FILE]
    assert mine["status"] == "done"
    assert json.loads(mine["evidence"]["command"]) == ["make test", "test -s hello.txt"]
    assert mine["evidence"]["exit"] == 0
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


def test_github_bootstrap_does_not_recreate_lifecycle_labels(github_run):
    """#304: blind hot-path creates make 34 calls; init alone needs the 14 bootstrap writes."""
    assert github_run["gh_calls"]["label create"] == 14


def test_every_readme_init_gesture_ran_from_the_repository_root(github_run):
    ran = github_run["observations"]["readme_gestures"]
    assert [rc for _g, rc in ran] == [0] * 5, ran
    assert any("preflight.py check" in g for g, _rc in ran)
    steps = [s for s in github_run["steps"] if s["step"].startswith("README gesture: ")]
    assert len(steps) == 5 and all(s["argv"][1].startswith(str(ROOT)) for s in steps), steps


@pytest.mark.parametrize("old,new", [
    # the pre-#277 README: a path relative to the PLUGIN directory, which a user in their own
    # repository cannot copy -- the control used to resolve it against the plugin dir and pass
    ("python3 <installed-sigma>/skills/sigma-init/scripts/preflight.py use-remote",
     "python3 skills/sigma-init/scripts/preflight.py use-remote"),
    ("python3 <installed-sigma>/skills/sigma-init/scripts/verify_detect.py decline",
     "python3 <installed-sigma>/skills/sigma-init/scripts/verify_detect.py refuse"),
    ("python3 <installed-sigma>/skills/sigma-init/scripts/preflight.py check . --sdlc .sdlc",
     "python3 <installed-sigma>/skills/sigma-init/scripts/preflight.py check . --sdlc .sdlc <token>"),
])
def test_control_an_uncopyable_or_drifted_init_gesture_goes_red(old, new, tmp_path):
    """Through the documented gesture (github mode drives the fake gh by path, so as a subprocess)."""
    assert README_TEXT.count(old) == 1, old
    drifted = tmp_path / "README.md"
    drifted.write_text(README_TEXT.replace(old, new), encoding="utf-8")
    rc, lines, blob, proc = _cli("--mode", "github", "--variant", "confirm", "--readme", str(drifted),
                                 workdir=tmp_path)
    assert rc == 1 and lines["github"].startswith("RED at README gesture"), proc.stdout[-2000:]


def test_control_a_plugin_relative_confirm_gesture_goes_red_at_that_gesture(tmp_path):
    old = "python3 <installed-sigma>/skills/sigma-init/scripts/verify_detect.py confirm"
    run = oc.run_local(ROOT, README_TEXT.replace(old, old.replace("<installed-sigma>/", "")), tmp_path)
    assert run["ok"] is False and run["failed_step"] == "verify confirm (README gesture)"
    assert "read from the repository root" in run["steps"][-1]["detail"], run["steps"][-1]


def test_the_gesture_assertion_is_seen_red_once(github_run):
    qs = oc.parse_quickstart(README_TEXT)
    obs = github_run["observations"]
    assert oc.check_gestures(obs, qs)["ok"]
    assert not oc.check_gestures(dict(obs, readme_gestures=obs["readme_gestures"][:-1]), qs)["ok"]
    assert not oc.check_gestures(dict(obs, readme_gestures=[]), dict(qs, gestures=[]))["ok"]


# ------------------------------------------------------------ #277 review: README usage + effects

def test_every_readme_script_gesture_fits_its_scripts_own_usage(cli_run):
    usage = cli_run["modes"]["readme-usage"]
    assert usage["ok"], usage["detail"]
    checked = [c["gesture"] for c in usage["checked"]]
    assert len(checked) >= 20, checked                  # the whole README, not just the init sections
    assert any("backlog_check.py dismiss-text" in g for g in checked), checked
    assert any("loop.py note .sdlc <goal>" in g for g in checked), checked
    for script in ("log.py", "ledger.py", "watch_daemon.py", "evals/run.py", "init_flow.py"):
        assert any(script in g for g in checked), (script, checked)   # every spelling is reached


@pytest.mark.parametrize("old,new,why", [
    # the #277 review's own find: a bare script with a comment where its verb should be (exit 2)
    ('python3 <installed-sigma>/skills/sigma-loop/scripts/loop.py note .sdlc <goal> "Deliberate',
     'python3 <installed-sigma>/skills/sigma-loop/scripts/auto_unpark.py  # see KEEP_PARKED_MARKER\nx "',
     "positionals [] fit no usage alternative"),
    ('dismiss-text blocked-by 40 "sequencing note, not a dependency"', "dismiss-text 40",
     "fit no usage alternative"),                                                # a missing <ref>
    ("reconcile.py census .sdlc", "reconcile.py censuss .sdlc", "fit no usage alternative"),
    ("verify_detect.py set .sdlc --command-file", "verify_detect.py set .sdlc --cmd-file",
     "flag --cmd-file is not in the script's usage"),
    ("migrate.py .sdlc            #", "migrate.py .sdlc extra      #", "fit no usage alternative"),
    ("loop.py session-end .sdlc", "loop.py session-over .sdlc", "fit no usage alternative"),
    ("scripts/reconcile.py census", "scripts/reconcile_gone.py census", "is not shipped"),
    # #277 review block 2: every spelling of a script path is matched, and only one is copyable
    ("python3 <installed-sigma>/skills/sigma-loop/scripts/ledger.py summary",
     'python3 "${CLAUDE_PLUGIN_ROOT}/skills/sigma-loop/scripts/ledger.py" summary', "not copyable"),
    ("python3 <installed-sigma>/skills/sigma-log/scripts/log.py status",
     "python3 $CLAUDE_PLUGIN_ROOT/skills/sigma-log/scripts/log.py status", "not copyable"),
    ("python3 <installed-sigma>/skills/sigma-loop/scripts/watch_daemon.py .sdlc",
     "python3 watch_daemon.py .sdlc", "not copyable"),
    ("python3 <installed-sigma>/skills/sigma-init/scripts/init_flow.py . --cursor",
     "python3 ~/sigma/skills/sigma-init/scripts/init_flow.py . --cursor", "not copyable"),
    ("python3 <installed-sigma>/evals/run.py", "python3 evals/run.py", "not copyable"),
])
def test_control_a_readme_gesture_its_script_would_refuse_is_red(old, new, why, tmp_path):
    assert old in README_TEXT, old
    out = oc.check_readme_usage(README_TEXT.replace(old, new, 1), ROOT, tmp_path)
    assert out["ok"] is False and out["failed_step"] == "README usage", out
    assert any(why in p for d in out["detail"] for p in d["problems"]), out["detail"]


@pytest.mark.parametrize("args,ok", [([".sdlc"], True), ([".sdlc", "--apply"], True),
                                     ([".sdlc", "--apply", "--replace-old-plugin"], True),
                                     ([], True), ([".sdlc", "extra"], False)])
def test_a_nested_optional_group_in_a_usage_line_is_one_optional_flag(args, ok):
    """`[--apply [--replace-old-plugin]]` (migrate.py since #326) is ONE optional flag group, not a
    `[--apply [--replace-old-plugin]` group followed by a required literal `]`."""
    usage = "usage: migrate.py [<sdlc_dir>] [--apply [--replace-old-plugin]]"
    assert (oc.usage_problems(args, usage) == []) is ok, oc.usage_problems(args, usage)


def test_control_the_usage_mode_goes_red_through_the_cli(tmp_path):
    """Through the documented gesture: the pre-fix README's auto_unpark line is `RED at README
    usage`, and the run exits 1 even though the local mode itself is green."""
    drifted = tmp_path / "README.md"
    drifted.write_text(README_TEXT.replace(
        'python3 <installed-sigma>/skills/sigma-loop/scripts/loop.py note .sdlc <goal> "Deliberate',
        "python3 <installed-sigma>/skills/sigma-loop/scripts/auto_unpark.py   # see KEEP\nx \"", 1),
        encoding="utf-8")
    rc, lines, blob, proc = _cli("--mode", "local", "--variant", "confirm", "--readme", str(drifted),
                                 workdir=tmp_path)
    assert rc == 1 and lines == {"readme-usage": "RED at README usage", "local": "GREEN"}, lines


class _Proc:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def test_each_gesture_effect_is_seen_red(tmp_path):
    """GESTURE_EFFECTS is what makes an executed gesture's exit code insufficient on its own."""
    repo = tmp_path
    (repo / ".sdlc").mkdir()
    cfg = {"verify": {"command": "make test", "enforce": True}, "work": {"remote": "origin"}}
    (repo / ".sdlc" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    eff = oc.GESTURE_EFFECTS
    assert eff[("verify_detect.py", "set")](None, repo)
    assert not eff[("verify_detect.py", "decline")](None, repo)
    assert eff[("preflight.py", "use-remote")](None, repo)
    assert not eff[("preflight.py", "local-only")](None, repo)
    check = eff[("preflight.py", "check")]
    assert check(_Proc(0, "sigma: preflight OK - git repository\n"), repo)
    assert check(_Proc(1, "sigma: preflight - 1 problem(s) the loop would otherwise hit\n"), repo)
    assert not check(_Proc(1, "sigma: preflight OK - git repository\n"), repo)   # rc/report mismatch
    assert check(_Proc(0, "sigma: preflight - 1 problem(s) the loop would otherwise hit\n"), repo)
    assert not check(_Proc(1, "usage: preflight.py check [repo_root]\n"), repo)     # a usage exit
    assert not check(_Proc(2, "sigma: preflight - 1 problem(s)\n"), repo)
    assert not check(_Proc(0, ""), repo)
    assert set(eff) == {("verify_detect.py", "set"), ("verify_detect.py", "decline"),
                        ("preflight.py", "check"), ("preflight.py", "use-remote"),
                        ("preflight.py", "local-only")}


def test_no_command_variants_reach_done_with_enforce_off(cli_run):
    local = next(g for g in cli_run["modes"]["local/no-command"]["goals"] if g["work"] == oc.WORK_FILE)
    github = cli_run["modes"]["github/no-command"]["observations"]
    for obs in (local, github):
        assert obs["scaffolded_verify"] == {"command": "", "enforce": False}, obs
        assert obs["verify_rc"] == 3, obs
    assert local["record_rc"] == 0 and local["status"] == "done"
    # #312: github runs the real work-ON path -- no evidence, yet the approved merge passes the
    # review gate; done still means merged (refused while open, recorded by reconcile after).
    assert github["evidence"] is None
    assert "review gate passed" in github["merge"] and github["early_done_rc"] == 4
    assert github["issue_final"] == "closed" and github["pr_final"] == "MERGED"


# ------------------------------------------------------------------------------ controls

def test_control_the_original_bug_goes_red_through_the_cli(tmp_path):
    """The PR #306 reviewer's exact repro: template default back, both scaffold rewrites gone."""
    sigma = _scratch_copy(tmp_path / "sigma")
    init_dir = sigma / "skills" / "sigma-init"
    _mutate(init_dir / "templates" / "config.json.tmpl",
            '"verify": { "command": "", "enforce": false }', '"verify": { "command": "", "enforce": true }')
    _mutate(init_dir / "scripts" / "init_flow.py",
            "        _vd.write_verify(sdlc, None, _vd.unconfirmed_why(_vd.detect(target)))\n", "        pass\n")
    _mutate(init_dir / "scripts" / "sdlc_init.py",
            "        vd.write_verify(sdlc, None, vd.unconfirmed_why(vd.detect(target)))\n", "        pass\n")
    rc, lines, blob, proc = _cli("--mode", "both", "--sigma", str(sigma), workdir=tmp_path)
    assert rc == 1, proc.stdout[-3000:]
    assert lines == {"readme-usage": "GREEN", "local": "GREEN", "github": "GREEN",
                     "local/no-command": "RED at record done",
                     "github/no-command": "RED at work pr"}, lines
    mine = next(g for g in blob["modes"]["local/no-command"]["goals"] if g["work"] == oc.WORK_FILE)
    assert mine["record_rc"] == 4 and "REFUSED" in mine["record_err"] and mine["status"] != "done"
    assert mine["scaffolded_verify"] == {"command": "", "enforce": True}
    # #267 refuses the broken enforce-on/no-command profile before publication, earlier
    # than the merge gate. The exception must never bypass ordinary verify freshness.
    pr_step = next(x for x in blob["modes"]["github/no-command"]["steps"] if x["step"] == "work pr")
    assert pr_step["rc"] == 4 and "no verify evidence" in pr_step["stdout_tail"]


def test_control_312_merge_gate_regression_goes_red_through_the_cli(tmp_path):
    """#312's repro, through the documented gesture: revert the merge gate to demanding evidence
    unconditionally and the github no-command variant -- the real work-ON path -- goes red, its
    approved merge parked on "no fresh verify evidence"; the confirm variant is unaffected."""
    sigma = _scratch_copy(tmp_path / "sigma")
    _mutate(sigma / "skills" / "sigma-loop" / "scripts" / "work.py",
            "refusal = state.done_refusal(sdlc_dir, goal) if required else None",
            "refusal = state.done_refusal(sdlc_dir, goal)")
    rc, lines, blob, proc = _cli("--mode", "github", "--sigma", str(sigma), workdir=tmp_path)
    assert rc == 1, proc.stdout[-3000:]
    # (the stdout line's name stops at " (", so the assertion name reads truncated here)
    assert lines == {"readme-usage": "GREEN", "github": "GREEN",
                     "github/no-command": "RED at assert:the review gate ran"}, lines
    assert blob["modes"]["github/no-command"]["failed_step"] == \
        "assert:the review gate ran (a sigma:block parked the merge)"
    gh_obs = blob["modes"]["github/no-command"]["observations"]
    assert gh_obs["merge"].startswith("PARK: no fresh verify evidence"), gh_obs["merge"]


def test_control_write_verify_regression_goes_red_at_record_done(tmp_path):
    sigma = _scratch_copy(tmp_path / "sigma")
    vd = sigma / "skills" / "sigma-init" / "scripts" / "verify_detect.py"
    _mutate(vd, '    verify["command"] = command\n    verify["enforce"] = bool(command)\n',
            '    verify["command"] = ""\n    verify["enforce"] = True\n')
    _mutate(vd, '    if verify["enforce"] and not verify["command"]:', '    if False:')
    run = oc.run_local(sigma, README_TEXT, tmp_path / "w")
    assert run["ok"] is False
    assert run["failed_step"] == "record done", run["failed_step"]
    demo, mine = run["goals"]
    assert demo["work"] == oc.DEMO_FILE and demo["status"] == "done"
    assert mine["work"] == oc.WORK_FILE and mine["record_rc"] == 4 and mine["verify_rc"] == 3
    assert "REFUSED" in mine["record_err"] and mine["status"] != "done"


RENAMED_ASK_FLAGS = [('["--board yes|no"]', '["--pin-board yes|no"]'),
                     ('["--verify N:ID",', '["--check N:ID",'),
                     ('["--local-only"]', '["--offline"]'),
                     ('["--mode local-goals|github"]', '["--backlog local-goals|github"]'),
                     ('["--ledger yes|no"]', '["--team-ledger yes|no"]')]


def test_control_renamed_ask_flags_go_red_at_init(tmp_path):
    """The reviewer's second repro: every [ask] flag renamed in init_flow.py. A user relaying those
    lines gets exit 2; the control must not stay green."""
    sigma = _scratch_copy(tmp_path / "sigma")
    for old, new in RENAMED_ASK_FLAGS:
        _mutate(sigma / INIT_FLOW, old, new)
    run = oc.run_local(sigma, README_TEXT, tmp_path / "w")
    assert run["ok"] is False and run["failed_step"] == "init", run["failed_step"]
    assert "unanswerable [ask]" in run["steps"][-1]["detail"], run["steps"][-1]


ASK_OUT = """
  [ask] mode: local-goals (goal files) or github (issues)? default: github (x). -> --mode local-goals|github
  [ask] verify: re-run with ... -> --verify N:ID ; --verify-command-file FILE ; --no-verify
  [ask] board: re-run with --board yes ... -> --board yes|no
  [ask] ledger: the team ledger? -> --ledger yes|no
  [ask] work: fix the remote above, or re-run with --local-only -> --local-only
"""


def test_ask_lines_are_parsed_and_answered_by_the_flag_they_offer():
    asks = oc.parse_asks(ASK_OUT)
    assert asks["verify"] == [("--verify", "N:ID"), ("--verify-command-file", "FILE"), ("--no-verify", None)]
    assert oc.answer_asks(asks, "github") == ["--mode", "github", "--board", "no", "--ledger", "no",
                                              "--local-only"]


@pytest.mark.parametrize("old,new,why", [
    ("-> --board yes|no", "-> --pin-board yes|no", "offers ['--pin-board']"),
    ("-> --local-only", "-> --offline", "offers ['--offline']"),
    ("-> --ledger yes|no", "-> --ledger on|off", "offers 'on|off'"),
    ("[ask] board:", "[ask] runner:", "no policy for this question"),
    ("-> --mode local-goals|github", "", "no machine-readable"),
])
def test_control_an_unanswerable_ask_is_red(old, new, why):
    assert old in ASK_OUT
    with pytest.raises(oc.Red) as exc:
        oc.answer_asks(oc.parse_asks(ASK_OUT.replace(old, new)), "github")
    assert exc.value.step == "init" and "unanswerable [ask]" in exc.value.detail
    assert why in exc.value.detail, exc.value.detail


def test_control_readme_drift_in_the_confirm_verb_goes_red_at_that_gesture(tmp_path):
    drifted = README_TEXT.replace("verify_detect.py confirm .sdlc <n> <id>",
                                  "verify_detect.py accept .sdlc <n> <id>")
    assert drifted != README_TEXT
    run = oc.run_local(ROOT, drifted, tmp_path)
    assert run["ok"] is False and run["failed_step"] == "verify confirm (README gesture)"


@pytest.mark.parametrize("old,new,why", [
    ("skills/sigma-init/scripts/init_flow.py", "skills/sigma-init/scripts/init.py", "does not ship"),
    ("/sigma-loop            #", "/sigma-run             #", "no `/sigma-loop` line"),
    ("/sigma-init --demo     #", "/sigma-start --demo    #", "no `/sigma-init` line"),
    ("claude plugin install sigmaloop@sigmaloop", "claude plugin add sigmaloop@sigmaloop", "`claude plugin` lines"),
    ("codex plugin add sigmaloop@sigmaloop", "codex plugin install sigmaloop@sigmaloop", "`codex plugin` lines"),
    ("codex plugin marketplace add https://github.com/Agrim-Intelligence/sigma", "codex marketplace add https://github.com/Agrim-Intelligence/sigma",
     "`codex plugin` lines"),
    ("/plugin install sigmaloop@sigmaloop", "/plugin install sigmaloop@sigmaloop-market", "`/plugin` lines"),
    ("(`--mode`, `--verify`, `--board`, `--ledger`, `--local-only`)",
     "(`--mode`, `--verify`, `--board`, `--ledger`, `--offline`)", "['--offline']"),
])
def test_control_readme_drift_fails_the_parse(old, new, why):
    assert old in README_TEXT, old
    with pytest.raises(oc.Red) as exc:
        oc.parse_quickstart(README_TEXT.replace(old, new))
    assert exc.value.step == "readme" and why in exc.value.detail, exc.value.detail


def test_control_an_init_flag_init_flow_dropped_fails_the_parse(tmp_path):
    sigma = _scratch_copy(tmp_path / "sigma")
    _mutate(sigma / INIT_FLOW, '_BOOL = {"--local-only", ', '_BOOL = {')
    with pytest.raises(oc.Red) as exc:
        oc.parse_quickstart(README_TEXT, sigma)
    assert "['--local-only']" in exc.value.detail, exc.value.detail


@pytest.mark.parametrize("line", [
    "sh -c 'touch {m}'",
    "bash skills/sigma-init/scripts/verify_detect.py confirm .sdlc 1 x",
    "node skills/sigma-init/scripts/verify_detect.py confirm .sdlc 1 x",
    "python3 -c 'open(\"{m}\", \"w\")'",
    "python3 skills/sigma-init/scripts/verify_detect.py confirm .sdlc $(touch {m}) 1",
    "python3 skills/sigma-init/scripts/verify_detect.py confirm .sdlc 1 x; touch {m}",
    "python3 skills/sigma-init/scripts/verify_detect.py confirm .sdlc `touch {m}` 1",
    "python3 skills/sigma-init/scripts/verify_detect.py confirm .sdlc 1 x | tee {m}",
    "python3 skills/sigma-init/scripts/nope.py confirm .sdlc 1 x",
    "python3 /usr/bin/env.py x",
    "python3 skills/../../../../tmp/x.py",
])
def test_control_a_readme_command_off_the_pinned_shape_is_refused(line, tmp_path):
    marker = tmp_path / "EXECUTED"
    with pytest.raises(oc.Red) as exc:
        oc._py_argv(line.format(m=marker), ROOT, {}, step="verify confirm (README gesture)", cwd=ROOT)
    assert "refused" in exc.value.detail and exc.value.step == "verify confirm (README gesture)"
    assert not marker.exists()


def test_control_a_shell_readme_gesture_is_refused_before_it_runs(tmp_path):
    marker = tmp_path / "EXECUTED"
    drifted = README_TEXT.replace("python3 <installed-sigma>/skills/sigma-init/scripts/verify_detect.py "
                                  "confirm .sdlc <n> <id>",
                                  f"sh -c 'touch {marker}' skills/sigma-init/scripts/verify_detect.py "
                                  "confirm .sdlc <n> <id>")
    assert drifted != README_TEXT
    run = oc.run_local(ROOT, drifted, tmp_path / "w")
    assert run["ok"] is False and run["failed_step"] == "verify confirm (README gesture)"
    assert "refused" in run["steps"][-1]["detail"] and not marker.exists()


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

NO_COMMAND_BREAKS = {
    "init left verify.enforce OFF with no command confirmed":
        lambda o: o.update(scaffolded_verify={"command": "", "enforce": True}),
    "loop verify said NO-COMMAND (exit 3)": lambda o: o.update(verify_rc=0),
    "record done exited 0 (nothing to enforce)": lambda o: o.update(record_rc=4),
    "goal frontmatter status is done": lambda o: o.update(status="in-progress"),
    "the loop made no gh call in local-goals mode": lambda o: o.update(gh_calls=[["issue", "list"]]),
}

GITHUB_NO_COMMAND_BREAKS = {
    "init left verify.enforce OFF with no command confirmed":
        NO_COMMAND_BREAKS["init left verify.enforce OFF with no command confirmed"],
    "loop verify said NO-COMMAND (exit 3)": NO_COMMAND_BREAKS["loop verify said NO-COMMAND (exit 3)"],
    "no verify evidence was written (nothing to run)":
        lambda o: o.update(evidence={"verify_state": "pass"}),
    **{k: v for k, v in GITHUB_BREAKS.items() if k != "verify evidence passed in the goal worktree"},
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


def test_every_no_command_assertion_is_seen_red_once(cli_run):
    local = next(g for g in cli_run["modes"]["local/no-command"]["goals"] if g["work"] == oc.WORK_FILE)
    local = dict(local, gh_calls=[])
    assert all(a["ok"] for a in oc.check_no_command(local))
    assert {a["name"] for a in oc.check_no_command(local)} == set(NO_COMMAND_BREAKS)
    for name, mutate in NO_COMMAND_BREAKS.items():
        _broken(oc.check_no_command, local, name, mutate)
    github = cli_run["modes"]["github/no-command"]["observations"]
    assert all(a["ok"] for a in oc.check_github_no_command(github))
    assert {a["name"] for a in oc.check_github_no_command(github)} == set(GITHUB_NO_COMMAND_BREAKS)
    for name, mutate in GITHUB_NO_COMMAND_BREAKS.items():
        _broken(oc.check_github_no_command, github, name, mutate)


def test_main_exit_codes_for_red_and_a_missing_readme(tmp_path):
    rc, lines, blob, proc = _cli("--mode", "local", "--variant", "confirm", "--readme",
                                 str(tmp_path / "missing.md"), workdir=tmp_path)
    assert rc == 2 and "Traceback" not in proc.stderr, proc.stderr
    assert "precondition missing" in proc.stderr and "missing.md" in proc.stderr
    drifted = tmp_path / "README.md"
    drifted.write_text(README_TEXT.replace("/sigma-loop            #", "/sigma-run             #"),
                       encoding="utf-8")
    rc, lines, blob, proc = _cli("--mode", "local", "--readme", str(drifted), workdir=tmp_path)
    assert rc == 1 and lines == {"readme": "RED at readme", "readme-usage": "GREEN"}, proc.stdout[-2000:]
    assert oc.main(["onboarding_control.py", "--mode", "local", "--variant", "no-command",
                    "--workdir", str(tmp_path), "--json", str(tmp_path / "r.json")]) == 0
    result = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert list(result["modes"]) == ["readme-usage", "local/no-command"] and result["ok"] is True


def test_the_gh_summary_folds_numbers_and_flags(tmp_path):
    calls = [["api", "-X", "PATCH", "repos/a/b/issues/12", "-f", "state=closed"],
             ["api", "repos/a/b/pulls?head=a:x"], ["pr", "view", "3", "--json", "state"]]
    assert oc._summarise_gh(calls) == {"api repos/a/b/issues/N": 1, "api repos/a/b/pulls": 1,
                                       "pr view": 1}
    (tmp_path / ".claude" / "plugins").mkdir(parents=True)
    empty = oc._surface_hash(str(tmp_path))
    (tmp_path / ".claude" / "plugins" / "installed_plugins.json").write_text("{}", encoding="utf-8")
    assert re.fullmatch(r"[0-9a-f]{16}", empty) and oc._surface_hash(str(tmp_path)) != empty


def test_acceptance_still_rejects_missing_feature_after_green_repository_baseline(tmp_path, monkeypatch):
    """#272: letting the repository baseline precede a future feature must not weaken its check."""
    implement = oc._local_goal_work
    def omit_feature(goal_path, repo):
        output = implement(goal_path, repo)
        if output == oc.WORK_FILE:
            (repo / output).unlink()
        return output
    monkeypatch.setattr(oc, '_local_goal_work', omit_feature)
    result = oc.run_local(ROOT, README_TEXT, tmp_path / 'missing-feature')
    assert not result['ok']
    assert result['failed_step'] == 'record done'

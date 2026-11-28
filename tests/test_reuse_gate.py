"""#2286: the plan input check (`reuse.check_plan`) is enforced by `red_green.py check-tests` and by
`work.py record-plan-review`.

Goal 7 is the new-format goal of tests/test_reuse.py (a git project, a record of three criteria, a dossier whose
table is RU-1 and RU-2); a plan that omits RU-2 from `## Reuse` is the incomplete one. The CLI tests run the
documented gesture (`red_green.py check-tests <sdlc> <goal>`) as a subprocess. Every test asserts a value, so each
goes red by AssertionError while the gate is absent. This file never prints. The `_runner` fake of tests/test_work.py
answers only the git calls the plan-copy lookup makes; `_branch_doc` shells out for itself, and these projects have
no branch for the goal, so no fake here can hand a legacy goal a dossier."""
import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_reuse import _project, _dossier, ROWS  # noqa: E402
from test_reuse_plan import _plan_text, REUSE_OK  # noqa: E402
from test_work import ENFORCING, ON, _runner, _started, work, state  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"
RED_GREEN = SCRIPTS / "red_green.py"
GENERATOR = ROOT / "skills" / "sigma-doctor" / "scripts" / "enforcement_table.py"

MISSING_RU2 = "- RU-1: Task 1 calls the resolver to find the dossier\n"
MAP_MISSING_AC2 = "- AC-1: tests/test_x.py::test_a\n- AC-3: UNTESTABLE: a wording change with no behaviour\n"
LEGACY_TESTS = "- `tests/test_x.py::test_a`\n"
ROOT_APP = "".join("line %d\n" % n for n in range(1, 11))
SUCCESS = "red_green.py: ## Tests and plan inputs in %s are usable by `work.py pr`\n"


def _goal(tmp_path, monkeypatch, plan=None, goal="7", config=ENFORCING, new_format=True):
    """(sdlc, goal, sha): a started goal whose plan is in the main checkout. `new_format=False` leaves it without
    a dossier, which is a legacy goal."""
    monkeypatch.setenv("CLAUDECODE", "1")
    sdlc, _, head = _project(tmp_path, git=True)
    (sdlc / "state").mkdir()
    (sdlc / "config.json").write_text(json.dumps(config))
    state.start_run(str(sdlc))
    if new_format:
        (sdlc / "research" / (goal + ".md")).write_text(_dossier(searched_at=head, rows=ROWS[:2]), encoding="utf-8")
    text = plan if plan is not None else _plan_text()
    (sdlc / "plans").mkdir(exist_ok=True)
    (sdlc / "plans" / (goal + ".md")).write_text(text, encoding="utf-8")
    _started(str(sdlc), goal=goal)
    tree = sdlc.parent / ".sdlc" / "work" / "0001-x" / "src"       # the goal's tree holds the evidence files
    tree.mkdir(parents=True)
    (tree / "app.py").write_text(ROOT_APP, encoding="utf-8")
    return str(sdlc), goal, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_tests(sdlc, goal):
    return subprocess.run([sys.executable, str(RED_GREEN), "check-tests", sdlc, goal], capture_output=True, text=True)


def _tree(sdlc):
    """Every file under the sdlc dir with its bytes: what a refusal must leave exactly as it found it."""
    base = pathlib.Path(sdlc)
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


def _record(sdlc, goal, sha, verdict="SOUND", config=ENFORCING, run=None):
    return work.record_plan_review(sdlc, config, goal, verdict, sha, run=run or _runner([]))


def _refusal(sdlc, goal, sha, verdict, config=ENFORCING):
    """The ValueError `_record` raises, or None when it records instead: an assert-able value, not a raise."""
    try:
        _record(sdlc, goal, sha, verdict, config=config)
    except ValueError as exc:
        return str(exc)
    return None


# ---- AC-1: check-tests runs the plan input check ---------------------------------------------------------------

def test_check_tests_exits_2_when_a_reuse_decision_is_not_cited(tmp_path, monkeypatch):
    sdlc, goal, _ = _goal(tmp_path, monkeypatch, plan=_plan_text(reuse=MISSING_RU2))
    got = _check_tests(sdlc, goal)
    assert got.returncode == 2 and got.stdout == "", got
    assert "RU-2" in got.stderr and got.stderr.startswith("red_green.py: "), got


def test_check_tests_exits_2_when_a_criterion_is_not_mapped(tmp_path, monkeypatch):
    sdlc, goal, _ = _goal(tmp_path, monkeypatch, plan=_plan_text(test_map=MAP_MISSING_AC2))
    got = _check_tests(sdlc, goal)
    assert got.returncode == 2 and got.stdout == "", got
    assert "AC-2" in got.stderr and got.stderr.startswith("red_green.py: "), got


def test_check_tests_still_exits_0_for_a_complete_plan(tmp_path, monkeypatch):
    sdlc, goal, _ = _goal(tmp_path, monkeypatch)
    got = _check_tests(sdlc, goal)
    assert got.returncode == 0 and got.stderr == "", got
    assert got.stdout == SUCCESS % (pathlib.Path(sdlc) / "plans" / "7.md"), got


def test_check_tests_reports_a_broken_tests_section_first(tmp_path, monkeypatch):
    bad = _plan_text(reuse=None, test_map=None, tests="- tests/test_x.py without backticks\n")
    sdlc, goal, _ = _goal(tmp_path, monkeypatch, plan=bad)
    got = _check_tests(sdlc, goal)
    assert got.returncode == 2 and got.stdout == "", got
    assert got.stderr == "red_green.py: ## Tests requires one backticked pytest selector per bullet\n", got
    # and a plan whose ## Tests is fine but whose ## Reuse is not is refused with the plan-input message
    (tmp_path / "second").mkdir()
    sdlc2 = _goal(tmp_path / "second", monkeypatch, plan=_plan_text(reuse=MISSING_RU2))
    got = _check_tests(sdlc2[0], sdlc2[1])
    assert got.returncode == 2 and got.stdout == "", got
    assert "RU-2" in got.stderr and got.stderr.startswith("red_green.py: "), got


# ---- AC-3: a legacy goal is unchanged --------------------------------------------------------------------------

def test_check_tests_for_a_legacy_goal_exits_0_and_warns_on_stderr(tmp_path, monkeypatch):
    sdlc, goal, _ = _goal(tmp_path, monkeypatch, plan=_plan_text(reuse=None, test_map=None), new_format=False)
    got = _check_tests(sdlc, goal)
    assert got.returncode == 0, got
    assert got.stdout == SUCCESS % (pathlib.Path(sdlc) / "plans" / "7.md"), got
    assert got.stderr == "red_green.py: legacy goal (no research dossier); plan inputs not checked\n", got


# ---- AC-2: record-plan-review refuses an approving verdict for incomplete inputs --------------------------------

def test_record_plan_review_refuses_an_approving_verdict_for_incomplete_inputs(tmp_path, monkeypatch):
    sdlc, goal, sha = _goal(tmp_path, monkeypatch, plan=_plan_text(reuse=MISSING_RU2))
    before = _tree(sdlc)
    for word in ("SOUND", "SOUND-WITH-REFINEMENTS"):
        message = _refusal(sdlc, goal, sha, word)
        assert message is not None, "an approving verdict was recorded for incomplete inputs"
        assert message.startswith("the plan's inputs are incomplete, so Plan-Review cannot pass it: "), message
        assert "RU-2" in message and "then run a fresh plan-review" in message, message
        assert _tree(sdlc) == before, "a refused verdict wrote something"
    assert not (pathlib.Path(sdlc) / "state" / "gates" / "7.json").exists()


def test_record_plan_review_still_records_fix_first_for_incomplete_inputs(tmp_path, monkeypatch):
    sdlc, goal, sha = _goal(tmp_path, monkeypatch, plan=_plan_text(reuse=MISSING_RU2))
    assert _refusal(sdlc, goal, sha, "SOUND") is not None, "an approving verdict was not refused"
    assert _record(sdlc, goal, sha, "FIX-FIRST")["verdict"] == "block"
    assert (pathlib.Path(sdlc) / "state" / "gates" / "7.json").is_file()


def test_record_plan_review_records_when_verify_is_not_enforced(tmp_path, monkeypatch):
    sdlc, goal, sha = _goal(tmp_path, monkeypatch, plan=_plan_text(reuse=MISSING_RU2), config=ON)
    message = _refusal(sdlc, goal, sha, "SOUND", config=ENFORCING)
    assert message is not None and "RU-2" in message, "the same verdict was not refused when verify is enforced"
    assert _record(sdlc, goal, sha, config=ON)["verdict"] == "pass"


def test_record_plan_review_checks_the_plan_copy_it_reviewed_when_only_the_branch_has_one(tmp_path, monkeypatch):
    """Main has no plan; the branch copy (tracked, unmodified per the fake git) is the reviewed one."""
    sdlc, goal, _ = _goal(tmp_path, monkeypatch)
    (pathlib.Path(sdlc) / "plans" / "7.md").unlink()
    branch = pathlib.Path(sdlc).parent / ".sdlc" / "work" / "0001-x" / ".sdlc" / "plans" / "7.md"
    branch.parent.mkdir(parents=True)
    run = _runner([("ls-files", ".sdlc/plans/7.md"), ("status --porcelain", "")])
    complete = _plan_text()
    branch.write_text(complete, encoding="utf-8")
    assert _record(sdlc, goal, hashlib.sha256(complete.encode()).hexdigest(), run=run)["verdict"] == "pass"
    incomplete = _plan_text(reuse=MISSING_RU2)
    branch.write_text(incomplete, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        _record(sdlc, goal, hashlib.sha256(incomplete.encode()).hexdigest(), run=run)
    assert "RU-2" in str(caught.value) and branch.as_posix() in str(caught.value)


def _crashing_reuse(monkeypatch, module):
    """Make `reuse.check_plan` raise, whichever way `module` loads it."""
    real = module._sibling if hasattr(module, "_sibling") else module._load

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    def loader(name):
        mod = real(name)
        if name == "reuse":
            monkeypatch.setattr(mod, "check_plan", boom)
        return mod
    monkeypatch.setattr(module, "_sibling" if hasattr(module, "_sibling") else "_load", loader)


def test_a_crash_in_the_plan_input_check_is_an_exit_2_message_in_check_tests(tmp_path, monkeypatch, capsys):
    sdlc, goal, _ = _goal(tmp_path, monkeypatch)
    rg = work._load("red_green")
    _crashing_reuse(monkeypatch, rg)
    assert rg.main(["check-tests", sdlc, goal]) == 2
    err = capsys.readouterr().err
    assert err.startswith("red_green.py: ") and "RuntimeError" in err and "boom" in err, err


def test_a_crash_in_the_plan_input_check_is_a_refusal_message_in_record_plan_review(tmp_path, monkeypatch):
    sdlc, goal, sha = _goal(tmp_path, monkeypatch)
    _crashing_reuse(monkeypatch, work)
    with pytest.raises(ValueError) as caught:
        _record(sdlc, goal, sha)
    assert "RuntimeError" in str(caught.value) and "boom" in str(caught.value), str(caught.value)


# ---- AC-3: legacy goals record exactly as before ---------------------------------------------------------------

LEGACY_CASES = {
    "no dossier": None,
    "dossier without reuse": "# Research: x\n\n## Goal in one paragraph\nWhat it does.\n\n## Blast radius\n- a file\n",
    "bare reuse heading": "# Research: x\n\n## Reuse\n- `src/app.py`: a helper (line 3).\n",
}


@pytest.mark.parametrize("goal", ["7", "0001-x"])
@pytest.mark.parametrize("case", sorted(LEGACY_CASES))
def test_legacy_goal_is_recorded_as_before(tmp_path, monkeypatch, case, goal):
    plan = "# Plan\n\n## Tests\n" + LEGACY_TESTS
    sdlc, goal, sha = _goal(tmp_path, monkeypatch, plan=plan, goal=goal, new_format=False)
    if LEGACY_CASES[case] is not None:
        (pathlib.Path(sdlc) / "research" / (goal + ".md")).write_text(LEGACY_CASES[case], encoding="utf-8")
    assert _record(sdlc, goal, sha)["verdict"] == "pass"
    got = _check_tests(sdlc, goal)
    assert got.returncode == 0, got
    assert got.stdout == SUCCESS % (pathlib.Path(sdlc) / "plans" / (goal + ".md")), got


# ---- AC-4: the enforcement table lists the check ---------------------------------------------------------------

def test_the_enforcement_table_lists_the_plan_input_check():
    spec_path = GENERATOR
    import importlib.util
    spec = importlib.util.spec_from_file_location("enforcement_table", spec_path)
    table = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(table)
    found = [r for r in table.rows(str(ROOT)) if "_plan_inputs_refusal" in r["source"]]
    assert len(found) == 1, found
    row = found[0]
    assert "record-plan-review" in row["mechanism"] and "check-tests" in row["mechanism"], row
    assert row["enabled_by"] == ("verify.enforce",), row
    assert "whatever `verify.enforce` is" in row["condition"], row
    generated = subprocess.run([sys.executable, str(GENERATOR)], capture_output=True, text=True, cwd=str(ROOT)).stdout
    assert (ROOT / "docs" / "enforcement.md").read_text(encoding="utf-8") == generated
    assert "Plan inputs are complete before Plan-Review passes" in generated

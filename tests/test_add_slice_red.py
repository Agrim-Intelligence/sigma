"""Pure move from tests/test_add_slice.py at 4042e97 (#2224 amendment 2): the wholly assertion-red nodes; test bodies byte-identical.

`work.py add-slice` (#2224): a follow-up joins its goal's plan as a slice, counted in a code-owned record.

Run: python3 -m pytest tests/test_add_slice*.py
(generic form: python -m pytest tests/test_add_slice*.py)

The C7/C9/C10/C12 tests drive the REAL CLI by subprocess (`python3 skills/sigma-loop/scripts/work.py add-slice ...`).
`acceptance.extend` does not exist until #2222 lands. Tests that need an acceptance record therefore never touch the
real acceptance.py: a DRIVER subprocess loads the real add_slice module and replaces its test-only `_acceptance` seam with a
double that has `extend` present or absent, so nothing flips when #2222 merges. Tests with no acceptance record use the
real `work.py add-slice` CLI. The e2e path through the real `extend` is exercised by tests/test_add_slice_extend_real.py.

CONTROLS: C7a/C7b extend probe (two clauses), C8 probe before write, C9 both copies, C10 count last / idempotent /
title identity, C11 cap via route, C12 plan-review hash gate, C30 unsafe stem, C31 selector ceiling, C33 lock held
across check+write (deterministic in-process), C34 TypeError, C35 slices.json notice.

"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import threading

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
WORK = S / "work.py"
PLAN = "# plan\n\n## Steps\n1. x\n\n## Tests\n- `tests/test_a.py`\n"
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fr, add_slice, work = _mod("followup_route"), _mod("add_slice"), _mod("work")

DOUBLE_PRESENT = ("import pathlib\n"
                  "def path(sdlc_dir, goal): return pathlib.Path(sdlc_dir) / 'acceptance' / (str(goal) + '.md')\n"
                  "def extend(sdlc_dir, goal, slice_ref, reason, lines, config=None, source=None):\n"
                  "    with open(pathlib.Path(sdlc_dir) / 'extend.log', 'a') as h: h.write(repr(lines) + '\\n')\n")
DOUBLE_ABSENT = DOUBLE_PRESENT.split("def extend")[0]
DOUBLE_BADSIG = DOUBLE_ABSENT + "def extend(): pass\n"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=GIT_ENV)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path
    _git(root, "init", "-q", "-b", "main")
    sdlc = root / ".sdlc"
    (sdlc / "plans").mkdir(parents=True)
    (sdlc / "state" / "work").mkdir(parents=True)
    (root / ".gitignore").write_text(".sdlc/state/\n.sdlc/work/\n.sdlc/acceptance/\n.sdlc/extend.log\n")
    (sdlc / "plans" / "2224.md").write_text(PLAN)
    cfg = {"work": {"enabled": True}, "gates": {"plan_review": {"enabled": True}}}
    (sdlc / "config.json").write_text(json.dumps(cfg))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "worktree", "add", "-q", "-b", "sdlc/2224", str(sdlc / "work" / "2224"))
    (sdlc / "state" / "work" / "2224.json").write_text(json.dumps(
        {"worktree": str(sdlc / "work" / "2224"), "branch": "sdlc/2224", "base": "main", "remote": "origin",
         "pr": ""}))
    crit = root / "crit.md"
    crit.write_text("### Task\nDo the thing.\n\n### Done when\n- the thing is done\n")
    doubles = root / "doubles"
    doubles.mkdir()
    for name, body in (("present", DOUBLE_PRESENT), ("absent", DOUBLE_ABSENT), ("badsig", DOUBLE_BADSIG)):
        (doubles / f"{name}.py").write_text(body)

    class P:
        pass
    p = P()
    p.root, p.sdlc, p.crit, p.doubles, p.cfg = root, sdlc, crit, doubles, cfg
    p.main_plan = sdlc / "plans" / "2224.md"
    p.branch_plan = sdlc / "work" / "2224" / ".sdlc" / "plans" / "2224.md"
    return p


DRIVER = ("import importlib.util, os, sys\n"
          "spec = importlib.util.spec_from_file_location('add_slice', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
          "spec.loader.exec_module(m)\n"
          "d = importlib.util.spec_from_file_location('acc', sys.argv[2]); acc = importlib.util.module_from_spec(d); d.loader.exec_module(acc)\n"
          "m._acceptance = lambda: acc\n"
          "if sys.argv[3] == 'crash': m._after_plan_write = lambda: os._exit(137)\n"
          "sys.exit(m.main(sys.argv[4:]))\n")


def with_acceptance(p):
    (p.sdlc / "acceptance").mkdir(exist_ok=True)
    (p.sdlc / "acceptance" / "2224.md").write_text("## Done when\n- a\n")


def cli(p, *extra, title="Fix the thing", goal="2224", mode="real"):
    """mode `real`: the real `work.py add-slice` CLI. Else the driver with the acceptance double of that name
    (present | absent | badsig) or `crash` (present double, hard exit after the plan write)."""
    tail = [str(p.sdlc), goal, "--title", title, "--criteria-file", str(p.crit), *extra]
    if mode == "real":
        argv = [sys.executable, str(WORK), "add-slice", *tail]
    else:
        double = "present" if mode == "crash" else mode
        argv = [sys.executable, "-c", DRIVER, str(S / "add_slice.py"), str(p.doubles / f"{double}.py"), mode, *tail]
    return subprocess.run(argv, cwd=p.root, capture_output=True, text=True)


def count(p):
    return len(fr.read_record(p.sdlc, "2224")["added"])


def test_happy_path_both_copies_identical_record_counted_extend_called_once(proj):
    with_acceptance(proj)
    r = cli(proj, mode="present")
    assert r.returncode == 0, r.stderr
    text = proj.main_plan.read_text()
    assert "## Added slice 1: Fix the thing" in text and "the thing is done" in text
    assert proj.branch_plan.read_bytes() == proj.main_plan.read_bytes()          # C9
    assert count(proj) == 1
    assert len((proj.sdlc / "extend.log").read_text().splitlines()) == 1
    assert "gates.plan_review is ON" in r.stdout and "verify.enforce" in r.stdout


def test_acceptance_extend_absent_refuses(proj):
    with_acceptance(proj)
    r = cli(proj, mode="absent")
    assert r.returncode == 4 and "acceptance.py extend" in r.stderr
    assert proj.main_plan.read_text() == PLAN and proj.branch_plan.read_text() == PLAN     # C8: probe before write
    assert count(proj) == 0


def test_no_acceptance_file_proceeds(proj):
    r = cli(proj)                                  # the real CLI and the real acceptance.py, no record
    assert r.returncode == 0 and "criteria not extended" in r.stdout
    assert count(proj) == 1


def test_extend_typeerror_refuses(proj):
    with_acceptance(proj)
    r = cli(proj, mode="badsig")
    assert r.returncode == 4 and "not guessing" in r.stderr
    assert count(proj) == 0


def test_at_cap_refuses(proj):
    cfg = dict(proj.cfg, followups={"max_added_slices": 1})
    (proj.sdlc / "config.json").write_text(json.dumps(cfg))
    assert cli(proj).returncode == 0
    before = proj.main_plan.read_text()
    r = cli(proj, title="Another")
    assert r.returncode == 3 and "slice-cap" in r.stderr and "handoff.py track" in r.stderr
    assert proj.main_plan.read_text() == before and count(proj) == 1


def test_rerun_adds_nothing_and_edited_criteria_retry_does_not_duplicate(proj):
    assert cli(proj).returncode == 0
    once = proj.main_plan.read_text()
    proj.crit.write_text("### Task\nDifferent text.\n")
    r = cli(proj)
    assert r.returncode == 0 and "already counted" in r.stdout and "never replaces" in r.stdout
    assert proj.main_plan.read_text() == once and count(proj) == 1


def test_crash_resume_counts_once(proj):
    r = cli(proj, mode="crash")
    assert r.returncode == 137
    assert "## Added slice 1" in proj.main_plan.read_text() and count(proj) == 0
    r2 = cli(proj)
    assert r2.returncode == 0, r2.stderr
    assert proj.main_plan.read_text().count("## Added slice") == 1 and count(proj) == 1


def test_selector_ceiling_refuses(proj):
    selectors = "".join("- `tests/test_f%d.py`\n" % n for n in range(25))
    proj.main_plan.write_text("# plan\n\n## Tests\n" + selectors)
    proj.branch_plan.write_text("# plan\n\n## Tests\n" + selectors)
    _git(proj.root, "add", "-A", "-f")
    before = proj.main_plan.read_text()
    r = cli(proj, "--test", "tests/test_new.py::t")
    assert r.returncode == 5 and "FILE selector" in r.stderr
    assert proj.main_plan.read_text() == before and count(proj) == 0


def test_test_selector_is_appended_unless_a_file_selector_covers_it(proj):
    assert cli(proj, "--test", "tests/test_a.py::case", "--test", "tests/test_new.py").returncode == 0
    text = proj.main_plan.read_text()
    assert "- `tests/test_new.py`" in text and "test_a.py::case" not in text


_REAL_LOAD = add_slice._load


def _fcntl_less(name, path=None):
    m = _REAL_LOAD(name, path)
    if name == "followup_route":
        m.fcntl = None
    return m


def test_fcntl_absent_refuses(proj, monkeypatch):
    monkeypatch.setattr(add_slice, "_load", _fcntl_less)
    assert add_slice.main([str(proj.sdlc), "2224", "--title", "t", "--criteria-file", str(proj.crit)]) == 5
    assert proj.main_plan.read_text() == PLAN


def test_cap_race_deterministic(proj, monkeypatch):
    """Thread A is held inside the critical section right after the cap check; B must block on the lock, and once A
    finishes B sees the cap reached. Without the lock held across check+write, B passes the check and both write."""
    (proj.sdlc / "config.json").write_text(json.dumps(dict(proj.cfg, followups={"max_added_slices": 1})))
    gate, inside = threading.Event(), threading.Event()
    first = []

    def hold():
        if not first:
            first.append(1)
            inside.set()
            gate.wait(5)
    monkeypatch.setattr(add_slice, "_after_check", hold)
    results = {}

    def run(key, title):
        results[key] = add_slice.main([str(proj.sdlc), "2224", "--title", title, "--criteria-file", str(proj.crit)])
    a = threading.Thread(target=run, args=("a", "slice a"))
    a.start()
    assert inside.wait(5)
    b = threading.Thread(target=run, args=("b", "slice b"))
    b.start()
    b.join(0.4)
    assert b.is_alive()                                  # blocked on the lock, not past the check
    gate.set()
    a.join(5)
    b.join(5)
    assert results == {"a": 0, "b": 3} and count(proj) == 1


def _gate(proj, sha=None):
    cfg = json.loads((proj.sdlc / "config.json").read_text())
    rec = work._record(proj.sdlc, "2224")
    return work._plan_review_refusal(str(proj.sdlc), cfg, rec, "2224", work._run)


def _review(proj, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    cfg = json.loads((proj.sdlc / "config.json").read_text())
    sha = _mod("review_context").plan_sha256(proj.main_plan)
    work.record_plan_review(str(proj.sdlc), cfg, "2224", "SOUND", sha)


def test_gate_refuses_until_fresh_plan_review_tracked_branch_copy(proj, monkeypatch):
    _review(proj, monkeypatch)
    assert _gate(proj) == ""
    assert cli(proj).returncode == 0
    assert "recorded" in _gate(proj) or "plan" in _gate(proj)             # state (i): stale record refused
    _review(proj, monkeypatch)
    assert _gate(proj) == ""


def test_later_slices_edit_main_only_and_the_gate_still_refuses(proj, monkeypatch):
    assert cli(proj).returncode == 0                     # slice 1: tracked clean branch copy edited too
    r2 = cli(proj, title="Second")
    assert r2.returncode == 0 and "branch copy of the plan is modified or untracked" in r2.stdout
    r3 = cli(proj, title="Third")
    assert r3.returncode == 0
    text = proj.main_plan.read_text()
    assert text.count("## Added slice") == 3 and "## Added slice 3: Third" in text
    assert proj.branch_plan.read_text().count("## Added slice") == 1       # untouched; `work.py commit` carries main
    assert count(proj) == 3
    assert _gate(proj) != ""
    _review(proj, monkeypatch)
    assert _gate(proj) == ""


def test_never_writes_slices_json_and_notices_when_present(proj):
    slices = proj.sdlc / "plans" / "2224.slices.json"
    slices.write_text("[]")
    r = cli(proj)
    assert r.returncode == 0 and "slices.json exists and was NOT updated" in r.stdout
    assert slices.read_text() == "[]"

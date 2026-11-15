"""Pure move from tests/test_add_slice.py at 4042e97 (#2224 amendment 2): the guard nodes (pass at base); test bodies byte-identical.

`work.py add-slice` (#2224): a follow-up joins its goal's plan as a slice, counted in a code-owned record.

Run: python3 -m pytest tests/test_add_slice*.py
(generic form: python -m pytest tests/test_add_slice*.py)

The C7/C9/C10/C12 tests drive the REAL CLI by subprocess (`python3 skills/sigma-loop/scripts/work.py add-slice ...`).
`acceptance.extend` does not exist until #2222 lands. Tests that need an acceptance record therefore never touch the
real acceptance.py: a DRIVER subprocess loads the real add_slice module and replaces its test-only `_acceptance` seam with a
double that has `extend` present or absent, so nothing flips when #2222 merges. Tests with no acceptance record use the
real `work.py add-slice` CLI. The e2e path through the real `extend` is UNPROVEN until then.

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
                  "def extend(sdlc_dir, goal, lines):\n"
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


def test_unsafe_stem_refuses(proj):
    r = cli(proj, goal="../x")
    assert r.returncode == 2 and not (proj.sdlc.parent / "x.json").exists()
    assert count(proj) == 0 and proj.main_plan.read_text() == PLAN

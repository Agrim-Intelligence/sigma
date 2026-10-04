"""#596 - no test runs a command over the whole repository under a short fixed subprocess timeout.

A fixed limit does not scale with repository size or runner speed: the growth-audit CLI test timed
out on a slow runner at a 15 second limit while the scan itself only grows. A test of a tool's logic
builds a small tree (the tool is fast on it, so any hang guard is generous); the one whole-repository
scan is a separately named slow test with a large, reasoned limit.

Known limits of this guard (a structural check, not proof): it sees only `subprocess.run` with a literal
numeric `timeout=`, a root constant named below (or a variable assigned from one in the same function),
and not `Popen`/`check_output`, a limit held in a variable, or a module-level call. It can also flag a
future test that merely runs a script located under the root; the fix there is the same, a larger limit.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT_NAMES = {"ROOT", "REPO", "REPO_ROOT"}   # the repository-root constants the tests use
SHORT = 60                      # seconds: a fixed limit below this over the whole repo is the defect


def _mentions(node, names):
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def short_whole_repo_runs(source):
    """Line numbers of subprocess.run calls with a constant timeout under SHORT that name the repo
    root as the command or cwd, directly or through a variable the same function assigned from it."""
    hits = []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = set(ROOT_NAMES)
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and _mentions(node.value, names):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and ast.unparse(node.func) == "subprocess.run"):
                continue
            limit = next((k.value for k in node.keywords if k.arg == "timeout"), None)
            if not (isinstance(limit, ast.Constant) and isinstance(limit.value, (int, float))
                    and limit.value < SHORT):
                continue
            rest = node.args + [k.value for k in node.keywords if k.arg == "cwd"]   # env= is not a scan target
            if any(_mentions(a, names) for a in rest):
                hits.append(node.lineno)
    return sorted(set(hits))


def test_no_test_runs_the_whole_repository_under_a_short_subprocess_timeout():
    found = []
    for path in sorted(TESTS.glob("test_*.py")):
        found += ["%s:%d" % (path.name, n) for n in short_whole_repo_runs(path.read_text(encoding="utf-8"))]

    assert found == [], (
        "a subprocess over the whole repository under a fixed short timeout flakes on a loaded runner; "
        "build a small tree, or make it the one slow test with a limit derived from a measurement "
        "(raising the limit is the fix for a command that is not a scan): %s" % found)


def test_detector_flags_the_old_growth_audit_gesture_and_passes_a_fixture_run():
    """The detector's own control: it must go red on the shape that flaked, and stay quiet otherwise."""
    old = (
        "def test_x():\n"
        "    command = [sys.executable, str(SCRIPT), str(ROOT)]\n"
        "    subprocess.run(command, check=True, capture_output=True, text=True, timeout=15)\n"
    )
    fixture = (
        "def test_x(tmp_path):\n"
        "    command = [sys.executable, str(SCRIPT), str(tmp_path)]\n"
        "    subprocess.run(command, check=True, capture_output=True, text=True, timeout=15)\n"
    )
    generous = old.replace("timeout=15", "timeout=600")

    assert short_whole_repo_runs(old) == [3]
    assert short_whole_repo_runs(fixture) == []
    assert short_whole_repo_runs(generous) == []

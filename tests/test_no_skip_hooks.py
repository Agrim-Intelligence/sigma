"""No hook or marker can quietly disarm the guards behind the public export's self-consistency (#1203).

Ordinary skips are NOT pinned: the suite has ~100 legitimate `skip`/`skipif`/`xfail` uses in ~60 files
(Windows-only, a missing CLI, a shallow clone), and a ledger of all of them would be churn that hides the
few that matter. What is pinned is the vector a reviewer used: a conftest hook that skips by rule, and a
skip/xfail marker on the guard tests themselves. The only skip the builder adds is the explicit
`EXPORT-SKIP` one it writes into the EXPORTED copies of five tests (tools/build_public_tree.py).
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOKS = {"pytest_collection_modifyitems", "pytest_runtest_setup", "pytest_runtest_call", "pytest_runtest_makereport",
         "pytest_pyfunc_call", "pytest_generate_tests"}
GUARD_TESTS = {
    "tests/test_write_surface.py": {"test_committed_inventory_matches_the_tracked_write_surface"},
    "tests/test_bk_gate.py": {"test_guard_pin_and_inventory"},
    "tests/test_readiness_exposure_scan.py": {"test_the_tracked_scan_of_this_tree_exits_0_with_no_stale_allowlist_entry"},
    "tests/test_b6_466.py": {"test_documented_gesture_resolves_the_rows"},
    "tests/test_goal_state_disposition.py": {"test_documented_gesture_resolves_the_rows"},
    "tests/test_liveness_disposition.py": {"test_documented_gesture_resolves_the_rows"},
    "tests/test_worktree_disposition.py": {"test_documented_gesture_resolves_the_rows"},
    "tests/test_no_private_sdlc_citations.py": {"test_no_shipped_file_cites_an_untracked_sdlc_design_or_plan"},
}


def _conftests():
    return [p for p in ROOT.rglob("conftest.py") if ".git" not in p.parts and "evals" not in p.parts]


def test_no_conftest_defines_a_hook_that_can_skip_by_rule():
    bad = []
    for path in _conftests():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in HOOKS:
                bad.append("%s:%s" % (path.relative_to(ROOT), node.name))
            if isinstance(node, ast.Attribute) and node.attr == "add_marker":
                bad.append("%s:add_marker" % path.relative_to(ROOT))
    assert bad == [], bad


def _is_skip(dec):
    text = ast.unparse(dec)
    return any(k in text for k in ("skip", "xfail")) and "EXPORT-SKIP" not in text


def test_the_guard_tests_carry_no_skip_or_xfail_marker():
    bad = []
    for rel, names in GUARD_TESTS.items():
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in names:
                found.add(node.name)
                bad += ["%s:%s" % (rel, node.name) for d in node.decorator_list if _is_skip(d)]
        assert found == names, (rel, names - found)
        marks = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in n.targets)]
        bad += ["%s:pytestmark" % rel for m in marks if _is_skip(m.value)]
    assert bad == [], bad

"""A static pin on the vectors that could quietly disarm the guards behind the public export's
self-consistency (#1203). It is NOT a lock.

Pinned: (1) no conftest defines a collection/run/report hook (the list is HOOKS), uses `add_marker`, or
assigns `collect_ignore` / `collect_ignore_glob`; (2) the eight guard tests carry no skip/xfail decorator
or `pytestmark`, call no `pytest.skip` / `importorskip` / `xfail`, and `return` nowhere before their first
`assert`; (3) the only exemption is the explicit skip that the export-time transform of #1203 (the
builder's `export_transforms`, in the PR that adds it; absent from a tree without it) writes into the
EXPORTED copy of exactly five named tests, matched by test name AND by the exact decorator text.

NOT covered, by design of a static check: a blanket skip (a hook or an autouse fixture that skips every
test, or a skip in an imported helper), `--deselect`/`-k`/`addopts` in `pytest.ini`, `pyproject.toml` or
`tox.ini`, a plugin, and the CI command line itself. Mitigation: review of any conftest/pytest-config
change, and the measured exported-suite counts in the #1203 PR (passed / skipped / the five EXPORT-SKIP
lines of `pytest -rs`). Ordinary skips (~100 in ~60 files) are deliberately not pinned.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOKS = {"pytest_collection_modifyitems", "pytest_runtest_setup", "pytest_runtest_call", "pytest_runtest_makereport",
         "pytest_pyfunc_call", "pytest_generate_tests", "pytest_ignore_collect", "pytest_configure",
         "pytest_runtest_logreport", "pytest_runtest_protocol", "pytest_collect_file", "pytest_deselected"}
IGNORE_NAMES = {"collect_ignore", "collect_ignore_glob"}
#: the five tests the export-time transform skips in the EXPORTED copy, and the exact decorator it writes
EXPORT_SKIPPED = {
    ("tests/test_b6_466.py", "test_documented_gesture_resolves_the_rows"),
    ("tests/test_goal_state_disposition.py", "test_documented_gesture_resolves_the_rows"),
    ("tests/test_liveness_disposition.py", "test_documented_gesture_resolves_the_rows"),
    ("tests/test_worktree_disposition.py", "test_documented_gesture_resolves_the_rows"),
    ("tests/test_no_private_sdlc_citations.py", "test_no_shipped_file_cites_an_untracked_sdlc_design_or_plan"),
}
EXPORT_DECORATOR = ("__import__('pytest').mark.skip(reason='EXPORT-SKIP public export without .sdlc/: "
                    "this test reads the private .sdlc store or cites its records')")  # equality, not a prefix

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


def _only_registers_markers(func):
    """The one reviewed hook: `pytest_configure` of tests/conftest.py only registers marker names."""
    calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)]
    return (len(func.body) == len([n for n in func.body if isinstance(n, ast.Expr)]) and calls
            and all(isinstance(c.func, ast.Attribute) and c.func.attr == "addinivalue_line"
                    and c.args and isinstance(c.args[0], ast.Constant) and c.args[0].value == "markers" for c in calls))


def test_no_conftest_defines_a_hook_that_can_skip_by_rule():
    bad = []
    for path in _conftests():
        rel = path.relative_to(ROOT)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.FunctionDef) and node.name == "pytest_configure" and str(rel) == "tests/conftest.py"
                    and _only_registers_markers(node)):
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in HOOKS:
                bad.append("%s:%s" % (rel, node.name))
            if isinstance(node, ast.Attribute) and node.attr == "add_marker":
                bad.append("%s:add_marker" % rel)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else []
            bad += ["%s:%s" % (rel, t.id) for t in targets if isinstance(t, ast.Name) and t.id in IGNORE_NAMES]
    assert bad == [], bad


def _is_skip(dec, rel, name):
    text = ast.unparse(dec)
    if not any(k in text for k in ("skip", "xfail")):
        return False
    return not ((rel, name) in EXPORT_SKIPPED and text == EXPORT_DECORATOR)


#: reviewed: a guard that runs against a checkout and cannot (an archive has no .git)
ALLOWED_CALLS = {("tests/test_readiness_exposure_scan.py", "test_the_tracked_scan_of_this_tree_exits_0_with_no_stale_allowlist_entry"):
                 ["not a git checkout"]}


def _body_nodes(func):
    """Every node of the function's BODY. Its decorators are judged by `_is_skip`: the exported copy's
    explicit skip IS a call (`mark.skip(...)`), and walking the whole definition flagged it (#1203)."""
    return [n for stmt in func.body for n in ast.walk(stmt)]


def _skipping_calls(func, allowed=()):
    out = []
    for node in _body_nodes(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("skip", "importorskip", "xfail"):
            if not (node.func.attr == "skip" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in allowed):
                out.append(node.func.attr)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("skip", "importorskip", "xfail"):
            out.append(node.func.id)
    return out


def _returns_before_first_assert(func):
    nodes = _body_nodes(func)
    first = min((n.lineno for n in nodes if isinstance(n, ast.Assert)), default=None)
    return [n.lineno for n in nodes if isinstance(n, ast.Return) and (first is None or n.lineno < first)]


def violations(rel, text):
    """The guard-test findings of one test file's source text (the file as it is in THIS tree, or as the
    export transform wrote it)."""
    bad = []
    names = GUARD_TESTS[rel]
    tree = ast.parse(text)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found.add(node.name)
            bad += ["%s:%s: marker" % (rel, node.name) for d in node.decorator_list if _is_skip(d, rel, node.name)]
            bad += ["%s:%s: %s()" % (rel, node.name, c) for c in _skipping_calls(node, ALLOWED_CALLS.get((rel, node.name), ()))]
            bad += ["%s:%s: return before the first assert (line %d)" % (rel, node.name, n) for n in _returns_before_first_assert(node)]
    if found != names:
        bad.append("%s: guard tests missing: %s" % (rel, sorted(names - found)))
    marks = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in n.targets)]
    bad += ["%s:pytestmark" % rel for m in marks if any(k in ast.unparse(m.value) for k in ("skip", "xfail"))]
    return bad


def test_the_guard_tests_carry_no_skip_marker_call_or_early_return():
    bad = []
    for rel in GUARD_TESTS:
        bad += violations(rel, (ROOT / rel).read_text(encoding="utf-8"))
    assert bad == [], bad


def _transformed(rel):
    """The file as the builder's export transform writes it (the real function, the real source text)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_public_tree_pin", ROOT / "tools" / "build_public_tree.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = (ROOT / rel).read_bytes()
    oid = mod.blob_id(data)
    _plan, changed, _rows = mod.export_transforms([(rel, "100644", oid)], [".sdlc"], lambda _oid: data)
    return changed[rel].decode("utf-8") if rel in changed else None


def test_the_pin_accepts_exactly_what_the_export_transform_writes():
    """The interaction that went red on day one (#1203 x the pin): run the pin's own check on the
    transformed copy of each of the five tests; it must pass for them and fail for any variation."""
    for rel, name in sorted(EXPORT_SKIPPED):
        text = _transformed(rel)
        assert text is not None and EXPORT_DECORATOR in ast.unparse(ast.parse(text)), rel
        assert violations(rel, text) == [], (rel, violations(rel, text))
        # a different reason, the call form in the body, a pytestmark, an early return: each is red
        assert violations(rel, text.replace("without .sdlc/", "without anything")) != [], rel
        deco = next(l for l in text.splitlines() if l.startswith("@__import__"))
        assert violations(rel, text.replace(deco, deco + "\n@__import__('pytest').mark.xfail")) != [], rel
        assert violations(rel, _inject(text, name, "    __import__('pytest').skip('x')\n")) != [], rel
        assert violations(rel, _inject(text, name, "    return\n")) != [], rel
        assert violations(rel, text + "\npytestmark = __import__('pytest').mark.skip('x')\n") != [], rel


def _inject(text, name, line):
    """Put `line` first in the body of `name` (its signature is on one line in every target)."""
    out, done = [], False
    for l in text.splitlines(keepends=True):
        out.append(l)
        if not done and l.startswith("def %s(" % name) and l.rstrip().endswith(":"):
            out.append(line)
            done = True
    assert done, name
    return "".join(out)


def test_the_exempt_set_is_exactly_five_named_guard_tests():
    assert len(EXPORT_SKIPPED) == 5
    assert all(n in GUARD_TESTS[p] for p, n in EXPORT_SKIPPED)

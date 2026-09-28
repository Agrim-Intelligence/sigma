"""The core names no private path and imports no private package (S1-G1; strict since #2584).

WHAT IS CHECKED, over the public surface's Python outside tests/ (`_scanned()`, from
`tests/public_surface.py`, the surface all three guards share), docstrings skipped (module, class,
def and async def). The private roots are `_PRIVATE_ROOTS`, the one place this file spells them;
everything else is derived from it.
  - PATH (R1, a segment-start path): a string constant holding a private root followed by `/` at the
    start of a path segment. A name that merely CONTAINS a root (`.../api/chat...`, `web<root>/`)
    is not one -- the old substring rule flagged a Slack API URL (#2584 DD-7).
  - SEGMENT (R2, a segment-spelled path): a string constant with no whitespace, one of whose
    `/`-separated segments EQUALS a private root -- the path joins R1 cannot see
    (`ROOT / "<root>" / ...`, `os.path.join("..", "<root>", ...)`, `"/" + "<root>"`). A root R1
    already matched in the same constant is not counted twice. This was a separate guard over
    `skills/agrim-loop/` only until #2584 folded it in here, over the whole scanned surface.
  - IMPORT: a level-0 `import`/`from ... import` whose first dotted segment is EXACTLY a private
    package root (`_IMPORT_ROOTS`, derived) -- so `import pickle` no longer matches a two-letter
    root by prefix (#2584).
  - An UNPARSEABLE file is a finding (`cannot parse`), never silently clean.

STRICT. There is no baseline file and no exemption: the guard passes only on zero findings. No
command writes an exemption, so a new finding cannot be accepted by regeneration (#2584 owner
ruling 3). Every failure message ends with the recheck gesture, `Recheck: python3 tests/<this
file>`. Run as a script, the file takes NO arguments (any argument exits 2, writing nothing), runs
every zero-argument test here, printing `ran: <name>` before each, and ends with one line:
`test_no_cross_boundary_paths: OK|FAIL (<k> finding(s) over <n> scanned file(s))`.

NOT COVERED: a root assembled from variables or f-string holes; a root split across concatenated
constants; `.sh`, `.md` and other non-Python files, and tests/ (the private-names guard covers
those textually); dynamic imports (`importlib`, `__import__`).

WHERE THE PRIVATE DIRECTION WENT. Private code naming the core's source tree is checked on the
private side, beside it (#2584); in a public snapshot of the core there is no private code, so the
core guard names no private file and passes there unchanged.
"""
import ast
import inspect
import pathlib
import re
import subprocess
import sys

import public_surface

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The private side's roots: the one listed spelling in this file (a pattern-list span of the
#: private-names guard). Everything below is derived from it.
_PRIVATE_ROOTS = ("insight", "pi", "collector", ".githooks")

#: The importable ones: a dotted directory is no Python package.
_IMPORT_ROOTS = tuple(r for r in _PRIVATE_ROOTS if not r.startswith("."))

_R1 = re.compile(r"(?<![\w.-])(%s)/" % "|".join(re.escape(r) for r in _PRIVATE_ROOTS))

_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

_RECHECK = "Recheck: python3 tests/test_no_cross_boundary_paths.py"


def _check_source(source, filename):
    """`[(line, kind, root)]` for one file; kind is `path` (R1), `segment` (R2), `import` (root is
    the imported module) or `cannot parse`."""
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError):
        return [(0, "cannot parse", "")]
    docstrings = set()
    for node in ast.walk(tree):
        if (isinstance(node, _DOCSTRING_OWNERS) and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            docstrings.add(id(node.body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            r1 = {m.group(1) for m in _R1.finditer(node.value)}
            out += [(node.lineno, "path", root) for root in sorted(r1)]
            if not re.search(r"\s", node.value):
                r2 = {seg for seg in node.value.split("/") if seg in _PRIVATE_ROOTS} - r1
                out += [(node.lineno, "segment", root) for root in sorted(r2)]
        elif isinstance(node, ast.Import):
            out += [(node.lineno, "import", a.name) for a in node.names
                    if a.name.split(".", 1)[0] in _IMPORT_ROOTS]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".", 1)[0] in _IMPORT_ROOTS:
                out.append((node.lineno, "import", node.module))
    return sorted(out, key=lambda r: r[0])


def _scanned():
    """The public surface's Python outside tests/ (`public_surface.public_files`): posix relpaths,
    sorted, any-case `.py`."""
    return [rel for rel in public_surface.public_files(ROOT)
            if rel.lower().endswith(".py") and not rel.startswith("tests/")]


def _scan():
    """`[(file, line, kind, root)]` over `_scanned()`."""
    return [(rel, line, kind, root) for rel in _scanned()
            for line, kind, root in _check_source(
                (ROOT / rel).read_text(encoding="utf-8-sig", errors="replace"), rel)]


def _assert_clean(findings):
    assert not findings, (
        "the core names a private path or imports a private package (%d finding(s)):\n"
        % len(findings)
        + "".join("  %s:%d [%s] %s\n" % f for f in findings)
        + "  -> the core reads no private file and imports no private package; there is no "
          "baseline to add it to\n" + _RECHECK)


def _kinds(source):
    return [(kind, root) for _line, kind, root in _check_source(source, "x.py")]


def _baselines_state():
    d = ROOT / "tests" / "boundary_baselines"
    return (d.exists(), sorted(p.name for p in d.iterdir()) if d.exists() else [])


def test_a_literal_private_path_is_found():
    r = _PRIVATE_ROOTS[0]
    assert _kinds(f'X = "{r}/rates/x.csv"\n') == [("path", r)]


def test_a_segment_built_private_path_is_found():
    r = _PRIVATE_ROOTS[0]
    assert _kinds(f'X = ROOT / "{r}" / "x"\n') == [("segment", r)]
    assert _kinds(f'import os\nX = os.path.join("..", "{r}", "x")\n') == [("segment", r)]
    assert _kinds(f'X = "/" + "{r}"\n') == [("segment", r)]
    assert _kinds(f'X = "/{r}"\nY = "a/{r}"\n') == [("segment", r), ("segment", r)]


def test_every_private_root_is_found_both_ways():
    """Every root, as a path and as a join segment, in one zero-argument test so the script
    gesture runs it too."""
    for root in _PRIVATE_ROOTS:
        assert _kinds(f'X = "{root}/x"\n') == [("path", root)], root
        assert _kinds(f'X = "{root}"\n') == [("segment", root)], root


def test_the_private_roots_are_the_name_guards():
    """`_PRIVATE_ROOTS`' VALUES are tied to the private-names guard's own patterns (#2584 review
    round 5): each package that guard refuses to see imported (`import <root>`), plus the hook
    directory it refuses to see named, is a root here, and nothing else is. Dropping a root from
    one guard alone goes red; a change needs both lists, in one reviewed diff."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_names_guard_for_roots", ROOT / "tests" / "test_no_private_names.py")
    names = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(names)
    descriptions = [d for _rx, d in names._PATTERNS]
    want = {d.split(" ", 1)[1] for d in descriptions if d.startswith("import ")}
    want |= {d.split(" ", 1)[0] for d in descriptions if d.endswith(" reference")
             and d.startswith(".")}
    assert set(_PRIVATE_ROOTS) == want, (sorted(_PRIVATE_ROOTS), sorted(want))


def test_a_name_that_merely_contains_a_root_is_not_found():
    r = _PRIVATE_ROOTS[0]
    assert _kinds('X = "https://slack.com/api/chat.postMessage"\n') == []
    assert _kinds(f'X = "no {r} here"\n') == []
    assert _kinds(f'X = "x/web{r}/y"\nY = "a{r}"\n') == []


def test_docstrings_are_not_scanned():
    r = _PRIVATE_ROOTS[0]
    source = (f'"""{r}/a"""\nclass C:\n    """{r}/b"""\ndef f():\n    """{r}/c"""\n'
              f'async def g():\n    """{r}/d"""\n')
    assert _kinds(source) == []


def test_private_package_imports_are_found_and_lookalikes_are_not():
    for root in _IMPORT_ROOTS:
        assert _kinds(f"import {root}\n") == [("import", root)]
        assert _kinds(f"from {root}.x import y\n") == [("import", f"{root}.x")]
        assert _kinds(f"import os, {root}.y as z\n") == [("import", f"{root}.y")]
        assert _kinds(f"from .{root} import x\n") == []
    assert _kinds("import pickle\nimport pipes\n") == []


def test_an_unparseable_file_is_a_finding():
    assert _kinds("print 'not python 3'\n") == [("cannot parse", "")]


def test_the_guard_takes_no_arguments_and_writes_nothing():
    before = _baselines_state()
    proc = subprocess.run([sys.executable, str(ROOT / "tests" / "test_no_cross_boundary_paths.py"),
                           "--write-baseline"], capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "this guard takes no arguments; there is no baseline to write (#2584)" in proc.stderr
    assert _baselines_state() == before


def test_the_real_core_is_scanned():
    scanned = _scanned()
    assert "hooks/gate_state.py" in scanned and "skills/agrim-loop/scripts/loop.py" in scanned
    print("cross-boundary guard scans %d file(s)" % len(scanned))


def test_no_cross_boundary_paths():
    _assert_clean(_scan())


def _zero_argument_tests():
    """Every `test_*` here that takes no fixture, in definition order."""
    return [(name, fn) for name, fn in list(globals().items())
            if name.startswith("test_") and inspect.isfunction(fn)
            and not inspect.signature(fn).parameters]


def _tests_the_script_cannot_run():
    """`test_*` functions that take a parameter: the script gesture cannot run them, so a guard
    whose documented invocation silently skipped one could not fail on it (#2584 review round 5)."""
    return [name for name, fn in list(globals().items())
            if name.startswith("test_") and inspect.isfunction(fn)
            and inspect.signature(fn).parameters]


# The script gesture sits at the END of the file on purpose: above a test, it would run before that
# test existed (#2580).
if __name__ == "__main__":
    if sys.argv[1:]:
        print("this guard takes no arguments; there is no baseline to write (#2584)", file=sys.stderr)
        sys.exit(2)
    failed = []
    for name, fn in _zero_argument_tests():
        print("ran: %s" % name)
        try:
            fn()
        except Exception as e:              # noqa: BLE001 - every failure is reported, then rc 1
            failed.append(name)
            print("FAILED %s: %s" % (name, e), file=sys.stderr)
    skipped = _tests_the_script_cannot_run()
    if skipped:
        failed.append("script coverage")
        print("FAILED script coverage: test(s) this script cannot run (they take a parameter): %s"
              % ", ".join(skipped), file=sys.stderr)
    findings, scanned = _scan(), _scanned()
    print("test_no_cross_boundary_paths: %s (%d finding(s) over %d scanned file(s))"
          % ("FAIL" if failed else "OK", len(findings), len(scanned)))
    sys.exit(1 if failed else 0)

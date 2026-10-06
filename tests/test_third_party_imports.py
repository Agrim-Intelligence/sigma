"""Shipped Python imports nothing outside the standard library and a named, licensed, optional list (#343).

WHAT IT PINS. Sigma Loop's shipped code claims to be standard-library only. This is the machine check of
that claim, so a new third-party import cannot arrive without someone also naming its licence. The
allowlist is `tests/fixtures/third_party_imports.json`; the licence facts behind each entry are in
`docs/launch/evidence/legal.md`. This is a fact check, not a legal one: it says what is imported, never
whether a licence is acceptable.

SHIPPED means: every tracked `*.py` file except everything under `tests/`, and the `test_*.py` and `conftest.py`
files under `evals/bench/tasks/` and `examples/` (the benchmark task repos and the example carry their own pytest
fixtures; they are fixtures, not plugin code). A `test_*.py` anywhere else is scanned like any other file.

THIRD-PARTY means: an absolute import whose first segment is neither in `sys.stdlib_module_names` nor a
module, package or plain directory sitting beside the importing file (how a script that sets up its own `sys.path` reaches
its siblings, for example `evals/bench/bench.py` and its `arms` package).

KNOWN LIMITS, stated so nobody reads more into a green run than is there:
  * Dynamic imports (`importlib.import_module(x)`, `__import__(x)`) are not covered; only `import` and
    `from ... import` statements are, as in `tests/test_import_boundary.py`.
  * A file or directory planted beside the importing file with the same name as a third-party package (a
    `requests.py`, or a `requests/` directory) makes that import read as local. `test_control_a_same_named_sibling_hides_the_import` pins this limit.
  * Shell and TypeScript are not Python: the one shipped npm channel is pinned by
    `test_npm_channel_dependencies_are_the_allowlisted_set`, nothing else non-Python is scanned.
  * The scan needs Python 3.10 or newer (`sys.stdlib_module_names`), which is the supported floor.

CONTROLS. The real-tree test is the gesture the docs give (`python -m pytest tests/test_third_party_imports.py -q`).
The planted-tree tests below run the same scanner on a temporary tree and each fails when the matching rule is
removed. The run in which each was first seen red is recorded in the pull request that added this file.
"""
import ast
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ALLOWLIST = json.loads((ROOT / "tests" / "fixtures" / "third_party_imports.json").read_text(encoding="utf-8"))
MODULES = ALLOWLIST["modules"]
NPM = ALLOWLIST["npm_channel"]

pytestmark = pytest.mark.skipif(not hasattr(sys, "stdlib_module_names"), reason="needs Python 3.10+")


FIXTURE_ROOTS = (("evals", "bench", "tasks"), ("examples",))


def _is_shipped(rel: str) -> bool:
    p = pathlib.PurePosixPath(rel)
    if p.suffix != ".py" or p.parts[0] == "tests":
        return False
    is_test_name = p.name.startswith("test_") or p.name == "conftest.py"
    return not (is_test_name and any(p.parts[:len(r)] == r for r in FIXTURE_ROOTS))


def _tracked_py(root: pathlib.Path):
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--", "*.py"], cwd=root, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [f for f in out.decode("utf-8").split("\0") if f and _is_shipped(f)]


def _imports(tree):
    """Yield (root_module, lineno, inside_function) for every absolute import statement.

    `inside_function` is True only when some enclosing node is a function or lambda; an import under a module-level
    `try:` or `if:` runs at import time and so is NOT lazy."""
    def walk(node, in_func):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    yield alias.name.split(".")[0], child.lineno, in_func
            elif isinstance(child, ast.ImportFrom) and child.level == 0 and child.module:
                yield child.module.split(".")[0], child.lineno, in_func
            yield from walk(child, in_func or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)))
    yield from walk(tree, False)


def violations(root: pathlib.Path, files, modules):
    """Every third-party import in `files` that the allowlist `modules` does not cover, as readable strings."""
    bad = []
    seen = {name: set() for name in modules}
    for rel in files:
        path = root / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        here = path.parent
        for name, lineno, in_func in _imports(tree):
            if name in sys.stdlib_module_names or (here / (name + ".py")).exists() or (here / name).is_dir():
                continue
            entry = modules.get(name)
            if entry is None:
                bad.append(f"{rel}:{lineno}: third-party import `{name}` is not in the allowlist")
                continue
            seen[name].add(rel)
            if rel not in entry.get("where", []):
                bad.append(f"{rel}:{lineno}: `{name}` is allowlisted, but not for this file")
            if entry.get("optional") and not in_func:
                bad.append(f"{rel}:{lineno}: optional `{name}` must be imported lazily, inside a function")
    for name, entry in modules.items():
        if not str(entry.get("licence", "")).strip():
            bad.append(f"allowlist entry `{name}` has no licence")
        for rel in entry.get("where", []):
            if rel not in seen[name]:
                bad.append(f"allowlist entry `{name}` names {rel}, which no longer imports it (stale entry)")
    return bad


def test_shipped_python_imports_only_stdlib_and_the_allowlist():
    files = _tracked_py(ROOT)
    if files is None:
        pytest.skip("not a git checkout (or git unavailable)")
    assert files, "found no shipped Python files: the scan would be vacuous"
    bad = violations(ROOT, files, MODULES)
    assert not bad, "third-party imports in shipped code:\n" + "\n".join(bad)


def test_npm_channel_dependencies_are_the_allowlisted_set():
    pkg = json.loads((ROOT / NPM["package_json"]).read_text(encoding="utf-8"))
    declared = sorted(set(pkg.get("dependencies", {})) | set(pkg.get("optionalDependencies", {})) | set(pkg.get("peerDependencies", {})))
    assert declared == sorted(NPM["dependencies"]), (
        f"{NPM['package_json']} declares {declared}, the allowlist says {NPM['dependencies']}; "
        "a new npm dependency needs its licence recorded in docs/launch/evidence/legal.md")


def test_every_allowlisted_name_appears_in_the_licence_fact_sheet():
    sheet = (ROOT / "docs" / "launch" / "evidence" / "legal.md").read_text(encoding="utf-8")
    for name, entry in MODULES.items():
        assert entry["pypi"] in sheet, f"{entry['pypi']} (import `{name}`) is missing from docs/launch/evidence/legal.md"
    for dep in NPM["dependencies"]:
        assert dep in sheet, f"{dep} is missing from docs/launch/evidence/legal.md"


# ---- controls on planted trees -------------------------------------------------------------------------

def _plant(tmp_path, files):
    for rel, text in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return list(files)


def test_control_a_planted_requests_import_goes_red_naming_it(tmp_path):
    files = _plant(tmp_path, {"skills/x/scripts/a.py": "import os\nimport requests\n"})
    bad = violations(tmp_path, files, MODULES)
    assert any("`requests`" in b for b in bad), bad


def test_control_from_import_and_submodule_import_are_caught(tmp_path):
    files = _plant(tmp_path, {"a.py": "from yaml import safe_load\n", "b.py": "import numpy.linalg as la\n"})
    bad = violations(tmp_path, files, {})
    assert any("`yaml`" in b for b in bad) and any("`numpy`" in b for b in bad), bad


def test_control_stdlib_relative_and_sibling_imports_are_clean(tmp_path):
    files = _plant(tmp_path, {"d/a.py": "import json, os.path\nfrom . import x\nfrom .y import z\nimport helper\nimport pkg.sub\n",
                              "d/helper.py": "", "d/pkg/__init__.py": ""})
    assert violations(tmp_path, files, {}) == []


def test_control_a_same_named_sibling_hides_the_import(tmp_path):
    """The documented limit, pinned: a planted requests.py (or requests/ directory) beside the importer hides the import."""
    files = _plant(tmp_path, {"d/a.py": "import requests\n", "d/requests.py": ""})
    assert violations(tmp_path, files, {}) == []
    files = _plant(tmp_path, {"e/a.py": "import requests\n", "e/requests/x.txt": ""})
    assert violations(tmp_path, files, {}) == []


def test_control_the_allowlisted_import_is_clean_only_where_listed_and_lazy(tmp_path):
    mods = {"slack_sdk": {"pypi": "slack-sdk", "licence": "MIT", "optional": True, "where": ["ok.py"]}}
    ok = _plant(tmp_path, {"ok.py": "def f():\n    from slack_sdk import WebClient\n    return WebClient\n"})
    assert violations(tmp_path, ok, mods) == []
    for text in ("import slack_sdk\n",
                 "try:\n    import slack_sdk\nexcept ImportError:\n    slack_sdk = None\n",
                 "if True:\n    import slack_sdk\n"):
        eager = _plant(tmp_path, {"ok.py": text})
        assert any("lazily" in b for b in violations(tmp_path, eager, mods)), text
    elsewhere = _plant(tmp_path, {"other.py": "def f():\n    import slack_sdk\n", "ok.py": "def f():\n    import slack_sdk\n"})
    assert any("other.py" in b and "not for this file" in b for b in violations(tmp_path, elsewhere, mods))


def test_control_a_stale_entry_and_a_missing_licence_are_caught(tmp_path):
    files = _plant(tmp_path, {"ok.py": "import os\n"})
    mods = {"slack_sdk": {"pypi": "slack-sdk", "licence": " ", "optional": True, "where": ["ok.py"]}}
    bad = violations(tmp_path, files, mods)
    assert any("no licence" in b for b in bad) and any("stale entry" in b for b in bad), bad


def test_control_test_files_and_tests_dir_are_not_shipped():
    assert not _is_shipped("tests/helper.py") and not _is_shipped("tests/sub/test_x.py")
    assert not _is_shipped("evals/bench/tasks/t/repo/test_a.py") and not _is_shipped("examples/e/conftest.py")
    assert _is_shipped("skills/x/scripts/test_like.py") and _is_shipped("hooks/conftest.py")
    assert not _is_shipped("README.md")
    assert _is_shipped("skills/sigma-loop/scripts/loop.py")

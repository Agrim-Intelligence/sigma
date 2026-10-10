"""Controls for golden task T3 (#885, slice 4 of epic #873): a multi-file refactor with a decoy and a generated file.

Every case copies the REAL `evals/golden/T3` into a temp root (with verify.py, the bench helper and the harness
`clean_env` reaches into), plants exactly one break, and runs the documented gesture `python3 evals/golden/verify.py`
(no flag) as a subprocess with `cwd` at that root. A case asserts exit 1 and that the output names T3 and the
property; the cases that matter for isolation also assert the property that must stay silent. No network, no model.

Unix only: verify.py refuses on Windows.
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import sysconfig

import pytest

from test_golden_verify import hashes, rehash

ROOT = pathlib.Path(__file__).resolve().parents[1]
TASK = ROOT / "evals" / "golden" / "T3"
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="verify.py refuses on Windows")

DECOY = "shipping/_vendor/legacy_rates.py"
GENERATED = "generated/rate_schema.py"
SHIM = "shipping/compat.py"
HIDDEN_FILES = {
    "files/test_hidden_rename.py", "verify.json",
    "naive/config/shipping.json", "naive/docs/rates.md", "naive/generated/rate_schema.py",
    "naive/shipping/__init__.py", "naive/shipping/_vendor/legacy_rates.py", "naive/shipping/cart.py",
    "naive/shipping/cli.py", "naive/shipping/compat.py", "naive/shipping/config.py", "naive/shipping/rates.py",
}


@pytest.fixture(scope="session")
def template(tmp_path_factory):
    """One planted root, built once: verify.py + the helper + the bench harness + the real T3 task."""
    base = tmp_path_factory.mktemp("t3-template")
    (base / "evals" / "golden").mkdir(parents=True)
    shutil.copyfile(ROOT / "evals" / "golden" / "verify.py", base / "evals" / "golden" / "verify.py")
    (base / "tools" / "readiness").mkdir(parents=True)
    shutil.copyfile(ROOT / "tools" / "readiness" / "bench_tasks.py", base / "tools" / "readiness" / "bench_tasks.py")
    shutil.copytree(ROOT / "evals" / "bench", base / "evals" / "bench",
                    ignore=shutil.ignore_patterns("tasks", "__pycache__"))
    shutil.copytree(TASK, base / "evals" / "golden" / "T3", ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    return base


def plant(template, tmp_path, mutate=None, rehash_hidden=False):
    root = tmp_path / "w"
    shutil.copytree(template, root)
    task = root / "evals" / "golden" / "T3"
    if mutate:
        mutate(task)
    if rehash_hidden:
        rehash(task)
    return root


def gesture(root):
    """The documented gesture, no flag."""
    done = subprocess.run([sys.executable, "evals/golden/verify.py"], cwd=root, capture_output=True, text=True,
                          timeout=120)
    return done, done.stdout + done.stderr


def edit(rel, old=None, new=None, tree="reference", append=None):
    def mutate(task):
        path = task / tree / rel
        text = path.read_text(encoding="utf-8")
        if append is not None:
            text += append
        else:
            assert old in text, f"{rel} has no {old!r}: the plant would be a no-op"
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
    return mutate


def add_file(rel, text, tree="reference"):
    def mutate(task):
        path = task / tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return mutate


def widen(entry, then):
    def mutate(task):
        path = task / "allowed_paths.json"
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) + [entry]), encoding="utf-8")
        then(task)
    return mutate


def set_allowed(old, new):
    def mutate(task):
        path = task / "allowed_paths.json"
        entries = json.loads(path.read_text(encoding="utf-8"))
        assert old in entries
        path.write_text(json.dumps([new if e == old else e for e in entries]), encoding="utf-8")
    return mutate


def test_t3_green_gesture(template, tmp_path):
    done, text = gesture(plant(template, tmp_path))
    assert done.returncode == 0, text
    assert "ok T3" in text and "0 red" in text, text


def test_t3_start_tree_hidden_fails(template, tmp_path):
    def mutate(task):
        shutil.rmtree(task / "repo")
        shutil.copytree(task / "reference", task / "repo")
    done, text = gesture(plant(template, tmp_path, mutate))
    assert done.returncode == 1 and "T3" in text and "hidden-on-start" in text, text
    assert "hidden-on-reference" not in text, text


MATRIX = [
    ("decoy-edit", edit(DECOY, append="\n# patched\n"), ["reference-diff", "hidden-on-reference"], []),
    ("generated-edit", edit(GENERATED, "calc_rate", "quote_rate"), ["reference-diff", "hidden-on-reference"], []),
    ("shim-edit", edit(SHIM, "calc_rate", "quote_rate"), ["reference-diff", "hidden-on-reference"], []),
    ("missed-deletion", add_file("docs/rates.md", "# Rates\n"), ["hidden-on-reference"], ["reference-diff"]),
    ("config-key-reverted", edit("config/shipping.json", "tariff_table", "rate_table"), ["hidden-on-reference"], []),
    ("callsite-reverted", edit("shipping/cart.py", "quote_rate", "calc_rate"), ["hidden-on-reference"], []),
    ("extra-file", add_file("shipping/extra.py", "X = 1\n"), ["reference-diff", "shipping/extra.py"],
     ["hidden-on-reference"]),
    ("widened-prefix-then-decoy-edit", widen("shipping/_vendor/", edit(DECOY, append="\n# patched\n")),
     ["hidden-on-reference"], ["reference-diff"]),
]


@pytest.mark.parametrize("name,mutate,present,silent", MATRIX, ids=[m[0] for m in MATRIX])
def test_t3_planted_break_is_red(template, tmp_path, name, mutate, present, silent):
    done, text = gesture(plant(template, tmp_path, mutate))
    assert done.returncode == 1, text
    assert "T3" in text and all(p in text for p in present), text
    assert not any(f"RED T3 {s}" in text or s in text for s in silent), text


def test_t3_pinned_hash_drift_is_red(template, tmp_path):
    hidden = edit("files/test_hidden_rename.py", append="\n# drift\n", tree="hidden")
    done, text = gesture(plant(template, tmp_path, hidden))
    assert done.returncode == 1 and "T3" in text and "hidden-sha256" in text, text


def test_t3_allowed_paths_exact_only(template, tmp_path):
    shipped = json.loads((TASK / "allowed_paths.json").read_text(encoding="utf-8"))
    assert shipped and not [e for e in shipped if e.endswith("/")], shipped
    for case, old, new, named in (("a", "docs/tariffs.md", "docs", "docs/tariffs.md"),
                                  ("b", "shipping/cli.py", "shipping/cl", "shipping/cli.py")):
        done, text = gesture(plant(template, tmp_path / case, set_allowed(old, new)))
        assert done.returncode == 1 and "RED T3 reference-diff" in text and f"{named} " in text, text


def test_t3_exact_only_pin_detects_prefix_regression(template, tmp_path):
    """Sensitivity control: with `_allowed` patched to a prefix match, the Case A plant is no longer red."""
    root = plant(template, tmp_path, set_allowed("docs/tariffs.md", "docs"))
    verify = root / "evals" / "golden" / "verify.py"
    text = verify.read_text(encoding="utf-8")
    old = 'return any(rel == entry or (entry.endswith("/") and rel.startswith(entry)) for entry in allowed)'
    assert old in text, "verify.py `_allowed` changed: update this control"
    verify.write_text(text.replace(old, "return any(rel.startswith(entry) for entry in allowed)"), encoding="utf-8")
    assert verify.read_text(encoding="utf-8") != text
    done, out = gesture(root)
    assert done.returncode == 0 and "RED T3 reference-diff" not in out, out


def _py_files():
    return sorted(p for p in TASK.rglob("*.py") if "__pycache__" not in p.parts)


def test_t3_task_shape():
    import ast
    task = json.loads((TASK / "task.json").read_text(encoding="utf-8"))
    assert task["origin"] == "planned" and task["trap"] is True and task["id"] == "T3"
    rubric = json.loads((TASK / "rubric.json").read_text(encoding="utf-8"))
    assert len(rubric["criteria"]) == 3
    allowed = json.loads((TASK / "allowed_paths.json").read_text(encoding="utf-8"))
    for rel in (DECOY, GENERATED, SHIM):
        assert (TASK / "repo" / rel).read_bytes() == (TASK / "reference" / rel).read_bytes(), rel
        assert rel not in allowed and not any(e.endswith("/") and rel.startswith(e) for e in allowed), rel
    first = (TASK / "repo" / GENERATED).read_text(encoding="utf-8").splitlines()[0]
    assert "AUTO-GENERATED" in first and "DO NOT EDIT" in first
    assert set(hashes(TASK)) == HIDDEN_FILES

    def is_stdlib(name):
        if name in ("shipping", "generated"):
            return True
        names = getattr(sys, "stdlib_module_names", None)  # 3.10+; older interpreters fall back to the location
        if names is not None:
            return name in names
        spec = importlib.util.find_spec(name)
        origin = getattr(spec, "origin", None) or ""
        return spec is not None and (origin in ("built-in", "frozen") or (
            origin.startswith(sysconfig.get_paths()["stdlib"]) and "site-packages" not in origin))

    banned_calls = {"write_text", "write_bytes", "rmtree", "remove", "unlink", "mkdir", "makedirs", "rmdir",
                    "rename", "touch", "write", "writelines"}
    for path in _py_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(is_stdlib(a.name.split(".")[0]) for a in node.names), path
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert is_stdlib(node.module.split(".")[0]), path
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                assert name not in banned_calls, f"{path}: {name}"
                if name == "open":
                    mode = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
                    mode += [k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
                    assert not any(set(str(m)) & set("wax+") for m in mode), path

    home = os.path.expanduser("~")
    needles = [home, pathlib.Path(home).parent.as_posix().rstrip("/") + "/"]
    for path in TASK.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            body = path.read_text(encoding="utf-8", errors="replace")
            assert not any(n in body for n in needles if len(n) > 1), path
    assert not [p for p in TASK.rglob("verify.py")]


def test_t3_readme_sentence_is_current():
    text = re.sub(r"\s+", " ", (ROOT / "evals" / "README.md").read_text(encoding="utf-8"))
    assert not re.search(r"land in later slices", text), "the stale 'tasks land in later slices' sentence is back"
    assert "`T3`" in text
    assert "tests/test_golden_t3.py" in text

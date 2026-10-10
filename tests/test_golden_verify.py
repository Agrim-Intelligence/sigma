"""Controls for the golden-task verify gesture (#881, slice 1 of epic #873).

The documented gesture is `python3 evals/golden/verify.py` (no flags). Every case below plants a
small tree in a temp dir (a copy of verify.py, the bench_tasks helper it loads, the bench harness
`clean_env` reaches into, and one green task `t1`), breaks exactly one property, runs that gesture as
a subprocess with `cwd` at the planted root, and asserts the exact exit code AND that the output names
the task and the property. One unbroken control must exit 0. No network, no model.

Unix only: verify.py refuses on Windows, so everything except the in-process refusal test is skipped there.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFY = ROOT / "evals" / "golden" / "verify.py"
BENCH_TASKS = ROOT / "tools" / "readiness" / "bench_tasks.py"
unix_only = pytest.mark.skipif(sys.platform == "win32", reason="verify.py refuses on Windows")

TASK_JSON = {
    "id": "t1", "kind": "bug-fix", "origin": "planned", "prompt": "Fix calc.add.\n\nMake the change in this repository.",
    "visible_command": ["python", "-c", "print('ok')"], "hidden_sha256": {}, "trap": False,
}
VERIFY_JSON = {"command": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", ".sigma-hidden/files"]}
HIDDEN_TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
FILES = {
    "task.json": None,  # written by _write_task
    "repo/calc.py": "def add(a, b):\n    return a - b\n",
    "repo/keep.py": "X = 1\n",
    "reference/calc.py": "def add(a, b):\n    return a + b\n",
    "reference/keep.py": "X = 1\n",
    "allowed_paths.json": json.dumps(["calc.py"]),
    "rubric.json": json.dumps({"criteria": ["adds"]}),
    "hidden/files/test_calc.py": HIDDEN_TEST,
    "hidden/verify.json": json.dumps(VERIFY_JSON),
}


def _put(base, rel, text):
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def hashes(task_dir):
    out = {}
    hidden = task_dir / "hidden"
    for path in sorted(hidden.rglob("*")):
        rel = path.relative_to(hidden).as_posix()
        if path.is_file() and not path.is_symlink() and "__pycache__" not in rel.split("/") \
                and not rel.endswith(".pyc") and path.name != ".DS_Store":
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def rehash(task_dir, **edits):
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    task["hidden_sha256"] = hashes(task_dir)
    task.update(edits)
    (task_dir / "task.json").write_text(json.dumps(task), encoding="utf-8")


@pytest.fixture(scope="session")
def template(tmp_path_factory):
    """One planted root, built once: verify.py + the helper + the bench harness + a green task t1."""
    base = tmp_path_factory.mktemp("golden-template")
    (base / "evals" / "golden").mkdir(parents=True)
    shutil.copyfile(VERIFY, base / "evals" / "golden" / "verify.py")
    (base / "tools" / "readiness").mkdir(parents=True)
    shutil.copyfile(BENCH_TASKS, base / "tools" / "readiness" / "bench_tasks.py")
    shutil.copytree(ROOT / "evals" / "bench", base / "evals" / "bench",
                    ignore=shutil.ignore_patterns("tasks", "__pycache__"))
    task = base / "evals" / "golden" / "t1"
    for rel, text in FILES.items():
        if text is not None:
            _put(task, rel, text)
    _put(task, "task.json", json.dumps(TASK_JSON))
    rehash(task)
    return base


def plant(template, tmp_path, mutate=None, late=None, rehash_after=True, task=True, extra=None):
    """Copy the template, drop t1 if asked, break one thing (`mutate`), recompute hashes, then `late` edits."""
    root = tmp_path / "w"
    shutil.copytree(template, root)
    t1 = root / "evals" / "golden" / "t1"
    if not task:
        shutil.rmtree(t1)
        return root
    if mutate:
        mutate(t1)
    if rehash_after:
        rehash(t1)
    if late:
        late(t1)
    return root


def run(root, extra_env=None, args=()):
    env = dict(os.environ, **(extra_env or {}))
    return subprocess.run([sys.executable, "evals/golden/verify.py", *args], cwd=root, env=env,
                          capture_output=True, text=True, timeout=120)


def out(done):
    return done.stdout + done.stderr


def red(template, tmp_path, prop, mutate=None, late=None, rehash_after=True, extra_env=None, code=1):
    done = run(plant(template, tmp_path, mutate, late, rehash_after), extra_env)
    assert done.returncode == code, out(done)
    text = out(done)
    assert "t1" in text and prop in text, text
    return text


def edit_task(**changes):
    def mutate(t1):
        task = json.loads((t1 / "task.json").read_text(encoding="utf-8"))
        for key, value in changes.items():
            if value is KeyError:
                task.pop(key, None)
            else:
                task[key] = value
        (t1 / "task.json").write_text(json.dumps(task), encoding="utf-8")
    return mutate


# ---- Task A: help, platform, zero tasks -----------------------------------------------------------------


@unix_only
def test_help_prints_usage_and_verifies_nothing(tmp_path):
    done = subprocess.run([sys.executable, str(VERIFY), "--help"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0
    assert "usage" in done.stdout.lower() and "RED" not in out(done)


@unix_only
def test_zero_tasks_is_loud_and_not_ok(template, tmp_path):
    done = run(plant(template, tmp_path, task=False))
    assert done.returncode == 0, out(done)
    assert "0 golden tasks found" in done.stdout and "nothing verified" in done.stdout
    assert not re.search(r"^ok\b", done.stdout, re.M)


@unix_only
def test_zero_task_path_is_not_a_blanket_pass(template, tmp_path):
    """The same root with one broken task must NOT take the exit-0 path."""
    red(template, tmp_path, "origin", edit_task(origin=KeyError))


def test_windows_is_refused_loudly(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("golden_verify_under_test", VERIFY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "platform", "win32")
    assert module.main([]) == 2
    assert "windows" in capsys.readouterr().err.lower()


# ---- Task B: the schema and the per-task checks ---------------------------------------------------------


@unix_only
def test_unbroken_tree_is_green(template, tmp_path):
    done = run(plant(template, tmp_path))
    assert done.returncode == 0, out(done)
    assert "ok t1" in done.stdout and "RED" not in out(done)


@unix_only
def test_missing_origin_is_red(template, tmp_path):
    red(template, tmp_path, "origin", edit_task(origin=KeyError))


@unix_only
@pytest.mark.parametrize("origin", ["issue:abc", "other", "issue:", "planned\n", 7])
def test_bad_origin_is_red(template, tmp_path, origin):
    red(template, tmp_path, "origin", edit_task(origin=origin))


@unix_only
@pytest.mark.parametrize("origin", ["planned", "issue:12"])
def test_good_origin_is_green(template, tmp_path, origin):
    done = run(plant(template, tmp_path, edit_task(origin=origin)))
    assert done.returncode == 0, out(done)


@unix_only
def test_id_must_equal_the_directory(template, tmp_path):
    red(template, tmp_path, "id", edit_task(id="zzz"))


@unix_only
def test_hidden_hash_mismatch_names_the_file(template, tmp_path):
    text = red(template, tmp_path, "hidden-sha256",
               late=lambda t1: (t1 / "hidden/files/test_calc.py").write_text(HIDDEN_TEST + "# tampered\n", encoding="utf-8"))
    assert "files/test_calc.py" in text


@unix_only
def test_extra_unhashed_hidden_file_is_red(template, tmp_path):
    text = red(template, tmp_path, "hidden-sha256",
               late=lambda t1: _put(t1, "hidden/files/test_extra.py", "def test_x():\n    pass\n"))
    assert "test_extra.py" in text


@unix_only
def test_declared_but_absent_hidden_file_is_red(template, tmp_path):
    def late(t1):
        task = json.loads((t1 / "task.json").read_text(encoding="utf-8"))
        task["hidden_sha256"]["files/test_ghost.py"] = "0" * 64
        (t1 / "task.json").write_text(json.dumps(task), encoding="utf-8")
    assert "test_ghost.py" in red(template, tmp_path, "hidden-sha256", late=late)


@unix_only
def test_hash_ignores_pycache_and_ds_store(template, tmp_path):
    def late(t1):
        _put(t1, "hidden/files/__pycache__/x.cpython-312.pyc", "junk")
        _put(t1, "hidden/files/stale.pyc", "junk")
        _put(t1, "hidden/.DS_Store", "junk")
    done = run(plant(template, tmp_path, late=late))
    assert done.returncode == 0, out(done)


@unix_only
def test_hidden_tests_passing_on_the_start_tree_is_red(template, tmp_path):
    text = red(template, tmp_path, "hidden-on-start",
               lambda t1: _put(t1, "repo/calc.py", "def add(a, b):\n    return a + b\n"))
    assert "exit 0" in text


@unix_only
def test_hidden_tests_failing_on_the_reference_is_red(template, tmp_path):
    red(template, tmp_path, "hidden-on-reference",
        lambda t1: _put(t1, "reference/calc.py", "def add(a, b):\n    return 0\n"))


@unix_only
def test_reference_change_outside_allowed_paths_is_red(template, tmp_path):
    text = red(template, tmp_path, "reference-diff", lambda t1: _put(t1, "reference/extra.py", "Y = 2\n"))
    assert "extra.py" in text


@unix_only
def test_reference_change_to_a_disallowed_existing_file_is_red(template, tmp_path):
    text = red(template, tmp_path, "reference-diff", lambda t1: _put(t1, "reference/keep.py", "X = 2\n"))
    assert "keep.py" in text


@unix_only
def test_reference_deleting_a_file_outside_allowed_paths_is_red(template, tmp_path):
    text = red(template, tmp_path, "reference-diff", lambda t1: (t1 / "reference/keep.py").unlink())
    assert "keep.py" in text


@unix_only
def test_allowed_directory_prefix_covers_new_files(template, tmp_path):
    def mutate(t1):
        _put(t1, "allowed_paths.json", json.dumps(["calc.py", "pkg/"]))
        _put(t1, "reference/pkg/new.py", "Z = 3\n")
    done = run(plant(template, tmp_path, mutate))
    assert done.returncode == 0, out(done)


NAIVE_FIXED = "def add(a, b):\n    return a + b\n"
NAIVE_STILL_BROKEN = "def add(a, b):\n    return a * b\n"


@unix_only
def test_naive_passing_the_hidden_tests_is_red(template, tmp_path):
    red(template, tmp_path, "naive", lambda t1: _put(t1, "hidden/naive/calc.py", NAIVE_FIXED))


@unix_only
def test_naive_failing_the_hidden_tests_is_green(template, tmp_path):
    done = run(plant(template, tmp_path, lambda t1: _put(t1, "hidden/naive/calc.py", NAIVE_STILL_BROKEN)))
    assert done.returncode == 0, out(done)


@unix_only
def test_naive_is_hashed(template, tmp_path):
    text = red(template, tmp_path, "hidden-sha256",
               late=lambda t1: _put(t1, "hidden/naive/calc.py", NAIVE_STILL_BROKEN))
    assert "naive/calc.py" in text


@unix_only
def test_naive_never_reaches_the_scored_tree(template, tmp_path):
    """The hidden test fails if `.sigma-hidden/naive` exists; green only when the bundle copy drops naive/."""
    def mutate(t1):
        _put(t1, "hidden/files/test_no_naive.py",
             "import os\n\n\ndef test_no_naive():\n    assert not os.path.exists('.sigma-hidden/naive')\n")
        _put(t1, "hidden/naive/calc.py", NAIVE_STILL_BROKEN)
        _put(t1, "hidden/naive/test_decoy.py", "def test_decoy():\n    assert True\n")
    done = run(plant(template, tmp_path, mutate))
    assert done.returncode == 0, out(done)


@unix_only
def test_rubric_must_be_a_json_object(template, tmp_path):
    red(template, tmp_path, "rubric", lambda t1: _put(t1, "rubric.json", json.dumps(["a", "b"])))


@unix_only
@pytest.mark.parametrize("rel", ["rubric.json", "allowed_paths.json", "hidden/verify.json"])
def test_a_missing_contract_file_is_red_not_a_traceback(template, tmp_path, rel):
    text = red(template, tmp_path, rel, lambda t1: (t1 / rel).unlink())
    assert "Traceback" not in text and "SystemExit" not in text


@unix_only
def test_allowed_paths_must_be_a_list_of_strings(template, tmp_path):
    red(template, tmp_path, "allowed_paths", lambda t1: _put(t1, "allowed_paths.json", json.dumps({"a": 1})))


@unix_only
@pytest.mark.parametrize("command", [
    "missing", [], ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
    ["python", "-m", "pytest", "-q", ".sigma-hidden/files"], ["python", "-q", "-p", "no:cacheprovider", ".sigma-hidden/files"],
    ["bash", "-m", "pytest", "-p", "no:cacheprovider", ".sigma-hidden/files"], "python -m pytest",
])
def test_malformed_verify_command_is_red_verify_json(template, tmp_path, command):
    def mutate(t1):
        spec = {} if command == "missing" else {"command": command}
        _put(t1, "hidden/verify.json", json.dumps(spec))
    text = red(template, tmp_path, "verify-json", mutate)
    assert "Traceback" not in text and "SystemExit" not in text


@unix_only
def test_a_directory_without_task_json_is_red_and_not_zero_tasks(template, tmp_path):
    def mutate(t1):
        (t1.parent / "t2").mkdir()
    root = plant(template, tmp_path, mutate)
    done = run(root)
    assert done.returncode == 1, out(done)
    assert "t2" in out(done) and "task-json" in out(done)
    assert "0 golden tasks found" not in out(done)


@unix_only
@pytest.mark.parametrize("command", [[], ["bash", "x"], "python x.py", ["python", 3]])
def test_visible_command_is_validated(template, tmp_path, command):
    red(template, tmp_path, "visible_command", edit_task(visible_command=command))


@unix_only
def test_visible_command_is_never_run(template, tmp_path):
    marker = tmp_path / "ran.txt"
    command = ["python", "-c", f"open({str(marker)!r}, 'w').write('x')"]
    done = run(plant(template, tmp_path, edit_task(visible_command=command)))
    assert done.returncode == 0, out(done)
    assert not marker.exists()


@unix_only
def test_invalid_task_json_is_exit_2(template, tmp_path):
    text = red(template, tmp_path, "task.json", lambda t1: (t1 / "task.json").write_text("{nope", encoding="utf-8"),
               rehash_after=False, code=2)
    assert "RED" not in text


@unix_only
def test_symlinks_are_red(template, tmp_path):
    for where in ("repo", "reference", "hidden/files"):
        def mutate(t1, where=where):
            os.symlink("calc.py", t1 / where / "link.py") if where != "hidden/files" else \
                os.symlink("test_calc.py", t1 / where / "link.py")
        text = red(template, tmp_path / where.replace("/", "_"), "symlink", mutate)
        assert "link.py" in text


def _link_out(rel):
    """Mutator: move `rel` of t1 to a sibling dir outside the task and leave a symlink in its place."""
    def mutate(t1):
        real = t1.parent.parent / "real" / rel.replace("/", "_")
        real.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(t1 / rel), str(real))
        os.symlink(real, t1 / rel)
    return mutate


@unix_only
@pytest.mark.parametrize("rel", ["repo", "reference", "hidden", "hidden/files", "task.json", "rubric.json",
                                 "allowed_paths.json", "hidden/verify.json"])
def test_a_symlinked_root_or_contract_file_is_red(template, tmp_path, rel):
    text = red(template, tmp_path, "symlink", _link_out(rel), rehash_after=False)
    assert rel in text and "Traceback" not in text


@unix_only
def test_a_symlinked_naive_dir_is_red(template, tmp_path):
    def mutate(t1):
        _put(t1, "hidden/naive/calc.py", "def add(a, b):\n    return 0\n")
        _link_out("hidden/naive")(t1)
    text = red(template, tmp_path, "symlink", mutate, rehash_after=False)
    assert "hidden/naive" in text


@unix_only
def test_a_symlinked_task_directory_is_red(template, tmp_path):
    def late(t1):
        real = t1.parent.parent / "real-t1"
        shutil.move(str(t1), str(real))
        os.symlink(real, t1)
    text = red(template, tmp_path, "symlink", late=late, rehash_after=False)
    assert "task directory" in text and "Traceback" not in text


@unix_only
@pytest.mark.parametrize("rel", ["repo/.sigma-hidden/x"])
def test_a_helper_crash_is_exit_2_naming_the_task_not_a_traceback(template, tmp_path, rel):
    done = run(plant(template, tmp_path, lambda t1: _put(t1, rel, "x\n")))
    assert done.returncode == 2, out(done)
    assert "t1" in out(done) and "Traceback" not in out(done) and "RED" not in out(done), out(done)


@unix_only
def test_a_dot_prefixed_task_dir_is_a_visible_skip(template, tmp_path):
    root = plant(template, tmp_path)
    (root / "evals" / "golden" / ".hidden-task").mkdir()
    done = run(root)
    assert done.returncode == 0, out(done)
    assert "skipped .hidden-task" in out(done), out(done)


@unix_only
def test_a_hung_hidden_test_is_killed_and_reported(template, tmp_path):
    started = time.monotonic()
    hang = "import time\n\n\ndef test_hang():\n    time.sleep(3600)\n"
    text = red(template, tmp_path, "timeout", lambda t1: _put(t1, "hidden/files/test_calc.py", hang),
               extra_env={"SIGMA_GOLDEN_TIMEOUT": "2"})
    assert time.monotonic() - started < 30
    assert "hidden-on-start" in text


@unix_only
@pytest.mark.parametrize("value", ["0", "601", "abc", "-3", ""])
def test_a_bad_timeout_override_is_exit_2(template, tmp_path, value):
    done = run(plant(template, tmp_path), {"SIGMA_GOLDEN_TIMEOUT": value})
    assert done.returncode == 2, out(done)
    assert "SIGMA_GOLDEN_TIMEOUT" in done.stderr


@unix_only
def test_only_selects_one_task_and_a_mistyped_id_is_exit_2(template, tmp_path):
    root = plant(template, tmp_path)
    assert run(root, args=["--only", "t1"]).returncode == 0
    done = run(root, args=["--only", "t9"])
    assert done.returncode == 2 and "t9" in done.stderr


# ---- Task C: pins of the helpers verify.py loads ---------------------------------------------------------

PINNED = {"clean_env", "_run", "_hidden_run", "_argv", "file_sha256"}


def _bench_tasks():
    spec = importlib.util.spec_from_file_location("bench_tasks_pinned", BENCH_TASKS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bench_tasks_private_names_verify_uses_exist():
    module = _bench_tasks()
    for name in sorted(PINNED):
        assert callable(getattr(module, name, None)), f"bench_tasks.{name} is gone; evals/golden/verify.py depends on it"


def test_clean_env_chain_reaches_arms_common_isolated_env(tmp_path, monkeypatch):
    """clean_env -> _bench_module().arms_common.isolated_env: the env must actually be produced."""
    module = _bench_tasks()
    monkeypatch.setenv("GH_TOKEN", "should-be-dropped")
    profile = tmp_path / "profile"
    env = module.clean_env(profile)
    for leaf in ("home", "claude-config", "codex-home", "tmp"):
        assert (profile / leaf).is_dir(), leaf
    assert pathlib.Path(env["HOME"]).resolve().is_relative_to(profile.resolve()) if hasattr(pathlib.PurePath, "is_relative_to") \
        else str(profile.resolve()) in str(pathlib.Path(env["HOME"]).resolve())
    assert "GH_TOKEN" not in env
    assert callable(module._bench_module().arms_common.isolated_env)


def test_verify_uses_only_pinned_names():
    source = VERIFY.read_text(encoding="utf-8")
    used = set(re.findall(r"\bbt\.(\w+)", source))
    assert used, "verify.py no longer reaches bench_tasks through `bt.`"
    assert used <= PINNED, f"unpinned bench_tasks names used by verify.py: {sorted(used - PINNED)}"


# ---- Task D: registrations -------------------------------------------------------------------------------


def test_ci_runs_the_gesture_right_after_the_quality_gate_with_the_same_gate():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    # The gesture runs in the nightly `full` job only; the required PR job `test` stays a subset (#957).
    lines = text[text.index("\n  full:\n"):].splitlines()
    names = [i for i, line in enumerate(lines) if line.strip().startswith("- name:")]
    gate = next(i for i in names if "quality gate" in lines[i])
    after = names[names.index(gate) + 1]
    assert "golden tasks verify" in lines[after]
    block = lines[after:names[names.index(after) + 1]]
    assert any(b.strip() == "run: python3 evals/golden/verify.py" for b in block)


def test_the_gesture_is_documented_where_the_gesture_guard_scans():
    assert "python3 evals/golden/verify.py" in (ROOT / "docs" / "agent-rules-detail.md").read_text(encoding="utf-8")
    readme = (ROOT / "evals" / "README.md").read_text(encoding="utf-8")
    assert "python3 evals/golden/verify.py" in readme and "contract/golden" in readme


def test_there_is_exactly_one_verify_py_under_evals():
    hits = [p for p in (ROOT / "evals").rglob("verify.py") if "__pycache__" not in p.parts]
    assert hits == [VERIFY], hits

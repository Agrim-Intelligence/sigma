"""Controls for the benchmark task set (#355): format, hidden-test isolation and verification.

The documented gesture is `python3 -m pytest tests/test_bench_tasks.py -q` (or
`python3 tools/readiness/bench_tasks.py check`). `test_the_committed_task_set_is_consistent` is the
repository-wide control: copy any hidden test file into `evals/bench/tasks/<id>/repo/` and it goes
red naming the copy. The other tests prove each rule against a small synthetic task set, no network
and no model.
"""
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "readiness" / "bench_tasks.py"
TASKS = ROOT / "evals" / "bench" / "tasks"
HIDDEN_TEXT = "def test_secret():\n    assert 1 + 1 == 3  # hidden\n"


def _tool():
    assert TOOL.is_file(), "tools/readiness/bench_tasks.py is missing"
    spec = importlib.util.spec_from_file_location("bench_tasks_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_tasks():
    assert TASKS.is_dir(), "evals/bench/tasks is missing"
    assert (TASKS / "manifest.json").is_file(), "evals/bench/tasks/manifest.json is missing"
    return json.loads((TASKS / "manifest.json").read_text(encoding="utf-8"))


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _copy_real(tmp_path):
    copy = tmp_path / "tasks"
    shutil.copytree(TASKS, copy)
    return copy


def _edit_task(directory, task_id, **changes):
    path = directory / task_id / "task.json"
    task = json.loads(path.read_text(encoding="utf-8"))
    task.update(changes)
    _write_json(path, task)


def _rebuild(tool, directory):
    tool.build_manifest(directory)


def _synthetic(tmp_path, tool, hidden_test, reference_fixed=True):
    """A one-task internal set plus its hidden root: `add(1, 2)` returns 3 only after the reference fix."""
    tasks = tmp_path / "syn-tasks"
    repo = tasks / "syn" / "repo"
    repo.mkdir(parents=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text("from calc import add\n\n\ndef test_add_zero():\n    assert add(5, 0) == 5\n",
                                       encoding="utf-8")
    hidden = tmp_path / "hidden"
    bundle = hidden / "syn"
    (bundle / "files").mkdir(parents=True)
    (bundle / "files" / "test_hidden_calc.py").write_text(hidden_test, encoding="utf-8")
    (bundle / "reference").mkdir()
    (bundle / "reference" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n" if reference_fixed else "def add(a, b):\n    return a - b\n",
        encoding="utf-8")
    (bundle / "run_hidden.py").write_text(tool.HIDDEN_RUNNER, encoding="utf-8")
    _write_json(bundle / "hidden.json", {"files": ["test_hidden_calc.py"], "run": ["test_hidden_calc.py"]})
    _write_json(bundle / "verify.json", {"command": ["python3", ".sigma-hidden/run_hidden.py"]})
    task = {"id": "syn", "kind": "bug-fix", "origin": "internal", "trap": False, "status": "ready",
            "prompt": "fix add", "source": "syn/repo",
            "visible_command": ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            "visible_expected": "pass", "hidden_bundle": "syn", "hidden_sha256": "", "hidden_files": {}}
    _write_json(tasks / "syn" / "task.json", task)
    tool.seal(tasks, hidden, "syn")
    _write_json(tasks / "manifest.json", {"schema": "sigma.benchmark-tasks/v1", "frozen": False, "model": {
        "id": "m", "training_cutoff": "2026-06", "cutoff_rule_date": "2026-07-01", "cutoff_source": "https://x"}})
    tool.build_manifest(tasks)
    return tasks, hidden


def test_the_committed_task_set_is_consistent():
    """Repository-wide control: also red when a hidden file's bytes appear anywhere under the tasks."""
    tool = _tool()
    _real_tasks()
    assert tool.check(TASKS) == []


def test_manifest_is_the_one_the_harness_loads_and_is_not_frozen_while_traps_await():
    tool = _tool()
    manifest = _real_tasks()
    assert tool.harness_accepts(TASKS) == ""
    assert manifest["frozen"] is False
    traps = [t for t in manifest["tasks"] if t["trap"]]
    assert len(traps) == 3 and all(t["status"] == "awaiting-author" for t in traps)
    assert 12 <= len(manifest["tasks"]) <= 18
    for row in manifest["tasks"]:
        if row["status"] == "ready":
            assert len(row["hidden_sha256"]) == 64


def test_manifest_names_the_pinned_model_cutoff_and_its_source():
    model = _real_tasks()["model"]
    assert model["id"] == "claude-sonnet-5-5"
    assert model["training_cutoff"] and model["cutoff_rule_date"] >= "2026-06-30"
    assert model["cutoff_source"].startswith("https://")


def test_copying_a_hidden_file_into_a_task_is_named_and_refused(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, HIDDEN_TEXT)
    assert [p for p in tool.check(tasks) if "copied into the repository" in p] == []
    (tasks / "syn" / "repo" / "test_hidden_calc.py").write_text(HIDDEN_TEXT, encoding="utf-8")
    problems = tool.check(tasks)
    assert any("syn:files/test_hidden_calc.py" in p and "syn/repo/test_hidden_calc.py" in p for p in problems)


def test_a_frozen_manifest_cannot_have_traps_awaiting_an_author(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    manifest["frozen"] = True
    _write_json(copy / "manifest.json", manifest)
    problems = tool.check(copy, hidden_root=tmp_path)
    assert any("awaiting an author" in p for p in problems)
    assert tool.check(copy, require_frozen=True) != []


def test_drift_between_manifest_and_task_files_is_refused(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    _edit_task(copy, "int-bugfix-1", prompt="a different prompt")
    assert any("drifted" in p for p in tool.check(copy))
    manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    manifest["tasks"] = manifest["tasks"][1:]
    _write_json(copy / "manifest.json", manifest)
    assert any("differ from task directories" in p for p in tool.check(copy))


def test_task_count_trap_count_and_hash_consistency_are_enforced(tmp_path):
    tool = _tool()
    few = _copy_real(tmp_path / "few")
    for victim in ("int-bugfix-1", "int-feature-1", "int-docs-1", "ext-lark-1618"):
        shutil.rmtree(few / victim)
    _rebuild(tool, few)
    assert any("owner decision" in p for p in tool.check(few))
    traps = _copy_real(tmp_path / "traps")
    shutil.rmtree(traps / "trap-3")
    _rebuild(tool, traps)
    assert any("2 trap tasks" in p for p in tool.check(traps))
    bad = _copy_real(tmp_path / "bad")
    _edit_task(bad, "int-bugfix-1", hidden_sha256="0" * 64)
    _rebuild(tool, bad)
    assert any("not the digest of hidden_files" in p for p in tool.check(bad))


def test_external_tasks_commit_no_tree_and_must_postdate_the_cutoff(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    fetch_path = copy / "ext-bottle-1539" / "fetch.json"
    fetch = json.loads(fetch_path.read_text(encoding="utf-8"))
    fetch["pr_created_at"] = "2026-06-01T00:00:00Z"
    fetch["stars"] = 10
    fetch["license"] = "GPL-3.0"
    _write_json(fetch_path, fetch)
    problems = tool.check(copy)
    assert any("before 2026-07-01" in p for p in problems) and any("fewer than 50 stars" in p for p in problems)
    assert any("is not MIT, BSD or Apache-2.0" in p for p in problems)
    subprocess.run(["git", "init", "-q"], cwd=copy, check=True)
    (copy / "ext-lark-1630" / "repo").mkdir()
    (copy / "ext-lark-1630" / "repo" / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=copy, check=True)
    assert any("external tree must not be committed" in p for p in tool.check(copy))


def test_a_hidden_root_inside_the_repository_is_refused():
    tool = _tool()
    _real_tasks()
    problems = tool.check(TASKS, hidden_root=ROOT / "docs")
    assert any("hidden root is inside the repository" in p for p in problems)


def test_bundle_digest_is_order_free_and_content_bound():
    tool = _tool()
    one = {"a.py": "1" * 64, "b/c.py": "2" * 64}
    assert tool.bundle_digest(one) == tool.bundle_digest(dict(reversed(list(one.items()))))
    assert tool.bundle_digest(one) != tool.bundle_digest({**one, "a.py": "3" * 64})
    assert tool.bundle_digest(one) != tool.bundle_digest({"x.py": "1" * 64, "b/c.py": "2" * 64})


def test_verify_accepts_a_hidden_test_that_fails_on_start_and_passes_on_reference(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    task = tool.load_tasks(tasks)["syn"]
    result = tool.verify_internal(tasks, hidden, "syn", task, python=sys.executable)
    assert result["status"] == "verified", result
    assert (result["visible_on_start"], result["hidden_on_start"], result["hidden_on_reference"]) == (
        "pass", "fail", "pass")


def test_verify_refuses_a_hidden_test_that_already_passes_on_the_starting_tree(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_zero():\n    assert add(5, 0) == 5\n")
    task = tool.load_tasks(tasks)["syn"]
    result = tool.verify_internal(tasks, hidden, "syn", task, python=sys.executable)
    assert result["status"] == "FAILED" and result["hidden_on_start"] == "pass"


def test_verify_refuses_a_reference_fix_that_does_not_make_the_hidden_tests_pass(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                               reference_fixed=False)
    task = tool.load_tasks(tasks)["syn"]
    result = tool.verify_internal(tasks, hidden, "syn", task, python=sys.executable)
    assert result["status"] == "FAILED" and result["hidden_on_reference"] == "fail"


def test_a_hung_hidden_test_is_killed_with_its_process_group(tmp_path):
    """The tool's runner is exercised in a child with its own deadline, so a runner that does not kill the group
    FAILS here (its pipe stays open and it would wait forever) instead of hanging the suite."""
    _tool()
    marker, pidfile = tmp_path / "alive", tmp_path / "pid"
    child = tmp_path / "child.py"
    child.write_text("import os, pathlib, sys, time\npathlib.Path(sys.argv[2]).write_text(str(os.getpid()))\n"
                     "while True:\n    pathlib.Path(sys.argv[1]).write_text(str(time.time()))\n    time.sleep(0.05)\n",
                     encoding="utf-8")
    parent = tmp_path / "parent.py"
    parent.write_text("import subprocess, sys, time\n"
                      f"subprocess.Popen([sys.executable, {str(child)!r}, {str(marker)!r}, {str(pidfile)!r}])\n"
                      "time.sleep(60)\n", encoding="utf-8")
    driver = ("import importlib.util, sys\n"
              f"spec = importlib.util.spec_from_file_location('t', {str(TOOL)!r})\n"
              "mod = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(mod)\n"
              f"print(mod._run([sys.executable, {str(parent)!r}], {str(tmp_path)!r}, timeout=2))\n")
    try:
        try:
            done = subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail("the runner did not return after its timeout: it left the process group alive")
        assert done.stdout.strip() == "(None, 'timeout')", done.stdout + done.stderr
        time.sleep(0.5)
        first = marker.read_text()
        time.sleep(0.5)
        assert marker.read_text() == first, "the grandchild survived the timeout"
    finally:
        if pidfile.exists():
            import os
            import signal
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass


def test_internal_starting_trees_give_the_recorded_visible_result_and_hold_no_hidden_names(tmp_path):
    """`pass` means exit 0 and `fail` means pytest exit 1 (tests ran and failed), never a collection error."""
    tool = _tool()
    _real_tasks()
    seen = set()
    for task_id, task in tool.load_tasks(TASKS).items():
        if task["origin"] != "internal" or task["status"] != "ready":
            continue
        copy = tmp_path / task_id
        shutil.copytree(TASKS / task_id / "repo", copy)
        code, tail = tool._visible_run(copy, task, sys.executable, 120)
        assert code == (0 if task["visible_expected"] == "pass" else 1), f"{task_id}: {tail}"
        seen.add(task["visible_expected"])
        assert not list(copy.glob("test_hidden*")), f"{task_id}: a hidden test name is in the starting tree"
    assert seen, "no internal task was checked"


def test_documented_cli_check_passes_and_frozen_check_refuses_an_unfrozen_set():
    _tool()
    ok = subprocess.run([sys.executable, str(TOOL), "check"], capture_output=True, text=True, cwd=ROOT)
    assert ok.returncode == 0 and "0 finding(s)" in ok.stdout, ok.stdout + ok.stderr
    frozen = subprocess.run([sys.executable, str(TOOL), "check", "--frozen"], capture_output=True, text=True, cwd=ROOT)
    assert frozen.returncode == 1 and "frozen is false" in frozen.stdout
    helped = subprocess.run([sys.executable, str(TOOL), "--help"], capture_output=True, text=True, cwd=ROOT)
    assert helped.returncode == 0 and "USAGE" in helped.stdout


def test_hidden_bundles_never_live_in_the_repository():
    """No file in the repository carries a hidden-test name; the hidden root is under the operator's home."""
    _real_tasks()
    names = [p for p in TASKS.rglob("*") if p.is_file() and (p.name.startswith("test_hidden") or
                                                              p.name in ("hidden.json", "verify.json", "run_hidden.py"))]
    assert names == []
    assert not (TASKS / "hidden").exists()


def test_materialize_fetches_only_the_base_tree_and_no_git_directory(tmp_path):
    tool = _tool()
    origin = tmp_path / "origin"
    origin.mkdir()

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=origin,
                              check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "uploadpack.allowAnySHA1InWant", "true")
    (origin / "lib.py").write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    (origin / "lib.py").write_text("VALUE = 2  # the fix\n", encoding="utf-8")
    git("commit", "-q", "-am", "fix")
    tasks = tmp_path / "tasks"
    _write_json(tasks / "ext-x" / "task.json", {"origin": "external"})
    _write_json(tasks / "ext-x" / "fetch.json", {"repo_url": str(origin), "base_sha": base})
    tree = tool.materialize_task(tasks, "ext-x")
    assert (tree / "lib.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (tree / ".git").exists()
    with pytest.raises(SystemExit):
        tool.materialize_task(tasks, "ext-x")


def test_an_external_task_cannot_record_failing_visible_tests_and_source_is_pinned(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    _edit_task(copy, "ext-bottle-1539", visible_expected="fail")
    _edit_task(copy, "int-bugfix-1", source="int-bugfix-1")
    _rebuild(tool, copy)
    problems = tool.check(copy)
    assert any("existing tests, which pass at the base commit" in p for p in problems)
    assert any("source must be int-bugfix-1/repo" in p for p in problems)


def test_a_prompt_that_links_the_fixing_pull_request_is_refused(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    fetch = json.loads((copy / "ext-bottle-1539" / "fetch.json").read_text(encoding="utf-8"))
    _edit_task(copy, "ext-bottle-1539", prompt=f"see https://github.com/x/y/pull/{fetch['pr_number']} for the fix")
    _rebuild(tool, copy)
    assert any("link to the fixing PR" in p for p in tool.check(copy))


def test_hidden_test_names_in_the_starting_repository_are_named(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add_for_real():\n    assert add(1, 2) == 3\n")
    assert tool.check(tasks, hidden_root=hidden) != [] and not [
        p for p in tool.check(tasks, hidden_root=hidden) if "hidden test names" in p]
    (tasks / "syn" / "repo" / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add_for_real():\n    assert add(5, 0) == 5\n", encoding="utf-8")
    assert any("hidden test names appear in the starting repository" in p and "test_add_for_real" in p
               for p in tool.check(tasks, hidden_root=hidden))


def test_a_bundle_for_a_task_awaiting_its_author_would_make_the_harness_run_the_placeholder(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    hidden = tmp_path / "hidden"
    for row in json.loads((copy / "manifest.json").read_text(encoding="utf-8"))["tasks"]:
        if row["status"] == "ready":
            (hidden / row["hidden_bundle"]).mkdir(parents=True)
    assert not [p for p in tool.check(copy, hidden_root=hidden) if "placeholder" in p]
    (hidden / "trap-1").mkdir()
    assert any("trap-1" in p and "placeholder" in p for p in tool.check(copy, hidden_root=hidden))


def test_the_harness_itself_refuses_the_unfrozen_manifest_while_a_trap_has_no_bundle(tmp_path):
    """The draft manifest cannot run by accident: `_validate_inputs` needs every task's bundle directory."""
    tool = _tool()
    bench = tool._bench_module()
    copy = _copy_real(tmp_path)
    hidden = tmp_path / "hidden"
    for row in json.loads((copy / "manifest.json").read_text(encoding="utf-8"))["tasks"]:
        if row["status"] == "ready":
            (hidden / row["hidden_bundle"]).mkdir(parents=True)
        (copy / row["source"]).mkdir(parents=True, exist_ok=True)
    tasks = bench._load_tasks(copy / "manifest.json")
    with pytest.raises(bench.BenchmarkRefusal, match="hidden bundle 'trap-1' is missing"):
        bench._validate_inputs(tasks, 1.0, hidden, tmp_path / "out" / "r.json", False, {}, tmp_path / "scr")


def test_a_ready_trap_needs_its_author_expected_catch_and_attestation(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    _edit_task(tasks, "syn", kind="trap-plan-defect", trap=True)
    tool.build_manifest(tasks)
    problems = tool.check(tasks, hidden_root=hidden)
    assert any("non-empty expected_catch.txt" in p for p in problems)
    assert any("non-empty author.json" in p for p in problems)
    assert any("must name its author" in p for p in problems)


def test_verify_fails_when_the_two_scoring_paths_disagree(tmp_path, monkeypatch):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    monkeypatch.setattr(tool._Scoring, "hidden", lambda self, tree, root, task: True)
    result = tool.verify_internal(tasks, hidden, "syn", tool.load_tasks(tasks)["syn"], python=sys.executable)
    assert result["status"] == "FAILED" and result["inconsistent"] is True


def test_materialize_refuses_a_tree_that_is_not_the_recorded_one_and_manifest_sha_reads_the_commit(tmp_path):
    tool = _tool()
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a, cwd=origin: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a],
                                                cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q")
    run("config", "uploadpack.allowAnySHA1InWant", "true")
    (origin / "lib.py").write_text("VALUE = 1\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    tasks = origin / "tasks"
    _write_json(tasks / "ext-x" / "task.json", {"origin": "external"})
    fetch = {"repo_url": str(origin), "base_sha": run("rev-parse", "HEAD"), "tree_sha256": "0" * 64}
    _write_json(tasks / "ext-x" / "fetch.json", fetch)
    with pytest.raises(SystemExit, match="not the one recorded"):
        tool.materialize_task(tasks, "ext-x")
    assert not (tasks / "ext-x" / "repo").exists()
    del fetch["tree_sha256"]
    _write_json(tasks / "ext-x" / "fetch.json", fetch)
    tool.materialize_task(tasks, "ext-x", record_digest=True)
    assert json.loads((tasks / "ext-x" / "fetch.json").read_text(encoding="utf-8"))["tree_sha256"]
    _write_json(tasks / "manifest.json", {"a": 1})
    run("add", "tasks/manifest.json")
    run("commit", "-q", "-m", "manifest")
    committed = tool.manifest_sha(tasks, "HEAD")
    (tasks / "manifest.json").write_text('{"a": 2}\n', encoding="utf-8")
    assert tool.manifest_sha(tasks, "HEAD") == committed != tool.manifest_sha(tasks)


def test_a_row_the_harness_would_refuse_is_reported_by_check(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    del manifest["tasks"][0]["visible_command"]
    _write_json(copy / "manifest.json", manifest)
    assert any("the harness refuses the manifest" in p for p in tool.check(copy))


def test_a_hidden_test_that_cannot_be_collected_does_not_count_as_failing_for_the_right_reason(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "import module_that_does_not_exist\n\n\ndef test_x():\n    assert True\n")
    result = tool.verify_internal(tasks, hidden, "syn", tool.load_tasks(tasks)["syn"], python=sys.executable)
    assert result["hidden_on_start"] == "fail" and result["hidden_on_start_exit"] != 1
    assert result["status"] == "FAILED" and "exit not 1" in result["reason"]


def test_selection_limits_per_repository_and_non_test_lines_are_checked(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    fetch_path = copy / "ext-bottle-1539" / "fetch.json"
    fetch = json.loads(fetch_path.read_text(encoding="utf-8"))
    fetch["nontest_lines_changed"] = 301
    _write_json(fetch_path, fetch)
    assert any("300 or fewer lines outside tests" in p for p in tool.check(copy))
    for name in ("ext-lark-1630", "ext-sqlparse-601", "ext-more-itertools-1252"):
        other = json.loads((copy / name / "fetch.json").read_text(encoding="utf-8"))
        other["repo_url"] = fetch["repo_url"]
        _write_json(copy / name / "fetch.json", other)
    assert any("at most 3 per repository" in p for p in tool.check(copy))


def test_the_starting_tree_digest_is_in_the_manifest_and_an_edit_after_sealing_is_named(tmp_path):
    tool = _tool()
    rows = {row["id"]: row for row in _real_tasks()["tasks"]}
    assert all(len(rows[i]["tree_sha256"]) == 64 for i, row in rows.items() if row["status"] == "ready")
    copy = _copy_real(tmp_path)
    (copy / "int-refactor-1" / "repo" / "pricing.py").write_text("TAX_RATE = 0.2\n", encoding="utf-8")
    assert any("starting tree differs from tree_sha256" in p for p in tool.check(copy))


def test_empty_and_tiny_files_never_count_as_a_leaked_hidden_file(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    (hidden / "syn" / "files" / "__init__.py").write_text("", encoding="utf-8")
    tool.seal(tasks, hidden, "syn")
    tool.build_manifest(tasks)
    (tasks / "syn" / "repo" / "__init__.py").write_text("", encoding="utf-8")
    assert [p for p in tool.check(tasks) if "copied into the repository" in p] == []


def test_third_party_code_runs_without_secret_shaped_environment_values(tmp_path, monkeypatch):
    tool = _tool()
    for name in ("GH_TOKEN", "ANTHROPIC_API_KEY", "MY_SECRET_VALUE", "AWS_PROFILE", "SSH_AUTH_SOCK"):
        monkeypatch.setenv(name, "leak-me")
    monkeypatch.setenv("BENCH_TASKS_HARMLESS", "kept")
    env = tool.clean_env(tmp_path / "profile")
    assert not [k for k in env if k in ("GH_TOKEN", "ANTHROPIC_API_KEY", "MY_SECRET_VALUE", "AWS_PROFILE", "SSH_AUTH_SOCK")]
    assert env["BENCH_TASKS_HARMLESS"] == "kept"
    assert env["HOME"] != str(pathlib.Path.home()) and str(tmp_path) in env["HOME"]
    import site
    assert env["PYTHONUSERBASE"] == site.getuserbase(), "the user base must survive the fresh HOME"


def test_materialize_uses_an_operator_cache_so_run_time_needs_no_network(tmp_path):
    tool = _tool()
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a], cwd=origin,
                                    check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q")
    run("config", "uploadpack.allowAnySHA1InWant", "true")
    (origin / "lib.py").write_text("VALUE = 1\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    tasks, cache = tmp_path / "tasks", tmp_path / "cache"
    _write_json(tasks / "ext-x" / "task.json", {"origin": "external"})
    _write_json(tasks / "ext-x" / "fetch.json", {"repo_url": str(origin), "base_sha": run("rev-parse", "HEAD")})
    tool.materialize_task(tasks, "ext-x", record_digest=True, cache=cache)
    assert (cache / "ext-x" / "lib.py").is_file()
    shutil.rmtree(tasks / "ext-x" / "repo")
    shutil.rmtree(origin)
    tool.materialize_task(tasks, "ext-x", cache=cache)
    assert (tasks / "ext-x" / "repo" / "lib.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def _visible_failing_synthetic(tmp_path, tool, visible_body, expected="fail"):
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add_hidden():\n    assert add(1, 2) == 3\n")
    (tasks / "syn" / "repo" / "test_calc.py").write_text(visible_body, encoding="utf-8")
    _edit_task(tasks, "syn", visible_expected=expected)
    tool.seal(tasks, hidden, "syn")
    return tasks, hidden


def test_a_visible_test_that_fails_for_the_stated_behaviour_is_the_signal_a_retry_arm_needs(tmp_path):
    tool = _tool()
    tasks, hidden = _visible_failing_synthetic(
        tmp_path, tool, "from calc import add\n\n\ndef test_add_basic():\n    assert add(2, 2) == 4\n")
    result = tool.verify_internal(tasks, hidden, "syn", tool.load_tasks(tasks)["syn"], python=sys.executable)
    assert result["status"] == "verified", result
    assert (result["visible_on_start"], result["visible_on_start_exit"], result["visible_on_reference"]) == (
        "fail", 1, "pass")


def test_a_visible_suite_that_cannot_even_be_collected_is_refused_for_a_recorded_fail(tmp_path):
    """The visible suite passes on the reference (so only the exit status distinguishes this from a real failure)."""
    tool = _tool()
    tasks, hidden = _visible_failing_synthetic(
        tmp_path, tool, "from calc import add_two\n\n\ndef test_add_two():\n    assert add_two(1) == 3\n")
    (hidden / "syn" / "reference" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef add_two(a):\n    return a + 2\n", encoding="utf-8")
    tool.seal(tasks, hidden, "syn")
    result = tool.verify_internal(tasks, hidden, "syn", tool.load_tasks(tasks)["syn"], python=sys.executable)
    assert result["visible_on_start"] == "fail" and result["visible_on_reference"] == "pass"
    assert result["visible_on_start_exit"] != 1
    assert result["status"] == "FAILED" and "failing by test failures" in result["reason"]


def test_hidden_files_are_overlaid_at_their_own_paths_so_sibling_fixtures_still_apply(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add_hidden():\n    assert add(1, 2) == 3\n")
    sub = tasks / "syn" / "repo" / "pkg"
    sub.mkdir()
    (sub / "conftest.py").write_text("import pytest\n\n\n@pytest.fixture\ndef three():\n    return 3\n", encoding="utf-8")
    bundle = hidden / "syn"
    (bundle / "files" / "pkg").mkdir()
    (bundle / "files" / "pkg" / "test_hidden_fixture.py").write_text(
        "import sys, os\nsys.path.insert(0, os.getcwd())\nfrom calc import add\n\n\n"
        "def test_uses_the_sibling_fixture(three):\n    assert add(1, 2) == three\n", encoding="utf-8")
    _write_json(bundle / "hidden.json", {"files": ["test_hidden_calc.py", "pkg/test_hidden_fixture.py"],
                                         "run": ["test_hidden_calc.py", "pkg/test_hidden_fixture.py"]})
    tool.seal(tasks, hidden, "syn")
    result = tool.verify_internal(tasks, hidden, "syn", tool.load_tasks(tasks)["syn"], python=sys.executable)
    assert result["status"] == "verified", result


def test_a_trap_bundle_must_hold_the_obvious_implementation_and_the_hidden_tests_must_fail_on_it(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    _edit_task(tasks, "syn", kind="trap-plan-defect", trap=True, author="a-handle")
    tool.build_manifest(tasks)
    assert any("must hold obvious/" in p for p in tool.check(tasks, hidden_root=hidden))
    (hidden / "syn" / "obvious").mkdir()
    (hidden / "syn" / "obvious" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tool.seal(tasks, hidden, "syn")
    result = tool.verify_internal(tasks, hidden, "syn", tool.load_tasks(tasks)["syn"], python=sys.executable)
    assert result["hidden_on_obvious"] == "pass" and result["status"] == "FAILED"
    assert "obvious (trap) implementation" in result["reason"]


def test_the_prompt_of_an_external_task_must_be_the_recorded_issue_text(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    prompt = json.loads((copy / "ext-lark-1630" / "task.json").read_text(encoding="utf-8"))["prompt"]
    _edit_task(copy, "ext-lark-1630", prompt=prompt.replace("Template", "Templat3", 1))
    _rebuild(tool, copy)
    assert any("not the issue title and body as recorded" in p for p in tool.check(copy))


def test_a_hidden_test_name_in_a_prompt_is_named(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_the_secret_name():\n    assert add(1, 2) == 3\n")
    _edit_task(tasks, "syn", prompt="make test_the_secret_name pass")
    tool.build_manifest(tasks)
    assert any("hidden test names appear in the prompt" in p for p in tool.check(tasks, hidden_root=hidden))


def test_the_environment_lock_is_bound_into_the_manifest(tmp_path):
    tool = _tool()
    manifest = _real_tasks()
    assert manifest["environment"]["python"].startswith("CPython 3.12")
    copy = _copy_real(tmp_path)
    lock = copy / "environment.lock"
    lock.write_text(lock.read_text(encoding="utf-8") + "extra==1.0\n", encoding="utf-8")
    assert any("lock_sha256 is not the hash" in p for p in tool.check(copy))
    lock.write_text("pluggy==1.6.0\n", encoding="utf-8")
    manifest_copy = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    manifest_copy["environment"]["lock_sha256"] = tool.file_sha256(lock)
    _write_json(copy / "manifest.json", manifest_copy)
    assert any("does not pin pytest" in p for p in tool.check(copy))
    lock.unlink()
    assert any("environment.lock is missing" in p for p in tool.check(copy))


def test_check_with_a_hidden_root_runs_the_harness_input_validation(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    hidden = tmp_path / "hidden"
    rows = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))["tasks"]
    ready = [row["hidden_bundle"] for row in rows if row["status"] == "ready"]
    for name in ready[:-1]:
        (hidden / name).mkdir(parents=True)
    problems = tool.check(copy, hidden_root=hidden)
    assert any("the harness refuses these tasks with this hidden root" in p and ready[-1] in p for p in problems)


def test_the_hidden_runner_is_clean_and_one_build_reproduces_the_sealed_digest(tmp_path):
    """A runner with a stray import once made every rebuilt bundle hash differently from the sealed one."""
    import ast
    tool = _tool()
    tree = ast.parse(tool.HIDDEN_RUNNER)
    imported = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert imported <= used, f"unused imports in the hidden runner: {sorted(imported - used)}"
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a], cwd=origin,
                                    check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q")
    run("config", "uploadpack.allowAnySHA1InWant", "true")
    (origin / "tests").mkdir()
    (origin / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "fix")
    fetch = {"repo_url": str(origin), "fix_sha": run("rev-parse", "HEAD"), "hidden_test_files": ["tests/test_x.py"]}
    digests = []
    for name in ("a", "b"):
        tasks = tmp_path / name / "tasks"
        _write_json(tasks / "ext-x" / "fetch.json", fetch)
        _write_json(tasks / "ext-x" / "task.json", {"origin": "external", "hidden_bundle": "ext-x"})
        digests.append(tool.hidden_from_pr(tasks, tmp_path / name / "hidden", "ext-x", tmp_path / name / "scratch"))
    assert digests[0] == digests[1]
    assert (tmp_path / "a" / "hidden" / "ext-x" / "run_hidden.py").read_text(encoding="utf-8") == tool.HIDDEN_RUNNER


def test_summary_line_reports_pytest_counts_or_says_there_were_none():
    tool = _tool()
    assert tool.summary_line("x\n=== 3 failed, 17 passed in 0.16s ===\n") == "3 failed, 17 passed in 0.16s"
    assert tool.summary_line("tests/t.py:239: AssertionError\n") == "no pytest count line"
    assert tool.summary_line(".....   [100%]\n") == "no pytest count line"


def test_python3_resolves_to_the_interpreter_under_test_even_when_its_directory_has_no_python3(tmp_path):
    tool = _tool()
    odd = tmp_path / "bin"
    odd.mkdir()
    (odd / "py-under-test").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    (odd / "py-under-test").chmod(0o755)
    scoring = tool._Scoring(tmp_path / "scratch" if (tmp_path / "scratch").mkdir() is None else tmp_path, str(odd / "py-under-test"))
    found = subprocess.run(["sh", "-c", "command -v python3"], env=scoring.env, capture_output=True, text=True)
    assert found.stdout.strip().endswith("interpreter-shim/python3"), found.stdout + found.stderr
    out = subprocess.run(["python3", "-c", "import sys; print(sys.version_info[0])"], env=scoring.env,
                         capture_output=True, text=True)
    assert out.stdout.strip() == "3"


def _local_external(tmp_path, tool):
    """An external task over a local origin: hidden fails on the base and passes on the fix; one old visible test stays red."""
    origin = tmp_path / "origin"
    (origin / "tests").mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a], cwd=origin,
                                    check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q")
    run("config", "uploadpack.allowAnySHA1InWant", "true")
    (origin / "lib.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (origin / "tests" / "test_lib.py").write_text("from lib import f\n\n\ndef test_f():\n    assert f() == 1\n", encoding="utf-8")
    (origin / "tests" / "test_old.py").write_text("from lib import f\n\n\ndef test_old():\n    assert f() == 1\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    base = run("rev-parse", "HEAD")
    (origin / "lib.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (origin / "tests" / "test_lib.py").write_text("from lib import f\n\n\ndef test_f():\n    assert f() == 2\n", encoding="utf-8")
    run("commit", "-q", "-am", "fix")
    fix = run("rev-parse", "HEAD")
    tasks, hidden, scratch = tmp_path / "tasks", tmp_path / "hidden", tmp_path / "scratch"
    (scratch / "verify").mkdir(parents=True)
    task = {"id": "ext-x", "kind": "bug-fix", "origin": "external", "trap": False, "status": "ready", "prompt": "p",
            "source": "ext-x/repo", "visible_command": ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
            "visible_expected": "pass", "hidden_bundle": "ext-x", "hidden_sha256": "", "hidden_files": {}, "tree_sha256": ""}
    _write_json(tasks / "ext-x" / "task.json", task)
    fetch = {"repo_url": str(origin), "base_sha": base, "fix_sha": fix, "hidden_test_files": ["tests/test_lib.py"],
             "pytest_args": []}
    _write_json(tasks / "ext-x" / "fetch.json", fetch)
    tool.hidden_from_pr(tasks, hidden, "ext-x", scratch / "build")
    return tasks, hidden, scratch / "verify", fetch


def test_external_verification_end_to_end_on_a_local_repository_and_records_a_red_visible_suite_on_the_fix(tmp_path):
    """verify_external over a local origin: the visible suite on the fix is measured (one old test the pull request did not
    update stays red) and recorded, not required."""
    tool = _tool()
    tasks, hidden, scratch, _ = _local_external(tmp_path, tool)
    result = tool.verify_external(tasks, hidden, "ext-x", tool.load_tasks(tasks)["ext-x"], scratch, sys.executable)
    assert result["status"] == "verified", result
    assert (result["visible_on_start"], result["hidden_on_start"], result["hidden_on_reference"]) == ("pass", "fail", "pass")
    assert result["hidden_on_start_exit"] == 1 and result["visible_on_reference"] == "fail"


def test_external_verification_refuses_a_base_tree_that_is_not_the_recorded_one(tmp_path):
    tool = _tool()
    tasks, hidden, scratch, fetch = _local_external(tmp_path, tool)
    _write_json(tasks / "ext-x" / "fetch.json", dict(fetch, tree_sha256="0" * 64))
    result = tool.verify_external(tasks, hidden, "ext-x", tool.load_tasks(tasks)["ext-x"], scratch, sys.executable)
    assert result["status"] == "FAILED" and result["inconsistent"] is True and "tree_sha256 recorded" in result["reason"]


def test_a_hidden_bundle_edited_after_sealing_is_named(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    assert not [p for p in tool.check(tasks, hidden_root=hidden) if "differs from the hashes" in p]
    with open(hidden / "syn" / "files" / "test_hidden_calc.py", "a", encoding="utf-8") as handle:
        handle.write("# edited\n")
    assert any("syn: hidden bundle on disk differs from the hashes in task.json" in p
               for p in tool.check(tasks, hidden_root=hidden))
    shutil.rmtree(hidden / "syn")
    assert any("syn: hidden bundle missing under the hidden root" in p for p in tool.check(tasks, hidden_root=hidden))


def test_the_trap_flag_must_agree_with_the_kind(tmp_path):
    tool = _tool()
    copy = _copy_real(tmp_path)
    _edit_task(copy, "int-bugfix-1", trap=True)
    _rebuild(tool, copy)
    assert any("`trap` must be true exactly for trap kinds" in p for p in tool.check(copy))
    _edit_task(copy, "int-bugfix-1", trap=False, kind="trap-plan-defect")
    _rebuild(tool, copy)
    assert any("`trap` must be true exactly for trap kinds" in p for p in tool.check(copy))


def _break_manifest(**changes):
    def mutate(copy):
        manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
        for key, value in changes.items():
            if "." in key:
                outer, inner = key.split(".")
                manifest[outer] = dict(manifest[outer], **{inner: value})
            else:
                manifest[key] = value
        _write_json(copy / "manifest.json", manifest)
    return mutate


def _break_task(task_id, **changes):
    def mutate(copy):
        _edit_task(copy, task_id, **changes)
        _rebuild(_tool(), copy)
    return mutate


def _drop_task_key(task_id, key):
    def mutate(copy):
        path = copy / task_id / "task.json"
        task = json.loads(path.read_text(encoding="utf-8"))
        del task[key]
        _write_json(path, task)
    return mutate


def _break_fetch(task_id, **changes):
    def mutate(copy):
        path = copy / task_id / "fetch.json"
        fetch = json.loads(path.read_text(encoding="utf-8"))
        for key, value in changes.items():
            if value is None:
                del fetch[key]
            else:
                fetch[key] = value
        _write_json(path, fetch)
    return mutate


def _delete(task_id, name):
    def mutate(copy):
        target = copy / task_id / name
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    return mutate


def _duplicate_row(copy):
    manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    manifest["tasks"].append(dict(manifest["tasks"][0]))
    _write_json(copy / "manifest.json", manifest)


def _git_dir_in_repo(copy):
    (copy / "int-feature-1" / "repo" / ".git").mkdir()
    (copy / "int-feature-1" / "repo" / ".git" / "HEAD").write_text("ref: x\n", encoding="utf-8")


def _empty_repo(copy):
    shutil.rmtree(copy / "int-docs-1" / "repo")
    (copy / "int-docs-1" / "repo").mkdir()


STRUCTURAL_RULES = [
    ("manifest missing", lambda c: (c / "manifest.json").unlink(), "manifest.json: missing"),
    ("schema", _break_manifest(schema="other/v9"), "manifest schema must be"),
    ("frozen not a bool", _break_manifest(frozen="no"), "must say `\"frozen\": true` or `false` explicitly"),
    ("duplicate ids", _duplicate_row, "manifest task ids are not unique"),
    ("model field", _break_manifest(**{"model.cutoff_source": ""}), "manifest model.cutoff_source is not recorded"),
    ("environment interpreter", _break_manifest(**{"environment.python": ""}), "the interpreter the lock was resolved under"),
    ("frozen needs a hidden root", _break_manifest(frozen=True), "pass --hidden-root"),
    ("task key missing", _drop_task_key("int-bugfix-1", "prompt"), "int-bugfix-1/task.json: missing `prompt`"),
    ("task key missing stops further checks", _drop_task_key("int-bugfix-1", "kind"), "int-bugfix-1/task.json: missing `kind`"),
    ("id differs", _break_task("int-bugfix-1", id="other"), "differs from its directory"),
    ("kind", _break_task("int-bugfix-1", kind="chore"), "kind 'chore' not in"),
    ("origin", _break_task("int-bugfix-1", origin="remote"), "origin must be external or internal"),
    ("status", _break_task("int-bugfix-1", status="done"), "status must be ready or awaiting-author"),
    ("only a trap awaits", _break_task("int-bugfix-1", status="awaiting-author"), "only a trap can await an author"),
    ("visible_expected", _break_task("int-bugfix-1", visible_expected="maybe"), "visible_expected must be pass or fail"),
    ("hidden bundle name", _break_task("int-bugfix-1", hidden_bundle="elsewhere"), "hidden_bundle must equal the id"),
    ("tree hash recorded", _break_task("int-bugfix-1", tree_sha256=""), "tree_sha256 is not recorded"),
    ("closing line", _break_task("int-bugfix-1", prompt="Fix top_words."), "prompt must end with the line `Make the change in this repository.`"),
    ("awaiting holds no hashes", _break_task("trap-1", hidden_sha256="a" * 64), "holds no hidden hashes yet"),
    ("awaiting says so", _break_task("trap-1", prompt="a real prompt"), "must say so in its prompt"),
    ("external needs fetch.json", _delete("ext-bottle-1539", "fetch.json"), "external task needs fetch.json"),
    ("external tree digest agrees", _break_task("ext-bottle-1539", tree_sha256="b" * 64), "differs from the base-tree digest"),
    ("internal repo non-empty", _empty_repo, "internal task needs a non-empty repo/"),
    ("internal repo holds no .git", _git_dir_in_repo, "repo/ must hold no links and no .git"),
    ("fetch key", _break_fetch("ext-bottle-1539", license=None), "ext-bottle-1539/fetch.json: missing `license`"),
    ("fetch sha form", _break_fetch("ext-bottle-1539", base_sha="XYZ"), "base_sha must be a full lowercase sha"),
    ("fetch test files", _break_fetch("ext-bottle-1539", hidden_test_files=[]), "the PR changed no test file"),
]


@pytest.mark.parametrize("name,mutate,expected", STRUCTURAL_RULES, ids=[r[0] for r in STRUCTURAL_RULES])
def test_each_structural_rule_names_its_problem(tmp_path, name, mutate, expected):
    tool = _tool()
    copy = _copy_real(tmp_path)
    assert tool.check(copy) == []
    mutate(copy)
    assert any(expected in problem for problem in tool.check(copy)), (name, tool.check(copy))


def test_interpreter_caches_are_not_scanned_for_leaked_hidden_files(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, HIDDEN_TEXT)
    cache = tasks / "syn" / "repo" / "__pycache__"
    cache.mkdir()
    (cache / "test_hidden_calc.cpython-312.pyc").write_text(HIDDEN_TEXT, encoding="utf-8")
    assert [p for p in tool.check(tasks) if "copied into the repository" in p] == []


def test_small_commands_refuse_what_they_cannot_do(tmp_path):
    tool = _tool()
    tasks, hidden = _synthetic(tmp_path, tool, "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    skipped = tool.verify_internal(tasks, hidden, "syn", dict(tool.load_tasks(tasks)["syn"], status="awaiting-author"))
    assert skipped["status"] == "skipped" and skipped["reason"] == "awaiting-author"
    with pytest.raises(SystemExit, match="not an external task"):
        tool.materialize_task(tasks, "syn")
    with pytest.raises(SystemExit, match="no hidden bundle"):
        tool.seal(tasks, tmp_path / "no-hidden-root", "syn")
    with pytest.raises(SystemExit, match="cannot read the manifest"):
        tool.manifest_sha(tasks, "no-such-revision")


def test_requirements_are_the_union_without_repeats_and_seal_binds_the_right_tree_digest(tmp_path):
    tool = _tool()
    tasks = tmp_path / "tasks"
    _write_json(tasks / "a" / "fetch.json", {"install": ["pytest", "regex", "regex"]})
    _write_json(tasks / "b" / "fetch.json", {"install": ["regex", "hypothesis"]})
    assert tool.union_requirements(tasks) == ["pytest", "regex", "hypothesis"]
    (tmp_path / "s").mkdir()
    syn_tasks, hidden = _synthetic(tmp_path / "s", tool, "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    assert tool.load_tasks(syn_tasks)["syn"]["tree_sha256"] == tool.tree_digest(syn_tasks / "syn" / "repo")
    _write_json(syn_tasks / "ext-y" / "task.json", {"origin": "external", "hidden_bundle": "syn"})
    _write_json(syn_tasks / "ext-y" / "fetch.json", {"tree_sha256": "c" * 64})
    tool.seal(syn_tasks, hidden, "ext-y")
    assert tool.load_tasks(syn_tasks)["ext-y"]["tree_sha256"] == "c" * 64

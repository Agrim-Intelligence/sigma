"""Hermetic controls for the #274 benchmark smoke harness."""
import importlib.util
import json
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCH_PATH = ROOT / "evals" / "bench" / "bench.py"


def _bench():
    spec = importlib.util.spec_from_file_location("benchmark_harness", BENCH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_task(root, task_id):
    source = root / task_id
    source.mkdir()
    (source / "visible.py").write_text("assert True\n", encoding="utf-8")
    return {
        "id": task_id,
        "prompt": f"complete {task_id}",
        "source": task_id,
        "visible_command": [sys.executable, "visible.py"],
        "hidden_bundle": task_id,
    }


def _manifest(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    data = {"schema": "sigma.benchmark-tasks/v1", "tasks": [
        _write_task(tasks, "one"), _write_task(tasks, "two"),
    ]}
    path = tasks / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _hidden_root(tmp_path):
    root = tmp_path / "hidden"
    for task_id in ("one", "two"):
        bundle = root / task_id
        bundle.mkdir(parents=True)
        (bundle / "verify.json").write_text(json.dumps({
            "command": [sys.executable, "-c",
                        "import pathlib; assert pathlib.Path('arm-complete').is_file()"],
        }), encoding="utf-8")
    return root


def _launcher(tmp_path):
    """A test launcher that forwards a serializable external-arm command."""
    path = tmp_path / "operator-sandbox"
    path.write_text("#!/bin/sh\n[ \"$1\" = \"--\" ] || exit 64\nshift\nexec \"$@\"\n", encoding="utf-8")
    path.chmod(0o700)
    return path


@pytest.fixture(autouse=True)
def _neutral_environment(monkeypatch, tmp_path):
    """Each targeted refusal owns its input instead of inheriting CI's guard or the real home.

    The harness content-hashes the operator's plugin directories, so every test runs with HOME
    pointing at an empty temp directory and never reads the real ``~/.claude``.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def test_documented_cli_refuses_to_run_without_an_explicit_token_ceiling(tmp_path, capsys):
    """Red control: deleting the ceiling check makes this documented gesture run."""
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    results = tmp_path / "results.json"

    assert bench.main(["run", "--manifest", str(manifest), "--hidden-root", str(hidden),
                       "--results", str(results), "--fake-arm"]) == 2, \
        "a missing token ceiling must refuse before the arm runs"

    assert not results.exists()
    assert "--max-tokens" in capsys.readouterr().err


def test_ci_environment_refuses_before_the_smoke_arm_or_results_write(tmp_path, monkeypatch):
    """Control: CI is a production refusal, not test-environment collateral."""
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    launcher = _launcher(tmp_path)
    results = tmp_path / "results.json"
    arm = bench.FakeArm()
    monkeypatch.setenv("CI", "true")

    with pytest.raises(bench.BenchmarkRefusal, match="refuses to run from CI"):
        bench.run_benchmark(manifest, [arm], max_tokens=1000, hidden_root=hidden,
                            results_path=results, scratch_root=tmp_path / "scratch",
                            isolation_launcher=launcher)

    assert arm.allowances == []
    assert not results.exists()


def test_two_task_fake_arm_smoke_writes_content_free_schema_results(tmp_path):
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    launcher = _launcher(tmp_path)
    results = tmp_path / "results.json"

    arm = bench.FakeArm()
    report = bench.run_benchmark(manifest, [arm], max_tokens=1000, hidden_root=hidden,
                                 results_path=results, scratch_root=tmp_path / "scratch",
                                 isolation_launcher=launcher)

    assert report["schema"] == "sigma.benchmark-results/v2"
    assert [(row["task"], row["arm"], row["status"], row["hidden_passed"])
            for row in report["runs"]] == [
                ("one", "fake", "completed", True),
                ("two", "fake", "completed", True),
            ]
    assert arm.allowances == ["1000", "1000"]
    persisted = results.read_text(encoding="utf-8")
    assert str(hidden) not in persisted
    assert json.loads(persisted) == report


def test_hidden_bundle_inside_a_task_tree_is_refused_before_the_arm_runs(tmp_path):
    bench = _bench()
    manifest = _manifest(tmp_path)
    inside_task = pathlib.Path(manifest).parent / "one" / "hidden"
    inside_task.mkdir()
    results = tmp_path / "results.json"
    launcher = _launcher(tmp_path)

    arm = bench.FakeArm()

    with pytest.raises(bench.BenchmarkRefusal, match="hidden root"):
        bench.run_benchmark(manifest, [arm], max_tokens=1000, hidden_root=inside_task,
                            results_path=results, scratch_root=tmp_path / "scratch",
                            isolation_launcher=launcher)

    assert not results.exists()
    assert arm.allowances == []


def test_documented_cli_refuses_without_operator_isolation_launcher(tmp_path, capsys):
    """Red control: removing launcher validation starts the documented fake-arm command."""
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    results = tmp_path / "results.json"

    assert bench.main(["run", "--manifest", str(manifest), "--hidden-root", str(hidden),
                       "--results", str(results), "--max-tokens", "1000", "--fake-arm"]) == 2

    assert not results.exists()
    assert "isolation launcher" in capsys.readouterr().err


def test_missing_operator_isolation_launcher_refuses_before_a_malicious_arm_runs(tmp_path):
    """Control: an arm that traverses parents is never invoked without a real boundary."""
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    results = tmp_path / "results.json"
    invoked = []

    arm = bench.FakeArm()

    with pytest.raises(bench.BenchmarkRefusal, match="isolation launcher"):
        bench.run_benchmark(manifest, [arm], max_tokens=1000, hidden_root=hidden,
                            results_path=results, scratch_root=tmp_path / "scratch")

    assert invoked == []
    assert arm.allowances == []
    assert not results.exists()


def test_launcher_under_untrusted_task_tree_is_refused_before_an_arm_runs(tmp_path):
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    unsafe = pathlib.Path(manifest).parent / "one" / "launcher"
    unsafe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    unsafe.chmod(0o700)
    invoked = []

    arm = bench.FakeArm()

    with pytest.raises(bench.BenchmarkRefusal, match="outside repository and task trees"):
        bench.run_benchmark(manifest, [arm], max_tokens=1000, hidden_root=hidden,
                            results_path=tmp_path / "results.json", scratch_root=tmp_path / "scratch",
                            isolation_launcher=unsafe)

    assert invoked == []
    assert arm.allowances == []


def test_valid_launcher_refuses_a_malicious_in_process_arm_before_it_can_traverse(tmp_path):
    """Control: a launcher-present run still never calls arbitrary Python arm code."""
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    launcher = _launcher(tmp_path)
    secret = hidden / "one" / "secret"
    secret.write_text("do-not-leak", encoding="utf-8")
    read = []

    class MaliciousArm(bench.Arm):
        name = "malicious"

        def run(self, workdir, *_args):
            read.append((workdir.parents[2] / "hidden" / "one" / "secret").read_text())
            return bench.ArmRun(cost_usd=0.0, tokens=0)

    with pytest.raises(bench.BenchmarkRefusal, match="enforceable isolation boundary"):
        bench.run_benchmark(manifest, [MaliciousArm()], max_tokens=1000,
                            hidden_root=hidden, results_path=tmp_path / "results.json",
                            scratch_root=tmp_path / "scratch", isolation_launcher=launcher)

    assert read == []


def test_live_arm_stays_refused_even_with_an_accepted_operator_launcher(tmp_path):
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    launcher = _launcher(tmp_path)
    invoked = []

    class FutureLiveArm(bench.Arm):
        name = "future"

        def run(self, *_args):
            invoked.append(True)
            return bench.ArmRun(cost_usd=0.0, tokens=0)

    with pytest.raises(bench.BenchmarkRefusal, match="enforceable isolation boundary"):
        bench.run_benchmark(manifest, [FutureLiveArm()], max_tokens=1000,
                            hidden_root=hidden, results_path=tmp_path / "results.json",
                            scratch_root=tmp_path / "scratch", isolation_launcher=launcher)

    assert invoked == []


def test_second_task_gets_only_the_remaining_ceiling_and_an_overshoot_is_recorded_never_hidden(tmp_path):
    """Control: removing the remaining-ceiling arithmetic hands the second task the full ceiling.

    The host offers no per-run token cap, so a run can overshoot; the harness cannot prevent that, it
    must count it, and the next pair must not start (the three-task variant lives in the arms tests).
    """
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    launcher = _launcher(tmp_path)
    results = tmp_path / "results.json"

    arm = bench.FakeArm(tokens=[600, 600])
    report = bench.run_benchmark(manifest, [arm], max_tokens=1000, hidden_root=hidden,
                                 results_path=results, scratch_root=tmp_path / "scratch",
                                 isolation_launcher=launcher)

    assert arm.allowances == ["1000", "400"]
    assert report["tokens_spent"] == 1200, "the overshoot is counted"
    assert [row["tokens"] for row in report["runs"]] == [600, 600]
    assert json.loads(results.read_text(encoding="utf-8")) == report

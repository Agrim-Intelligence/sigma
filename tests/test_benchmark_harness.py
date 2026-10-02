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
def _neutral_environment(monkeypatch):
    """Each targeted refusal owns its input instead of inheriting CI's guard."""
    monkeypatch.delenv("CI", raising=False)


def test_documented_cli_refuses_to_run_without_an_explicit_spend_ceiling(tmp_path, capsys):
    """Red control: deleting the ceiling check makes this documented gesture run."""
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    results = tmp_path / "results.json"

    assert bench.main(["run", "--manifest", str(manifest), "--hidden-root", str(hidden),
                       "--results", str(results), "--fake-arm"]) == 2, \
        "a missing spend ceiling must refuse before the arm runs"

    assert not results.exists()
    assert "--max-usd" in capsys.readouterr().err


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
        bench.run_benchmark(manifest, [arm], max_usd=0.01, hidden_root=hidden,
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
    report = bench.run_benchmark(manifest, [arm], max_usd=0.01, hidden_root=hidden,
                                 results_path=results, scratch_root=tmp_path / "scratch",
                                 isolation_launcher=launcher)

    assert report["schema"] == "sigma.benchmark-results/v1"
    assert [(row["task"], row["arm"], row["status"], row["hidden_passed"])
            for row in report["runs"]] == [
                ("one", "fake", "completed", True),
                ("two", "fake", "completed", True),
            ]
    assert arm.allowances == ["0.01", "0.01"]
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
        bench.run_benchmark(manifest, [arm], max_usd=0.01, hidden_root=inside_task,
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
                       "--results", str(results), "--max-usd", "0.01", "--fake-arm"]) == 2

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
        bench.run_benchmark(manifest, [arm], max_usd=0.01, hidden_root=hidden,
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
        bench.run_benchmark(manifest, [arm], max_usd=0.01, hidden_root=hidden,
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
            return bench.ArmRun(cost_usd=0.0)

    with pytest.raises(bench.BenchmarkRefusal, match="enforceable isolation boundary"):
        bench.run_benchmark(manifest, [MaliciousArm()], max_usd=0.01,
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
            return bench.ArmRun(cost_usd=0.0)

    with pytest.raises(bench.BenchmarkRefusal, match="enforceable isolation boundary"):
        bench.run_benchmark(manifest, [FutureLiveArm()], max_usd=0.01,
                            hidden_root=hidden, results_path=tmp_path / "results.json",
                            scratch_root=tmp_path / "scratch", isolation_launcher=launcher)

    assert invoked == []


def test_second_task_gets_only_remaining_budget_and_an_overrun_writes_no_report(tmp_path, monkeypatch):
    """Control: removing the remaining-budget check lets a two-task smoke overspend."""
    bench = _bench()
    manifest = _manifest(tmp_path)
    hidden = _hidden_root(tmp_path)
    launcher = _launcher(tmp_path)
    results = tmp_path / "results.json"
    scoring = []

    def visible(*_args):
        scoring.append("visible")
        return True

    def hidden_score(*_args):
        scoring.append("hidden")
        return True

    monkeypatch.setattr(bench, "_command_passed", visible)
    monkeypatch.setattr(bench, "_hidden_passed", hidden_score)

    arm = bench.FakeArm(costs=[0.006, 0.006])
    with pytest.raises(bench.BenchmarkRefusal, match="remaining spend ceiling"):
        bench.run_benchmark(manifest, [arm], max_usd=0.01, hidden_root=hidden,
                            results_path=results, scratch_root=tmp_path / "scratch",
                            isolation_launcher=launcher)

    assert arm.allowances == ["0.01", "0.004"]
    assert scoring == ["visible", "hidden"]
    assert not results.exists()

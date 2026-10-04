"""Tests for tools/readiness/mutation_sample.py (#360): the per-module mutation sample.

These tests are NOT red-by-assertion against the old code (the tool is new, there is no old behaviour to
fail them), so they are kept out of the plan's `## Tests`; each one was instead judged by a mutation of the
tool that it must catch (recorded in the PR). The clock, the load average and the sleeper are injected, so
nothing here depends on timing. Hermetic: no mutmut, no network. The mutmut and pytest subprocesses are replaced by fakes at the seams
`measure(sh=..., mutation=...)` and `sample(measure_fn=..., clock=..., loadavg=..., sleep=...)`. The one
real-tool run (mutmut 2.5.1 on one module) is recorded in docs/launch/evidence/, not re-run here.
"""
import importlib.util
import json
import pathlib
import subprocess
import types

import pytest

ROOT = pathlib.Path(__file__).parent.parent
FIX = pathlib.Path(__file__).parent / "fixtures" / "mutmut"


def _load():
    spec = importlib.util.spec_from_file_location("mutation_sample", ROOT / "tools" / "readiness" / "mutation_sample.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MS = _load()


def _repo(tmp_path, files):
    """A tiny committed git repository: the frozen, clean clone `sample` insists on."""
    for rel, text in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "x"]):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    return tmp_path


def _units(tmp_path, files):
    p = tmp_path / "units.json"
    p.write_text(json.dumps({"tier_a": [
        {"unit": "A%d" % i, "files": [{"path": f, "start": 1, "end": 9}]} for i, f in enumerate(files)]}))
    return p


FACTS = ("2.5.1", "3.12.0")
# Spelled in pieces so this file itself holds no literal home path for the leak scan to find.
MAC_HOMES = "/" + "Users"
LINUX_HOMES = "/" + "home"


def test_tier_a_modules_are_whole_files_deduplicated_and_python_only(tmp_path):
    units = _units(tmp_path, ["a/big.py", "a/big.py", "b/small.py", "install.sh", "hooks.json"])
    assert MS.tier_a_modules(units) == (["a/big.py", "b/small.py"], ["install.sh", "hooks.json"])


def test_tests_are_chosen_by_filename_then_by_reference_and_capped(tmp_path):
    _repo(tmp_path, {"m/alpha.py": "x=1\n", "m/beta.py": "x=1\n", "m/gamma.py": "x=1\n",
                     "tests/test_alpha_extra.py": "pass\n", "tests/test_alpha.py": "pass\n",
                     "tests/test_other.py": "import beta\nbeta.py\n",
                     "tests/test_one.py": "from beta import x\n"})
    assert MS.select_tests(tmp_path, "m/alpha.py") == (["tests/test_alpha.py", "tests/test_alpha_extra.py"], "filename")
    files, rule = MS.select_tests(tmp_path, "m/beta.py")
    assert rule == "references(max5)" and files[0] == "tests/test_other.py" and "tests/test_one.py" in files
    assert MS.select_tests(tmp_path, "m/gamma.py") == ([], "references(max5)")


def _fake_measure(calls):
    def fn(root, module, venv, timeout):
        calls.append((module, timeout))
        return {"verdict": "unverified", "kill_rate": 0.5, "killed": 1, "survived": 1,
                "tests_used": ["tests/t.py"], "reason_code": None, "reason": "r", "seconds": 1.0}
    return fn


def _setup(tmp_path):
    root = _repo(tmp_path / "clone", {"s/big.py": "a\n" * 30, "s/small.py": "a\n" * 3, "s/mid.py": "a\n" * 10})
    return root, _units(tmp_path, ["s/big.py", "s/small.py", "s/mid.py", "install.sh"])


def test_sample_runs_cheapest_first_one_at_a_time_and_records_every_file(tmp_path):
    root, units = _setup(tmp_path)
    calls = []
    doc = MS.sample(root, units, tmp_path / "v", tmp_path / "out.json", measure_fn=_fake_measure(calls), facts=FACTS)
    assert [c[0] for c in calls] == ["s/small.py", "s/mid.py", "s/big.py"]
    assert doc["complete"] is True and doc["mutmut"] == "2.5.1"
    assert set(doc["modules"]) == {"s/big.py", "s/small.py", "s/mid.py", "install.sh"}
    assert doc["modules"]["install.sh"]["reason_code"] == "not-python"
    assert doc["modules"]["install.sh"]["kill_rate"] is None
    assert doc["budget"]["module_timeout_seconds"] == 900 and "60 minutes" in doc["budget"]["deviation_from_issue"]
    assert json.loads((tmp_path / "out.json").read_text())["sha12"] == doc["sha12"]


def test_a_rerun_skips_modules_already_recorded(tmp_path):
    root, units = _setup(tmp_path)
    out = tmp_path / "out.json"
    MS.sample(root, units, tmp_path / "v", out, measure_fn=_fake_measure([]), facts=FACTS, only=["s/small.py"])
    calls = []
    MS.sample(root, units, tmp_path / "v", out, measure_fn=_fake_measure(calls), facts=FACTS)
    assert [c[0] for c in calls] == ["s/mid.py", "s/big.py"]


def test_the_overall_cap_records_unreached_modules_as_not_run_never_as_a_number(tmp_path):
    root, units = _setup(tmp_path)
    ticks = iter(range(0, 10_000, 100))          # each clock read advances 100 s
    calls = []
    doc = MS.sample(root, units, tmp_path / "v", tmp_path / "out.json", budget=450,
                    measure_fn=_fake_measure(calls), facts=FACTS, clock=lambda: next(ticks))
    skipped = [m for m, e in doc["modules"].items() if e.get("reason_code") == "not-run-budget"]
    assert skipped and len(calls) < 3
    for m in skipped:
        assert doc["modules"][m]["kill_rate"] is None and doc["modules"][m]["verdict"] == "absent"
    assert doc["complete"] is False
    ran = [e for m, e in doc["modules"].items() if m not in skipped and e["reason_code"] != "not-python"]
    assert ran and all(e["allowed_seconds"] <= 900 for e in ran), "each run records the time it was allowed"


def test_the_sample_pauses_while_the_machine_is_loaded(tmp_path):
    root, units = _setup(tmp_path)
    loads, slept = iter([12.0, 11.0, 3.0] + [3.0] * 50), []
    MS.sample(root, units, tmp_path / "v", tmp_path / "out.json", measure_fn=_fake_measure([]), facts=FACTS,
              only=["s/small.py"], loadavg=lambda: next(loads), sleep=slept.append)
    assert slept == [60, 60]


def test_a_dirty_clone_is_refused(tmp_path):
    root, units = _setup(tmp_path)
    (root / "s" / "small.py").write_text("changed\n")
    with pytest.raises(SystemExit):
        MS.sample(root, units, tmp_path / "v", tmp_path / "out.json", measure_fn=_fake_measure([]), facts=FACTS)


def test_a_result_for_another_commit_is_not_resumed(tmp_path):
    root, units = _setup(tmp_path)
    out = tmp_path / "out.json"
    out.write_text(json.dumps({"sha": "0" * 40, "modules": {}}))
    with pytest.raises(SystemExit):
        MS.sample(root, units, tmp_path / "v", out, measure_fn=_fake_measure([]), facts=FACTS)


def test_the_evidence_file_carries_no_host_path(tmp_path):
    root, units = _setup(tmp_path)

    def leaky(root_, module, venv, timeout):
        e = _fake_measure([])(root_, module, venv, timeout)
        e["reason"] = "failed in %s and %s/.cache" % (root_, pathlib.Path.home())
        return e

    MS.sample(root, units, tmp_path / "v", tmp_path / "out.json", measure_fn=leaky, facts=FACTS, only=["s/small.py"])
    text = (tmp_path / "out.json").read_text()
    assert str(tmp_path) not in text and str(pathlib.Path.home()) not in text and "<clone>" in text


# --- measure(): one module, with the subprocesses faked ------------------------------------------

def _mut(result):
    return types.SimpleNamespace(
        ABSENT="absent", run=lambda root, targets, **kw: result,
        parse_survivor_ids=lambda text: [4])


def _root(tmp_path, tests=("tests/test_mod.py",)):
    for t in tests:
        (tmp_path / t).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / t).write_text("pass\n")
    return tmp_path


def test_a_timeout_is_absent_with_a_reason_and_never_a_number(tmp_path):
    def sh(argv, root, timeout):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    e = MS.measure(_root(tmp_path), "s/mod.py", tmp_path / "v", 5, mutation=_mut(None), sh=sh)
    assert (e["verdict"], e["reason_code"], e["kill_rate"]) == ("absent", "timeout", None)


def test_a_module_with_no_tests_is_absent_no_tests(tmp_path):
    (tmp_path / "tests").mkdir()
    e = MS.measure(tmp_path, "s/mod.py", tmp_path / "v", 5, mutation=_mut(None), sh=lambda *a: (0, ""))
    assert e["reason_code"] == "no-tests" and e["kill_rate"] is None and e["tests_used"] == []


def test_scoped_tests_that_fail_unmutated_are_not_measured(tmp_path):
    e = MS.measure(_root(tmp_path), "s/mod.py", tmp_path / "v", 5, mutation=_mut(None),
                   sh=lambda argv, root, timeout: (1, "1 failed\nFAILED tests/test_mod.py::t"))
    assert e["reason_code"] == "baseline-failed" and e["kill_rate"] is None


def test_a_measured_module_keeps_the_rate_the_counts_and_the_survivors(tmp_path):
    seen = []

    def sh(argv, root, timeout):
        seen.append(argv)
        if "show" in argv:
            return 0, "--- s/mod.py\n+++ s/mod.py\n@@ -1 +1 @@\n-x\n+y\n"
        if "results" in argv:
            return 0, (FIX / "2.5.1-results.txt").read_text()
        return 0, ""

    result = {"verdict": "unverified", "kill_rate": 0.75, "killed": 3, "survived": 1,
              "reason": "kill rate 0.75 < 0.80 -- 1 mutant(s) survived", "ms": 5,
              "counts": {"killed": 3, "survived": 1}}
    e = MS.measure(_root(tmp_path), "s/mod.py", tmp_path / "v", 5, mutation=_mut(result), sh=sh)
    assert (e["kill_rate"], e["killed"], e["survived"]) == (0.75, 3, 1)
    assert e["tests_used"] == ["tests/test_mod.py"] and e["test_rule"] == "filename"
    assert e["survivors"]["total"] == 1 and "+y" in e["survivors"]["items"][0]["diff"]
    assert any("coverage" in a for argv in seen for a in argv), "the coverage scoping step did not run"


def test_a_crash_in_the_runner_is_absent_crash_not_an_exception(tmp_path):
    def boom(argv, root, timeout):
        raise OSError("disk full")

    e = MS.measure(_root(tmp_path), "s/mod.py", tmp_path / "v", 5, mutation=_mut(None), sh=boom)
    assert e["reason_code"] == "crash" and "disk full" in e["reason"] and e["kill_rate"] is None


def test_a_machine_that_stays_loaded_stops_the_sample_instead_of_adding_to_it(tmp_path):
    root, units = _setup(tmp_path)
    calls, slept = [], []
    doc = MS.sample(root, units, tmp_path / "v", tmp_path / "out.json", measure_fn=_fake_measure(calls), facts=FACTS,
                    load_wait_max=120, loadavg=lambda: 50.0, sleep=slept.append)
    assert calls == [] and slept == [60, 60]
    assert doc["modules"]["s/small.py"]["reason_code"] == "not-run-budget"
    assert "load limit" in doc["modules"]["s/small.py"]["reason"]


def test_the_clone_is_restored_even_when_the_run_times_out(tmp_path, monkeypatch):
    restored = []
    monkeypatch.setattr(MS, "_restore", lambda root: restored.append(root))

    def sh(argv, root, timeout):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    MS.measure(_root(tmp_path), "s/mod.py", tmp_path / "v", 5, mutation=_mut(None), sh=sh)
    assert len(restored) == 2, "restore must run before the module and again after it, timeout or not"


def test_the_rate_is_reported_with_the_range_the_other_mutant_kinds_allow(tmp_path):
    result = {"verdict": "unverified", "kill_rate": 0.5, "killed": 2, "survived": 2, "reason": "r", "ms": 1,
              "counts": {"killed": 2, "survived": 2, "timeout": 2, "suspicious": 2, "skipped": 9}}
    e = MS.measure(_root(tmp_path), "s/mod.py", tmp_path / "v", 5, mutation=_mut(result),
                   sh=lambda *a: (0, ""))
    assert e["kill_rate"] == 0.5 and e["rate_range_all_mutants"] == [0.25, 0.75]


def test_a_foreign_home_path_in_a_diff_or_traceback_is_scrubbed_too(tmp_path):
    root, units = _setup(tmp_path)

    def leaky(root_, module, venv, timeout):
        e = _fake_measure([])(root_, module, venv, timeout)
        e["reason"] = 'File "%s/someone-else/x.py", line 3 and %s/ci/work' % (MAC_HOMES, LINUX_HOMES)
        return e

    MS.sample(root, units, tmp_path / "v", tmp_path / "out.json", measure_fn=leaky, facts=FACTS, only=["s/small.py"])
    text = (tmp_path / "out.json").read_text()
    assert MAC_HOMES not in text and LINUX_HOMES not in text and "<home>" in text


def test_a_module_leaves_the_clone_clean_even_when_mutmut_left_files_behind(tmp_path):
    root = _repo(tmp_path / "clone", {"s/mod.py": "x = 1\n", "tests/test_mod.py": "pass\n"})

    def sh(argv, root_, timeout):
        (root_ / "s" / "mod.py").write_text("x = 2  # mutated\n")
        (root_ / "s" / "mod.py.bak").write_text("x = 1\n")
        (root_ / ".mutmut-cache").write_text("c")
        (root_ / ".coverage.host.1").write_text("c")
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    e = MS.measure(root, "s/mod.py", tmp_path / "v", 5, mutation=_mut(None), sh=sh)
    assert e["reason_code"] == "timeout"
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert status == "" and (root / "s" / "mod.py").read_text() == "x = 1\n"


def test_a_survivor_diff_keeps_the_mutated_lines_and_drops_the_neighbouring_source():
    raw = ("--- s/mod.py\n+++ s/mod.py\n@@ -3,5 +3,5 @@\n context above\n-    return 0 <= x\n+    return 1 <= x\n"
           " context below\n")
    assert MS._compact_diff(raw) == ("--- s/mod.py\n+++ s/mod.py\n@@ -3,5 +3,5 @@\n"
                                     "-    return 0 <= x\n+    return 1 <= x")


def test_a_timeout_recorded_under_a_smaller_allowance_is_retried_with_a_larger_one(tmp_path):
    root, units = _setup(tmp_path)
    out = tmp_path / "out.json"

    def slow(root_, module, venv, timeout):
        return {"verdict": "absent", "kill_rate": None, "killed": None, "survived": None, "tests_used": [],
                "reason_code": "timeout", "reason": "t", "seconds": float(timeout)}

    MS.sample(root, units, tmp_path / "v", out, module_timeout=100, measure_fn=slow, facts=FACTS, only=["s/small.py"])
    calls = []
    MS.sample(root, units, tmp_path / "v", out, module_timeout=100, measure_fn=_fake_measure(calls), facts=FACTS,
              only=["s/small.py"])
    assert calls == [], "the same allowance must not retry a recorded timeout"
    MS.sample(root, units, tmp_path / "v", out, module_timeout=500, measure_fn=_fake_measure(calls), facts=FACTS,
              only=["s/small.py"])
    assert [c[0] for c in calls] == ["s/small.py"], "a larger allowance retries it"


def test_the_reason_code_comes_from_the_tools_code_not_from_its_wording(tmp_path):
    result = {"verdict": "absent", "kill_rate": None, "killed": 0, "survived": 0, "ms": 1, "counts": {},
              "reason": "some reworded message that says nothing about the time limit", "code": "timeout"}
    e = MS.measure(_root(tmp_path), "s/mod.py", tmp_path / "v", 5, mutation=_mut(result), sh=lambda *a: (0, ""))
    assert e["reason_code"] == "timeout"


def test_a_crash_is_retried_on_a_rerun_because_it_may_be_transient(tmp_path):
    root, units = _setup(tmp_path)
    out = tmp_path / "out.json"

    def crashed(root_, module, venv, timeout):
        return {"verdict": "absent", "kill_rate": None, "killed": None, "survived": None, "tests_used": [],
                "reason_code": "crash", "reason": "OSError", "seconds": 1.0}

    MS.sample(root, units, tmp_path / "v", out, measure_fn=crashed, facts=FACTS, only=["s/small.py"])
    calls = []
    MS.sample(root, units, tmp_path / "v", out, measure_fn=_fake_measure(calls), facts=FACTS, only=["s/small.py"])
    assert [c[0] for c in calls] == ["s/small.py"]

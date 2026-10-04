"""Hermetic contract for the flake census (#337, merge 1: workflow, script, docs).

No network and no GitHub call. The census is classified from hand-made JUnit files. One test runs a real
two-test pytest project three times through `run` and `aggregate` so the documented local gesture is
exercised end to end. This change records no census result: nothing here claims a real run happened.

Every test reaches the tool, workflow, doc or fixtures through `_need`, which asserts the path exists, so
against a tree without them each test fails on an AssertionError (a red), not on an ImportError.
"""
import hashlib
import importlib.util
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "readiness" / "flake_census.py"
WORKFLOW = ROOT / ".github" / "workflows" / "flake-census.yml"
DOC = ROOT / "docs" / "launch" / "flake-census.md"
FIXTURES = ROOT / "tests" / "fixtures" / "flake_census"
_MODULE = {}
_COUNTER = itertools.count(1)
SHA = "0123456789abcdef0123456789abcdef01234567"


def _need(path):
    assert path.exists(), "missing from this tree: " + str(path.relative_to(ROOT))
    return path


def census():
    if "mod" not in _MODULE:
        spec = importlib.util.spec_from_file_location("flake_census_under_test", _need(TOOL))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _MODULE["mod"] = mod
    return _MODULE["mod"]


def _case(classname, name, outcome):
    attrs = 'classname="%s" name="%s" time="0.00"' % (classname, name)
    if outcome == "passed":
        return "<testcase %s/>" % attrs
    if outcome == "failed":
        return '<testcase %s><failure message="boom">boom</failure></testcase>' % attrs
    if outcome == "error":
        return '<testcase %s><error message="boom">boom</error></testcase>' % attrs
    assert outcome == "skipped", outcome
    return '<testcase %s><skipped type="pytest.skip" message="s">s</skipped></testcase>' % attrs


def write_run(directory, cases, name=None):
    """One JUnit file. A unique timestamp keeps two runs from being byte-identical, as real runs are not."""
    directory.mkdir(parents=True, exist_ok=True)
    number = next(_COUNTER)
    body = "\n".join(_case(*case) for case in cases)
    text = ('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" '
            'timestamp="2026-10-04T00:00:%02d">%s</testsuite></testsuites>\n' % (number % 60, body)
            + "<!-- %d -->\n" % number)
    path = directory / (name or "junit-%04d.xml" % number)   # zero padded: sorted order is call order
    path.write_text(text, encoding="utf-8")
    return path


def node(name, classname="tests.test_x"):
    return classname + "::" + name


# ---------------------------------------------------------------- classification

def test_fixture_runs_classify_each_kind_of_test():
    result = census().aggregate([FIXTURES], "linux")
    assert result["schema"] == "flake-census/v1"
    assert result["os"] == "linux"
    assert result["runs"] == 3
    assert result["flaky"] == [node("test_c")]
    assert node("test_b") not in result["flaky"]
    assert result["always_failing"] == [node("test_b"), node("test_err")]
    assert result["skipped_only"] == [node("test_s")]
    assert result["missing"] == [node("test_d")]
    assert node("test_a") not in (result["flaky"] + result["always_failing"] + result["missing"])


def test_classes_partition_the_nodes_and_totals_prove_it():
    result = census().aggregate([FIXTURES], "linux")
    totals = result["totals"]
    assert totals["tests"] == 6
    assert (totals["clean"] + totals["flaky"] + totals["always_failing"] + totals["skipped_only"]
            + totals["mixed_nonpassing"]) == totals["tests"]
    assert totals["clean"] == 2 and totals["flaky"] == 1 and totals["always_failing"] == 2
    assert totals["missing"] == 1


def test_error_element_counts_as_failure(tmp_path):
    for outcome in ("passed", "error", "passed"):
        write_run(tmp_path, [("t", "a", outcome)])
    assert census().aggregate([tmp_path], "linux")["flaky"] == [node("a", "t")]


def test_skip_and_failure_mix_is_reported_not_lost(tmp_path):
    for outcome in ("failed", "skipped", "failed"):
        write_run(tmp_path, [("t", "a", outcome)])
    result = census().aggregate([tmp_path], "linux")
    assert result["mixed_nonpassing"] == [node("a", "t")]
    assert result["flaky"] == [] and result["always_failing"] == [] and result["skipped_only"] == []


def test_pass_and_skip_mix_is_clean_by_definition(tmp_path):
    for outcome in ("passed", "skipped", "passed"):
        write_run(tmp_path, [("t", "a", outcome)])
    result = census().aggregate([tmp_path], "linux")
    assert result["flaky"] == [] and result["totals"]["clean"] == 1


def test_flaky_and_missing_overlap_by_design(tmp_path):
    write_run(tmp_path, [("t", "a", "passed")])
    write_run(tmp_path, [("t", "a", "failed")])
    write_run(tmp_path, [("t", "other", "passed")])
    result = census().aggregate([tmp_path], "linux")
    assert node("a", "t") in result["flaky"] and node("a", "t") in result["missing"]


def test_always_failing_and_absent_from_a_run_is_both(tmp_path):
    write_run(tmp_path, [("t", "a", "failed"), ("t", "z", "passed")])
    write_run(tmp_path, [("t", "z", "passed")])
    result = census().aggregate([tmp_path], "linux")
    assert node("a", "t") in result["always_failing"] and node("a", "t") in result["missing"]
    assert node("a", "t") not in result["flaky"]


def test_collection_error_with_empty_classname_is_parsed_not_rejected(tmp_path):
    write_run(tmp_path, [("", "tests.test_broken", "error"), ("t", "a", "passed")])
    write_run(tmp_path, [("", "tests.test_broken", "error"), ("t", "a", "passed")])
    result = census().aggregate([tmp_path], "linux")
    assert "tests.test_broken" in result["always_failing"]


def test_intermittent_collection_error_floods_missing_and_is_not_flaky(tmp_path):
    write_run(tmp_path, [("", "tests.test_broken", "error")])
    write_run(tmp_path, [("t", "a", "passed"), ("t", "b", "passed")])
    write_run(tmp_path, [("t", "a", "passed"), ("t", "b", "passed")])
    result = census().aggregate([tmp_path], "linux")
    assert result["flaky"] == []
    assert result["always_failing"] == ["tests.test_broken"]
    assert node("a", "t") in result["missing"] and node("b", "t") in result["missing"]
    assert result["per_run"][0]["tests"] == 1 and result["per_run"][1]["tests"] == 2


def test_duplicate_node_ids_in_one_file_are_merged_and_reported(tmp_path):
    write_run(tmp_path, [("t", "a", "passed"), ("t", "a", "failed")])
    write_run(tmp_path, [("t", "a", "passed")])
    result = census().aggregate([tmp_path], "linux")
    assert result["duplicate_node_ids"] == [node("a", "t")]
    assert result["flaky"] == [node("a", "t")]


def test_per_run_counts_make_a_hollow_run_visible(tmp_path):
    write_run(tmp_path, [("t", "a", "passed"), ("t", "b", "failed"), ("t", "c", "skipped")])
    write_run(tmp_path, [("t", "a", "passed")])
    per_run = census().aggregate([tmp_path], "linux")["per_run"]
    assert per_run == [{"tests": 3, "failed": 1, "skipped": 1}, {"tests": 1, "failed": 0, "skipped": 0}]


def test_directory_input_is_searched_recursively_and_in_sorted_order(tmp_path):
    first = write_run(tmp_path / "junit-1", [("t", "a", "passed")], name="junit-1.xml")
    second = write_run(tmp_path / "junit-2", [("t", "a", "passed")], name="junit-2.xml")
    result = census().aggregate([tmp_path], "linux")
    assert result["runs"] == 2
    assert census().aggregate([second, first], "linux")["runs"] == 2


# ---------------------------------------------------------------- bad input

def test_bad_input_is_refused(tmp_path):
    mod = census()
    good = write_run(tmp_path / "good", [("t", "a", "passed")])
    not_xml = tmp_path / "not-xml.xml"
    not_xml.write_text("this is not xml", encoding="utf-8")
    empty = write_run(tmp_path / "empty", [])
    nameless = tmp_path / "nameless.xml"
    nameless.write_text("<testsuites><testsuite><testcase classname='t'/></testsuite></testsuites>",
                        encoding="utf-8")
    twin = tmp_path / "twin.xml"
    twin.write_bytes(good.read_bytes())
    for inputs in ([not_xml], [empty], [nameless], [good, twin], [tmp_path / "missing.xml"], []):
        with pytest.raises(mod.BadInput):
            mod.aggregate(inputs, "linux")
    (tmp_path / "bare").mkdir()
    with pytest.raises(mod.BadInput):
        mod.aggregate([tmp_path / "bare"], "linux")


def test_unreadable_inputs_are_bad_input_not_a_traceback(tmp_path):
    mod = census()
    runs = tmp_path / "runs"
    write_run(runs, [("t", "a", "passed")])
    (runs / "sub.xml").mkdir()
    assert mod.main(["aggregate", str(runs), "--os", "linux", "--json", str(tmp_path / "o.json")]) == 2
    locked = write_run(tmp_path / "locked", [("t", "a", "passed")])
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("this account can read a mode 000 file")
        assert mod.main(["aggregate", str(locked), "--os", "linux", "--json", str(tmp_path / "p.json")]) == 2
    finally:
        locked.chmod(0o600)


def test_run_into_an_uncreatable_output_directory_is_bad_input(tmp_path, monkeypatch):
    mod = census()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setattr(mod, "RUN", _Recorder())
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert mod.main(["run", str(tmp_path), "--runs", "1", "--python", "python", "--out", str(blocker / "sub")]) == 2


def test_main_exit_codes_are_0_clean_1_flaky_2_bad(tmp_path):
    mod = census()
    clean, flaky = tmp_path / "clean", tmp_path / "flaky"
    for outcome in ("passed", "passed"):
        write_run(clean, [("t", "a", outcome)])
    for outcome in ("passed", "failed"):
        write_run(flaky, [("t", "a", outcome)])
    assert mod.main(["aggregate", str(clean), "--os", "linux", "--json", str(tmp_path / "c.json")]) == 0
    assert mod.main(["aggregate", str(flaky), "--os", "linux", "--json", str(tmp_path / "f.json")]) == 1
    assert mod.main(["aggregate", str(tmp_path / "nope"), "--os", "linux",
                     "--json", str(tmp_path / "n.json")]) == 2
    assert json.loads((tmp_path / "f.json").read_text())["flaky"] == [node("a", "t")]
    assert not (tmp_path / "n.json").exists()


def test_the_documented_cli_gesture_runs_as_a_subprocess(tmp_path):
    out = tmp_path / "out.json"
    done = subprocess.run([sys.executable, str(_need(TOOL)), "aggregate", str(FIXTURES), "--os", "linux",
                           "--json", str(out)], capture_output=True, text=True, cwd=str(ROOT))
    assert done.returncode == 1, done.stderr
    assert json.loads(out.read_text())["flaky"] == [node("test_c")]


def test_evidence_json_is_created_exclusively(tmp_path):
    mod = census()
    write_run(tmp_path / "runs", [("t", "a", "passed")])
    out = tmp_path / "out.json"
    out.write_text("previous evidence", encoding="utf-8")
    assert mod.main(["aggregate", str(tmp_path / "runs"), "--os", "linux", "--json", str(out)]) == 2
    assert out.read_text(encoding="utf-8") == "previous evidence"


def test_meta_is_recorded_and_host_paths_are_refused(tmp_path):
    mod = census()
    runs = tmp_path / "runs"
    write_run(runs, [("t", "a", "passed")])
    ok = tmp_path / "ok.json"
    assert mod.main(["aggregate", str(runs), "--os", "linux", "--json", str(ok), "--meta", "python=3.12",
                     "--meta", "run_url=https://example.invalid/actions/runs/1"]) == 0
    assert json.loads(ok.read_text())["context"] == {"python": "3.12",
                                                     "run_url": "https://example.invalid/actions/runs/1"}
    for bad in ("path=/home/someone/work", "Bad Key=1", "novalue", "where=/Users/x/y", "tmp=/tmp",
                "home=~/work", "drive=C:\\work", "fwd=C:/Users/x", "two=a /root"):
        out = tmp_path / ("bad-%d.json" % abs(hash(bad)))
        assert mod.main(["aggregate", str(runs), "--os", "linux", "--json", str(out), "--meta", bad]) == 2
        assert not out.exists()


# ---------------------------------------------------------------- combine

def _os_json(tmp_path, os_name, runs=10, flaky=False):
    directory = tmp_path / ("runs-" + os_name)
    for i in range(runs):
        write_run(directory, [("t", "a", "passed"), ("t", "b", "failed" if flaky and i == 0 else "passed")])
    out = tmp_path / (os_name + ".json")
    census().main(["aggregate", str(directory), "--os", os_name, "--json", str(out)])
    return out


def test_combine_keeps_both_operating_systems_and_the_limits(tmp_path):
    linux, macos = _os_json(tmp_path, "linux"), _os_json(tmp_path, "macos")
    out = tmp_path / "evidence.json"
    assert census().main(["combine", str(linux), str(macos), "--sha", SHA, "--json", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["schema"] == "flake-census/v1" and doc["sha"] == SHA
    assert doc["linux"]["os"] == "linux" and doc["linux"]["runs"] == 10
    assert doc["macos"]["os"] == "macos" and doc["macos"]["runs"] == 10
    text = " ".join(doc["limits"])
    assert "cannot prove" in text and "3.12" in text and "xdist" in text


def test_combine_exits_1_when_either_system_has_a_flaky_test(tmp_path):
    linux, macos = _os_json(tmp_path, "linux"), _os_json(tmp_path, "macos", flaky=True)
    out = tmp_path / "evidence.json"
    assert census().main(["combine", str(linux), str(macos), "--sha", SHA, "--json", str(out)]) == 1
    assert json.loads(out.read_text())["macos"]["flaky"] == [node("b", "t")]


def test_combine_refuses_a_partial_or_mislabelled_census(tmp_path):
    mod = census()
    linux, macos = _os_json(tmp_path, "linux"), _os_json(tmp_path, "macos")
    (tmp_path / "short").mkdir()
    short = _os_json(tmp_path / "short", "macos", runs=9)
    cases = [
        [str(linux), str(tmp_path / "absent.json"), "--sha", SHA],
        [str(linux), str(linux), "--sha", SHA],
        [str(macos), str(linux), "--sha", SHA],
        [str(linux), str(short), "--sha", SHA],
        [str(linux), str(macos), "--sha", "abc123"],
    ]
    for index, args in enumerate(cases):
        out = tmp_path / ("refused-%d.json" % index)
        assert mod.main(["combine", *args, "--json", str(out)]) == 2, args
        assert not out.exists()


# ---------------------------------------------------------------- run

class _Recorder:
    last = None

    def __init__(self, write=True, returncode=0):
        self.calls, self.write, self.returncode = [], write, returncode

    def __call__(self, command, cwd=None, **kwargs):
        self.calls.append((list(command), cwd, kwargs))
        _Recorder.last = self.calls[-1]
        if self.write:
            junit = [a.split("=", 1)[1] for a in command if a.startswith("--junitxml=")][0]
            Path(junit).write_text("<testsuites/>", encoding="utf-8")
        return subprocess.CompletedProcess(command, self.returncode)


def test_run_builds_the_documented_command_without_retries(tmp_path, monkeypatch):
    mod = census()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    recorder = _Recorder()
    monkeypatch.setattr(mod, "RUN", recorder)
    results = mod.run(tmp_path, 2, "/venv/bin/python", tmp_path / "out", ["-n", "4", "-p", "no:cacheprovider"])
    assert [row["run"] for row in results] == [1, 2]
    command, cwd, _ = recorder.calls[0]
    assert command[:3] == ["/venv/bin/python", "-m", "pytest"] and cwd == tmp_path
    for expected in ("tests/", "-q", "no:randomly", "no:rerunfailures", "no:flaky",
                     "--junitxml=" + str(tmp_path / "out" / "junit-1.xml")):
        assert expected in command, expected
    assert command[-4:] == ["-n", "4", "-p", "no:cacheprovider"]
    assert not any(a.startswith("--rerun") for a in command)


def test_run_numbers_from_start(tmp_path, monkeypatch):
    mod = census()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setattr(mod, "RUN", _Recorder())
    results = mod.run(tmp_path, 2, "python", tmp_path / "out", [], start=6)
    assert [row["run"] for row in results] == [6, 7]
    assert (tmp_path / "out" / "junit-6.xml").exists() and (tmp_path / "out" / "junit-7.xml").exists()


@pytest.mark.parametrize("arg", [
    ["--reruns", "2"], ["--reruns=2"], ["--only-rerun", "x"], ["--force-flaky"], ["-p", "rerunfailures"],
    ["-prerunfailures"], ["-p", "flaky"], ["--junitxml=x.xml"], ["--junit-xml=x.xml"], ["--lf"],
    ["--last-failed"], ["--ff"], ["--nf"], ["--sw"], ["-x"], ["-xq"], ["--maxfail=1"], ["-k", "name"],
    ["-kname"], ["-m", "slow"], ["--deselect", "tests/a.py::t"], ["--ignore", "tests/a"],
    ["--ignore-glob=x*"], ["--collect-only"], ["--pyargs", "pkg"], ["tests/test_a.py"], ["tests/test_a.py::t"],
    ["-o", "addopts=--reruns=2"], ["-c", "other.ini"], ["--exitf"], ["--last-f"], ["--maxf=1"], ["--rerun"], ["-qp", "rerunfailures"],
    ["-qprerunfailures"], ["--pdb"],
])
def test_run_refuses_retry_and_narrowing_arguments(tmp_path, monkeypatch, arg):
    mod = census()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    recorder = _Recorder()
    monkeypatch.setattr(mod, "RUN", recorder)
    with pytest.raises(mod.BadInput):
        mod.run(tmp_path, 1, "python", tmp_path / "out", arg)
    assert recorder.calls == []


def test_run_refuses_an_environment_that_would_inject_pytest_options(tmp_path, monkeypatch):
    mod = census()
    monkeypatch.setenv("PYTEST_ADDOPTS", "--reruns=3")
    monkeypatch.setattr(mod, "RUN", _Recorder())
    with pytest.raises(mod.BadInput):
        mod.run(tmp_path, 1, "python", tmp_path / "out", [])


def test_run_refuses_to_overwrite_an_observation_or_run_nothing(tmp_path, monkeypatch):
    mod = census()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    recorder = _Recorder()
    monkeypatch.setattr(mod, "RUN", recorder)
    out = tmp_path / "out"
    out.mkdir()
    (out / "junit-2.xml").write_text("earlier observation", encoding="utf-8")
    with pytest.raises(mod.BadInput):
        mod.run(tmp_path, 3, "python", out, [])
    with pytest.raises(mod.BadInput):
        mod.run(tmp_path, 0, "python", tmp_path / "fresh", [])
    with pytest.raises(mod.BadInput):
        mod.run(tmp_path / "no-such-repo", 1, "python", tmp_path / "fresh", [])
    assert recorder.calls == [] and (out / "junit-2.xml").read_text() == "earlier observation"


def test_run_resolves_a_relative_out_so_the_overwrite_guard_checks_where_pytest_writes(tmp_path, monkeypatch):
    mod = census()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    repo, elsewhere = tmp_path / "repo", tmp_path / "elsewhere"
    repo.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(mod, "RUN", _Recorder())
    mod.run(repo, 1, "python", "rel", [])
    command, cwd, _ = _Recorder.last
    assert "--junitxml=" + str(elsewhere.resolve() / "rel" / "junit-1.xml") in command
    assert (elsewhere / "rel" / "junit-1.xml").exists() and not (repo / "rel").exists()
    with pytest.raises(mod.BadInput):
        mod.run(repo, 1, "python", "rel", [])


def test_run_flags_a_run_that_ended_abnormally_even_though_it_wrote_xml(tmp_path, monkeypatch, capsys):
    mod = census()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    for code, expected in ((0, 0), (1, 0), (2, 1), (3, 1), (5, 1)):
        recorder = _Recorder(returncode=code)
        monkeypatch.setattr(mod, "RUN", recorder)
        out = tmp_path / ("out%d" % code)
        assert mod.main(["run", str(tmp_path), "--runs", "1", "--python", "python", "--out", str(out)]) == expected
        assert json.loads(capsys.readouterr().out)["runs"][0]["exit"] == code


def test_run_accepts_ordinary_pytest_options(tmp_path, monkeypatch):
    mod = census()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setattr(mod, "RUN", _Recorder())
    mod.run(tmp_path, 1, "python", tmp_path / "out", ["-n", "4", "--color=no", "--tb=short", "-rA",
                                                     "--durations=5", "--import-mode=prepend", "-rx", "-rxs", "--pdbcls=x:y"])


def test_run_with_an_unstartable_interpreter_is_bad_input_not_a_traceback(tmp_path, monkeypatch):
    mod = census()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    assert mod.main(["run", str(tmp_path), "--runs", "1", "--python", str(tmp_path / "no-python"),
                     "--out", str(tmp_path / "out")]) == 2


def test_combine_refuses_an_aggregate_without_its_classification_lists(tmp_path):
    mod = census()
    good, other = _os_json(tmp_path, "linux"), _os_json(tmp_path, "macos")
    doc = json.loads(good.read_text())
    del doc["flaky"]
    hollow = tmp_path / "hollow.json"
    hollow.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "o.json"
    assert mod.main(["combine", str(hollow), str(other), "--sha", SHA, "--json", str(out)]) == 2
    assert not out.exists()


def test_run_main_exit_1_when_a_run_wrote_no_xml(tmp_path, monkeypatch):
    mod = census()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setattr(mod, "RUN", _Recorder(write=False))
    assert mod.main(["run", str(tmp_path), "--runs", "1", "--python", "python",
                     "--out", str(tmp_path / "out")]) == 1
    assert mod.main(["run", str(tmp_path), "--runs", "1", "--python", "python", "--out",
                     str(tmp_path / "out2"), "--", "--reruns", "2"]) == 2


def test_run_then_aggregate_finds_the_alternating_test_in_a_real_pytest_project(tmp_path):
    """End to end on the documented local gesture: a real pytest, three real runs, no retries."""
    mod = census()
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_alt.py").write_text(
        "from pathlib import Path\n"
        "COUNTER = Path(__file__).resolve().parent.parent / 'counter.txt'\n"
        "def test_alternating():\n"
        "    n = int(COUNTER.read_text()) if COUNTER.exists() else 0\n"
        "    COUNTER.write_text(str(n + 1))\n"
        "    assert n != 1\n"
        "def test_steady():\n"
        "    assert True\n"
        "def test_broken():\n"
        "    assert False\n", encoding="utf-8")
    env_keep = os.environ.pop("PYTEST_ADDOPTS", None)
    try:
        code = mod.main(["run", str(project), "--runs", "3", "--python", sys.executable,
                         "--out", str(tmp_path / "junit"), "--", "-p", "no:cacheprovider"])
    finally:
        if env_keep is not None:
            os.environ["PYTEST_ADDOPTS"] = env_keep
    assert code == 0
    result = mod.aggregate([tmp_path / "junit"], "linux")
    assert result["runs"] == 3
    assert result["flaky"] == ["tests.test_alt::test_alternating"]
    assert result["always_failing"] == ["tests.test_alt::test_broken"]
    assert result["totals"]["clean"] == 1


# ---------------------------------------------------------------- workflow

TRIGGER_KEY = re.compile(r"^  ([A-Za-z_]+):", re.M)


def validate_workflow(text):
    """Refuse a flake-census workflow that could run unprompted or stop measuring what it claims."""
    on_block = re.search(r"^on:\n((?:[ \t]+.*\n|\n)+)", text, re.M)
    assert on_block, "no on: block"
    assert TRIGGER_KEY.findall(on_block.group(1)) == ["workflow_dispatch"], "must be dispatch-only"
    assert not re.search(r"^\s*(push|pull_request|pull_request_target|schedule|workflow_run):", text, re.M)
    assert re.search(r"^permissions:\n  contents: read\n", text, re.M), "needs read-only permissions"
    assert "runs-on: ubuntu-latest" in text
    assert "timeout-minutes: 60" in text
    assert "fail-fast: false" in text
    assert "run: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]" in text
    assert 'python-version: "3.12"' in text
    assert "python -m pytest tests/ -q -p no:randomly -p no:rerunfailures -p no:flaky " \
           "--junitxml=junit-${{ matrix.run }}.xml" in text
    assert "--reruns" not in text and "continue-on-error" not in text
    assert "actions/upload-artifact@v4" in text
    assert "if: ${{ !cancelled() }}" in text
    assert "name: junit-${{ matrix.run }}" in text
    assert "if-no-files-found: error" in text
    assert not re.search(r"\bgh (issue|pr|api|workflow|run|release)\b", text)
    assert "secrets." not in text and "GITHUB_TOKEN" not in text


def test_workflow_is_dispatch_only_and_measures_ten_unretried_runs():
    validate_workflow(_need(WORKFLOW).read_text(encoding="utf-8"))


@pytest.mark.parametrize("old,new", [
    ("on:\n  workflow_dispatch:\n", "on:\n  workflow_dispatch:\n  push:\n    branches: [main]\n"),
    ("on:\n  workflow_dispatch:\n", "on:\n  workflow_dispatch:\n  schedule:\n    - cron: '0 3 * * *'\n"),
    ("on:\n  workflow_dispatch:\n", "on:\n  pull_request:\n"),
    ("run: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]", "run: [1, 2, 3, 4, 5, 6, 7, 8, 9]"),
    ('python-version: "3.12"', 'python-version: "3.13"'),
    ("-p no:rerunfailures", "--reruns 2 -p no:rerunfailures"),
    ("if: ${{ !cancelled() }}", "if: success()"),
    ("fail-fast: false", "fail-fast: true"),
    ("permissions:\n  contents: read\n", "permissions:\n  contents: write\n"),
], ids=["push", "schedule", "pull-request", "nine-runs", "python-313", "reruns", "not-cancelled",
        "fail-fast", "write-permission"])
def test_workflow_validator_refuses_a_mutated_copy(old, new):
    text = _need(WORKFLOW).read_text(encoding="utf-8")
    assert old in text, "mutation target moved: " + old
    with pytest.raises(AssertionError):
        validate_workflow(text.replace(old, new, 1))


# ---------------------------------------------------------------- docs and hygiene

def _doc():
    return _need(DOC).read_text(encoding="utf-8")


def test_doc_control_gesture_copied_from_the_doc_reports_the_fixture_classification(tmp_path):
    section = _doc().split("## Control", 1)[1]
    line = [l.strip() for l in section.splitlines()
            if l.strip().startswith("python3 tools/readiness/flake_census.py aggregate")][0]
    out = tmp_path / "control.json"
    argv = [sys.executable if a == "python3" else a for a in shlex.split(line.replace("<out.json>", str(out)))]
    done = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))
    assert done.returncode == 1, done.stderr
    result = json.loads(out.read_text())
    assert result["flaky"] == [node("test_c")] and node("test_b") in result["always_failing"]


def test_doc_states_cost_limits_and_the_recorded_result():
    text = _doc()
    for required in ("workflow_dispatch", "150", "210", "runner-minutes", "cannot prove", "## Recorded result",
                     "always_failing", "mixed_nonpassing", "missing", "skipped_only", "red", "3.12"):
        assert required in text, required
    assert "flaky and missing" in text.replace("`", "")
    assert "records no census result" not in text and "no evidence file" not in text
    recorded = text.split("## Recorded result", 1)[1].split("\n## ", 1)[0]
    evidence = sorted((ROOT / "docs" / "launch" / "evidence").glob("flake-*.json"))
    assert evidence, "the recorded-result section must point at a committed evidence file"
    for path in evidence:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["sha"] in recorded and path.name in recorded
        assert "10 runs" in recorded and "37187467571" in recorded


def test_no_host_path_or_secret_shaped_text_in_the_new_files():
    # The tool itself is not scanned: it carries the pattern that refuses host paths, as source text.
    files = [_need(WORKFLOW), _need(DOC)] + sorted(_need(FIXTURES).glob("*.xml"))
    assert len(files) == 5
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"/Users/|/home/[a-z]|[A-Z]:\\\\", text), path.name
        assert not re.search(r"ghp_[A-Za-z0-9]{20}|AKIA[0-9A-Z]{12}|BEGIN [A-Z ]*PRIVATE KEY", text), path.name

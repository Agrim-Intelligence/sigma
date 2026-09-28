"""scope.py (#920, agrim-scope skill, wave 3 of epic #902): the ONE thin bridge connecting #918's
`compile_plan.compile_plan()` to #919's `assign.py execute --report <path>` CLI -- exactly the
piece the orchestration layer is otherwise missing.

`compile_plan.py`'s own CLI printed human text only when this shipped (`assign.py` did not exist
yet when #918 shipped, so its CLI was never given a reason to emit a chainable, machine-readable
report; #1919 has since added a `--json` arm to it, which prints and writes no file).
`scope.py` closes that one gap WITHOUT touching either already-shipped, already-tested file: it
only ever calls the real `compile_plan.compile_plan()` function (never re-derives its logic) and
prints/writes its report as JSON, matching `brainstorm.py`/`dedup.py`/`assign.py resolve`'s own
established `json.dumps(..., ensure_ascii=False, sort_keys=True)` convention.

Hermetic like every sibling test in this suite where possible (monkeypatching `compile_plan.py`'s
own `compile_plan()` function to avoid re-testing ITS internals -- already covered by
tests/test_compile_plan.py), plus one real end-to-end case through the actual local-goals source
(no `gh`, no network -- mirrors test_compile_plan.py's own
`test_main_success_creates_a_real_local_goal_file`) to prove the wrapper genuinely delegates rather
than reimplementing.
"""
import importlib.util
import json
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCOPE_SCRIPTS = _ROOT / "skills" / "agrim-scope" / "scripts"


def _mod(name, where):
    spec = importlib.util.spec_from_file_location(name, where / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


scope = _mod("scope", SCOPE_SCRIPTS)


def _init_sdlc(tmp_path, config=None):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(json.dumps(config if config is not None else {}))
    return sdlc


def _write_plan(tmp_path, plan):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


# --------------------------------------------------------------------------------------- main() usage


def test_main_usage_error_with_no_args(capsys):
    assert scope.main(["scope.py"]) == 2
    assert "usage" in capsys.readouterr().err


def test_main_usage_error_when_plan_flag_missing(tmp_path, capsys):
    sdlc = _init_sdlc(tmp_path)
    assert scope.main(["scope.py", str(sdlc)]) == 2
    assert "usage" in capsys.readouterr().err


def test_main_reports_an_unreadable_plan_file(tmp_path, capsys):
    sdlc = _init_sdlc(tmp_path)
    rc = scope.main(["scope.py", str(sdlc), "--plan", str(tmp_path / "missing.json")])
    assert rc == 2
    assert "could not read plan" in capsys.readouterr().err


def test_main_reports_invalid_json_in_the_plan_file(tmp_path, capsys):
    sdlc = _init_sdlc(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{not json")
    rc = scope.main(["scope.py", str(sdlc), "--plan", str(plan_path)])
    assert rc == 2
    assert "could not read plan" in capsys.readouterr().err


# ---------------------------------------------------------------------------- delegates, never reimplements


def test_compile_and_report_delegates_to_the_real_compile_plan_function(tmp_path, monkeypatch):
    sdlc = _init_sdlc(tmp_path, config={"marker": "config-loaded"})
    seen = {}

    def fake_compile_plan(sdlc_dir, config, plan, *, source=None, goal_label=False):
        seen["sdlc_dir"] = sdlc_dir
        seen["config"] = config
        seen["plan"] = plan
        seen["goal_label"] = goal_label
        return {"epic": None, "issues": {"a": "9"}, "failed": {}, "skipped": {},
                "order": ["a"], "warnings": []}

    monkeypatch.setattr(scope.compile_plan, "compile_plan", fake_compile_plan)
    plan = {"issues": [{"key": "a", "title": "x"}]}
    report = scope.compile_and_report(str(sdlc), plan, actionable=True)

    assert seen["sdlc_dir"] == str(sdlc)
    assert seen["config"] == {"marker": "config-loaded"}
    assert seen["plan"] == plan
    assert seen["goal_label"] is True
    assert report["issues"] == {"a": "9"}


def test_compile_and_report_actionable_defaults_to_false(tmp_path, monkeypatch):
    sdlc = _init_sdlc(tmp_path)
    seen = {}

    def fake_compile_plan(sdlc_dir, config, plan, *, source=None, goal_label=False):
        seen["goal_label"] = goal_label
        return {"epic": None, "issues": {}, "failed": {}, "skipped": {}, "order": [], "warnings": []}

    monkeypatch.setattr(scope.compile_plan, "compile_plan", fake_compile_plan)
    scope.compile_and_report(str(sdlc), {"issues": []})
    assert seen["goal_label"] is False


def test_compile_and_report_raises_valueerror_for_a_structurally_invalid_plan(tmp_path):
    """No monkeypatch here -- the REAL compile_plan.compile_plan() must be the one raising, proving
    scope.py never swallows or re-derives that validation."""
    sdlc = _init_sdlc(tmp_path)
    plan = {"issues": [{"key": "a", "title": "x", "blocked_by": ["ghost"]}]}
    try:
        scope.compile_and_report(str(sdlc), plan)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "unknown" in str(exc)


# --------------------------------------------------------------------------------- main(): JSON output


def test_main_prints_the_report_as_a_single_json_line_to_stdout(tmp_path, capsys, monkeypatch):
    sdlc = _init_sdlc(tmp_path)
    plan_path = _write_plan(tmp_path, {"issues": [{"key": "a", "title": "x"}]})

    def fake_compile_plan(sdlc_dir, config, plan, *, source=None, goal_label=False):
        return {"epic": None, "issues": {"a": "9"}, "failed": {}, "skipped": {},
                "order": ["a"], "warnings": []}

    monkeypatch.setattr(scope.compile_plan, "compile_plan", fake_compile_plan)
    rc = scope.main(["scope.py", str(sdlc), "--plan", str(plan_path)])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    payload = json.loads(out)
    assert payload["issues"] == {"a": "9"}
    assert "\n" not in out   # a single JSON line, matching brainstorm.py/dedup.py/assign.py's own CLI


def test_main_writes_the_report_to_the_report_path_when_given(tmp_path, monkeypatch):
    sdlc = _init_sdlc(tmp_path)
    plan_path = _write_plan(tmp_path, {"issues": [{"key": "a", "title": "x"}]})
    report_path = tmp_path / "report.json"

    def fake_compile_plan(sdlc_dir, config, plan, *, source=None, goal_label=False):
        return {"epic": "5", "issues": {"a": "9"}, "failed": {}, "skipped": {},
                "order": ["a"], "warnings": []}

    monkeypatch.setattr(scope.compile_plan, "compile_plan", fake_compile_plan)
    rc = scope.main(["scope.py", str(sdlc), "--plan", str(plan_path), "--report", str(report_path)])
    assert rc == 0
    on_disk = json.loads(report_path.read_text())
    assert on_disk["epic"] == "5"
    assert on_disk["issues"] == {"a": "9"}


def test_main_report_flag_is_optional(tmp_path, monkeypatch):
    """No --report given -- stdout still carries the JSON, nothing written to disk, no crash."""
    sdlc = _init_sdlc(tmp_path)
    plan_path = _write_plan(tmp_path, {"issues": [{"key": "a", "title": "x"}]})

    def fake_compile_plan(sdlc_dir, config, plan, *, source=None, goal_label=False):
        return {"epic": None, "issues": {"a": "9"}, "failed": {}, "skipped": {},
                "order": ["a"], "warnings": []}

    monkeypatch.setattr(scope.compile_plan, "compile_plan", fake_compile_plan)
    rc = scope.main(["scope.py", str(sdlc), "--plan", str(plan_path)])
    assert rc == 0


def test_main_actionable_flag_reaches_compile_plan_as_goal_label_true(tmp_path, monkeypatch):
    sdlc = _init_sdlc(tmp_path)
    plan_path = _write_plan(tmp_path, {"issues": [{"key": "a", "title": "x"}]})
    seen = {}

    def fake_compile_plan(sdlc_dir, config, plan, *, source=None, goal_label=False):
        seen["goal_label"] = goal_label
        return {"epic": None, "issues": {"a": "9"}, "failed": {}, "skipped": {},
                "order": ["a"], "warnings": []}

    monkeypatch.setattr(scope.compile_plan, "compile_plan", fake_compile_plan)
    rc = scope.main(["scope.py", str(sdlc), "--plan", str(plan_path), "--actionable"])
    assert rc == 0
    assert seen["goal_label"] is True


def test_main_exit_code_1_when_failed_or_skipped_present(tmp_path, monkeypatch):
    sdlc = _init_sdlc(tmp_path)
    plan_path = _write_plan(tmp_path, {"issues": [{"key": "a", "title": "x"}]})

    def fake_compile_plan(sdlc_dir, config, plan, *, source=None, goal_label=False):
        return {"epic": None, "issues": {}, "failed": {"a": "gh: boom"}, "skipped": {},
                "order": [], "warnings": []}

    monkeypatch.setattr(scope.compile_plan, "compile_plan", fake_compile_plan)
    rc = scope.main(["scope.py", str(sdlc), "--plan", str(plan_path)])
    assert rc == 1


def test_main_report_write_failure_is_reported_not_a_raw_traceback(tmp_path, capsys, monkeypatch):
    """A directory sitting where --report needs to write a FILE turns write_text into an OSError
    (IsADirectoryError on POSIX) -- hermetic, no chmod/permission trickery needed, mirrors
    test_assign.py's own `test_start_now_self_plan_file_write_failure_is_reported_not_raised`.
    The report was already compiled (compile_plan already ran for real) and already printed to
    stdout BEFORE the write is attempted, so this must degrade to a clear message + non-zero exit,
    never an uncaught traceback that would hide the fact real work already happened."""
    sdlc = _init_sdlc(tmp_path)
    plan_path = _write_plan(tmp_path, {"issues": [{"key": "a", "title": "x"}]})

    def fake_compile_plan(sdlc_dir, config, plan, *, source=None, goal_label=False):
        return {"epic": None, "issues": {"a": "9"}, "failed": {}, "skipped": {},
                "order": ["a"], "warnings": []}

    monkeypatch.setattr(scope.compile_plan, "compile_plan", fake_compile_plan)
    blocking_dir = tmp_path / "report.json"
    blocking_dir.mkdir()

    rc = scope.main(["scope.py", str(sdlc), "--plan", str(plan_path), "--report", str(blocking_dir)])

    assert rc == 1
    out, err = capsys.readouterr()
    assert json.loads(out.strip())["issues"] == {"a": "9"}   # the report still reached stdout
    assert "could not write --report" in err
    assert "stdout" in err                                    # tells the caller where the data is


def test_main_report_write_creates_missing_parent_directories(tmp_path):
    """The --report path's own parent directory not existing yet must not be a failure -- mirrors
    the mkdir(parents=True, exist_ok=True) convention every sibling write in this skill uses
    (assign.py's own plan-file write does the same)."""
    sdlc = _init_sdlc(tmp_path, config={"discovery": {"source": "local-goals"}})
    plan_path = _write_plan(tmp_path, {"issues": [{"key": "a", "title": "Do the thing"}]})
    report_path = tmp_path / "nested" / "not-yet-created" / "report.json"

    rc = scope.main(["scope.py", str(sdlc), "--plan", str(plan_path), "--report", str(report_path)])

    assert rc == 0
    assert report_path.exists()
    assert json.loads(report_path.read_text())["issues"]


def test_main_reports_a_structurally_invalid_plan_as_usage_error_and_writes_nothing(tmp_path, capsys):
    """Real (not faked) compile_plan.compile_plan() -- proves the ValueError path end to end, and
    that a --report path is never written when nothing was ever compiled."""
    sdlc = _init_sdlc(tmp_path)
    plan_path = _write_plan(tmp_path, {"issues": [{"key": "a", "title": "x", "blocked_by": ["ghost"]}]})
    report_path = tmp_path / "report.json"
    rc = scope.main(["scope.py", str(sdlc), "--plan", str(plan_path), "--report", str(report_path)])
    assert rc == 2
    assert "unknown" in capsys.readouterr().err
    assert not report_path.exists()


# ------------------------------------------------------------------------- real end-to-end (local-goals)


def test_main_end_to_end_through_the_real_local_goals_source(tmp_path, capsys):
    """No monkeypatch at all -- the real compile_plan.compile_plan(), the real LocalSource, a real
    goal file on disk. Proves scope.py genuinely delegates rather than reimplementing anything
    compile_plan.py already does, mirroring test_compile_plan.py's own
    `test_main_success_creates_a_real_local_goal_file`."""
    sdlc = _init_sdlc(tmp_path, config={"discovery": {"source": "local-goals"}})
    plan_path = _write_plan(tmp_path, {"issues": [{"key": "a", "title": "Do the thing", "body": "b",
                                                    "priority": "P2"}]})
    report_path = tmp_path / "report.json"
    rc = scope.main(["scope.py", str(sdlc), "--plan", str(plan_path), "--report", str(report_path)])
    assert rc == 0

    stdout_report = json.loads(capsys.readouterr().out.strip())
    disk_report = json.loads(report_path.read_text())
    assert stdout_report == disk_report
    assert list(disk_report["issues"].keys()) == ["a"]

    goal_files = list((sdlc / "goals").glob("*.md"))
    assert len(goal_files) == 1
    assert "Do the thing" in goal_files[0].read_text()


# --------------------------------------------- #1919: the "human text only" claim in three docs
# `compile_plan.py`'s CLI gained a `--json` arm for `agrim-goal-review`. Three separate documents --
# this test's own docstring, `scope.py`'s module docstring and `skills/agrim-scope/SKILL.md` -- each
# opened by asserting that CLI emits human text ONLY, which the flag falsified in all three at once.
# Pinned against the LIVE `main()` rather than against each other, so the code is what moves first.


def test_no_document_still_claims_compile_plans_cli_is_human_text_only():
    import inspect
    compile_plan = _mod("compile_plan", SCOPE_SCRIPTS)
    assert '"--json"' in inspect.getsource(compile_plan.main), "the live fact this prose tracks"
    stale = "human-text" + "-only"          # assembled: this file is one of the three it scans
    for rel in ("skills/agrim-scope/scripts/scope.py", "skills/agrim-scope/SKILL.md",
                "tests/test_scope.py"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert stale not in text, f"{rel} still asserts a CLI shape that changed"
        assert "--json" in text, f"{rel} describes that CLI without naming its machine channel"

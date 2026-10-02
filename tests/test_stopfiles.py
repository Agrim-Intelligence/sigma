"""#416: a daemon stop-file left in <sdlc>/state is REPORTED, with its age, by the doctor row, the
SessionStart line and stopfiles.py -- every gesture a user is given. Run through the real scripts
(subprocess), the way the D4 readiness drill does."""
import json, os, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCTOR = ROOT / "skills" / "agrim-doctor" / "scripts" / "doctor.py"
HOOK = ROOT / "hooks" / "session_start.sh"
STOPFILES = ROOT / "skills" / "agrim-loop" / "scripts" / "stopfiles.py"
ENV = {k: v for k, v in os.environ.items() if k != "SIGMA_RUN_ID"}


def _repo(tmp_path, names=(), age=0, ledger=False):
    state = tmp_path / ".sdlc" / "state"
    state.mkdir(parents=True)
    cfg = {"ledger": {"enabled": True, "watch": {"interval_seconds": 1}}} if ledger else {}
    (tmp_path / ".sdlc" / "config.json").write_text(json.dumps(cfg))
    for n in names:
        f = state / n
        f.touch()
        t = time.time() - age
        os.utime(f, (t, t))
    return tmp_path


def _doctor(repo):
    return subprocess.run([sys.executable, str(DOCTOR), "check", str(repo / ".sdlc")],
                          cwd=repo, capture_output=True, text=True, env=ENV).stdout


def _hook(repo):
    p = subprocess.run(["bash", str(HOOK)], cwd=repo, capture_output=True, text=True,
                       env={**ENV, "CLAUDE_PROJECT_DIR": str(repo), "SIGMA_ALLOW_COEXIST": "1"})
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_doctor_reports_each_stop_file_with_age_and_leaves_it(tmp_path):
    repo = _repo(tmp_path, ("watch.stop", "supervisor.stop"), age=3 * 86400)
    out = _doctor(repo)
    assert "daemon stop-files" in out
    assert "watch.stop" in out and "supervisor.stop" in out and "3d old" in out
    assert (repo / ".sdlc/state/watch.stop").exists() and (repo / ".sdlc/state/supervisor.stop").exists()


def test_doctor_row_says_none_when_no_stop_file(tmp_path):
    out = _doctor(_repo(tmp_path))
    assert "daemon stop-files" in out and "STOPPED" not in out


def test_session_start_names_the_stop_file_even_when_ledger_watcher_looks_stale(tmp_path):
    repo = _repo(tmp_path, ("supervisor.stop",), age=7200, ledger=True)
    ctx = json.loads(_hook(repo))["hookSpecificOutput"]["additionalContext"]
    assert "supervisor.stop" in ctx and "2.0h old" in ctx


def test_session_start_silent_without_stop_file(tmp_path):
    assert "stop-file" not in _hook(_repo(tmp_path))


def test_cli_line_empty_without_stop_file_and_never_raises(tmp_path):
    repo = _repo(tmp_path)
    p = subprocess.run([sys.executable, str(STOPFILES), "line", str(repo / ".sdlc")],
                       capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout == ""
    p = subprocess.run([sys.executable, str(STOPFILES), "line", str(tmp_path / "missing")],
                       capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout == ""


def test_dangling_symlink_is_not_reported_as_a_stop_for_any_daemon(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".sdlc" / "state"
    for n in ("watch.stop", "supervisor.stop"):
        (state / n).symlink_to(state / "nowhere")
    out = _doctor(repo)
    assert "STOPPED" not in out


def test_directory_named_watch_stop_is_flagged_as_ignored_but_supervisor_dir_is_a_stop(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".sdlc" / "state"
    (state / "watch.stop").mkdir()
    (state / "supervisor.stop").mkdir()
    p = subprocess.run([sys.executable, str(STOPFILES), "line", str(repo / ".sdlc")],
                       capture_output=True, text=True)
    assert "STOPPED by stop-file: supervisor.stop" in p.stdout
    assert "not a regular file" in p.stdout and "watch.stop" in p.stdout.split("not a regular file")[1]
    assert "STOPPED by stop-file: watch.stop" not in p.stdout


def test_unknown_age_reads_naturally():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sf", STOPFILES)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert "age unknown" in m._describe("watch.stop", "ledger watcher", None)
    assert "unknown age old" not in m._describe("watch.stop", "ledger watcher", None)


def test_unreadable_state_dir_is_could_not_check_not_an_all_clear(tmp_path):
    import pytest
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    repo = _repo(tmp_path, ("watch.stop",))
    state = repo / ".sdlc" / "state"
    state.chmod(0)
    try:
        assert "COULD NOT CHECK" in _doctor(repo)
        p = subprocess.run([sys.executable, str(STOPFILES), "line", str(repo / ".sdlc")],
                           capture_output=True, text=True)
        assert "COULD NOT CHECK" in p.stdout
    finally:
        state.chmod(0o755)

"""D7 'every refusal prints its fix': loop.py messages that named the problem but not the way out
(triage: docs/launch/evidence/d7-refusal-triage-complete-*.md). stdout contracts (`DONE`, `FAILED
exit=N evidence=...`, the exit codes) are untouched; the fix rides stderr or is appended."""
import json, os, pathlib, tempfile

from test_loop import _backlog, _lease_base, _loop, _verify_base


def test_agent_start_collision_names_the_reclaim_and_thread_levers(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        monkeypatch.setenv("CODEX_SESSION_ID", "01999aaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa")
        assert lp._dispatch(["loop.py", "agent-start", base, "42", "--pid", str(os.getpid())]) == 2
        err = capsys.readouterr().err
    assert "cannot register agent identity" in err              # tests/test_loop.py pins this substring
    assert f"`loop.py agent-reclaim {base} 42`" in err and "--thread" in err


def test_unverified_flake_says_why_it_blocks_done_and_how_to_clear_it(monkeypatch, capsys):
    lp = _loop()
    monkeypatch.setattr(lp, "_flake_verdict", lambda *a, **k: {
        "verdict": "unverified", "runs": [], "ms": 1,
        "disagreement": "runs disagreed on: tests/test_x.py::test_y"})
    with tempfile.TemporaryDirectory() as d:
        base = _verify_base(d, command="true")
        assert lp.verify_goal(base, "42") == 5
        err = capsys.readouterr().err
    assert "UNVERIFIED exit=5 -- runs disagreed on: tests/test_x.py::test_y" in err
    assert "the suite passed but is not deterministic: fix or remove the listed test(s)" in err
    assert f"`loop.py verify {base} 42`" in err
    # exit 5 does not block `record done` (evidence is still written pass/exit 0): never claim it does
    assert "stays blocked" not in err and "earns no credit" not in err


def test_failed_verify_points_at_the_evidence_tail_on_stderr_and_keeps_stdout(monkeypatch, capsys):
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        base = _verify_base(d, command="false")
        assert lp.verify_goal(base, "42") == 1
        out = capsys.readouterr()
    assert out.out.startswith("FAILED exit=1 evidence=")            # the stdout contract is unchanged
    assert '"tail"' in out.err and f"`loop.py verify {base} 42`" in out.err


def test_next_on_an_empty_local_backlog_says_there_are_no_goal_files(capsys):
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 0)
        lp.state.start_run(base)
        assert lp.main(["loop.py", "next", base]) == 0
        out = capsys.readouterr()
    assert out.out.strip() == "DONE"                                 # supervise_classify reads this line
    assert "no goal files" in out.err and "/sigma-scope" in out.err and ".sdlc/goals/" in out.err


def test_next_on_a_drained_local_backlog_stays_quiet_about_missing_files(capsys):
    """A goal file that exists but is done is a finished backlog, not an unqueued one."""
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        (pathlib.Path(base) / "goals" / "0001.md").write_text("---\nid: 0001\nstatus: done\n---\nx\n")
        lp.state.start_run(base)
        assert lp.main(["loop.py", "next", base]) == 0
        out = capsys.readouterr()
    assert out.out.strip() == "DONE" and "no goal files" not in out.err

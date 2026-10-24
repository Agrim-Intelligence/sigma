"""#2029 — the successor brief at HANDOFF, the classifier ignoring its span, the read-only meter verb,
and the hand-off docs. Plan: `.sdlc/plans/2029.md`; refinements: `.sdlc/plans/2029-review.md`.

Each clause's control (the break that turns it red) is named in the plan's Task 1.5, 2.5, 3.5, 4.5.
"""
import importlib.util
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sigma-loop" / "scripts"
LOG_PY = ROOT / "skills" / "sigma-log" / "scripts" / "log.py"
DOCS = ROOT / "docs" / "running-the-loop.md"
SKILL = ROOT / "skills" / "sigma-loop" / "SKILL.md"
SID = "5b0e8f4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
METER = re.compile(r"^meter: context=(\d+|unavailable \([^)]*\)) source=(host|unavailable) "
                   r"threshold=(\d+|none) \((.+)\) verdict=(under|over|unknown)$")


def _load(name, path=None):
    spec = importlib.util.spec_from_file_location(f"{name}_2029", path or S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


@pytest.fixture
def lp():
    return _load("loop")


def _backlog(tmp_path, n=1, config=None):
    base = tmp_path / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    cfg = {"budget": {"max_iterations": 999}}
    cfg.update(config or {})
    (base / "config.json").write_text(json.dumps(cfg))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "state" / "review-queue.md").write_text("# Q\n")
    for i in range(1, n + 1):
        (base / "goals" / f"{i:04d}.md").write_text(f"---\nid: {i:04d}\nstatus: pending\n---\nx\n")
    return str(base)


def _handoff_backlog(lp, tmp_path):
    base = _backlog(tmp_path, 1, {"handoff": {"after_goals": 1}})
    lp.state.advance_cursor(base, "goal")
    return base


# --- Task 1: the brief on the HANDOFF path --------------------------------------------------------

def test_handoff_prints_one_brief_on_stderr_and_the_bare_token_on_stdout(lp, tmp_path, capsys):
    base = _handoff_backlog(lp, tmp_path)
    assert lp.main(["loop.py", "next", base, "--session-pid", "12345"]) == 0
    out = capsys.readouterr()
    assert out.out == "HANDOFF\n"
    assert out.err.count("SUCCESSOR BRIEF:") == 1


def test_next_batch_handoff_prints_the_brief_once(lp, tmp_path, capsys):
    base = _handoff_backlog(lp, tmp_path)
    assert lp.main(["loop.py", "next-batch", base, "--session-pid", "12345"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "HANDOFF"
    assert out.err.count("SUCCESSOR BRIEF:") == 1


def test_no_brief_on_done_budget_or_a_goal(lp, tmp_path, capsys):
    base = _backlog(tmp_path / "goal", 1)
    lp.main(["loop.py", "next", base, "--session-pid", "12345"])
    goal = capsys.readouterr()
    assert goal.out.strip().endswith("0001.md") and "SUCCESSOR BRIEF" not in goal.err
    empty = _backlog(tmp_path / "done", 0)
    lp.main(["loop.py", "next", empty, "--session-pid", "12345"])
    done = capsys.readouterr()
    assert done.out.strip() == "DONE" and "SUCCESSOR BRIEF" not in done.err
    capped = _backlog(tmp_path / "budget", 2, {"budget": {"max_iterations": 1}})
    lp.state.advance_cursor(capped, "goal")
    lp.main(["loop.py", "next", capped, "--session-pid", "12345"])
    budget = capsys.readouterr()
    assert budget.out.strip() == "BUDGET" and "SUCCESSOR BRIEF" not in budget.err


def _resume(brief):
    return next(line for line in brief.splitlines() if line.startswith("resume:"))


def test_resume_line_carries_feature_and_label(lp, tmp_path):
    base = _backlog(tmp_path)
    line = _resume(lp._successor_brief(base, ["--session-pid", "1", "--skip", "a", "--feature", "voice",
                                              "--label", "run-x"], {}))
    head, _, fallback = line.partition(" (Codex/Cursor: ")
    assert head.endswith("/sigma-loop --feature voice --label run-x")
    assert fallback.endswith("--feature voice --label run-x)")
    assert "--skip" not in line and "--session-pid" not in line


def test_resume_line_without_scope_is_bare(lp, tmp_path):
    base = _backlog(tmp_path)
    line = _resume(lp._successor_brief(base, ["--session-pid", "1"], {}))
    assert "run: /sigma-loop (Codex/Cursor: " in line
    assert line.endswith(f"next {shlex.quote(base)})")


def test_resume_fallback_runs_as_pasted(lp, tmp_path):
    base = _backlog(tmp_path)
    line = _resume(lp._successor_brief(base, ["--feature", "voice"], {}))
    words = shlex.split(line.partition("(Codex/Cursor: ")[2].rstrip(")"))
    assert pathlib.Path(words[1]).resolve() == (S / "loop.py").resolve()
    assert words[2:] == ["next", base, "--feature", "voice"]


def test_brief_embeds_log_slots_verbatim(lp, tmp_path):
    base = _backlog(tmp_path)
    expected = _load("log", LOG_PY).slots(base).rstrip("\n")
    assert expected
    brief = lp._successor_brief(base, [], {})
    lines = brief.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("live slots ("))
    assert "\n".join(lines[at + 1:at + 1 + len(expected.splitlines())]) == expected


def test_a_failing_slots_reader_still_prints_the_brief(lp, tmp_path, capsys, monkeypatch):
    base = _handoff_backlog(lp, tmp_path)

    def boom(_sdlc_dir):
        raise RuntimeError("no log")
    monkeypatch.setattr(lp, "_live_slots", boom)
    assert lp.main(["loop.py", "next", base, "--session-pid", "12345"]) == 0
    out = capsys.readouterr()
    assert out.out == "HANDOFF\n"
    assert "live slots: unavailable (RuntimeError)" in out.err


def _decisions(brief):
    return brief.splitlines()[-1]


def test_open_decisions_local_names_review_queue_headings_as_ever_queued(lp, tmp_path):
    base = _backlog(tmp_path)
    (pathlib.Path(base) / "state" / "review-queue.md").write_text("# Q\n## 0003-a.md\nx\n## 0007-b.md\n")
    assert _decisions(lp._successor_brief(base, [], {})) == (
        "open decisions: parked goals ever queued (review-queue.md, not pruned): 0003-a.md, 0007-b.md")


def test_open_decisions_local_empty_says_none(lp, tmp_path):
    base = _backlog(tmp_path)
    assert _decisions(lp._successor_brief(base, [], {})) == (
        "open decisions: parked goals ever queued: none (review-queue.md)")


def test_open_decisions_github_points_at_the_label(lp, tmp_path):
    base = _backlog(tmp_path)
    (pathlib.Path(base) / "state" / "review-queue.md").write_text("## 0003-a.md\n")
    brief = lp._successor_brief(base, [], {"discovery": {"source": "github"}})
    assert _decisions(brief) == "open decisions: parked goals: issues labelled sdlc:parked"


def test_brief_ends_with_its_open_decisions_line(lp, tmp_path):
    base = _backlog(tmp_path)
    for config in ({}, {"discovery": {"source": "github"}}):
        assert _decisions(lp._successor_brief(base, [], config)).startswith("open decisions:")
    (pathlib.Path(base) / "state" / "review-queue.md").write_text("## 0003-a.md\n")
    assert _decisions(lp._successor_brief(base, [], {})).startswith("open decisions:")


def test_daemon_clause_names_the_dropped_flags(lp, tmp_path):
    brief = lp._successor_brief(_backlog(tmp_path), [], {})
    who = next(line for line in brief.splitlines() if line.startswith("who starts it:"))
    assert "supervise_daemon.py relaunches by itself (without --feature/--label)" in who


class _FixedRng:
    def randint(self, lo, hi):
        return lo


def _classify(text):
    return _load("supervise_classify").classify(text, now=0, rng=_FixedRng())


def test_daemon_classifies_the_bare_brief_as_a_handoff(lp, tmp_path, monkeypatch):
    monkeypatch.setattr(lp, "_live_slots", lambda _d: "")
    brief = lp._successor_brief(_backlog(tmp_path), ["--feature", "voice"], {})
    for head in ("LOOP STOP: handoff\n", "HANDOFF\n"):
        action, secs, reason = _classify(head + brief)
        assert (action, secs) == ("relaunch", 60) and "hand-off" in reason
    # The fixed text alone trips nothing checked before _HANDOFF, so a brief cut before its closing
    # line (which the strip leaves whole) still cannot fake a stop or a usage limit.
    sc = _load("supervise_classify")
    for name in ("_DONE", "_DONE_DEGRADED", "_LIMIT", "_RESET_AT", "_HOLD", "_BUDGET"):
        assert not getattr(sc, name).search(brief), name


# --- Task 2: the classifier ignores the brief's span when probing for a usage limit ---------------

_LIMIT_SLOT = ("Status — 1 goal(s) in flight, of 1 active in the action log\n\n"
               "* 🔵 **#42** · P5 IMPLEMENT · Back off when the API is rate-limited · model tier unrecorded\n"
               "  ↳ writing the retry → PR\n\n1 shown")
_LIMIT_SLUG = "## 0042-handle-rate-limit-retries.md\n"


def _limit_brief(lp, tmp_path, monkeypatch, indent=""):
    base = _backlog(tmp_path)
    (pathlib.Path(base) / "state" / "review-queue.md").write_text(_LIMIT_SLUG)
    monkeypatch.setattr(lp, "_live_slots", lambda _d: _LIMIT_SLOT)
    brief = lp._successor_brief(base, [], {})
    assert "rate-limit-retries" in _decisions(brief)
    return "\n".join(indent + line for line in brief.splitlines())


def test_a_rate_limit_slot_title_inside_the_brief_does_not_sleep_a_handoff(lp, tmp_path, monkeypatch):
    action, secs, _ = _classify("LOOP STOP: handoff\n" + _limit_brief(lp, tmp_path, monkeypatch))
    assert (action, secs) == ("relaunch", 60)


def test_an_indented_relayed_brief_is_still_stripped(lp, tmp_path, monkeypatch):
    text = "LOOP STOP: handoff\n" + _limit_brief(lp, tmp_path, monkeypatch, indent="    ")
    assert _classify(text)[:2] == ("relaunch", 60)


def test_a_real_usage_limit_line_outside_the_brief_still_sleeps(lp, tmp_path, monkeypatch):
    text = ("LOOP STOP: handoff\n" + _limit_brief(lp, tmp_path, monkeypatch)
            + "\nClaude usage limit reached; resets 5pm\n")
    action, _secs, reason = _classify(text)
    assert action == "sleep" and "stated reset" in reason


def test_a_brief_cut_before_its_closing_line_strips_nothing(lp, tmp_path, monkeypatch):
    brief = _limit_brief(lp, tmp_path, monkeypatch)
    cut = "\n".join(brief.splitlines()[:-1])
    assert _classify("LOOP STOP: handoff\n" + cut)[0] == "backoff"


# --- Task 3: the read-only meter verb -------------------------------------------------------------

def _transcript(tmp_path, monkeypatch, tokens):
    config = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    path = config / "projects" / "-repo" / f"{SID}.jsonl"
    path.parent.mkdir(parents=True)
    (path.parent / SID).mkdir()
    line = {"type": "assistant", "timestamp": "2026-10-24T10:00:00.000Z",
            "message": {"role": "assistant", "model": "claude-opus-5", "id": "m1",
                        "content": [{"type": "text", "text": "ok"}],
                        "usage": {"input_tokens": tokens - 3000, "cache_read_input_tokens": 2000,
                                  "cache_creation_input_tokens": 1000, "output_tokens": 5}}}
    path.write_text(json.dumps(line) + "\n")


def _meter(lp, capsys, *argv):
    rc = lp.main(["loop.py", "meter", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_meter_prints_reading_and_source(lp, tmp_path, monkeypatch, capsys):
    _transcript(tmp_path, monkeypatch, 90_000)
    rc, out, _ = _meter(lp, capsys, _backlog(tmp_path))
    assert rc == 0 and METER.match(out.strip()), out
    assert "context=90000 source=host " in out


def test_meter_unavailable_names_the_reason(lp, tmp_path, capsys):
    rc, out, _ = _meter(lp, capsys, _backlog(tmp_path), "--baseline", "1000")
    assert rc == 0 and METER.match(out.strip()), out
    assert out.startswith("meter: context=unavailable (no session id) source=unavailable ")
    assert out.strip().endswith("verdict=unknown")


def _tree(base):
    return sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in pathlib.Path(base).rglob("*"))


def test_meter_writes_nothing_and_claims_nothing(lp, tmp_path, monkeypatch, capsys):
    _transcript(tmp_path, monkeypatch, 90_000)
    base = _backlog(tmp_path)
    before = _tree(base)
    rc, out, _ = _meter(lp, capsys, base, "--baseline", "1000")
    assert rc == 0 and out.startswith("meter: ")
    assert _tree(base) == before
    assert not (pathlib.Path(base) / "state" / "heartbeat").exists()
    assert "meter" not in lp._ARMS_CLAIM


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "1.5"])
def test_meter_rejects_a_bad_baseline(lp, tmp_path, capsys, bad):
    rc, out, err = _meter(lp, capsys, _backlog(tmp_path), f"--baseline={bad}")
    assert rc == 2 and out == "" and len(err.strip().splitlines()) == 1


def test_meter_threshold_from_explicit_config(lp, tmp_path, monkeypatch, capsys):
    _transcript(tmp_path, monkeypatch, 90_000)
    base = _backlog(tmp_path, config={"handoff": {"after_context_tokens": 300_000}})
    rc, out, _ = _meter(lp, capsys, base, "--baseline", "1000")
    assert "threshold=300000 (handoff.after_context_tokens) verdict=under" in out


def test_meter_threshold_derived_from_baseline(lp, tmp_path, monkeypatch, capsys):
    """The whole context-shaped block reaches the resolver: its `window` caps the derived value."""
    _transcript(tmp_path, monkeypatch, 90_000)
    monkeypatch.setenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "200000")
    rc, out, _ = _meter(lp, capsys, _backlog(tmp_path), "--baseline", "80000")
    expected, _basis = lp._context_threshold({}, {"first_pick": 80_000, "window": 200_000})
    assert expected == 90_000                         # 45% of the 200k window, below 3 x 80k
    assert f"threshold={expected} (derived from --baseline" in out
    assert out.strip().endswith("verdict=over")


def test_meter_verdict_over_at_the_threshold(lp, tmp_path, monkeypatch, capsys):
    _transcript(tmp_path, monkeypatch, 90_000)
    at = _backlog(tmp_path / "at", config={"handoff": {"after_context_tokens": 90_000}})
    assert _meter(lp, capsys, at)[1].strip().endswith("verdict=over")
    above = _backlog(tmp_path / "above", config={"handoff": {"after_context_tokens": 90_001}})
    assert _meter(lp, capsys, above)[1].strip().endswith("verdict=under")


def test_meter_handoff_disabled_prints_none(lp, tmp_path, monkeypatch, capsys):
    _transcript(tmp_path, monkeypatch, 90_000)
    base = _backlog(tmp_path, config={"handoff": {"enabled": False, "after_context_tokens": 300_000}})
    out = _meter(lp, capsys, base, "--baseline", "1000")[1]
    assert "threshold=none (handoff disabled) verdict=unknown" in out


def test_meter_without_a_baseline_says_no_baseline(lp, tmp_path, monkeypatch, capsys):
    _transcript(tmp_path, monkeypatch, 90_000)
    out = _meter(lp, capsys, _backlog(tmp_path))[1]
    assert "threshold=none (no baseline) verdict=unknown" in out


# --- Task 4: the docs and the skill ---------------------------------------------------------------

def _section():
    text = DOCS.read_text(encoding="utf-8")
    start = text.index("## Hand-off: who starts the next session")
    end = text.find("\n## ", start + 1)
    return " ".join(text[start:end if end != -1 else None].split())


@pytest.mark.parametrize("phrase", [
    "supervise_daemon.py", "one-click", "holds no goal state", "replaces itself",
    "a person or a scheduler", "only at hand-off", "as subagents", "never as separate sessions",
    "top-level", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT",
])
def test_running_the_loop_documents_the_handoff_paths(phrase):
    assert phrase in _section()


def test_skill_stop_relays_the_successor_brief():
    assert "successor brief" in " ".join(SKILL.read_text(encoding="utf-8").split())


GESTURE = "python3 <installed-sigma>/skills/sigma-loop/scripts/loop.py meter .sdlc --baseline <first reading>"


def test_the_documented_meter_gesture_runs(tmp_path, monkeypatch):
    found = [line for line in DOCS.read_text(encoding="utf-8").splitlines() if GESTURE in line]
    assert len(found) == 1, found
    base = _backlog(tmp_path)
    _transcript(tmp_path, monkeypatch, 90_000)
    line = (GESTURE.replace("<installed-sigma>", str(ROOT)).replace(" .sdlc ", f" {base} ")
            .replace("<first reading>", "1000"))
    argv = shlex.split(line)
    argv[0] = sys.executable
    run = subprocess.run(argv, capture_output=True, text=True, env=dict(os.environ), timeout=60)
    assert run.returncode == 0, run.stderr
    assert METER.match(run.stdout.strip()), run.stdout
    assert " source=host " in run.stdout

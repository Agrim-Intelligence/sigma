"""Process-time hook — hooks/time_track.py.

The pure transition function is tested in-process with no subprocess at all; that is the whole
reason the hook is one state machine over one marker rather than three scripts. The stdin CLI
around it is tested the way `tests/test_research_capture.py` tests its hook: a real `python3`
subprocess fed a payload, never through `_py.sh` (that wrapper's own tests are pre-existing
failures on this host and are confirmed only by the live manual check).
"""
import importlib.util
import json
import pathlib

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "time_track.py"


def _mod():
    spec = importlib.util.spec_from_file_location("time_track", HOOK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------- the transition function (S2)


def test_a_skill_switch_mid_turn_splits_the_segment():
    """S2's acceptance criterion. `/sigma-plan` invoked partway through a turn must not hand the
    whole turn to either skill: the seconds before the switch belong to what was active, the rest
    to the new skill. Attributing a whole turn to one skill is what would make "time in plan"
    false every time a skill is invoked after the first sentence."""
    tt = _mod()
    state = {"skill": "sigma-dossier", "started": 1000}
    state, closed = tt.apply(state, "skill", 5000, skill="sigma-plan")
    assert closed == {"name": "sigma-dossier", "started": 1000, "ms": 4000}
    assert state == {"skill": "sigma-plan", "started": 5000}


def test_a_prompt_opens_a_segment_for_the_active_skill():
    tt = _mod()
    state, closed = tt.apply({"skill": "sigma-plan", "started": None}, "prompt", 100)
    assert closed is None
    assert state == {"skill": "sigma-plan", "started": 100}


def test_no_skill_is_active_before_any_invocation():
    """Work before the first skill is still work; it is attributed to a real, visible bucket."""
    tt = _mod()
    assert tt.fresh_state() == {"skill": tt.NO_SKILL, "started": None}
    assert tt.NO_SKILL == "(none)"


def test_a_real_prompt_and_stop_keep_the_no_skill_bucket(tmp_path):
    """The marker read between hook processes must preserve the documented no-skill bucket."""
    tt = _mod()
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    tt.handle(str(tmp_path), "prompt", {"session_id": "s1", "prompt": "hello"}, now_ms=1000)
    tt.handle(str(tmp_path), "stop", {"session_id": "s1"}, now_ms=4000)
    lines = tt.timing_store.read_session(tmp_path / ".sdlc", "s1")
    assert [(line["name"], line["ms"]) for line in lines] == [("(none)", 3000)]


def test_a_typed_slash_command_is_a_switch():
    """The plan-review finding this exists for: a user-typed `/sigma-plan` arrives as PROMPT TEXT,
    never as a `Skill` tool call, and every Sigma skill is keyed "use when the user runs
    /sdlc-…". Without this, a human-driven session is attributed entirely to `(none)` under a
    `precise` label."""
    tt = _mod()
    state, closed = tt.apply({"skill": "sigma-dossier", "started": None}, "prompt", 100,
                             prompt="/sigma-plan make it retry on 503")
    assert closed is None
    assert state == {"skill": "sigma-plan", "started": 100}


def test_only_the_token_is_taken_from_a_slash_prompt():
    """The one security boundary the feature adds. The prompt is on stdin; the token is the only
    thing that may leave it, and the rest must be unrecoverable from anything the hook returns."""
    tt = _mod()
    state, _ = tt.apply(tt.fresh_state(), "prompt", 100,
                        prompt="/sigma-plan the password is hunter2-MARKER")
    assert state["skill"] == "sigma-plan"
    assert "MARKER" not in json.dumps(state) and "hunter2" not in json.dumps(state)


def test_a_leading_path_or_number_is_not_a_skill_switch():
    """Code review C8: `/e/sigma is the repo` switched to a skill named `e`, and `/123` to
    `123`. A skill token starts with a letter and is the whole first word."""
    tt = _mod()
    for prompt in ("/e/sigma/sigma is the repo", "/123 things", "/", "/sigma-plan/extra x",
                   "/-dash"):
        state, _ = tt.apply({"skill": "sigma-plan", "started": None}, "prompt", 100, prompt=prompt)
        assert state["skill"] == "sigma-plan", prompt


def test_a_slash_token_is_bounded():
    tt = _mod()
    state, _ = tt.apply(tt.fresh_state(), "prompt", 100, prompt="/" + "a" * 500)
    assert len(state["skill"]) <= 80


def test_a_prompt_that_is_not_a_slash_command_keeps_the_active_skill():
    tt = _mod()
    state, _ = tt.apply({"skill": "sigma-plan", "started": None}, "prompt", 100,
                        prompt="ok, and /sigma-review is not at the start")
    assert state["skill"] == "sigma-plan"


def test_stop_closes_and_opens_a_provisional_segment():
    """`Stop` is not once per turn: with the stop gate on, a blocked Stop makes the agent continue
    and Stop fires again. Closing and re-opening means the continuation is measured; a genuine
    end leaves the provisional segment dangling, and the next prompt drops it (S4)."""
    tt = _mod()
    state, closed = tt.apply({"skill": "sigma-implement", "started": 1000}, "stop", 4000)
    assert closed == {"name": "sigma-implement", "started": 1000, "ms": 3000}
    assert state == {"skill": "sigma-implement", "started": 4000}


def test_a_switch_with_no_open_segment_closes_nothing():
    """A `Skill` event can be the first thing the hook sees (a missed prompt event, a fresh
    marker). Nothing is open, so nothing is closed — and certainly nothing is invented."""
    tt = _mod()
    state, closed = tt.apply({"skill": "(none)", "started": None}, "skill", 500, skill="sigma-plan")
    assert closed is None
    assert state == {"skill": "sigma-plan", "started": 500}


def test_a_backwards_clock_records_nothing():
    """A negative interval is unmeasurable. Omitted, never a zero, never a negative."""
    tt = _mod()
    state, closed = tt.apply({"skill": "sigma-plan", "started": 5000}, "stop", 4000)
    assert closed is None
    assert state["started"] == 4000


# --------------------------------------------------------------------- the stdin CLI (S3)

import os
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _run(project_dir, event, payload, **env_extra):
    """The repo's own hook-test idiom (`test_research_capture._run`): a real `python3` subprocess
    fed the payload on stdin — never through `_py.sh`. The two session env vars are stripped so a
    test's own environment cannot leak a session id in."""
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.pop("SIGMA_RUN_ID", None)
    env.update(env_extra)
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return subprocess.run(["python3", str(HOOK), event], input=data, capture_output=True, env=env)


def _adopted(tmp):
    (pathlib.Path(tmp) / ".sdlc" / "state").mkdir(parents=True)
    return tmp


def _every_file_under(root):
    return [p for p in pathlib.Path(root).rglob("*") if p.is_file()]


def test_the_hook_never_writes_prompt_text_to_disk():
    """S3's acceptance criterion, and the one security boundary the feature adds. The prompt is
    on stdin; a marker string inside it must reach no file — while the `/sigma-plan` prefix on the
    same prompt still registers as a switch. Both halves in one test, so the switch cannot be
    satisfied by the cheap route of writing the prompt."""
    def leaked(tmp):
        blobs = [p.read_text(encoding="utf-8", errors="replace")
                 for p in _every_file_under(pathlib.Path(tmp) / ".sdlc")]
        return any("MARKER" in b or "hunter2" in b for b in blobs), blobs

    with tempfile.TemporaryDirectory() as tmp:
        _adopted(tmp)
        r = _run(tmp, "prompt", {"session_id": "s1",
                                 "prompt": "/sigma-plan the token is hunter2-MARKER-9f1"})
        assert r.returncode == 0, r.stderr
        # Scanned IMMEDIATELY after the prompt event, not only at the end: a later event rewrites
        # the marker, and a control that leaked the prompt into it was erased by the following
        # `stop` before a single end-of-test scan ever looked. A prompt that touches disk even
        # briefly is a leak.
        hit, _ = leaked(tmp)
        assert not hit, "prompt text reached disk on the prompt event"

        r = _run(tmp, "stop", {"session_id": "s1"})
        assert r.returncode == 0, r.stderr
        hit, blobs = leaked(tmp)
        assert blobs, "nothing was written at all"
        assert not hit, "prompt text reached disk by the stop event"
        assert any("sigma-plan" in b for b in blobs)                  # the switch registered


def test_no_sdlc_directory_means_no_write_and_exit_zero():
    """The hook fires in every repository on the machine. It records only where Sigma is
    adopted — not a config key, the same rule `sigma_gate.sh` follows."""
    with tempfile.TemporaryDirectory() as tmp:
        r = _run(tmp, "prompt", {"session_id": "s1", "prompt": "/sigma-plan"})
        assert r.returncode == 0, r.stderr
        assert not (pathlib.Path(tmp) / ".sdlc").exists()


def test_garbage_stdin_is_a_no_op():
    with tempfile.TemporaryDirectory() as tmp:
        _adopted(tmp)
        r = _run(tmp, "prompt", b"garbage{{ not json")
        assert r.returncode == 0
        assert _every_file_under(pathlib.Path(tmp) / ".sdlc") == []


def test_the_session_id_comes_from_the_payload_then_the_environment():
    """Claude Code delivers the id on stdin; the script floor only has the environment. Both are
    the same value on Claude Code, and either satisfies the chain."""
    with tempfile.TemporaryDirectory() as tmp:
        _adopted(tmp)
        _run(tmp, "prompt", {"session_id": "from-payload", "prompt": "x"},
             CLAUDE_CODE_SESSION_ID="from-env")
        _run(tmp, "prompt", {"prompt": "y"}, CLAUDE_CODE_SESSION_ID="from-env")
        sessions = sorted(p.name for p in (pathlib.Path(tmp) / ".sdlc/state/time/_sessions").iterdir())
        assert sessions == ["from-env", "from-payload"]


def test_an_unsafe_payload_session_id_falls_through_not_over():
    """A traversal-shaped id on stdin is treated as absent — the next link is used — and the hook
    still exits 0. It never becomes a path, and it never fails the turn."""
    with tempfile.TemporaryDirectory() as tmp:
        _adopted(tmp)
        r = _run(tmp, "prompt", {"session_id": "../../escape", "prompt": "x"},
                 SIGMA_RUN_ID="run-7")
        assert r.returncode == 0
        root = pathlib.Path(tmp) / ".sdlc/state/time/_sessions"
        assert sorted(p.name for p in root.iterdir()) == ["run-7"]
        assert not (pathlib.Path(tmp) / "escape").exists()


def test_the_closed_segment_is_appended_before_the_marker_is_rewritten(tmp_path, monkeypatch):
    """The crash window that de-duplication exists for: if the append fails, the marker must still
    hold the OLD state, so the next event re-closes the same segment (same `started`) and the
    store collapses it. Written the other way round, a failed append would advance the marker and
    the segment would be lost outright."""
    tt = _mod()
    project = tmp_path
    (project / ".sdlc" / "state").mkdir(parents=True)
    tt.handle(str(project), "prompt", {"session_id": "s1", "prompt": "x"}, now_ms=1000)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(tt.timing_store, "append_session", boom)
    try:
        tt.handle(str(project), "stop", {"session_id": "s1"}, now_ms=4000)
    except OSError:
        pass                                             # `main` is what swallows; `handle` does not
    marker = json.loads((project / ".sdlc/state/time/_sessions/s1/open.json").read_text())
    assert marker["started"] == 1000                     # not advanced past the failed append


# --------------------------------------------------------------------- double-stop and dangling (S4)


def test_a_double_stop_loses_nothing_and_a_dangling_segment_records_nothing():
    """S4's acceptance criterion, in `apply()` terms. With the stop gate on, a blocked `Stop` makes
    the agent continue and `Stop` fires again: both stretches must be measured. At a genuine end,
    the provisional segment `stop` leaves open is DANGLING when the next prompt arrives and is
    dropped — that drop is the whole idle-exclusion mechanism, so a 55-second wait produces no
    line at all."""
    tt = _mod()
    state = tt.fresh_state()
    recorded = []
    state, closed = tt.apply(state, "prompt", 0)                          # turn begins
    assert closed is None
    state, closed = tt.apply(state, "stop", 3000)                         # blocked by the gate…
    recorded.append(closed)
    state, closed = tt.apply(state, "stop", 5000)                         # …continued, then ended
    recorded.append(closed)
    state, closed = tt.apply(state, "prompt", 60000)                      # 55 s later, next turn
    recorded.append(closed)

    assert [c["ms"] if c else None for c in recorded] == [3000, 2000, None]
    assert state == {"skill": tt.NO_SKILL, "started": 60000}


def test_a_prompt_on_a_crashed_turn_records_nothing_for_the_old_segment():
    """A turn that never reached `Stop` (an interrupt, a crash) leaves a segment open with no end.
    The interval is unmeasurable, so it is dropped — never estimated to the next prompt."""
    tt = _mod()
    state, closed = tt.apply({"skill": "sigma-implement", "started": 1000}, "prompt", 9000)
    assert closed is None
    assert state == {"skill": "sigma-implement", "started": 9000}


def test_end_to_end_a_double_stop_writes_exactly_two_lines_and_idle_writes_none(tmp_path):
    """The same sequence through `handle()` and the real store, with the clock injected. Two
    lines for the two measured stretches; nothing for the idle gap; the marker left open for
    the new turn."""
    tt = _mod()
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    p = str(tmp_path)
    tt.handle(p, "prompt", {"session_id": "s1", "prompt": "/sigma-implement go"}, now_ms=0)
    tt.handle(p, "stop", {"session_id": "s1"}, now_ms=3000)
    tt.handle(p, "stop", {"session_id": "s1"}, now_ms=5000)
    tt.handle(p, "prompt", {"session_id": "s1", "prompt": "next"}, now_ms=60000)

    lines = tt.timing_store.read_session(tmp_path / ".sdlc", "s1")
    assert [(e["name"], e["ms"]) for e in lines] == [("sigma-implement", 3000),
                                                     ("sigma-implement", 2000)]
    marker = json.loads((tmp_path / ".sdlc/state/time/_sessions/s1/open.json").read_text())
    assert marker == {"skill": "sigma-implement", "started": 60000}


# --------------------------------------------------------------------- security review (2026-09-11)


def test_the_marker_is_never_written_through_a_link(tmp_path):
    """Security S1 (probe G): `open.json -> notes.txt` had `notes.txt` overwritten with marker
    JSON on the next event. A marker that is a link is refused; nothing is written."""
    import os
    tt = _mod()
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    victim = tmp_path / "notes.txt"
    victim.write_text("precious\n")
    sdir = tmp_path / ".sdlc" / "state" / "time" / "_sessions" / "s1"
    sdir.mkdir(parents=True)
    try:
        os.symlink(str(victim), str(sdir / "open.json"))
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("file symlink creation is not available on this host")

    assert tt.handle(str(tmp_path), "prompt", {"session_id": "s1", "prompt": "x"}, now_ms=1000) is None
    assert victim.read_text() == "precious\n"


def test_a_hostile_marker_is_sanitised_not_propagated(tmp_path):
    """Security S4. The marker is read back from disk with type trust: a dict `skill` became a
    dict `name` in `turns.jsonl` and crashed `render_session`; a non-int `started` raised on every
    event until the next prompt. A bad marker is a fresh marker, and a name is always a bounded
    string."""
    tt = _mod()
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    sdir = tmp_path / ".sdlc" / "state" / "time" / "_sessions" / "s1"
    sdir.mkdir(parents=True)
    (sdir / "open.json").write_text(json.dumps({"skill": {"evil": "dict"}, "started": "x"}))

    tt.handle(str(tmp_path), "stop", {"session_id": "s1"}, now_ms=5000)    # must not raise
    (sdir / "open.json").write_text(json.dumps({"skill": "ok\nline", "started": 1000}))
    tt.handle(str(tmp_path), "stop", {"session_id": "s1"}, now_ms=5000)

    lines = tt.timing_store.read_session(tmp_path / ".sdlc", "s1")
    assert all(isinstance(e["name"], str) and "\n" not in e["name"] for e in lines)
    ROOT_ = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "time_report", ROOT_ / "skills" / "sigma-time" / "scripts" / "time_report.py")
    tr = importlib.util.module_from_spec(spec); spec.loader.exec_module(tr)
    assert isinstance(tr.render_session(str(tmp_path / ".sdlc"), "s1"), str)


def test_hook_wired_in_hooks_json():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]

    def wired(event, needs_matcher=None):
        for h in hooks.get(event, []):
            if needs_matcher is not None and h.get("matcher") != needs_matcher:
                continue
            if "time_track.py" in json.dumps(h):
                return True
        return False

    assert wired("UserPromptSubmit")
    assert wired("PreToolUse", needs_matcher="Skill")
    assert wired("Stop")

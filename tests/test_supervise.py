"""Overnight supervisor: the exit classifier (pure) + the wrapper e2e with a fake
session command. No real sessions, no sleeping (scale=0), no network."""
import importlib.util, os, pathlib, re, subprocess, sys, time
import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _mod():
    spec = importlib.util.spec_from_file_location("sc", S / "supervise_classify.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _daemon_mod():
    spec = importlib.util.spec_from_file_location("sd", S / "supervise_daemon.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


class _FixedRng:
    def randint(self, lo, hi):
        return lo


def test_done_when_loop_reports_its_own_stop():
    m = _mod()
    for tail in ("stopped: backlog-empty", "LOOP STOP: backlog-empty\n3 done, 1 parked",
                 "Backlog is empty.", "DONE"):
        assert m.classify(tail, rng=_FixedRng())[0] == "done", tail


def test_classify_degraded_done_backs_off_instead_of_done():
    """#1084: the tail shape `_print_pick` actually emits for a degraded-read DONE (bare `DONE`
    on stdout, `DONE (degraded ...)` on stderr, merged into one tail the way `supervise_daemon.py`
    hands it to the classifier). Today, before this fix, this exact tail classifies `"done"` --
    the masked-GraphQL-failure bug #1084 exists to stop."""
    m = _mod()
    tail = "DONE\nDONE (degraded read — backlog state unknown)\n"
    assert m.classify(tail, rng=_FixedRng())[0] == "backoff"


def test_classify_degraded_done_reason_names_the_ambiguity():
    m = _mod()
    tail = "DONE\nDONE (degraded read — backlog state unknown)\n"
    _, _, reason = m.classify(tail, rng=_FixedRng())
    assert "degraded" in reason


def test_classify_degraded_backoff_escalates_like_crash():
    m = _mod()
    tail = "DONE\nDONE (degraded read — backlog state unknown)\n"
    assert m.classify(tail, attempt=0, rng=_FixedRng())[1] == 300
    assert m.classify(tail, attempt=1, rng=_FixedRng())[1] == 600
    assert m.classify(tail, attempt=2, rng=_FixedRng())[1] == 1200
    assert m.classify(tail, attempt=3, rng=_FixedRng())[1] == 3600
    assert m.classify(tail, attempt=99, rng=_FixedRng())[1] == 3600      # capped


def test_stop_report_alone_is_NOT_done_budget_wins():
    # THE review-found bug: the "N done, M parked" report prints on EVERY stop —
    # a budget stop carrying it must classify relaunch, never done.
    m = _mod()
    action, _, _ = m.classify("LOOP STOP: budget\n0 done, 2 parked, 0 failed", rng=_FixedRng())
    assert action == "relaunch"
    action, _, _ = m.classify("2 done, 1 parked, 0 failed", rng=_FixedRng())
    assert action != "done"                     # report without a success marker = unknown


def test_budget_stop_relaunches_after_short_pause():
    m = _mod()
    # the true contract: `loop.py next` prints BUDGET on its own line
    action, secs, _ = m.classify("$ loop.py next .sdlc\nBUDGET\n0 done, 2 parked", rng=_FixedRng())
    assert action == "relaunch" and secs == 60


def test_budget_stop_still_relaunches_alongside_the_new_diagnostic_line():
    """#411 consumer-check: `loop.py next`'s stdout+stderr are merged by supervise_daemon.py itself
    (its per-run `subprocess.run(argv_cmd, stdout=f, stderr=subprocess.STDOUT, ...)`), so the
    diagnostic naming which ceiling tripped now lands in the SAME
    captured tail this classifier reads. `_BUDGET`'s own first alternative (`^\\s*BUDGET\\s*$`) is
    an EXACT-LINE match that a suffixed "BUDGET (...)" line would NOT satisfy -- this is exactly
    why the bare `BUDGET` line stays untouched on its own line and the diagnostic is a SEPARATE
    line: proves the combined output classifies "relaunch" the same as before, not "unclassified"."""
    m = _mod()
    tail = ("$ loop.py next .sdlc\n"
            "BUDGET\n"
            "BUDGET (elapsed 720min >= max_minutes 480)\n"
            "0 done, 2 parked")
    action, secs, _ = m.classify(tail, rng=_FixedRng())
    assert action == "relaunch" and secs == 60


# ------------------------------------------------------------------------------- handoff (#2521)
# A `handoff` stop is a DELIBERATE, healthy context-bound checkpoint for the orchestrating
# session -- never a problem -- so it gets the same short relaunch pause BUDGET gets, never the
# longer "not a crash" ABNORMAL pause a genuinely unrecognised stop gets.


def test_handoff_stop_relaunches_after_a_short_pause():
    """Mirrors test_budget_stop_relaunches_after_short_pause exactly, for the new verdict — a
    hand-off is a deliberate, healthy checkpoint, so it gets the SAME short pause budget gets
    (60s), never the 300s 'ABNORMAL STOP' pause a genuinely unrecognised stop gets."""
    m = _mod()
    action, secs, reason = m.classify("LOOP STOP: handoff\nN done, M parked, K failed", rng=_FixedRng())
    assert action == "relaunch"
    assert secs == 60
    assert "hand-off" in reason


def test_bare_handoff_token_with_no_loop_stop_sentence_still_relaunches():
    """Post-PR review, finding 1 (blocking): mirrors test_handoff_stop_relaunches_after_a_short_
    pause's real-contract counterpart at line 71 (the bare-`BUDGET`-token test) -- `loop.py`'s
    `_print_pick` unconditionally prints the bare `HANDOFF` token to stdout for EVERY handoff stop,
    independent of whether the session's own prose ever reaches the "LOOP STOP: handoff" sentence
    SKILL.md instructs it to print. A tail ending in the bare token alone -- e.g. the model's final
    response was cut off, truncated, or interrupted before finishing that sentence, but the tool
    call's own stdout already landed in the transcript tail -- must still classify as the healthy
    HANDOFF relaunch, not fall through to the generic ABNORMAL/300s bucket."""
    m = _mod()
    action, secs, reason = m.classify(
        "$ loop.py next .sdlc\nHANDOFF\n0 done, 2 parked", rng=_FixedRng())
    assert action == "relaunch"
    assert secs == 60
    assert "hand-off" in reason


def test_quoted_handoff_marker_is_not_a_handoff_stop():
    """Same anchoring discipline _DONE/_BUDGET/_ABNORMAL already require (module docstring): a
    MENTION of the marker, not on its own line, must never BE one."""
    m = _mod()
    text = 'The session said: "I am deliberately not printing LOOP STOP: handoff yet"'
    action, secs, reason = m.classify(text, rng=_FixedRng())
    assert action != "relaunch" or "hand-off" not in reason


def test_backlog_and_budget_still_win_over_handoff():
    """Ordering: _DONE and _BUDGET are both checked before _HANDOFF in the classifier (mirrors
    test_backlog_and_budget_still_win_over_abnormal's own precedence test)."""
    m = _mod()
    action, secs, reason = m.classify("LOOP STOP: budget\nLOOP STOP: handoff", rng=_FixedRng())
    assert "budget" in reason and "hand-off" not in reason


def test_removing_the_handoff_branch_falls_back_to_abnormal_pause_with_a_misleading_label():
    """THE CONTROL (AGENTS.md 'run the control, or the check is decoration'): deliberately delete
    the _HANDOFF branch this task adds and confirm the classifier regresses to exactly the wrong
    behaviour — a healthy, deliberate context refresh reported as an 'ABNORMAL STOP... not a
    crash', at the 300s pause instead of the 60s one, which is precisely the LIVENESS violation
    (AGENTS.md: a healthy state must be distinguishable from a broken one) this branch exists to
    prevent. Manually confirmed against TODAY's unpatched module (before this task's implementation
    landed) that this is exactly the baseline behavior: action=relaunch, secs=300, "ABNORMAL STOP"
    in reason -- so this control, once the real _HANDOFF branch exists, proves the branch (not
    something else) is what changes that outcome."""
    m = _mod()
    src = pathlib.Path(m.__file__).read_text()
    patched = re.sub(
        r'\n    if _HANDOFF\.search\(text\):\n        return \("relaunch", _HANDOFF_PAUSE,.*?\)\n',
        '\n', src, flags=re.S)
    assert patched != src, (
        "the _HANDOFF branch text was not found in the module source -- the control's own regex is "
        "stale and is not actually removing anything, which would make it prove nothing")
    ns = {}
    exec(compile(patched, "supervise_classify_mutated", "exec"), ns)
    action, secs, reason = ns["classify"]("LOOP STOP: handoff\nN done, M parked, K failed", rng=_FixedRng())
    assert action == "relaunch" and secs == 300 and "ABNORMAL STOP" in reason, (
        "expected the mutated (branch-removed) classifier to mislabel a hand-off stop as ABNORMAL "
        "— if this fails, the _HANDOFF branch was not actually the thing making the real test above "
        "pass, and this control is not proving what it claims to")


def test_limit_with_reset_time_sleeps_until_reset_plus_jitter():
    m = _mod()
    # now = 03:00 local; message says resets at 5:30 -> 2.5h + 120s jitter
    now = time.mktime((2026, 7, 24, 3, 0, 0, 0, 0, -1))
    action, secs, reason = m.classify(
        "You have hit your usage limit. Your limit resets at 5:30am.",
        now=now, rng=_FixedRng())
    assert action == "sleep"
    assert secs == int(2.5 * 3600) + 120
    assert "reset" in reason


def test_limit_reset_earlier_today_means_tomorrow():
    m = _mod()
    now = time.mktime((2026, 7, 24, 6, 0, 0, 0, 0, -1))   # 6:00; "resets at 5:30" = next day
    action, secs, _ = m.classify("usage limit reached — resets at 5:30", now=now, rng=_FixedRng())
    assert action == "sleep"
    assert secs == int(23.5 * 3600) + 120


def test_limit_without_time_backs_off_capped():
    m = _mod()
    waits = [m.classify("rate limit exceeded, try later", attempt=a, rng=_FixedRng())[1]
             for a in (0, 1, 2, 9)]
    assert waits == [1800, 3600, 3600, 3600]


def test_unknown_crash_escalates_capped():
    m = _mod()
    waits = [m.classify("Traceback (most recent call last): boom", attempt=a, rng=_FixedRng())[1]
             for a in (0, 1, 2, 3, 9)]
    assert waits == [300, 600, 1200, 3600, 3600]


def _run_supervisor(tmp_path, fake_script, max_runs="10"):
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    fake = tmp_path / "fake-claude.sh"
    fake.write_text("#!/usr/bin/env bash\n" + fake_script)
    fake.chmod(0o755)
    env = {**os.environ,
           "SIGMA_CLAUDE_CMD": str(fake),
           "SIGMA_SUPERVISE_MAX_RUNS": max_runs,
           "SIGMA_SUPERVISE_SLEEP_SCALE": "0"}
    return subprocess.run([sys.executable, str(S / "supervise_daemon.py"), str(base)],
                          capture_output=True, text=True, env=env, timeout=60), base


def test_wrapper_exits_zero_on_backlog_empty(tmp_path):
    proc, base = _run_supervisor(
        tmp_path, 'echo "run complete"; echo "LOOP STOP: backlog-empty"\n')
    assert proc.returncode == 0
    assert "action=done" in (base / "state" / "supervisor.log").read_text()


def test_wrapper_relaunches_through_limit_then_finishes(tmp_path):
    # 1st session: limit (no parseable time -> backoff, scaled to 0s); 2nd: done.
    script = (
        'N="$(cat "$STATE_DIR/n" 2>/dev/null || echo 0)"; N=$((N+1)); echo "$N" > "$STATE_DIR/n"\n'
        'if [ "$N" -lt 2 ]; then echo "usage limit reached, try again later"; else echo "LOOP STOP: backlog-empty"; fi\n')
    base_dir = tmp_path / ".sdlc"
    script = script.replace("$STATE_DIR", str(tmp_path))
    proc, base = _run_supervisor(tmp_path, script)
    assert proc.returncode == 0
    log = (base / "state" / "supervisor.log").read_text()
    assert "action=backoff" in log and "action=done" in log


def test_wrapper_stop_file_halts_cleanly(tmp_path):
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    (base / "state" / "supervisor.stop").write_text("")
    env = {**os.environ, "SIGMA_CLAUDE_CMD": "false",
           "SIGMA_SUPERVISE_SLEEP_SCALE": "0"}
    proc = subprocess.run([sys.executable, str(S / "supervise_daemon.py"), str(base)],
                          capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0 and "stop-file" in proc.stdout


def test_wrapper_max_runs_caps_a_crash_loop(tmp_path):
    proc, base = _run_supervisor(tmp_path, 'echo "segfault-ish nonsense"\n', max_runs="3")
    assert proc.returncode == 1
    log = (base / "state" / "supervisor.log").read_text()
    assert "max runs (3) reached" in proc.stdout + log
    # Tightened (plan §B, additional break-it pass C4): assert the EXACT run count from the log,
    # not just the returncode/message -- an off-by-one (`runs > max_runs` instead of `runs >=
    # max_runs`) lets ONE extra run through while still printing "max runs (3) reached" and
    # returning 1, so a looser assertion here would not catch it.
    assert log.count("supervisor: run #") == 3, \
        f"expected exactly 3 runs logged, got {log.count('supervisor: run #')}:\n{log}"


def test_a_missing_session_command_is_a_crash_not_a_traceback(tmp_path):
    """RESILIENCY regression (AGENTS.md: "every failure mode has a stated recovery"). The bash
    original SURVIVES `SIGMA_CLAUDE_CMD` naming a binary that doesn't exist -- the shell's own
    `command not found` on line-44-equivalent is a 127 exit plus a message on stderr, not an
    exception, so it flows into `$RUNOUT`, the classifier reads `command not found` (a real
    `_CRASH_SIG` alternative -- `test_real_crash_signatures_still_escalate` pins the same wording
    for "zsh: command not found: claude"), and the supervisor climbs the ordinary crash-backoff
    ladder. This is a DESIGNED behaviour: an unlaunchable `claude` (not on PATH in a headless/cron
    env) is exactly the overnight failure this component exists to survive.

    A Python `subprocess.run` on a nonexistent executable raises `FileNotFoundError` (an `OSError`
    subclass) instead of returning a non-zero exit code -- an unhandled one kills the whole daemon
    process with a traceback, no log line, no exit code contract honoured at all. Driving the
    daemon the documented way (not via a wrapping bash fake-session script, since the defect is in
    the subprocess launch itself, before any fake script would even matter)."""
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    env = {**os.environ,
           "SIGMA_CLAUDE_CMD": "definitely-not-a-real-binary-xyz",
           "SIGMA_SUPERVISE_MAX_RUNS": "2",
           "SIGMA_SUPERVISE_SLEEP_SCALE": "0"}
    proc = subprocess.run([sys.executable, str(S / "supervise_daemon.py"), str(base)],
                          capture_output=True, text=True, env=env, timeout=30)
    assert "Traceback (most recent call last)" not in proc.stderr, \
        f"the daemon itself died with a traceback instead of surviving the missing command:\n{proc.stderr}"
    assert proc.returncode == 1, \
        f"expected exit 1 (max-runs reached, matching bash), got {proc.returncode}\nstderr:\n{proc.stderr}"
    log = (base / "state" / "supervisor.log").read_text()
    assert "max runs (2) reached" in proc.stdout + log, log
    assert "action=backoff" in log, \
        f"expected the crash-backoff ladder (matching bash's own behaviour), got:\n{log}"


def test_an_unwritable_state_dir_still_reaches_max_runs(tmp_path):
    """RESILIENCY regression, cycle 2. bash in this scenario degrades NOISILY (permission-denied
    on every redirection/`tee`) but keeps looping to `max_runs` and exits 1 -- it got this
    tolerance for free from `set -uo pipefail` WITHOUT `-e`. The port has to ask for it
    explicitly: `_append`'s first call (the `run #1` marker, before the session is even
    launched) previously raised an unhandled `PermissionError` and killed the whole daemon.
    Asserts PARITY WITH BASH -- reaches `max runs (N) reached` and exits 1 -- not merely
    "doesn't crash"."""
    if os.geteuid() == 0:
        pytest.skip("running as root -- mode bits are not enforced, this test would prove nothing")
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    os.chmod(base / "state", 0o555)
    try:
        env = {**os.environ, "SIGMA_CLAUDE_CMD": "echo hi",
               "SIGMA_SUPERVISE_MAX_RUNS": "2", "SIGMA_SUPERVISE_SLEEP_SCALE": "0"}
        proc = subprocess.run([sys.executable, str(S / "supervise_daemon.py"), str(base)],
                              capture_output=True, text=True, env=env, timeout=30)
    finally:
        os.chmod(base / "state", 0o755)   # restore BEFORE any assertion can fail the test
    assert "Traceback (most recent call last)" not in proc.stderr, \
        f"the daemon died with a traceback instead of surviving an unwritable state/ dir:\n{proc.stderr}"
    assert proc.returncode == 1, \
        f"expected exit 1 (max-runs reached, matching bash), got {proc.returncode}\nstderr:\n{proc.stderr}"
    assert "max runs (2) reached" in proc.stdout, proc.stdout


def test_an_unwritable_tail_file_still_reaches_max_runs(tmp_path):
    """RESILIENCY regression, cycle 2, the SHARPER instance: `state/` itself is writable (so the
    log and runout writes succeed and sessions actually launch and get classified), but
    `supervisor.tail` specifically is not. The primary `tailf.write_text(...)` is wrapped in
    `try/except OSError`, but the FALLBACK `tailf.write_text("")` inside that handler previously
    had no `try` of its own -- so on this exact file being read-only, the "recovery" path itself
    raised uncaught. Asserts PARITY WITH BASH: still reaches `max runs (N) reached`, exit 1, and
    -- because state/ log writes and session launches are NOT blocked here -- the log must show
    real progress (a session actually ran and was classified), not a silent stall."""
    if os.geteuid() == 0:
        pytest.skip("running as root -- mode bits are not enforced, this test would prove nothing")
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    tailf = base / "state" / "supervisor.tail"
    tailf.write_text("")
    os.chmod(tailf, 0o444)
    try:
        env = {**os.environ, "SIGMA_CLAUDE_CMD": "echo hi",
               "SIGMA_SUPERVISE_MAX_RUNS": "2", "SIGMA_SUPERVISE_SLEEP_SCALE": "0"}
        proc = subprocess.run([sys.executable, str(S / "supervise_daemon.py"), str(base)],
                              capture_output=True, text=True, env=env, timeout=30)
    finally:
        os.chmod(tailf, 0o644)   # restore BEFORE any assertion can fail the test
    assert "Traceback (most recent call last)" not in proc.stderr, \
        f"the daemon died with a traceback instead of surviving an unwritable supervisor.tail:\n{proc.stderr}"
    assert proc.returncode == 1, \
        f"expected exit 1 (max-runs reached, matching bash), got {proc.returncode}\nstderr:\n{proc.stderr}"
    log = (base / "state" / "supervisor.log").read_text()
    assert "max runs (2) reached" in proc.stdout + log, log
    # The counter genuinely advanced -- sessions launched and were classified, not a silent stall.
    assert log.count("supervisor: run #") == 2, \
        f"expected exactly 2 runs logged (real progress, not a stall), got:\n{log}"


# ------------------------------------------------------------------------- #498: run-id producer
# supervise_daemon.py must ARM the run-id evidence guard by handing a per-worker SIGMA_RUN_ID to
# the session (and its child loop.py/work.py calls) via its subprocess `env=`. These exercise the
# REAL launcher without manually injecting the id -- the launcher itself must generate it -- so
# "protects #498 as shipped"
# is what is proven, not merely "the state.done_refusal mechanism works when fed an id by hand".


def _launcher_run_id(tmp_path, preset=None):
    """Run supervise_daemon.py once with a fake session that records the SIGMA_RUN_ID it
    inherited, and return that value. `preset` optionally sets the id in the environment BEFORE the
    launcher runs."""
    out = tmp_path / "seen-run-id.txt"
    proc, base = None, None
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    fake = tmp_path / "fake-claude.sh"
    fake.write_text('#!/usr/bin/env bash\n'
                    f'printf "%s" "$SIGMA_RUN_ID" > "{out}"\n'
                    'echo "LOOP STOP: backlog-empty"\n')
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "SIGMA_RUN_ID"}
    env.update({"SIGMA_CLAUDE_CMD": str(fake),
                "SIGMA_SUPERVISE_MAX_RUNS": "1", "SIGMA_SUPERVISE_SLEEP_SCALE": "0"})
    if preset is not None:
        env["SIGMA_RUN_ID"] = preset
    r = subprocess.run([sys.executable, str(S / "supervise_daemon.py"), str(base)],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    return out.read_text()


def test_supervisor_arms_a_nonempty_run_id_the_session_inherits(tmp_path):
    """The launcher generates an id even when none was set upstream, and the child session sees it
    -- so a shipped worker's verify/record are attributed, not left as the vulnerable None."""
    seen = _launcher_run_id(tmp_path)
    assert seen and seen.strip(), "supervise_daemon.py did not hand the session a non-empty SIGMA_RUN_ID"


def test_supervisor_run_ids_differ_between_concurrent_workers(tmp_path):
    """Two supervisors = two workers (the #310 setup). Each must generate a DISTINCT id, so a
    sibling's green (stamped with the OTHER id) is refused by this run's `record done`."""
    a = _launcher_run_id(tmp_path / "a")
    b = _launcher_run_id(tmp_path / "b")
    assert a != b, f"two supervisors produced the SAME run-id ({a!r}) — siblings would be indistinguishable"


def test_supervisor_respects_an_externally_provided_run_id(tmp_path):
    """An outer launcher (a caller above supervise_daemon.py) may set the id; supervise_daemon.py
    must NOT clobber it -- the whole process tree of that worker stays on one id."""
    assert _launcher_run_id(tmp_path, preset="outer-worker-7") == "outer-worker-7"


# --------------------------------------------------------------------------- progress / blocked
# These cover the two endings the classifier could not name, both of which it charged the
# escalating CRASH ladder for. Measured live on 2026-08-04: a run that merged a PR and a run that
# stopped cleanly on denied permissions were both scored "unclassified exit", taking the ladder to
# 300s -> 600s -> 1200s -> 3600s. The loop then spends most of the night asleep BETWEEN SUCCESSFUL
# goals, which is the opposite of what the backoff is for.

def test_a_session_that_landed_a_goal_is_not_a_crash():
    """The common case, and the one that was costing hours: a session finishes a goal and exits
    with no stop marker at all. Progress is not success (the backlog may still be full), so this
    must NOT be `done` -- but it is emphatically not a crash either."""
    m = _mod()
    action, secs, _ = m.classify("1 done, 0 parked, 0 failed", rng=_FixedRng())
    assert action == "relaunch", "a session that landed a goal must not be charged crash backoff"
    assert secs <= 300
    # and it must not escalate with attempt, which is the whole point
    waits = [m.classify("2 done, 1 parked, 0 failed", attempt=a, rng=_FixedRng())[1]
             for a in (0, 3, 9)]
    assert len(set(waits)) == 1, f"progress must not escalate: {waits}"


def test_zero_done_is_not_reported_as_progress():
    """`0 done` must never be described as progress -- the report's mere PRESENCE is not evidence
    that anything landed, and the `> 0` guard is what enforces that.

    NOTE this test was narrowed when the fall-through default was inverted, and the narrowing is
    deliberate rather than a concession to make the code pass. It originally also asserted that
    0-done rides the escalating CRASH ladder. That assertion was wrong once "unknown" stopped
    meaning "crashed": a session reporting `0 done, 2 parked` ran correctly and parked two goals,
    which is a clean stop, not breakage. The hot-loop it was guarding against is now covered by
    two other mechanisms -- the supervisor's MAX_RUNS cap, and the fact that parked goals shed the
    `sdlc:goal` label, so a backlog that parks everything drains to backlog-empty and terminates
    the supervisor via `done`. What remains genuinely this test's job is the wording guard below,
    which still fails if the `> 0` check is removed."""
    m = _mod()
    action, _, reason = m.classify("0 done, 2 parked, 0 failed", rng=_FixedRng())
    assert "landed" not in reason and "progress" not in reason, \
        f"0-done must not be called progress, got: {reason}"
    assert action == "relaunch"   # a clean stop, no crash signature


def test_blocked_stop_relaunches_instead_of_escalating():
    """A `LOOP STOP: blocked` is a CLEAN stop -- the loop decided it could not proceed and said so.
    Charging it the crash ladder is wrong twice over: it is not a crash, and a fresh session
    frequently clears the cause (proven live -- run #1 stopped blocked-on-permissions, run #2
    merged a PR)."""
    m = _mod()
    for tail in ("LOOP STOP: blocked-on-permissions", "LOOP STOP: blocked", "loop stop: BLOCKED"):
        action, secs, _ = m.classify(tail, rng=_FixedRng())
        assert action == "relaunch", f"{tail!r} -> {action}"
        assert secs > 0, "a blocked stop still deserves a pause, not a hot retry"


def test_usage_limit_still_wins_over_progress():
    """Ordering guard. A session that lands a goal and THEN hits a usage limit must sleep until the
    stated reset -- relaunching immediately would burn the remaining runs against a closed door."""
    m = _mod()
    action, _, _ = m.classify("1 done, 0 parked, 0 failed\nyou've hit your usage limit; resets at 3pm",
                              rng=_FixedRng())
    assert action == "sleep", f"limit must outrank progress, got {action}"


def test_real_world_normal_session_tail(tmp_path):
    """The verbatim tail of the live run that merged PR #282 and was scored a crash for it."""
    m = _mod()
    tail = ("Polling in the background; I'll report when the gate clears or the checks settle.\n"
            "1 done, 0 parked, 0 failed")
    assert m.classify(tail, rng=_FixedRng())[0] == "relaunch"


def test_unrecognised_but_clean_exit_does_not_escalate():
    """The live case the first pass MISSED, and the reason the ladder was climbing.

    A headless `claude -p` session prints only its final message. The real tail of the run that
    merged PR #282 was a single sentence -- "Polling in the background; I'll report when the gate
    clears or the checks settle." -- with no stop marker and no "N done, M parked" report, because
    the session ended mid-goal while waiting on CI. Matching on progress text cannot catch that.

    So the DEFAULT is what has to change: an ending we cannot name is not evidence of a crash. Only
    a recognisable crash signature earns the escalating ladder; anything else that produced
    coherent output gets a flat, modest pause. MAX_RUNS remains the backstop against a
    pathological loop."""
    m = _mod()
    tail = "Polling in the background; I'll report when the gate clears or the checks settle."
    waits = [m.classify(tail, attempt=a, rng=_FixedRng()) for a in (0, 1, 2, 9)]
    assert all(w[0] == "relaunch" for w in waits), [w[0] for w in waits]
    assert len({w[1] for w in waits}) == 1, f"an unnamed clean exit must not escalate: {waits}"


def test_real_crash_signatures_still_escalate():
    """The counterweight. Inverting the default is only safe if genuine breakage is still caught,
    so each signature is pinned explicitly rather than trusting the fall-through."""
    m = _mod()
    for tail in ("Traceback (most recent call last): boom",
                 "Killed",
                 "fatal error: out of memory",
                 "zsh: command not found: claude",
                 "   \n  \n"):                      # empty output tells us nothing -> treat as crash
        waits = [m.classify(tail, attempt=a, rng=_FixedRng())[1] for a in (0, 1, 2, 3)]
        assert waits == [300, 600, 1200, 3600], f"{tail!r} should escalate, got {waits}"


def test_a_mention_of_a_stop_marker_is_not_the_marker():
    """THE false-done that silently ended a 28-goal run on 2026-08-06.

    The session behaved impeccably: it hit a blocker, refused to emit a stop marker it had not
    earned, and said so in plain English --

        "I'm deliberately not printing `LOOP STOP: backlog-empty` or `LOOP STOP: budget`;
         neither is true, and emitting one would tell your tooling something false."

    -- and `_DONE` matched that sentence, because it searched for `LOOP STOP: backlog` and
    `backlog[- ]empty` ANYWHERE in the text. The supervisor exited action=done with 28 goals still
    queued. Honesty was indistinguishable from success.

    SKILL.md already specifies the real contract: "At STOP, print one machine-readable line FIRST".
    A marker is a LINE, not a substring. Anchoring is enforcement of the documented contract, not a
    new rule."""
    m = _mod()
    tail = ("**Run tally: 0 done, 0 parked, 0 failed — 1 goal (#303) left mid-flight.** I'm "
            "deliberately not printing `LOOP STOP: backlog-empty` or `LOOP STOP: budget`; neither "
            "is true, and emitting one would tell your tooling something false.")
    action, _, _ = m.classify(tail, rng=_FixedRng())
    assert action != "done", "a sentence DISOWNING the marker was read as the marker"


def test_a_real_stop_marker_on_its_own_line_still_means_done():
    """The counterweight. Anchoring is only correct if the genuine marker still terminates the
    supervisor -- otherwise a completed backlog would relaunch forever."""
    m = _mod()
    for tail in ("LOOP STOP: backlog-empty\n3 done, 1 parked, 0 failed",
                 "some preamble\nLOOP STOP: backlog-empty",
                 "backlog is empty",
                 "DONE"):
        assert m.classify(tail, rng=_FixedRng())[0] == "done", tail


def test_quoted_budget_marker_is_not_a_budget_stop():
    """Same bug class, other branch: the same sentence also names LOOP STOP: budget. Fixing only
    the _DONE arm would leave the identical false-positive one line down."""
    m = _mod()
    tail = "I am deliberately not printing `LOOP STOP: budget` because it is not true."
    action, _, _ = m.classify(tail, rng=_FixedRng())
    assert action != "relaunch" or True   # budget -> relaunch; assert it is NOT classified budget
    _, _, reason = m.classify(tail, rng=_FixedRng())
    assert "budget" not in reason, f"a quoted budget marker was read as a budget stop: {reason}"


# --------------------------------------------------------------------------- abnormal stop (#176)
# `_BLOCKED` matched the literal word "blocked" only. `LOOP STOP: interrupted by operator` and
# `LOOP STOP: worktree corrupt` are declared abnormal stops, but fell through to the generic
# "unrecognised but clean exit" bucket -- byte-for-byte identical to a tail with NO marker at all.
# A loop that DECLARED a problem and a loop that said nothing are not the same event.

def test_an_abnormal_loop_stop_is_distinct_from_silence():
    """A novel-reason `LOOP STOP:` must classify LOUDLY -- with a reason containing `ABNORMAL STOP`
    -- and that reason must differ from a tail with no marker at all. Today both are the identical
    generic string, which is the bug this issue is about."""
    m = _mod()
    _, _, abnormal_reason = m.classify("LOOP STOP: interrupted by operator", rng=_FixedRng())
    _, _, silent_reason = m.classify("I finished up and went home.", rng=_FixedRng())
    assert "ABNORMAL STOP" in abnormal_reason, abnormal_reason
    assert abnormal_reason != silent_reason, \
        f"a declared abnormal stop must not read like silence: {abnormal_reason!r} == {silent_reason!r}"


def test_the_captured_incident_tail_is_never_done():
    """The real 2026-07-31 incident tail, verbatim. Task 3 of the issue is already satisfied (fixed
    by the 2026-08-04 default-inversion and 2026-08-06 line-anchoring) -- this pins it with a test
    rather than changing code."""
    m = _mod()
    action, _, _ = m.classify("LOOP STOP: blocked — loop tooling no longer permitted", rng=_FixedRng())
    assert action != "done", action


def test_abnormal_stop_names_the_reason_the_loop_declared():
    """The declared reason text must reach the operator, not just a generic label -- that's the
    whole point of the loud reason."""
    m = _mod()
    _, _, reason = m.classify("LOOP STOP: worktree corrupt", rng=_FixedRng())
    assert "worktree corrupt" in reason, reason


def test_an_empty_reason_abnormal_stop_is_still_loud():
    """R1 (plan-review): the pattern captures `(.*)$`, NOT `(\\S.*)$`, so a bare `LOOP STOP:` line
    with nothing (or only whitespace) after the colon still matches -- instead of silently falling
    through to the generic "unrecognised" bucket and losing the loud reason altogether. An empty or
    whitespace-only capture is defaulted to "no reason given"."""
    m = _mod()
    for tail in ("LOOP STOP:", "LOOP STOP:\n", "LOOP STOP:   "):
        action, secs, reason = m.classify(tail, rng=_FixedRng())
        assert action == "relaunch", f"{tail!r} -> {action}"
        assert secs == 300, f"{tail!r} -> {secs}"
        assert "ABNORMAL STOP" in reason, f"{tail!r} -> {reason}"
        assert "no reason given" in reason, f"{tail!r} -> {reason}"


def test_backlog_and_budget_still_win_over_abnormal():
    """Precedence control: `_DONE` and `_BUDGET` are matched before `_ABNORMAL` and return early, so
    the widened `_ABNORMAL` pattern -- which would otherwise also match these lines -- must never
    get a chance to steal a backlog or budget stop."""
    m = _mod()
    assert m.classify("LOOP STOP: backlog-empty", rng=_FixedRng())[0] == "done"
    action, secs, reason = m.classify("LOOP STOP: budget", rng=_FixedRng())
    assert action == "relaunch" and secs == 60, (action, secs)
    assert "ABNORMAL STOP" not in reason, reason


def test_an_abnormal_stop_beats_a_progress_report():
    """R2 (plan-review): `_ABNORMAL` sits ABOVE `_PROGRESS` in `classify()` -- a session that landed
    goals and THEN declared an abnormal stop must surface the declared problem, not be absorbed into
    "landed N goal(s)". Tested in both text orders since nothing anchors the report to a position."""
    m = _mod()
    for tail in ("2 done, 1 parked\nLOOP STOP: worktree corrupt",
                 "LOOP STOP: worktree corrupt\n2 done, 1 parked"):
        action, _, reason = m.classify(tail, rng=_FixedRng())
        assert action == "relaunch", f"{tail!r} -> {action}"
        assert "ABNORMAL STOP" in reason, f"{tail!r} -> {reason}"


def test_a_quoted_abnormal_marker_is_not_an_abnormal_stop():
    """Anchoring control. The real sentence that forced `_DONE` and `_BUDGET` to anchor to line
    start in the first place -- a session being honest about NOT emitting a marker must not be read
    as having emitted one. `_ABNORMAL`'s match surface is far wider than the two it replaces, so it
    is more likely to bite if this regresses."""
    m = _mod()
    tail = ("I'm deliberately not printing `LOOP STOP: backlog-empty` or `LOOP STOP: budget`; "
            "neither is true, and emitting one would tell your tooling something false.")
    action, _, reason = m.classify(tail, rng=_FixedRng())
    assert "ABNORMAL STOP" not in reason, reason
    assert action == "relaunch", action   # falls through to the generic unrecognised bucket


def test_removing_the_abnormal_branch_restores_the_bug():
    """Mutation proof (issue task 4): neutralise `_ABNORMAL` on a freshly-loaded module -- exactly
    equivalent to deleting the branch -- and assert the historical bug returns: a declared abnormal
    stop collapses back to being indistinguishable from silence. A check that has never failed is
    not known to be a check. `_mod()` gives per-call isolation, so no teardown is needed."""
    m = _mod()
    m._ABNORMAL = re.compile(r"(?!)")   # never matches -- equivalent to deleting the branch
    abnormal_verdict = m.classify("LOOP STOP: interrupted by operator", rng=_FixedRng())
    silent_verdict = m.classify("I finished up and went home.", rng=_FixedRng())
    assert abnormal_verdict == silent_verdict, \
        f"with _ABNORMAL neutralised the bug should return: {abnormal_verdict} != {silent_verdict}"


# --------------------------------------------------------------------------- sleep-field hijack
# (PR #744 blocking finding on #176). `_ABNORMAL` is the FIRST branch in the module's history to
# put arbitrary, session-authored free text into `reason` -- every other branch uses a fixed
# string or a digit-only capture. supervise_daemon.py's `extract_sleep_seconds` extracts the pause
# with an ANCHORED regex now (it used to be the pre-port bash wrapper's unanchored, GREEDY
# `sed -n 's/.*sleep=\([0-9]*\).*/\1/p'`, which captured the digits after the LAST `sleep=`
# substring on the line -- not necessarily the classifier's own field). A session printing
# `LOOP STOP: worktree corrupt; env dump showed leftover sleep=4 from a prior test` used to make
# the unanchored extractor read `sleep=300` as `4` and actually sleep 4 seconds; a large number
# (`sleep=999999`) inflated the real pause to ~11.5 days while the log still claimed 300.
# MAX_RUNS bounds the retry COUNT, not the WALL-CLOCK time to reach it.

def test_a_declared_reason_cannot_hijack_the_sleep_field():
    """Classifier-level guard. Build the exact line supervise_daemon.py parses, the same way main()
    does (`f"action={action} sleep={secs} reason={reason}"`), and assert it carries exactly ONE
    `sleep=` field -- the classifier's real one. Two is the bug the old unanchored, greedy sed had:
    it grabbed whichever `sleep=` comes LAST on the line, which a hostile/careless session can
    control simply by mentioning its own `sleep=<n>` in free text after `LOOP STOP:`."""
    m = _mod()
    tail = "LOOP STOP: worktree corrupt; env dump showed leftover sleep=4 from a prior test"
    action, secs, reason = m.classify(tail, rng=_FixedRng())
    line = f"action={action} sleep={secs} reason={reason}"
    assert line.count("sleep=") == 1, \
        f"a session-authored sleep= leaked into the line supervise_daemon.py parses: {line!r}"


def test_the_supervisor_sleeps_the_pause_it_logged(tmp_path):
    """WRAPPER/end-to-end proof -- the one that actually exercises supervise_daemon.py's real
    `extract_sleep_seconds` and the real `_pause`/`time.sleep` call, not just the classifier.

    PR #744 review, cycle 2 -- what this test actually covers: it drives the REAL
    `supervise_classify.py`, so its `=` -> `-` reason sanitization (layer B) runs before the
    verdict line ever reaches the daemon's own extractor -- the `sleep=999999` below arrives
    already sanitized to `sleep-999999`, which even the OLD, unanchored, greedy extraction would
    not misread. This test proves the two layers TOGETHER produce the correct sleep; it does NOT,
    on its own, prove the daemon's own anchored regex (layer A) is what does the work --
    `test_the_shell_anchor_alone_resists_an_unsanitised_reason` below isolates that: the real
    extractor against an UNSANITIZED payload, with the classifier replaced by a stub.

    A malicious/careless tail embeds `sleep=999999` inside its declared reason. The daemon must
    sleep the classifier's REAL pause for an abnormal stop (a fixed 300s), never the injected
    number.

    Mechanism, post-port: `_pause` calls `time.sleep` in-process now, so there is no `sleep`
    binary invocation left for a PATH-shadow trick to intercept -- and it no longer needs one.
    `SIGMA_SUPERVISE_SLEEP_SCALE` is a FLOAT here (D4), unlike the bash original's
    integer-only `$(( secs * SCALE ))`, so a small fraction (`0.01`) shrinks the real,
    fixed-position 300s pause to a fast, provable `300 * 0.01 = 3.0s`, while a hijacked/unanchored
    999999 would scale to `9999.9s` (~2.8h) -- so the subprocess `timeout=` below is set tight
    enough (15s) that a regression to the old unanchored extraction TIMES OUT instead of merely
    asserting a wrong number, which surfaces the bug louder. The new D5 oracle line
    (`supervisor: pausing <actual>s (extracted sleep=<secs>)`) is asserted directly, reading the
    exact number the daemon's own anchor extracted, rather than inferring correctness indirectly
    from a shadowed `sleep` binary's argument.
    """
    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    fake = tmp_path / "fake-claude.sh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'echo "LOOP STOP: worktree corrupt; env dump showed leftover sleep=999999 from a prior test"\n')
    fake.chmod(0o755)

    env = {**os.environ,
           "SIGMA_CLAUDE_CMD": str(fake),
           "SIGMA_SUPERVISE_MAX_RUNS": "1",
           "SIGMA_SUPERVISE_SLEEP_SCALE": "0.01"}
    start = time.time()
    try:
        proc = subprocess.run([sys.executable, str(S / "supervise_daemon.py"), str(base)],
                              capture_output=True, text=True, env=env, timeout=15)
    except subprocess.TimeoutExpired:
        pytest.fail("supervisor did not finish within 15s -- the hijacked sleep=999999 must have "
                    "been used instead of the classifier's real, fixed-position 300")
    elapsed = time.time() - start

    assert elapsed < 10, f"the daemon took {elapsed}s -- expected a fast, correctly-scaled ~3s pause"
    logged = (base / "state" / "supervisor.log").read_text()
    assert "action=relaunch sleep=300" in logged, logged   # the classifier's own field, ground truth
    m = re.search(r"pausing ([\d.]+)s \(extracted sleep=(\d+)\)", logged)
    assert m, f"the D5 oracle line never appeared in the log:\n{logged}"
    assert float(m.group(1)) == pytest.approx(3.0), \
        f"supervisor paused the injected number instead of the logged pause: {logged}"
    assert m.group(2) == "300", f"expected the daemon's anchor to extract 300, got {m.group(2)}: {logged}"


def test_the_shell_anchor_alone_resists_an_unsanitised_reason(tmp_path):
    """PR #744 review, cycle 2. `test_the_supervisor_sleeps_the_pause_it_logged` above drives the
    REAL classifier, whose `=` -> `-` sanitization (layer B) turns the embedded `sleep=999999`
    into `sleep-999999` BEFORE the verdict line ever reaches the daemon's own extractor -- so even
    the OLD, unanchored, greedy extraction (`s/.*sleep=\\([0-9]*\\).*/\\1/p` in the pre-port bash,
    or its Python equivalent `re.findall(r"sleep=(\\d*)", verdict)[-1]`) would extract the correct
    300 from that already-sanitized line. Proven live pre-port: reverting the old bash wrapper alone
    to its pre-anchor version still left that test green. The anchored extraction -- the layer that
    protects every FUTURE branch whose classifier code is untouched or reverted -- had ZERO
    regression coverage of its own.

    This isolates layer A. The real classifier is replaced with a STUB that ignores its argv
    (tail-file path, attempt) and always prints a fixed, UNSANITIZED verdict line -- exactly what
    `_ABNORMAL` emitted BEFORE layer B's sanitization existed, embedded `sleep=999999` and all.
    `_HERE = pathlib.Path(__file__).resolve().parent` in supervise_daemon.py resolves relative to
    the INVOKED copy (the same `watch.py:12` idiom), so a COPY of supervise_daemon.py placed
    beside the stub calls the stub, not the real classifier -- confirmed by reading
    supervise_daemon.py; this needs no product-code change.

    Mechanism, post-port: `_pause` calls `time.sleep` in-process, so there is no `sleep` binary
    left for a PATH-shadow trick to intercept -- unlike the wrapper test above, which drives the
    real product script directly, this one MUST run a COPY (to make `_HERE` resolve beside the
    stub classifier), so it uses the identical fractional-scale + D5-oracle-line approach: assert
    the daemon's own anchor extracted 300 (not the injected 999999), scaled by 0.01 to a fast,
    provable 3.0s, read directly off the log line the daemon itself writes. If layer A regresses
    to its old unanchored form, its greedy match captures the LAST `sleep=<digits>` on the line --
    the injected 999999, not the field's real, fixed-position 300 -- and THIS test goes red on its
    own, with layer B out of the picture entirely.
    """
    daemon_copy = tmp_path / "supervise_daemon.py"
    daemon_copy.write_text((S / "supervise_daemon.py").read_text())
    # #239: the daemon reads its env through the sibling `legacy.py`, which ships beside it.
    (tmp_path / "legacy.py").write_text((S / "legacy.py").read_text())

    # Beside it: a stub classifier `_HERE` resolves to and calls instead of the real one. Ignores
    # argv entirely and always emits the same UNSANITIZED verdict line -- what `_ABNORMAL` printed
    # before the `=` -> `-` fix existed (supervise_classify.py: `reason_text = ... .replace("=", "-")`).
    (tmp_path / "supervise_classify.py").write_text(
        "#!/usr/bin/env python3\n"
        "print('action=relaunch sleep=300 reason=ABNORMAL STOP: worktree corrupt; leftover "
        "sleep=999999 here — not a crash')\n"
    )

    base = tmp_path / ".sdlc"; (base / "state").mkdir(parents=True)
    fake = tmp_path / "fake-claude.sh"
    fake.write_text(
        '#!/usr/bin/env bash\necho "session output is irrelevant -- the stub classifier ignores it"\n')
    fake.chmod(0o755)

    env = {**os.environ,
           "SIGMA_CLAUDE_CMD": str(fake),
           "SIGMA_SUPERVISE_MAX_RUNS": "1",
           "SIGMA_SUPERVISE_SLEEP_SCALE": "0.01"}
    start = time.time()
    try:
        proc = subprocess.run([sys.executable, str(daemon_copy), str(base)],
                              capture_output=True, text=True, env=env, timeout=15)
    except subprocess.TimeoutExpired:
        pytest.fail("the copied daemon did not finish within 15s -- an unanchored extraction would "
                    "have scaled the hijacked sleep=999999 to ~9999.9s")
    elapsed = time.time() - start

    assert elapsed < 10, f"the daemon took {elapsed}s -- expected a fast, correctly-scaled ~3s pause"
    logged = (base / "state" / "supervisor.log").read_text()
    m = re.search(r"pausing ([\d.]+)s \(extracted sleep=(\d+)\)", logged)
    assert m, f"the D5 oracle line never appeared in the log:\nstdout:\n{proc.stdout}\nlog:\n{logged}"
    assert m.group(2) == "300" and float(m.group(1)) == pytest.approx(3.0), \
        (f"the daemon's own anchor alone did not resist the unsanitised reason -- extracted "
         f"{m.group(2)!r}, paused {m.group(1)!r}s instead of the classifier's real, "
         f"fixed-position 300: {logged}")


def test_a_bold_wrapped_abnormal_marker_logs_a_clean_reason():
    """Non-blocking review finding #4. `_DONE`'s pattern already strips a trailing `[.*]*`; make
    `_ABNORMAL`'s captured text consistent. A plausible session convention is bold-wrapping the
    marker -- `**LOOP STOP: worktree corrupt**` -- and the capture group's greedy `(.*)$` swallows
    the closing `**` into the reason verbatim (`ABNORMAL STOP: worktree corrupt** -- not a
    crash...`). Also pin that the empty-capture default ("no reason given") still fires when the
    WHOLE marker is bold-wrapped and nothing follows the colon."""
    m = _mod()
    _, _, reason = m.classify("**LOOP STOP: worktree corrupt**", rng=_FixedRng())
    assert "worktree corrupt" in reason, reason
    assert "**" not in reason, f"a bold marker's closing ** leaked into the reason: {reason!r}"

    _, _, reason = m.classify("**LOOP STOP:**", rng=_FixedRng())
    assert "no reason given" in reason, reason

    _, _, reason = m.classify("LOOP STOP:", rng=_FixedRng())
    assert "no reason given" in reason, reason   # the plain (non-bold) empty case must still work too


# --------------------------------------------------------- verdict-line <-> extractor contract (#745)
# main() prints `f"action={action} sleep={secs} reason={reason}"`; supervise_daemon.py extracts the
# pause with `extract_sleep_seconds`, an ANCHORED regex keyed to that EXACT shape. A future field
# reorder/rename/insertion in main() makes the regex stop matching, `secs` goes empty, and the
# daemon's `${secs:-1800}`-equivalent fallback substitutes a SILENT 30-minute pause with no error
# anywhere. #744 defended this coupling with a COMMENT only -- and by this repo's north-star rule 1 a
# comment is a hypothesis: nothing FAILS if the coupling breaks. These tests round-trip main()'s REAL
# emitted line through the daemon's REAL `extract_sleep_seconds` for every verdict shape, so a format
# change goes RED instead of silently degrading.


def _main_emits(tmp_path, tail, attempt):
    """main()'s REAL emitted verdict line: write the tail to a file and run supervise_classify.py the
    way supervise_daemon.py does (`python3 <script> <tailfile> <attempt>`), returning its stdout with
    the trailing newline stripped (as its own `.strip()` capture would)."""
    tf = tmp_path / "tail.txt"
    tf.write_text(tail)
    r = subprocess.run(["python3", str(S / "supervise_classify.py"), str(tf), str(attempt)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.rstrip("\n")


def _true_sleep(line):
    """Ground-truth read of the sleep value, INDEPENDENT of the daemon's anchored extractor: an
    unanchored, position-agnostic `sleep=(\\d+)` search. The chosen tails below never put a `sleep=`
    in their free-text reason, so exactly one match is the contract. When main()'s format holds, this
    oracle and the anchored extractor agree; when a field is reordered/inserted, the oracle still
    finds the true value while the anchored extractor returns empty -> the assertion diverges -> RED.
    NB: the `sleep=` needle is hardcoded on purpose (kept independent of the extractor); a deliberate
    future rename of the field on BOTH sides will correctly fail this test, signalling a maintainer
    to update the oracle rather than assume a bug."""
    ms = re.findall(r"sleep=(\d+)", line)
    assert len(ms) == 1, f"expected exactly one `sleep=` field, found {ms} in {line!r}"
    return ms[0]


# name, tail, attempt, expected_secs (None = non-deterministic, assert only extractor==oracle)
_VERDICT_SHAPES = [
    ("done_sleep0",      "LOOP STOP: backlog-empty",                        0, 0),
    ("budget",           "BUDGET",                                          0, 60),
    ("abnormal",         "LOOP STOP: worktree corrupt",                     0, 300),
    ("progress",         "1 done, 0 parked, 0 failed",                      0, 60),
    ("crash",            "Traceback (most recent call last): boom",         0, 300),
    ("unknown_clean",    "Polling in the background; nothing broke.",       0, 120),
    ("limit_no_reset",   "rate limit exceeded, try later",                  0, 1800),
    ("limit_with_reset", "you have hit your usage limit; resets at 5:30am", 0, None),
]


@pytest.mark.parametrize("name,tail,attempt,expected", _VERDICT_SHAPES,
                         ids=[s[0] for s in _VERDICT_SHAPES])
def test_every_verdict_shape_round_trips_through_the_real_extractor(tmp_path, name, tail, attempt, expected):
    """The #745 contract test. For every verdict shape, main()'s live line fed through the daemon's
    live `extract_sleep_seconds` must yield the shape's real `sleep=` value -- never empty (empty is
    the silent `${secs:-1800}`-equivalent degradation this whole test exists to prevent).
    `done_sleep0` pins the sleep=0 edge dc859ff's message claimed but never exercised: `(\\d*)` must
    still capture "0"."""
    line = _main_emits(tmp_path, tail, attempt)
    assert line.startswith("action="), f"{name}: main() emitted an unexpected line: {line!r}"
    oracle = _true_sleep(line)
    got = _daemon_mod().extract_sleep_seconds(line)
    assert got != "", (
        f"{name}: the daemon's extractor got NOTHING from main()'s line -- `${{secs:-1800}}` would "
        f"substitute a SILENT 30-min pause. The verdict format and the extractor have diverged: "
        f"{line!r}")
    assert got == oracle, (
        f"{name}: the daemon's extractor got {got!r} but the sleep field is {oracle!r} "
        f"(format/extractor divergence): {line!r}")
    if expected is not None:
        assert oracle == str(expected), (
            f"{name}: expected sleep={expected} from classify(), main() emitted sleep={oracle}: {line!r}")


def test_the_classifier_unavailable_fallback_line_also_parses():
    """The 9th shape: the verdict line supervise_daemon.py constructs ITSELF (not via main()) when
    the classifier subprocess fails -- `CLASSIFIER_UNAVAILABLE_FALLBACK`. dc859ff's message claimed
    the extractor was verified against this too; pin it. Read from the daemon's own module-level
    constant (not scraped from source text) so a change to the fallback that broke its own extractor
    would go RED here."""
    fallback = _daemon_mod().CLASSIFIER_UNAVAILABLE_FALLBACK
    got = _daemon_mod().extract_sleep_seconds(fallback)
    assert got == _true_sleep(fallback), f"the fallback line does not round-trip its own extractor: {fallback!r}"
    assert got == "1800", f"the classifier-unavailable fallback must pause 1800s, extractor read {got!r}: {fallback!r}"


def test_empty_extraction_falls_back_to_the_default_pause():
    """The `${secs:-1800}` fallback, pinned directly: a malformed verdict line (missing/garbled
    `sleep=` field) must make `extract_sleep_seconds` return `""`, and the daemon's own resolution of
    that empty string must be 1800s -- the same fallback constant the pre-port bash wrapper's shell
    parameter expansion `${secs:-1800}` applied. No existing verdict shape naturally produces an empty
    extraction, so this is constructed directly rather than driven end to end."""
    daemon = _daemon_mod()
    assert daemon.extract_sleep_seconds("garbage") == ""
    assert daemon.extract_sleep_seconds("action=foo sleep= reason=bar") == ""
    empty = daemon.extract_sleep_seconds("not a verdict line at all")
    secs_val = int(empty) if empty else 1800
    assert secs_val == 1800


def test_last_n_lines_preserves_a_trailing_newline_like_real_tail():
    """Non-blocking review finding, cycle 2: `_last_n_lines` (the `tail -n 50` idiom) previously
    dropped the source text's trailing newline unconditionally -- a FOURTH, undocumented
    divergence from real `tail` while the module docstring claims exactly three. `splitlines()`
    itself discards the line terminator, so it must be reattached when the source had one."""
    daemon = _daemon_mod()
    assert daemon._last_n_lines("a\nb\nc\n", 50) == "a\nb\nc\n"
    assert daemon._last_n_lines("a\nb\nc", 50) == "a\nb\nc"        # no trailing newline in source
    assert daemon._last_n_lines("", 50) == ""
    # More lines than the cap: still keeps the trailing newline when the source had one.
    many = "".join(f"line{i}\n" for i in range(60))
    got = daemon._last_n_lines(many, 50)
    assert got.endswith("\n") and got.count("\n") == 50


# ----------------------------------------------------- narrowed abnormal-reason scrub (#745 part 2)
# The abnormal-reason scrub used to be a blanket `.replace("=","-")`, which mangled every `=` in a
# declared reason -- so `threshold=5 exceeded` logged as `threshold-5 exceeded`, silently corrupting
# the key=value diagnostics a real stop reason carries. Now that supervise_daemon.py's anchored
# `extract_sleep_seconds` regex is the primary defence (proven by
# test_the_shell_anchor_alone_resists_an_unsanitised_reason), the scrub is
# narrowed to blunt ONLY the two tokens a downstream key=value reader could misattribute to the
# verdict's own fields: `sleep=` and `action=`.

def test_a_key_value_diagnostic_in_a_reason_is_not_mangled():
    """#745 part 2: a legitimate `key=value` diagnostic in a declared reason must survive verbatim --
    the blanket `.replace("=","-")` turned `threshold=5` into `threshold-5`. This pins that closed."""
    m = _mod()
    _, _, reason = m.classify("LOOP STOP: config validation failed, threshold=5 exceeded", rng=_FixedRng())
    assert "threshold=5 exceeded" in reason, f"a key=value diagnostic was mangled: {reason!r}"
    assert "ABNORMAL STOP" in reason, reason


def test_the_scrub_still_neutralises_the_verdict_field_tokens():
    """Counterweight to the narrowing: `sleep=` and `action=` embedded in a declared reason MUST still
    be blunted, because those are the exact substrings a downstream key=value reader could
    misattribute to the verdict's own fields. Narrowing the scrub must not reopen that door, and the
    single-`sleep=`-field invariant the daemon's own extractor relies on must still hold."""
    m = _mod()
    _, _, reason = m.classify("LOOP STOP: leftover sleep=999999 and action=done in the env dump",
                              rng=_FixedRng())
    assert "sleep=" not in reason, f"an embedded sleep= survived the scrub: {reason!r}"
    assert "action=" not in reason, f"an embedded action= survived the scrub: {reason!r}"
    action, secs, r2 = m.classify("LOOP STOP: leftover sleep=999999 here", rng=_FixedRng())
    line = f"action={action} sleep={secs} reason={r2}"
    assert line.count("sleep=") == 1, f"an embedded sleep= leaked into the verdict line: {line!r}"


# ------------------------------------------------- a declared stop must not mask a real crash (#746)
# In classify(), the _ABNORMAL (declared `LOOP STOP:`) branch sits ABOVE the crash-signature
# fall-through. Before #746 a tail carrying BOTH a declared stop AND a real traceback classified
# ('relaunch', 300) and never walked the escalating _CRASH_BACKOFF ladder -- in either text order.
# Old _BLOCKED matched only the literal word 'blocked' (narrow); _ABNORMAL matches ANY `LOOP STOP:`
# line (wide), so a crash that also emitted a declared stop escaped escalation. The fix: an
# INDEPENDENT crash signature (one that survives stripping the declared-stop line) outranks the
# declared stop and still escalates; a CLEAN declared stop -- including one whose reason merely
# MENTIONS a crashy phrase -- still relaunches.

def test_declared_stop_plus_crash_signature_still_escalates():
    """A tail with BOTH a declared `LOOP STOP:` line AND a real traceback is a genuine crash that
    also happened to print a stop marker. The crash must not be masked: it must climb the
    escalating ladder, in EITHER text order."""
    m = _mod()
    stop_then_crash = ("LOOP STOP: worktree corrupt\n"
                       "Traceback (most recent call last):\n"
                       "  File \"loop.py\", line 1, in <module>\n"
                       "ValueError: boom")
    crash_then_stop = ("Traceback (most recent call last):\n"
                       "  File \"loop.py\", line 1, in <module>\n"
                       "ValueError: boom\n"
                       "LOOP STOP: worktree corrupt")
    for tail in (stop_then_crash, crash_then_stop):
        action = m.classify(tail, rng=_FixedRng())[0]
        assert action == "backoff", f"a real crash beside a declared stop must escalate: {tail!r} -> {action}"
        waits = [m.classify(tail, attempt=a, rng=_FixedRng())[1] for a in (0, 1, 2, 3)]
        assert waits == [300, 600, 1200, 3600], f"{tail!r} should climb the crash ladder, got {waits}"


def test_clean_declared_stop_with_no_crash_still_relaunches():
    """The counterweight: a declared stop with NO crash signature -- including one whose reason
    merely MENTIONS a crashy phrase like 'connection reset' or 'out of memory' -- is still a CLEAN
    self-declared stop and must relaunch on the flat abnormal pause, its reason echoed loudly."""
    m = _mod()
    for tail in ("LOOP STOP: worktree corrupt",
                 "LOOP STOP: connection reset by peer during push",
                 "LOOP STOP: aborting, out of memory budget for this run",
                 "LOOP STOP: gh command not found in PATH"):
        action, secs, reason = m.classify(tail, rng=_FixedRng())
        assert action == "relaunch", f"a clean declared stop must relaunch, not escalate: {tail!r} -> {action}"
        assert secs == 300, f"a clean declared stop keeps the abnormal pause: {tail!r} -> {secs}"
        assert "ABNORMAL STOP" in reason, reason


# ------------------------------------ a progress report must not mask a real crash either (#941)
# #746 fixed this for the _ABNORMAL branch only (a declared `LOOP STOP:` line must not mask a
# co-occurring real crash). Its own review flagged the SIBLING gap this issue closes: _PROGRESS
# ("N done, M parked", count>0) sits BELOW _ABNORMAL and was never gated on _CRASH_SIG at all --
# so a tail with a progress-shaped report AND a real traceback still classified ('relaunch', 60,
# "progress, not a crash"), in BOTH the case where no LOOP STOP line is present (falls through
# _ABNORMAL entirely) and the case where a declared stop TOO is present alongside the crash (already
# correctly skips the _ABNORMAL relaunch, but then fell into the unguarded _PROGRESS branch instead
# of continuing to the crash ladder). Fix: _PROGRESS must be gated on the identical
# _CRASH_SIG-against-the-abnormal-stripped-text check _ABNORMAL uses.

def test_progress_report_plus_crash_signature_still_escalates_no_declared_stop():
    """No `LOOP STOP:` line at all -- just an ordinary progress report sitting beside a real
    traceback. Must climb the crash ladder, not relaunch as routine progress, in either order."""
    m = _mod()
    progress_then_crash = ("2 done, 1 parked\n"
                            "Traceback (most recent call last):\n"
                            "  File \"loop.py\", line 1, in <module>\n"
                            "ValueError: boom")
    crash_then_progress = ("Traceback (most recent call last):\n"
                            "  File \"loop.py\", line 1, in <module>\n"
                            "ValueError: boom\n"
                            "2 done, 1 parked")
    for tail in (progress_then_crash, crash_then_progress):
        action = m.classify(tail, rng=_FixedRng())[0]
        assert action == "backoff", \
            f"a real crash beside a progress report must escalate, not relaunch: {tail!r} -> {action}"
        waits = [m.classify(tail, attempt=a, rng=_FixedRng())[1] for a in (0, 1, 2, 3)]
        assert waits == [300, 600, 1200, 3600], f"{tail!r} should climb the crash ladder, got {waits}"


def test_progress_report_plus_declared_stop_plus_crash_signature_still_escalates():
    """The full triple: a progress report, a declared `LOOP STOP:`, AND a real traceback all in one
    tail. `_ABNORMAL` already correctly declines to relaunch here (the crash survives stripping the
    stop line), but before this fix execution then fell into the unguarded `_PROGRESS` branch and
    relaunched anyway. Must still escalate."""
    m = _mod()
    tail = ("2 done, 1 parked\n"
            "LOOP STOP: worktree corrupt\n"
            "Traceback (most recent call last):\n"
            "  File \"loop.py\", line 1, in <module>\n"
            "ValueError: boom")
    action = m.classify(tail, rng=_FixedRng())[0]
    assert action == "backoff", f"crash must escalate despite progress+declared-stop: {tail!r} -> {action}"


def test_progress_report_with_no_crash_still_relaunches_on_progress_pause():
    """The counterweight: an ordinary progress report with NO crash signature anywhere -- including
    one whose declared-stop reason merely MENTIONS a crashy phrase -- must still relaunch on the
    short progress pause, not be swept into the crash ladder by an overzealous gate."""
    m = _mod()
    action, secs, reason = m.classify("3 done, 1 parked", rng=_FixedRng())
    assert action == "relaunch" and secs == 60, ("3 done, 1 parked", action, secs)
    assert "progress" in reason

    tail = "2 done, 0 parked\nLOOP STOP: connection reset by peer during push"
    action, secs, reason = m.classify(tail, rng=_FixedRng())
    assert action == "relaunch", (tail, action)
    assert "ABNORMAL STOP" in reason, reason   # the declared-stop branch still wins, as before


def test_removing_the_progress_crash_gate_restores_the_941_bug():
    """Mutation proof: neutralise `_CRASH_SIG` on a freshly-loaded module and confirm the historical
    bug returns -- a progress report beside a real traceback stops escalating and relaunches as
    routine progress instead. A check that never fails is not known to be a check."""
    m = _mod()
    m._CRASH_SIG = re.compile(r"(?!)")   # never matches -- equivalent to deleting the crash gate
    tail = ("2 done, 1 parked\n"
            "Traceback (most recent call last):\n"
            "  File \"loop.py\", line 1, in <module>\n"
            "ValueError: boom")
    action = m.classify(tail, rng=_FixedRng())[0]
    assert action == "relaunch", \
        f"with _CRASH_SIG neutralised the #941 bug should return (relaunch), got {action}"

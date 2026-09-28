"""Local-only action log — write side (skills/agrim-loop/scripts/actionlog.py). Mirrors
tests/test_ledger.py's own shape: schema/vocabulary enforcement, the fail-open contract, scrubbing,
and — the acceptance criteria's own required proofs (plan section 5, #463) — a forgery-prevention
test (the agent-facing CLI path can never write an INTERNAL-only kind) and a REAL two-process
concurrency test (matching this repo's own #387 "real processes, not mocked" bar).

The byte-identical-ledger proof and the no-ledger-import pin both live in tests/test_work.py
instead of here: that file already has the rich merge/post_review gate-simulation fixtures
(_runner/_view/_rights/_review/_protected) the byte-identical proof needs to exercise the real
work.py call sites end to end, and duplicating them here would be exactly the
hardened-sibling-divergence pattern this session's own retrospectives flag."""
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


actionlog = _mod("actionlog")

ON = {"action_log": {"enabled": True}}
OFF = {"action_log": {"enabled": False}}


def _sdlc(tmp_path, config):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config))
    (d / "state" / "STATE.md").write_text(
        "# Loop State\niteration: 0\nrun_iteration: 0\nlast_run: none\n")
    return str(d)


# --------------------------------------------------------------------- schema + vocabulary


def test_actionlog_append_writes_expected_schema(tmp_path):
    d = _sdlc(tmp_path, ON)
    entry = actionlog.append(d, "158", "note", "agent", thread="slice-a1", text="hello")
    assert entry["goal"] == "158"
    assert entry["thread"] == "slice-a1"
    assert entry["actor"] == "agent"
    assert entry["kind"] == "note"
    assert entry["text"] == "hello"
    assert set(entry) == {"ts", "goal", "thread", "actor", "kind", "text"}
    assert isinstance(entry["ts"], str) and entry["ts"].endswith("Z")
    assert "." in entry["ts"], "millisecond precision, not ledger._stamp()'s whole-second shape"
    on_disk = json.loads(actionlog.log_path(d, "158").read_text().strip())
    assert on_disk == entry


def test_actionlog_default_thread_is_main(tmp_path):
    d = _sdlc(tmp_path, ON)
    entry = actionlog.append(d, "158", "note", "agent", text="hi")
    assert entry["thread"] == "main"


def test_actionlog_append_rejects_unknown_kind(tmp_path):
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError, match="unknown action-log kind"):
        actionlog.append(d, "158", "banana", "agent")


def test_actionlog_append_rejects_unknown_field_for_kind(tmp_path):
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError, match="unknown flag"):
        actionlog.append(d, "158", "note", "agent", bogus="x")


def test_actionlog_append_rejects_a_raw_newline_in_any_flag_value(tmp_path):
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError, match="newline"):
        actionlog.append(d, "158", "note", "agent", text="line one\nline two")


def test_actionlog_append_rejects_a_raw_newline_in_thread(tmp_path):
    """`thread` is a flag value too (an agent-supplied `--thread`), not exempt from the newline
    guard just because it's a named parameter rather than a **fields entry."""
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError, match="newline"):
        actionlog.append(d, "158", "note", "agent", thread="a\nb", text="x")


def test_actionlog_append_rejects_out_of_vocabulary_file_op(tmp_path):
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError, match="unknown op"):
        actionlog.append(d, "158", "file", "agent", path="x.py", op="opened")


def test_actionlog_append_accepts_every_real_file_op(tmp_path):
    d = _sdlc(tmp_path, ON)
    for op in actionlog.FILE_OPS:
        entry = actionlog.append(d, "158", "file", "agent", path="x.py", op=op)
        assert entry["op"] == op


def test_actionlog_append_rejects_out_of_vocabulary_gate(tmp_path):
    """INTERNAL_KINDS's own `gate.gate` vocabulary check, exercised directly (not via the CLI,
    since `gate` is never CLI-reachable at all) — proves append() itself enforces it, not just the
    kind_allowlist split."""
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError, match="unknown gate"):
        actionlog.append(d, "158", "gate", "loop", gate="bogus", verdict="pass")


def test_actionlog_append_accepts_every_real_internal_gate(tmp_path):
    d = _sdlc(tmp_path, ON)
    for gate in actionlog.INTERNAL_GATE_KINDS:
        entry = actionlog.append(d, "158", "gate", "loop", gate=gate, verdict="pass")
        assert entry["gate"] == gate


def test_actionlog_internal_kinds_and_agent_kinds_do_not_overlap():
    assert set(actionlog.INTERNAL_KINDS) & set(actionlog.AGENT_KINDS) == set()
    assert set(actionlog.ALL_KINDS) == set(actionlog.INTERNAL_KINDS) | set(actionlog.AGENT_KINDS)


def test_actionlog_released_kind_accepts_reason(tmp_path):
    """#841: loop.py's `release` verb writes this INTERNAL kind — `reason` mirrors the field the
    CLI-facing `release` verb itself takes, the same shape `recorded`'s own `detail` field has."""
    d = _sdlc(tmp_path, ON)
    entry = actionlog.append(d, "158", "released", "loop", reason="skipped this run")
    assert entry["kind"] == "released" and entry["reason"] == "skipped this run"


def test_actionlog_agent_reclaimed_kind_accepts_thread_and_pid(tmp_path):
    """#2015: `loop.py::agent_reclaim()` writes this INTERNAL kind whenever it confirms a thread's
    registered pid genuinely dead and removes its marker — the durable audit trail a takeover
    never had before this fix. `thread` is the envelope field every kind already carries (see
    `append()`); `pid` is the one new field, the specific dead pid that was cleared."""
    d = _sdlc(tmp_path, ON)
    entry = actionlog.append(d, "158", "agent_reclaimed", "loop", thread="slice-a1", pid=999999)
    assert entry["kind"] == "agent_reclaimed"
    assert entry["thread"] == "slice-a1"
    assert entry["pid"] == "999999"


# --------------------------------------------------------------------- forgery prevention (CLI)


def test_actionlog_agent_kinds_cannot_write_an_internal_kind(tmp_path, capsys):
    """The forgery-prevention proof (mirrors `_EMIT_KINDS`'s own "an agent must never fabricate a
    Class-1 event" test class): the agent-facing path — `loop.py log`, the actual CLI verb —
    called with kind="claimed" (an INTERNAL_KINDS-only kind) is refused. `append()` ALONE would
    happily accept "claimed" (it's in ALL_KINDS); the refusal has to come from the CLI's own
    kind_allowlist check, which is what this proves end to end."""
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "claimed"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "claimed" in err
    assert "file" in err and "note" in err   # names the actually-allowed kinds
    assert actionlog.read_goal(d, "158") == []


@pytest.mark.parametrize("kind", actionlog.INTERNAL_KINDS)
def test_actionlog_cli_rejects_every_internal_kind(tmp_path, kind):
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", kind])
    assert rc == 2
    assert actionlog.read_goal(d, "158") == []


def test_actionlog_agent_kinds_can_write_a_real_agent_kind(tmp_path):
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "note", "--text", "hello"])
    assert rc == 0
    entries = actionlog.read_goal(d, "158")
    assert len(entries) == 1
    assert entries[0]["kind"] == "note" and entries[0]["text"] == "hello"
    assert entries[0]["actor"] == "agent"


def test_actionlog_cli_pops_thread_before_field_validation(tmp_path):
    """`--thread` is an entry-level label, not a per-kind field — it must never be rejected as an
    "unknown flag" for a kind whose own field whitelist doesn't mention it."""
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "file", "--thread", "slice-a1",
                  "--path", "src/bar.py", "--op", "edit"])
    assert rc == 0
    entries = actionlog.read_goal(d, "158")
    assert entries[0]["thread"] == "slice-a1"


def test_agent_done_records_the_phase_its_dispatch_named(tmp_path):
    """#1687. `agent_dispatch` and `agent_done` are a MATCHED PAIR — SKILL.md logs the phase on
    both — and the CLI rejected `--phase` on the second half with "unknown flag(s) phase for kind
    'agent_done'". A goal running seven phase agents therefore wrote seven `agent_done` rows that
    nothing could tell apart: the phase path carries no `--thread` (only slices do)."""
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    assert lp.main(["loop.py", "log", d, "158", "agent_dispatch",
                    "--role", "phase", "--phase", "implement", "--model", "implement"]) == 0
    assert lp.main(["loop.py", "log", d, "158", "agent_done",
                    "--role", "phase", "--phase", "implement", "--result", "merged"]) == 0
    dispatched, done = actionlog.read_goal(d, "158")
    assert dispatched["phase"] == done["phase"] == "implement"
    assert done["result"] == "merged"


def test_agent_done_still_refuses_a_field_that_is_not_its_own(tmp_path, capsys):
    """Widening the whitelist by one must not open it: `agent_done` has no `signal`, and the
    control is the point — a whitelist never seen refuse anything proves nothing."""
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "agent_done", "--role", "phase", "--signal", "x"])
    assert rc == 2 and "signal" in capsys.readouterr().err
    assert actionlog.read_goal(d, "158") == []


def test_agent_dispatch_refuses_role_phase_with_no_model(tmp_path, capsys):
    """#2514. `phase` and `slice` are the two roles whose tier is already resolved BEFORE
    dispatch (`model_choice` at pick time) -- a dispatch logged with neither must be refused, the
    same posture as an unknown field, naming both `model` and the phase it was for."""
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "agent_dispatch",
                  "--role", "phase", "--phase", "research"])
    err = capsys.readouterr().err
    assert rc == 2 and "model" in err and "research" in err
    assert actionlog.read_goal(d, "158") == []


def test_agent_dispatch_refuses_missing_model_when_action_log_is_off(tmp_path, capsys):
    """The shipped default is OFF; disabling storage must not disable dispatch validation."""
    d = _sdlc(tmp_path, OFF)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "agent_dispatch",
                  "--role", "phase", "--phase", "review"])
    assert rc == 2 and "model" in capsys.readouterr().err
    assert actionlog.read_goal(d, "158") == []



def test_agent_dispatch_refuses_role_slice_with_no_model(tmp_path, capsys):
    """Same shape as above for `--role slice` -- the other role `running.md` resolves a tier for
    before dispatch (the goal ceiling or a `resolve-step` downgrade)."""
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "agent_dispatch",
                  "--role", "slice", "--phase", "implement"])
    err = capsys.readouterr().err
    assert rc == 2 and "model" in err
    assert actionlog.read_goal(d, "158") == []


def test_agent_dispatch_accepts_role_phase_with_model(tmp_path):
    """The positive counterpart: `--model sonnet` on a `phase` dispatch is accepted and the tier
    is recorded on that row."""
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "agent_dispatch",
                  "--role", "phase", "--phase", "research", "--model", "sonnet"])
    assert rc == 0
    entries = actionlog.read_goal(d, "158")
    assert entries[0]["model"] == "sonnet"


def test_agent_dispatch_refuses_role_goal_slot_with_no_model(tmp_path, capsys):
    """#2544 extends #2514's rule to a third role whose tier is resolved BEFORE dispatch: the SAME
    `_next()`/`next_batch()` call that returns the goal already resolved it (pick-time
    `model_choice`; `references/picking.md` step 1a reads it back off that call's own stderr,
    falling back to a manual resolve when the automatic line is missing) -- so a tier is always
    available and a goal-slot dispatch naming none is a bug, not a genuine absence."""
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "agent_dispatch", "--role", "goal-slot"])
    assert rc == 2 and "model" in capsys.readouterr().err
    assert actionlog.read_goal(d, "158") == []


def test_agent_dispatch_accepts_role_goal_slot_with_model(tmp_path):
    """Positive counterpart: `--model sonnet` on a `goal-slot` dispatch is accepted and recorded."""
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "agent_dispatch", "--role", "goal-slot",
                  "--model", "sonnet"])
    assert rc == 0
    entries = actionlog.read_goal(d, "158")
    assert entries[0]["model"] == "sonnet"


def test_agent_dispatch_refuses_role_goal_slot_with_no_model_even_when_action_log_is_off(
        tmp_path, capsys):
    """Mirrors `origin/main`'s own `test_agent_dispatch_refuses_missing_model_when_action_log_is_
    off` (#2521 P6 review, commit 79095309 -- not yet rebased into this branch; see this plan's
    "feature/core vs. main divergence" section). Goal-slot's required-model check runs BEFORE the
    `enabled()` gate, deliberately, so disabling local action-log STORAGE can never silently
    disable dispatch VALIDATION -- they are different concerns. Goal-slot is new code and adopts
    that already-reviewed shape from birth; `phase`/`slice`'s own check on THIS branch still sits
    after `enabled()`, deliberately left that way by this same goal (see Decision 2) pending the
    real rebase."""
    d = _sdlc(tmp_path, OFF)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "agent_dispatch", "--role", "goal-slot"])
    err = capsys.readouterr().err
    assert rc == 2 and "model" in err
    assert actionlog.read_goal(d, "158") == []


def test_agent_dispatch_role_phase_still_refuses_an_unknown_field(tmp_path, capsys):
    """Widening the whitelist by one (`model`) must not silently accept a genuinely bad field
    (mirrors `test_agent_done_still_refuses_a_field_that_is_not_its_own`'s own framing)."""
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "agent_dispatch", "--role", "phase",
                  "--phase", "research", "--model", "sonnet", "--signal", "x"])
    assert rc == 2 and "signal" in capsys.readouterr().err
    assert actionlog.read_goal(d, "158") == []


def test_actionlog_cli_rejects_unknown_field_with_a_usable_message(tmp_path, capsys):
    d = _sdlc(tmp_path, ON)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "note", "--bogus", "x"])
    assert rc == 2
    assert "bogus" in capsys.readouterr().err
    assert actionlog.read_goal(d, "158") == []


def test_actionlog_cli_is_silent_on_success():
    """Nothing parses this verb's stdout the way `next`/`next-batch` parse a goal (mirrors
    `qc`/`note`'s existing convention) — success prints nothing at all, unlike `emit`, which prints
    the entry id."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"
        (base / "state").mkdir(parents=True)
        (base / "config.json").write_text(json.dumps(ON))
        lp = _mod("loop")
        proc = subprocess.run([sys.executable, str(S / "loop.py"), "log", str(base), "158",
                               "note", "--text", "hi"], capture_output=True, text=True)
        assert proc.returncode == 0
        assert proc.stdout == ""


def test_actionlog_cli_is_off_when_disabled(tmp_path, capsys):
    d = _sdlc(tmp_path, OFF)
    lp = _mod("loop")
    rc = lp.main(["loop.py", "log", d, "158", "note", "--text", "hello"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OFF" in out and "action_log" in out
    assert actionlog.read_goal(d, "158") == []


# --------------------------------------------------------------------- fail-open + scrub


def test_actionlog_safe_append_swallows_a_raising_append(tmp_path, monkeypatch, capsys):
    """Line-for-line the same shape as `ledger.py`'s own test of that name — proving the fail-open
    guarantee independently."""
    d = _sdlc(tmp_path, ON)

    def raiser(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(actionlog, "append", raiser)
    result = actionlog.safe_append(d, "158", "note", text="x")
    assert result is None
    assert "entry skipped (non-fatal)" in capsys.readouterr().err


def test_actionlog_safe_append_defaults_actor_to_loop(tmp_path):
    d = _sdlc(tmp_path, ON)
    entry = actionlog.safe_append(d, "158", "claimed")
    assert entry["actor"] == "loop"


def test_actionlog_scrubs_a_secret_shaped_detail_field(tmp_path):
    d = _sdlc(tmp_path, ON)
    secret = "AKIAIOSFODNN7EXAMPLE"
    entry = actionlog.append(d, "158", "note", "agent", text=f"see key {secret}")
    assert secret not in entry["text"]
    assert secret not in actionlog.log_path(d, "158").read_text()


def test_sanitize_never_raises_when_the_scrub_module_is_unreachable(monkeypatch):
    """Fail-open even past the scrub load itself — mirrors ledger.py's own identically-named test
    for its `_sanitize_free_text`: a broken/missing hooks/research_capture.py must degrade to
    flatten+cap only, never raise, keeping append() safe for its own fail-open callers."""
    monkeypatch.setattr(actionlog, "_scrub_module", lambda: None)
    result = actionlog._sanitize("a\nb" + "x" * 600)
    assert "\n" not in result and len(result) <= actionlog.FREE_TEXT_CAP


def test_scrub_module_load_failure_is_observable_on_stderr(monkeypatch, capsys):
    """A fail-open degrade to flatten+cap-only must never be SILENT — one line to stderr, matching
    `ledger.py`'s own identical test for its `_scrub_module`."""
    monkeypatch.setattr(actionlog, "_SCRUB_MODULE", None)
    monkeypatch.setattr(actionlog, "_SCRUB_LOAD_ATTEMPTED", False)

    def boom(name, path):
        raise OSError("no such file")
    import importlib.util as _ilu
    monkeypatch.setattr(_ilu, "spec_from_file_location", boom)
    mod = actionlog._scrub_module()
    assert mod is None
    err = capsys.readouterr().err
    assert "non-fatal" in err and "scrub" in err


def test_sanitize_degrades_when_the_real_scrub_call_itself_raises(monkeypatch, capsys):
    """The OTHER fail-open branch: the module loads fine, but `_scrub()` itself raises on a given
    call — must still degrade to flatten+cap, never raise, and still say so on stderr."""
    class _BoomMod:
        @staticmethod
        def _scrub(text):
            raise RuntimeError("scrub exploded")
    monkeypatch.setattr(actionlog, "_scrub_module", lambda: _BoomMod)
    result = actionlog._sanitize("a\nb")
    assert result == "a b"
    assert "non-fatal" in capsys.readouterr().err


# --------------------------------------------------------------------- config gate


def test_actionlog_is_a_noop_when_action_log_disabled(tmp_path):
    d = _sdlc(tmp_path, OFF)
    assert actionlog.append(d, "158", "note", "agent", text="x") is None
    assert not actionlog.log_path(d, "158").exists()


def test_actionlog_is_a_noop_when_action_log_block_is_absent(tmp_path):
    d = _sdlc(tmp_path, {})
    assert actionlog.append(d, "158", "note", "agent", text="x") is None


def test_actionlog_enabled_is_a_strict_is_true_check():
    """Mirrors `ledger.enabled()`'s own strict idiom — a truthy string or a stray 1 must not
    silently switch a local trace on."""
    assert actionlog.enabled({"action_log": {"enabled": "true"}}) is False
    assert actionlog.enabled({"action_log": {"enabled": 1}}) is False
    assert actionlog.enabled({"action_log": {"enabled": True}}) is True
    assert actionlog.enabled({}) is False


# --------------------------------------------------------------------- concurrency (real processes)

_CONCURRENT_WRITER_SCRIPT = """
import sys, importlib.util
actionlog_py, sdlc_dir, goal, n, offset = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
spec = importlib.util.spec_from_file_location("actionlog", actionlog_py)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
for i in range(n):
    m.safe_append(sdlc_dir, goal, "note", actor="agent", thread="w" + offset,
                  text="line-" + offset + "-" + str(i))
"""


def test_concurrent_appends_from_two_real_processes_do_not_corrupt_the_file(tmp_path):
    """The concurrency claim in append()'s own docstring, proven, not asserted — REAL subprocesses
    (matching this repo's own #387 "real processes, not mocked/threaded" concurrency bar), two
    independent OS processes appending N lines each to the SAME goal's log file at genuinely
    overlapping instants (Popen, not sequential `run` calls, so they actually overlap in wall-clock
    time). Total line count must be 2N and every line must parse as valid JSON — concurrent appends
    interleave LINES, never corrupt a line's own bytes."""
    d = _sdlc(tmp_path, ON)
    goal = "concurrent-goal"
    n = 40
    script = tmp_path / "writer.py"
    script.write_text(_CONCURRENT_WRITER_SCRIPT)
    procs = [subprocess.Popen([sys.executable, str(script), str(S / "actionlog.py"), d, goal, str(n), tag])
             for tag in ("a", "b")]
    for p in procs:
        assert p.wait(timeout=60) == 0

    lines = actionlog.log_path(d, goal).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 * n
    parsed = [json.loads(line) for line in lines]   # every line parses -- no interleaved/corrupted bytes
    assert sum(1 for e in parsed if e["thread"] == "wa") == n
    assert sum(1 for e in parsed if e["thread"] == "wb") == n


# --------------------------------------------------------------------- read side (module-internal)


def test_read_goal_is_empty_for_a_goal_with_no_file(tmp_path):
    d = _sdlc(tmp_path, ON)
    assert actionlog.read_goal(d, "no-such-goal") == []


def test_read_goal_skips_a_malformed_and_a_blank_line(tmp_path):
    d = _sdlc(tmp_path, ON)
    actionlog.append(d, "158", "note", "agent", text="first")
    with actionlog.log_path(d, "158").open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write("\n")               # a blank line -- a process killed mid-write can leave one
    actionlog.append(d, "158", "note", "agent", text="second")
    entries = actionlog.read_goal(d, "158")
    assert [e["text"] for e in entries] == ["first", "second"]


def test_active_goals_reports_the_last_entry_per_goal(tmp_path):
    d = _sdlc(tmp_path, ON)
    actionlog.append(d, "a", "note", "loop", text="first")
    actionlog.append(d, "a", "note", "loop", text="second")
    actionlog.append(d, "b", "note", "loop", text="only")
    goals = dict(actionlog.active_goals(d))
    assert goals["a"]["text"] == "second"
    assert goals["b"]["text"] == "only"


def test_active_goals_is_empty_for_an_absent_log_dir(tmp_path):
    d = _sdlc(tmp_path, ON)
    assert actionlog.active_goals(d) == []


def test_log_path_uses_work_stem_not_a_reimplementation(tmp_path):
    """The plan's own explicit requirement: "using work.stem(), not a reimplementation" — a
    `.md`-suffixed local goal path and its bare stem must resolve to the SAME log file."""
    d = _sdlc(tmp_path, ON)
    assert actionlog.log_path(d, "0001-x.md") == actionlog.log_path(d, "0001-x")
    assert actionlog.log_path(d, "158").name == "158.jsonl"


def test_log_path_rejects_a_goal_that_would_escape_state_log_via_path_traversal(tmp_path):
    """1.0.4 validation-pass finding: unlike `thread` (guarded by loop.py's
    `_unsafe_thread_reason`), `goal` reached `log_path()`'s pathlib `/` join completely
    unvalidated — a goal with no `.md` suffix skips `work.stem()`'s own directory-stripping
    `.stem` reduction and is embedded raw. Reproduced live before this fix: a goal of
    `"../../../ESCAPED-outside-sdlc"` wrote a `.jsonl` file three directories above
    `state/log/`, outside `RUNTIME_IGNORES` coverage. `log_path()` itself is the chokepoint (not
    just `append()`), so every caller is protected regardless of how it reaches this function."""
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError, match="unsafe goal"):
        actionlog.log_path(d, "../../../ESCAPED-outside-sdlc")
    with pytest.raises(ValueError, match="unsafe goal"):
        actionlog.log_path(d, "goals/nested")               # a bare '/', no '..' needed
    with pytest.raises(ValueError, match="unsafe goal"):
        actionlog.log_path(d, "C:\\Windows\\evil")           # Windows drive-letter-rooted shape
    # legitimate shapes from the existing test above must still resolve, unaffected
    assert actionlog.log_path(d, "0001-x.md") == actionlog.log_path(d, "0001-x")
    assert actionlog.log_path(d, "158").name == "158.jsonl"


def test_actionlog_cli_refuses_a_path_traversal_goal_and_writes_nothing_outside_the_tree(tmp_path):
    """End-to-end, real subprocess, real filesystem: the exact reproduction from the validation
    pass, now refused loudly (exit 2) with nothing written anywhere — not just inside `.sdlc/`,
    literally nowhere on disk, proven by checking the specific path the pre-fix code actually
    wrote to."""
    d = _sdlc(tmp_path, ON)
    escaped_path = tmp_path / "ESCAPED-outside-sdlc.jsonl"    # 3 dirs above state/log/, from d
    proc = subprocess.run(
        [sys.executable, str(S / "loop.py"), "log", str(d), "../../../ESCAPED-outside-sdlc",
         "note", "--text", "traversal probe"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "unsafe goal" in proc.stderr
    assert not escaped_path.exists()

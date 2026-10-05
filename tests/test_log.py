"""Local-only action log — read side (skills/sigma-log/scripts/log.py). Mirrors
tests/test_status.py's own shape: a thin, hermetic read layer over a state file loop.py/work.py
write elsewhere.

Fixtures here are written with the REAL writer (skills/sigma-loop/scripts/actionlog.py's own
`append()`) so these tests are validated against genuine production-shaped output, not a
hand-rolled approximation that could drift from the real format — this is a TEST-ONLY dependency
on actionlog.py, not a production one: log.py's own source never imports it (format-only coupling,
see both modules' docstrings), which tests/test_actionlog.py separately pins for actionlog.py's
side and a plain grep here (test_log_module_has_no_actionlog_import) pins for log.py's."""
import importlib.util
import json
import pathlib
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG_S = ROOT / "skills" / "sigma-log" / "scripts"
ACTIONLOG_S = ROOT / "skills" / "sigma-loop" / "scripts"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


log = _load(LOG_S / "log.py", "log")
actionlog = _load(ACTIONLOG_S / "actionlog.py", "actionlog")

ON = {"action_log": {"enabled": True}}


def _sdlc(tmp_path):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(ON))
    return str(d)


def _seed(d, goal, kind, actor="loop", thread="main", now=None, **fields):
    """A single real, writer-produced entry — thin wrapper so fixtures below read as a short,
    ordered script rather than a wall of raw actionlog.append() calls."""
    return actionlog.append(d, goal, kind, actor, thread=thread, now=now, **fields)


# --------------------------------------------------------------------- empty state


def test_status_empty_state_when_no_log_dir(tmp_path):
    d = _sdlc(tmp_path)
    out = log.status(d)
    assert "no entries yet" in out
    assert "action_log" in out


def test_goal_view_empty_state_for_an_absent_goal(tmp_path):
    d = _sdlc(tmp_path)
    out = log.goal_view(d, "no-such-goal")
    assert "no log entries" in out
    assert "action_log" in out


# --------------------------------------------------------------------- status: active/inactive


def test_status_lists_a_goal_whose_last_entry_is_not_recorded(tmp_path):
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 100)
    _seed(d, "158", "worktree_start", now=now - 50, worktree="/x", branch="sdlc/158")
    out = log.status(d)
    assert "active goals: 1" in out
    assert "158" in out
    assert "worktree_start" in out


def test_status_omits_a_goal_whose_last_entry_is_recorded(tmp_path):
    """A goal counts as ACTIVE iff its file has a `claimed` entry and its last entry, across every
    thread, is not `recorded` — a goal that finished must not show up as still "active"."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 100)
    _seed(d, "158", "worktree_start", now=now - 50, worktree="/x", branch="sdlc/158")
    _seed(d, "158", "recorded", now=now, result="done", detail=None)
    out = log.status(d)
    assert "active goals: 0" in out


def test_status_omits_a_goal_with_no_claimed_entry_at_all(tmp_path):
    """A `file`/`note` entry alone, with no `claimed`, must not read as an active goal — matches
    the module's own active/inactive rule exactly (claimed AND not-yet-recorded, not merely
    "has any entries at all")."""
    d = _sdlc(tmp_path)
    _seed(d, "158", "note", actor="agent", text="a stray note")
    out = log.status(d)
    assert "active goals: 0" in out


def test_status_lists_multiple_active_goals_newest_activity_first(tmp_path):
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "100", "claimed", now=now - 500)
    _seed(d, "200", "claimed", now=now - 10)
    out = log.status(d)
    assert "active goals: 2" in out
    # "200" (10s ago) is more recent than "100" (500s ago) -- must be listed first.
    assert out.index("200") < out.index("100")


def test_status_reports_the_correct_thread_for_each_row(tmp_path):
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 100)
    _seed(d, "158", "agent_dispatch", actor="agent", thread="slice-a1", now=now - 4,
          role="slice", phase="implement", model="sonnet")
    out = log.status(d)
    assert "[main]" in out and "[slice-a1]" in out


# --------------------------------------------------------------------- goal view


def test_goal_view_shows_a_genuine_multi_thread_sequence(tmp_path):
    """Scripted: `claimed` (thread main), then agent_dispatch/file/agent_done interleaved across
    two distinct `--thread` values (simulating step 3b's slice wave) — the goal view must attribute
    each line to the right thread and report the correct distinct-thread count in its header."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now)
    _seed(d, "158", "agent_dispatch", actor="agent", thread="slice-a1", now=now + 1,
          role="slice", phase="implement", model="sonnet")
    _seed(d, "158", "agent_dispatch", actor="agent", thread="slice-b2", now=now + 1.1,
          role="slice", phase="implement", model="sonnet")
    _seed(d, "158", "file", actor="agent", thread="slice-a1", now=now + 2,
          path="src/bar.py", op="edit")
    _seed(d, "158", "agent_done", actor="agent", thread="slice-a1", now=now + 3,
          role="slice", result="done")
    _seed(d, "158", "file", actor="agent", thread="slice-b2", now=now + 2.5,
          path="src/baz.py", op="create")
    _seed(d, "158", "agent_done", actor="agent", thread="slice-b2", now=now + 4,
          role="slice", result="done")

    out = log.goal_view(d, "158")
    assert "3 thread(s)" in out
    assert "main, slice-a1, slice-b2" in out
    assert "src/bar.py" in out and "slice-a1" in out
    assert "src/baz.py" in out and "slice-b2" in out
    # oldest-first: claimed (thread main) must be the FIRST body line.
    lines = out.splitlines()
    first_entry_line = next(l for l in lines[1:] if l.strip())
    assert "claimed" in first_entry_line and "[loop,main]" in first_entry_line


def test_goal_view_reports_actor_for_every_line(tmp_path):
    d = _sdlc(tmp_path)
    _seed(d, "158", "claimed")
    _seed(d, "158", "note", actor="agent", text="hi")
    out = log.goal_view(d, "158")
    assert "[loop,main]" in out
    assert "[agent,main]" in out


# --------------------------------------------------------------------- end-to-end acceptance


def test_end_to_end_status_and_goal_reflect_each_step_at_that_point_in_the_sequence(tmp_path):
    """The draft's own ask, made concrete: claim a goal, dispatch 2 subagents on two threads, edit
    a file, record a `gate` for a merge, record the final outcome — assert `status`/`goal` reflect
    each step correctly AT THAT POINT in the sequence, not just at the very end."""
    d = _sdlc(tmp_path)
    now = time.time()
    goal = "158"

    _seed(d, goal, "claimed", now=now)
    assert "active goals: 1" in log.status(d)
    assert "1 thread(s)" in log.goal_view(d, goal)

    _seed(d, goal, "agent_dispatch", actor="agent", thread="slice-a1", now=now + 1,
          role="slice", phase="implement", model="sonnet")
    _seed(d, goal, "agent_dispatch", actor="agent", thread="slice-b2", now=now + 1,
          role="slice", phase="implement", model="sonnet")
    status_now = log.status(d)
    assert "[slice-a1]" in status_now and "[slice-b2]" in status_now
    assert "3 thread(s)" in log.goal_view(d, goal)

    _seed(d, goal, "file", actor="agent", thread="slice-a1", now=now + 2,
          path="src/bar.py", op="edit")
    assert "src/bar.py" in log.goal_view(d, goal)

    _seed(d, goal, "gate", actor="loop", now=now + 3, gate="merge", verdict="pass")
    assert "merge" in log.goal_view(d, goal) and "pass" in log.goal_view(d, goal)
    assert "active goals: 1" in log.status(d)      # still active -- not recorded yet

    _seed(d, goal, "recorded", actor="loop", now=now + 4, result="done", detail=None)
    assert "active goals: 0" in log.status(d)       # NOW inactive -- last entry is `recorded`
    final_view = log.goal_view(d, goal)
    assert "recorded" in final_view
    assert "6 entries" in final_view


# --------------------------------------------------------------------- module boundary


def test_log_module_has_no_actionlog_import():
    """Zero code coupling to the writer, format-only (both modules' own docstrings) — a plain,
    line-anchored substring check that log.py's own source never loads actionlog.py. Line-anchored
    rather than a bare substring test for the same reason tests/test_work.py's identical-shaped
    check is: the module's own docstrings legitimately DISCUSS "actionlog.py" in prose."""
    import re
    src = (LOG_S / "log.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import actionlog\b|from actionlog import)", src, re.MULTILINE)
    assert '_load("actionlog")' not in src
    assert "importlib" not in src, "log.py should need no dynamic loading at all — it is zero-dep"


def test_stem_matches_actionlogs_own_stem_rule():
    """log.py's local `_stem()` copy must agree with actionlog.py's (work.stem()-backed) rule, or
    the two would silently read/write different files for the same goal."""
    assert log._stem("0001-x.md") == "0001-x"
    assert log._stem("158") == "158"


# --------------------------------------------------------------------- log.py's own independent
# read_goal/_epoch/active — this file is a SEPARATE, zero-import copy of the write side's read
# logic (module docstring), so its own edge cases need their own direct proof, not just an
# assumption that actionlog.py's own tests cover them.


def test_read_goal_skips_a_malformed_and_a_blank_line(tmp_path):
    d = _sdlc(tmp_path)
    _seed(d, "158", "note", actor="agent", text="first")
    with (log.log_dir(d) / "158.jsonl").open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write("\n")
    _seed(d, "158", "note", actor="agent", text="second")
    entries = log.read_goal(d, "158")
    assert [e["text"] for e in entries] == ["first", "second"]


def test_epoch_returns_none_for_unparseable_timestamps():
    assert log._epoch("not a timestamp") is None
    assert log._epoch(None) is None
    assert log._epoch("2026-08-06T16:34:02Z") is None    # missing the .mmm milliseconds part


# --- #486/PR #487 independent review: read_goal() had zero validation on `goal`, unlike
# actionlog.py's write-side log_path() (the original bug this PR set out to fix) -- an arbitrary-
# file-DISCLOSURE bug, reproduced live: a crafted traversal goal read an unrelated planted file's
# real content into command output. `log.py` is a deliberately independent, zero-import copy (see
# module docstring) so it needs its OWN local `_unsafe_goal_reason`, not a shared one.


def test_unsafe_goal_reason_rejects_path_traversal_shapes():
    assert log._unsafe_goal_reason("../../../SECRET") is not None
    assert log._unsafe_goal_reason("goals/nested") is not None
    assert log._unsafe_goal_reason("158") is None
    assert log._unsafe_goal_reason("0007-cache") is None


def test_read_goal_never_discloses_a_real_file_outside_the_sandbox_for_a_traversal_goal(tmp_path):
    """The reviewer's own live reproduction, proven functionally: a real file planted OUTSIDE
    .sdlc, at exactly the location the pre-fix code's path join would resolve to, must never have
    its content surfaced by read_goal(). `state/log/` must exist first -- POSIX path resolution
    needs every intermediate component of a `..`-bearing path to actually exist before the `..`
    segments can resolve at all (same precondition test_loop.py's own agent_end traversal test
    needed)."""
    d = tmp_path / ".sdlc"
    (d / "state" / "log").mkdir(parents=True)
    secret = tmp_path / "SECRET.jsonl"           # 3 levels of '../' from .sdlc/state/log/<goal>.jsonl
    secret.write_text(json.dumps({"ts": "2026-01-01T00:00:00.000Z", "goal": "x", "thread": "main",
                                   "actor": "agent", "kind": "note", "text": "TOP-SECRET-PAYLOAD"}) + "\n")

    entries = log.read_goal(str(d), "../../../SECRET")

    assert entries == []
    assert secret.exists() and "TOP-SECRET-PAYLOAD" in secret.read_text()   # untouched, not deleted either


def test_goal_view_distinguishes_unsafe_goal_from_no_entries(tmp_path):
    """#499 — goal_view() must detect unsafe goals and report a distinct message, not the generic
    "config needs action_log enabled" hint. The unsafe reason must be included in the output."""
    d = _sdlc(tmp_path)
    out = log.goal_view(d, "../../../SECRET")
    # The output must mention it was refused as unsafe
    assert "refused as unsafe" in out
    # It must NOT print the generic config hint
    assert "action_log" not in out
    # It must include the actual unsafe reason
    assert "must not contain" in out or ".." in out


def test_epoch_returns_none_for_a_regex_matching_but_semantically_invalid_date():
    """The regex shape alone (`\\d{4}-\\d{2}-\\d{2}...`) can match a syntactically-plausible but
    calendar-invalid value (month 99) — `time.strptime` then raises ValueError, which must degrade
    to None, not propagate."""
    assert log._epoch("9999-99-99T99:99:99.000Z") is None


def test_epoch_round_trips_a_real_stamp():
    """`_epoch()` is `actionlog._stamp()`'s own inverse — a real, writer-produced `ts` value must
    parse back to a sub-second-precise epoch float, preserving the millisecond part exactly."""
    epoch = log._epoch("2026-08-06T16:34:02.117Z")
    assert epoch is not None
    assert round(epoch % 1, 3) == 0.117


def test_active_called_directly_on_a_missing_log_dir_is_empty(tmp_path):
    """`active()`'s own defensive `is_dir()` guard, exercised directly rather than only through
    `status()`'s outer early-return (which never reaches it) — matches
    `actionlog.active_goals()`'s identical parity guard."""
    d = _sdlc(tmp_path)
    assert log.active(d) == []


def test_active_skips_a_goal_file_that_produced_no_entries(tmp_path):
    """An empty (zero-byte) .jsonl file under state/log/ must not crash `active()` or be reported
    as an active goal."""
    d = _sdlc(tmp_path)
    (log.log_dir(d)).mkdir(parents=True, exist_ok=True)
    (log.log_dir(d) / "999.jsonl").write_text("", encoding="utf-8")
    assert log.active(d) == []


# --------------------------------------------------------------------- CLI (main())


def test_main_status_prints_and_returns_zero(tmp_path, capsys):
    d = _sdlc(tmp_path)
    _seed(d, "158", "claimed")
    rc = log.main(["log.py", "status", d])
    assert rc == 0
    assert "active goals: 1" in capsys.readouterr().out


def test_main_goal_prints_and_returns_zero(tmp_path, capsys):
    d = _sdlc(tmp_path)
    _seed(d, "158", "claimed")
    rc = log.main(["log.py", "goal", d, "158"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 entries" in out and "claimed" in out


def test_main_usage_fallback_on_bad_args(capsys):
    rc = log.main(["log.py"])
    assert rc == 2
    assert "usage" in capsys.readouterr().err


# =====================================================================================
# slots: Block A over the same ACTIVE-goal derivation (#2113)
#
# `render.py` is loaded here so the two modules' shared constants can be PINNED against each
# other. That is a test-only load, exactly like `actionlog` above: log.py's own source shells out
# to render.py and imports nothing from `skills/sigma-loop/scripts/`, which
# `test_log_module_loads_no_sibling_skill_module` re-checks now that a second sibling is in play.
render = _load(ACTIONLOG_S / "render.py", "render")

OFF = {"action_log": {"enabled": False}}
GITHUB_ON = {"action_log": {"enabled": True},
             "discovery": {"source": "github", "github": {"repo": "acme/widgets"}}}

#: Every §2 liveness glyph, so a test can assert that a line carries NONE of them.
_GLYPHS = tuple(glyph for glyph, _ in render.MARKERS)


def _config(d, config):
    (pathlib.Path(d) / "config.json").write_text(json.dumps(config), encoding="utf-8")


def _mirror(d, records):
    """The local board mirror `title_from_mirror` reads — the same NDJSON `mirror.py` writes."""
    path = pathlib.Path(d) / log.MIRROR_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _running_goal(d, goal, now, phase="implement", title=None):
    """One goal in flight: claimed, a tier chosen, a phase dispatched, a verify run. Written with
    the REAL writer, like every other fixture in this file."""
    _seed(d, goal, "claimed", now=now - 300)
    _seed(d, goal, "model_choice", actor="agent", now=now - 290, model="sonnet")
    _seed(d, goal, "agent_dispatch", actor="agent", now=now - 200, role="phase", phase=phase, model="sonnet")
    _seed(d, goal, "verify_run", now=now - 10, ok="True", exit="0", ms="1234")
    if title is not None:
        _mirror(d, [{"number": int(goal), "title": title}])


# --------------------------------------------------------------------- the three config states


def test_slots_says_the_log_is_off_and_claims_nothing_at_all_about_slots(tmp_path):
    """The branch that runs on every fresh adopter, since `action_log.enabled` defaults to false.
    The log files here are REAL and non-empty — so a line that leaked any slot detail would prove
    the off branch had gone and read them, which is the whole thing it must not do."""
    d = _sdlc(tmp_path)
    now = time.time()
    _running_goal(d, "158", now, title="Extract the coherence validator")
    _config(d, OFF)
    out = log.slots(d)
    assert out.startswith("action log off -- slot detail unavailable")
    # The off line names its config path by design, and pytest's basetemp (`pytest-2158`) can hold
    # the goal id's digits — so excuse exactly that path, and nothing else, before asserting absence.
    leaked = out.replace(str(pathlib.Path(d) / "config.json"), "")
    assert "158" not in leaked
    assert "coherence" not in leaked
    assert not any(glyph in leaked for glyph in _GLYPHS)


def test_the_off_line_and_the_enabled_but_empty_line_are_two_different_facts(tmp_path):
    """"Switched off" and "on, and there is genuinely nothing in flight" are different answers and
    a reader must be able to tell them apart from the line alone. This is the whole point of the
    degradation branch: absence must never be reported as a confident answer, and two different
    absences must not print as one."""
    d = _sdlc(tmp_path)
    enabled_but_empty = log.slots(d)
    _config(d, OFF)
    switched_off = log.slots(d)

    assert enabled_but_empty != switched_off
    assert switched_off.startswith(log.OFF_LINE)
    assert log.OFF_LINE not in enabled_but_empty
    assert enabled_but_empty.startswith("action log on")
    assert "nothing logged yet" in enabled_but_empty
    assert "nothing logged yet" not in switched_off


def test_a_config_that_cannot_be_read_is_unknown_never_off(tmp_path):
    """#1778's defect, refused: a MISSING config is "I could not look", not "it is off" — and a
    goal worktree, where this command will genuinely be run, has no `.sdlc/config.json` at all."""
    d = _sdlc(tmp_path)
    (pathlib.Path(d) / "config.json").unlink()
    out = log.slots(d)
    assert out.startswith(log.UNKNOWN_LINE)
    assert log.OFF_LINE not in out
    assert "no config.json" in out


def test_a_malformed_config_is_unknown_too(tmp_path):
    d = _sdlc(tmp_path)
    (pathlib.Path(d) / "config.json").write_text("{ not json", encoding="utf-8")
    out = log.slots(d)
    assert out.startswith(log.UNKNOWN_LINE)
    assert log.OFF_LINE not in out


def test_enabled_with_files_but_nothing_active_is_its_own_line(tmp_path):
    """A third absence: the log is on and has content, but no goal is claimed-and-unrecorded."""
    d = _sdlc(tmp_path)
    _seed(d, "158", "note", actor="agent", text="a stray note")   # no `claimed` -> not active
    out = log.slots(d)
    assert out.startswith("action log on")
    assert "0 active goals" in out
    assert "nothing logged yet" not in out


def test_a_truthy_but_not_true_enabled_value_does_not_switch_the_log_on(tmp_path):
    """Strict `is True`, mirroring `actionlog.enabled()` — otherwise this reader would claim slot
    detail from a config the WRITER treats as off, and every row would be stale."""
    d = _sdlc(tmp_path)
    _config(d, {"action_log": {"enabled": "yes"}})
    assert log.slots(d).startswith(log.OFF_LINE)


# --------------------------------------------------------------------- the block itself


def test_slots_renders_a_contract_shaped_block_for_a_real_goal(tmp_path):
    d = _sdlc(tmp_path)
    _config(d, GITHUB_ON)
    now = time.time()
    _running_goal(d, "158", now, title="Extract the coherence validator")

    out = log.slots(d)
    lines = out.splitlines()
    assert lines[0].startswith("Status — 1 goal(s) in flight, of 1 active in the action log")
    assert lines[0].endswith(":")
    assert ("* 🔵 **[#158](https://github.com/acme/widgets/issues/158)** · P5 IMPLEMENT · "
            "Extract the coherence validator · model sonnet "
            "(predicted; agent-set)") in out
    assert "  ↳ verify_run ok=True exit=0 — " in out
    assert out.rstrip().endswith("never what unblocks next.")


def test_loop_heartbeat_tail_reports_age_without_calling_an_idle_loop_dead(tmp_path):
    d = _sdlc(tmp_path)
    hb = pathlib.Path(d) / "state" / "heartbeat"; hb.mkdir(parents=True)
    (hb / "42.json").write_text(json.dumps({"pid": 42, "last_seen": 1000.0}))
    assert log.loop_heartbeat_tail(d, now=1061.0) == "loop heartbeat: 00:01:01 ago"


def test_the_description_never_claims_what_unblocks_next(tmp_path):
    """§3's arrow is a requirement WITH exceptions (#2124), and this producer is squarely in one:
    the action log records what happened and holds no record of what unblocks anything. So no
    arrow is ever written, and the tail says so rather than leaving a reader to wonder."""
    d = _sdlc(tmp_path)
    now = time.time()
    _running_goal(d, "158", now)
    out = log.slots(d)
    assert "→" not in out
    assert "never what unblocks next" in out


def test_the_ref_is_bold_without_a_link_when_the_config_never_said_these_are_issue_numbers(tmp_path):
    """§3: "Bold-without-link only when no URL exists yet." In local-goals mode a stem is a file
    name, so there is no issue to link and inventing one would be a lie."""
    d = _sdlc(tmp_path)          # the default fixture config has no `discovery` block at all
    now = time.time()
    _running_goal(d, "158", now)
    out = log.slots(d)
    assert "**#158**" in out
    assert "](http" not in out


def test_a_repo_that_would_break_the_markdown_link_yields_no_link(tmp_path):
    d = _sdlc(tmp_path)
    _config(d, {"action_log": {"enabled": True},
                "discovery": {"source": "github", "github": {"repo": "acme/widg ets)"}}})
    now = time.time()
    _running_goal(d, "158", now)
    out = log.slots(d)
    assert "**#158**" in out
    assert "](http" not in out


# --------------------------------------------------------------------- the marker axis


def test_the_marker_is_read_off_the_newest_code_written_row_not_the_newest_agent_row(tmp_path):
    """The shape EVERY finished goal in this repo's real log has: the loop writes `recorded`, and
    an `agent_done` lands after it. Reading the newest row of any class would call that goal
    `running`; the newest CODE-WRITTEN row knows it is done. This is design D-2 in the marker
    axis — an agent-typed row must never set a liveness claim."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 400)
    _seed(d, "158", "agent_dispatch", actor="agent", now=now - 300, role="phase", phase="review", model="sonnet")
    _seed(d, "158", "recorded", now=now - 200, result="done")
    _seed(d, "158", "agent_done", actor="agent", now=now - 10, role="goal-slot", result="done")

    out = log.slots(d)
    assert "* ✅ " in out
    assert "* 🔵 " not in out


def test_a_claim_with_no_code_written_action_after_it_is_queued_not_running(tmp_path):
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 100)
    _seed(d, "158", "agent_dispatch", actor="agent", now=now - 50, role="phase", phase="research", model="sonnet")
    out = log.slots(d)
    assert "* ⚪ " in out


def test_an_armed_merge_is_merging(tmp_path):
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 400)
    _seed(d, "158", "agent_dispatch", actor="agent", now=now - 300, role="phase", phase="review", model="sonnet")
    _seed(d, "158", "merge_armed", now=now - 10, pr="2127")
    assert "* 🟢 " in log.slots(d)


def test_a_recorded_result_this_table_has_not_seen_is_blocked_never_running(tmp_path):
    """`failed`, and anything a future writer records that this table does not know, must fall to
    §2's "failed, or needs a human decision" — never quietly to `running`, which would report a
    stopped goal as moving."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 400)
    _seed(d, "158", "agent_dispatch", actor="agent", now=now - 300, role="phase", phase="implement", model="sonnet")
    _seed(d, "158", "recorded", now=now - 200, result="failed")
    _seed(d, "158", "agent_done", actor="agent", now=now - 10, role="goal-slot", result="failed")
    assert "* 🔴 " in log.slots(d)


def test_every_marker_this_module_can_emit_is_in_the_contracts_own_set(tmp_path):
    states = (set(log.MARKER_BY_KIND.values()) | set(log.MARKER_BY_RESULT.values())
              | {log.MARKER_DEFAULT, log.MARKER_RECORDED_OTHER})
    assert states <= {state for _, state in render.MARKERS}


# --------------------------------------------------------------------- the phase axis


def test_a_goal_with_no_sdlc_phase_is_withheld_and_counted_never_given_one(tmp_path):
    """§2: "If you genuinely cannot name one, the work is not in the SDLC and does not belong in a
    slot line." Only agent rows carry a phase, so a just-claimed goal may honestly have none, and
    nothing in a `claimed` row says which phase the loop enters next."""
    d = _sdlc(tmp_path)
    now = time.time()
    _running_goal(d, "158", now)
    _seed(d, "777", "claimed", now=now - 5)            # claimed, nothing else: no phase anywhere
    out = log.slots(d)
    assert "1 withheld (no SDLC phase recorded)" in out
    assert "777" not in out
    assert "#158" in out


def test_a_phase_value_outside_the_contract_table_is_not_mapped_onto_one(tmp_path):
    """`code_review` and `pr_review` are real values in this repo's own log — sub-agent roles
    written before `loop.py log` began enforcing the vocabulary. They are not §2 phases, and
    rounding one to `P6 REVIEW` would be inventing a fact."""
    d = _sdlc(tmp_path)
    now = time.time()
    _running_goal(d, "158", now)                       # a renderable sibling, so the block exists
    _seed(d, "777", "claimed", now=now - 100)
    _seed(d, "777", "agent_dispatch", actor="agent", now=now - 50, role="phase",
          phase="pr_review", model="sonnet")
    out = log.slots(d)
    assert "1 withheld (no SDLC phase recorded)" in out
    assert "P6 REVIEW" not in out
    assert "777" not in out


def test_when_nothing_has_a_phase_the_command_says_so_instead_of_going_silent(tmp_path):
    """Block A needs at least one slot, so this cannot BE a block — but it must still be an
    answer. The count and the reason both survive."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 10)
    _seed(d, "777", "claimed", now=now - 20)
    out = log.slots(d)
    assert out.startswith("action log on")
    assert "2 active goal(s), none renderable as a slot" in out
    assert "158" in out and "777" in out
    assert not any(glyph in out for glyph in _GLYPHS)


# --------------------------------------------------------------------- the six-slot ceiling


def test_slots_caps_at_six_and_counts_the_rest_in_the_tail(tmp_path):
    """§3: "Max 6 slots. More than 6 means collapse, not scroll." Collapse here is: the six most
    recently active goals are rendered, and the remainder is COUNTED — so the block never grows a
    seventh slot line and the reader is never left guessing how many they are not being shown."""
    d = _sdlc(tmp_path)
    now = time.time()
    for index in range(9):
        _running_goal(d, str(100 + index), now - index * 1000)

    out = log.slots(d)
    assert len([line for line in out.splitlines() if line.startswith("* ")]) == log.MAX_SLOTS
    assert "6 shown; 3 more collapsed at the 6-slot cap" in out
    assert "Status — 9 goal(s) in flight, of 9 active in the action log" in out
    # newest-activity-first: goal 100 is the most recent, 108 the oldest; 106-108 are collapsed.
    assert "#100" in out and "#108" not in out


# --------------------------------------------------------------------- titles


def test_a_long_issue_title_is_elided_by_this_producer_not_refused_by_the_renderer(tmp_path):
    """88 characters is `phase_report.TITLE_MAX`, and render.py REFUSES over it rather than
    truncating. So a producer must elide with a visible `…` — the same rule `clean_title` applies
    — or one long issue title costs the whole block its shape."""
    d = _sdlc(tmp_path)
    _config(d, GITHUB_ON)
    now = time.time()
    _running_goal(d, "158", now, title="x" * 200)
    out = log.slots(d)
    assert "Block A not constructed" not in out
    title = out.split(" · ")[2]
    assert title.endswith("…")
    assert len(title) <= log.TITLE_MAX_CHARS


def test_a_title_that_no_local_source_knows_says_so_rather_than_guessing(tmp_path):
    """No mirror on this host is ordinary, not exceptional — local mode never writes one. The
    field is required and not nullable, so the absence has to be SAID."""
    d = _sdlc(tmp_path)
    _config(d, GITHUB_ON)
    now = time.time()
    _running_goal(d, "158", now)
    assert log.TITLE_UNAVAILABLE in log.slots(d)


def test_the_mirror_is_read_never_fetched(tmp_path):
    """No network call, on any host — the title comes out of the NDJSON the pick already wrote."""
    d = _sdlc(tmp_path)
    _config(d, GITHUB_ON)
    now = time.time()
    _mirror(d, [{"number": 999, "title": "a different goal"},
                {"number": 158, "title": "the right one"}])
    _running_goal(d, "158", now)
    out = log.slots(d)
    assert "the right one" in out
    assert "a different goal" not in out


# --------------------------------------------------------------------- descriptions


def test_an_over_long_description_is_reduced_and_the_reduction_is_visible(tmp_path):
    """A description this module cannot fit in §3's 20 words is its own bug to handle. Dropping the
    field values keeps the block; announcing the drop keeps the reader honest about what they are
    looking at. The full entry is still one `log.py goal <id>` away."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 100)
    _seed(d, "158", "agent_dispatch", actor="agent", now=now - 50,
          role="a b c d e f g h i j k l m n o p q r s t u v w", phase="implement")
    out = log.slots(d)
    assert "Block A not constructed" not in out
    description = [l for l in out.splitlines() if l.startswith("  ↳ ")][0]
    assert log.ELIDED in description
    assert len(description[len("  ↳ "):].split()) <= log.DESCRIPTION_MAX_WORDS


def test_free_text_fields_never_reach_a_description(tmp_path):
    """`gate.why` is routinely a whole paragraph in the real log. It is left out of the ≤20-word
    line by construction, not truncated at the edge of one."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 200)
    _seed(d, "158", "agent_dispatch", actor="agent", now=now - 100, role="phase", phase="review", model="sonnet")
    _seed(d, "158", "gate", now=now - 10, gate="post_review", verdict="block",
          why="a long paragraph explaining exactly what went wrong " * 5)
    out = log.slots(d)
    assert "  ↳ gate gate=post_review verdict=block — " in out
    assert "long paragraph" not in out


def test_an_unknown_log_kind_degrades_to_the_bare_kind(tmp_path):
    """A kind a FUTURE actionlog.py adds must not break this reader — the exact cross-skill
    breakage #1626's retro recorded, where a new kind trailing `recorded` changed what a different,
    unedited skill reported. Hand-written rather than seeded through `actionlog.append`, on
    purpose: the whole point is a kind the writer does not have yet."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 100)
    _seed(d, "158", "agent_dispatch", actor="agent", now=now - 50, role="phase", phase="implement", model="sonnet")
    with (log.log_dir(d) / "158.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": "2999-01-01T00:00:00.000Z", "goal": "158", "thread": "main",
                                 "actor": "loop", "kind": "quantum_entangled",
                                 "spooky": "at a distance"}) + "\n")
    out = log.slots(d)
    assert "  ↳ quantum_entangled — " in out
    assert "spooky" not in out


# --------------------------------------------------------------------- refusals from render.py


def test_a_render_refusal_keeps_the_facts_and_names_the_refusal(tmp_path):
    """A goal whose STEM is not a legal ref, reached the way the product reaches it: `active()`
    globs `*.jsonl`, and a stem with a space passes the traversal guard, so this is the real path
    and not a test seam. log.py deliberately does not re-validate what render.py validates — a
    second copy of that regex is exactly the drift #2124 was filed for — so the refusal is real,
    and what matters is that it costs the SHAPE and not the FACTS."""
    d = _sdlc(tmp_path)
    now = time.time()
    _running_goal(d, "158", now)
    source = log.log_dir(d) / "158.jsonl"
    (log.log_dir(d) / "my goal.jsonl").write_text(source.read_text(encoding="utf-8"),
                                                  encoding="utf-8")

    out = log.slots(d)
    assert "Block A not constructed" in out
    assert "REFUSED [bad-ref]" in out                    # the typed refusal, verbatim, on stdout
    assert "2 goal(s) would have been slots" in out      # the facts survive
    assert "log.py status" in out                        # and where to get them unshaped
    assert not any(glyph in out for glyph in _GLYPHS)    # never a hand-shaped look-alike block


def test_a_missing_renderer_is_reported_not_crashed(tmp_path):
    """This skill is independently installable, so its sibling may genuinely not be on the host."""
    d = _sdlc(tmp_path)
    now = time.time()
    _running_goal(d, "158", now)
    out = log.slots(d, render_py=tmp_path / "nowhere" / "render.py")
    assert "Block A not constructed" in out
    assert "the renderer is not on this host" in out


def test_main_slots_prints_and_returns_zero_even_when_the_block_is_refused(tmp_path, capsys):
    """Exit 0 on a refusal is deliberate: this command answered what it was asked, and only the
    shaping failed. A non-zero exit would invite a caller to throw away the stdout that still
    carries the facts — which is the vanishing the whole branch exists to prevent."""
    d = _sdlc(tmp_path)
    now = time.time()
    _running_goal(d, "158", now)
    source = log.log_dir(d) / "158.jsonl"
    (log.log_dir(d) / "my goal.jsonl").write_text(source.read_text(encoding="utf-8"),
                                                  encoding="utf-8")
    rc = log.main(["log.py", "slots", d])
    assert rc == 0
    assert "REFUSED [bad-ref]" in capsys.readouterr().out


def test_main_slots_prints_a_block_and_returns_zero(tmp_path, capsys):
    d = _sdlc(tmp_path)
    _config(d, GITHUB_ON)
    now = time.time()
    _running_goal(d, "158", now, title="Extract the coherence validator")
    rc = log.main(["log.py", "slots", d])
    assert rc == 0
    assert "P5 IMPLEMENT" in capsys.readouterr().out


# --------------------------------------------------------------------- vocabulary pins


def test_code_written_kinds_match_the_writers_own_list():
    """log.py cannot import actionlog.py, so its copy of the unforgeable-kind list is pinned here
    instead. A kind added there and missed here would silently demote that goal's marker."""
    assert log.CODE_WRITTEN_KINDS == actionlog.INTERNAL_KINDS


def test_phase_kinds_match_the_renderers_own_table():
    assert log.PHASE_KINDS == tuple(kind for kind, _ in render.PHASE_TOKENS)


def test_the_caps_and_the_ceiling_match_the_renderers():
    """Three constants live in both modules because neither may import the other. Drift between a
    producer's cap and its renderer's is exactly the defect #2124 was filed for."""
    assert log.MAX_SLOTS == render.MAX_SLOTS
    assert log.TITLE_MAX_CHARS == render.TITLE_MAX_CHARS
    assert log.DESCRIPTION_MAX_WORDS == render.DESCRIPTION_MAX_WORDS


def test_bounded_fields_are_a_subset_of_what_the_writer_actually_writes():
    """Every field this module prints must be one the writer can produce for that kind — a name
    that drifted would simply never render, and would do so silently."""
    for kind, fields in log.BOUNDED_FIELDS.items():
        assert kind in actionlog.ALL_FIELDS, kind
        assert set(fields) <= set(actionlog.ALL_FIELDS[kind]), kind
    assert set(log.BOUNDED_FIELDS) == set(actionlog.ALL_KINDS)


def test_log_module_loads_no_sibling_skill_module():
    """log.py shells out to render.py and imports nothing from `skills/sigma-loop/scripts/` — the
    only arrangement that satisfies both of this repo's import bans at once."""
    import re
    src = (LOG_S / "log.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import render\b|from render import)", src, re.MULTILINE)
    # LINE-ANCHORED, like the actionlog check above: this module's own docstring legitimately
    # DISCUSSES `sys.path` and `importlib` in prose, and a bare substring test would read that
    # prose as the thing it forbids -- this repo's own recorded trap.
    assert not re.search(r"^\s*(import importlib\b|from importlib)", src, re.MULTILINE)
    assert not re.search(r"^\s*sys\.path", src, re.MULTILINE)
    assert "spec_from_file_location" not in src


def test_a_goals_other_live_threads_are_named_in_its_one_slot_line(tmp_path):
    """`active()` yields one row per (goal, thread); a slot line is per GOAL, which is the unit
    `status()` itself already counts. Folding them must not make the other threads DISAPPEAR — the
    line says which thread it is reporting and how many more that goal has."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 400)
    _seed(d, "158", "agent_dispatch", actor="agent", thread="slice-a1", now=now - 300,
          role="slice", phase="implement", model="sonnet")
    _seed(d, "158", "agent_dispatch", actor="agent", thread="slice-b2", now=now - 200,
          role="slice", phase="implement", model="sonnet")
    _seed(d, "158", "file", actor="agent", thread="slice-b2", now=now - 10,
          path="src/baz.py", op="edit")

    out = log.slots(d)
    assert len([line for line in out.splitlines() if line.startswith("* ")]) == 1
    description = [line for line in out.splitlines() if line.startswith("  ↳ ")][0]
    assert "[slice-b2]" in description          # which thread this row came from
    assert "(+2 threads)" in description        # and that the goal has more


# --------------------------------------------------------------------- live work outranks finished


def _finished_goal(d, goal, now, phase="review"):
    """A goal the loop CLOSED: claimed, a phase dispatched, `recorded`, and — the shape every
    finished goal in the real log has — an `agent_done` landing AFTER the `recorded`."""
    _seed(d, goal, "claimed", now=now - 400)
    _seed(d, goal, "agent_dispatch", actor="agent", now=now - 300, role="phase", phase=phase, model="sonnet")
    _seed(d, goal, "recorded", now=now - 200, result="done")
    _seed(d, goal, "agent_done", actor="agent", now=now - 10, role="goal-slot", result="done")


def test_a_live_goal_is_never_displaced_by_a_finished_one_at_the_cap(tmp_path):
    """The finding this rule exists for (#2113, review). Against this repo's real log the block
    came back six deep with ONE live goal and five that had finished up to 174 hours earlier,
    because a `recorded` goal never leaves `active()`'s set once any row lands after it. Recency
    alone is not "what is happening".

    Here the finished goals are the MOST RECENT rows in the log — under an ordering by recency they
    would take every slot. They take none while anything is in flight."""
    d = _sdlc(tmp_path)
    now = time.time()
    for index in range(3):                      # in flight, but each older than the finished ones
        _running_goal(d, str(200 + index), now - 5000 - index * 1000)
    for index in range(4):                      # finished, and far more recently active
        _finished_goal(d, str(300 + index), now - index * 10)

    out = log.slots(d)
    slot_lines = [line for line in out.splitlines() if line.startswith("* ")]
    assert len(slot_lines) == 3
    assert all("✅" not in line for line in slot_lines)
    for index in range(3):
        assert f"#{200 + index}" in out
    for index in range(4):
        assert f"#{300 + index}" not in out
    assert "Status — 3 goal(s) in flight, of 7 active in the action log" in out
    assert "3 shown; 4 finished, not shown" in out


def test_a_finished_goal_does_not_take_a_free_slot_either(tmp_path):
    """Not merely "in flight first": while anything is in flight the slots are ITS, even when five
    of the six sit empty. A softer "finished goals may fill the leftovers" rule was written first
    and DEFEATED by the real log — one goal claimed and never recorded, last touched 200 hours ago,
    stretched every derived recency boundary with it and let a week-old block straight back in."""
    d = _sdlc(tmp_path)
    now = time.time()
    _running_goal(d, "158", now - 3000)
    _finished_goal(d, "777", now)

    out = log.slots(d)
    assert len([line for line in out.splitlines() if line.startswith("* ")]) == 1
    assert "#158" in out and "777" not in out
    assert "1 shown; 1 finished, not shown" in out


def test_with_nothing_in_flight_the_most_recently_finished_are_shown_and_said_to_be(tmp_path):
    """Nothing to displace, so the block is useful rather than empty — and the headline says what
    it is looking at, so finished work is never read as live work."""
    d = _sdlc(tmp_path)
    now = time.time()
    for index in range(3):
        _finished_goal(d, str(300 + index), now - index * 10)

    out = log.slots(d)
    assert out.startswith("Status — nothing in flight; showing the most recently finished, "
                          "of 3 active in the action log:")
    assert len([line for line in out.splitlines() if line.startswith("* ")]) == 3
    assert "3 shown" in out
    assert "finished, not shown" not in out          # all three ARE shown


def test_a_parked_or_failed_goal_counts_as_closed_not_as_in_flight(tmp_path):
    """`is_closed` is the newest code-written row being `recorded`, whatever the result — the loop
    has moved on from a parked or failed goal exactly as it has from a done one. Neither is hidden:
    each takes a slot the moment nothing is in flight, and is counted in the tail when something
    is."""
    d = _sdlc(tmp_path)
    now = time.time()
    _running_goal(d, "158", now - 3000)
    for goal, result in (("777", "parked"), ("888", "failed")):
        _seed(d, goal, "claimed", now=now - 400)
        _seed(d, goal, "agent_dispatch", actor="agent", now=now - 300, role="phase", phase="plan", model="sonnet")
        _seed(d, goal, "recorded", now=now - 200, result=result)
        _seed(d, goal, "agent_done", actor="agent", now=now - 5, role="goal-slot", result=result)

    out = log.slots(d)
    assert len([line for line in out.splitlines() if line.startswith("* ")]) == 1
    assert "2 finished, not shown" in out


def test_the_tier_caveat_is_the_short_form_the_contract_asks_for(tmp_path):
    """`docs/output-contract.md` §3: "The tier caveat is short on purpose. `predicted` and
    `agent-set` are two distinct claims ... Neither needs a full sentence to do so." #2112 shortened
    `phase_report.py`'s copy and left `render.py`'s Block A field on the 53-character form, so the
    system said one fact two ways. Both claims must still be present and distinct."""
    d = _sdlc(tmp_path)
    now = time.time()
    _running_goal(d, "158", now)
    out = log.slots(d)
    assert "model sonnet (predicted; agent-set)" in out
    assert "not observed" not in out and "not code-derived" not in out
    assert render.TIER_LABEL_PREDICTED in out and render.TIER_LABEL_AGENT_SOURCED in out

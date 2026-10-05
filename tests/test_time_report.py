"""Read side of the timing store — skills/sigma-time/scripts/time_report.py.

Two things carry most of the weight here. The routing test (`share` explicitly false) exists
because `ledger.read_all` covers only the SHARED events directory: a reader that called it alone
returns nothing on the shipped template default, which surfaces as a confident zero rather than an
error. And the degradation tests exist because "not recorded" and "recorded as zero" are different
answers, and a renderer that prints `0m` for the first one is lying in the most ordinary way.
"""
import importlib.util
import json
import pathlib

import pytest
from journal_events import journal_events

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sigma-loop" / "scripts"


def _mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tr = _mod(ROOT / "skills" / "sigma-time" / "scripts" / "time_report.py", "time_report")
timing_store = _mod(S / "timing_store.py", "timing_store")
ledger = _mod(S / "ledger.py", "ledger")


def _sdlc(tmp_path, config=None):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config if config is not None else {}))
    return str(d)


def _phase_events(sdlc_dir, config, goal, phase, start_ts, end_ts):
    for state_, ts in (("start", start_ts), ("end", end_ts)):
        ledger.append(sdlc_dir, config, "phase", goal, stream=ledger.EVENTS,
                      phase=phase, state=state_, now=ts)


# --------------------------------------------------------------------------- formatting


def test_format_ms_reads_as_a_human_duration():
    assert tr.format_ms(41_000) == "41s"
    assert tr.format_ms(491_000) == "8m11s"
    assert tr.format_ms(9_600_000) == "2h40m"


def test_format_ms_is_honest_about_what_it_cannot_measure():
    assert tr.format_ms(None) is None
    assert tr.format_ms(-5) is None
    assert tr.format_ms("nonsense") is None


# --------------------------------------------------------------------------- degradation (S7)


def test_a_goal_with_nothing_recorded_says_so_rather_than_printing_zero():
    """S7's acceptance criterion. A zero here would assert that the work took no time, which is a
    measurement claim about work nobody observed."""
    with pytest.MonkeyPatch.context():
        pass
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = _sdlc(pathlib.Path(tmp))
        out = tr.render_goal(d, "482")
        assert "not recorded" in out
        assert "0m" not in out and "0s" not in out


def test_active_is_labelled_a_lower_bound(tmp_path):
    """A phase parked and resumed measures only from the resume, and a phase that never ended has
    no interval at all — so `active` understates. The output says which direction it is wrong in
    rather than presenting a floor as an exact figure."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "phase", "implement", 9_600_000, started=1)
    assert "lower bound" in tr.render_goal(d, "482")


def test_effort_is_never_presented_as_wall_clock(tmp_path):
    """It double-counts by design — a background run inside a phase is already in that phase's
    duration — so it can exceed real elapsed time. Labelling it is what keeps that honest."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "phase", "implement", 1_000_000, started=1)
    timing_store.append(d, "482", "verify", "command", 400_000)
    out = tr.render_goal(d, "482")
    assert "not wall-clock" in out
    assert "background" in out


def test_an_unknown_unit_says_unknown(tmp_path):
    assert "unknown" in tr.render_unit(_sdlc(tmp_path), "no-such-unit", repo="acme/app")


# --------------------------------------------------------------------------- history fallback (S8)


def test_history_is_derived_from_paired_events_when_the_store_is_empty(tmp_path):
    """Goals that finished before this feature shipped have nothing in the store, but the ledger
    may still hold their phase boundaries."""
    cfg = {"journal": {"enabled": True}, "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    _phase_events(d, cfg, "482", "implement", 1_000_000, 1_000_600)

    t = tr.goal_totals(d, "482")
    assert t["recorded"] is True and t["derived"] is True
    assert t["active_ms"] == 600_000
    assert "derived" in tr.render_goal(d, "482")


def test_a_measured_total_always_beats_a_derived_one(tmp_path):
    """The store is millisecond-precise and the ledger whole-second, so a goal with both must
    report the measured figure."""
    cfg = {"journal": {"enabled": True}, "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    _phase_events(d, cfg, "482", "implement", 1_000_000, 1_000_600)
    timing_store.append(d, "482", "phase", "implement", 1234, started=1)

    t = tr.goal_totals(d, "482")
    assert t["derived"] is False and t["active_ms"] == 1234


@pytest.mark.parametrize("cfg", [
    {"journal": {"enabled": True}, "ledger": {"enabled": True, "actor": "rae"}},
    {"journal": {"enabled": True, "share": False}, "ledger": {"enabled": True, "actor": "rae"}},
    {"journal": {"enabled": True, "share": True}, "ledger": {"enabled": True, "actor": "rae"}},
])
def test_derived_history_reads_what_the_writer_really_wrote(tmp_path, cfg):
    """S8's acceptance criterion, restated for #2574/S1-G3, and still the trap this slice exists
    for: the reader must read where the WRITER lands, not where a config key suggests.

    What changed is that there is now only one place the writer lands. The old block's `share` selected
    between `.sdlc/events/` and `.sdlc/ledger/events/` and is deleted; every value above — including
    the `share: true` that used to publish — puts the events in the journal dir. `ledger.read_all`
    is still blind to it by design (it reads one stream's one directory), which is exactly why
    `all_events` unions both rather than calling it alone: a reader that called it alone would
    report zero here, and a confident zero is the fabricated reading the north-star forbids."""
    d = _sdlc(tmp_path, cfg)
    _phase_events(d, cfg, "482", "implement", 1_000_000, 1_000_600)

    assert list(ledger.local_events_dir(d).glob("*.jsonl"))          # the journal dir, every time
    assert not (pathlib.Path(d) / "ledger" / "events").exists()      # never the published one
    assert [e for e in ledger.read_all(d, stream=ledger.EVENTS)
            if e.get("kind") == "phase"] == []                       # read_all alone still sees none

    assert tr.goal_totals(d, "482")["active_ms"] == 600_000


def test_derived_background_sums_the_verify_durations(tmp_path):
    """S13. `verify` events have carried a real measured `ms` all along, so a recovered goal that
    reported `background: 0` was stating a measurement nobody took — the same fabricated zero the
    rest of this feature refuses. They are summed, not assumed."""
    cfg = {"journal": {"enabled": True}, "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    _phase_events(d, cfg, "482", "implement", 1_000_000, 1_000_600)
    for ms in (252_000, 228_000):
        ledger.append(d, cfg, "verify", "482", stream=ledger.EVENTS, ok=True, exit=0, ms=ms)

    t = tr.goal_totals(d, "482")
    assert t["derived"] is True
    assert t["background_ms"] == 480_000
    assert t["background_runs"] == 2
    assert t["effort_ms"] == 600_000 + 480_000


def test_a_derived_goal_with_no_verify_events_reports_no_background(tmp_path):
    """Zero background runs is genuinely zero background time — the absence is measured here, not
    assumed, because the events were read and there were none."""
    cfg = {"journal": {"enabled": True}, "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    _phase_events(d, cfg, "482", "implement", 1_000_000, 1_000_600)

    t = tr.goal_totals(d, "482")
    assert t["background_ms"] == 0 and t["background_runs"] == 0
    assert t["effort_ms"] == 600_000


def test_a_goal_with_verify_events_but_no_paired_phases_says_active_is_unmeasured(tmp_path):
    """A crash can leave a phase started and never ended while its verify runs still recorded. The
    background time is real and is reported; `active` is UNKNOWN, and a 0 there would claim the
    phases took no time. So it is None, and `effort` is None too — an unknown cannot be added to
    anything."""
    cfg = {"journal": {"enabled": True}, "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    ledger.append(d, cfg, "phase", "482", stream=ledger.EVENTS, phase="implement",
                  state="start", now=1_000_000)                       # never ended
    ledger.append(d, cfg, "verify", "482", stream=ledger.EVENTS, ok=True, exit=0, ms=252_000)

    t = tr.goal_totals(d, "482")
    assert t["recorded"] is True
    assert t["background_ms"] == 252_000
    assert t["active_ms"] is None and t["effort_ms"] is None

    out = tr.render_goal(d, "482")
    assert "not measurable" in out
    assert "0s" not in out and "0m" not in out


def test_a_repeated_start_measures_from_the_later_one(tmp_path):
    """Park-and-resume re-enters a phase, so a repeated start is routine rather than a crash
    signature. Measuring from the earlier start would fold the whole park into active time — the
    exact error a downstream duration metric documents choosing against."""
    cfg = {"journal": {"enabled": True}, "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    ledger.append(d, cfg, "phase", "482", stream=ledger.EVENTS, phase="implement",
                  state="start", now=1_000_000)                       # ... then parked overnight
    ledger.append(d, cfg, "phase", "482", stream=ledger.EVENTS, phase="implement",
                  state="start", now=1_050_000)                       # resumed
    ledger.append(d, cfg, "phase", "482", stream=ledger.EVENTS, phase="implement",
                  state="end", now=1_050_600)

    assert tr.goal_totals(d, "482")["active_ms"] == 600_000           # not 50_600_000


# --------------------------------------------------------------------------- review fixes


def test_a_unit_render_without_a_repo_refuses_rather_than_misfiling(tmp_path, capsys):
    """Code review C1, BLOCKING: run as the docs gave it — `unit <sdlc_dir> <unit>` with no
    `<repo>` — the rollup filed the local repository's own goals under "recorded in that
    checkout". The rollup is single-repo by construction and cannot know which repo is local
    without being told; without a repo it refuses, and the CLI returns usage."""
    d = _sdlc(tmp_path)
    units = pathlib.Path(d) / "features" / "units"
    units.mkdir(parents=True)
    (units / "h.json").write_text(json.dumps({
        "schema": "sigma/features@1",
        "features": {"h": {"title": "h", "owner": None, "open": True, "parent": None,
                           "tracking_issue": None,
                           "repos": {"acme/app": {"branch": "f", "owner": None,
                                                  "authorized": True, "goals": [482]}}}}}))
    timing_store.append(d, "482", "phase", "implement", 2000, started=1)

    out = tr.render_unit(d, "h", repo=None)
    assert "not counted" not in out
    assert "<owner/name>" in out
    assert tr.main(["time_report.py", "unit", d, "h"]) == 2


def test_a_ledger_row_with_a_malformed_timestamp_is_ignored(tmp_path):
    """Code review C2: `ledger._epoch` returns -1 for garbage, never None, so a `start` with a
    bad `ts` opened at epoch -1 and the next `end` added ~1.79 billion seconds to `active`."""
    cfg = {"journal": {"enabled": True}, "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    events_dir = pathlib.Path(d) / "ledger" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "rae-h.1.jsonl").write_text(
        json.dumps({"id": "rae:1:1", "ts": "garbage", "actor": "rae", "kind": "phase",
                    "goal": "482", "phase": "implement", "state": "start"}) + "\n"
        + json.dumps({"id": "rae:1:2", "ts": "2026-09-10T00:10:00Z", "actor": "rae",
                      "kind": "phase", "goal": "482", "phase": "implement", "state": "end"}) + "\n")
    t = tr.goal_totals(d, "482")
    assert t["recorded"] is False or t["active_ms"] < 1_000_000_000_000


def test_elapsed_is_reported_when_the_ledger_has_claim_and_terminal_entries(tmp_path):
    """The spec's fourth figure — calendar time, idle included, context only — was documented and
    never built (code review, docs drift). Shown only when the coordination ledger holds the
    goal's `claimed` and a terminal entry; never required for the other three."""
    cfg = {"ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    ledger.append(d, cfg, "claimed", "482", now=1_000_000)
    ledger.append(d, cfg, "done", "482", now=1_000_000 + 17 * 3600)
    timing_store.append(d, "482", "phase", "implement", 9_600_000, started=1)

    out = tr.render_goal(d, "482")
    assert "elapsed" in out and tr.format_ms(17 * 3600 * 1000) in out
    assert "idle included" in out


def test_elapsed_is_omitted_when_the_ledger_has_nothing(tmp_path):
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "phase", "implement", 9_600_000, started=1)
    assert "elapsed" not in tr.render_goal(d, "482")


# --------------------------------------------------------------------------- session rendering (S6)


def test_a_floor_only_session_says_total_is_not_measurable(tmp_path):
    """S6's acceptance criterion. On a host with no turn hooks only scripts are recorded, and the
    floor cannot see `sigma-plan` or `sigma-implement` at all (they invoke no scripts). Printing the
    script sum as the total would be a confident undercount; the honest total is "not
    measurable", with the floor shown beside it and labelled."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "s1", "script", "loop", 252_000)
    timing_store.append_session(d, "s1", "script", "loop", 228_000)
    out = tr.render_session(d, "s1")
    assert "floor only" in out
    assert "not measurable" in out
    assert tr.format_ms(480_000) in out                       # the floor, shown
    assert "precise" not in out


def test_a_precise_session_reports_turn_time_as_total_with_scripts_beside_it(tmp_path):
    """Turn time IS the total; script time is reported beside it and their sum appears nowhere —
    scripts run inside turns."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "s1", "turn", "sigma-implement", 100_000, started=1)
    timing_store.append_session(d, "s1", "script", "loop", 20_000)
    out = tr.render_session(d, "s1")
    assert "precise" in out
    assert tr.format_ms(100_000) in out                       # total = turn time
    assert tr.format_ms(20_000) in out                        # scripts, beside
    assert tr.format_ms(120_000) not in out                   # never summed
    assert "sigma-implement" in out


def test_the_no_skill_bucket_is_shown_and_worded_honestly(tmp_path):
    """Work before any skill was invoked is still work. Shown, not hidden — and worded as "no
    skill invoked yet", because a skill stays active until the next switch and the reader should
    not mistake this bucket for "unattributable"."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "s1", "turn", "(none)", 45_000, started=1)
    out = tr.render_session(d, "s1")
    assert "(no skill)" in out and "no skill invoked yet" in out


def test_the_unattributed_bucket_is_labelled_as_shared(tmp_path):
    """`unattributed-<date>` is not a session: two concurrent hookless sessions merge into it. The
    header must say so rather than present it as one session."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "unattributed-2026-09-10", "script", "loop", 1_000)
    out = tr.render_session(d, "unattributed-2026-09-10")
    assert "possibly several sessions" in out
    assert "2026-09-10" in out


def test_a_session_with_nothing_recorded_says_so(tmp_path):
    out = tr.render_session(_sdlc(tmp_path), "s-empty")
    assert "not recorded" in out
    assert "0s" not in out and "0m" not in out


def test_the_session_verb_defaults_to_the_current_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SIGMA_RUN_ID", "run-cli")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "run-cli", "turn", "sigma-plan", 60_000, started=1)
    assert tr.main(["time_report.py", "session", d]) == 0
    assert "run-cli" in capsys.readouterr().out


# --------------------------------------------------------------------------- unit rendering


def test_a_unit_render_names_the_repos_it_could_not_read(tmp_path):
    d = _sdlc(tmp_path)
    units = pathlib.Path(d) / "features" / "units"
    units.mkdir(parents=True)
    (units / "hardening.json").write_text(json.dumps({
        "schema": "sigma/features@1",
        "features": {"hardening": {"title": "h", "owner": None, "open": True, "parent": None,
                                   "tracking_issue": None,
                                   "repos": {"acme/app": {"branch": "f", "owner": None,
                                                          "authorized": True, "goals": [482]},
                                             "acme/sibling": {"branch": "f", "owner": None,
                                                              "authorized": True,
                                                              "goals": [901, 902]}}}}}))
    timing_store.append(d, "482", "phase", "implement", 2000, started=1)

    out = tr.render_unit(d, "hardening", repo="acme/app")
    assert "acme/sibling" in out and "not counted" in out


# ------------------------------------------- S1-G3 / #2574: the union read, not a routed read


def _write_event(sdlc_dir, rel, ident, goal, phase, state, ts):
    d = pathlib.Path(sdlc_dir) / rel
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"id": ident, "ts": ledger._stamp(ts), "actor": "rae", "kind": "phase",
                       "goal": goal, "phase": phase, "state": state})
    with (d / "rae-h.1.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


@pytest.mark.parametrize("cfg", [
    {"journal": {"enabled": True}},
    {"journal": {"enabled": False}},
    {"journal": {"enabled": True, "share": True}},
    {"journal": {"enabled": True, "share": False}},
    {},
])
def test_all_events_reads_the_legacy_shared_dir_whatever_the_config_says(tmp_path, cfg):
    """One release of legacy history lives in `.sdlc/ledger/events/` and is never moved. A reader
    that stopped reading it the moment the config changed would silently lose it."""
    d = _sdlc(tmp_path, cfg)
    _write_event(d, "ledger/events", "rae:1", "482", "implement", "start", 1_000_000)
    _write_event(d, "ledger/events", "rae:2", "482", "implement", "end", 1_000_600)
    assert tr.goal_totals(d, "482")["active_ms"] == 600_000


@pytest.mark.parametrize("cfg", [
    {"journal": {"enabled": True}},
    {"journal": {"enabled": False}},
    {"journal": {"enabled": True, "share": True}},
    {},
])
def test_all_events_reads_the_local_dir_whatever_the_config_says(tmp_path, cfg):
    """`share: true` and a missing config used to route this reader AWAY from `.sdlc/events/`.
    After the move that is where every new event lands, so routing on config would return a
    confident zero on the shipped default."""
    d = _sdlc(tmp_path, cfg)
    _write_event(d, "events", "rae:1", "482", "implement", "start", 1_000_000)
    _write_event(d, "events", "rae:2", "482", "implement", "end", 1_000_600)
    assert tr.goal_totals(d, "482")["active_ms"] == 600_000


def test_all_events_unions_both_dirs_without_double_counting(tmp_path):
    """A goal whose history straddles the move: its start is in the legacy dir, its end in the new
    one. Only a union pairs them, and the pair must be counted once."""
    d = _sdlc(tmp_path, {"journal": {"enabled": True}})
    _write_event(d, "ledger/events", "rae:1", "482", "implement", "start", 1_000_000)
    _write_event(d, "events", "rae:2", "482", "implement", "end", 1_000_600)
    events = [e for e in tr.all_events(d) if e.get("goal") == "482"]
    assert len(events) == 2, events
    assert tr.goal_totals(d, "482")["active_ms"] == 600_000

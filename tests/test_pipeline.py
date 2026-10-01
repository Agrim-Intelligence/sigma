"""Tests for the bidirectional pipeline report card (pipeline.py) and the
machine done_when (loop.py verify + verify.enforce). All deterministic, $0."""
import json, pathlib, importlib.util, subprocess, sys, tempfile, os
from journal_events import journal_events

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _project(d, stages):
    root = pathlib.Path(d)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "--local",
                    "sigma.allowRepositoryShellCommands", "true"], check=True)
    base = root / ".sdlc"
    (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    (base / "config.json").write_text(json.dumps({"budget": {"max_iterations": 10}}))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "state" / "review-queue.md").write_text("# Q\n")
    (base / "pipeline.json").write_text(json.dumps({"name": "demo", "stages": stages}))
    return str(base)


# --- F15: a malformed pipeline.json must be treated as absent, never a traceback -----------


def _sdlc_only(d):
    base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    return base


def test_load_pipeline_treats_a_top_level_array_as_no_pipeline():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_only(d)
        (base / "pipeline.json").write_text(json.dumps([1, 2]))
        assert _mod("pipeline").load_pipeline(str(base)) is None


def test_load_pipeline_treats_a_top_level_scalar_as_no_pipeline():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_only(d)
        (base / "pipeline.json").write_text(json.dumps(42))
        assert _mod("pipeline").load_pipeline(str(base)) is None


def test_load_pipeline_treats_invalid_json_as_no_pipeline():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_only(d)
        (base / "pipeline.json").write_text("not json")
        assert _mod("pipeline").load_pipeline(str(base)) is None


def test_load_pipeline_treats_a_dict_missing_stages_as_no_pipeline():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_only(d)
        (base / "pipeline.json").write_text(json.dumps({"name": "demo"}))    # no "stages"
        assert _mod("pipeline").load_pipeline(str(base)) is None


def test_cli_card_on_a_malformed_pipeline_json_prints_no_pipeline_not_a_traceback():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_only(d)
        (base / "config.json").write_text(json.dumps({"budget": {"max_iterations": 10}}))
        (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
        (base / "state" / "review-queue.md").write_text("# Q\n")
        (base / "pipeline.json").write_text("[1, 2]")
        proc = subprocess.run([sys.executable, str(S / "pipeline.py"), "card", str(base)],
                              capture_output=True, text=True)
        assert proc.returncode == 3
        assert "Traceback" not in proc.stderr
        assert "NO-PIPELINE" in proc.stderr


def test_cli_propose_on_invalid_json_prints_no_pipeline_not_a_traceback():
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_only(d)
        (base / "config.json").write_text(json.dumps({"budget": {"max_iterations": 10}}))
        (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
        (base / "state" / "review-queue.md").write_text("# Q\n")
        (base / "pipeline.json").write_text("not json")
        proc = subprocess.run([sys.executable, str(S / "pipeline.py"), "propose", str(base)],
                              capture_output=True, text=True)
        assert proc.returncode == 3
        assert "Traceback" not in proc.stderr
        assert "NO-PIPELINE" in proc.stderr


def test_card_reports_both_directions_with_honest_absence():
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / "raw.txt").write_text("x")
        base = _project(d, [
            {"name": "extract", "produces": ["raw.txt"],
             "checks": {"forward": [{"name": "raw ok", "run": "true"}]}},
            {"name": "transform",
             "checks": {"reverse": [{"name": "rows trace back", "run": "true"}]}},
            {"name": "publish"},          # no checks at all → honest ABSENT lanes
        ])
        card = _mod("pipeline").build_card(base)
        stages = {s["stage"]: s["signals"] for s in card["stages"]}
        assert any(x["status"] == "PASS" and x["direction"] == "forward"
                   for x in stages["extract"])
        assert any(x["status"] == "PASS" and x["direction"] == "reverse"
                   for x in stages["transform"])
        publish = stages["publish"]
        assert {x["direction"] for x in publish if x["status"] == "ABSENT"} == {"forward", "reverse"}
        assert card["verdict"]["clean"] is False          # blocked lanes forbid a clean verdict
        assert {"action": "declare_checks", "stage": "publish", "direction": "reverse"} \
            in card["verdict"]["next_actions"]


def test_failing_check_and_missing_artifact_localize_to_their_stage():
    with tempfile.TemporaryDirectory() as d:
        base = _project(d, [
            {"name": "extract", "produces": ["nope-*.txt"]},                  # missing artifact
            {"name": "transform", "checks": {"reverse": [{"name": "trace", "run": "false"}]}},
        ])
        card = _mod("pipeline").build_card(base)
        assert card["verdict"]["failing_stages"] == ["extract", "transform"]
        assert card["verdict"]["overall"] == "FAIL"


def test_warn_exit_code_2_is_warn_not_fail():
    with tempfile.TemporaryDirectory() as d:
        base = _project(d, [
            {"name": "s", "checks": {"forward": [{"name": "advisory", "run": "exit 2"}]}},
        ])
        card = _mod("pipeline").build_card(base)
        assert card["stages"][0]["signals"][0]["status"] == "WARN"
        assert card["verdict"]["failing_stages"] == []


def test_compare_finds_recurrence_and_improvement():
    with tempfile.TemporaryDirectory() as d:
        base = _project(d, [
            {"name": "s", "checks": {"forward": [{"name": "gate", "run": "false"}]}},
        ])
        pl = _mod("pipeline")
        prior = pl.build_card(base)
        delta_same = pl.compare_cards(prior, pl.build_card(base))
        assert delta_same["recurrence_count"] == 1 and not delta_same["improved"]
        (pathlib.Path(base) / "pipeline.json").write_text(json.dumps(
            {"name": "demo", "stages": [
                {"name": "s", "checks": {"forward": [{"name": "gate", "run": "true"}]}}]}))
        delta_fixed = pl.compare_cards(prior, pl.build_card(base))
        assert delta_fixed["improved"] and delta_fixed["recurrence_count"] == 0

        # The third branch (issue #298, [E15.S4]): compare_cards' compare_finds_recurrence test
        # above only ever exercised still_failing/improved -- "regressed" (now worse than before)
        # was never triggered on the engine side. base is currently PASS (the write just above);
        # flip it to FAIL and diff against that PASS epoch.
        prior_pass = pl.build_card(base)
        (pathlib.Path(base) / "pipeline.json").write_text(json.dumps(
            {"name": "demo", "stages": [
                {"name": "s", "checks": {"forward": [{"name": "gate", "run": "false"}]}}]}))
        delta_regressed = pl.compare_cards(prior_pass, pl.build_card(base))
        assert delta_regressed["regressed"] and not delta_regressed["improved"]
        assert delta_regressed["regressed"][0]["before"] == pl.PASS
        assert delta_regressed["regressed"][0]["now"] == pl.FAIL


def test_severity_order_matches_the_contract():
    """Engine-side pin of the severity order (issue #298, [E15.S4]) -- the sibling of
    contract/vocabulary.json's "severity_order" key, hand-typed independently on both
    sides (Decision 3: no shared module across the plugin/product boundary)."""
    pl = _mod("pipeline")
    assert (pl.PASS, pl.WARN, pl.FAIL, pl.ABSENT) == ("PASS", "WARN", "FAIL", "ABSENT")
    assert pl._ORDER == {pl.PASS: 0, pl.ABSENT: 1, pl.WARN: 2, pl.FAIL: 3}


def test_no_pipeline_json_exits_3():
    with tempfile.TemporaryDirectory() as d:
        base = _project(d, [])
        (pathlib.Path(base) / "pipeline.json").unlink()
        proc = subprocess.run([sys.executable, str(S / "pipeline.py"), "card", base],
                              capture_output=True, text=True)
        assert proc.returncode == 3 and "NO-PIPELINE" in proc.stderr


# --- machine done_when: loop.py verify + verify.enforce ---

def _goal_backlog(d, verify_command=None, enforce=False):
    base = pathlib.Path(d) / ".sdlc"
    subprocess.run(["git", "init", "-q", str(pathlib.Path(d))], check=True)
    subprocess.run(["git", "-C", str(pathlib.Path(d)), "config", "--local",
                    "sigma.allowRepositoryShellCommands", "true"], check=True)
    (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    cfg = {"budget": {"max_iterations": 10},
           "verify": {"command": "", "enforce": enforce}}
    (base / "config.json").write_text(json.dumps(cfg))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "state" / "review-queue.md").write_text("# Q\n")
    fm = "---\nid: 0001\nstatus: pending\n"
    if verify_command:
        fm += f'verify_command: {verify_command}\n'
    (base / "goals" / "0001.md").write_text(fm + "---\nx\n")
    return str(base), str(base / "goals" / "0001.md")


def _loop_cli(*args):
    return subprocess.run([sys.executable, str(S / "loop.py"), *args],
                          capture_output=True, text=True)


def test_verify_verb_records_passing_evidence():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d, verify_command="true")
        proc = _loop_cli("verify", base, goal)
        assert proc.returncode == 0 and "VERIFIED" in proc.stdout
        ev = json.loads((pathlib.Path(base) / "state" / "verify" / "0001.json").read_text())
        assert ev["exit"] == 0


def test_verify_verb_no_command_is_honest_exit_3():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d)
        proc = _loop_cli("verify", base, goal)
        assert proc.returncode == 3 and "NO-COMMAND" in proc.stderr


def test_enforce_refuses_done_without_fresh_evidence_then_accepts():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d, verify_command="true", enforce=True)
        _loop_cli("start", base)
        refused = _loop_cli("record", base, goal, "done")
        assert refused.returncode == 4 and "REFUSED" in refused.stderr
        assert "status: pending" in pathlib.Path(goal).read_text()   # nothing recorded
        assert _loop_cli("verify", base, goal).returncode == 0
        accepted = _loop_cli("record", base, goal, "done")
        assert accepted.returncode == 0
        assert "status: done" in pathlib.Path(goal).read_text()


def test_enforce_refuses_failed_verify_evidence():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d, verify_command="false", enforce=True)
        _loop_cli("start", base)
        assert _loop_cli("verify", base, goal).returncode == 1
        refused = _loop_cli("record", base, goal, "done")
        assert refused.returncode == 4 and "FAILED" in refused.stderr


# F17/#342: `enforce: 1` / `enforce: "true"` are easy JSON mistakes for the literal bool `true` —
# a strict `is True` check silently treats them as off and lets an unverified `done` through. Same
# shape as test_enforce_refuses_done_without_fresh_evidence_then_accepts above, just spelled wrong.


def test_enforce_accepts_truthy_int_spelling():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d, verify_command="true", enforce=1)
        _loop_cli("start", base)
        refused = _loop_cli("record", base, goal, "done")
        assert refused.returncode == 4 and "REFUSED" in refused.stderr   # gate is ACTIVE, not silently off
        assert "status: pending" in pathlib.Path(goal).read_text()
        assert _loop_cli("verify", base, goal).returncode == 0
        accepted = _loop_cli("record", base, goal, "done")
        assert accepted.returncode == 0
        assert "status: done" in pathlib.Path(goal).read_text()


def test_enforce_accepts_truthy_string_spelling():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d, verify_command="true", enforce="true")
        _loop_cli("start", base)
        refused = _loop_cli("record", base, goal, "done")
        assert refused.returncode == 4 and "REFUSED" in refused.stderr   # gate is ACTIVE, not silently off
        assert _loop_cli("verify", base, goal).returncode == 0
        accepted = _loop_cli("record", base, goal, "done")
        assert accepted.returncode == 0
        assert "status: done" in pathlib.Path(goal).read_text()


def test_enforce_off_keeps_prior_behavior():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d)                    # enforce=False default
        assert _loop_cli("record", base, goal, "done").returncode == 0
        assert "status: done" in pathlib.Path(goal).read_text()


def test_enforce_never_blocks_parked_or_failed():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d, enforce=True)
        assert _loop_cli("record", base, goal, "parked", "needs a call").returncode == 0
        assert "status: parked" in pathlib.Path(goal).read_text()


# --- in-process coverage of the CLI paths (subprocess runs don't count) ---

def test_loop_verify_helpers_are_the_state_functions_not_local_copies():
    """The alias block's own claim, enforced (#538). Both helpers live in state.py so work.py's
    merge gate and loop.py's record-done path require the SAME evidence rule without importing each
    other. A module-level `def _done_refusal` further down loop.py used to overwrite the alias
    during module exec, so loop.py silently ran a textual duplicate: no drift on the day, but a fix
    to `state.done_refusal` (F11/#341 was one) could never reach the record-done path. Identity, not
    behavior, is the pin — a re-introduced duplicate would pass a behavioral test on the day it was
    written and rot from there."""
    lp = _mod("loop")
    assert lp._done_refusal is lp.state.done_refusal
    assert lp._evidence_path is lp.state.evidence_path     # the sibling the same refactor got right


def test_verify_goal_and_refusal_in_process():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d, verify_command="true", enforce=True)
        lp = _mod("loop")
        lp.state.start_run(base)
        assert lp._done_refusal(base, goal) == "no verify evidence for this goal"
        assert lp.verify_goal(base, goal) == 0
        assert lp._done_refusal(base, goal) is None
        # stale evidence (predates the run) refuses again
        lp.state.start_run(base)
        import time as _t; _t.sleep(1.1)     # comfortable margin past the evidence's own timestamp
        lp.state.start_run(base)
        assert lp._done_refusal(base, goal) == "verify evidence predates this run"


def test_done_refusal_in_process_rejects_sub_second_stale_evidence(monkeypatch):
    """F11/#341: the whole-second-floor staleness hole (both `at` and `run_started_at` used to be
    `int(time.time())`). Reproduces the issue's repro through LOOP.PY's own entry point rather than
    state.py's -- worth keeping distinct from the state.py unit tests even though #538 deleted
    loop.py's textual duplicate and `_done_refusal` is now the alias: this pins that the record-done
    path actually reaches the fixed rule. A verify evidence write that lands 0.1s into second T, this
    run starting 0.3s into that SAME second T -- 0.2s of real staleness that used to floor-collide
    and read as fresh."""
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d, verify_command="true", enforce=True)
        lp = _mod("loop")
        t = 1_700_000_000.0
        monkeypatch.setattr(lp.time, "time", lambda: t + 0.3)
        lp.state.start_run(base)                           # this run "starts" 0.3s into second T
        monkeypatch.setattr(lp.time, "time", lambda: t + 0.1)
        assert lp.verify_goal(base, goal) == 0              # evidence written 0.1s into the SAME second
        assert int(t + 0.1) == int(t + 0.3) == int(t)       # the whole-second collision, confirmed
        assert lp._done_refusal(base, goal) == "verify evidence predates this run"


def test_verify_goal_failing_command_and_config_fallback():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d, enforce=False)
        cfg = pathlib.Path(base) / "config.json"
        cfg.write_text(json.dumps({"budget": {"max_iterations": 10},
                                   "verify": {"command": "false", "enforce": False}}))
        lp = _mod("loop")
        assert lp.verify_goal(base, goal) == 1           # config-level command, failing
        assert lp._done_refusal(base, goal).startswith("last verify FAILED")


def test_loop_main_verbs_in_process(capsys):
    with tempfile.TemporaryDirectory() as d:
        base, goal = _goal_backlog(d, verify_command="true", enforce=True)
        lp = _mod("loop")
        assert lp.main(["loop.py", "start", base]) == 0
        assert lp.main(["loop.py", "record", base, goal, "done"]) == 4      # refused, no evidence
        assert lp.main(["loop.py", "verify", base, goal]) == 0
        assert lp.main(["loop.py", "record", base, goal, "done"]) == 0      # evidence fresh
        assert lp.main(["loop.py", "spend", base, "42"]) == 0
        assert lp.main(["loop.py", "bogus"]) == 2
        capsys.readouterr()


def test_pipeline_main_json_and_compare_in_process(tmp_path, capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _project(d, [
            {"name": "s", "checks": {"forward": [{"name": "gate", "run": "false"}]}},
        ])
        pl = _mod("pipeline")
        prior_json = tmp_path / "prior.json"
        assert pl.main(["pipeline.py", "card", base, "--json", str(prior_json)]) == 1
        out = capsys.readouterr().out
        assert "Pipeline report card" in out and "verdict: FAIL" in out
        assert pl.main(["pipeline.py", "card", base, "--compare", str(prior_json)]) == 1
        out = capsys.readouterr().out
        assert "STILL FAILING" in out and "recurrence" in out
        assert pl.main(["pipeline.py", "card", base, "--compare", str(tmp_path / "nope.json")]) == 3
        assert pl.main(["pipeline.py"]) == 2
        capsys.readouterr()


def test_pipeline_render_regressed_row_and_invalid_spec():
    with tempfile.TemporaryDirectory() as d:
        base = _project(d, [
            {"name": "s", "checks": {"forward": [{"name": "gate", "run": "true"}]}},
        ])
        pl = _mod("pipeline")
        good = pl.build_card(base)
        (pathlib.Path(base) / "pipeline.json").write_text(json.dumps(
            {"name": "demo", "stages": [
                {"name": "s", "checks": {"forward": [{"name": "gate", "run": "false"}]}}]}))
        bad = pl.build_card(base)
        delta = pl.compare_cards(good, bad)
        assert delta["regressed"] and "REGRESSED" in pl.render(bad, delta)
        (pathlib.Path(base) / "pipeline.json").write_text(json.dumps({"stages": "not-a-list"}))
        assert pl.load_pipeline(base) is None


def test_check_timeout_reads_fail():
    with tempfile.TemporaryDirectory() as d:
        base = _project(d, [{"name": "s", "checks": {"forward": [{"name": "hang", "run": "sleep 2"}]}}])
        pl = _mod("pipeline")
        pl._CHECK_TIMEOUT_SECS = 0.2
        card = pl.build_card(base)
        sig = card["stages"][0]["signals"][0]
        assert sig["status"] == "FAIL" and "timed out" in sig["detail"]


# --- the feedback circle (0.6): failing card signals become proposed goals ---

def test_propose_writes_proposed_goals_with_wired_verify():
    with tempfile.TemporaryDirectory() as d:
        base = _project(d, [
            {"name": "transform",
             "checks": {"reverse": [{"name": "rows trace back", "run": "false"}]}},
        ])
        pl = _mod("pipeline")
        created = pl.propose_goals(base, pl.build_card(base))
        assert len(created) == 1
        text = pathlib.Path(created[0]).read_text()
        assert "status: proposed" in text and "source: detector" in text
        assert "verify_command: false" in text          # the failing check IS the proof-of-fix
        # dedup: proposing again creates nothing new
        assert pl.propose_goals(base, pl.build_card(base)) == []


def test_proposed_goals_are_never_auto_picked_until_promoted():
    with tempfile.TemporaryDirectory() as d:
        base = _project(d, [
            {"name": "s", "checks": {"forward": [{"name": "gate", "run": "false"}]}},
        ])
        pl = _mod("pipeline")
        created = pl.propose_goals(base, pl.build_card(base))
        lp = _mod("loop")
        src = lp.sources.get_source(base, lp.state.load_config(base))
        kind, _ = lp._next(base, src, lp.state.load_config(base))
        assert kind == "DONE"                            # proposed is invisible to the loop
        # a human promotes it → the loop picks it up
        goal = pathlib.Path(created[0])
        goal.write_text(goal.read_text().replace("status: proposed", "status: pending"))
        kind, picked = lp._next(base, src, lp.state.load_config(base))
        assert kind == "goal" and picked == str(goal)


def test_propose_cli_and_status_counts_proposed(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _project(d, [
            {"name": "s", "checks": {"forward": [{"name": "gate", "run": "false"}]}},
        ])
        pl = _mod("pipeline")
        assert pl.main(["pipeline.py", "propose", base]) == 0
        assert "proposed 1 goal(s)" in capsys.readouterr().out
        st_path = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-status" / "scripts" / "status.py"
        spec = importlib.util.spec_from_file_location("status", st_path)
        st = importlib.util.module_from_spec(spec); spec.loader.exec_module(st)
        assert st.summary(base)["proposed"] == 1


def test_e2e_feedback_circle_break_detect_propose_fix_verify_clean(tmp_path):
    """The whole loop on a neutral project, end to end and $0:
    a stage breaks -> the card localizes it -> propose writes a groomable goal with the
    failing check wired as its proof -> a human promotes -> the loop works the goal ->
    verify.enforce demands the machine evidence -> the next card is clean and --compare
    reports the improvement. No LLM, no network, no gate on the examined run anywhere."""
    root = tmp_path
    base = _project(str(root), [
        {"name": "transform",
         "checks": {"reverse": [{"name": "outputs trace to inputs",
                                 "run": "test -f fixed.txt"}]}},
    ])
    (pathlib.Path(base) / "config.json").write_text(json.dumps(
        {"budget": {"max_iterations": 10}, "verify": {"command": "", "enforce": True}}))
    pl, lp = _mod("pipeline"), _mod("loop")

    broken = pl.build_card(base)                      # 1. detect + localize
    assert broken["verdict"]["failing_stages"] == ["transform"]
    prior = json.loads(json.dumps(broken))

    created = pl.propose_goals(base, broken)          # 2. findings become groomable work
    assert len(created) == 1
    goal = pathlib.Path(created[0])

    session_pid = os.getpid()
    lp.session_start(base, session_pid)
    lp.state.start_run(base)
    src = lp.sources.get_source(base, lp.state.load_config(base))
    assert lp._next(base, src, lp.state.load_config(base), session_pid=session_pid)[0] == "DONE"   # human gate holds

    goal.write_text(goal.read_text().replace("status: proposed", "status: pending"))  # 3. groom

    kind, picked = lp._next(base, src, lp.state.load_config(base), session_pid=session_pid)  # 4. the loop works it
    assert kind == "goal" and picked == str(goal)
    assert lp.main(["loop.py", "record", base, picked, "done"]) == 4      # no evidence -> refused
    (root / "fixed.txt").write_text("the fix")                            # the actual fix lands
    assert lp.main(["loop.py", "verify", base, picked]) == 0              # machine proof
    assert lp.main(["loop.py", "record", base, picked, "done"]) == 0

    healed = pl.build_card(base)                      # 5. the loop proves its own fix
    assert healed["verdict"]["failing_stages"] == []
    delta = pl.compare_cards(prior, healed)
    assert delta["improved"] and delta["recurrence_count"] == 0


# ---- discovery-scan -> proposed goals (Slice 6) --------------------------------------------------

def _read_goal(base, name):
    return (pathlib.Path(base) / "goals" / name).read_text()


def test_propose_from_discovery_writes_inert_proposed_goals():
    pl = _mod("pipeline")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True)
        cands = [{"title": "Resolve 2 TODO/FIXME marker(s) in a.py", "category": "tech-debt",
                  "source": "discovery", "priority": "low", "evidence": ["a.py:2", "a.py:3"]}]
        created = pl.propose_from_discovery(str(base), cands)
        assert len(created) == 1
        body = pathlib.Path(created[0]).read_text()
        assert "status: proposed" in body and "source: discovery" in body
        assert "a.py:2" in body and "a.py:3" in body     # locations preserved
        # dedup: re-proposing the same (category, file) writes nothing new, even with a changed count
        cands2 = [dict(cands[0], title="Resolve 5 TODO/FIXME marker(s) in a.py",
                       evidence=["a.py:2", "a.py:3", "a.py:9"])]
        assert pl.propose_from_discovery(str(base), cands2) == []


def test_propose_from_discovery_never_writes_the_marker_text():
    pl = _mod("pipeline")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True)
        # a real collector never puts marker text in the candidate; the title/evidence are location-only
        cands = [{"title": "Resolve 1 TODO/FIXME marker(s) in a.py", "category": "tech-debt",
                  "source": "discovery", "priority": "low", "evidence": ["a.py:1"]}]
        created = pl.propose_from_discovery(str(base), cands)
        assert "a.py:1" in pathlib.Path(created[0]).read_text()


def test_discover_end_to_end_and_fail_open():
    pl = _mod("pipeline")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
        (root / "a.py").write_text("# TODO: rotate AKIALEAK00000000000 soon\n")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
        base = root / ".sdlc"; (base / "goals").mkdir(parents=True)
        created = pl.discover(str(base), str(root))
        assert len(created) == 1
        body = pathlib.Path(created[0]).read_text()
        assert "status: proposed" in body and "AKIALEAK00000000000" not in body   # secret-safe end to end
        assert pl.discover(str(base), str(root)) == []                            # dedup

    with tempfile.TemporaryDirectory() as d2:      # non-git tree -> fail-open, proposes nothing
        base2 = pathlib.Path(d2) / ".sdlc"; (base2 / "goals").mkdir(parents=True)
        assert pl.discover(str(base2), str(d2)) == []


# ------------------------------------------------------------------ #139 Slice 4: site h (scan)
# Needs ledger.enabled AND journal.enabled (the Slice 0 AND-gate) for an events-stream write to
# actually land — see test_ledger.py's gate tests for the gate itself.

JOURNAL_ON = {"ledger": {"enabled": True, "actor": "rae"}, "journal": {"enabled": True}}


def _telemetry_project(root):
    base = root / ".sdlc"
    (base / "goals").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(JOURNAL_ON))
    return base


def test_discover_emits_a_scan_event_per_candidate():
    pl = _mod("pipeline")
    ledger = _mod("ledger")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
        # 25 TODOs in one file: the collector caps `evidence` at 10 but tracks the real count in `cnt`
        (root / "a.py").write_text("".join("# TODO line %d\n" % i for i in range(25)))
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
        base = _telemetry_project(root)
        pl.discover(str(base), str(root))
        events = [e for e in journal_events(ledger, str(base)) if e["kind"] == "scan"]
        assert len(events) == 1
        e = events[0]
        assert e["category"] == "tech-debt"
        assert e["file"] == "a.py"                 # stripped of the trailing :line
        assert e["count"] == 25                    # the REAL total, not len(evidence) (capped at 10)
        assert e["goal"] == "(discovery-scan)"


def test_discover_tolerates_a_malformed_candidate_without_raising(monkeypatch):
    """Independent review flag: Python evaluates call ARGUMENTS in the caller's frame, so a
    non-dict candidate makes `c.get(...)` raise AttributeError straight out of `discover()` unless
    each per-candidate body is individually guarded — a hole `discover()`'s own docstring
    ('Fail-open... never raises') predates this story but was never actually proven for a
    malformed candidate until now. Monkeypatching `ledger.append` to raise is NOT sufficient here
    since that is the case `safe_append` already covers — this needs a bad LIST ITEM, not a bad
    ledger."""
    pl = _mod("pipeline")

    class FakeProc:
        returncode = 0
        stdout = json.dumps({"schema": "discovery-scan/v1",
                             "candidates": ["not-a-dict", {"category": "tech-debt",
                                                            "evidence": ["a.py:1"]}]})
        stderr = ""

    monkeypatch.setattr(pl.subprocess, "run", lambda *a, **k: FakeProc())
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_project(pathlib.Path(d))
        created = pl.discover(str(base), d)         # must not raise
        assert isinstance(created, list) and len(created) == 1   # the one well-formed candidate survives


def test_discover_survives_a_raising_ledger_append(monkeypatch):
    """The site's fail-open test: would fail if `discover()` ever called `ledger.append` directly
    instead of `ledger.safe_append`."""
    pl = _mod("pipeline")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
        (root / "a.py").write_text("# TODO: rotate AKIALEAK00000000000 soon\n")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
        base = _telemetry_project(root)

        def raiser(*a, **k):
            raise RuntimeError("ledger broke")
        monkeypatch.setattr(pl.ledger, "append", raiser)
        created = pl.discover(str(base), str(root))
        assert len(created) == 1                     # the real discover outcome, unaffected by the raise

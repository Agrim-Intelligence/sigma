"""#2223: the contract text and docs that say what `release_dependents.py` does -- the `--land` fourth
exception (quoted, never reworded), the "no new key" claim, the unmeasured labels and the stated gaps."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _flat(path):
    return re.sub(r"\s+", " ", (ROOT / path).read_text(encoding="utf-8"))


SENTENCE = ("While a `--land` run is active, no goal other than the one it names may be picked, "
            "and no other goal is written to.")


def test_land_md_names_a_fourth_exception_limited_to_dependents():
    land = _flat("skills/sigma-goal/references/land.md")
    assert "Four exceptions, named rather than hidden" in land and "Three exceptions" not in land
    assert ("whose writes are limited to dependents of N (open `sdlc:blocked` goals that name N, only when every "
            "dependency they name is closed, never a parked goal): each loses `sdlc:blocked`, gets one audit note "
            "and, when a board is active, has its card moved to Ready; N itself loses `sdlc:blocking`") in land


def test_running_the_loop_says_four_exceptions():
    text = _flat("docs/running-the-loop.md")
    assert "Four exceptions, named in the skill" in text and "Three exceptions" not in text
    assert ("only when every dependency they name is closed, never a parked goal): each loses `sdlc:blocked`, "
            "gets one audit note and, when a board is active, has its card moved to Ready, and N itself loses "
            "`sdlc:blocking`") in text


def test_fourth_exception_fits_the_1200_char_window():
    after = _flat("skills/sigma-goal/references/land.md").split(SENTENCE, 1)[1][:1200]
    assert "ledger watcher" in after and "release_dependents.py" in after and "never a parked goal" in after


def test_cloud_sessions_says_release_is_rest_only_and_unmeasured():
    text = _flat("docs/cloud-sessions.md")
    assert "Releasing a closed goal's dependents" in text, "cloud-sessions.md has no release section (#2223)"
    section = text[text.index("Releasing a closed goal's dependents"):][:2500]
    assert "REST-only" in section and "unmeasured" in section and "never run in a real cloud container" in section


def test_cloud_sessions_names_the_crash_after_merge_gap():
    section = _flat("docs/cloud-sessions.md")
    assert "Releasing a closed goal's dependents" in section, "cloud-sessions.md has no release section (#2223)"
    section = section[section.index("Releasing a closed goal's dependents"):][:2500]
    assert "crash after `work.merge`" in section and "no watcher" in section and "loop.py next" in section


def test_label_model_says_parked_is_never_released():
    text = _flat("docs/label-model.md")
    assert "never from an `sdlc:parked` goal" in text and "released into no pool" in text


def test_configuration_names_no_new_key():
    text = _flat("docs/configuration.md")
    assert "no new key" in text
    assert "discovery.release_dependents" not in text and "discovery.release-dependents" not in text


def test_backlog_md_no_longer_claims_mode_is_the_only_key():
    text = _flat("docs/backlog.md")
    assert "`discovery.auto_unpark.mode` (`on` default | `off`) is the only key this feature reads" not in text
    for key in ("discovery.auto_unpark.interval_minutes", "discovery.dag.mode", "discovery.dag.ttl_minutes",
                "backlog_check.doctor_scan.max_issues", "SIGMA_WATCH_CALL_TIMEOUT"):
        assert key in text, key


def test_safety_posture_names_default_on_and_the_collision_inert_case():
    text = _flat("docs/backlog.md")
    assert "defaults ON" in text and "inert where `blocked_label` equals `goal_blocked_label`" in text
    assert "not measured" in text and "`--cap N`" in text


def test_backlog_states_the_exact_map_rule_and_the_scale_and_timeout_limits():
    text = _flat("docs/backlog.md")
    assert "never says \"still open\"" not in text, "the blanket map claim must go (#2223 finding 1)"
    assert "ignores its stale `still open` entry for N itself" in text
    assert "any other open blocker the map records still holds the goal" in text
    assert "the `record done` hook has no per-call timeout" in text
    assert "checked between goals" in text and "a hung REST read inside one goal is unbounded" in text
    assert "re-reads each blocked goal's comments and its refs' states" in text
    assert "up to about 100" in text and "unmeasured" in text


def test_backlog_says_the_watcher_records_refused_and_deferred():
    text = _flat("docs/backlog.md")
    assert "records `refused` or `deferred` in its state file and prints one stderr line" in text


def test_cloud_sessions_does_not_state_a_missing_map_as_fact():
    text = _flat("docs/cloud-sessions.md")
    assert "A cloud container has no dependency map" not in text
    assert "only when the ledger snapshot restored one" in text


def test_orchestrate_docs_say_the_wave_end_gesture_is_a_script_run_not_a_trigger():
    for path in ("docs/orchestrate.md", "skills/sigma-orchestrate/references/run.md"):
        text = _flat(path)
        assert "not an automatic trigger" in text, path
        assert "no orchestrate script calls it" in text, path


def test_backlog_md_names_every_edge_form_the_scoped_listing_cannot_see():
    """Review r2 finding 5: the listing path's prefilter matches only `#N` in the title or body; a dependent that
    names N by URL, `GH-N`, or only in a comment waits for the watcher's every-blocked-goal pass. Said in one place."""
    text = _flat("docs/backlog.md")
    assert "names the finished goal only by URL, as `GH-N`, or only in a comment" in text


def test_label_model_names_what_the_verb_writes_on_a_dependent_and_the_finished_goal():
    text = _flat("docs/label-model.md")
    assert ("posts one audit note and, when a board is active, moves its card to Ready; on the finished goal it "
            "strips `sdlc:blocking`") in text


def test_orchestrate_run_md_does_not_claim_the_verb_removes_only_a_label():
    text = _flat("skills/sigma-orchestrate/references/run.md")
    assert "removing only that label" not in text and "audit note" in text

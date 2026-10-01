"""Focused controls for the #348 merge-recovery drills."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "readiness" / "drills.py"


def _module():
    spec = importlib.util.spec_from_file_location("readiness_drills", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_mandatory_d1_seed_mapping_covers_every_actual_merge_checkpoint():
    drills = _module()

    assert drills.SEED_CHECKPOINTS == {
        1: "pre_write",
        2: "durable_remote_update",
        3: "post_response_before_ack",
        4: "pre_write",
        5: "durable_remote_update",
    }
    assert set(drills.SEED_CHECKPOINTS.values()) == set(drills.MERGE_CHECKPOINTS)
    with pytest.raises(drills.UsageError):
        drills.checkpoint_for_seed(6)


def test_evidence_refuses_filename_body_or_checkout_sha_mismatch(tmp_path):
    drills = _module()
    sha = "a" * 40
    payload = {"schema": drills.EVIDENCE_SCHEMA, "frozen_commit": sha, "runs": [],
               "command": [], "interpreter": sys.executable, "platform": "win32",
               "windows_skip_reason": drills.WINDOWS_SKIP_REASON,
               "b6": drills.B6_DISPOSITION}
    path = tmp_path / "drills-aaaaaaaaaaaa.json"

    drills.validate_evidence(path, payload, sha)
    with pytest.raises(drills.UsageError):
        drills.validate_evidence(path, {**payload, "frozen_commit": "b" * 40}, sha)
    with pytest.raises(drills.UsageError):
        drills.validate_evidence(tmp_path / "drills-bbbbbbbbbbbb.json", payload, sha)


def test_evidence_schema_requires_reproducibility_and_failure_disposition(tmp_path):
    drills = _module()
    sha = "a" * 40
    payload = {
        "schema": drills.EVIDENCE_SCHEMA, "frozen_commit": sha, "command": ["drills.py", "evidence"],
        "interpreter": sys.executable, "platform": "win32",
        "windows_skip_reason": drills.WINDOWS_SKIP_REASON, "b6": drills.B6_DISPOSITION,
        "runs": [],
    }
    drills.validate_evidence(tmp_path / "drills-aaaaaaaaaaaa.json", payload, sha)
    with pytest.raises(drills.UsageError):
        drills.validate_evidence(tmp_path / "drills-aaaaaaaaaaaa.json",
                                 {key: value for key, value in payload.items() if key != "b6"}, sha)


@pytest.mark.parametrize("seed,checkpoint", [
    (1, "pre_write"), (2, "durable_remote_update"), (3, "post_response_before_ack"),
])
def test_d1_kills_at_the_selected_actual_fake_merge_checkpoint(tmp_path, seed, checkpoint):
    drills = _module()

    result = drills.run_d1(tmp_path, ROOT, seed)

    assert result["fault"]["checkpoint"] == checkpoint
    assert result["fault"]["barrier"]["checkpoint"] == checkpoint
    assert all(item["passed"] for item in result["invariants"])


def test_d1_records_the_real_work_merge_and_recovery_command_paths(tmp_path):
    """D1's recovery receipts must come from the killed fixture, not a preflight."""
    drills = _module()

    result = drills.run_d1(tmp_path, ROOT, 1)

    lifecycle = result["lifecycle"]
    assert lifecycle["killed_work_merge"]["returncode"] < 0
    assert lifecycle["killed_work_merge"]["argv"][1].endswith("work.py")
    # Seed 1 faults before the remote write, so the same fixture recovers by
    # retrying the real lifecycle, not by directly invoking the fake gh tool.
    assert lifecycle["retry_work_merge"]["returncode"] == 0
    assert "merged" in lifecycle["retry_work_merge"]["stdout"]
    assert lifecycle["next"]["returncode"] == 0
    assert lifecycle["reconcile_merges"]["returncode"] == 0
    assert lifecycle["second_reconcile_merges"]["returncode"] == 0
    assert lifecycle["fault_fixture"] == lifecycle["recovery_fixture"]
    assert lifecycle["fault_fixture"].endswith("fixture")
    assert lifecycle["fake_github"]["prs"]["100"]["state"] == "MERGED"
    assert lifecycle["fake_gh_unhandled"] == ""


def test_d2_restored_awaiting_merge_state_prevents_repick_and_converges_once(tmp_path):
    drills = _module()
    result = drills.run_d2(tmp_path, ROOT, 1)
    by_name = {item["name"]: item for item in result["invariants"]}
    assert result["fault"]["external_in_progress_removed"] is True
    assert by_name["next_does_not_repick_restored_goal"]["passed"] is True
    assert by_name["done_recorded_exactly_once"]["passed"] is True
    assert by_name["external_and_local_terminal_state_converge"]["passed"] is True
    assert result["lifecycle"]["fault_fixture"] == result["lifecycle"]["recovery_fixture"]
    assert result["lifecycle"]["fake_github"]["issues"]["1"]["state"] == "closed"
    assert result["lifecycle"]["worktree_exists"] is False


def test_d2_uses_the_public_next_and_reconcile_lifecycle_after_overlay_loss(tmp_path):
    drills = _module()

    result = drills.run_d2(tmp_path, ROOT, 1)

    lifecycle = result["lifecycle"]
    assert lifecycle["next"]["returncode"] == 0
    assert lifecycle["reconcile_merges"]["returncode"] == 0
    assert lifecycle["fake_gh_unhandled"] == ""


def test_d3_runs_documented_reconcile_twice_and_records_done_once(tmp_path):
    drills = _module()
    result = drills.run_d3(tmp_path, ROOT, 1)
    assert result["recovery_commands"] == [
        ["loop.py", "reconcile-merges", "<dir>"],
        ["loop.py", "reconcile-merges", "<dir>"],
    ]
    assert all(item["passed"] for item in result["invariants"])


def test_d3_records_real_reconcile_passes_after_the_fake_merge_lifecycle(tmp_path):
    drills = _module()

    result = drills.run_d3(tmp_path, ROOT, 1)

    lifecycle = result["lifecycle"]
    assert lifecycle["fake_merge"]["returncode"] < 0
    assert lifecycle["fake_merge"]["remote_merge_durable"] is True
    assert lifecycle["fake_merge"]["remote_merge_response_emitted"] is True
    assert lifecycle["first_reconcile"]["returncode"] == 0
    assert lifecycle["second_reconcile"]["returncode"] == 0
    assert lifecycle["fault_fixture"] == lifecycle["recovery_fixture"]
    assert lifecycle["fake_github"]["prs"]["100"]["state"] == "MERGED"
    assert lifecycle["worktree_exists"] is False
    assert lifecycle["fake_gh_unhandled"] == ""


def test_public_cli_dispatches_d1_to_its_real_runner(tmp_path):
    output = tmp_path / "d1.json"
    proc = subprocess.run([sys.executable, str(SCRIPT), "run", "D1", "--seed", "1",
                           "--workdir", str(tmp_path / "run"), "--json", str(output)],
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(output.read_text())["drill"] == "D1"


def test_documented_d1_gesture_measures_a_seeded_delay_and_kills_work_merge(tmp_path):
    """The published ``run D1`` gesture must fault the real work.py merge lifecycle.

    The duration bound is deliberately asserted from the public JSON receipt so a
    future change cannot retain only a static seed-to-checkpoint mapping while
    silently dropping the measured fault window.
    """
    output = tmp_path / "d1.json"
    proc = subprocess.run([sys.executable, str(SCRIPT), "run", "D1", "--seed", "2",
                           "--workdir", str(tmp_path / "run"), "--json", str(output)],
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(output.read_text())

    fault = result["fault"]
    assert 0 < fault["measured_work_merge_duration_ns"]
    assert 0 < fault["seeded_delay_ns"] <= fault["measured_work_merge_duration_ns"]
    killed = result["lifecycle"]["killed_work_merge"]
    assert killed["returncode"] < 0
    assert killed["argv"][1].endswith("work.py")
    assert result["lifecycle"]["fault_fixture"] == result["lifecycle"]["recovery_fixture"]


@pytest.mark.parametrize("duration", [1, 999_999, 1_000_000, 100_000_001])
def test_d1_seeded_delay_never_exceeds_the_measured_merge_duration(duration):
    """A fast host must not turn D1's lower delay preference into an overrun."""
    drills = _module()

    delay = drills.seeded_delay_ns(2, duration)

    assert 0 < delay <= duration


def test_d2_receipt_proves_terminal_action_log_work_record_cursor_and_board_convergence(tmp_path):
    """D2 must inspect the actual restored fixture, not infer terminal state from the issue."""
    drills = _module()

    result = drills.run_d2(tmp_path, ROOT, 1)

    convergence = result["lifecycle"]["convergence"]
    assert convergence["pr_state"] == "MERGED"
    assert convergence["issue_state"] == "closed"
    assert convergence["labels"] == []
    assert convergence["action_log_done_count"] == 1
    assert convergence["work_record_exists"] is False
    assert convergence["worktree_exists"] is False
    assert convergence["cursor"] == "1 -> done"
    assert convergence["board"] == {"configured": True, "project_number": 1,
                                    "item_id": "PITEM_1", "status": "Done",
                                    "mirror_state": "closed"}


def test_d3_kills_the_actual_work_merge_parent_after_fake_merge_then_reconciles_once(tmp_path):
    """D3's lost-ack fault belongs to work.py, never a standalone fake-gh helper."""
    drills = _module()

    result = drills.run_d3(tmp_path, ROOT, 1)

    fault = result["lifecycle"]["fake_merge"]
    convergence = result["lifecycle"]["convergence"]
    assert fault["returncode"] < 0
    assert fault["argv"][1].endswith("work.py")
    assert fault["remote_merge_durable"] is True
    assert fault["fake_gh_returned_success"] is True
    assert fault["parent_killed_after_success_before_ack"] is True
    assert convergence["action_log_done_count"] == 1
    assert convergence["work_record_exists"] is False
    assert convergence["worktree_exists"] is False


def test_d2_and_d3_converge_the_configured_fake_projects_v2_card_to_done(tmp_path):
    """The recovery receipt must read the fake board, not a local mirror.

    The fixture configures a real fake Projects-v2 board/card before the
    lifecycle begins.  D2 restores local state and D3 loses the parent only
    after fake-gh has returned merge success; both recovery paths must then
    read the configured card back as Done.
    """
    drills = _module()

    d2 = drills.run_d2(tmp_path / "d2", ROOT, 1)
    d3 = drills.run_d3(tmp_path / "d3", ROOT, 1)

    for result in (d2, d3):
        convergence = result["lifecycle"]["convergence"]
        assert convergence["board"]["configured"] is True
        assert convergence["board"]["project_number"] == 1
        assert convergence["board"]["item_id"] == "PITEM_1"
        assert convergence["board"]["status"] == "Done"
        assert result["lifecycle"]["fake_github"]["projects"][0]["items"]["PITEM_1"]["values"]["FIELD_STATUS"] == "OPT_DONE"

    fault = d3["lifecycle"]["fake_merge"]
    assert fault["fake_gh_returned_success"] is True
    assert fault["parent_killed_after_success_before_ack"] is True


def test_public_b6_disposition_persists_dedup_without_github_write(tmp_path):
    output = tmp_path / "b6.json"
    finding = tmp_path / "finding.txt"
    finding.write_text("D4 stop-file reporting control failed for seed 1.\n")
    proc = subprocess.run([sys.executable, str(SCRIPT), "disposition", "--finding", str(finding),
                           "--sdlc", str(tmp_path / ".sdlc"), "--json", str(output)],
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(output.read_text())
    assert result["schema"] == "readiness-drill-b6/v1"
    assert result["disposition"] == "no-github-write"


def test_public_frozen_all_seed_evidence_run_builds_validated_schema(tmp_path):
    """The documented evidence gesture must produce the complete frozen run."""
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                         text=True, capture_output=True, check=True).stdout.strip()
    output = tmp_path / ("drills-" + sha[:12] + ".json")
    proc = subprocess.run([sys.executable, str(SCRIPT), "evidence", "--workdir",
                           str(tmp_path / "runs"), "--json", str(output)],
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr

    drills = _module()
    payload = json.loads(output.read_text())
    assert output.name == "drills-" + sha[:12] + ".json"
    drills.validate_evidence(output, payload, sha)
    assert payload["command"] == ["drills.py", "evidence", "--workdir", "<workdir>",
                                  "--json", "drills-" + sha[:12] + ".json"]
    assert {(run["drill"], run["seed"]) for run in payload["runs"]} == {
        (drill, seed) for drill in ("D1", "D2", "D3", "D4") for seed in range(1, 6)
    }
    assert all("checkpoint" in run and "invariants" in run for run in payload["runs"])
    public_evidence = json.dumps(payload)
    assert "/Users/" not in public_evidence
    assert ".codex/config.toml" not in public_evidence
    assert ".claude/settings.json" not in public_evidence
    d4 = [run for run in payload["runs"] if run["drill"] == "D4"]
    assert all(run["expected_failure_states"] == {
        "doctor_explicit_stop_file_reporting": "control_failed_and_recorded",
        "session_start_explicit_stop_file_reporting": "control_failed_and_recorded",
    } for run in d4)


def test_d4_runs_real_stop_file_daemon_and_records_two_expected_reporting_failures(tmp_path):
    drills = _module()

    result = drills.run_d4(tmp_path, ROOT)

    assert result["drill"] == "D4"
    assert result["fault"]["daemon"]["returncode"] == 0
    assert "stop-file present" in result["fault"]["daemon"]["stdout"]
    assert result["fault"]["stop_file_exists"] is True
    by_name = {entry["name"]: entry for entry in result["invariants"]}
    assert by_name["doctor_explicit_stop_file_reporting"]["passed"] is False
    assert by_name["session_start_explicit_stop_file_reporting"]["passed"] is False
    assert by_name["doctor_explicit_stop_file_reporting"]["observed"]["outcome"] == "control_failed_and_recorded"
    assert by_name["session_start_explicit_stop_file_reporting"]["observed"]["outcome"] == "control_failed_and_recorded"
    assert result["dedup"]["schema"] == "brainstorm-dedup/v1"
    json.dumps(result)


def test_public_d4_evidence_redacts_host_configuration_locations(tmp_path):
    """A host-path leak in a doctor/session transcript must not reach frozen evidence."""
    drills = _module()

    result = drills.run_d4(tmp_path, ROOT, 1)
    doctor = next(item for item in result["invariants"]
                  if item["name"] == "doctor_explicit_stop_file_reporting")
    doctor["observed"]["stdout"] += (
        "\nhost guidance: /Users/alice/.codex/config.toml "
        "and /Users/alice/.claude/settings.json; "
        "C:\\Users\\alice\\.codex\\config.toml\n"
    )

    public_run = drills._evidence_run(result)
    transcript = json.dumps(public_run)
    public_doctor = next(item for item in public_run["invariants"]
                         if item["name"] == "doctor_explicit_stop_file_reporting")
    public_stdout = public_doctor["observed"]["stdout"]

    assert "/Users/" not in transcript
    assert ".codex/config.toml" not in transcript
    assert ".claude/settings.json" not in transcript
    assert "C:\\Users\\" not in public_stdout


def test_public_evidence_redacts_absolute_temporary_paths_in_all_receipt_fields(tmp_path):
    """Scratch checkout paths must not survive argv, stderr, or D4 observations.

    The frozen drill runner deliberately uses isolated temporary directories.
    Those locations are machine-specific just like a home directory, and a
    public evidence record must not expose them while changing neither a
    control's pass/fail meaning nor its frozen commit identifier.
    """
    drills = _module()
    result = drills.run_d4(tmp_path, ROOT, 1)
    scratch = "/private/tmp/sigma348-frozen.zfVoLg/checkout"
    doctor = next(item for item in result["invariants"]
                  if item["name"] == "doctor_explicit_stop_file_reporting")
    doctor["observed"].update({
        "argv": [scratch + "/skills/agrim-loop/scripts/work.py", "merge"],
        "stderr": "removed " + scratch + "/runs/d4-seed-1/fixture",
        "path": scratch + "/d4-repo/.sdlc/state/watch.stop",
    })
    result["fault"]["daemon"]["stderr"] = "scratch=" + scratch + "/daemon.log"

    public_run = drills._evidence_run(result)
    transcript = json.dumps(public_run)
    public_doctor = next(item for item in public_run["invariants"]
                         if item["name"] == "doctor_explicit_stop_file_reporting")

    assert scratch not in transcript
    assert "/private/tmp/" not in transcript
    assert public_doctor["passed"] is False
    assert public_doctor["observed"]["outcome"] == "control_failed_and_recorded"
    assert result["frozen_commit"] == drills._head(ROOT)


def test_public_evidence_redacts_retired_plugin_warning_text(tmp_path):
    """Public readiness receipts must pass the repository's strict-name scan."""
    drills = _module()
    result = drills.run_d4(tmp_path, ROOT, 1)
    doctor = next(item for item in result["invariants"]
                  if item["name"] == "doctor_explicit_stop_file_reporting")
    retired_name = "Loop" + "Smith"
    doctor["observed"]["stdout"] += "\nlegacy plugin: " + retired_name + " remains installed\n"

    public_run = drills._evidence_run(result)
    transcript = json.dumps(public_run).lower()

    assert "loop" + "smith" not in transcript
    assert "<retired-plugin>" in transcript

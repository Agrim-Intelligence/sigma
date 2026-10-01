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
    assert lifecycle["work_merge"]["returncode"] == 0
    assert "review gate passed" in lifecycle["work_merge"]["stdout"]
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
    assert lifecycle["fake_merge"]["remote_merge_returncode"] == 0
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

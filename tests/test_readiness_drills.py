"""Focused controls for the #348 merge-recovery drills."""
import importlib.util
import json
from pathlib import Path

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
    payload = {"schema": drills.EVIDENCE_SCHEMA, "frozen_commit": sha, "runs": []}
    path = tmp_path / "drills-aaaaaaaaaaaa.json"

    drills.validate_evidence(path, payload, sha)
    with pytest.raises(drills.UsageError):
        drills.validate_evidence(path, {**payload, "frozen_commit": "b" * 40}, sha)
    with pytest.raises(drills.UsageError):
        drills.validate_evidence(tmp_path / "drills-bbbbbbbbbbbb.json", payload, sha)


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

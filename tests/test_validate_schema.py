#!/usr/bin/env python3
"""Tests for contract validation."""
import json
import pathlib
import subprocess
import sys
import tempfile

import pytest


def run_validator(file_path):
    """Run the validator on a file and return (exit_code, stdout, stderr)."""
    repo_root = pathlib.Path(__file__).parent.parent
    validator = repo_root / "contract" / "validate.py"

    result = subprocess.run(
        [sys.executable, str(validator), str(file_path)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )

    return result.returncode, result.stdout, result.stderr


def test_validate_passes_on_golden_entries():
    """Test that validator passes on golden entries.jsonl (once created)."""
    repo_root = pathlib.Path(__file__).parent.parent
    golden_entries = repo_root / "contract" / "golden" / "entries.jsonl"

    if not golden_entries.exists():
        pytest.skip("golden/entries.jsonl not yet created")

    exit_code, stdout, stderr = run_validator(golden_entries)
    assert exit_code == 0, f"Expected exit 0, got {exit_code}. stderr: {stderr}"


def test_validate_passes_on_golden_events():
    """Test that validator passes on golden events.jsonl (once created)."""
    repo_root = pathlib.Path(__file__).parent.parent
    golden_events = repo_root / "contract" / "golden" / "events.jsonl"

    if not golden_events.exists():
        pytest.skip("golden/events.jsonl not yet created")

    exit_code, stdout, stderr = run_validator(golden_events)
    assert exit_code == 0, f"Expected exit 0, got {exit_code}. stderr: {stderr}"


def test_validate_rejects_invalid_merge_observation_key(tmp_path):
    event = tmp_path / "events.jsonl"
    event.write_text(json.dumps({
        "id": "a:1:1", "ts": "2026-01-01T00:00:00Z", "actor": "a", "goal": "2577",
        "kind": "merge_observed", "observation_key": "bad", "subject_kind": "goal",
        "subject": "2577", "pr": 1, "merge_sha": "a" * 40,
    }) + "\n")
    code, _out, err = run_validator(event)
    assert code != 0 and "observation_key" in err


@pytest.mark.parametrize(
    "merged_entry_key, expected_error",
    [
        ("A" * 64, "merged_entry_key must be 64 lowercase hexadecimal characters"),
        ("a" * 63, "merged_entry_key must be 64 lowercase hexadecimal characters"),
        ("g" * 64, "merged_entry_key must be 64 lowercase hexadecimal characters"),
    ],
)
def test_validate_rejects_a_present_merged_entry_key_with_an_invalid_shape(
    tmp_path, merged_entry_key, expected_error,
):
    """A malformed new stable identity must never be accepted as a valid one."""
    entry = {
        "id": "a:1", "ts": "2026-01-01T00:00:00Z", "actor": "a", "goal": "2577",
        "kind": "merged", "pr": 1,
    }
    if merged_entry_key is not None:
        entry["merged_entry_key"] = merged_entry_key
    entries = tmp_path / "entries.jsonl"
    entries.write_text(json.dumps(entry) + "\n")

    code, _out, err = run_validator(entries)

    assert code != 0
    assert expected_error in err


def test_validate_keeps_historical_merged_rows_compatible(tmp_path):
    """Minor contract upgrades cannot make already-written merged history unreadable."""
    entries = tmp_path / "entries.jsonl"
    entries.write_text(json.dumps({"id": "a:1", "ts": "2025-01-01T00:00:00Z", "actor": "a",
                                   "goal": "2577", "kind": "merged", "pr": 1}) + "\n")
    code, _out, err = run_validator(entries)
    assert code == 0, err


def test_validate_keeps_historical_non_merged_entries_compatible(tmp_path):
    """The merged-only schema must not retroactively require a key on lifecycle history."""
    entries = tmp_path / "entries.jsonl"
    entries.write_text(json.dumps({
        "id": "a:1", "ts": "2026-01-01T00:00:00Z", "actor": "a", "goal": "2577",
        "kind": "done",
    }) + "\n")

    code, _out, err = run_validator(entries)

    assert code == 0, err


@pytest.mark.parametrize("kind", ["review_posted", "ci_observed"])
def test_validate_rejects_incomplete_typed_observations(tmp_path, kind):
    event = tmp_path / "events.jsonl"
    event.write_text(json.dumps({"id": "a:1", "ts": "2026-01-01T00:00:00Z", "actor": "a",
                                 "goal": "2577", "kind": kind}) + "\n")
    code, _out, err = run_validator(event)
    assert code != 0 and "observation_key" in err
    assert "head_sha" in err and "pr must be positive" in err


def test_validate_accepts_an_unblock_review_observation(tmp_path):
    event = tmp_path / "events.jsonl"
    event.write_text(json.dumps({
        "id": "a:1", "ts": "2026-01-01T00:00:00Z", "actor": "a", "goal": "2577",
        "kind": "review_posted", "observation_key": "a" * 64, "brief_hash": "b" * 64,
        "evidence_id": "review_evidence_0001", "comment_id": 1, "pr": 1,
        "head_sha": "c" * 40, "verdict": "unblock",
    }) + "\n")
    code, _out, err = run_validator(event)
    assert code == 0, err


def test_validate_passes_on_real_ledger_entries():
    """Test that validator passes on a real entry file from .sdlc/ledger/entries/."""
    repo_root = pathlib.Path(__file__).parent.parent
    ledger_dir = repo_root / ".sdlc" / "ledger" / "entries"

    if not ledger_dir.exists():
        pytest.skip("ledger entries directory not found")

    # Find the first real entry file
    entry_files = list(ledger_dir.glob("*.jsonl"))
    if not entry_files:
        pytest.skip("no ledger entry files found")

    entry_file = entry_files[0]
    exit_code, stdout, stderr = run_validator(entry_file)
    assert exit_code == 0, f"Expected exit 0 for {entry_file}, got {exit_code}. stderr: {stderr}"


def test_validate_fails_on_corrupt_kind():
    """Test that validator fails when kind is invalid."""
    repo_root = pathlib.Path(__file__).parent.parent

    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        # Write a line with an invalid kind
        json.dump({
            "id": "test:1:1",
            "actor": "testactor",
            "timestamp": "2026-01-01T00:00:00Z",
            "kind": "nonexistent"
        }, f)
        f.write("\n")
        temp_path = f.name

    try:
        exit_code, stdout, stderr = run_validator(temp_path)
        assert exit_code != 0, f"Expected non-zero exit code, got {exit_code}"
        assert "unknown" in stderr.lower() or "nonexistent" in stderr.lower(), \
            f"Expected error about invalid kind, got: {stderr}"
    finally:
        pathlib.Path(temp_path).unlink()


def test_validate_fails_on_missing_kind_field():
    """Test that validator fails when kind field is missing."""
    repo_root = pathlib.Path(__file__).parent.parent

    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        # Write a line without kind field
        json.dump({
            "id": "test:1:1",
            "actor": "testactor",
            "timestamp": "2026-01-01T00:00:00Z",
        }, f)
        f.write("\n")
        temp_path = f.name

    try:
        exit_code, stdout, stderr = run_validator(temp_path)
        assert exit_code != 0, f"Expected non-zero exit code, got {exit_code}"
        assert "kind" in stderr.lower(), f"Expected error about missing kind, got: {stderr}"
    finally:
        pathlib.Path(temp_path).unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])


def _golden_line(kind, stream):
    """One real golden line of `kind`, asserted present so the control can never run on nothing."""
    path = pathlib.Path(__file__).parent.parent / "contract" / "golden" / (stream + ".jsonl")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and json.loads(line).get("kind") == kind:
            return line + "\n"
    raise AssertionError("no %r line in golden/%s.jsonl -- the control would be vacuous" % (kind, stream))


@pytest.mark.parametrize("layout", ["stem", "directory"])
@pytest.mark.parametrize("kind,home,wrong", [("merged", "entries", "events"),
                                             ("merge_observed", "events", "entries")])
def test_validate_rejects_a_well_formed_record_in_the_wrong_stream(tmp_path, layout, kind, home, wrong):
    """A kind that is well-formed for ITS stream must still be refused in the other one.

    Dispatching on `kind` alone let a `merged` entry validate inside events.jsonl, so the
    per-stream membership contract/README.md documents was unenforced. Both real layouts are
    covered: `golden/<stream>.jsonl` names the stream in its stem, and the live ledger's
    `<stream>/<actor>-<host>.<pid>.jsonl` names it only in the parent directory.
    """
    line = _golden_line(kind, home)
    assert line.strip(), "golden line is empty -- the control would be vacuous"

    def placed(stream):
        if layout == "stem":
            target = tmp_path / stream / (stream + ".jsonl")
        else:
            target = tmp_path / stream / "alice-e4c9c89f.44752.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(line, encoding="utf-8")
        return target

    code, _, err = run_validator(placed(wrong))
    assert code == 1, "a %r record in the %s stream was accepted" % (kind, wrong)
    assert "cannot appear in the %s stream" % wrong in err

    # Non-vacuity: the very same bytes in their OWN stream must still validate, so the refusal
    # above is about placement and not about a line the validator rejects everywhere.
    code, _, err = run_validator(placed(home))
    assert code == 0, err

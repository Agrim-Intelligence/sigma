# SPDX-License-Identifier: MIT
"""Engine-side half of the data contract (issue #298, [E15.S4]): proves the engine still WRITES
what contract/vocabulary.json declares, and that an unknown field is still ignorable by
an older reader (clause 4). Zero reference to the private side anywhere in this file — clause 3's own
requirement, the same posture tests/test_ledger.py's vocabulary pin already keeps."""
import importlib.util
import json
import pathlib

import pytest
from journal_events import journal_events

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _mod("ledger")
ON = {"ledger": {"enabled": True, "actor": "dana"}, "journal": {"enabled": True}}

_TYPED_EVENT_FIELDS = {
    "merge_observed": {"observation_key": "a" * 64, "subject_kind": "goal", "subject": "g.md",
                         "pr": 1, "merge_sha": "b" * 40},
    "review_posted": {"observation_key": "a" * 64, "brief_hash": "b" * 64,
                      "evidence_id": "evidence_identifier", "comment_id": 1, "pr": 1,
                      "head_sha": "c" * 40, "verdict": "approve"},
    "ci_observed": {"observation_key": "a" * 64, "pr": 1, "head_sha": "b" * 40,
                    "gate_verdict": "pass", "checks_total": 1, "checks_truncated": False,
                    "checks": [{"name": "unit", "conclusion": "pass"}]},
}


def _sdlc(tmp_path):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(ON))
    return d


@pytest.mark.parametrize("kind", ledger.KINDS)
def test_every_entries_kind_round_trips(tmp_path, kind):
    """Positive proof, entries stream: every KINDS member round-trips through the real
    append() + read_all() path with only whitelisted fields present -- the engine still emits
    exactly what the contract's vocabulary.json declares under "entries_kinds"."""
    d = _sdlc(tmp_path)
    fields = {"merged_entry_key": "a" * 64} if kind == "merged" else {}
    ledger.append(d, ON, kind, "g.md", **fields)
    records = ledger.read_all(d)
    assert records and records[-1]["kind"] == kind
    assert set(records[-1]) <= {"id", "ts", "actor", "kind", "goal"} | set(ledger.OPTIONAL_FIELDS)


@pytest.mark.parametrize("kind", ledger.EVENT_KINDS)
def test_every_event_kind_round_trips(tmp_path, kind):
    """Same proof, events stream: every EVENT_KINDS member round-trips, and only that kind's own
    EVENT_FIELDS whitelist may appear -- the contract's "event_kinds"."""
    d = _sdlc(tmp_path)
    ledger.append(d, ON, kind, "g.md", stream=ledger.EVENTS, **_TYPED_EVENT_FIELDS.get(kind, {}))
    records = journal_events(ledger, d)
    assert records and records[-1]["kind"] == kind
    assert set(records[-1]) <= {"id", "ts", "actor", "kind", "goal"} | set(ledger.EVENT_FIELDS[kind])


def test_an_unknown_field_written_by_a_newer_writer_is_ignored_not_fatal(tmp_path):
    """ledger.py's own "additive by design" contract (see OPTIONAL_FIELDS' docstring), actually
    exercised: a line written with a field this version of ledger.py has never heard of
    (simulating a newer engine version's writer) must still parse -- the record survives, the
    unknown field just rides along unused. Nothing in tests/test_ledger.py asserted this before
    this goal (grepped, empty)."""
    d = _sdlc(tmp_path)
    path = ledger.entry_file(d, "dana", ledger.ENTRIES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "id": "dana:1", "ts": "2026-08-01T00:00:00Z", "actor": "dana", "kind": "note", "goal": "g",
        "a_field_from_a_future_engine_version": "must not be fatal",
    }) + "\n")
    records = ledger.read_all(d)
    assert len(records) == 1 and records[0]["kind"] == "note"
    # Survives INTACT, not merely survives: without this line the test passes even if read_all()
    # strips every unknown key, which is the opposite of the additive contract it claims to prove
    # (verified by mutation -- stripping unknown fields left the assertion above green).
    assert records[0]["a_field_from_a_future_engine_version"] == "must not be fatal"

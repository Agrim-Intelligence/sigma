"""The doctor row for follow-ups filed with no recorded reason (#2224).

Run: python3 -m pytest tests/test_followup_doctor_row.py
(generic form: python -m pytest tests/test_followup_doctor_row.py)

What it sees: only OPEN issues carrying `sdlc:followup`, through ONE capped read (200). A direct `gh issue create` with no
label and a closed leak are invisible. `handoff open` and `feature_rebase` filings carry the label with no marker and
count as unrecorded. The cutoff is the earliest `createdAt` among marker-bearing issues IN THAT SAME READ (no local file).
CONTROLS: C21a test_historical_issue_not_flagged (created-before-cutoff clause), C21b test_earliest_marker_sets_the_cutoff
(earliest-marker order, weakened to "any marker"), C22 test_ceiling_is_in_the_row_name / test_list_failure_adds_no_row,
C36 test_file_mode_row_not_applicable.
"""
import importlib.util
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills"
M = "<!-- sigma:followup-reason:slice-cap -->"


def _mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fr = _mod("followup_route", S / "sigma-loop" / "scripts" / "followup_route.py")
doctor = _mod("doctor", S / "sigma-doctor" / "scripts" / "doctor.py")


def issue(n, at, marked=False):
    return {"number": n, "createdAt": at, "body": "b" + (M if marked else "")}


def test_historical_issue_not_flagged():
    rows = [issue(1, "2026-01-01T00:00:00Z"), issue(2, "2026-06-01T00:00:00Z", True), issue(3, "2026-07-01T00:00:00Z")]
    cutoff, flagged = fr.unrecorded(rows, "in-goal")
    assert cutoff == "2026-06-01T00:00:00Z" and flagged == [3]


def test_earliest_marker_sets_the_cutoff():
    rows = [issue(5, "2026-08-01T00:00:00Z", True), issue(4, "2026-03-01T00:00:00Z", True),
            issue(3, "2026-05-01T00:00:00Z"), issue(2, "2026-02-01T00:00:00Z")]
    cutoff, flagged = fr.unrecorded(rows, "in-goal")
    assert cutoff == "2026-03-01T00:00:00Z" and flagged == [3]


def test_no_marker_anywhere_flags_nothing():
    assert fr.unrecorded([issue(1, "2026-01-01T00:00:00Z")], "in-goal") == (None, [])


def test_file_mode_row_not_applicable():
    rows = [issue(1, "2026-01-01T00:00:00Z", True), issue(2, "2026-07-01T00:00:00Z")]
    assert fr.unrecorded(rows, "file") == (None, [])


def _row(monkeypatch, rows, cfg=None):
    monkeypatch.setattr(doctor, "_list", lambda *a, **k: rows)
    return doctor._followup_reason_row(cfg or {}, {}, None)


def test_ceiling_is_in_the_row_name(monkeypatch):
    row = _row(monkeypatch, [issue(1, "2026-01-01T00:00:00Z", True), issue(2, "2026-02-01T00:00:00Z")])
    assert "/200" in row["name"] and not row["ok"] and "#2" in row["fix"]
    assert "handoff open" in row["fix"] and "feature_rebase" in row["fix"]


def test_flagged_list_is_truncated_with_more(monkeypatch):
    rows = [issue(1, "2026-01-01T00:00:00Z", True)] + [issue(n, "2026-02-01T00:00:00Z") for n in range(2, 16)]
    row = _row(monkeypatch, rows)
    assert "+4 more" in row["fix"]


def test_exactly_the_cap_says_there_may_be_more(monkeypatch):
    rows = [issue(1, "2026-01-01T00:00:00Z", True), issue(2, "2026-02-01T00:00:00Z")] + \
        [issue(n, "2026-01-01T00:00:00Z") for n in range(3, 201)]
    assert "may be more" in _row(monkeypatch, rows)["fix"]


def test_list_failure_adds_no_row(monkeypatch):
    assert _row(monkeypatch, None) is None


def test_file_mode_row_is_ok_and_says_not_applicable(monkeypatch):
    row = _row(monkeypatch, [issue(1, "2026-01-01T00:00:00Z", True), issue(2, "2026-02-01T00:00:00Z")],
               {"followups": {"mode": "file"}})
    assert row["ok"] and "not applicable" in row["name"]


def test_not_yet_in_use_is_ok(monkeypatch):
    row = _row(monkeypatch, [issue(1, "2026-01-01T00:00:00Z")])
    assert row["ok"] and "not yet in use" in row["name"]

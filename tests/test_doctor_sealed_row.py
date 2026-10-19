"""#850: doctor row `sealed tests: N sealed, M drifted ...` -- read-only, local files only.

Walks the LIVE work records (a few per slot), not every seal record ever written. Controls: the absent-when-none
clause, per-record isolation (one raising record must not hide another's drift), the unchecked-not-drift rule for
a record whose worktree is gone, the 200 cap, and that the row writes nothing."""
import hashlib
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import test_doctor as td  # noqa: E402

SRC = b'def test_one():\n    assert False\n'


def _rows(tmp_path, goals=(), cfg=None, mutate=None):
    """goals: [(stem, worktree_state)] with worktree_state in clean|drifted|gone|unsealed|badrecord."""
    base = pathlib.Path(td._sdlc(tmp_path, cfg or {}))
    for i, (stem, kind) in enumerate(goals):
        wt = tmp_path / f"wt{stem}"
        d = wt / "tests" / "acceptance" / stem
        d.mkdir(parents=True)
        f = d / "test_x.py"
        f.write_bytes(SRC if kind != "drifted" else SRC + b"# edited\n")
        (base / "state" / "work").mkdir(parents=True, exist_ok=True)
        (base / "state" / "work" / f"{stem}.json").write_text(json.dumps({"worktree": str(wt)}))
        if kind != "unsealed":
            (base / "state" / "sealed").mkdir(parents=True, exist_ok=True)
            rel = f"tests/acceptance/{stem}/test_x.py"
            rec = {"schema": "sigma-sealed/1", "goal": stem, "generation": 1,
                   "files": {rel: {"sha256": hashlib.sha256(SRC).hexdigest(), "bytes": len(SRC)}}}
            text = "{nope" if kind == "badrecord" else json.dumps(rec)
            (base / "state" / "sealed" / f"{stem}.json").write_text(text)
        if kind == "gone":
            f.unlink()
            d.rmdir()
            for p in (wt / "tests" / "acceptance", wt / "tests", wt):
                p.rmdir()
        os.utime(base / "state" / "work" / f"{stem}.json", (1000 + i, 1000 + i))
    if mutate:
        mutate(base)
    got = td._doc().check(str(base), run=td._runner())
    return base, [c for c in got if c["name"].startswith("sealed")]


def test_row_absent_when_no_goal_is_sealed(tmp_path):
    assert _rows(tmp_path)[1] == []
    assert _rows(tmp_path / "b", goals=[("11", "unsealed")])[1] == []


def test_clean_is_ok(tmp_path):
    rows = _rows(tmp_path, goals=[("11", "clean"), ("12", "clean")])[1]
    assert len(rows) == 1 and rows[0]["ok"] is True and "2 sealed" in rows[0]["name"]
    assert "drifted" not in rows[0]["name"]


def test_drifted_is_not_ok_naming_goal_path_and_the_fix(tmp_path):
    rows = _rows(tmp_path, goals=[("11", "clean"), ("12", "drifted")])[1]
    row = rows[0]
    assert row["ok"] is False and "1 drifted" in row["name"]
    assert "#12" in row["name"] and "differs tests/acceptance/12/test_x.py" in row["name"]
    assert "git -C" in row["fix"] and "park" in row["fix"]


def test_a_record_whose_worktree_is_gone_is_unchecked_not_drift(tmp_path):
    row = _rows(tmp_path, goals=[("11", "gone"), ("12", "clean")])[1][0]
    assert row["ok"] is True and "1 unchecked" in row["name"] and "drifted" not in row["name"]


def test_one_unreadable_record_does_not_hide_anothers_drift(tmp_path):
    row = _rows(tmp_path, goals=[("11", "badrecord"), ("12", "drifted")])[1][0]
    assert "1 unreadable" in row["name"] and "1 drifted" in row["name"] and row["ok"] is False


def test_an_unreadable_seal_record_is_not_ok_and_named(tmp_path):
    row = _rows(tmp_path, goals=[("11", "clean"), ("12", "badrecord")])[1][0]
    assert row["ok"] is False and "unreadable" in row["name"] and "#12" in row["name"]


def test_a_record_problem_does_not_hide_drift_in_the_text(tmp_path):
    row = _rows(tmp_path, goals=[("11", "badrecord"), ("12", "drifted")])[1][0]
    assert row["ok"] is False and "#11" in row["name"] and "#12" in row["name"] and "differs" in row["name"]


def test_a_work_record_that_raises_is_counted_unreadable(tmp_path):
    def garble(base):
        (base / "state" / "work" / "11.json").write_text("{nope")
    row = _rows(tmp_path, goals=[("11", "clean"), ("12", "drifted")], mutate=garble)[1][0]
    assert "1 unreadable" in row["name"] and "1 drifted" in row["name"]


def test_live_goal_past_the_cap_is_summarised_not_walked(tmp_path):
    goals = [(str(100 + i), "drifted") for i in range(3)] + [(str(200 + i), "clean") for i in range(200)]
    row = _rows(tmp_path, goals=goals)[1][0]
    assert "203 sealed" in row["name"] and "3 more not checked" in row["name"]
    assert "drifted" not in row["name"]                  # the 3 oldest are past the cap: never walked


def test_a_work_record_that_cannot_be_statted_is_unreadable_not_a_lost_row(tmp_path):
    def break_one(base):                       # a dangling symlink: glob lists it, stat() raises
        os.symlink(str(base / "nowhere"), str(base / "state" / "work" / "13.json"))
        (base / "state" / "sealed" / "13.json").write_text("{}")
    row = _rows(tmp_path, goals=[("11", "clean"), ("12", "drifted")], mutate=break_one)[1]
    assert len(row) == 1 and "1 drifted" in row[0]["name"] and "#12" in row[0]["name"]


def test_the_newest_work_records_are_checked_first(tmp_path):
    goals = [(str(100 + i), "clean") for i in range(200)] + [("999", "drifted")]
    row = _rows(tmp_path, goals=goals)[1][0]
    assert "1 drifted" in row["name"] and "#999" in row["name"]


def test_the_row_writes_nothing(tmp_path):
    base, _ = _rows(tmp_path, goals=[("11", "drifted")])

    def snap():
        return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(base.rglob("*"))}
    before = snap()
    td._doc().check(str(base), run=td._runner())
    assert snap() == before

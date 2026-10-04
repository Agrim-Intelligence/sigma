"""The S4 review evidence (#587) matches the committed units file and carries no host path."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNITS = json.loads((ROOT / "docs/launch/review-units.json").read_text())
EVID = ROOT / "docs/launch/evidence" / f"review-s4-{UNITS['sha'][:12]}.json"
HOST = re.compile(r"/(?:Users|home|private|tmp|var)/\w|~/|\.sigma-ops|[A-Z]:\\\\")


def _ev():
    return json.loads(EVID.read_text())


def test_evidence_units_sha_and_unit_ids_match_the_units_file():
    ev = _ev()
    assert ev["schema"] == "review-s4/v1" and ev["units_sha"] == UNITS["sha"]
    assert [u["unit"] for u in ev["units"]] == [u["unit"] for u in UNITS["tier_a"]]
    assert ev["tier_b_reviewed"] is False
    assert ev["scoring"].startswith("no dimension is scored")


def test_every_unit_is_reviewed_or_not_reached_with_a_reason_and_totals_add_up():
    ev = _ev()
    rev = [u for u in ev["units"] if u["status"] == "reviewed"]
    for u in ev["units"]:
        assert u["status"] in ("reviewed", "not reached")
        assert u["status"] == "reviewed" or u["reason"]
    for u in rev:
        assert u["findings_found"] == u["verified"] + u["dropped"] == len(u["dropped_ids"]) + sum(u["severity"].values())
        assert u["tokens"] == u["review_tokens"] + u["verify_tokens"]
    t = ev["totals"]
    assert t["units_reviewed"] == len(rev) and t["units_reviewed"] + t["units_not_reached"] == 16
    assert t["reviewer_and_verifier_tokens"] == sum(u["tokens"] for u in rev)
    c = ev["counters"]
    assert c["cumulative"] == c["at_release"] + c["this_goal_measured"]
    assert c["over_checkpoint"] == c["cumulative"] - c["checkpoint"] == 7369450
    assert c["headroom_cap"] == c["cap"] - c["cumulative"]
    assert c["cumulative"] == c["at_release"] + ev["this_goal_first_wave_total"]


def test_per_unit_lines_and_totals_tie_to_the_units_file_and_the_md():
    ev, lines = _ev(), {u["unit"]: u["lines"] for u in UNITS["tier_a"]}
    rev = [u for u in ev["units"] if u["status"] == "reviewed"]
    assert all(u["lines"] == lines[u["unit"]] for u in ev["units"])
    t, c = ev["totals"], ev["counters"]
    assert t["lines_reviewed"] == sum(u["lines"] for u in rev)
    assert t["findings_found"] == sum(u["findings_found"] for u in rev)
    assert t["verified"] == sum(u["verified"] for u in rev)
    assert all(sum(u["dimensions"].values()) == u["verified"] for u in rev)
    assert c["this_goal_measured"] == t["reviewer_and_verifier_tokens"] + ev["governance"]["tokens"] + ev["slot_overhead"]["tokens"]
    blockers = [f for f in ev["filed"] if f["label"] == "launch:blocker"]
    assert blockers and all(f["queued"] and not f["promoted"] for f in blockers)
    md = EVID.with_suffix(".md").read_text()
    for number in (f"{c['cumulative']:,}", f"{t['findings_found']} reported", f"{t['units_reviewed']} of 16"):
        assert number in md


def test_no_host_path_in_evidence_files():
    for p in (EVID, EVID.with_suffix(".md")):
        assert not HOST.search(p.read_text()), p.name

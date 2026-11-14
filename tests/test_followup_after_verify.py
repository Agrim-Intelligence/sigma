"""A find after the first green verify is still a slice and forces a second verify, WHEN `verify.enforce` is on (#2224).

Run: python3 -m pytest tests/test_followup_after_verify.py
(generic form: python -m pytest tests/test_followup_after_verify.py)

No detector for "first green verify" exists and none is invented: every find routes through the same function at any
time. The second verify is forced by machinery that already exists, ONLY while `verify.enforce` is on:
`work._test_first_refusal` returns "" when it is off, and with it on the proof it checks is bound to the plan's
exact bytes (`red_green.refusal`: "plan changed since observed green"), so the slice's plan edit stales it.
Honest limit: that is the test-first proof, not `done_refusal`'s freshness (a plan edit alone leaves a green
evidence file fresh); the slice's own code change stales the content fingerprint. Conditional on enforce, stated in
docs/configuration.md.
CONTROLS: C38 (run with enforce off: the enforce-ON test goes red).
"""
import json

from test_add_slice import proj, cli, work, _mod, fr           # noqa: F401  (fixtures and helpers)
from test_followup_track_routing import make, track, CFG        # noqa: F401

rg = _mod("red_green")
state = _mod("state")


def _write_cfg(proj, enforce):
    cfg = json.loads((proj.sdlc / "config.json").read_text())
    cfg["verify"] = {"enforce": enforce}
    (proj.sdlc / "config.json").write_text(json.dumps(cfg))
    return cfg


def _green_evidence(sdlc, goal):
    ev = state.evidence_path(sdlc, goal)
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(json.dumps({"command": "pytest", "exit": 0, "at": 9e12}))


def test_a_find_after_a_green_verify_is_refused_with_the_add_slice_gesture(make, capsys):
    sdlc, src = make()
    _green_evidence(sdlc, "7")
    assert track(sdlc) == 3 and src.created is None
    err = capsys.readouterr().err
    assert "work.py add-slice" in err and "deferred" not in err.lower()


def _proof(plan):
    return {"provenance": rg.PROVENANCE, "passed": True, "plan_sha256": rg.digest(plan),
            "selectors": rg.selectors(plan, None), "nodes": {}}


def test_the_slice_stales_the_planned_test_proof_and_enforce_on_refuses_until_reverified(proj):
    cfg = _write_cfg(proj, True)
    proof = _proof(proj.main_plan)
    root = str(proj.sdlc / "work" / "2224")
    assert rg.refusal(str(proj.sdlc), "2224", root, proj.main_plan, proof) != "plan changed since observed green"
    assert cli(proj).returncode == 0
    assert rg.refusal(str(proj.sdlc), "2224", root, proj.main_plan, proof) == "plan changed since observed green"
    rec = work._record(str(proj.sdlc), "2224")
    refused = work._test_first_refusal(str(proj.sdlc), cfg, rec, "2224", work._run)
    assert refused.startswith("TEST-FIRST REFUSED")


def test_with_enforce_off_nothing_forces_a_reverify(proj):
    cfg = _write_cfg(proj, False)
    assert cli(proj).returncode == 0
    rec = work._record(str(proj.sdlc), "2224")
    assert work._test_first_refusal(str(proj.sdlc), cfg, rec, "2224", work._run) == ""


def test_no_first_green_detector_exists_in_the_scripts():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "skills"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "first_green" not in text and "first green verify detector" not in text, path

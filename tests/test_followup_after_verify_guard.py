"""Pure move from tests/test_followup_after_verify.py at 4042e97 (#2224 amendment 2): the guard nodes (pass at base); test bodies byte-identical.

A find after the first green verify is still a slice and forces a second verify, WHEN `verify.enforce` is on (#2224).

Run: python3 -m pytest tests/test_followup_after_verify*.py
(generic form: python -m pytest tests/test_followup_after_verify*.py)

No detector for "first green verify" exists and none is invented: every find routes through the same function at any
time. The second verify is forced by machinery that already exists, ONLY while `verify.enforce` is on:
`work._test_first_refusal` returns "" when it is off, and with it on the proof it checks is bound to the plan's
exact bytes (`red_green.refusal`: "plan changed since observed green"), so the slice's plan edit stales it.
Honest limit: that is the test-first proof, not `done_refusal`'s freshness (a plan edit alone leaves a green
evidence file fresh); the slice's own code change stales the content fingerprint. Conditional on enforce, stated in
docs/configuration.md.
CONTROLS: C38 (run with enforce off: the enforce-ON test goes red).

"""
def test_no_first_green_detector_exists_in_the_scripts():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "skills"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "first_green" not in text and "first green verify detector" not in text, path

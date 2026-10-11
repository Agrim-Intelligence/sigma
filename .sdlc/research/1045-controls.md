# 1045 controls (evidence)

Documented gesture: `python3 evals/skills/smoke.py` (from the repo root). Each card rule was broken once
in a card or tree copy, the gesture run, the break restored. Every one was seen RED (named finding, exit 1):

- bogus library stem and role flip (library stem not in LIBRARY_ONLY, and gesture<->library): RED
- missing card for a script skill: RED
- gate typo (one character off a pinned gate): RED
- bad producer (path that does not exist): RED
- changed `expect` on an exercised fixture: RED
- removed fixture file on an exercised card: RED
- home path in a card path field: RED
- empty `.py` under a scripts dir (unlisted script): RED

Honest note on the producer control: it was seen red on `smoke.py`. The real-tree gate is a pytest selector:
`tests/test_skill_smoke.py::test_script_skill_cards_have_no_runner_findings`. Re-run at retro time with system
python3 (no venv in the worktree): producer in `evals/skills/cards/sigma-log.json` changed to a missing path ->
1 failed; restored -> `cmp` identical, 1 passed. (An earlier sed attempt matched nothing, so it stayed green;
that was a dud break, not a pass.) Tree after: only the intended 1045 changes.

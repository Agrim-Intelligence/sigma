# Retro: golden task T1 (merge intervals)

Grade: partial (all five criteria evidenced except the last, which is only partly verified before commit).

Evidence per criterion
- Layout: `evals/golden/T1/` holds task.json, repo/, hidden/files/, hidden/verify.json, reference/, rubric.json, allowed_paths.json.
- Verify: `python3 evals/golden/verify.py` printed `ok T1`, `golden: 1 task(s), 0 red`; ten planted breaks went red (see `.sdlc/research/882-controls.md`).
- Hidden edge cases: 7 of 8 hidden tests fail on the start tree while the visible 2 pass.
- stdlib-only, no writes: write-surface check and rename check were clean.
- Guards: exposure scan reads HEAD, so it could not see uncommitted files and is unrun; third-party-import test could not be collected on Python 3.9.

Residual debt
- Run the exposure scan and the import-scan test on 3.10+ after commit.
- CI golden-verify step was absent when tested; one verifier test failed for that reason, unrelated to T1.
- Hidden tests are readable in a checkout (accepted design doubt); only the later executor can close it.

Rot: none. Plan 882 is finished history and may be archived. No standing-doc changes proposed.

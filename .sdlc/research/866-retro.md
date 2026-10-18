# #866 retro (advisory, final diff)
Grade: achieved on AC-1..AC-4 and AC-6; AC-5 partial/diverged in wording (layer1.py defines the arm contract because live.py has no arm interface; the fake-arm hermetic path is real). AC-7 not independently re-measured here (the test run exceeded the retro's time budget; write_surface check not confirmed).
Overall: partial.
Debt: live.py not wired to the arm contract; real-model run_scenario path untested; config written after init session (ordering unmeasured against a real loop); sweep_stale failures swallowed.
Follow-ups (unfiled, proposed): wire live.py to the layer1 arm contract; one live smoke run; surface sweep_stale errors in the artifact row.
Lessons: (1) verify a cited interface exists at plan time. (2) Both review-cycle defects (deadline semantic, shielding) escaped the author's own tests; deadline and signal/cleanup guards need a control broken per clause before review, not after.

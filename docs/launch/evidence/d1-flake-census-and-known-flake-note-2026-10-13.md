# D1 evidence: flake census and one known flake it did not catch

A flaky-test census of 10 runs on Linux and 10 runs on macOS, Python 3.12 only, exists:
`docs/launch/evidence/flake-c82e3dfa7420.json` (measured at commit c82e3dfa7420; method and limits in
`docs/launch/flake-census.md`). It recorded no flaky test in either half. Linux ran single-process; macOS ran
with 4 xdist workers.

That result does not mean the suite has no flaky test. Ten runs cannot prove zero, and the census did not
catch one flake that is now known:

- Issue #1242: `test_term_comes_before_kill` in `tests/test_bounded_run.py` failed on the Linux legs of the nightly
  of 2026-10-11 (all four, **including Python 3.12**: 3 failed tests each on 3.12, 3.11 and 3.13, 2 on 3.10, the other two being
  `test_overrun_stops_the_whole_group` and `test_split_mode_peak_memory_under_a_flood_is_bounded`) and again on the 3.10 leg
  of the manual run of 2026-10-12 (the TERM grace escalated to KILL). The census ran Python 3.12 at an earlier commit, so these
  failures were outside what it observed; the 2026-10-11 failures were on 3.12 itself. All three, and the 2026-10-12 failure, are triaged in
  `d1-nightly-failure-triage-3a303bee2cea.md`: the 2026-10-11 logs show a test-clock race and a worker-memory reading, both
  fixed by #1153, not the zombie mechanism of #1242 (a second, separate mechanism, still open).
- A comment on #1242 reports a local reproduction under CPU load (a container limited to 2 CPUs with 6 busy
  loops): 6 of 40 runs failed, and 2 of 12 in an earlier batch, against 0 of 8 failures without load. The
  cause is unproven; a rejected ordering theory is recorded. A candidate fix with a deterministic reproduction is up as PR #1273: the mechanism is proven, the trigger seen in CI is NOT proven.
- Status: #1242 is OPEN and stays open until the nightly is green over several runs. It is not fixed, and this note does not claim otherwise. The D1 bar of "0 flaky
  tests on Python 3.12" rests on the census alone, and the load-sensitive failure above was not seen by it.

# D1 evidence: flake census and one known flake it did not catch

A flaky-test census of 10 runs on Linux and 10 runs on macOS, Python 3.12 only, exists:
`docs/launch/evidence/flake-c82e3dfa7420.json` (measured at commit c82e3dfa7420; method and limits in
`docs/launch/flake-census.md`). It recorded no flaky test in either half. Linux ran single-process; macOS ran
with 4 xdist workers.

That result does not mean the suite has no flaky test. Ten runs cannot prove zero, and the census did not
catch one flake that is now known:

- Issue #1242: `test_term_comes_before_kill` in `tests/test_bounded_run.py` failed once on the Linux Python
  3.10 leg of a full-matrix run (the TERM grace escalated to KILL). The census ran Python 3.12 only, so a
  3.10 failure was outside what it observed.
- A comment on #1242 reports a local reproduction under CPU load (a container limited to 2 CPUs with 6 busy
  loops): 6 of 40 runs failed, and 2 of 12 in an earlier batch, against 0 of 8 failures without load. The
  cause is unproven; the issue records one untested candidate and one rejected ordering theory.
- Status: #1242 is OPEN. It is not fixed, and this note does not claim otherwise. The D1 bar of "0 flaky
  tests on Python 3.12" rests on the census alone, and the load-sensitive failure above was not seen by it.

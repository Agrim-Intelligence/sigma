# #375 retro (grade: achieved)

Shipped: opt-in post guard (`post_guard.py`) at sources._run, work._run, gh_api._default_run and two
call-site hooks; runbook section; structural test of known gh write routes.
Lessons: (1) plan-review found five unguarded write paths by tracing callers; a new gh write path now
fails `test_every_gh_write_site_is_a_known_route`. (2) Code review found argv shapes (`-am`, `-mX`, global
flags, gh api reads) that a flag table misses: the control list should include shorthand clusters.
(3) Process: research and plan ran inline, so `loop.py phases` reports plan as ended without an agent id.
Residual debt: gaps named in the runbook (sync/ledger ops commits, `--input -`, labels/milestones).
Measured: 49 tests in tests/test_post_guard.py; 11 controls broken once and seen red. Not measured: guard
latency on a large patterns file.

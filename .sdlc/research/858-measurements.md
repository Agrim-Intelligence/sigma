# #858 measurements and control transcripts

Host: Apple M4 Pro, 12 cores, macOS; git 2.39.5 (Apple Git-154); Python 3.12 (project venv). The host was
under heavy unrelated load while these ran (load average 25 to 35 on 12 cores, 136 to 198 at the worst), so
every timing below is an UPPER-ish figure for this machine and must not be quoted as a forecast.
Local-path transport; network latency and GitHub ref limits NOT measured (slice 1 spike B-1).

## Namespace read cost (tests/test_claim_refs_race.py::test_namespace_fetch_cost_measured, seconds)

| refs | read_all first fetch | read_all no-op | read_tips (ls-remote) |
|---:|---:|---:|---:|
| 10 | 0.103 | 0.105 | 0.038 |
| 100 | 0.189 | 0.157 | 0.037 |
| 1000 | 0.605 | 0.298 | 0.071 |

`read_all` first cost 30 s at 1000 refs in an earlier build that ran one `cat-file` per ref; it now reads every
body with ONE `for-each-ref` (`%(contents)`), which is what the table above measures. The measurement test took
36 s under load and the smoke test 25 s; no `slow` marker is used (under 60 s each).

## Smoke test sensitivity (one-off count script, 100 goals x 10 clones, 1000 attempts, 10 workers)

* guarded form: 0 of 100 goals with more than one winner; 100 WON, 900 LOST_RACE, 0 OUTAGE/REFUSED.
* plain `--force`: 100 of 100 goals with more than one winner; 1000 of 1000 attempts WON.
Timing-dependent (the research run saw 98 to 99 of 100); the smoke test is NOT the guard's proof.

## Controls (each break was an EDIT to claim_refs.py, the documented bare gesture run, then the file restored)

Gesture: `python -m pytest tests/test_claim_refs.py -q` (race file for C-3).

* C-1 `_lease_flag` returns `--force`: 7 failed, 90 passed. Red: create, reclaim, stale-delete, double-delete,
  renew/stale-renew, retried-delete, explicit-lease-argv tests.
* C-2a lease without the sha (`--force-with-lease=<ref>`): 8 failed. C-2b bare `--force-with-lease`: 8 failed.
  Behavioural race tests DID go red on this host (reclaim, stale delete, renew) as well as the argv test.
* C-3 same break, `python -m pytest tests/test_claim_refs_race.py -q`: smoke test red (1 failed, 1 passed).
  Timing-dependent; not the guard's proof (C-1 is).
* C-4a every rejection as LOST_RACE: 6 failed (hook decline, directory/file conflict, rejected-create-absent rows).
* C-4b transport failure / unknown output as LOST_RACE: 6 failed (exit 128, not-a-repository, empty, garbage, missing remote).
* C-4c `=` as WON: 2 failed (up-to-date row, re-push test).
* C-4d post-win tip confirmation removed: 3 failed (tip-disagrees, delete-tip-still-there, demoted-win retry).
* C-4e nonce stripped: 3 failed (identical-inputs-differ, parse round trip, create race).
* Process-group kill replaced by `proc.kill()` (plan-review refinement 2): 1 failed
  (`test_timeout_is_outage_and_kills_process_group`, the grandchild survived).
* Windows guard removed: 1 failed (`test_default_runner_refuses_on_windows`).
* C-5 write-surface ratchet, in a scratch repo with the file tracked: without the inventory entry,
  `python tools/readiness/write_surface.py check . <inventory>` reports
  `new write site skills/sigma-loop/scripts/claim_refs.py:_push git-push`; adding a second `git push` in a second
  function adds `claim_refs.py:_second git-push` (one function per push is what keeps it one entry). With the entry the
  check is clean. The scanner emits `git-push` only (count 1) for `_push`: the lease flag is built in `_lease_flag`,
  outside the call, so no `git-destructive` row is produced and none is claimed.
* C-6 hermeticity: pointing `GIT_CONFIG_GLOBAL` at a non-devnull path, or the remote assertion at a path outside the
  tmp directory, turns `test_tests_are_hermetic` red (1 failed each).
* C-7 allowlist: with `claim_refs` absent from `LIBRARY_ONLY`, `tests/test_script_help.py` reported 4 help-run
  failures (`claim_refs.py --help` rc 0, empty output); with it, 4 passed.

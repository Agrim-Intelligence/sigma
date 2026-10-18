# Retro: #810 live entrypoint (advisory, autonomous)

Grade: PARTIAL (intent met on the model-free, refusal and ledger path; live run path unmeasured).

Evidence run in .sdlc/work/810 (uncommitted tree): `$HOME/.sigma-venv312/bin/python -m pytest` on the 4 regression test files: 362 passed.
(System python3 is 3.9.6: `test_ledger_imports_only_stdlib...` fails there on `sys.stdlib_module_names`, an env artefact, not a defect.)
`env -i python3 evals/regression/live.py` -> `REFUSED [no-credential]`, exit 2. `write_surface.py check` rc=0. No `evals/bench` changes.

## Per-criterion
- AC-1 achieved: empty env exits 2, empty stdout, NOT RUN row.
- AC-2 achieved in code: no-cap, no-opt-in, unsupported-platform, pull-request-event, launcher-precondition and launcher-latch rungs exist and are tested. Windows leg is only a workflow step (manual dispatch), not seen to run.
- AC-3 achieved: Ledger.reserved books the belt before launch; signal/exit settle at full belt; SIGKILL leaves it open and counted.
- AC-4 achieved by design: exact-value redaction + launcher named variable; tested. Not independently fuzzed against a real claude.
- AC-5 achieved: subprocess via arms.common.run_claude / launcher; no evals/bench diff.
- AC-6 achieved in code: exit 97 + latch file -> launcher-latch NOT RUN; latch bound to live.py via a launcher config with parent_marker "live.py" (operator install step, checked by refusal if wrong).
- AC-7 partial: empty-env and no-cap gestures run locally. The Windows runner gesture is written, never executed (workflow_dispatch). write_surface green says nothing about append/fd writes (ledger); the compensating scan in test_regression_ledger covers them (now extended for tempfile).

## Residual debt (top first)
1. No real `claude -p` run has ever gone through this path (launcher README still says so). Every run-path claim is mock-verified only. Product debt, needs one measured live run before it is called working.
2. Windows refusal unmeasured (AC-7); also ledger fcntl-absent path tested only by blocking fcntl.
3. Review non-blocking: `--commit` is a free 40-hex string not tied to the plugin content actually exported/tested (row can attest the wrong commit). Fix: derive from the plugin_dir/export or verify equality.
4. Timeout (`--timeout-s`) is any positive number, unbounded before bands exist; slice 5 (baseline-derived belt/timeouts, needs #814/#817) deferred, so timeouts are not machine-derived (SAFETY: should be clamped).
5. `--ledger-dir` unvalidated (world-writable, relative, or per-checkout dir splits the cap; per-user default is the only shared one). Validate or document.
6. Ledger is per-machine; lost hosted runner forgets belts (defer #819). Gated API-key runs report NOT RUN launcher-api-key-unsupported until goal 831.
7. Structural: two atomic-write idioms (row writer vs ledger) and write_surface blind spot for open()/os.write remain; the second detector lives in a test, not in write_surface.py. Candidate: teach write_surface.py those rules.

## Proposals (all need owner approval; nothing edited)
| Lesson | Store | Edit |
| --- | --- | --- |
| "write_surface green" is insufficient for append/fd/tempfile writes | standing rule / tooling | Extend tools/readiness/write_surface.py to classify open(w/a), os.open, os.write, tempfile.*; then retire the duplicate scan in test_regression_ledger |
| Windows legs authored but never run are not evidence | docs/cloud-sessions or AGENTS.md note | One line: manual-dispatch workflow steps count as unmeasured until a run is linked |
| First model spawn needs one measured live run before "working" | audit-trail | Open follow-up: live smoke run with a real subscription token, record cost, latch behaviour |
| Items 3-5 above | follow-up goals | Tie --commit to export; clamp timeout; validate --ledger-dir |

Standing-doc rot: none found.

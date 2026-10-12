# D1 evidence: mutation record, finished and absent modules

Source: `docs/launch/evidence/mutation-70c2c6e96136.json`, measured at commit 70c2c6e96136 and NOT re-run
for this record (no mutation was run on 2026-10-13). The file has `complete: false`: 14 of 33 modules
finished within budget and 19 are absent. This note reads the file; it adds no measurement. The module
paths below are as recorded in that file, which was cut when the skill directory was still named
`agrim-loop`; the same files live today under `skills/sigma-loop/scripts/`. The tool that produced
the file is `skills/sigma-loop/scripts/mutation.py` (a timeout, a crash and "not run" all produce
`absent`, never a number, never 0).

Budget recorded in the file: 900 seconds per module, 10,800 seconds overall (used: 10,800.1), mutmut 2.5.1,
Python 3.12, one module at a time on a shared machine. This is a documented deviation from the issue, which
allowed 60 minutes per module and no cap.

## Kill rate for the finished modules, as recorded

Kill rate = killed / (killed + survived) over the mutants on lines the scoped test files execute. It
measures how hard those tests check that module, not how well the module is tested overall. Timeout and
suspicious mutants are in neither side of the rate.

| Module | Kill rate | Killed | Survived | Verdict recorded (threshold 0.8) |
|---|---|---|---|---|
| hooks/decision_gate.py | 0.7286 | 247 | 92 | unverified |
| hooks/issue_field_gate.py | 0.8085 | 38 | 9 | verified |
| hooks/research_capture.py | 0.7 | 63 | 27 | unverified |
| hooks/time_track.py | 0.8261 | 114 | 24 | verified |
| skills/agrim-init/scripts/board_setup.py | 0.6945 | 557 | 245 | unverified |
| skills/agrim-init/scripts/setup_wizard.py | 0.8364 | 138 | 27 | verified |
| skills/agrim-loop/scripts/blockers.py | 0.7089 | 151 | 62 | unverified |
| skills/agrim-loop/scripts/channel_notify.py | 0.6718 | 88 | 43 | unverified |
| skills/agrim-loop/scripts/gh_session.py | 0.6 | 3 | 2 | unverified (5 mutants: thin) |
| skills/agrim-loop/scripts/run_with_timeout.py | 0.6667 | 22 | 11 | unverified |
| skills/agrim-loop/scripts/scrub.py | 0.8026 | 61 | 15 | verified |
| skills/agrim-loop/scripts/slack_client.py | 0.6238 | 63 | 38 | unverified |
| skills/agrim-loop/scripts/state.py | 0.7447 | 245 | 84 | unverified |
| skills/agrim-loop/scripts/upstream.py | 0.6601 | 371 | 191 | unverified |

Summary: 4 of the 14 are at or above 0.8; 10 are below. Pooled over the 14 (2,161 killed, 870 survived; this
sum is computed here from the recorded counts and is not a figure the file states) the rate is 0.713. The
verdict compares against the tool's default 0.8 and is not a decision; the owner sets the threshold.
Survivors include equivalent mutants that change no behaviour, so a low rate does not by itself mean a
defect. The file stores diffs for at most 60 survivors per module.

## The 19 absent modules and the reason each did not finish

### Not a Python file (7): `reason_code: not-python`, "mutmut mutates Python only"

These were never mutation candidates; they are listed in the unit list but cannot be measured by this tool.

- hooks/_py.sh
- hooks/agrim_gate.sh
- hooks/completion_gate.sh
- hooks/hooks.json
- hooks/plan_gate.sh
- hooks/session_start.sh
- install.sh

### Exceeded the per-module timeout (10): `reason_code: timeout`

mutmut was stopped at the allowance, and the file records the module as "not measured, deliberately neither
a pass nor a merge freeze". The file records why the time ran out only as "timeout"; it does not record
which part of each module made it slow, so the underlying cause is **reason not recorded**.

| Module | mutmut ran for (seconds, as recorded) | Module total recorded (seconds) |
|---|---|---|
| hooks/gate_state.py | 882.8 | 900.0 |
| skills/agrim-init/scripts/init_flow.py | 865.1 | 900.0 |
| skills/agrim-loop/scripts/feature_rebase.py | 828.7 | 900.0 |
| skills/agrim-loop/scripts/ledger.py | 883.7 | 900.0 |
| skills/agrim-loop/scripts/reconcile.py | 892.4 | 900.0 |
| skills/agrim-loop/scripts/slack_commands_listen.py | 867.6 | 900.0 |
| skills/agrim-loop/scripts/sources.py | 312.6 | 338.1 |
| skills/agrim-loop/scripts/supervise_daemon.py | 862.3 | 900.0 |
| skills/agrim-loop/scripts/sync.py | 760.1 | 900.0 |
| skills/agrim-loop/scripts/watch_daemon.py | 554.5 | 900.0 |

Two notes from the file's own `notes`:

- `sources.py` was given only the 338 seconds left under the overall cap, so its timeout is not comparable
  to the others (operator-reported; the allowance was not recorded for it).
- For sync.py and watch_daemon.py the module total is well above the recorded mutmut run time. The file does
  not itemize the difference; whether it was the coverage step is **reason not recorded**.

### Not reached before the overall cap (2): `reason_code: not-run-budget`

"The overall wall-clock cap was reached before this module was reached."

- skills/agrim-loop/scripts/loop.py
- skills/agrim-loop/scripts/work.py

## What this does and does not support

- The D1 bar reads "kill rate recorded for the modules that finished within budget, absent modules listed
  with the reason". The 14 above are the recorded set; the 19 are listed with the recorded reason.
- The absent set includes large, central modules (work.py, loop.py, sync.py, ledger.py, reconcile.py,
  the watcher and supervisor). Nothing here says how well their tests check them.
- Per the file: "A module with no number is absent and says why; it is never recorded as 0." Retrying a
  timeout means running the same command with a larger `--module-timeout`; this has not been done.
- The file was recorded at commit 70c2c6e96136; the code has changed since. These rates are not a statement
  about the launch commit.

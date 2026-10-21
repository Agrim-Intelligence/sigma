# Upkeep agent level, host login: the real-model run

The first recorded run of upkeep's agent level (Level 2) against the real CLI and a real model, in host-login mode
(`upkeep.conflicts.agent.login: "host"`), on 21 Oct 2026. Machine: macOS 27, Claude Code 2.1.304, Python 3.13. Model:
`claude-sonnet-5-5` for both the resolver and the reviewer. No token or API-key variable was set in any environment of
the run: the sessions logged in from the CLI's own login.

## 1. Confinement probes (real CLI, the launcher's own environment builder)

A `claude -p` session in a throwaway directory outside the home, with the launcher's host-mode environment, asked to read
a file inside its directory, a canary file in the home, a canary file in `/private/tmp`, to run `echo` with any tool, to
list its other tools, and to say whether it saw an instruction file (a `CLAUDE.md` was planted in its own directory).

| Flags beyond `-p --output-format json --model sonnet --permission-mode acceptEdits --allowedTools Read,Edit,Write,Glob,Grep` | inside | home canary | tmp canary | shell | other tools | instructions |
|---|---|---|---|---|---|---|
| `--setting-sources "" --strict-mcp-config` | read | **read** | **read** | **ran `echo`** | **Agent, Bash, Skill, Workflow and more** | none |
| `--restricted --tools Read,Edit,Write,Glob,Grep --strict-mcp-config --setting-sources "" --disable-slash-commands` | read | refused | refused | none | none | none |

A second probe with the restricted set asked the session to resolve a conflicted file and then write a file into the home
and into `/private/tmp`. On disk afterwards: the conflicted file resolved with both sides kept and no markers; neither
outside file existed. Both sessions logged in. Cost of the probes: under $0.07 in total.

So `--allowedTools` only pre-approves: it removes no tool. The launcher now adds the restricted set to every session no OS
wall confines (always in host mode, and on a platform with no wall), and host mode builds no wall, since on macOS the wall
denies the keychain that holds the CLI's login.

## 2. End to end on a throwaway repository

A new private repository with a three-function Python module and its tests, Sigma initialised (GitHub mode, ledger on,
verify `python3 -m pytest -q`), a unit `demo` opened with `define.py open`, upkeep enabled with `work.base` `main`, a $2
per-unit and $10 per-day cap. Each side then added a function and its test at the same place in the same two files, and
extended the same import line: `main` added `shout`, the unit added `whisper`. Each side passed its own tests; the two
did not merge. The gesture throughout was the documented one pass: `feature_rebase.py upkeep .sdlc demo`.

| Pass | Configuration | Result |
|---|---|---|
| 1 | `resolve: mechanical` | refused: the unit commit had not come through a pull request; filed an issue; the documented `ack` cleared it |
| 2 | `resolve: mechanical` | `parked`: a source conflict; filed the parked-unit issue |
| 3 | `resolve: agent`, `login: host` | `parked` after 3 attempts, `unit-line-dropped`: every resolution merged the two import lines, and the dropped-line check wanted the unit's version of the line word for word |
| 4 | the same, after the dropped-line fix, `main` one commit further (`wave` added to the same import line) | **`rebased`, level 2**: pairing and verify passed, reviewer verdict `approve`, backup ref written, unit tip moved, both parked-unit issues closed |

Pass 3 found a defect no fake could show: when both sides edit the same base line, no correct resolution keeps either
version word for word. A line a side wrote in a base region the other side also changed now counts as kept when every
word it introduced is still in the resolved file; a line only one side touched must still survive word for word.

Measured on the remote after pass 4, not read from Sigma's report:

- the unit branch contains `main`, and its one commit holds `shout`, `wave` and `whisper` in the module and
  `from app import greet, farewell, shout, wave, whisper` in the tests; the five tests pass on the pushed branch;
- the backup ref under `refs/sigma/backup/demo/` holds the unit's old tip;
- the two parked-unit issues are closed.

Charge: $3.00 (the $2 per-unit cap plus the reviewer's half of it), because a host run is charged its cap: the shared
transcript store is never priced. Wall time of pass 4: 45 seconds.

## 3. What this does not show

- The scrubbed mode with the macOS wall: no real-model run is recorded for it.
- Linux and Windows: not run.
- Conflict quality in general: one conflict was resolved; the resolver's judgement on harder conflicts is unmeasured.
- The watcher's scheduled sweep: the run used the documented one-pass verb, not the window and job path.

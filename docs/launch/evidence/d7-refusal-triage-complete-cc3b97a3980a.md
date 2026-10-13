# D7: the refusal sites that were not triaged, now triaged (cc3b97a3980a)

Source tree: origin/main at cc3b97a3980a, plus the four fix pull requests listed below (open, not merged
when this was written). The first D7 record (`d7-onboarding-refusals-f627d06a.md`) triggered 45 first-run
scenarios by hand and said a static scan finds about 180 more candidate refusal sites that were not
triaged. This record triages all of them, and the sites in the modules around them.

**What this is, and what it is not.** It is a static classification by reading source, done by four
read-only reviewers (one per module group) and checked by me on every site that was fixed. Only the fixed
sites were triggered for real (each by a failing test, see below). The counts of "prints-fix" and
"unreachable-on-first-run" are judgments from reading, not measurements, and two of the groups are
approximate (named below). It is not a claim that every message in the repository was read.

## Method

1. A scan (`ast`, then a second pass on message strings) of `skills/*/scripts`, `hooks` and `tools` for
   sites that exit nonzero or decline to do what was asked: `REFUSED`/`refus`, `sys.exit`, `return` of 2 or
   more, `raise SystemExit`, a `print(..., file=sys.stderr)` before a nonzero return, and
   `not found`/`must`/`cannot`/`unknown`/`invalid` messages. 476 exit sites plus 480 message sites were found
   in the onboarding modules before de-duplication; fragments of one multi-part message count as one site.
2. Four reviewers read the source around every site in their group and classified it:
   `prints-fix` (names the command, flag, setting or file edit that resolves it, or for a usage error
   lists the valid input), `no-fix`, `misleading` (names a fix that does not match the cause), or
   `unreachable-on-first-run` (needs a unit registry, a ledger, a board, cross-repo siblings, Codex,
   an opt-in gate, a daemon, an exotic filesystem, or a command only an agent types).
   "First run" means: a fresh repository, `/sigma-init`, default config, local-goals or github mode,
   then `loop.py start|next|verify|record`, `work.py start|commit|pr|merge`, `sync`, `promote`, `define`.
3. Every no-fix or misleading site that a first run can reach was fixed with a failing test first, and each
   fixed clause was then broken once and seen to fail (see "Controls"). Sites that need a design or an owner
   decision, or that are opt-in, were filed as `launch:next` / `priority:P3` issues instead.
4. `tools/` is maintainer tooling. It ships inside the plugin (the repository root is the plugin and nothing
   excludes it) but no user command invokes it except `tools/onboarding_control.py`; its 61 top-level and 55
   readiness candidate sites (`grep -c -E 'REFUSED|sys.exit|SystemExit'`) were counted, not classified one by one.

## Counts

| Module group | Sites | Prints-fix | No-fix or misleading, reachable on a first run | Unreachable on a first run | Undetermined |
|---|---|---|---|---|---|
| `loop.py` | 103 | 59 | 5 | 39 | 0 |
| `work.py` | about 243 | about 59 | 22 sites in 17 rows | about 162 | 0 |
| init, setup, wizard, preflight, verify_detect, board_*, define, doctor | 218 | 136 | 22 (20 no-fix, 2 misleading) | 57 | 3 |
| hooks, sync, state, gh_api, promote, unpark, phase_gate, hard_stop, ledger, shell_policy | 285 | 47 | 30 (22 no-fix, 8 misleading) | 208 | 0 |
| **Total** | **about 849** | **about 301** | **79** | **about 466** | **3** |

Every row sums to its Sites cell and the Total row is the column sums (301 + 79 + 466 + 3 = 849). Not counted in
any cell: about 40 "non-fatal, continuing" diagnostics in `loop.py` and three kinds of message in `work.py`
(a non-refusal, three advisories that proceed anyway, a by-design human escalation), excluded as not refusals;
and three more `work.py` no-fix sites that are reachable only after opting in to `work.auto_merge`, which the
`work.py` reviewer counted inside its unreachable figure. The `work.py` unreachable count is grep-derived,
about +/-10%, so the work.py row and the totals are approximate; `gh_api.py` (119 sites) was classified from the
grep line list, not read one by one.

Per file in the last group (sites / prints-fix / no-fix / misleading / unreachable; sums to 285 / 47 / 22 / 8 / 208):
`plan_gate.sh` 3/3/0/0/0, `completion_gate.sh` 1/1/0/0/0, `decision_gate.py` 15/1/1/0/13,
`issue_field_gate.py` 1/1/0/0/0, `research_capture.py` 1/0/0/0/1, `sync.py` 34/2/3/1/28,
`state.py` 40/10/2/1/27, `gh_api.py` 119/1/13/0/105, `promote.py` 21/10/1/3/7, `unpark.py` 15/7/1/3/4,
`phase_gate.py` 11/9/0/0/2, `hard_stop.py` 4/0/0/0/4, `ledger.py` 19/1/1/0/17, `shell_policy.py` 1/1/0/0/0.

Per file in the init group (sites / prints-fix / no-fix / misleading / unreachable / undetermined; sums to
218 / 136 / 20 / 2 / 57 / 3): `init_flow.py` 22/20/0/0/2/0, `sdlc_init.py` 12/9/3/0/0/0,
`preflight.py` 27/21/3/0/2/1, `verify_detect.py` 18/14/2/1/1/0, `setup_wizard.py` 1/1/0/0/0/0,
`wizard_actions.py` 4/0/1/0/3/0, `board_setup.py` 23/0/0/0/23/0, `board_layout.py` 18/0/0/0/18/0,
`setup.py` 9/6/0/0/3/0, `doctor.py` (grouped by check) 62/56/1/0/5/0, `define.py` 22/9/10/1/0/2.

**Which numbers are authoritative.** The reviewer's own headline for the init group (154 prints-fix, 8 + 1 + 6
reachable defective, 47 unreachable, 2 undetermined) did not agree with its own per-file table. The per-file table
sums to 218 sites and is the authoritative one; the headline was recomputed from it. Two judgment calls in the
recount: the 41 board sites (`board_setup.py`, `board_layout.py`) are all opt-in, so they are counted as
unreachable on a first run although the reviewer's note says 33 of them do print a fix and about 8 do not (those
8 are filed as #1291); and the 3 undetermined sites are the one preflight proxy-block row plus the two `define.py`
messages whose text is built in files outside the group, which no one read.

All of these classifications are static source-reading by four reviewers; none is a measurement, and only the
sites that were fixed were triggered (by a failing test).

## Every fixed site

Each is a failing-test-first change; the message keeps the exit code of the site it replaces, and where a
`loop.py` classifier needle reads a work.py verdict the fix is appended after the existing wording.

Pull request A (work.py, state, promote/unpark, gh transports):
- `work.py pr`: dirty worktree now names `work.py commit <sdlc> <goal>`; empty branch names it too.
- `work.py rebase` before `start`: names `work.py start`.
- `work.py merge`: `auto_merge is off` names the setting and both values (`work.auto_merge` to `protected` or
  `always` in `.sdlc/config.json`) and keeps the `leaving PR #N for a human` ending the skills route on; unknown
  merge rights and an unreadable PR name `gh auth status` and the re-run; mergeability UNKNOWN, conflicts with
  the base, BLOCKED, `rebase deferred`, CI-cycles exhausted (names `work.max_review_cycles`), four CI-log parks,
  a refused direct merge and a PR closed without merging each name the next command.
- `work.py finish` keeping a dirty worktree names `--force`; `work.py start` with a fetch failure that is not a
  missing ref names `git ls-remote` and the re-run.
- Plan copies that differ (`loop.py verify`) name both files and the re-run.
- An invalid `.sdlc/config.json`: was a traceback (exit 1) from `work.py` and from `loop.py`; now one line with
  the file, line and fix (`loop.py` exit 2, `work.py` exit 1). A `ConfigInvalid` subclass of `JSONDecodeError`
  keeps the existing `state.load_config` test contract; a missing config in `work.py` also stopped being a traceback.
- `/sigma-promote` and `/sigma-unpark` in local-goals: said `unable to expand placeholder in path: no git remotes
  found`; now say they need github discovery mode and name `/sigma-init --mode github`. A malformed config is named
  instead of being mistaken for local-goals.
- `gh` not on PATH in `promote`, `unpark`, `gh_api` and `sources`: was `[Errno 2] ... 'gh'`; now names the install
  URL and `gh auth login`. A vanished working directory is not reported as a missing `gh`.

Pull request B (loop.py): `agent-start` collision names `agent-reclaim` and `--thread`; verify exit 5
(UNVERIFIED) says why `record done` stays blocked and the re-run; a failed verify points at the evidence tail on
stderr (stdout contract unchanged); `loop.py next` on a local backlog with no goal files says so and names
`.sdlc/goals/` and `/sigma-scope` (a backlog of finished goal files stays quiet).

Pull request C (init, preflight, verify_detect, define, doctor): `--codex` with a symlinked or malformed
AGENTS.md (a traceback) is a `REFUSED` line with the repair, exit 2, from both callers; preflight unknown-host
and auth-timeout rows name the next step; `preflight.py local-only|use-remote` on an unparseable config names
the file and fix; `verify_detect` unreadable `--command-file`, and a failed trust write no longer blames a missing
worktree; `define` missing `--name`/`--type`, per-step failures, and the local-goals `declare`/`bump-priority`
notes; `doctor` gets a `config.json is valid JSON` row only when the file is broken, and the rulesets
"could not check" row stops reading as a fault.

Pull request D (sync, ledger, handoff, unpark, decision_gate): the `ledger is off` and `knowledge sync is off`
notices name the gesture and the re-run; the `OFF` line of `ledger.py append` and `handoff.py` says nothing
was written and how to turn it on; `unpark.py resolve` with an unreadable `--answers` file names the expected
shape; `decision_gate.py init` on an existing registry says what to do next.

Pull requests: #1286 (A), #1287 (B), #1293 (C), #1294 (D).

## Filed, not fixed

- #1288: Windows stance (file locking raises with no fix and no handler; needs a supported-platform decision).
- #1289: no documented way to resume a parked goal in local-goals mode.
- #1290: gh failures reach the user as raw stderr; a remedy table keyed on `gh_api.classify()` is a design.
- #1291: opt-in refusals with no fix (board setup and layout, `setup.py hooks` and `labels`, the wizard's pip step,
  `define declare` per-issue rows, the second-plugin warning in `init_flow.py`), plus three rare ones commented
  on it: the opt-in auto-merge arm failure, the ledger publish deferral against a remote that does not exist,
  and hand-corrupted config tracebacks in `verify_detect.write_verify` and `define.py open --config`.

## Reviewed and deliberately unchanged

- `state.done_refusal` "last verify FAILED (exit N)": pinned by equality in `tests/test_state.py`, and the
  `loop.py` wrapper that prints it already appends the `loop.py verify` command.
- The `DONE (degraded read ...)` text and the bare `DONE` token: `supervise_classify.py` reads them.

## Controls

For every fixed clause the guarding test was run with that clause deleted or weakened, and the by-hand reading
of each message is still the system of record for "prints its fix", as in the first record. True results per part:

- Part A (work.py and shared): the first pass ran 23 mutations and 4 survived (a weakened `--force-with-lease`
  clause, the "then re-run" wording of the UNKNOWN-mergeability message, the rebase-deferred tail, and a
  github-config guard whose test injected its own runner); each was fixed by strengthening its test and re-run red.
  The review pass added 8 mutations (the three `gh`-missing checks, the setting hint, the pickle form, the strict
  config read, the shared guard twice), all red, and the last commit added 3 (plan-sync, refused direct merge,
  closed PR), all red. No survivors at the end.
- Part B: 5 mutations, all red.
- Part C: 20 mutations, all red.
- Part D: 7 mutations were run, all red. One clause changed in that PR, the `OFF` line in `handoff.py`, was NOT
  covered by any mutation and no test asserted it (reverting it left every test green); a reviewer found that.
  A `handoff.py ack` test was added afterwards and 2 mutations of that clause were run, both red. So the 9
  mutation count and "no survivors" are true only as of the commit that added that test.

## What could not be reached

- Nothing was run on Windows, Linux, Codex or Cursor; the Windows lock path is read, not run.
- The exact `gh` stderr for 401 and 403 was not captured, so the gh passthrough family is filed, not fixed.
- The `gate_hold` message text, `coexist.doctor_row`, the upkeep landing doctor row, `render_label_report`, and the
  acceptance-record messages live in other modules and were not audited.
- Whether a human-typed goal name containing `/` or `..` reaches an unhandled `ValueError` in `loop.py claim` was
  not traced.
- `gh_api.py` (119 sites) and the unreachable counts for `work.py` are grep-derived.
- No outsider was asked to hit any of these; the claim is the weaker one D7 already states.

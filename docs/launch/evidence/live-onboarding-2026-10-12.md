# Live onboarding drills, 2026-10-12 (#300 and #301)

One run each, on one machine. These are anecdotes with measured numbers, not a benchmark. Nothing
here was run on Linux, Codex or Cursor.

## Setup

- Sigma: plugin files exported from origin/main at 03fa8189 (`git archive origin/main .claude-plugin
  skills hooks`), loaded with `claude --plugin-dir`. The README marketplace install was NOT used, so
  the install step of the Quickstart is not covered by this run (the isolated-profile install is
  recorded in `docs/onboarding-control.md`). A per-call `--settings` file set every plugin in the
  owner's profile to disabled for these calls; no profile, plugin or marketplace config was changed.
- Host: Claude Code 2.1.284 on macOS (Darwin 25.6), `claude auth status`: `claude.ai`, subscription
  `max`. No API key was set (`apiKeySource: None` in every result). Model `claude-sonnet-5-5`; one
  run also used `claude-haiku-4-5-20251001` for its phase agents (the loop chose that tier).
- Headless: `claude -p "<prompt>" --plugin-dir <plugin> --settings <all-plugins-off> --permission-mode
  auto|acceptEdits --max-budget-usd N`, no bypass flag. Headless means no human could answer a
  question, so init's answers were given in the prompt. The session inherited the host environment
  (a nested session); `phase_report.py end` nevertheless MEASURED cost on every phase, unlike the
  script control, which printed `cost: unavailable`.
- Dollar figures are the host's `total_cost_usd`/`costUSD` at list price on a subscription login:
  an estimate, not billing.

## #300: local-goals mode, live model

Fresh scratch git repo, no remote: `README.md`, `Makefile` (`test:` -> `test -s hello.txt`).

### `/sigma-init --demo` (22 s wall, 3 turns, $0.127, 0 permission denials, exit 0)

The one question it asked, verbatim:

```
[ask] verify: re-run with --verify N:ID (a candidate above), --verify-command-file FILE (your own), or --no-verify -> --verify N:ID ; --verify-command-file FILE ; --no-verify
```

Answered with `--verify 1:22cc66aa7d26`. Mode and work (`--mode local-goals --local-only`) were passed
as flags and never re-asked. Messages, verbatim:

```
sigma-init: scaffold - 7 created, 0 kept
sigma-init: demo goal queued - `.sdlc/goals/0000-demo.md`. Run `/sigma-loop` to watch the SDLC run it end to end (Goal -> Research -> ... -> Review).
sigma-init: verify command - detected `make test` (Makefile test target runs 'test -s hello.txt') [id 22cc66aa7d26].
verify.enforce stays OFF until you confirm one.
verify: command = "make test", enforce ON (Git-local shell trust recorded; loop.py verify runs it; record done needs it green)
```

No refusal. Then one goal was filed by hand as `.sdlc/goals/0002-onboarding-hello.md` (`Add hello.txt`,
"Create hello.txt with one line.") and the scaffold committed.

### `/sigma-loop`, attempt 1: `--permission-mode acceptEdits` (17 s, $0.128): did nothing

The model could not start the loop. Verbatim from its result: `The Bash tool rejects any command that
contains shell expansion, such as $PPID or $(...), with the errors "Contains shell syntax (string) that
cannot be statically analyzed" and "Contains simple_expansion".` 3 permission denials, all on the
`--session-pid "$PPID"` gesture. No state changed. Known: #721 (also #685 item 3).

### `/sigma-loop`, attempt 2: `--permission-mode auto` (286 s wall, 4 turns reported)

Final message, verbatim: `LOOP STOP: backlog-empty` / `1 done, 0 parked, 1 failed.` (In every quoted block in this file, `<scratch repo>`, `<repo>`, `<plugin>`, `<worktree>` and `<sha>` are redactions of local paths and hashes, not text the tool printed.)

- `0002 Add hello.txt`: **done** (`hello.txt` = `hello`, verify pass, review approved).
- `0000 Demo`: **failed**. `loop.py verify` printed `FAILED exit=2 evidence=.sdlc/state/verify/0000-demo.json`;
  the evidence `command` is `["make test", "python3 -c ... sigma-demo.md"]`, tail `test -s hello.txt` /
  `make: *** [test] Error 1`. Both the repository command and the goal's command ran, although the demo
  goal says its own command "wins over config `verify.command`". Filed as #1196. Acceptance asked for the
  demo goal AND one filed goal to reach `done`: **only the filed goal did.**
- Other messages, verbatim: `acceptance.py: REFUSED: no Done when section; draft 3–7 criteria in a file and pass --draft`;
  `acceptance.py: REFUSED: acceptance requires 3–7 statements of at most 1024 characters each`;
  `loop: witness skipped (non-fatal): ## Tests requires one backticked pytest selector per bullet`;
  `loop.py verify: goal '.sdlc/goals/0000-demo.md' has no work record — verifying the PROJECT ROOT (<scratch repo>) at <sha>, NOT a goal worktree. If that checkout is stale, this result is about the wrong code.`
- `work.enabled` is off in local-only, so nothing was committed by the loop (expected).

Measured by the host, whole attempt 2 (`modelUsage`, claude-sonnet-5-5): input 196, output 30,087,
cache read 4,717,773, cache creation 271,614 tokens; `total_cost_usd` $2.018. The result's top-level
`usage` block (8 in, 1,279 out) covers only the final turn and must not be read as the total.
12 subagent (Agent) calls. `permission_denials`: 0.

`phase_report.py end` lines (verbatim; none was `cost: unavailable`). Sum of these 12 lines $0.82;
the other ~$1.20 of the $2.018 is the orchestrating session, which no phase line attributes:

```
**0000-demo Demo - write a Sigma hello note** P2 RESEARCH → P3 PLAN — 23s · $0.10 · tokens 8 in, 180 out · claude-sonnet-5-5 · goal so far: active 23s · effort 23s.
**0000-demo Demo - write a Sigma hello note** P3 PLAN → P4 PLAN-REVIEW — 21s · $0.07 · tokens 8 in, 868 out · claude-sonnet-5-5 · goal so far: active 44s · effort 44s.
**0000-demo Demo - write a Sigma hello note** P4 PLAN-REVIEW → P5 IMPLEMENT — 20s · $0.07 · tokens 8 in, 122 out · claude-sonnet-5-5 · goal so far: active 1m04s · effort 1m04s.
**0000-demo Demo - write a Sigma hello note** P5 IMPLEMENT → P6 REVIEW — 15s · $0.06 · tokens 8 in, 66 out · claude-sonnet-5-5 · goal so far: active 1m19s · effort 1m19s.
**0000-demo Demo - write a Sigma hello note** P6 REVIEW → P7 RETRO — 17s · $0.07 · tokens 10 in, 134 out · claude-sonnet-5-5 · goal so far: active 1m36s · effort 1m36s.
**0000-demo Demo - write a Sigma hello note** P7 RETRO → last phase — 17s · $0.06 · tokens 8 in, 78 out · claude-sonnet-5-5 · goal so far: active 1m53s · effort 1m53s.
**0002-onboarding-hello Add hello.txt** P2 RESEARCH → P3 PLAN — 22s · $0.05 · tokens 6 in, 356 out · claude-sonnet-5-5 · goal so far: active 22s · effort 22s.
**0002-onboarding-hello Add hello.txt** P3 PLAN → P4 PLAN-REVIEW — 18s · $0.08 · tokens 8 in, 1,553 out · claude-sonnet-5-5 · goal so far: active 40s · effort 40s.
**0002-onboarding-hello Add hello.txt** P4 PLAN-REVIEW → P5 IMPLEMENT — 22s · $0.08 · tokens 10 in, 163 out · claude-sonnet-5-5 · goal so far: active 1m02s · effort 1m02s.
**0002-onboarding-hello Add hello.txt** P5 IMPLEMENT → P6 REVIEW — 13s · $0.06 · tokens 8 in, 61 out · claude-sonnet-5-5 · goal so far: active 1m16s · effort 1m16s.
**0002-onboarding-hello Add hello.txt** P6 REVIEW → P7 RETRO — 14s · $0.06 · tokens 8 in, 108 out · claude-sonnet-5-5 · goal so far: active 1m29s · effort 1m29s.
**0002-onboarding-hello Add hello.txt** P7 RETRO → last phase — 14s · $0.06 · tokens 8 in, 335 out · claude-sonnet-5-5 · goal so far: active 1m43s · effort 1m43s.
```

Time from first init call to the filed goal `done`: 22 s init + 17 s failed attempt + 286 s loop =
325 s of agent wall time, not counting my own pauses between calls (unmeasured as one continuous
interval).

## #301: github mode, real GitHub

Repository `Agrim-Intelligence/sigma-drill-3`, created PRIVATE (`private: true`, `auto_init`), plus one
commit adding `Makefile` (`test:` -> `test -s hello.txt`). Token scopes: gist, project, read:org,
repo, user, workflow, write:packages (no `delete_repo`; the owner deletes the repository by hand).

### `/sigma-init --demo` (32 s, 3 turns, $0.130, 0 denials)

Same single `[ask] verify` question as above, answered `--verify 1:22cc66aa7d26`. Github mode
(`--mode github`), board no, ledger no. The model's own summary says 13 labels were created on the first run and all present on the second (that is its prose, not a captured command). Captured afterwards: `gh label list -R Agrim-Intelligence/sigma-drill-3` returns 22 labels, of which 13 start with `sdlc:` or `priority:`; the other 9 are GitHub's defaults.
Verbatim:

```
github mode: file it as an issue - gh issue create --label sdlc:goal --title "[Demo] Sigma" --body "<paste the demo goal body>" - then /sigma-loop picks it (no board: mirroring is off; /sigma-init --board yes creates and pins one).
Next: label an issue `sdlc:goal` (assigned to you), then /sigma-loop (Codex/Cursor: python3 <plugin>/skills/sigma-loop/scripts/loop.py next <repo>/.sdlc)
```

So in github mode `--demo` does not queue anything the loop runs; the user must file the issue. The one
goal, `gh issue create --label sdlc:goal --assignee @me --title "Add hello.txt" --body "Create hello.txt with one line."`,
was filed per the runbook (issue #1).

### `/sigma-loop` (440 s wall, `--permission-mode auto`, 0 denials)

Final message, verbatim: `LOOP STOP: backlog-empty` / `0 done, 0 parked, 0 failed. 1 goal is waiting on a human merge.`
PR #2 opened on `sdlc/1`, `sigma:approve` comment with a `sigma-review-evidence` marker posted, goal
recorded `review`, `auto_merge` off so the PR was left open. `mergeStateStatus` CLEAN, `reviewDecision`
empty (approval is a comment, not a GitHub review). PR title: `chore: add research and acceptance record`
(the last commit's headline, not the goal; existing issue #1014).

Four refusal messages on the way to the PR, verbatim. They group as three problems: (1) no Done when section, (2) the research dossier not on the branch, (3) the test-first gate, whose two messages (`acceptance record changed since verify`, then `cannot validate planned test proof`) are one item:

```
acceptance.py: REFUSED: no Done when section; draft 3–7 criteria in a file and pass --draft
this goal's research dossier is not on the branch: .sdlc/research/1.md exists but sdlc/1 does not carry it — copy it to <repo>/.sdlc/work/1/.sdlc/research/1.md, `work.py commit`, then re-run (nothing pushed)
TEST-FIRST REFUSED: acceptance record changed since verify; re-run verify against the recorded intent; run `loop.py verify` on the planned tests while red, then green; or supply --no-tests <reason> for an explicit exception (nothing pushed)
TEST-FIRST REFUSED: cannot validate planned test proof: ## Tests requires 1–25 pytest selectors; use --no-tests for an explicit exception; run `loop.py verify` on the planned tests while red, then green; or supply --no-tests <reason> for an explicit exception (nothing pushed)
```

The model recovered by drafting acceptance after the work, copying research and acceptance into the
worktree by hand, and opening the PR with `--no-tests "project has no pytest suite; proof is the
Makefile test (test -s hello.txt), verified green"`. Its own report lists the late acceptance record,
a `work.py merge` that parked for out-of-order phases (review and retro were re-run), and plan edits
applied before the plan-review record. Filed as #1197.

Measured by the host: `modelUsage` claude-sonnet-5-5 input 92, output 13,608, cache read 3,245,171,
cache creation 67,197 ($1.054); claude-haiku-4-5-20251001 input 588, output 20,747, cache read
1,743,064, cache creation 117,770 ($0.426); `total_cost_usd` $1.480. 9 subagent calls. Phase lines
(verbatim; 8 lines for 6 phases, review and retro appear twice because they were re-run; sum $0.31):

```
**#1 Add hello.txt** P2 RESEARCH → P3 PLAN — 22s · $0.05 · tokens 50 in, 724 out · claude-haiku-4-5-20251001 · goal so far: active 22s · effort 22s.
**#1 Add hello.txt** P3 PLAN → P4 PLAN-REVIEW — 14s · $0.02 · tokens 44 in, 806 out · claude-haiku-4-5-20251001 · goal so far: active 36s · effort 36s.
**#1 Add hello.txt** P4 PLAN-REVIEW → P5 IMPLEMENT — 1m25s · $0.10 · tokens 132 in, 2,110 out · claude-haiku-4-5-20251001 · goal so far: active 2m00s · effort 2m00s.
**#1 Add hello.txt** P5 IMPLEMENT → P6 REVIEW — 28s · $0.04 · tokens 84 in, 757 out · claude-haiku-4-5-20251001 · goal so far: active 2m28s · effort 2m28s.
**#1 Add hello.txt** P6 REVIEW → P7 RETRO — 19s · $0.02 · tokens 44 in, 401 out · claude-haiku-4-5-20251001 · goal so far: active 2m46s · effort 2m46s.
**#1 Add hello.txt** P7 RETRO → last phase — 22s · $0.03 · tokens 58 in, 450 out · claude-haiku-4-5-20251001 · goal so far: active 3m08s · effort 3m08s.
**#1 Add hello.txt** P6 REVIEW → P7 RETRO — 23s · $0.02 · tokens 44 in, 91 out · claude-haiku-4-5-20251001 · goal so far: active 3m31s · effort 3m31s.
**#1 Add hello.txt** P7 RETRO → last phase — 19s · $0.03 · tokens 42 in, 1,193 out · claude-haiku-4-5-20251001 · goal so far: active 3m49s · effort 3m49s.
```

### Human merge and reconcile (12 s is the merge.start to merge.end span: merge plus reconcile plus my checks)

Run by me in the throwaway repository, as the contract allows. The block below was typed in an interactive terminal and is NOT captured in any raw log; the captured record is the loop's own action-log row for the goal: `PR #2 merged (observed by the merge-reconcile pass)`, `result: done`, `ts 2026-10-12T01:34:03.576Z`.

```
gh pr merge 2 --squash -R Agrim-Intelligence/sigma-drill-3          # exit 0
python3 <plugin>/skills/sigma-loop/scripts/loop.py reconcile-merges .sdlc
loop: removed <repo>/.sdlc/work/1, deleted branch sdlc/1
work.py: pruned 1 review generation(s) of 1 terminal goal(s)
1 done (PR #2 merged)
```

`reconcile-merges` took 6.9 s wall (interactive terminal, not captured to a log; the merge-to-reconcile-record gap is about 6 s). Checks: issue #1 `CLOSED` with no labels left; PR #2 `MERGED`;
`hello.txt` on `main` via the contents API reads `Hello, world!`; `.sdlc/state/verify/1.json` reads
`verify_state: pass`, `exit: 0`, command `make test`. The squash commit on main carries the PR title
`chore: add research and acceptance record (#2)`.

`git pull` in the working clone then ABORTED (interactive terminal, not captured to a log; the file names are the PR's own committed files `.sdlc/acceptance/1.md`, `.sdlc/plans/1.md`, `.sdlc/research/1.md`): `error: The following untracked working tree files would
be overwritten by merge: .sdlc/acceptance/1.md .sdlc/plans/1.md .sdlc/research/1.md`. The loop
recreates the files locally and the PR also commits them. Existing issue #1161.

## Deviations from the script control's expected output

| Control (fake gh / scripted work) | Live | Where filed |
|---|---|---|
| Demo goal reaches `done` | demo goal `failed`: both verify commands ran | #1196 |
| Acceptance recorded before the work; `work.py pr` passes | acceptance drafted after the work, four refusal messages in three groups, `--no-tests` | #1197 |
| `phase_report.py end` prints `cost: unavailable ...` (nested session) | cost measured on every phase (28 lines: 12 local + 8 github + 8 vanilla re-run) | recorded here, not a defect |
| `loop next` runs the demo goal first in github mode | github-mode `--demo` queues nothing the loop picks; init says to file an issue | recorded here (init says so itself) |
| No `$PPID` problem (script control) | `acceptEdits` denies `--session-pid "$PPID"`; the loop did nothing | #721 (commented) |
| PR title is the goal | PR title is the last commit headline | #1014 (commented) |
| Working clone stays fast-forwardable | `git pull` aborts on untracked `.sdlc` records | #1161 (commented) |
| 86 gh calls, 34 label creates, 14 of them init's | not counted live (unmeasured); init created 13 sdlc:/priority: labels on first run (model summary; 22 labels captured later, 9 are GitHub defaults) | |
| Board offer, `@me` resolution | `@me` resolved (assignee set at filing, the goal was picked). Board declined; the Projects board path was NOT exercised | |

## #300 re-run on a vanilla repo (second dated subsection, 2026-10-12)

Same rules as above, run after the coordinator pointed out that the first demo failure came from the scratch
repo's own `make test` needing `hello.txt` (#1196). Plugin re-exported from origin/main cc92a834 (main had moved).
Fresh local scratch repo, no remote, `README.md` and a `Makefile` whose `test` target is `true`; nothing else.
Answers are what a first-time user would give: local-goals, local only, accept the detected command.

### `/sigma-init --demo` (19 s, 3 turns, $0.127 host list-price estimate, 0 denials)

Without any answer flags in the prompt it asked three questions, verbatim:

```
[ask] work: fix the remote above, or re-run with --local-only -> --local-only
[ask] mode: local-goals (goal files) or github (issues)? default: local-goals (origin is not a GitHub repository). -> --mode github|local-goals
[ask] verify: re-run with --verify N:ID (a candidate above), --verify-command-file FILE (your own), or --no-verify -> --verify N:ID ; --verify-command-file FILE ; --no-verify
```

Messages, verbatim: `[FAIL] git remote 'origin': no remote named 'origin' (this repository has no remote at all)`;
`DECISION: work.enabled is ON, but this repository has no usable remote, so no goal can be pushed or opened as a PR.`;
``detected `make test` (Makefile test target runs 'true') [id 22cc66aa7d26]. verify.enforce stays OFF until you confirm one.``;
final `Next: /sigma-loop`. Answers: `--local-only`, `--mode local-goals`, `--verify 1:22cc66aa7d26`. No refusal.

### `/sigma-loop`, `--permission-mode auto` (160 s wall, 3 turns reported, 0 denials)

Result, verbatim: `LOOP STOP: backlog-empty` / `1 done, 0 parked, 0 failed.` **The demo goal reached `done`**:
frontmatter `status: done`, `sigma-demo.md` holds the one-line note, `loop.py verify` printed
`VERIFIED exit=0`. Only the demo goal was queued, so no second goal ran. `work.enabled` is off, so nothing
was committed.

Two refusals on the way, verbatim (the model recorded the review verdict after retro, then re-ran review and
retro; filed as #1199):

```
REFUSED: the SDLC phase record is incomplete (README: every goal runs its seven phases):
  - review: no verdict recorded -> python3 "${CLAUDE_SKILL_DIR}/scripts/work.py" record-review <sdlc> <goal> --verdict APPROVE|SEND-BACK|BLOCK --agent-id <reviewer agentId>
  lever: for a trivial goal, `loop.py waive-phases <sdlc> <goal> research,retro --reason "<why>"` (recorded, visible; only research and retro can be waived); the whole gate is `gates.phase_record.enabled: false` in .sdlc/config.json
REFUSED: the SDLC phase record is incomplete (README: every goal runs its seven phases):
  - review: the review verdict was recorded AFTER retro -> re-run the phases in order: a change after its review needs a fresh review
  lever: (same line as above)
```

Host-reported figures (list-price estimate on a subscription login, not billing): `modelUsage` claude-sonnet-5-5
input 124, output 13,742, cache read 2,751,538, cache creation 190,786 tokens; `total_cost_usd` $1.244.
8 subagent calls. `phase_report.py end` lines, verbatim (8 lines for 6 phases, review and retro twice; sum
$0.53; none `cost: unavailable`; the other ~$0.71 is the orchestrating session):

```
**0000-demo Demo - write a Sigma hello note** P2 RESEARCH → P3 PLAN — 19s · $0.11 · tokens 8 in, 292 out · claude-sonnet-5-5 · goal so far: active 19s · effort 19s.
**0000-demo Demo - write a Sigma hello note** P3 PLAN → P4 PLAN-REVIEW — 15s · $0.06 · tokens 8 in, 331 out · claude-sonnet-5-5 · goal so far: active 33s · effort 33s.
**0000-demo Demo - write a Sigma hello note** P4 PLAN-REVIEW → P5 IMPLEMENT — 16s · $0.06 · tokens 8 in, 220 out · claude-sonnet-5-5 · goal so far: active 50s · effort 50s.
**0000-demo Demo - write a Sigma hello note** P5 IMPLEMENT → P6 REVIEW — 14s · $0.06 · tokens 8 in, 281 out · claude-sonnet-5-5 · goal so far: active 1m03s · effort 1m03s.
**0000-demo Demo - write a Sigma hello note** P6 REVIEW → P7 RETRO — 12s · $0.06 · tokens 6 in, 259 out · claude-sonnet-5-5 · goal so far: active 1m15s · effort 1m15s.
**0000-demo Demo - write a Sigma hello note** P7 RETRO → last phase — 14s · $0.06 · tokens 8 in, 257 out · claude-sonnet-5-5 · goal so far: active 1m29s · effort 1m29s.
**0000-demo Demo - write a Sigma hello note** P6 REVIEW → P7 RETRO — 11s · $0.07 · tokens 8 in, 383 out · claude-sonnet-5-5 · goal so far: active 1m40s · effort 1m40s.
**0000-demo Demo - write a Sigma hello note** P7 RETRO → last phase — 10s · $0.05 · tokens 6 in, 187 out · claude-sonnet-5-5 · goal so far: active 1m50s · effort 1m50s.
```

Agent wall time for the clean first run: 19 s init + 160 s loop = 179 s, excluding my pauses between calls
(unmeasured as one continuous interval). One run, one machine; no outside person.

## Not measured or not run

Linux; Codex; Cursor; the README marketplace install in a live session (`--plugin-dir` instead); a run
by a person outside the team (D7); a continuous first-run clock from install to merged PR; the gh call
count of the live run; billing (subscription login, list-price estimates only); whether the model
tier chosen for the github run (haiku) was derived by Sigma or by the model ("I chose haiku from the
goal's size", its words). Repository `Agrim-Intelligence/sigma-drill-3` remains for the owner to delete
by hand.

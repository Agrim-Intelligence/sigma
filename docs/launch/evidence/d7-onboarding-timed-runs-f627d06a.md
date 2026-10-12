# D7: timed first runs by agents, local-goals mode (f627d06a, 2026-10-12)

Three timed runs of an AI agent (Claude Code, headless) from a fresh plugin copy and a fresh repository to
two goals `done`. One machine (macOS, Darwin 25.6), one mode. **These are agent runs, not runs by people;
the claim they support is weaker than "outside people" and says nothing about how a person new to Sigma
would fare** (see [the OnChart record](d7-onchart-owner-statement-2026-10-12.md); the outsider test, #353,
was not done and is `launch:next`).

## Result against the threshold

Threshold (`docs/launch/review-plan.md`, D7, amended 2026-10-12): median at most 30 minutes to first goal
done on the agent runs.

| run | setup | `/sigma-init --demo` | `/sigma-loop` to LOOP STOP | total (setup to stop) | goals done at the stop |
|---|---|---|---|---|---|
| 1 | under 1 s | 26 s | 585 s | 611 s (10:11) | 2 of 2 |
| 2 | under 1 s | 20 s | 365 s | 385 s (6:25) | 2 of 2 |
| 3 | under 1 s | 22 s | 535 s | 557 s (9:17) | 2 of 2 |

**Median total: 557 s (9:17), against 1800 s. The threshold is met on these runs, by a factor of about
three.** Worst run 10:11.

What the number is. It is wall clock for the whole sequence, which ends when the loop stopped with both goals
`done`, so it is an upper bound on "time to the first goal done"; the moment the first goal reached `done` was
not captured separately. The two goals are the demo goal that `--demo` queued and one goal the agent filed.
Each run did `done` for both (frontmatter `status: done`, `hello.txt` and `sigma-demo.md` present in each repository). The scaffold's
`0001-example.md` stayed `proposed` and was not run in any of the three.

Host-estimated cost (the host's `total_cost_usd` at list price on a subscription login: an estimate, not
billing): init $0.126, $0.125, $0.132; loop $1.61, $1.44, $1.21.

## What was run

- Sigma: the plugin files of origin/main at f627d06a, exported with `git archive` into a fresh directory per
  run and loaded with `claude --plugin-dir`. **The README marketplace install was NOT part of the clock.** The
  "setup" column is the export plus `git init` and one commit. The install line itself is covered by the isolated-profile
  control in `docs/onboarding-control.md`, not timed here.
- Repository: new `git init`, `README.md`, and a `Makefile` whose `test` target is
  `test ! -e hello.txt || test -s hello.txt`. No remote.
- Agent: `claude -p` (Claude Code CLI as recorded in the earlier live drills, the version listed in
  `live-onboarding-2026-10-12.md`) with `--permission-mode auto`, every other plugin of the owner's profile
  disabled for the call through `--settings`, 0 permission denials in all six calls. Two calls per run. The first: run
  `/sigma-init --demo --mode local-goals --local-only` and, if it asks for a verify command, answer with the detected
  candidate. The second: create one goal file `0002-onboarding-hello.md` in the format of the demo goal, then run
  `/sigma-loop` until it stops. The init flags were given in the prompt because a headless call has no human
  to answer; a person would have been asked the same three questions (work, mode, verify).
- Each run is a fresh session with no memory of the previous one. The three ran one after another, not in parallel.

## What this does not measure

- Any person. No human read the README, typed a command, or waited. Reading and decision time is zero here.
- The time to write the first goal: the agent was told the title and body and wrote the file, so the user's
  work of deciding what to ask for is excluded.
- The marketplace install, Linux, Codex, Cursor, Windows.
- github mode. **It was not repeated.** The earlier live github-mode drill ran against a private throwaway
  repository, one run (`live-onboarding-2026-10-12.md`, #301, 440 s for the loop plus a human merge), and that repository
  no longer exists, so no private drill repository was available for three timed runs. No repository was created for this page.
  The scripted onboarding control does cover github mode against a fake `gh`: one run of the whole control
  (local-goals and github, every variant) took 143 s on this machine and was green. That run has no model, so it is not an
  agent run and does not count toward the median above.
- Python: the harness used the system Python 3.9 for the Sigma scripts in the refusal scenarios; the live agent runs
  used whatever interpreter the plugin's hooks resolve, which was not recorded.
- Variance: three runs, range 385 to 611 s. The runs differ in turn count (the first loop call reported 3 turns, the
  others 39 and 41); the host's `duration_ms` in the result covers only part of the call and was not used. Wall
  clock around the process is the figure above.

## Refusals the agent met on the way

No call was blocked by a permission denial. Refusals of Sigma's own (phase record, acceptance, verify) that the agent
met inside the loop were not captured per run; the earlier drill file records those it saw. The list of what a first-run user can hit
is on [the refusal page](d7-onboarding-refusals-f627d06a.md).

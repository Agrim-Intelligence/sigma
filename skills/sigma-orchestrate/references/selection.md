# sigma-orchestrate selection details

description: Drive a batch of GitHub goals in a Sigma project to merged PRs: acknowledge the set, label it as one batch, map dependencies, plan dependency waves, then run one `/sigma-goal <N> --land` session per goal in parallel, merge approved PRs, unblock dependents and report. Follow-ups stay inside their own goal (one full verify per goal). Use for /sigma-orchestrate, "orchestrate these goals", "run these issues in waves", or a list or GitHub filter of goals to run in parallel.
allowed-tools: Bash(python3 *), Bash(gh api *), Bash(git *), Read, Grep

These fuller triggers are kept outside the always-listed description. Read the
skill's main instructions before acting.

Use it when the work is several unrelated goals that should run as separate sessions at once. Use
`/sigma-chain` for one goal and its prerequisites, `/sigma-triage` to plan a drain for one `/sigma-loop`, and
`/sigma-goal <N> --land` for a single goal.

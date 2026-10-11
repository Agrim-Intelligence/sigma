# Resolver CLI probe: findings

This is the committed finding record of the probe that the upkeep resolver slices left open (the flags the launcher refuses by name until a probe confirms them). No product code changed. Every call below was a real headless call of the installed command line, made from a clean empty working directory with a cleaned environment, on one macOS machine, with one small named spend (about $0.5 in total, notional cost as reported by the tool). Nothing was measured on Windows or Linux, and the cost figures are single observations, not averages.

- Tool version: Claude Code 2.1.296.
- Model used: `claude-sonnet-5-5` (the alias `sonnet` was also tried).
- Date: 11 October 2026.

## 1. The design flags

All twelve design flags are listed by the tool's own help. `--max-turns` is not listed. Each flag was then used in one real call that asked for the single word OK.

| Flag | Result | Note |
|---|---|---|
| `--max-budget-usd 0.5` | works | see section 5 for the cap being hit |
| `--permission-mode default` | works | |
| `--allowedTools Read` | works | |
| `--disallowedTools Bash` | works | |
| `--tools ""` | works | an empty value turns every tool off |
| `--settings '{}'` | works | a JSON string is accepted |
| `--setting-sources ""` | works | see section 4: it also stops hooks and instruction files |
| `--strict-mcp-config` | works | |
| `--no-session-persistence` | works | no transcript is written |
| `--add-dir <directory>` | works | |
| `--append-system-prompt "..."` | works | |
| `--bare` | does NOT authenticate | the call answers "Not logged in": it skips the login and needs an API key (section 3) |

`--model` accepts both the alias (`sonnet`) and the full id (`claude-sonnet-5-5`); the reply names the full id.

## 2. The reply envelope

With `--output-format json` the reply is one JSON object. Keys seen: `type`, `subtype`, `is_error`, `result`, `total_cost_usd`, `num_turns`, `duration_ms`, `stop_reason`, `session_id`, `usage` and `modelUsage` (keyed by the full model id). A successful call has `subtype` `success`; a failed login has `is_error` true and the text "Not logged in" in `result`. A one-word call cost between about $0.002 and $0.06, depending on how much of the fixed prompt was served from cache.

## 3. Credential and billing behaviour

- In a normal environment the call authenticates with the user's existing subscription login. In a cleaned environment it also needed `USER`, `LOGNAME`, `SHELL` and `TMPDIR` for the login to be found.
- An isolated profile does not authenticate: a fresh empty configuration directory answered "Not logged in", and so did a fresh home directory with the real configuration directory named. The login lives outside the configuration directory and is found through the home directory.
- Consequence for the launcher, which builds its environment from nothing with a fresh home: a real run needs a credential the operator chooses by name (an API key variable), or a launcher that keeps the real home. This is a decision for the owner and is not settled here.
- `total_cost_usd` is reported on every call, including on a subscription login, as a notional figure. Whether and how a subscription login is billed was not measured.

## 4. Settings, hooks and instruction files in the working directory

A scratch project held an instruction file naming a secret word and a project hook that touches a marker file.

| Flags | Instruction file read | Project hook ran |
|---|---|---|
| defaults | yes (the secret word was answered) | yes |
| `--setting-sources ""` | no | no |
| `--setting-sources "" --strict-mcp-config` | no | no |

So a model started in a repository with default flags executes that repository's hooks and reads its instruction files. `--setting-sources ""` stops both. Any resolver that works inside repository content should pass it, in addition to refusing a working directory with instruction files in an ancestor.

## 5. The budget flag: overshoot and exit code

`--max-budget-usd 0.001` stopped the call: exit code 1, `subtype` `error_max_budget_usd`, no `result`. The turn had already cost $0.048, about 48 times the cap, because the cap is checked between turns. So the flag is a runaway guard, not a hard per-call ceiling, and a launcher that counts spend must count the reported cost of the stopped call.

## 6. The prompt on standard input

`claude -p` with no prompt argument and the prompt on standard input works (exit code 0, the expected reply).

## 7. A killed run

A run killed with SIGKILL after 4 seconds left one transcript file (session persistence was on). It holds queue, user and attachment records and no usage record, so the spend of a turn in flight at the kill is not metered anywhere readable. A launcher must charge a killed run the full per-run cap, as the design says.

## 8. The directory choice: export or a second worktree

Measured: settings isolation (section 4) is a flag, not a property of the directory. Not measured: whether an ancestor's instruction file is read when the working directory has none. Recommendation, not measurement: a plain export of the stopped tree (no `.git`, no instruction files, no hooks) under a directory with no instruction file in any ancestor, plus `--setting-sources ""`, is the simpler isolation than a second worktree, because a worktree carries the repository's own settings and hooks into the model's reach.

## What this settles and what it leaves open

- Settled: the flag table (eleven of twelve work), the envelope, prompt on standard input, the budget overshoot and exit code, killed-run metering, settings isolation.
- Left to the owner: the credential route and billing basis (section 3), and the model catalog ids, which the launcher still keeps as a placeholder.
- Not covered: a real resolver run on a real conflict (it needs the credential decision), Windows and Linux, and any run on a hosting service.

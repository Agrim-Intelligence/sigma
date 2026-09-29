# The onboarding control

`tools/onboarding_control.py` answers one question with a run, not a reading: does the README
Quickstart, followed literally on a fresh repository, get one goal to `done`? It is the control for
epic #227 (plugin install -> `/agrim-init` -> one goal to done), built in #237.

It runs in CI on every push (`tests/test_onboarding_control.py`, Linux, Python 3.10 and 3.12), with
no secrets, no network and no model session.

## Run it

```
python3 tools/onboarding_control.py                         # both modes, from this checkout
python3 tools/onboarding_control.py --mode local            # local-goals mode only
python3 tools/onboarding_control.py --install all --from-install --json run.json
```

`--install claude|codex|all` runs the README's `claude plugin ...` / `codex plugin ...` lines into an
ISOLATED profile (HOME, `CLAUDE_CONFIG_DIR` and `CODEX_HOME` all inside the run's temp directory)
and records a hash of the real profile's plugin surface (`~/.claude/plugins`,
`~/.claude/settings.json`, `~/.codex/config.toml`, `~/.codex/plugins`) before and after. A host CLI
that is not installed is reported `skipped`, never green. `--from-install` runs every script from
the installed Claude Code copy instead of the checkout. `--sigma DIR` runs another Sigma tree (a
scratch copy for a control); `--readme FILE` follows another README (a drift control).

Exit 0: every mode reached `done` and every assertion held. Exit 1: red; the JSON names the first
failing step. Exit 2: a precondition is missing (`git` or `make` not on PATH, or Windows).

## What it follows, step by step

The commands come from README.md, parsed at run time, so a README that drifts from the shipped
scripts turns the control red:

| From the README | What the control does with it |
|---|---|
| `claude plugin marketplace add <SIGMA_REPO>` / `claude plugin install sigma@sigma` | `--install`: runs them into the isolated profile, `<SIGMA_REPO>` = the checkout |
| `codex plugin marketplace add <SIGMA_REPO>` / `codex plugin add sigma@sigma` | `--install`: the same, into an isolated `CODEX_HOME` |
| `/agrim-init --demo` (the `### Claude Code` block) | its flags |
| `python3 <installed-sigma>/skills/agrim-init/scripts/init_flow.py . ...` | the script `/agrim-init` runs |
| `/agrim-loop` (the `### Claude Code` block) | must be present; the loop is then driven by the scripts the agrim-loop skill names |
| `python3 skills/agrim-init/scripts/verify_detect.py confirm .sdlc <n> <id>` | run with `<n>` and `<id>` copied from init's printed candidate |

Everything else comes from what `/agrim-init` prints: every `[ask]` line is answered with the flag
it names and init is re-run (github mode's board and ledger questions only appear once the mode is
answered, so this takes up to three rounds), and the `Next:` line's `loop.py next` command is the
one the loop runs.

**Local-goals mode.** A fresh `git init` repository with no remote and a `Makefile` whose `test`
target checks `hello.txt` (so the confirmed verify command fails before the work and passes after
it). A `gh` stub on PATH logs every call. Init answers: `--mode local-goals`, `--local-only`. Then:
confirm `make test` -> file `.sdlc/goals/0002-onboarding-hello.md` (no frontmatter
`verify_command`, so config's command is what proves it) -> `loop.py start` -> `loop.py next`
until `DONE`, and for each goal: `agent-start`, `phase_report.py start/end implement`, the work,
`loop.py verify`, `loop.py record done`. The demo goal `--demo` queued runs first.

Asserted per goal: `record done` exit 0; frontmatter `status: done`; verify evidence
(`.sdlc/state/verify/<goal>.json`) `verify_state: pass`, `exit: 0`, a non-empty command; the loop
made no `gh` call. And the control's own goal is among those done.

**github mode.** A local bare `origin`, a clone with the same Makefile, and the stateful fake `gh`
from `tests/test_public_bootstrap_control.py` (extracted by `ast`, so the control and that test
share one fake). Init answers `--mode github --repo acme/onboarding-demo`, then `--board no
--ledger no`; the adopted `.sdlc/` is committed and pushed. Then: `gh issue create` labelled
`sdlc:goal`, assigned `@me` -> `loop.py start/next` -> `agent-start` -> `work.py start` ->
`phase_report.py` -> the work in the goal worktree -> `loop.py verify` -> `work.py commit`, `pr` ->
a reviewer's `sigma:block` comment -> `work.py merge` (must park) -> `sigma:approve` ->
`work.py merge` (passes the review gate, leaves the PR for a human on the shipped
`auto_merge: off`) -> `loop.py record done` (must be REFUSED, exit 4: #232 done means merged) ->
`record review` -> the human's `gh pr merge` -> `loop.py reconcile-merges`.

Asserted: the review gate parked on `sigma:block` and passed once approved; `record done` refused
while the PR was open; issue open and PR open before the merge; reconcile printed
`<n> done (PR #<pr> merged)`; issue closed; PR merged; `hello.txt` on `origin/main`; verify evidence
passed; and every gh call was one the fake models. The gh calls themselves are recorded by kind.

## Recorded runs

2026-09-29, macOS (Darwin 25.6), Python 3.12.13, from the INSTALLED Claude Code copy
(`--install all --from-install`; the marketplace install takes the checkout's committed HEAD, not
its working tree). Whole run 15.4s. Durations are wall-clock per step, measured by the control.

Host install (isolated profile):

| Step | Seconds | Result |
|---|---|---|
| `claude plugin marketplace add <SIGMA_REPO>` | 0.816 | ok |
| `claude plugin install sigma@sigma` | 0.793 | ok, 1.0.0 |
| `codex plugin marketplace add <SIGMA_REPO>` | 0.058 | ok |
| `codex plugin add sigma@sigma` | 0.097 | ok, 1.0.0 |
| real profile plugin-surface hash before / after | | `b4844e1dc784e0ab` / `b4844e1dc784e0ab`: untouched |

The Codex CLI is not on PATH on this machine; the control found it in the ChatGPT app bundle
(`codex-cli 0.154.0-alpha.6.2`). Install works there; a live Codex session was not run (see below).

Local-goals mode, GREEN in 3.05s:

| Step | Seconds |
|---|---|
| init (README flags: `--demo`) — exit 1, asks mode, work, verify | 0.665 |
| init (answers `--mode local-goals --local-only`) | 0.156 |
| verify confirm (README gesture) -> `make test`, enforce ON | 0.029 |
| loop start | 0.112 |
| loop next -> `0000-demo.md` | 0.326 |
| agent-start / phase_report start / end / loop verify / record done | 0.094 / 0.066 / 0.095 / 0.196 / 0.109 |
| loop next -> `0002-onboarding-hello.md` | 0.323 |
| agent-start / phase_report start / end / loop verify / record done | 0.094 / 0.069 / 0.096 / 0.161 / 0.119 |
| loop next -> `DONE` | 0.293 |

gh calls in local-goals mode: one, `gh auth status`, made by init's preflight in the FIRST round,
before the mode was answered. The loop made none.

github mode, GREEN in 9.55s:

| Step | Seconds |
|---|---|
| init (README flags) / (answers: mode) / (answers: board, ledger) | 0.237 / 1.218 / 0.303 |
| verify confirm (README gesture) | 0.032 |
| gh issue create (`sdlc:goal`, `@me`) | 0.026 |
| loop start / loop next -> `1` | 0.153 / 4.398 |
| agent-start / work start | 0.105 / 0.272 |
| phase_report start / end | 0.065 / 0.095 |
| loop verify / work commit / work pr | 0.268 / 0.247 / 0.332 |
| work merge on `sigma:block` (parked) / on `sigma:approve` (gate passed) | 0.315 / 0.404 |
| record done while the PR is open (REFUSED, exit 4) / record review | 0.169 / 0.126 |
| human merge / reconcile-merges (`1 done (PR #100 merged)`) | 0.066 / 0.444 |

`loop next` is 4.4s of the 9.5s: the backlog read's empty-read backoff, the same cost the bootstrap
control measured.

gh calls, by kind (one goal cycle): `label create` 34, `pr view` 9, `api repos/<repo>/issues` 8,
`api graphql` 7, `issue view` 4, `api repos/<repo>/labels` 3, `api .../pulls/N` 3, `issue list` 3,
`repo view` 3, `api .../pulls` 2, `api user` 2, `pr comment` 2, and one each of `issue create`,
`issue edit`, `issue comment`, `issue close`, `pr merge`, `api repos/{owner}/{repo}`,
`api repos/<repo>/issues/N`, `api .../git/refs/heads/sdlc/N`. Unmodelled calls: none. Of the 34
label creates, 14 are init's (fresh repository, all needed) and 20 are two passes of the loop's
lifecycle-label self-heal against labels that already exist, each answered 422: filed as #304.

**Tokens and cost: N/A.** No model session ran; this is a script-level control. `phase_report.py
end` did run at every phase boundary and printed its honest line, recorded as-is:
`cost: unavailable on this host (phase ran inline within a nested session; no top-level transcript
to window ...)`.

CI shape: `tests/test_onboarding_control.py`, 12 tests, 23.4s on Python 3.12 and 18.0s on 3.10
(this Mac, from the checkout).

## The controls, seen red

Each was run through the CLI gesture above, not only through pytest, and each is also a test.

| Control | Gesture | Result |
|---|---|---|
| The empty-verify-command default reintroduced: in a scratch copy, `verify_detect.write_verify` writes `enforce: true, command: ""` again, its guard removed | `--mode local --sigma <scratch>` | exit 1, **RED at `record done`** (5.8s): the demo goal (its own `verify_command`) is done; the control's goal gets `loop.py verify` NO-COMMAND exit 3, then `record done` REFUSED exit 4 |
| The same default reintroduced in the TEMPLATE only (`config.json.tmpl` `enforce: true`) | `--mode local --sigma <scratch>` | GREEN: init rewrites `verify` to enforce OFF on scaffold, and the confirm sets the command. The trap now needs `write_verify` itself to regress |
| README drift: `verify_detect.py confirm` renamed `accept` | `--mode local --readme <copy>` | exit 1, RED at `verify confirm (README gesture)` |
| README drift: `init_flow.py` renamed `init.py` | `--mode local --readme <copy>` | exit 1, RED at `readme` (the README names a script that does not ship) |
| README drift: `/agrim-loop`, `/agrim-init` or `claude plugin install` renamed | pytest (parse) | RED at `readme` |
| Every assertion in `check_local` (5) and `check_github` (11) broken once, alone, against a real green run's observations | pytest | each one false, all others true |

The `/agrim-init` drift case caught the control's own first draft: it took the first `/agrim-init`
line anywhere in the Quickstart, so with the first-run line renamed it silently used the "Adopting
into an existing repo" line instead. It now reads the first-run pair from the `### Claude Code`
block only.

## What this does NOT cover

Each gap is an issue, not a silent skip:

| Not covered | Why | Issue |
|---|---|---|
| A live-model `/agrim-loop` turn in Claude Code | spends tokens; the control drives the scripts the skill names instead | #300 |
| Real GitHub (labels, `@me`, protection, mergeability, the board offer, token scopes) | the control never creates external state; github mode uses the fake gh | #301, runbook below |
| A live Codex session (the `[ask]` relay, the loop driven from `AGENTS.md`) | install measured working; a session spends tokens | #302 |
| A live Cursor session (`.cursor/rules/sdlc.mdc` driving a goal) | no plugin system to install; a session spends tokens | #303 |
| Windows | POSIX-only (a `make` target, a `#!` fake gh); the module skips there | #305 |

## OWNER RUNBOOK: real GitHub mode

Run by a human, attended, once per release that touches onboarding (#301). It creates a throwaway
PRIVATE repository and deletes it at the end; no agent runs the deletion.

```
# 0. a scratch directory and names (<org> is the org you test under)
export T=$(mktemp -d) R=<org>/sigma-onboarding-$(date +%Y%m%d%H%M)
# 1. the throwaway private repository, with one commit and a Makefile verify target
gh repo create "$R" --private --clone --add-readme --description "sigma onboarding control (throwaway)" && cd "$(basename "$R")"
printf 'test:\n\ttest -s hello.txt\n' > Makefile && git add Makefile && git commit -m "verify target" && git push
# 2. the README Quickstart, literally (Claude Code session in this directory)
#    /plugin marketplace add <SIGMA_REPO>   /plugin install sigma@sigma   (restart)
#    /agrim-init --demo      -> answer: github mode, board no, ledger no; confirm `make test`
# 3. one goal
gh issue create --label sdlc:goal --assignee @me --title "Add hello.txt" --body "Create hello.txt with one line."
# 4. /agrim-loop -> it opens a PR, runs the review gate, records `review` and leaves the PR for you
gh pr merge <pr> --squash         # you merge it
python3 <installed-sigma>/skills/agrim-loop/scripts/loop.py reconcile-merges .sdlc
# 5. check: issue closed, PR merged, hello.txt on main, .sdlc/state/verify/<n>.json passed
gh issue view 1 --json state && gh pr view <pr> --json state && git pull && cat hello.txt
# 6. record durations + phase_report cost lines under "Recorded runs" in this file, then TEAR DOWN BY HAND
gh repo delete "$R" --yes         # needs the delete_repo scope: gh auth refresh -s delete_repo
```

Deviations from the fake-gh run above are the finding: each one is filed as its own issue.

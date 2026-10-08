# The onboarding control

`tools/onboarding_control.py` answers one question with a run, not a reading: does the README
Quickstart, followed literally on a fresh repository, get one goal to `done`? It is the control for
epic #227 (plugin install -> `/sigma-init` -> one goal to done), built in #237.

It runs in CI on every push (`tests/test_onboarding_control.py`, Linux, Python 3.10, 3.11, 3.12 and 3.13), with
no secrets, no network and no model session. CI runs it WITHOUT `--install`: the host plugin CLIs
are not on CI runners, so the install lines are parsed and checked there, and run only by hand.

## Run it

```
python3 tools/onboarding_control.py                         # both modes x both variants, from this checkout
python3 tools/onboarding_control.py --mode local            # local-goals mode only (both variants)
python3 tools/onboarding_control.py --variant no-command    # only the no-command variant
python3 tools/onboarding_control.py --install all --from-install --json run.json
```

`--install claude|codex|all` runs the README's `claude plugin ...` / `codex plugin ...` lines into an
ISOLATED profile (HOME, `CLAUDE_CONFIG_DIR` and `CODEX_HOME` all inside the run's temp directory)
and records a hash of the real profile's plugin surface (`~/.claude/plugins`,
`~/.claude/settings.json`, `~/.codex/config.toml`, `~/.codex/plugins`) before and after. A host CLI
that is not installed is reported `skipped`, never green. `--from-install` runs every script from
the installed Claude Code copy instead of the checkout. `--sigma DIR` runs another Sigma tree (a
scratch copy for a control); `--readme FILE` follows another README (a drift control).

Exit 0: every mode and variant reached `done` and every assertion held. Exit 1: red; the JSON names
the first failing step, and one line per run says `GREEN` or `RED at <step>` (`local`, `github`,
`local/no-command`, `github/no-command`). Exit 2: a precondition is missing (`git` or `make` not on
PATH, Windows, or the README cannot be read -- a message, not a traceback) or a bad argument.

## What it follows, step by step

The commands come from README.md, parsed at run time, so a README that drifts from the shipped
scripts turns the control red:

| From the README | What the control does with it |
|---|---|
| `claude plugin marketplace add https://github.com/Agrim-Intelligence/sigmaloop` / `claude plugin install sigmaloop@sigmaloop` | exactly these two lines, the id matching `.claude-plugin/marketplace.json`; `--install` runs them into the isolated profile with that URL replaced by the checkout path |
| `codex plugin marketplace add https://github.com/Agrim-Intelligence/sigma` / `codex plugin add sigmaloop@sigmaloop` | checked the same way; `--install`: run into an isolated `CODEX_HOME` |
| `/plugin marketplace add https://github.com/Agrim-Intelligence/sigmaloop` / `/plugin install sigmaloop@sigmaloop` (in-session) | checked the same way (a model turn; not run) |
| every init flag the Quickstart shows (`/sigma-init ...` and `init_flow.py ...` lines, and the inline-code flags in its `/sigma-init` subsections, e.g. the `[ask]` list `--mode`, `--verify`, `--board`, `--ledger`, `--local-only`) | each must be in init_flow.py's own parser (`_VALUE`/`_BOOL`, read by `ast`) |
| `/sigma-init --demo` (the `### Claude Code` block) | its flags |
| `python3 <installed-sigma>/skills/sigma-init/scripts/init_flow.py . ...` | the script `/sigma-init` runs |
| every `python3 <installed-sigma>/...` gesture under "What `/sigma-init` will ask you" and "If `/sigma-init` says you lack access" (fenced lines and the access table's inline code; #277) | github mode, confirm variant, after the goal is done: each run from the repository root, `<file>` = a file holding `make test`, `<remote>` = `origin`; `preflight.py check` may exit 1 (its report), every other gesture must exit 0 -- and the exit code is not trusted alone: `GESTURE_EFFECTS` asserts each one's effect (`set` wrote `make test` with enforce ON, `decline` left enforce OFF, `check` printed exactly one preflight report header consistent with its exit code, `use-remote` wrote `work.remote`, `local-only` wrote `work.enabled: false`) |
| every OTHER `python3 <script>.py ...` gesture anywhere in the README (fenced, inline, a table cell, inside `$(...)`; the path spelled any way -- `<installed-sigma>/`, `"${VAR}/"`, `~/dir/`, absolute, repository-relative, a bare name; #277 review) | mode `readme-usage`, every run: a path that is not `<installed-sigma>/<shipped script>` is red (it exits 2 when copied from the user's repository); the rest are not executed (most need a live board or loop) but checked against the script's own usage -- `<script> --help` must exit 0 with a usage, the gesture's verb must be one it lists, its positional count must fit that alternative (`<x>` required, `[x]` optional, `...`/`(...)`/`[options]` open), and every `--flag` must appear in the usage. Limit: a gesture that parses but does something other than its prose claims is not caught; that stays a prose review |
| `/sigma-loop` (the `### Claude Code` block) | must be present; the loop is then driven by the scripts the sigma-loop skill names |
| `python3 <installed-sigma>/skills/sigma-init/scripts/verify_detect.py confirm .sdlc <n> <id>` | run with `<n>` and `<id>` copied from init's printed candidate (confirm variant) |

A README or printed command runs only in one pinned shape: first token `python3` (or the `python` /
`py` that init itself prints), run as the control's own interpreter; second token a `.py` script
that exists under the Sigma directory's `skills/` or `tools/`, read from the repository root as a
shell reads it (`<installed-sigma>` is the Sigma directory; any other relative path resolves against
the repository, so a path relative to the plugin directory is refused, #277); no argument carrying shell syntax
(`` ` `` `$` `;` `&` `|` `<` `>` parentheses, braces, globs, control characters). Anything else is
refused before it runs. Nothing goes through a shell.

Everything else comes from what `/sigma-init` prints. Every `[ask]` line ends in one
machine-readable shape, `[ask] <id>: <prose> -> <answer> ; <answer>`, each answer `--flag`,
`--flag VALUE|VALUE` or `--flag PLACEHOLDER` (`init_flow.ask_line`). The control parses it and
answers from a small policy keyed by question id and flag NAME: mode `--mode local-goals|github`,
work `--local-only`, board `--board no`, ledger `--ledger no`, verify by the README's confirm
gesture (never an init flag). A question with no policy, a line that no longer offers the policy's
flag (renamed), a value outside the offered set, or a line without the machine part is **red,
"unanswerable [ask]"** -- a real user relaying such a line would get exit 2. Init is re-run until
nothing is open (github mode's board and ledger questions only appear once the mode is answered).
Two answers the control supplies itself, because no `[ask]` names them: `--repo
acme/onboarding-demo` in github mode (the origin is a local path, not a GitHub owner/name, so
github mode needs the repository given). Both github variants run with work ON -- the real path
(#312). The `Next:` line's `loop.py next` command is the one the loop runs.

**Two variants per mode.** `confirm`: the repository has a Makefile test target and the README
gesture confirms it (enforce ON). `no-command`: the repository has only a README.txt, so nothing is
detected; the verify question is left open, as a user with no test command leaves it, and the
default init SCAFFOLDS is exactly what `record done` sees. The confirm variant can never see a bad
default -- the confirm overwrites it -- which is why the no-command variant exists (review of PR
#306). Asserted for the control's goal there: init left `verify.enforce` OFF with no command;
`loop.py verify` said NO-COMMAND (exit 3); `record done` exited 0; the goal is done (local: status
`done`, no gh call). The github no-command variant asserts its own set (below).

**Local-goals mode, confirm variant.** A fresh `git init` repository with no remote and a `Makefile` whose `test`
target checks `hello.txt` (so the confirmed verify command fails before the work and passes after
it). A `gh` stub on PATH logs every call. Init answers: `--mode local-goals`, `--local-only`. Then:
confirm `make test` -> file `.sdlc/goals/0002-onboarding-hello.md` (no frontmatter
`verify_command`, so config's command is what proves it) -> `loop.py start` -> `loop.py next`
until `DONE`, and for each goal: `agent-start`, `phase_report.py start/end implement`, the work,
`loop.py verify`, `loop.py record done`. The demo goal `--demo` queued runs first.

Asserted per goal: `record done` exit 0; frontmatter `status: done`; verify evidence
(`.sdlc/state/verify/<goal>.json`) `verify_state: pass`, `exit: 0`, a non-empty command; the loop
made no `gh` call. And the control's own goal is among those done.

**github mode, confirm variant.** A local bare `origin`, a clone with the same Makefile, and the stateful fake `gh`
from `tests/test_public_bootstrap_control.py` (extracted by `ast`, so the control and that test
share one fake). Init answers `--mode github --repo acme/onboarding-demo`, then `--board no
--ledger no`. Nothing is committed after init: the goal's worktree needs nothing from it (an
earlier draft committed and pushed `.sdlc/` and labelled the issue `priority:P2`; the PR #306 review
showed removing either stays green, so both are gone). Then: `gh issue create` labelled
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

**github mode, no-command variant.** The same origin and fake, a repository with nothing to
confirm, init answered exactly as the confirm variant -- work ON, no `--local-only` (#312). Then
the confirm variant's whole flow: `gh issue create` -> `loop.py start/next` -> `work.py start` ->
the work -> `loop.py verify` (NO-COMMAND, exit 3, no evidence) -> `work.py commit`, `pr` ->
`sigma:block` -> `work.py merge` (must park on the review gate, naming `sigma:block`) ->
`sigma:approve` -> `work.py merge` (must pass the review gate) -> `record done` (refused, exit 4)
-> `record review` -> the human's `gh pr merge` -> `loop.py reconcile-merges`.

Asserted: init left `verify.enforce` OFF with no command; `loop.py verify` exit 3; no verify
evidence was written; and every github-mode assertion above except the evidence one. Before #312
this variant passed `--local-only`, because `work.py merge` demanded verify evidence even with
enforce off and no command -- evidence `loop.py verify` cannot write -- so every merge parked on
"no fresh verify evidence". That was the bug: `state.verify_required` is now the one rule (enforce
on, or a command declared) and the merge demands evidence only then. The control: that guard
reverted in a scratch copy turns `github/no-command` RED through the documented gesture
(`--mode github --sigma <scratch>`), its approved merge parked on "no fresh verify evidence", while
`github` stays GREEN.

## Recorded runs

2026-09-29, macOS (Darwin 25.6), Python 3.12.13, from the checkout, the documented gesture
`python3 tools/onboarding_control.py`: exit 0, whole run 18.6s. `local` GREEN 2.46s, `github`
GREEN 8.22s, `local/no-command` GREEN 1.98s (`loop verify` exit 3 NO-COMMAND, `record done` exit 0),
`github/no-command` GREEN 5.94s (`loop verify` exit 3, `record done` exit 0, issue closed).

2026-09-29, macOS (Darwin 25.6), Python 3.12.13, after #312 (github no-command on the real work-ON
path), `/opt/homebrew/bin/python3.12 tools/onboarding_control.py`: exit 0, whole run 29.8s wall.
`local` GREEN 4.05s, `github` GREEN 11.64s, `local/no-command` GREEN 4.72s, `github/no-command`
GREEN 9.22s (`loop verify` exit 3, no evidence, approved merge "review gate passed", `record done`
refused while open, reconcile `1 done (PR #100 merged)`, issue closed, 86 gh calls -- the same
count as the confirm variant). The #312 guard reverted: `github/no-command` RED at its review-gate
assertion (the merge parked on evidence first), `github` GREEN, exit 1.

2026-09-29, macOS (Darwin 25.6), Python 3.12.13, after #277 (the README's init gestures executed),
`/opt/homebrew/bin/python3.12 tools/onboarding_control.py`: exit 0, 14.8s wall. `local` GREEN 2.27s,
`github` GREEN 5.51s (its five README init gestures 0.03-0.13s each, all exit 0), `local/no-command`
GREEN 2.18s, `github/no-command` GREEN 4.72s. The control: the pre-#277 README (`git show
HEAD:README.md`, plugin-relative paths) through `--mode both --variant confirm --readme <that copy>`:
exit 1, `local` and `github` both RED at `verify confirm (README gesture)`, "not a script under
<sigma>/skills or /tools, read from the repository root".

The run below predates the variants and the `[ask]` parsing; its install and per-step numbers are
still the only measured `--install` run.

2026-09-29, macOS (Darwin 25.6), Python 3.12.13, from the INSTALLED Claude Code copy
(`--install all --from-install`; the marketplace install takes the checkout's committed HEAD, not
its working tree). Whole run 15.4s. Durations are wall-clock per step, measured by the control.

Host install (isolated profile). These rows were recorded on 2026-09-29, before the plugin was renamed (#524),
under the previous plugin id and repository: they are history, not a measurement of `sigmaloop@sigmaloop`.

| Step | Seconds | Result |
|---|---|---|
| `claude plugin marketplace add <the repository as it was then>` | 0.816 | ok |
| `claude plugin install <plugin id as it was then>` | 0.793 | ok, 1.0.0 |
| `codex plugin marketplace add <the repository as it was then>` | 0.058 | ok |
| `codex plugin add <plugin id as it was then>` | 0.097 | ok, 1.0.0 |
| real profile plugin-surface hash before / after | | `b4844e1dc784e0ab` / `b4844e1dc784e0ab`: untouched |

**Re-run for the new plugin id (2026-10-05, #524), Claude Code only.** `python3 tools/onboarding_control.py --mode local
--install claude --from-install` on macOS with Claude Code 2.1.284, from the committed renamed tree, whole run 21.0s:
`claude plugin marketplace add` of the checkout (the README's URL swapped for the checkout path) 0.685s, ok, adding the
marketplace `sigmaloop`; `claude plugin install sigmaloop@sigmaloop` 0.727s, ok, 1.0.0; local-goals GREEN in 8.9s; real
profile untouched. `claude plugin details` of that install lists 42 skills and 5 hooks. This is one run, on one machine. The
Codex rows above were NOT re-run (no Codex CLI on the machine that did this change).

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

CI shape: `tests/test_onboarding_control.py`, 40 tests (the green runs come from ONE subprocess
run of the documented gesture), 55.5s on Python 3.12 and 47.0s on 3.10 (this Mac, from the
checkout); the PR #306 repro through the CLI is ~20s of that.

## The controls, seen red

Each was run through the CLI gesture above, not only through pytest, and each is also a test.

| Control | Gesture | Result |
|---|---|---|
| The empty-verify-command default reintroduced: in a scratch copy, `verify_detect.write_verify` writes `enforce: true, command: ""` again, its guard removed | `--mode local --sigma <scratch>` | exit 1, **RED at `record done`** (5.8s): the demo goal (its own `verify_command`) is done; the control's goal gets `loop.py verify` NO-COMMAND exit 3, then `record done` REFUSED exit 4 |
| The ORIGINAL bug, as the PR #306 review reproduced it: `config.json.tmpl` back to `"enforce": true` with an empty command, and both scaffold rewrites removed (`init_flow.py`'s and `sdlc_init.py`'s `write_verify(sdlc, None, ...)`), `write_verify` itself intact | `--mode both --sigma <scratch>` | exit 1 (27.5s): `local` and `github` GREEN (the confirm overwrites the default -- before the no-command variant this was the whole run, and it was wrongly green); `local/no-command` **RED at `record done`**: `loop verify` exit 3, `record done` REFUSED exit 4. Since #312 `github/no-command` runs work ON and goes **RED at its enforce-off assertion**; its merge parks on "no fresh verify evidence" and names the `verify_detect.py` fix (enforce ON still demands evidence) |
| #312: the merge gate's `state.verify_required` guard reverted in a scratch copy (every merge demands evidence again) | `--mode github --sigma <scratch>` | exit 1: `github` GREEN; `github/no-command` **RED at its review-gate assertion**: the `sigma:block` merge and the approved merge both parked on "no fresh verify evidence" |
| Every `[ask]` flag renamed in `init_flow.py` (`--board`, `--verify`, `--local-only`, `--mode`, `--ledger` to names nothing accepts) | pytest (`run_local` on a scratch copy) | RED at `init`: "unanswerable [ask] mode: it offers ['--backlog'] ..." |
| An `[ask]` with an unknown id, without the ` -> ` part, a renamed flag, or a value outside its set | pytest (parser) | RED, "unanswerable [ask]" |
| An init flag the README shows renamed (`--local-only` -> `--offline`), or dropped from init_flow.py's parser | pytest (parse) | RED at `readme` |
| A drifted install line (claude, codex or in-session `/plugin`; the id not the manifest's) | pytest (parse) | RED at `readme` |
| A README gesture that is not the pinned shape (`sh -c 'touch <marker>' ...`, `bash`/`node` first, `$(...)`, `;`, a backtick, `|`, a script that does not exist or lies outside `skills/`/`tools/`) | pytest | refused before running; the marker file is never created |
| `--readme` pointing at a missing file | CLI | exit 2, `precondition missing: cannot read the README`, no traceback |
| README drift: `verify_detect.py confirm` renamed `accept` | `--mode local --readme <copy>` | exit 1, RED at `verify confirm (README gesture)` |
| README drift: `init_flow.py` renamed `init.py` | `--mode local --readme <copy>` | exit 1, RED at `readme` (the README names a script that does not ship) |
| README drift: `/sigma-loop`, `/sigma-init` or `claude plugin install` renamed | pytest (parse) | RED at `readme` |
| #277: an init gesture written relative to the plugin directory (the pre-#277 README's `python3 skills/sigma-init/scripts/preflight.py use-remote ...`), a renamed verb (`decline` -> `refuse`), or a placeholder nothing fills | `--mode github --variant confirm --readme <copy>` | exit 1, RED at `README gesture: ...`; the same drift in the confirm gesture is RED at `verify confirm (README gesture)`, "read from the repository root" |
| #277 review: the pre-fix README line `python3 <installed-sigma>/skills/sigma-loop/scripts/auto_unpark.py   # see KEEP_PARKED_MARKER ...` (exit 2, usage) | `--mode local --variant confirm --readme <copy>` | exit 1, RED at `README usage`: "positionals [] fit no usage alternative"; also red in pytest: a missing `<ref>` on `dismiss-text`, an unknown verb, an unknown `--flag`, an extra positional, a script that does not ship |
| #277 review: each `GESTURE_EFFECTS` predicate fed a wrong effect (enforce left ON after `decline`, a usage line or an rc/report mismatch from `preflight.py check`) | pytest | each one false |
| Every assertion in `check_local` (5), `check_github` (11), `check_no_command` (5) and `check_github_no_command` (13) broken once, alone, against a real green run's observations | pytest | each one false, all others true |

Each guard the controls above rely on was itself broken once (the variant removed, the offered-flag
check skipped, an unknown question tolerated, the shell-syntax / interpreter / script pins removed,
the README-flag and install checks skipped, the README read unguarded, a no-command assertion
forced true) and the matching test was seen red, on Python 3.10 and 3.12.

The `/sigma-init` drift case caught the control's own first draft: it took the first `/sigma-init`
line anywhere in the Quickstart, so with the first-run line renamed it silently used the "Adopting
into an existing repo" line instead. It now reads the first-run pair from the `### Claude Code`
block only.

## What this does NOT cover

Each gap is an issue, not a silent skip:

| Not covered | Why | Issue |
|---|---|---|
| A live-model `/sigma-loop` turn in Claude Code | spends tokens; the control drives the scripts the skill names instead | #300 |
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
#    /plugin marketplace add https://github.com/Agrim-Intelligence/sigmaloop   /plugin install sigmaloop@sigmaloop   (restart)
#    /sigma-init --demo      -> answer: github mode, board no, ledger no; confirm `make test`
# 3. one goal
gh issue create --label sdlc:goal --assignee @me --title "Add hello.txt" --body "Create hello.txt with one line."
# 4. /sigma-loop -> it opens a PR, runs the review gate, records `review` and leaves the PR for you
gh pr merge <pr> --squash         # you merge it
python3 <installed-sigma>/skills/sigma-loop/scripts/loop.py reconcile-merges .sdlc
# 5. check: issue closed, PR merged, hello.txt on main, .sdlc/state/verify/<n>.json passed
gh issue view 1 --json state && gh pr view <pr> --json state && git pull && cat hello.txt
# 6. record durations + phase_report cost lines under "Recorded runs" in this file, then TEAR DOWN BY HAND
gh repo delete "$R" --yes         # needs the delete_repo scope: gh auth refresh -s delete_repo
```

Deviations from the fake-gh run above are the finding: each one is filed as its own issue.

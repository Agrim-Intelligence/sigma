# Platform support and quality

What is validated on each host, and how quality is guarded. Back to the [README](../README.md).

## Status (honest)

Sigma's **full SDLC run remains validated on Claude Code**. The Codex adapter has been exercised
in a real Codex session through its first loop command, and its plugin inventory and rollout usage
were checked against the real Codex CLI/session store. A complete Codex goal through PR merge has
not yet been measured. On Cursor, set-up and one goal to a merged pull request were measured, but not
the loop; on Antigravity, the plugin install and set-up were measured, but not a goal run; see
[Other platforms supported](#other-platforms-supported).

The **git half** of per-goal worktrees is verified end-to-end against a real repo (worktree cut from
`<remote>/<base>`, `verify_command` running in the goal's tree, the main checkout left untouched). The
**`gh` half** — opening the PR and the merge gate — is verified end-to-end against real, live, merged
PRs with independent post-PR review + auto-merge (confirmed via 9+ merged PRs showing an
independent-review approving comment from the same account that merged, as of 2026-08-06; a REST re-count
on 2026-10-13 of the source repository found 97 such PRs merged by that date, all merged by the account that
posted the approving comment; those comments carry the same approve marker under its pre-rename prefix
(78 of the 97) or a bare APPROVE heading (19), not the current `sigma:approve` spelling). The
gate fires reliably in production use on this very repo.

## Other platforms supported

The full pipeline is validated on **Claude Code**. Codex now has a project adapter and measured
component checks; a complete Codex goal through PR merge is still unverified. Cursor: set-up and one
goal to a merged pull request were measured on 18 Nov 2026; the loop on several goals was not.
Antigravity: the plugin install and set-up were measured on 18 Nov 2026; a goal run was not and has
never been run there.

### Cursor

> **Measured in the Cursor app on 18 Nov 2026, on a private scratch repository.**
> `/sigma-init --cursor` with the GitHub-mode flags ran and set the repository up with a Projects
> board, and `/sigma-doctor` reported all checks ready. `/sigma-goal 1 --land` first refused because
> `work.auto_merge` was `off`; with it set to `always`, it ran a real issue to a merged pull request
> with passing tests. The pull request was merged by hand: on a free-plan private repository GitHub
> refuses the branch-protection read with HTTP 403, and Sigma then holds a merge (see the opt-in
> `work.protection_unreadable` in [configuration](configuration.md)).
>
> **Not measured:** `/sigma-loop` on several goals, the Cursor command-line agent, hooks, and an
> auto-merge on a repository whose protection can be read.

Sigma ships no Cursor plugin manifest (whether Cursor's plugin support could load one is unverified),
and Sigma uses no Cursor hook and no `code-review` plugin there. Cursor documents a hooks system
(`.cursor/hooks.json`, <https://cursor.com/docs/hooks>, read 17 Nov 2026); Sigma does not use it, and
whether it fires in Cursor's command-line agent is unmeasured (forum reports say some events do not).
No Sigma behavior depends on a hook. From the root of your
repository, with `<installed-sigma>` your Sigma checkout:

```
python3 <installed-sigma>/skills/sigma-init/scripts/init_flow.py . --cursor --demo
```

That writes **`.cursor/rules/sdlc.mdc`** — an *always-applied* Cursor rule carrying the full
7-phase discipline (Cursor's analog of the Claude hook) — and **`.cursor/rules/output-contract.mdc`**,
which points status output at `render.py` instead of at prose to imitate, scaffolds the `.sdlc/` layer,
and pins `companions: off` so each phase would run via the **portable `sigma-*` executors** instead of the
Claude-only companions. The loop, model-selection, status and KG **helpers are plain zero-dep
`python3`** — run them from Cursor's terminal (e.g.
`python3 <installed-sigma>/skills/sigma-loop/scripts/loop.py next .sdlc`). The goal is the same spine,
executors, and audit trail without Claude.

### Codex (partial live validation)

From an installed Sigma skill in Codex, run `/sigma-init` or its scaffolder with `--codex`:

```sh
python3 <installed-sigma>/skills/sigma-init/scripts/sdlc_init.py . --codex
```

This writes or refreshes a managed `AGENTS.md` block while preserving rules outside it and leaving
Claude's project configuration alone. Each skill explains how Codex resolves the installed skill
directory in commands that use `CLAUDE_SKILL_DIR`. `/sigma-doctor` checks Codex's installed, enabled
Sigma version; the review
resolver recognizes a Codex session and offers `codex exec --sandbox read-only --ephemeral` as a fresh
review process. Codex never receives the shared `haiku`/`sonnet`/`opus`/`fable` ledger tiers as model IDs:
`predict.py host-model codex` resolves them, or the explicit `off` fallback, through the plugin's versioned map and `model_host_overrides.codex`. Slice sessions use the existing goal worktree and the
main checkout's manifest; without both paths they refuse to print a runnable command. Codex slice
planning requires `work.enabled` and a goal worktree; without them, run the goal as one unit. Slices
sharing a checkout run sequentially.

The first `loop.py start` gesture succeeded in a disposable real Codex CLI session. Codex phase
usage data was checked against real rollout records; older rollout IDs without a bounded date lookup
report usage as unavailable. **A complete Codex goal through PR merge has not
yet been measured**. The separate Codex supervisor is available as an opt-in
path after the bounded smoke in [the migration guide](codex-autonomy.md);
the Claude-specific unattended watcher still has no Codex budgeted backend.


### Antigravity

> **Measured in `agy` 1.5 on 18 Nov 2026, on a private scratch repository.**
> `agy plugin install https://github.com/Agrim-Intelligence/sigma` installs from the public repository
> and converts the Claude Code plugin: 46 skills and 4 hook groups (PostToolUse, PreToolUse,
> SessionStart, Stop). The `UserPromptSubmit` hook and the `PreToolUse` hook on skills are skipped,
> because Antigravity has no such event or tool. The converted skills still contain the Claude-only
> `${CLAUDE_SKILL_DIR}` placeholder, and each skill carries a note telling the model to use its own
> folder. `/sigma-init --antigravity` with the GitHub-mode flags ran inside `agy` and produced a
> GitHub-mode setup with a Projects board and an `AGENTS.md`.
>
> **Not measured:** a goal run (`/sigma-goal`), the loop, whether the converted hooks fire (their
> paths are relative), the headless `agy -p` command, and an independent reviewer. A goal run has
> never been run in Antigravity.

Install with the plugin command above, then run `/sigma-init --antigravity` from the root of your
repository. If the slash command is not available, run the init flow directly, with
`<installed-sigma>` the installed plugin directory:

```
python3 <installed-sigma>/skills/sigma-init/scripts/init_flow.py . --antigravity
```

`--antigravity` is opt-in. It writes the same managed block in **`AGENTS.md`** that `--codex` writes
(skip-if-exists, refreshed when the plugin text changes, every other byte kept) and prints where
Antigravity looks. The flag itself writes only `AGENTS.md`; it changes no config value (`/sigma-init`'s
ordinary `.sdlc/` scaffolding still runs). The block's wording is Codex's, unadapted for
Antigravity. It creates no `.agents/` directory and does not link or copy any skill.

What Antigravity's official docs say (read 17 Nov 2026): rules come from `AGENTS.md`, `GEMINI.md` or
`.agents/rules/*.md` (<https://antigravity.google/docs/rules>), and skills are `SKILL.md` folders under
`.agents/skills/` (<https://antigravity.google/docs/skills>). That is documentation read, not behavior
observed.

Also unmeasured: MCP, workflows, subagents and so an independent reviewer, and a goal run end to
end. Tier 3 (a goal end to end) and tier 4 (the unattended loop with an
independent reviewer) are not attempted. No Sigma hook is load-bearing here, as on every host.

## Quality & drift (`evals/`)

The kit's "output" is agent *behavior*, so quality is guarded in two tiers, re-run on every change to
catch drift (see [`evals/README.md`](../evals/README.md)):

- **Tier 1 — deterministic behavioral gate (free, in CI):** `python3 <installed-sigma>/evals/run.py` runs the intent hook
  over a behavioral corpus (`evals/fixtures.json`) — a deterministic proxy for *"the agent got the right
  discipline signal"* — scores it, and **fails the build if the score drops below `evals/baseline.json`.**
  That drop is the drift signal.
- **Tier 2 — LLM-judge behavioral evals (opt-in, parked):** run the agent on each fixture goal and have
  an LLM judge score the transcript against its rubric. The runner + injectable `agent`/`judge` seam are
  built and tested; the real LLM wiring is withheld until the API budget is greenlit, so `--live` prints
  a parked notice instead of spending.

## Requirements

- **Runtime:** bash + python3 (stdlib) — zero dependencies. Two optional features additionally need
  the [`gh`](https://cli.github.com) CLI, authenticated (`gh auth login`): the **GitHub backlog
  source**, and the **PR + merge gate** half of per-goal worktrees (`work.enabled` on its own needs
  only `git`; `work.py pr` / `merge` are what need `gh`). The default local source stays zero-dep.
- **Knowledge graph (optional):** the graph builder — default `graphify` (`pip install graphifyy`);
  off unless `knowledge_graph.enabled` is set.
- **Companion (optional):** `code-review` — an extra Review pass when installed. Never required:
  the `sigma-*` executors run every phase.
- **Dev/test:** `pip install pytest`, then `pytest tests/ -v`. **CI** (GitHub Actions) runs the full
  suite — including the **leakage gate** (`tools/leak_scan.py`: home paths, secret shapes,
  private-key bodies and links into the owner's other repositories; its docstring lists what it
  cannot see), the **hook behavioral-spec**, and the **Tier-1 quality
  gate** (`evals/run.py`) — weekly and on demand, on Linux with Python 3.10, 3.11, 3.12 and 3.13 and on macOS with Python 3.12; nightly, one macOS Python 3.12 leg runs the suite minus the heaviest test files (`nightly`); every push and pull request runs one macOS Python 3.12 leg (the `test` job, the short smoke set in `.github/smoke-tests.txt`). The `test` job also runs the safety check (`evals/safety/check.py`: secret shapes, sinks, hostile fixtures). CI neither measures nor
  enforces code coverage. A one-off local run of the suite under coverage.py (Python 3.12, macOS) gave last measured
  line coverage of **90.9%** over `skills/` and `hooks/`; it understates, because scripts tests copy elsewhere and
  children started with a scrubbed environment are not traced. A Linux trial of the same run took 2.1 times as long
  as the plain leg and tripped a timing test, so it is not a gate yet. Commands, the trial and what is not
  measured: [docs/launch/coverage.md](launch/coverage.md).

## Install verification

The marketplace this repository adds is named `sigmaloop`, so the plugin id is `sigmaloop@sigmaloop` on
Claude Code and on Codex. The Claude Code install was measured in an isolated profile for this name; see
[onboarding-control.md](onboarding-control.md). Codex reads the same `.claude-plugin/marketplace.json`:
Sigma Loop ships no other manifest. On 2026-10-08 the marketplace URL and plugin id were tested in an
isolated `CODEX_HOME` against the 1.0.3 feature branch; the enabled inventory and installed skill
resolved to the same clean commit. A full Codex goal-to-merge run has not yet been measured. Sigma ships
no Cursor plugin manifest; whether Cursor's own plugin support could install Sigma is unverified.

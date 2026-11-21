# Platform support and quality

How each host is installed and set up, and how quality is guarded. Back to the [README](../README.md).

## Status

Sigma works on all four hosts: Claude Code, Codex, Cursor and Antigravity. The sections below give the
install, the init flag and what each host's adapter writes.

| Host | Install | Init flag |
|---|---|---|
| Claude Code | `claude plugin marketplace add`, then `claude plugin install sigmaloop@sigmaloop` | none |
| Codex | `codex plugin marketplace add`, then `codex plugin add sigmaloop@sigmaloop` | `--codex` |
| Cursor | clone this repository | `--cursor` |
| Antigravity | `agy plugin install https://github.com/Agrim-Intelligence/sigma` | `--antigravity` |

The **git half** of per-goal worktrees is verified end-to-end against a real repo (worktree cut from
`<remote>/<base>`, `verify_command` running in the goal's tree, the main checkout left untouched). The
**`gh` half** — opening the PR and the merge gate — runs against real, live, merged PRs with an
independent post-PR review and auto-merge. The gate fires reliably in production use on this very repo.

## Other platforms supported

Sigma works on Claude Code, Codex, Cursor and Antigravity. This section and those below describe how
each is set up.

### Claude Code

Install from the plugin marketplace, then run `/sigma-init` from the root of your repository. Claude
Code is the host with hooks: the session-start, prompt and tool hooks are accelerators on top of the
Python gates that every host shares.

### Cursor

Sigma works with Cursor. It ships a Cursor plugin package (`.cursor-plugin/plugin.json` and
`marketplace.json`) that carries its skills and no hooks. Sigma uses no Cursor hook and no `code-review`
plugin there, and no behaviour depends on a hook. From the root of your repository, with
`<installed-sigma>` your Sigma checkout:

```
python3 <installed-sigma>/skills/sigma-init/scripts/init_flow.py . --cursor --demo
```

That writes **`.cursor/rules/sdlc.mdc`** — an *always-applied* Cursor rule carrying the full
7-phase discipline (Cursor's analog of the Claude hook) — and **`.cursor/rules/output-contract.mdc`**,
which points status output at `render.py` instead of at prose to imitate, scaffolds the `.sdlc/` layer,
and pins `companions: off` so each phase runs via the **portable `sigma-*` executors** instead of the
Claude-only companions. The loop, model-selection, status and KG **helpers are plain zero-dep
`python3`** — run them from Cursor's terminal (e.g.
`python3 <installed-sigma>/skills/sigma-loop/scripts/loop.py next .sdlc`). The goal is the same spine,
executors, and audit trail without Claude.

On a free-plan private repository GitHub refuses the branch-protection read with HTTP 403, and Sigma
then holds a merge; merge by hand, or set the opt-in `work.protection_unreadable` (see
[configuration](configuration.md)).

### Codex

Sigma works with Codex. From an installed Sigma skill in Codex, run `/sigma-init` or its scaffolder with
`--codex`:

```sh
python3 <installed-sigma>/skills/sigma-init/scripts/sdlc_init.py . --codex
```

This writes or refreshes a managed `AGENTS.md` block while preserving rules outside it and leaving
Claude's project configuration alone. Each skill explains how Codex resolves the installed skill
directory in commands that use `CLAUDE_SKILL_DIR`. `/sigma-doctor` checks Codex's installed, enabled
Sigma version; the review resolver recognizes a Codex session and offers
`codex exec --sandbox read-only --ephemeral` as a fresh review process. Codex never receives the shared
`haiku`/`sonnet`/`opus`/`fable` ledger tiers as model IDs: `predict.py host-model codex` resolves them, or the explicit `off` fallback,
through the plugin's versioned map and `model_host_overrides.codex`.
Slice sessions use the existing goal worktree and the main checkout's manifest; without both paths they
refuse to print a runnable command. Codex slice planning requires `work.enabled` and a goal worktree;
without them, run the goal as one unit. Slices sharing a checkout run sequentially. A separate Codex
supervisor is available as an opt-in path ([the migration guide](codex-autonomy.md)).

### Antigravity

Sigma works with Antigravity. Install with the plugin command, then run `/sigma-init --antigravity`
from the root of your repository:

```
agy plugin install https://github.com/Agrim-Intelligence/sigma
```

`agy` installs from the public repository and converts the Claude Code plugin. If the slash command is
not available, run the init flow directly, with `<installed-sigma>` the installed plugin directory:

```
python3 <installed-sigma>/skills/sigma-init/scripts/init_flow.py . --antigravity
```

`--antigravity` is opt-in. It writes the same managed block in **`AGENTS.md`** that `--codex` writes
(skip-if-exists, refreshed when the plugin text changes, every other byte kept) and prints where
Antigravity looks. The flag itself writes only `AGENTS.md`; it changes no config value (`/sigma-init`'s
ordinary `.sdlc/` scaffolding still runs). It creates no `.agents/` directory and does not link or copy
any skill. The install converts the plugin's hooks.

Antigravity reads rules from `AGENTS.md`, `GEMINI.md` or `.agents/rules/*.md`
(<https://antigravity.google/docs/rules>), and skills are `SKILL.md` folders under `.agents/skills/`
(<https://antigravity.google/docs/skills>). Converted skills carry a note telling the model to use its
own folder in place of the Claude-only `${CLAUDE_SKILL_DIR}` placeholder. No Sigma hook is load-bearing
here, as on every host.

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
- **CI:** every push and pull request runs the smoke set and the safety check; the full suite runs in 6 shards per
  leg on Linux (Python 3.10 to 3.13) and macOS (Python 3.12), on demand and weekly (Sundays). CI neither measures nor
  enforces code coverage. A local run of the suite under coverage.py (Python 3.12, macOS) gave last measured
  line coverage of **90.9%** over `skills/` and `hooks/`; see [docs/launch/coverage.md](launch/coverage.md).

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
  enforces code coverage. 

## Install verification

The marketplace this repository adds is named `sigmaloop`, so the plugin id is `sigmaloop@sigmaloop` on
Claude Code and on Codex. Codex reads the same `.claude-plugin/marketplace.json`: Sigma Loop ships no
other manifest. Sigma ships a Cursor plugin package (`.cursor-plugin/`) under the same `sigmaloop` id.

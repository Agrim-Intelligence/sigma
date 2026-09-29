---
name: agrim-setup
description: Alias of /agrim-init, the one setup flow for a repo (access, mode, verify, labels, board offer). Use for repository adoption or /agrim-setup.
allowed-tools: Bash(python3 *), Bash(bash *), Bash(git *), Bash(gh *), Read, Edit
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-setup

Detailed selection triggers: [selection](references/selection.md).

**`/agrim-setup` is an alias of `/agrim-init`** (#236): the same flow, the same questions, the same
files. There is one entry point; this skill only forwards to it, so the two can never disagree.

```bash
SETUP="${CLAUDE_SKILL_DIR}/scripts/setup.py"
```

1. Run the flow from the repository root, passing through any flags the user gave:

   `python3 "$SETUP" init .`

   `setup.py init ...` hands its arguments to `agrim-init/scripts/init_flow.py` unchanged. Everything
   that follows -- the `[ask]` lines and which flag answers each (mode, work, verify, board, ledger),
   what `--yes` may and may not default, the exit codes and the `Resume:` line, and what to do on
   Claude Code versus Codex / Cursor -- is exactly [`/agrim-init`'s steps](../agrim-init/SKILL.md).
   Follow them there; do not re-derive them here.

2. What adoption used to add on top, and where it lives now:
   - **github discovery scoped to `@me`, the labels:** the flow's github mode (`--mode github`) sets
     both; `assignee` is written only where it is unset.
   - **the ledger:** `--ledger yes`; the flow then prints the `sync.py bootstrap` line, which pushes
     an ops branch, so run it when the user is ready.
   - **a PR per goal:** `work.enabled` ships on; `--local-only` turns it off on purpose (and the
     loop stops warning about it).
   - **the ignore scope:** `--ignore-scope local` writes the runtime-dir rules to `.git/info/exclude`
     instead of `.gitignore` -- use it when the user wants tracked files untouched, or when
     `.git/info/exclude` already has a blanket `.sdlc/` line (never narrow it).
   - **a board:** `--board yes` (see `/agrim-init` 1c).

The building blocks stay available for a human or a script: `python3 "$SETUP" detect .`,
`ignore . --scope tracked|local`, `ignore-status .`, `labels .sdlc`, and
`configure .sdlc --repo <owner/name> [--source local-goals]` -- which now refuses (exit 2, nothing
written) github mode with no repository, and a missing `.sdlc/` (run `/agrim-init` first).

**Public repository?** If you are adopting Sigma's own public repository, or you choose the
public-repository profile for any other public repo, apply it after the flow:
[references/public-repo.md](references/public-repo.md).

**One caveat to mention if the host repo has its own edit-gating hooks:** Sigma's Implement phase
edits go through the same tool calls a human would, so a host `PreToolUse` hook that gates source edits
(e.g. on a plan-freshness check) applies to them too. If the repo has one, make sure whatever it
expects (a plan doc, a sentinel) is satisfied, or a source-code goal can be denied mid-Implement.

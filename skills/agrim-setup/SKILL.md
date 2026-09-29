---
name: agrim-setup
description: Adopt Sigma into an existing repo with .sdlc, board defaults, ledger, and verification. Use for repository adoption or /agrim-setup.
allowed-tools: Bash(python3 *), Bash(bash *), Bash(git *), Bash(gh *), Read, Edit
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-setup

Detailed selection triggers: [selection](references/selection.md).

Adopt Sigma into a repo that already has code and history, in one pass, with defaults that don't
surprise you. This is the "master prompt": you run it, it inspects the repo, and it configures the
plugin the way a real team wants it — instead of ten manual edits to `config.json`.

```bash
SETUP="${CLAUDE_SKILL_DIR}/scripts/setup.py"
LOOP="${CLAUDE_SKILL_DIR}/../agrim-loop/scripts"
```

Work through these in order. Report what you find and what you set at each step; only ask the user
when a choice genuinely can't be inferred.

## 1. Inspect the repo

- **Repo:** `python3 "$SETUP" detect .` → `owner/name` from the git remote. If empty, ask (or the repo
  has no remote → use `--source local-goals` below).
- **Board:** `gh project list --owner <owner> --format json` — if exactly one plausible board exists,
  use it; if several, ask which; if none or `gh project` isn't scoped, leave the board off (the loop
  runs on issues + labels regardless). Use what you know from context/memory about which repo and board
  this project uses before asking.
- **Already adopted?** If `.sdlc/config.json` exists, you're re-running — that's fine, everything below
  is idempotent and preserves existing settings.

## 2. Scaffold `.sdlc/` if it isn't there

If there's no `.sdlc/`, run **`/agrim-init`** first. On Codex, invoke its scaffolder with `--codex`
so `AGENTS.md` carries the standing SDLC rules, including when `.sdlc/` already exists. Then
continue.

## 3. Pick the verify command (do NOT skip — this is a known trap)

`verify.enforce: true` with an **empty** `verify.command` refuses *every* `done` forever. So find a
real command: `python3 <sigma>/skills/agrim-init/scripts/verify_detect.py detect .` lists the
candidates it finds by reading files (pytest, `package.json` scripts.test, `go.mod`, `Cargo.toml`, a
`Makefile` test target, a CI test step). Confirm one with the user, or ask them for the exact
command. If you genuinely can't get one yet, leave verify off and say so; never
enable enforce without a command. `setup.py configure` guarantees this, but choose the command here.

## 4. Choose the ignore scope (respect an existing choice)

The runtime dirs (`.sdlc/state/`, `.sdlc/ledger/`, `.sdlc/work/`, `.sdlc/knowledge/`) must be
git-ignored. Check what's already there: `python3 "$SETUP" ignore-status .`.
- **Default `tracked`** — add them to the shared `.gitignore`. Right for a repo adopting Sigma as
  its real workflow.
- **`local`** — add them to `.git/info/exclude` instead, touching nothing the team sees. Use this when
  the adopter's intent is "local experiment, don't modify tracked files," or when `.git/info/exclude`
  already carries a blanket `.sdlc/` line (never narrow it). If you see a blanket exclude, prefer
  `local` and leave the existing line alone.

## 5. Write the config + ignores (one call each)

```bash
python3 "$SETUP" configure .sdlc --repo <owner/name> --verify "<the command, or omit>" [--source local-goals]
python3 "$SETUP" ignore . --scope <tracked|local>
python3 "$SETUP" labels .sdlc
```

Defaults `configure` sets: **github discovery scoped to `assignee: @me`**, **ledger on**, **work
(a PR per goal) on** with `auto_merge: off` (a clean PR is left for a human — change to `protected` or
`always` only on an explicit per-repo authorization). It preserves anything already set and never
turns on the verify trap.

`labels` creates the core lifecycle labels (`sdlc:goal`, `sdlc:in-progress`, `sdlc:parked`,
`sdlc:blocked`, `sdlc:blocking`, plus the promotion/design overlays) on the target repo — the same
idempotent, colour-preserving mechanism the loop already calls before every claim/park, so a fresh
adoption doesn't finish fully configured with nothing pickable. It never creates `priority:P<n>`
labels and never applies any label to an issue — those stay a deliberate, separate, human decision
(which issues become pickable is a real triage call, not a mechanical setup step). No-op in
`local-goals` mode or before `discovery.github.repo` is set.

**Public repository?** If you are adopting Sigma's own public repository, or you choose the
public-repository profile for any other public repo, apply it after these three calls. It replaces
step 6's `sync.py bootstrap` line with its own heredoc — still run `/agrim-doctor`, step 6's other
call — and it changes what step 7 says about the ledger:
[references/public-repo.md](references/public-repo.md).

## 6. Bootstrap the ledger + verify

```bash
python3 "$LOOP/sync.py" bootstrap .sdlc     # create the ops branch + seed your file + TEAM.md + push
```
Then run **`/agrim-doctor`** and show the result — it confirms the board scope, ledger, verify, and
`work.enabled` state at a glance.

## 7. Hand back a two-line summary

State plainly: the repo + board it's wired to, that discovery is scoped to `@me`, that the core
lifecycle labels exist now (and that no issue carries `sdlc:goal` yet — applying it to specific
issues is the next, separate, human step), that the ledger is up and pushed, that PRs are on (and the
`auto_merge` value), and the verify command (or that verify is off until one is set). Then:
"`/agrim-loop` runs a goal; `/agrim-ledger` reads/hands-off the ledger."

**One caveat to mention if the host repo has its own edit-gating hooks:** Sigma's Implement phase
edits go through the same tool calls a human would, so a host `PreToolUse` hook that gates source edits
(e.g. on a plan-freshness check) applies to them too. If the repo has one, make sure whatever it
expects (a plan doc, a sentinel) is satisfied, or a source-code goal can be denied mid-Implement.

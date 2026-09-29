---
name: agrim-init
description: Scaffold the per-project .sdlc layer. Use for a new repository setup or /agrim-init.
allowed-tools: Bash(python3 *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-init

Detailed selection triggers: [selection](references/selection.md).

Scaffold the `.sdlc/` project layer, then report what happened.

1. Run the bundled scaffolder from the repository root:

   `python3 "${CLAUDE_SKILL_DIR}/scripts/sdlc_init.py"`

   (Pass a target path as the first argument, and pass through any `--github` / `--demo` / `--vision`
   / `--cursor` / `--codex` flags the user gave. On Codex, pass `--codex` even when the user did not
   spell it out: this writes or refreshes its managed `AGENTS.md` block and preserves other rules.
   **`--github`** also installs the GitHub PM scaffolding —
   epic/task/bug issue templates, the auto-add-to-project workflow, a critical-insight template, and a
   label guide — into `.github/` — and, when `origin` is a GitHub remote, creates the `sdlc:*` and
   `priority:P0`–`P3` labels there (one line per label; exits non-zero naming any label it could not
   create). **`--demo`** queues a small, safe, runnable demo goal so `/agrim-loop`
   shows the SDLC immediately; with `--github`, also file it as an `sdlc:goal` issue (`gh issue create`)
   so it runs on the board. **`--vision`** scaffolds the opt-in north-star. **`--cursor`** installs the
   **Cursor host adapter** (*experimental — not yet verified in a live Cursor session*) — two
   always-applied rules, `.cursor/rules/sdlc.mdc` carrying the SDLC discipline (Cursor has no
   `UserPromptSubmit` hook, so the rule is its analog) and `.cursor/rules/output-contract.mdc`
   pointing status output at `render.py` rather than at prose to imitate — and pins `companions: off`
   so the portable `agrim-*` executors are used.) **`--codex`** adds the standing Codex rule block
   without changing `.sdlc/config.json` or the Claude host path.
1a. `/agrim-init` now git-ignores the machine-written runtime dirs itself, by shelling out to
    `setup.py ignore` (the same mechanism `/agrim-setup` uses) at the end of scaffolding — a bare
    `/agrim-init`, on its own, is a real, supported install and no longer depends on also running
    `/agrim-setup` for `.sdlc/events/`, `.sdlc/ledger/`, and the other runtime dirs to stay
    git-ignored (#2626). To change the ignore *scope* afterward (tracked vs. `.git/info/exclude`
    local-only), move the lines by hand — `/agrim-setup` never relocates a rule it finds already in
    place, so rerunning it does not migrate the scope for you. A failed ignore-write now fails the
    command itself (nonzero exit from the CLI, or `ok: false` from the wizard path) instead of
    silently leaving the runtime dirs uncovered.
1b. **Preflight (#229).** The scaffolder checks, up front, what the loop needs from git and `gh`:
    a git repository, the `work.remote` remote (default `origin`), the base branch pushed there,
    `gh` installed, `gh auth status`, and the token's scopes (`repo`, `workflow`, `read:org` when the
    owner is an organization, `project` when a board is on). **A directory that is not a git
    repository is REFUSED before anything is written** (exit 2; the printed fix is `git init`).
    Every other problem is printed after the scaffold as `[FAIL]` or `[CANNOT VERIFY]` (a
    fine-grained token reports no scopes -- never read that as a pass), with one line per host
    (Claude Code / Codex / Cursor) carrying the exact command, and a `Meanwhile:` line. The same
    check runs any time: `python3 "${CLAUDE_SKILL_DIR}/scripts/preflight.py" check . --sdlc .sdlc`.
    A fresh `git init` with no commit yet is not refused: the missing commit and the remote are
    both reported. A GitLab/Bitbucket remote -- or any URL with more than `owner/repo` in its path,
    such as Bitbucket Server's `/scm/o/r.git` -- is reported as "gh only supports GitHub hosts"
    (pushing still works; only opening a PR needs `gh`). An ssh remote's host may be an
    `~/.ssh/config` alias (`git@github-work:o/r.git`): it is resolved with `ssh -G` and gh is
    checked against the real host; an alias that cannot be resolved is `CANNOT VERIFY`, never a
    `gh auth login` to an alias.
    When `work.enabled` is on but there is no usable remote (or no `gh`, or a non-GitHub host), it prints a **DECISION**:
    keep work on and fix the cause, or run local-only (`work.enabled: false`: the loop edits this
    checkout directly, no worktree, branch, push or PR). Nothing flips it silently.
    - **Claude Code:** ask the user with a real question: add the remote (they give the URL; run
      the printed `git remote add` / `git push -u` lines only on their yes), use another existing
      remote (`preflight.py use-remote .sdlc <name>`), or go local-only
      (`python3 "${CLAUDE_SKILL_DIR}/scripts/preflight.py" local-only .sdlc`). Never run
      `gh auth login` / `gh auth refresh` for them -- they are interactive; hand them the line.
    - **Codex / Cursor:** relay the printed preflight block and DECISION verbatim. It carries the
      exact gesture and the exact config line; do not choose for the user.
1c. **Project board (#235), github mode only** (`--github`, or `discovery.source: github`; never in
    local-goals mode). When no `discovery.github.project.number` is pinned, init prints an `OFFER`
    block and runs nothing. The gesture is
    `python3 "${CLAUDE_SKILL_DIR}/scripts/board_setup.py" create <abs .sdlc> [--owner O] [--title T] [--template N|OWNER/N] [--number N] [--yes]`.
    Without `--yes` it only reads and prints what it would do. With `--yes` it checks the gh
    `project` scope (preflight's check and fix lines), then creates `<repo> — SDLC` or copies a
    template board, and links the repository. It pins `project.number` and `project.owner` right
    away, then sets the Status options and the Priority field (`P0`..`P4`, from
    `discovery.PRIORITIES`). On a board it did not create in the same run (`--number`, a pin), it
    only ADDS missing options and renames, recolours or deletes nothing. At the end it reads back the board's
    "Item closed" workflow. It refuses a title the owner already uses and prints the manual runbook
    (`--number N` adopts that board on purpose). Every step prints `[ok]`, `[FAIL]` or `[manual]`.
    A failure exits 1 and prints the exact resume command. See [board](references/board.md).
    - **Claude Code:** ask the user a real yes/no question ("Create and pin the board?"). Only on
      yes, run the printed `... --yes` line. On no, say the loop mirrors nothing until a number is
      pinned. Never run it unasked.
    - **Codex / Cursor:** relay the OFFER block verbatim. It carries the exact command for the
      user to run.
2. Read the printed `created / skipped` summary and the git tip. `/agrim-init` creates
   `.sdlc/ledger/` holding only a `README.md`; the ledger stays off (`ledger.enabled: null`) until
   you enable it and `/agrim-ledger` bootstraps it.
3. Report which files were created, then settle the verify command. `loop.py verify` reads it
   from two places only: a local goal's frontmatter `verify_command` (wins), else
   `verify.command` in `.sdlc/config.json`. `.sdlc/project.md` is not read for it. The scaffold
   never leaves `verify.enforce` on with an empty command, because that refuses every `done`. A
   fresh config ships enforce OFF, with the reason in `verify._why`, and the scaffolder prints the
   numbered candidates LAST, after every file it writes (`--codex`, `--cursor`, `--github`), each
   with its `[id ...]` -- a hash of the exact command -- and the gestures naming the scaffolded
   `.sdlc` by its absolute, quoted path
   (`python3 "${CLAUDE_SKILL_DIR}/scripts/verify_detect.py" detect .` lists them all with ids; use
   `python` or `py` where `python3` is absent).
   **Never paste a detected command into a shell line.** Candidate text comes from the repository
   (a CI `run:` line is whatever its author wrote), so the gestures below never carry it: `confirm`
   re-detects, and stores candidate `<n>` only if it still has the `<id>` that was shown --
   otherwise it refuses ("the repository changed since the report") and stores nothing; re-run
   `/agrim-init` and confirm from the new report. A `.sdlc` that is a symlink is refused.
   - **Claude Code:** ask the user with a real question: confirm a detected candidate, replace
     it with their own command, or decline. On confirm, run
     `python3 "${CLAUDE_SKILL_DIR}/scripts/verify_detect.py" confirm .sdlc <n> <id>` with the
     number and id printed beside the chosen candidate (or paste the printed confirm line, which
     already carries the absolute `.sdlc` path). On replace,
     write their command to a file with your file-writing tool (not a shell `echo`), then run
     `python3 "${CLAUDE_SKILL_DIR}/scripts/verify_detect.py" set .sdlc --command-file <file>`.
     Either sets the command and turns enforce ON. On decline, run
     `python3 "${CLAUDE_SKILL_DIR}/scripts/verify_detect.py" decline .sdlc`. That keeps enforce
     OFF and records why. If nothing was detected, say so and say that enforce is OFF.
   - **Codex / Cursor:** relay the printed candidate block verbatim. It carries the exact
     `confirm` gesture (number and id) and the exact config line. Do not guess a command on the user's behalf.
4. Relay the git tip. Never overwrite a file the scaffolder reports as skipped — those hold live state.

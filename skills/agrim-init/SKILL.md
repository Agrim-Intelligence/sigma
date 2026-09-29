---
name: agrim-init
description: The one setup command - scaffold .sdlc, check access, choose mode and verify command. Use for a new repository setup or /agrim-init.
allowed-tools: Bash(python3 *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-init

Detailed selection triggers: [selection](references/selection.md).

**This is the one command a new user runs after installing Sigma** (#236). It asks the few
questions that matter and leaves the repository ready for `/agrim-loop`. `/agrim-setup` is an
alias: it runs this same flow. The flow is `scripts/init_flow.py`, the same Python on every host.

1. Run the flow from the repository root:

   `python3 "${CLAUDE_SKILL_DIR}/scripts/init_flow.py" .`

   plus any flags the user gave. On Codex, pass `--codex` even when the user did not spell it out
   (it writes or refreshes the managed `AGENTS.md` block and keeps other rules); on Cursor,
   `--cursor`. It runs, in order, and prints one section each:
   `1/5 preflight` (git, remote, base, `gh`, auth, scopes -- 1b below), `2/5 mode` (local-goals or
   github; the default is github when `origin` is a GitHub repository), `3/5 verify` (3 below),
   `4/5 github` (github mode only: the `sdlc:*` + `priority:P0`-`P3` labels, `assignee: @me`, the
   board OFFER -- 1c below -- and the ledger question), `5/5 summary` and a `Next:` line
   (`/agrim-loop`, or `--demo` first when nothing is queued).
   Every question not yet answered is ONE `[ask]` line naming the flag that answers it, in one
   machine-readable shape: `[ask] <id>: <prose> -> <answer> ; <answer>`, each answer a bare
   `--flag` or `--flag VALUE|VALUE` (upper-case values are placeholders you fill in). Everything
   after the last ` -> ` is the machine part (`tools/onboarding_control.py` parses it):

   | Question | Flags |
   |---|---|
   | mode | `--mode local-goals` / `--mode github` (`--repo OWNER/NAME` when `origin` is not GitHub) |
   | work (only when no usable remote) | `--local-only` / `--work on` |
   | verify | `--verify N:ID` / `--verify-command-file FILE` / `--no-verify` |
   | board (github) | `--board yes` / `--board no` |
   | ledger (github) | `--ledger yes` / `--ledger no` |

   Other flags: `--demo` (queue a runnable demo goal), `--vision`, `--codex`, `--cursor`,
   `--github-templates` (the `.github/` issue templates and workflows; `--github` is shorthand for
   `--mode github --github-templates`), `--ignore-scope local` (1a below).
   **`.sdlc/config.json` is the one source of truth.** `.sdlc/state/init.json` records only that a
   question was answered, so a re-run does not ask it again; it never re-applies a value over
   config.json. A setting changed since (`preflight.py local-only`, a hand edit) is kept, and the run
   prints `[kept] ... config.json wins` with the flag that would change it. Only a flag on the
   current run changes an existing setting (work, ledger, board, source). The repository is
   `--repo`, else config.json's, else the current `origin` (never a remembered one).
   **`--yes` takes only the safe defaults** (mode = detected, ledger = off), and only for a question
   config.json does not already answer: on an already-configured repository it changes nothing and
   says "kept". It never answers the board, the verify command, or a work flip. A template value the
   flow scaffolded counts as unanswered ONLY while config.json is exactly what the flow last wrote:
   init.json records config.json's SHA-256 and mtime at that moment, and any other write -- `setup.py
   configure`, `preflight.py local-only`, `board_setup.py` pinning, a hand edit, even a revert to
   the same bytes -- makes every key it carries explicit, so `--yes` leaves it. A missing,
   unreadable or fingerprint-less init.json means nothing is open. Only a key config.json does not
   carry (absent or `null`, e.g. the template's undecided `ledger.enabled`) stays open.
   - **Claude Code:** ask the user each `[ask]` as a real question, then re-run the same command
     with the answers as flags. Never pass `--board yes`, `--verify`, `--local-only` or
     `--ledger yes` on the user's behalf.
   - **Codex / Cursor:** relay the `[ask]` lines verbatim; they carry the exact flags. Do not choose
     for the user.

   Exit 0: every attempted step passed (open questions are allowed). Exit 1: a step failed; the last
   line is `Resume: <command>` -- relay it. Exit 2: refused before anything was written (not a git
   repository, another plugin active, github mode with no repository).

   The scaffolder underneath, `scripts/sdlc_init.py`, still runs alone (same `--codex` / `--cursor`
   / `--demo` / `--vision` flags); it asks nothing and never sets the mode -- its `--github` creates
   labels but leaves `discovery.source: local-goals`, so prefer the flow.
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
1c. **Project board (#235), github mode only** (never in local-goals mode). When no
    `discovery.github.project.number` is pinned, init prints an `OFFER` block and runs nothing;
    until you answer, the flow keeps `project.enabled` OFF (with the reason in `_enabled_why`),
    because the loop would otherwise create a board on its first github pick. `--board yes` runs
    the gesture below with `--yes` and turns `project.enabled` on only when it succeeded and a board
    is pinned; a failure leaves `project.enabled` as it was (nothing writes it before success, and
    board_setup never writes it). On success board_setup's "enabled is not true" note is dropped
    from the flow's output, since the flow turns it on on the next line. On an already-pinned board (say one
    pinned by hand after declining), `--board yes` runs the same gesture, which refuses a pinned
    number the owner does not have, then turns mirroring on. `--board no` turns mirroring off and
    records the decline. A remembered answer never runs the gesture. The gesture is
    `python3 "${CLAUDE_SKILL_DIR}/scripts/board_setup.py" create <abs .sdlc> [--owner O] [--title T] [--template N|OWNER/N] [--number N] [--yes]`.
    Without `--yes` it only reads and prints what it would do. With `--yes` it checks the gh
    `project` scope (preflight's check and fix lines), then creates `<repo> — SDLC` or copies a
    template board, and links the repository. It pins `project.number` and `project.owner` right
    away, then sets the Status options and the Priority field (`P0`..`P4`, from
    `discovery.PRIORITIES`). On a board it did not create (`--number`, a pin), it only APPENDS
    missing options and renames, recolours, reorders or deletes nothing. **Adopting a board does
    not change how the loop picks work:** the `Ready` lane is the loop's queue switch, so it is
    added only to a board board_setup created that has no card yet; elsewhere it prints
    `[skip] Ready lane`, the loop keeps picking by the `sdlc:goal` label, and the printed
    `board_migrate.py --owner O --project N --backlog <lane> --apply` line is the explicit step to
    move to the board queue (it adds `Ready` and moves the queued cards into it). At the end it reads back the board's
    "Item closed" workflow. It refuses a title the owner already uses and prints the manual runbook
    (`--number N` adopts that board on purpose). Every step prints `[ok]`, `[FAIL]` or `[manual]`.
    A failure exits 1 and prints the exact resume command. See [board](references/board.md).
    After a successful `--board yes` the flow also PRINTS (never runs) the canonical-layout step
    (#234): `scripts/board_layout.py fields|views <abs .sdlc>` (dry runs until `--yes`: Phase, Area
    and Model tier fields, and the six views) and `verify` (read-only). Relay those lines; run them
    only when the user asks. Design and runbook: `docs/board.md`.
    - **Claude Code:** ask the user a real yes/no question ("Create and pin the board?"). Only on
      yes, re-run the flow with `--board yes`; on no, with `--board no` (the loop mirrors nothing
      until a number is pinned). Never run it unasked.
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
   The flow's flags do the same as the gestures below: `--verify <n>:<id>` is `confirm`,
   `--verify-command-file <file>` is `set --command-file`, `--no-verify` is `decline`.
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

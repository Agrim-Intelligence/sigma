# Changelog

All notable changes to Sigma are recorded here, newest first.

## Unreleased

- `tests/test_risk_detect.py` is no longer flaky on macOS (#244, #145). The root cause was in
  `skills/agrim-loop/scripts/risk-detect.sh`. It ran a per-command `LC_ALL=C grep` inside a
  process-substitution subshell. With Homebrew bash (linked to libintl), every locale assignment
  calls `setlocale()`, which calls into CoreFoundation, and CoreFoundation is not fork-safe. Under
  load, the forked subshell sometimes crashed with SIGSEGV, and because the script is fail-open,
  the crash looked like "no content hits". The script now sets `LC_ALL=C` once in the main shell
  and never assigns a locale variable again. It also prints `risk-detect: content scan incomplete`
  on stderr when the content scan dies before it finishes; it still exits 0 with valid JSON.
  Measured with the whole file at `-n 8` plus 12 busy loops: 17 of 110 runs failed before the fix
  and 0 of 160 after. Two new tests guard the fix, and both fail on the old script. One runs the
  script under xtrace and checks that the only locale assignment is the top-level pin. The other
  runs a copy of the script whose scan is killed partway through, and checks that it prints the
  stderr warning.
- Sigma now detects the plugin under its previous name on the same repository, and refuses rather
  than writing alongside it (#240). `skills/agrim-loop/scripts/coexist.py` reads the Claude Code
  settings (`enabledPlugins` and hand-registered hooks, with local over project over user
  precedence), Codex's `config.toml`, the `.sdlc` owner markers, and the live watcher. While the
  old plugin is active, `/agrim-init`, `loop.py start`, `watch_daemon.py` and `migrate.py --apply`
  refuse (exit 2) and print what was found and the fix. The automatic watcher start stays off.
  `/agrim-doctor` shows a failing `coexistence` row, `status.py` warns on stderr, and the
  session-start hook repeats the message. `SIGMA_ALLOW_COEXIST=1` lets the write surfaces continue
  with a warning. Two watchers were already impossible, because both plugins use the same lock
  files. Sigma now also names a watcher it did not start instead of only reporting "already
  running". New local state: `.sdlc/state/owner.json` and `.sdlc/state/watch.owner`. Only an
  active signal can make an unmarked watcher active, so a Sigma-only upgrade with old state and a
  running pre-upgrade watcher is not refused. The detector also reads Claude Code's managed
  settings, accepts comments in settings files, treats a plugin enabled but not installed on this
  machine as a note, matches hooks by path rather than by substring, and reads every TOML spelling
  of a Codex plugin entry. A running watcher is identified as the old plugin's only by a directory
  named exactly the old name in its script path, never by the repository path it was given. The
  session-start hook no longer stops after the message: the ledger-watcher staleness warning, the
  wizard and the policy brief still run. Under `SIGMA_ALLOW_COEXIST=1` the hook adds one line and
  `coexist.py check` exits 0. On macOS and Windows a watcher left running by the old plugin after it
  was disabled is only a note, because its command line cannot be read. See `docs/upgrading.md`.
- `/agrim-init` now checks what the loop needs from git and `gh` before the first goal does
  (#229). The new `skills/agrim-init/scripts/preflight.py` (stdlib only) checks: a git repository,
  the `work.remote` remote (default `origin`, or which remotes exist), the base branch pushed
  there, `gh` installed, `gh auth status`, and the token's scopes: `repo`, `workflow`, `read:org`
  when the owner is an organization, `project` when a board is on. It parses both `gh` scope
  formats. A fine-grained or app token reports no scopes, so that case prints `CANNOT VERIFY`,
  never a pass. A directory that is not a git repository is refused before anything is written.
  Every other failure prints one line for each host (Claude Code, Codex, Cursor) with the exact
  command, plus what Sigma does meanwhile. When `work.enabled` is on but there is no remote (or no
  `gh`), init prints a decision: fix the cause, or turn `work.enabled` off. Two gestures act on
  it: `preflight.py local-only <sdlc>` and `use-remote <sdlc> <name>`. Nothing is switched off
  silently. `work.py start` now raises the same message in place of git's raw `fatal: 'origin'
  does not appear to be a git repository`. It asks only after the fetch has failed, so the
  measured call count on the success path is unchanged. `/agrim-doctor` runs the same checks as
  rows. Each fix comes from the check that failed, so with `gh` absent the fix is to install
  `gh`, where it used to say `gh auth login`. Every git and `gh` call is time-limited by
  `SIGMA_WATCH_CALL_TIMEOUT` (default 120s), and a call that runs over has its whole process tree
  killed. Nothing prompts. The test suite now also guards `subprocess.Popen` against live `gh`
  calls, and child processes get an empty gh config.

- `/agrim-init` now leaves a working verify command, and never leaves `verify.enforce` on with an
  empty command (#228). Before this, the shipped config refused `record done` for every goal,
  including the Quickstart demo. The new `skills/agrim-init/scripts/verify_detect.py` proposes a
  command by reading files only: pytest, `package.json` scripts.test, `go.mod`, `Cargo.toml`, a
  `Makefile` test target (its recipe is shown, as `package.json`'s script is) or a CI test step.
  Each candidate is printed with an id, a hash of its exact command. `verify_detect.py confirm
  <sdlc> <n> <id>` re-detects and records candidate `n`, turning enforce on, only if it still has
  that id. If the repository changed since the report, it refuses and stores nothing. So no
  repository text is ever pasted into a shell, and the stored command is exactly the one shown.
  Detection ignores hidden, cache and vendored directories and non-source files, so running pytest
  once, or the `AGENTS.md` that `--codex` writes, cannot change the candidates. The report is
  printed after every file `/agrim-init` writes. Its gestures name the scaffolded `.sdlc` by
  absolute, quoted path. A `.sdlc` that is a symlink is refused. `set .sdlc
  --command-file <file>` (or `-` for stdin) records your own command. `decline` keeps enforce off
  and records the reason. A CI step containing a shell metacharacter (`` ` $ ; & | < > ``) or a
  control character is never proposed: it is named by file only. Every printed line escapes
  control characters, so an ESC sequence in a repository file cannot repaint the terminal.
  Claude Code asks the user to choose. Codex and Cursor print the numbered candidates and the
  exact config line. Printed gestures and the demo's `verify_command` use the interpreter that is
  on PATH (`python3`, `python` or `py`). A goal's `verify_command: ''` now counts as empty in
  `loop.py`, as it already did in `/agrim-doctor`. A fresh config now ships `verify.enforce: false`, with the reason in `verify._why`. The
  `--demo` goal carries its own `verify_command`, so the Quickstart reaches `done`. The false
  instruction to fill in `.sdlc/project.md` is gone; the command is read only from goal
  `verify_command` or config `verify.command`. `/agrim-doctor` and the setup wizard flag enforce
  on with no command, with a one-line fix. `record done` now names a missing command rather than
  saying "run verify first".

- Upgrade path from the plugin's previous name (#239). A repository adopted under the previous
  name's 1.4.x releases now works under Sigma with no data loss. Sigma reads the old schema ids
  (features, landing, withheld and propagation records), the old feature-doc and Codex
  `AGENTS.md` markers, the old environment-variable prefix (`SIGMA_*` wins when both are set), the
  renamed `drift_watch.channels` key, and the old PR and issue markers, so an old `block` comment
  on an open PR still blocks it. One helper, `skills/agrim-loop/scripts/legacy.py`, does all of
  this reading.
- New: `skills/agrim-doctor/scripts/migrate.py`, a one-shot, idempotent rewrite of that state to
  Sigma's names. It is a dry run by default and writes only with `--apply`. It swaps text in place
  and checks each result by reading it back. It refuses (exit 2) anything it cannot rewrite with
  certainty, leaves history alone, lists environment variables by name only, and refuses to run
  while a watcher is live. See `docs/upgrading.md`.
- Cross-repository propagation no longer overwrites a sibling repository's registry file that
  still carries the old schema id.
- Labels exist before the first pick (#230). `/agrim-init --github` (when `origin` is on GitHub),
  `setup.py labels` and `loop.py start` in github mode now create the ten `sdlc:*` labels and
  `priority:P0`–`P3`. Each run reads the repository's labels once over REST and creates only the
  missing ones, so an existing label is never recoloured and a bootstrapped repo costs one read,
  no writes. Every label is reported as `created`, `existed` or `FAILED: <reason>`; "ensured" is
  printed only when all were measured present, and any failure exits non-zero naming the label
  (`loop.py start` refuses to start). Previously `setup.py labels` printed "ensured" even when
  every create had been refused. When no open issue carries `sdlc:goal`, `loop.py next` still
  prints a bare `DONE` on stdout and now says `0 issues carry sdlc:goal — label one to start` on
  stderr.

## 1.0.0 — the first public release

The first release of the public core: a gated software development lifecycle for coding agents,
run from GitHub issues, with every phase reviewed before the next one starts.

- Seven gated phases for every goal: goal, research, plan, plan review, implement, review and
  retrospective. Each phase writes an artifact the next one reads, and a review verdict is needed
  before any code is edited.
- `/agrim-goal` runs one goal through all seven phases with an approval gate at each boundary, for
  supervised, end-to-end work.
- `/agrim-loop` drains a backlog autonomously: it claims goals, dispatches fresh phase agents, opens
  verified pull requests, and parks or continues each goal until stopped or out of budget.
- Work is tracked where it already lives: GitHub issues, a project board, and the `sdlc:*` label
  model (goal membership, in-progress, blocked, blocking, parked, needs-confirmation).
- `/agrim-doctor` checks the project setup, the dependencies and the host, and prints the fix
  command for anything not ready. `/agrim-init` scaffolds a project's `.sdlc/` layer and
  `/agrim-setup` adopts the kit into an existing repository.
- A local action log records what each goal did; `/agrim-log` reads it back as live status, and
  `/agrim-status` shows the backlog, the current iteration and the review queue.
- One renderer builds every status line, so a single glance names the goal, the phase and what is
  happening right now.
- Host-agnostic by design: the full pipeline is validated on Claude Code, and a Codex adapter runs
  the same skills and scripts there (a complete Codex goal through pull-request merge is still
  unverified). A Cursor adapter ships as experimental, not yet verified in a live Cursor session.
  Lifecycles live in the kit's own Python and in git, never in one host's hooks.
- Safe by default: nothing sends data off the machine, spawns a background process, or consumes
  quota without the operator opting in.
- Released under the MIT licence.

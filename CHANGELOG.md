# Changelog

All notable changes to Sigma are recorded here, newest first.

## Unreleased

- **Rebase upkeep keeps merge-commit landings instead of flattening them, and a locked unit has
  an exit** (#161, ported from the predecessor's #2756). A goal that landed on `feature/<unit>` as
  a "Merge pull request #N" commit was flattened by the next upkeep pass: a plain `git rebase`
  replayed its second-parent commits onto the first-parent line, where their subjects carry no PR
  trace, so every later pick refused the branch as carrying "commits no pull request accounts
  for" -- forever, with no way out inside Sigma (measured on a real host repo: 18 commits, 76
  behind `main`, five teammates' loops blocked). Upkeep and `/agrim-rebase` now run `git rebase
  --rebase-merges`, which recreates the merge with its subject intact; a squash-only branch
  replays exactly as before. For a branch that was already flattened, `feature_rebase.py ack .sdlc
  <unit> <sha>...|--all` records the confirmed commits in the tracked
  `.sdlc/features/rebase-acks/<unit>.json`, keyed by patch-id so the ack survives the rebase it
  unblocks, and read from the remote integration branch too so one landed ack frees every
  teammate. Only commits the check currently reports can be acked. The filed finding now names
  both real exits instead of a remedy that had no mechanism. **Sigma-only:** the #144 data-loss
  guard stays the outer guard -- an ack only lets the pass reach the replay, and a replay that
  would remove or roll back the branch's content is still refused before any push (acked commits
  plus a revert in the base: `would-drop`, remote unchanged), including when `--rebase-merges`
  puts merge commits in the replayed history. The predecessor's companion fix in the same
  release, `promote.py list` saying UNKNOWN for an unread queue (#2757), was already ported.

- The rebase loss guard (#144) is now exact about what a human decided and what the branch already
  had (#278, closes #144's two open review findings). **The conflict walker no longer exempts a
  whole file for a one-line resolution.** Resolving one conflicted hunk used to exempt the whole
  path from the guard, so a base revert that git had already merged outside the markers (300 of the
  branch's lines in the repro) was force-pushed. Now only a resolution that deletes the file
  (action `removed`, ABANDON on a file the base deleted) is exempt, and content resolutions stay
  guarded. The walker's option `[3]` now says what it does: "Take the base's version of the whole
  file (drops this branch's changes to it)", not "Abandon this hunk". **`rebase_brief.push_branch`
  now allows a local deletion that was never pushed.** It used to compare HEAD only with the
  remote tip, so a local `drop obsolete` commit was refused, and the advice (`git reset --keep
  <remote tip>`) threw away the local commits. It now also takes the pre-rebase head:
  `attempt_rebase` passes it, the walker reads the stopped rebase's `orig-head`, and the
  manual-recovery push reads `<branch>@{1}` when the branch reflog's newest entry is a rebase's own
  landing on that branch (`rebase (finish): …`, `pull <argv> (finish): …` for a one-go `pull
  --rebase`, or `rebase (continue) (finish): …`, measured against real git; older gits' `rebase
  finished: …` accepted, not measured). A loss that already exists between the remote tip and that
  head is exempt **only when it is the branch's own deliberate local DELETION**: the path is in
  the remote tip and gone from that head, a non-merge commit unique to the branch has a `D` for
  exactly that path, and the base has not touched the path since the remote tip. So a local `git
  rm` commit passes (amended or squashed too), but the same loss left by an earlier, never-pushed
  local rebase onto a base holding a revert is refused (it was force-pushed before) — including
  when a sibling branch commit edited the same file, which the first version of this rule took for
  the loss's author (review block #2: `x.txt` 359 → 60 lines force-pushed). **A rollback in that
  range is never exempt**, even the branch's own: the human confirms a deliberate one with the
  `git push --force-with-lease <remote> HEAD:<branch>` the refusal prints. With no base, no
  pre-rebase head, or a **shallow clone** (the refusal says so) nothing is exempt, and all of one
  push's guard reads share one wall-clock budget, `SIGMA_WATCH_CALL_TIMEOUT` (default 120s);
  running out refuses the push. Everything the rebase itself loses is still refused. The advice names the pre-rebase
  head. When that head is unknown, the advice points at `git reflog <branch>` and no longer at the
  remote tip. If a refusal cannot put the branch back (`git reset --keep` fails), the refusal is
  recorded in the git dir and every later push of that branch (`push_branch`, `work.rebase()`,
  `work.pr()`) is refused with the recovery command until HEAD is back at the pre-rebase head.
  **`work.rebase()` runs the same guard before its goal-branch force-push**, on both the plain path
  and the CHANGELOG union rescue, but only over the paths the goal changed since it forked. So a goal
  whose commit reached the base as a copy (a rebase-merge) that was later reverted is no longer
  replayed away silently. It returns `rebase refused, it would lose content: …` (classified
  `needs_decision`, not `merge_conflict`: nothing conflicts), pushes nothing and resets the worktree
  to its pre-rebase head. `docs/branching-model.md` §15 now states the fleet-wide cost of the
  conservative refusal: a reverted dependency bump blocks upkeep on every feature branch cut in
  that window, and each branch stays blocked until a person resolves it. Each fix has a real-git
  test, and each test was seen red against the old code and against a deliberately broken guard.
- **The plan-review verdict is a record: `work.py pr` can refuse a plan that was not reviewed**
  (#258). Until now the plan-review verdict was an optional journal event nothing read.
  - The plan-review brief now names the exact bytes under review: a `Plan file:` line and a
    `Plan sha256:` line (sha256 of the plan's raw bytes; the committed branch copy, pointed at with
    `git show`, when the main checkout has none).
  - New verb `work.py record-plan-review <sdlc> <goal> --verdict SOUND|SOUND-WITH-REFINEMENTS|FIX-FIRST
    --plan-sha256 <hex> [--reason <text>]`. The sha comes from the brief written at dispatch (the
    documented gestures read its first `Plan sha256:` line, never a rebuilt brief). It refuses,
    writing nothing, when any existing copy of the plan (main checkout, committed on the branch) no
    longer holds those bytes. It stores `{at, goal, plan, plan_hash, reviewer_route, verdict}` at
    `.sdlc/state/gates/<stem>.json` (atomic replace) only while `work.enabled` is on and that
    `.sdlc` holds the goal's work record; otherwise it keeps no file and says so. `work.py finish`
    prunes it with the work record.
  - It is now the single emitter of the `gate` journal event (`gate=plan_review`, verdict
    `pass|warn|block`, no new field); `agrim-plan-review` no longer tells the agent to `loop.py
    emit` one.
  - New opt-in `gates.plan_review.enabled` (ships OFF, not org-lockable, #174). With it on,
    `work.py pr` refuses to push unless an approving verdict (SOUND or SOUND-WITH-REFINEMENTS) is
    recorded for the exact bytes of the plan on the branch (the main checkout's copy when the branch
    carries none). A malformed record, an unreadable plan, or a recorded plan that no longer
    resolves fails closed. Not covered: `<stem>.slices.json`, design PRs, a goal with no plan (left
    to `gates.hard_plan_gate`), and pushes after `pr`'s own (a `work.py rebase` force-push,
    `merge()`). The record is agent-written: it proves a verdict was recorded, not who reviewed.
  - `docs/enforcement.md` regenerated: "Plan review before implementation" moves from `advice` to a
    `Python gate` row (`work.py` `_plan_review_refusal`). `/agrim-doctor` gains a
    `plan-review gate` row, including ON-but-not-enforced when `work.enabled` is off.

- **`/agrim-init` is the one entry point; `/agrim-setup` is its alias** (#236, folds in #186).
  - New `skills/agrim-init/scripts/init_flow.py` runs, in order: preflight (#229), mode
    (local-goals or github; github by default when `origin` is a GitHub repository), the verify
    command (#228), and in github mode the labels (#230), `assignee: @me`, the board OFFER (#235)
    and the ledger question; then a one-screen summary and the next command. It integrates the
    sibling scripts; it does not re-implement them.
  - Questions are flags, so Claude Code, Codex and Cursor run the same flow: `--mode`, `--repo`,
    `--local-only` / `--work on`, `--verify N:ID` / `--verify-command-file` / `--no-verify`,
    `--board yes|no`, `--ledger yes|no`. An unanswered one prints an `[ask]` line with its flag.
    `--yes` takes only the detected mode and ledger off; it never answers the board, the verify
    command or a work flip. Answers are remembered in `.sdlc/state/init.json`, so a re-run is
    idempotent (measured: zero label writes and an unchanged config on the second run). Exit 1
    on a failed step or a blocking preflight problem, with a `Resume:` line; exit 2 when refused
    before any write.
  - In github mode `discovery.github.project.enabled` stays off until `--board yes`: the loop
    otherwise creates a board on its first github pick, so "no board without a yes" held for init
    and broke at the first `loop.py next`.
  - The scaffolded example goal ships `status: proposed`; the loop's first pick on a fresh repo
    was "Example goal — delete me". `--demo` still queues a runnable demo.
  - `setup.py configure` refuses (exit 2, nothing written) github mode with no repository, and a
    missing `.sdlc/` (was a traceback). `setup.py init ...` forwards to the flow.
  - The setup wizard fires only in an adopted repository (`.sdlc/config.json`, and no other
    plugin's `state/owner.json`), in `setup_wizard.wizard_status()` so every host gets it (#186:
    it fired in every repository the user opened, where a decline could not be remembered).
  - `loop.py start` no longer warns about `work.enabled` off once `--local-only` (or
    `preflight.py local-only`) recorded the choice; the warning, the `record done` note and
    doctor's row point at `/agrim-init` instead of `/agrim-setup`.
  - The old `sdlc_init.py --github` still works and now says it does not switch the backlog to
    GitHub. The control, on a fresh repo with a labelled issue: the old gesture leaves the issue
    unpicked (and, before this change, picked the placeholder goal); the new flow picks it.

- **`/agrim-init` offers to create the GitHub Project board, and pins it** (#235). The loop only
  creates a board when the owner has none, so in any real organisation nothing ever wrote
  `discovery.github.project.number`. Now:
  - In github mode (`--github` or `discovery.source: github`, never in local-goals mode), init prints
    an OFFER block and makes no call. On Claude Code the agent asks yes/no. On Codex and Cursor the
    block carries the command to run.
  - New `skills/agrim-init/scripts/board_setup.py create <.sdlc> [--owner O] [--title T]
    [--template N|OWNER/N] [--number N] [--yes]`. Without `--yes` it only reads. With `--yes` it
    checks the gh `project` scope (preflight's check and per-host fix, #229), then creates
    `<repo> — SDLC` linked to the repository, or copies a template board and links it. It pins
    `project.number` + `project.owner` right away (atomic; no other key touched). It sets Status to
    the `project.columns` options, renaming GitHub's `Todo` / `In progress` with their ids kept so
    the built-in workflows stay on, and adds Priority `P0`..`P4` from `discovery.PRIORITIES`.
    Finally it reads back the "Item closed" workflow.
  - It refuses a title the owner already uses and prints the manual runbook (`--number N` adopts
    that board on purpose). A missing `project` scope is refused with the remediation. Any failed
    step exits 1 with the exact resume command, and a re-run reuses the pinned board.
  - GraphQL schema introspection (read-only) shows `createProjectV2View` exists, while no mutation
    creates or enables a workflow. When "Item closed" is off, board_setup prints the manual step and
    the `/projects/<n>/workflows` deep link. Whether `copyProjectV2` carries views and workflows
    could not be checked without a mutation; `references/board.md` has a 5-step check for the owner.
  - The loop honours a pinned number outside the first 100 boards (one `gh project view`, only
    then). A pinned number now wins over a title match.
  - New doctor row: `pinned board #N reachable`. It fails only when GitHub answers that the board
    does not exist. When the read itself fails (offline, rate limit, gh missing, no `project`
    scope) there is no row. The read is skipped under `cheap_only` (the SessionStart wizard).
  - Adopting a board does not change how the loop picks work (review of PR #279, block #2). The
    `Ready` lane is the loop's queue switch: with it, only a card in `Ready` is picked, so adding it
    to a board whose goal cards sit in other lanes stranded them and the loop read DONE (reproduced:
    pick `5` before adoption, nothing after). board_setup now adds `Ready` only to a board it
    created (`project.setup_created`) that has no card yet. Elsewhere it prints `[skip] Ready lane`
    and the explicit `board_migrate.py --owner O --project N --backlog <lane> --apply` step, which
    adds the lane and seeds it. A `Ready` the board already has is left as it is. A resume of our
    own still-empty board finishes it like a fresh one; once a loop tick carded goals on it, the
    resume is an adoption.
  - The loop's empty-`Ready` warning now counts every open, eligible goal card outside `Ready` (a
    human's own `Todo`, `Needs design`, no Status), not only Backlog cards, names each lane and the
    migrate command, and prints once per run.
  - Adopting a board a human built (`--number N`, or a pin) renames nothing (review of PR #279).
    Every existing option keeps its id, name, colour, description and position, and only the
    missing options are appended after them. Only our own empty board gets GitHub's `Todo` /
    `In progress` renamed. A lane differing only by case (`In progress`) is mapped in `project.columns`. A
    same-named field that is not single-select, a `p0`-style Priority variant, or unreadable
    option colours is REFUSED (exit 2) with the manual fix.
  - `sources._options_mutation` quotes every value it puts into GraphQL with JSON escaping, and
    sends an existing option's colour and description back instead of resetting them. The fields
    read is paginated (`per_page=100`, `--paginate`).
  - The Priority field is `P0`..`P4` (from `discovery.PRIORITIES`), not the issue's `P0`..`P3`.
    On Windows the resume command is double-quoted, and it is not printed when a value contains
    `"`, `%`, `$`, a backtick or `!`.
  - Cost, measured against a fake gh: a fresh create is 4 GraphQL calls (3 mutations) plus
    5 + ceil(boards/100) REST reads. A re-run on a finished board is 1 GraphQL read. The loop's
    steady state adds no calls.

- **Done now means merged** (#232, owner decision). On the shipped defaults (`work.auto_merge:
  "off"`, `work.require_review: "changes"`) a goal used to be recorded `done` and its issue closed
  while its PR was still open, and the review gate never ran. Now:
  - `loop.py record <dir> <goal> done` exits 4 (`REFUSED: PR #N …`) unless the goal's PR is merged,
    whatever `auto_merge` says. It reads the PR once, over REST. An open PR, or one it cannot read,
    points you to `record review`. A PR closed without merging points you to `record parked`.
    Goals with no PR, including local goals, are unchanged.
  - New non-terminal outcome, `record review` (awaiting merge). The issue stays open and keeps
    `sdlc:goal` and the claim's `sdlc:in-progress`. Its card moves to QC and it gets one note. The
    ledger claim ends, the checkout is kept, and `next` never serves the goal again.
  - New `loop.py reconcile-merges <dir>`. It also runs on every `next`/`next-batch` (inside the
    budget gate) and on the watch daemon's `reconcile_tick.py`. When a waiting goal's PR has merged,
    it replays the existing merge observation and records `done`, which closes the issue and
    releases the checkout. A PR closed without merging parks the goal. It is idempotent, and a
    close that fails is retried on the next pass.
  - Cost is bounded. Each pass reads at most 10 PRs, oldest-checked first. The automatic triggers
    skip a PR they re-read in the last 120 s. With nothing waiting, a pass makes no `gh` call.
    Measured with a fake runner: 10 PR reads per pass at both 50 and 500 waiting goals, with 3 ms
    and 14 ms of local overhead. At 100x the call count stays flat and close latency grows to
    ceil(N/10) passes.
  - `work.py merge` now runs the post-PR review gate under `auto_merge: "off"` too. A `sigma:block`
    parks the merge, and a clean line reads `… — review gate passed (require_review: changes) —
    auto_merge is off, leaving PR #N for a human`.
  - The `/agrim-loop` routing, `references/landing.md`, the README table, the public-repo guide
    and the generated `docs/enforcement.md` now describe this flow.
  - `tests/test_public_bootstrap_control.py` runs one goal end to end on the defaults, against a
    fake `gh` and a bare origin. It was red on the old code, and each guard was broken once and
    seen red.
- The README's first-run path is now executable as written (#231). The "older plugin" callout
  named a floor from the previous name's version numbering, which the shipped 1.0.0 plugin could never meet; it now states the 1.0.0
  floor that `AGENTS.md` sets and `/agrim-doctor` enforces, and the upgrade command
  `claude plugin update sigma@sigma` (the old `marketplace update` line only refreshed the
  listing). The Quickstart has one install per host (Claude Code, Codex, Cursor) behind a single
  `<SIGMA_REPO>` placeholder, names `/agrim-init` as the next step (with one line on when to add
  `/agrim-setup`), and adds "What `/agrim-init` will ask you" and "If `/agrim-init` says you lack
  access" with the exact commands the preflight prints. `--github` is no longer described as
  setting up the Projects board: it copies `.github/` templates and creates labels, and the loop
  creates the board. Rows marked "(opt-in)" that are on by default are relabelled, internal issue
  references are removed from the README, and no shipped file names the previous name's
  label-model version any more. `examples/hello-sdlc/README.md` no longer tells you to install a companion. The new
  `tests/test_readme_first_run.py` checks that every `/agrim-*` skill, repo script and
  `/agrim-init` flag the README names exists, and that the floor never exceeds the shipped version.
  Measured: a clean-HOME install from a local-path marketplace (`claude plugin marketplace add`,
  `claude plugin install sigma@sigma`, Claude Code 2.1.284) and the `/agrim-init` script gestures
  in a fresh repository. The Codex install lines follow Codex's published plugin CLI and were not
  run here.

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
  `gh`, where it used to say `gh auth login`. Every local git call is time-limited by
  `SIGMA_WATCH_CALL_TIMEOUT` (default 120s); the three network calls (`git ls-remote`,
  `gh auth status`, `gh api users/<owner>`) by the smaller of that and 15s, so a dead host reads
  `CANNOT VERIFY (timed out)` quickly. A call that runs over has its whole process tree killed
  (the Windows `taskkill` and the drain after it are bounded too). The SessionStart wizard
  (`cheap_only`) never runs `git ls-remote` or the owner lookup: with an unreachable ssh remote it
  used to stall about 75s in review; the test with a hanging `ls-remote` stub now measures 0.41s.
  Those two rows say "not checked here; run /agrim-doctor" and are never shown as a pass. A fresh
  `git init` with no commit still gets its remote checked and the DECISION printed. `gh auth
  status` reads the active account only (`--active`, with a fallback for older gh), so a stale
  second account no longer fails a valid one. A GitLab or Bitbucket remote gets "gh only supports
  GitHub hosts" and the local-only option, not `gh auth login -h gitlab.com`; a remote URL
  that cannot be parsed is not assumed to be github.com. Credentials in a remote URL
  (`user:token@`) are removed from any text shown. The SSO link names the real host (GitHub
  Enterprise too). `brew install` is suggested only when `brew` is on PATH. The wording now says
  that only opening a PR needs `gh`, and pushing works without it. Nothing prompts. The test suite now also guards `subprocess.Popen` against live `gh`
  calls, and child processes get an empty gh config. An ssh remote whose host is an
  `~/.ssh/config` alias (`git@github-work:o/r.git` with `Host github-work` -> `HostName
  github.com`, the common multi-account setup) is resolved through `ssh -G <host>` (local, no
  connection, at most 5s) and gh is asked about the real host; `ssh.github.com` (ssh over 443)
  reads as github.com. An alias nothing can resolve is `CANNOT VERIFY`, never a FAIL and never
  `gh auth login -h <alias>` (gh cannot log in to an alias), and a `(cannot verify)` row is never a
  SessionStart wizard step. A `ghu_` (GitHub App user) token is `CANNOT VERIFY` like other
  non-classic tokens, not "no scopes". A remote URL with more than two path segments (Bitbucket
  Server `/scm/o/r.git`, a GitLab subgroup, Azure DevOps) is treated as not GitHub and gets the
  DECISION. With `gh` absent and no `brew`/`winget`, the DECISION points at
  https://cli.github.com instead of "the commands above".

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

- Feature-branch rebase upkeep no longer deletes branch content when the base holds a revert of the
  branch's own commits (#144). Before it pushes, upkeep now compares the branch tip's tree with the
  replayed tree. If any tracked path would disappear, or would be rolled back to a version the
  branch's own history already moved past (a reverted edit, or an undone rename), it refuses with
  the new `would-drop` outcome and pushes nothing. Base renames and ordinary base edits are allowed,
  and the branch's own deletions never count. The pick line says `was NOT rebased` and names the
  paths, a tracked issue is filed, and `/agrim-doctor` shows the unit as blocked until a clean pass
  clears it (not while `rebase_upkeep` is off or the unit is closed). `feature_rebase.py upkeep`
  exits 1 on it, and `rebase_brief.py rebase` runs the same check before its own force-push. The
  trade-offs: a plain upstream deletion, a move that rewrites past rename similarity, or a base
  reverting its own older change to a file the branch carries is refused the same way; a partial
  revert merged with other changes is not seen, and neither is a full base revert of a file the
  branch kept editing afterwards (the replay yields a version that never existed). The history read
  counts versions created by merge commits and the root commit, ignores chmod-only changes, pins its
  own git config so a user's `log.showRoot`/`log.diffMerges`/colour settings cannot switch it off,
  decodes paths as UTF-8, and fails closed after `SIGMA_REBASE_GUARD_TIMEOUT` seconds (default 120).
  The `agrim-rebase` skill's single push chokepoint, `rebase_brief.push_branch`, runs the same check
  against the commit the lease would overwrite, so `rebase_brief.py rebase`, Slack `--rebase`, the
  conflict walker's final push and its manual-recovery push all refuse a push that would lose
  content, naming the paths; only a path the walk resolved by deletion is that human's decision. See
  `docs/branching-model.md` §3b and §15.
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

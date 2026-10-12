# D7: the refusals a first-run user can hit, each triggered and checked for its fix (f627d06a)

Plugin files exported from origin/main at f627d06a (`git archive f627d06a .claude-plugin skills hooks`),
run on macOS (Darwin 25.6) with the system `python3` (3.9) and git from the host. Every scenario below
was run for real in a fresh temporary repository and its exit code and output were captured; nothing
was read off the source. Offline: no network, no GitHub, no model. The scenarios that need "no gh" ran
with a PATH holding only git, python3 and make.

**What this is not.** It is not every message the code can print. The commands on the onboarding path
are `/sigma-init` (`init_flow.py`, `sdlc_init.py`, `preflight.py`, `verify_detect.py`), `/sigma-doctor`
(`doctor.py`), `loop.py start|next|verify|record|agent-start` and `work.py start|commit|pr`. A static
scan of those files for message strings next to `REFUSED`/`refus`, or next to a `return`/`exit` of 2 or
more, finds about 180 candidate sites (init 41, doctor 13, loop 60, work 67). Many are fragments of one
multi-part message, and `loop.py` and `work.py` hold sites for merge, upkeep and features that a first
run does not reach. They were not triaged one by one. The 45 scenarios below are the ones a first run
can plausibly reach, chosen by reading the README Quickstart and the first-run live drills
(`live-onboarding-2026-10-12.md`); a refusal that needs a state this list did not build is not here.
Not covered: the github-mode `work.py pr|merge` refusals on a live board (the onboarding control
`docs/onboarding-control.md` exercises the review-gate and done-means-merged refusals against a fake
`gh`), the refusals only Windows, Linux, Codex or Cursor produce, and the model-side refusals the live
runs hit (listed in the live drill file `live-onboarding-2026-10-12.md`, not repeated here).

## How "prints its fix" was checked

Mechanically first: the full captured output (not only the line shown) must contain a backticked
command, a `--flag`, a `/sigma-...` command, "re-run", "Fix", "install it" or be a usage line. A Python
traceback never counts. That is a weak test (a backtick can name the problem), so every row was then
read by hand; the "note" column records each place the reading disagreed with the mechanical result.
Result: 45 scenarios, 3 that are not refusals at all (exit 0), 1 that is a refusal with a misleading
fix, 3 that print no fix and 1 that names a config key only. Each became an issue labelled
`launch:next`, none was dropped, except W5, which is not a refusal and is deliberately unfiled (see its row):

- #1245: `loop.py record <goal> <unknown-status>` exits 0 and parks the goal silently (L9).
- #1246: refusals that print no fix: `work.py pr` without gh (W9), `work.py start` without a base
  branch (W2), a missing `/sigma-init` target directory (I2), and `work is off` (W4).
- #1247: a goal that does not exist is accepted by `work.py start` (W3) and misreported by
  `loop.py record ... done` (L8).

Control, and its limit: the mechanical check returned "no fix" for the four outputs the reading
found to lack one or to name only a config key (I2, W2, W4, W9), so it is not blind to the failure it
looks for. The individual clauses of its pattern were NOT each broken and seen to fail, and the check
is not a committed test; the scripts that ran this were scratch files and are not in the repository.
The by-hand reading is the system of record.

## The scenarios

Exit codes are as the script returned them. "UNJUDGED" would mark a row neither check could place; there are none.

| id | command that triggers it | exit | the refusal line (verbatim; `<tmp>` and `<plugin>` are redactions of local paths) | prints its fix | note |
|---|---|---|---|---|---|
| I1 | init_flow.py <dir> in a directory that is not a git repository | 2 | `sigma-init: REFUSED - nothing written. The loop needs a git repository (a worktree and a branch per goal are cut from it).` | fix |  |
| I2 | init_flow.py <missing dir> | 2 | `sigma-init: target directory does not exist: <tmp>/i2/nope` | NO FIX | prints only the missing path (#1246) |
| I3 | init_flow.py <dir> --mode github, origin is not a GitHub remote | 2 | `sigma-init: REFUSED - github mode needs a repository, and `origin` is not a GitHub remote. Re-run with --repo OWNER/NAME (or add the GitHub remote first). Nothing was written.` | fix |  |
| I4 | init_flow.py <dir> --bogus (unknown flag) | 2 | `sigma-init: unknown option --bogus` | fix |  |
| I5 | init_flow.py <dir> with a malformed .sdlc/config.json | 2 | `sigma-init: REFUSED - <tmp>/i5/.sdlc/config.json is not valid JSON (Expecting property name enclosed in double quotes: line 1 column 2 (char 1); truncated or hand-edited?). Nothing was written and the file i...` | fix |  |
| I6 | init_flow.py <dir> --mode local-goals with no remote and no --local-only (preflight blocks) | 0 | `sigma-init: preflight - 1 problem(s) the loop would otherwise hit at the first goal:` | fix | exit 0 with a failing preflight section: the failed check and its fix are printed |
| I7 | init_flow.py <dir> --mode bogus | 2 | `sigma-init: --mode takes one of: local-goals \| github` | fix |  |
| I8 | init_flow.py <dir> --verify 9:aaaaaaaaaaaa (no such candidate) | 1 | `verify_detect: REFUSED -- no candidate 9 -- the repository changed since the report, or the number is wrong (it has 1 now); nothing stored. Re-run /sigma-init (or `detect`) and confirm from the new report` | fix | the verify step prints the refusal and what to re-run |
| I9 | sdlc_init.py <dir> in a directory that is not a git repository | 2 | `sigma-init: REFUSED - nothing written. The loop needs a git repository (a worktree and a branch per goal are cut from it).` | fix |  |
| I10 | preflight.py check <dir> in a repository with no remote | 1 | `sigma: preflight - 1 problem(s) the loop would otherwise hit at the first goal:` | fix |  |
| I11 | preflight.py check <dir> where origin has no base branch | 1 | `sigma: preflight - 2 problem(s) the loop would otherwise hit at the first goal:` | fix |  |
| I12 | preflight.py <bogus verb> | 2 | `usage: preflight.py check [repo_root] [--sdlc <dir>] \| local-only <sdlc_dir> \| use-remote <sdlc_dir> <remote>` | fix |  |
| I13 | preflight.py check <dir> where the directory is not a git repository | 1 | `sigma: preflight - 1 problem(s) the loop would otherwise hit at the first goal:` | fix |  |
| I14 | init_flow.py <dir> --mode github --repo acme/x with the gh CLI not installed | 1 | `sigma-init: preflight - 1 problem(s) the loop would otherwise hit at the first goal:` | fix | the failed check says to install gh from its URL and re-run |
| I15 | init_flow.py <dir> --mode github --repo notaslug | 2 | `sigma-init: --repo takes OWNER/NAME (e.g. acme/app), not a URL, a path, or a name ending .git` | fix |  |
| V1 | verify_detect.py confirm .sdlc (missing <n> <id>) | 2 | `usage: verify_detect.py detect [repo_root] \| confirm <sdlc_dir> <n> <id> \| set <sdlc_dir> (--command-file <file> \| - \| <command>) \| decline <sdlc_dir>` | fix |  |
| V2 | verify_detect.py confirm .sdlc 1 zzzz (bad id) | 2 | `verify_detect: REFUSED -- zzzz is not a candidate id (12 lowercase hex digits, printed beside each candidate)` | prose fix | says what an id is (12 lowercase hex digits printed beside each candidate); no command |
| V3 | verify_detect.py confirm .sdlc 9 aaaaaaaaaaaa (no such candidate) | 2 | `verify_detect: REFUSED -- no candidate 9 -- the repository changed since the report, or the number is wrong (it has 1 now); nothing stored. Re-run /sigma-init (or `detect`) and confirm from the new report` | fix |  |
| V4 | verify_detect.py confirm on a directory with no config.json | 2 | `verify_detect: no config.json under <tmp>/v4/.sdlc -- run /sigma-init first` | fix |  |
| V5 | verify_detect.py set .sdlc '' (empty command) | 2 | `verify_detect: REFUSED -- an empty command would refuse every `done`; use `decline` to turn enforce off instead` | fix |  |
| V6 | verify_detect.py set .sdlc with a newline in the command | 2 | `verify_detect: REFUSED -- the command contains a newline or control character; a verify command is one visible line` | prose fix | states the rule (a verify command is one visible line); no command |
| V7 | verify_detect.py with no verb | 2 | `usage: verify_detect.py detect [repo_root] \| confirm <sdlc_dir> <n> <id> \| set <sdlc_dir> (--command-file <file> \| - \| <command>) \| decline <sdlc_dir>` | fix |  |
| D1 | doctor.py check .sdlc in a repository never initialised | 0 | `[MISSING] project layer -> run /sigma-init to scaffold .sdlc/` | fix | a report, exit 0: each [MISSING] row ends with a fix |
| D2 | doctor.py <bogus verb> | 2 | `usage: doctor.py check [sdlc_dir] \| features [sdlc_dir] \| hygiene [sdlc_dir] [repo_root]` | fix |  |
| D3 | doctor.py check .sdlc with enforce ON and an empty verify.command | 0 | `[MISSING] verify command present (enforce is on) -> verify.enforce is on but no verify.command (and no goal sets verify_command) — every `done` is refused. Fix: python3 <sigma>/skills/sigma-init/scripts/veri...` | fix | a report, exit 0: the [MISSING] row ends with a fix |
| L1 | loop.py start <dir with no .sdlc> | 2 | `loop.py: no config.json under <tmp>/l1/.sdlc — this .sdlc directory was never initialized. Run /sigma-init to scaffold it (or /sigma-setup to adopt Sigma into an existing repo).` | fix |  |
| L2 | loop.py next <dir with no .sdlc> | 2 | `loop.py: no config.json under <tmp>/l2/.sdlc — this .sdlc directory was never initialized. Run /sigma-init to scaffold it (or /sigma-setup to adopt Sigma into an existing repo).` | fix |  |
| L3 | loop.py <bogus verb> | 2 | `usage: loop.py start <dir> [--session-pid PID] \| start-run <dir> [--session-pid PID] \| next <dir> [--skip a,b] [--feature NAME] [--session-pid PID] \| next-batch <dir> [--skip a,b] [--feature NAME] [--session...` | fix |  |
| L4 | loop.py record .sdlc <goal> done with verify.enforce ON and no command | 4 | `REFUSED: no verify command declared for this goal (config verify.enforce is on, verify.command is empty, and the goal has no `verify_command`) — set one: python3 <installed-sigma>/skills/sigma-init/scripts/v...` | fix |  |
| L5 | loop.py record .sdlc <goal> done without a recorded verify pass (enforce ON) | 4 | `REFUSED: no verify evidence for this goal — run `loop.py verify <tmp>/l5/.sdlc <goal>` first (config verify.enforce is on)` | fix |  |
| L6 | loop.py verify .sdlc <goal> with a configured command not yet trusted | 2 | `loop.py verify: REFUSED: repository-configured shell command requires explicit operator trust; run `git -C <trusted-project> config --local sigma.allowRepositoryShellCommands true` after inspecting the project` | fix |  |
| L7 | loop.py record .sdlc <goal> done with no phase records | 4 | `REFUSED: the SDLC phase record is incomplete (README: every goal runs its seven phases):` | fix |  |
| L8 | loop.py record .sdlc 9999 done (no such goal) | 4 | `REFUSED: the SDLC phase record is incomplete (README: every goal runs its seven phases):` | misleading fix | the fix printed is for phase records; the real problem is that the goal does not exist (#1247) |
| L9 | loop.py record .sdlc <goal> bogus-status | 0 | (no output) | NOT A REFUSAL | exit 0, no output, the goal is now `status: parked` (#1245) |
| L10 | loop.py verify .sdlc <goal> with no verify command | 3 | `NO-COMMAND (set goal frontmatter `verify_command` or config `verify.command`; set one: python3 <installed-sigma>/skills/sigma-init/scripts/verify_detect.py detect . lists candidates with ids, `... confirm .s...` | fix |  |
| L11 | loop.py agent-start .sdlc <goal> (missing --pid) | 2 | `loop.py agent-start: --pid is required` | fix |  |
| W1 | work.py start .sdlc <goal> with no remote configured | 1 | `[FAIL] git remote 'origin': no remote named 'origin' (this repository has no remote at all)` | fix |  |
| W2 | work.py start .sdlc <goal> where origin has no base branch | 1 | `work: git fetch origin main: fatal: couldn't find remote ref main — base 'main' came from the current HEAD` | NO FIX | git's error only; the fix (`git push -u origin main`) is printed by preflight.py, not here (#1246) |
| W3 | work.py start .sdlc 9999 (no such goal) | 0 | `worktree <tmp>/w3/.sdlc/work/9999 on sdlc/9999 (cut from origin/main)` | NOT A REFUSAL | exit 0, a worktree and branch were cut for a goal that does not exist (#1247) |
| W4 | work.py start .sdlc <goal> with work.enabled false (local-only) | 1 | `work is off (config: "work": {"enabled": true})` | weak fix | names the config key, not the command that sets it (#1246) |
| W5 | work.py start .sdlc <goal> in a repository whose root checkout has tracked uncommitted changes (the printed path is the goal worktree it cut under that root) | 0 | `worktree <tmp>/w5/.sdlc/work/0001-x on sdlc/0001-x (cut from origin/main)` | NOT A REFUSAL | a dirty root checkout did not stop `work.py start`; unfiled, because cutting the worktree from origin/main does not depend on the root's dirt and no defect was shown |
| W6 | work.py <bogus verb> | 2 | `usage: work.py start\|commit\|pr\|rebase\|post-review\|merge\|finish <sdlc_dir> <goal>` | fix |  |
| W7 | work.py start .sdlc (missing goal) | 2 | `usage: work.py start\|commit\|pr\|rebase\|post-review\|merge\|finish <sdlc_dir> <goal>` | fix |  |
| W8 | work.py commit .sdlc <goal> --message x with a secret-shaped file (.env) in the goal worktree | 0 | `REFUSED — `git add -A` staged 1 path(s) or added-content match(es) requiring attention in this goal's worktree. Nothing was committed and those index entries were reset; every file is still on disk, untouched.` | fix | exit 0 by design: the commit is withheld and the path is listed with two fixes |
| W9 | work.py pr .sdlc <goal> with the gh CLI not installed | 1 | `work: [Errno 2] No such file or directory: 'gh'` | NO FIX | raw `[Errno 2] No such file or directory: 'gh'` (#1246) |

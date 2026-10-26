# Plan review: #2066, revision 2 (P4 PLAN-REVIEW)

Reviewed by: dispatched subagent (author-blind). The brief reached me directly, so I am the resolved reviewer; I did not
re-resolve or dispatch again.

Inputs reviewed, at HEAD `dcfde5f` of this worktree:

| File | sha256 |
| --- | --- |
| `.sdlc/plans/2066.md` | `a97029d3430086136bcb301e2f26bcbd7bd3ebcc27af4d9ce3a3dd1144aa267b` |
| `.sdlc/research/2066.md` | `d902ffbb5e9db714a3c00f39f29c75af6c66c4b1d9d69d67aee047ffa10faeab` |
| `.sdlc/reviews/security-review-2066-plan.md` | `6030a6305409749c99e50bcb4ebe5f89f5d59584ddc430d7baac5521018d688c` |

Strategic alignment: `north_star.py` printed `absent <main checkout>/.sdlc/context/north-star.md`, so the
check was skipped. I used AGENTS.md's five properties instead. `zones.py check --goal 2066 --plan .sdlc/plans/2066.md`
printed `verdict: unknown` (no readable work record), and an unknown verdict never rejects.

## Verdict: REVISE (FIX-FIRST)

The core design holds up against the code:
- the approval is a new predicate used on the verify path only (`verify`, `red`);
- it must equal the value of EVERY declared command, and the identity is part of the hashed input;
- `repository_shell_commands_allowed` and the other 12 sinks are unchanged;
- the git-local grant is untouched.

I found no fail-open in the comparison itself.

The new `repository-sourced` clause is where it fails. As specified it fails open, or is evaded declaratively, in four
ways I reproduced in temp repos (B2 to B4), and its closed list is never written down (B1). Two controls the plan relies
on cannot go red (B5, B6). Each needs an edit to the plan, and editing the plan voids any approval, so the right verdict
is a narrow revision and a fresh review, not refinements carried into Implement.

Fewer than half the findings are structural, so patch the plan rather than regenerate it.

## Blocking findings

**B1. `HOST_ENV_FILES` is never enumerated.**
- What the plan says: Files says only "`HOST_ENV_FILES` (the closed list)". Exposure names VS Code
  `terminal.integrated.env.*` and a SessionStart hook writing `$CLAUDE_ENV_FILE`. S2's list contains neither of their
  files (`.vscode/settings.json`, and the hook script).
- What the target repository tracks: several `.claude/` files besides `settings.json`, among them a paste-only
  setup script (its README says it is not run by the repository; it is pasted into the environment's setup-script
  box).
- Change: write the list into the plan, one entry per line with the host each serves.
- Decisions to record in the plan:
  - `.claude/settings.json` plus `.claude/hooks/**`, or all of `.claude/**`. The wider glob also refuses the target
    repository the day an operator records the value in that paste-only script.
  - `.vscode/settings.json`: add it.
  - `.github/workflows/**`: a CI job's env is repository-controlled, and a same-repository PR's workflow edits apply.
    Either add it to the list, or say in the docs and TM-04 that CI is not a supported place for this approval.
- Parametrise test 9 over every entry.

**B2. A subdirectory path fails open: pathspecs resolve relative to `git -C <path>`.**
- Measured, file at the repository top:

  | Command | Exit code |
  | --- | --- |
  | `git -C <repo> grep -l -F NAME -- .claude/settings.json` | 0 (found) |
  | `git -C <repo>/svc/x grep ... -- .claude/settings.json` | 1 (not found) |
  | the same from `<repo>/svc/x/.sdlc` | 1 (not found) |
  | `-- ':(top).claude/settings.json'` from the subdirectory | 0 (found) |

- Places that pass a non-top path:
  - `work.project_root(sdlc_dir)` is the parent of `.sdlc` (`work.py:1481-1482`). That is a subdirectory for a
    monorepo `.sdlc`, and it is the `verify` root when there is no worktree (`work.py:1819`).
  - the doctor's `project` (`doctor.py:4459`);
  - init's `repo` (`init_flow.py:505`);
  - the existing tests' habit of passing the `.sdlc` directory itself (`tests/test_shell_policy.py:47,57`).
- Change:
  - put `:(top)` magic on every pathspec (or resolve `--show-toplevel` first);
  - add a case to test 9 that passes the `.sdlc` subdirectory;
  - add a control: drop `:(top)` and see the test go red.

**B3. A failed `git grep` must refuse, and the plan never says so.**
- `git grep` exits 0 on a match and 1 on none. Anything else means it did not look: 128 outside a repository
  (measured), a timeout, or an `OSError`.
- The failure table has no row for it, and the controls table has no control for it. An implementation that reads
  "no stdout" as clean fails open whenever the grep fails. The same applies to the main-checkout half when its
  directory is not a working tree (see R3).
- Change:
  - add the row: grep exit code other than 0 or 1, or an exception, refuses (reason `error`);
  - add a test that injects a failing runner;
  - add a control: treat any non-zero exit as clean, and see the test go red.

**B4. The literal-name grep is evaded declaratively, outside the plan's own stated residual.**
- Measured:
  - A tracked `.claude/settings.json` whose bytes are `{"env": {"SIGMA_TRUSTED_VERIFY_SHA256": "deadbeef"}}`
    (a literal backslash-u) gives `git grep -l -F SIGMA_TRUSTED_VERIFY_SHA256` exit 1. `json.load` decodes the key to
    `SIGMA_TRUSTED_VERIFY_SHA256`.
  - A TOML quoted key decodes the same way (`tomllib.loads`, measured), which covers `mise.toml` and
    `.codex/config.toml`.
  - A tracked symlink `.claude/settings.json -> ../config/claude.json` (index mode 120000): `git grep` searches the link
    text, exits 1, while the file it points to names the variable.
- Why the residual does not cover this: the plan excuses only names "built at run time", on the grounds that a host
  running repository hooks has already granted code execution. These two need no hook. S2's motivating case is
  exactly an organisation that disables project hooks but still has `env` applied.
- Change:
  - list tracked entries with `git ls-files -s -z -- <:(top) pathspecs>`;
  - refuse any listed entry whose mode is 120000 or 160000;
  - read each regular file's bytes in Python, decode `\uXXXX` and `\UXXXXXXXX` escapes, then search for the name;
  - add tests for the escaped key and for the symlink, each with a control.
- Cost: one `ls-files` subprocess plus reads of a few small files, about the same as the `git grep` it replaces.

**B5. The control "read the variable through `legacy.getenv` → test 12" cannot go red.**
- `legacy.getenv` falls back to the retired prefix only for names in `FALLBACK_ENV` (`legacy.py:117-123`). For a
  `POST_RENAME_ENV` name it returns `env.get(name, default)` (`legacy.py:124`), which is identical to the direct read.
- The definition of done requires every control to go red, so as written this row either cannot be met or gets
  recorded falsely.
- Change:
  - make the control "register the name in `FALLBACK_ENV` AND read it via `legacy.getenv`";
  - restore S9's second assertion, which test 12 dropped: an empty Sigma value next to a matching retired-prefix value
    gives `empty`.

**B6. The S4 fix is not run on the gesture the docs give, and test 24 cannot fail.**
- Why test 24 cannot fail: it runs one fixed invocation by path. Test 28 runs the documented text but plants nothing.
  The control "verify-approval run via `python3 -c`" changes neither the code nor a test that runs the docs. AGENTS.md:
  "Copy the gesture out of the docs, run THAT, and see it red."
- Running by path is not enough. Measured with a by-path script and a planted `hashlib.py` in the current directory:

  | Environment | Result |
  | --- | --- |
  | none | stdlib `hashlib` imported, no sentinel |
  | `PYTHONPATH=:` (an empty entry, as an unset-variable append leaves) | planted module ran, sentinel written |
  | `PYTHONPATH=.` | planted module ran, sentinel written |
  | `python3 -I` with `PYTHONPATH=.` | planted module did not run |

  `python3 -I .../doctor.py --help` exits 0, so the doctor works under `-I`.
- Change:
  - document the gesture as `python3 -I <installed-sigma>/skills/sigma-doctor/scripts/doctor.py verify-approval .sdlc`;
  - make test 28 the S4 test: extract the gesture from `docs/cloud-sessions.md`, run it in a temp project holding a
    planted `json.py` and `hashlib.py`, with `PYTHONPATH=:` in its environment, and assert no sentinel and the
    known-answer value (R2);
  - control: drop `-I` from the doc and see test 28 go red.

## Non-blocking refinements

**R1. The identity pattern rejects `_`.**
- GitHub's naming rule allows `_` in repository names (from GitHub's rule, not exercised live).
- The pattern is ported from `feature_sync._SLUG_RE` (`feature_sync.py:244`). Under it, `owner/my_repo` is
  `no-identity` forever, so the approval can never work for such a repository.
- Test 25 is affected too: pytest temp paths contain `_`. The local-path `origin` from `_committed_verify_clone`
  (`tests/test_init_flow.py:1243-1258`) parses to something like `test_..._tr0/repo`, which the pattern rejects. The
  test must set a GitHub-shaped `origin`.
- Change: allow `_` in both segments (the identity is only hashed), and add `owner/my_repo` to test 7.

**R2. No known-answer test.**
- Every test derives its expected value from `approval_value` itself, so a change to the domain prefix or separator
  stays green.
- Meanwhile every value an operator pasted stops matching, and the whole fleet refuses (RESILIENCY).
- Change: hard-code one hex digest in test 1, computed in the test with `hashlib`. Control: alter the prefix and see
  test 1 go red.

**R3. Finding the main checkout.**
- `git rev-parse --git-common-dir` prints the relative `.git` in the main worktree and an absolute path in a linked one
  (measured). `north_star.py:99` already documents this trap. Use `--path-format=absolute`, as `bounded_run.py:445` and
  `decision_store.py:541` do, or take the first entry of `git worktree list --porcelain`.
- A bare repository with worktrees has no main working tree. Skip that half rather than refuse forever.

**R4. A third tree is not checked.**
- `project_root(sdlc_dir)` can itself be a linked worktree: the Claude Code desktop layout `.claude/worktrees/<name>`,
  which is how this session runs (see `git worktree list`). The host applied THAT worktree's `.claude/settings.json`,
  which can differ from both the goal worktree and the main checkout.
- Change: check the project root too (deduplicated), or name this as a residual in the docs and TM-04.

**R5. Evaluate once.**
- `verify_refusal_message(path, commands)` runs the decision a second time. A refusal then pays the git calls twice:
  about 108 ms median for the five calls, 183 ms worst, in a tiny temp repo on this shared machine (n=20). The printed
  reason can also differ from the one that decided.
- Change: one call returns `(source, reason, detail)`, and the message is built from it.
- Two more things to state:
  - when the variable is unset, the message stays byte-identical to `refusal_message()`;
  - `environment_approval` returns before any git call when the variable is unset. Add a test that counts the calls.

**R6. Doctor details.**
- The passing-row advisory cannot be built with `_chk`, which blanks `fix` when the row is OK (`doctor.py:197`).
  Hand-build that row.
- Keep the failing fix's pinned phrases:
  - `config --local sigma.allowRepositoryShellCommands true` and `loop.py verify` (`tests/test_doctor.py:1760`);
  - `Git worktree` (`tests/test_doctor.py:1797`).
- Wrap `_load_loop_script` in `try`. Its docstring says "Never raises", but its body has no `try`
  (`doctor.py:1113-1117`).
- The cited precedent `doctor.py:4482` is stale: `landing_preflight` is loaded at `doctor.py:1190` and `4507`.

**R7. Session-start cost.**
- `doctor.check(cheap_only=True)` runs from the unconditional SessionStart hook (`setup_wizard.py:34-54`). Once the
  variable is set, every cloud session start pays the new git calls.
- Checked: the wizard's allow-list (`setup_wizard.py:72-111`) excludes the trust row, so the value is not surfaced at
  session start.
- Change: the definition of done should state this cost with its measurement.

**R8. Liveness of a refusal.**
- The verify refusal writes `exit=2` only, to both the ledger and the action log (`loop.py:6537-6538`). That is
  indistinguishable from a missing git-local grant.
- After `verify.command` changes on the base, every cloud goal refuses `mismatch`, and only stderr says why.
- Change: add a fixed reason token, never the value, to those two events. The action log whitelists fields per kind
  (`actionlog.py:302`), so the field is added there. Or file it as a tracked follow-up.

**R9. Changing `verify.command` is a hard cutover.**
- Only one value is accepted per environment. Parallel containers cloned before and after the merge need different
  values, and the issue's premise is many containers in parallel.
- Change: document the cutover sequence and the re-verify after the value is updated, or allow a strictly parsed
  two-value form for the overlap (owner decision).

**R10. `red`'s error reason.**
- Collapsing an unreadable config or acceptance record into `[]` reports `no-command` when a command exists.
- Change: report it as `error` ("could not read the declared commands"), still exit 2, and leave the git-local path
  unchanged.
- Add S5's local-frontmatter case to test 17. S5 asked for both setups; the plan has only the acceptance case.

**R11. Pin the whole blast radius, not one sink.**
- Test 11 covers one sink (pipeline).
- Change: add an AST or grep ratchet that `verify_trust_source` and `environment_approval` are referenced only in
  `loop.py` (`red`, `verify`), `doctor.py` and `init_flow.py`. Control: call one from `pipeline.py` and see it go red.

**R12. Say plainly that the doctor hands the value to agents.**
- `/sigma-doctor` is agent-run, and its failing row prints the exact value to set (which the brief requires).
- So S8 and the prose ratchet are friction, not a control.
- `docs/cloud-sessions.md` is linked for agents from `AGENTS.md:175`. That is S10's placement concern, and the plan
  does not address it.
- Change: say this in Exposure and in TM-04.

**R13. Small items.**
- Build the retired prefix from fragments (`tests/test_no_private_names.py`; precedent `tests/conftest.py:270`).
- `_no_host_legacy_env` (`tests/conftest.py:273-279`) already deletes every retired-prefix variable, so the new
  fixture needs only the Sigma name.
- Split the veto control into its two clauses: `false`, and a value git cannot read as a boolean (rc 128, measured).
- Put `{workers}` in the `verify-approval` test's command, so raw-string hashing is pinned (S11).
- Never print the remote URL on `no-identity`: `git remote get-url` prints `https://x-access-token:...@github.com/...`
  verbatim (measured).
- Keep the new code Python 3.9-compatible; the docs record 3.9.6 runs.
- Say why the identity ignores `work.remote`, when the doctor's existing slug read uses `work.remote or origin`
  (`doctor.py:1192-1198`).
- Add a parity test of the identity port against `feature_sync.slug_from_remote_url`, lower-cased.

## Forensic verification

### Correct

**Blast radius.** Only `loop.py:6392` (`red`) and `loop.py:6535` (`verify`) change. These 12 sites keep
`repository_shell_commands_allowed`:
- `autowatch.py:885` and `autowatch.py:1046`;
- `backlog_check.py:812`;
- `bounded_run.py:470`;
- `loop.py:4750`;
- `pipeline.py:68`;
- `reviewer.py:1066`;
- `work.py:4078`;
- `verify_merge.py:122`;
- `init_flow.py:506`, until its one-line swap.

The resource-guarded verify calls `bounded.run_group` directly (`resource_verify.py:254`), not `run_verify`, so there
is no second git-local gate on the verify path. `kg.py:412-415` and `risk-detect.sh:139` are separate copies and stay
unchanged. `work.py pr` runs tests only through the opt-in sealed gate (`work.py:4081`).

**The verify path in `loop.py`.**
- `commands` is built at `loop.py:6466` from `state.declared_verify_commands` (`state.py:1619-1644`): repository command
  first, then the acceptance or frontmatter command, deduplicated; local frontmatter replaces the repository command.
- `load_config` is called outside the `try` at `loop.py:6463`. The CLI turns `ConfigMissing` and `ConfigInvalid` into
  exit 2 (`loop.py:7587-7589`), so "exits non-zero with nothing run" holds.
- An invalid acceptance record exits 2 before the gate (`loop.py:6467-6470`).
- The evidence is written at `loop.py:6645`, and no test pins its key set.

**Tests the plan says keep working.**
- Monkeypatches of `loop.shell_policy.repository_shell_commands_allowed` (`test_d7_refusal_fixes_work.py:239`,
  `test_resource_mode_resolver_1759.py:84,103`, `test_work.py:7074`) keep working, because the module global is looked
  up at call time.
- Substring assertions keep passing under "`refusal_message()` plus one clause": `tests/test_test_first_gate.py:531`
  and `tests/test_init_flow.py:1277`.

**`shell_policy` must stay standard-library only.** `tests/unattended_support.py:77` copies only `shell_policy.py` and
`bounded_run.py` into a variant folder. Porting the slug parser (rather than loading `feature_sync`) and reading
`environ` directly respect that. Say it in the plan.

**Doctor.** `_verify_trusted` (`doctor.py:175-186`), the trust row (`doctor.py:4455-4470`), the #1513 printer
(`doctor.py:6966-6975`), `USAGE` (`doctor.py:6940`) and `main` (`doctor.py:6944`) are where the plan says.
`tests/test_script_help.py` only runs a top-level `--help` (`:155-157`, `:188-196`), so adding the subcommand to the
`USAGE` line is enough.

**Other sources the plan cites.**
- `init_flow._verify_trust_lines` (`init_flow.py:498-523`); `_sp` is loaded at `init_flow.py:124`.
- `POST_RENAME_ENV` (`legacy.py:53-59`); `test_legacy_compat.py:333-338` scans `skills/**` and `hooks/*`.
- `feature_sync._slug_from_url` (`feature_sync.py:541-564`).
- The Exposure citations: `red_green.py:89-92` and `red_green.py:184`, `flake_check.py:144`, `resource_verify.py:90-94`.

**Git behaviour (measured in temp repos).**
- `git grep` searches tracked files only: an untracked `.envrc` gives 1, a tracked one gives 0.
- `--type=bool`:

  | git-local value | Exit code | Prints |
  | --- | --- | --- |
  | unset | 1 | nothing |
  | `""` | 0 | `false` |
  | `yes`, `1`, `on` | 0 | `true` |
  | `maybe` | 128 | fatal error |
  | two values, `false` then `true` | 0 | `true` (last wins) |

- A linked worktree reads the common git config.
- `git remote get-url` with no `origin` exits 2. It applies `insteadOf` rewrites; `git config --get` does not.

**The target repository's acceptance records (recounted).** 214 records: 117 null, 62 equal to `bash scripts/verify.sh`, 34 with
their own command, and 1 my parser missed. That agrees with the research. Its `origin` names a
private repository of the same owner.

### Partially correct or stale

- "No case fails open": not as specified (B2, B3, B4).
- The doctor precedent at `doctor.py:4482` is stale (R6).
- "`shell_policy.py:30`": the `--local` read is at line 31.
- `_load_loop_script` "Never raises": it can raise, so the fallback needs a `try` (R6).
- The passing-row advisory is not possible through `_chk` (R6).
- Test 25 cannot pass with a temp-path `origin` (R1).

## Audit of the Controls table

These rows go red against the stated tests. Six of them only through the reason assertion, so tests 3 to 5 must assert
the reason, not just a refusal:
- `fullmatch` → `match`: test 3 (reason), test 4 (reason);
- hex class widened: test 3 (reason);
- `all` → `any`;
- `no-command` clause deleted: `[]`, and `[""]` by reason;
- `try` deleted;
- identity dropped;
- identity not case-folded;
- `repository-sourced` deleted;
- untracked files checked too;
- main-checkout half skipped;
- `repository_shell_commands_allowed` consulting the environment;
- `verify` gate wiring;
- `red` gate wiring;
- refusal prints the value;
- evidence `trust` omitted;
- doctor hint includes the command;
- doctor ignores `shell_policy`;
- init line reverted;
- prose ratchet;
- doc gesture subcommand changed;
- `legacy.py` registration removed.

These rows do not hold as written:
- read through `legacy.getenv`: cannot go red (B5);
- `verify-approval` via `python3 -c`: not a code or doc break that any test runs (B6);
- veto: one row for two clauses (R13);
- "that refusal test" for the conftest row: name the test.

Missing: B2, B3, B4 (two breaks: escaped key and symlink), R2, R5 (no git call when unset), R11.

## Unverified

- That Claude Code applies a committed `.claude/settings.json` `env` block, including under managed settings that turn
  project hooks off. This is documented host behaviour, not exercised here.
- That Claude Code's settings parser decodes `\u` escapes in keys. Any JSON parser must; not exercised against Claude
  Code.
- That GitHub allows `_` in repository names. Taken from GitHub's naming rule, not exercised live.
- The cloud clone's `origin` URL shape, and how the environment-variables panel delivers values.
- Whether Claude Code reads `.claude/settings.json` from the launch directory or the git top level (this affects R4).

## Scratch evidence

`<scratch>`
holds:
- `r` and `r5`: the tracked-filter, exit-code, binary and deleted-file grep cases;
- `r3`: the JSON-escape evasion;
- `r2`: the symlink evasion;
- `r4`: the subdirectory pathspec case;
- `m`: the linked-worktree, boolean-config, remote-URL and timing repo;
- `plant` and `tool`: the `PYTHONPATH` sentinel case.

None of them set `SIGMA_TRUSTED_VERIFY_SHA256` in any process environment.

---

# Revision 3 re-check

Plan sha256 `969dcfcd8c8a6d13d690beee1b2f642284d088680e07af09b8772f901583d8e0` (411 lines), against HEAD `dcfde5f` and
the scratch repos named above. I wrote nothing outside this file and set the variable in no process.

## Verdict: REVISE (narrow, one blocking item)

Revision 3 closes B1 to B6 as written (each checked below). One new blocking finding, N1, came out of trying to break
the clauses revision 3 adds. It also reaches the identity read, which revisions 2 and 3 share, so I missed it in the
first round.

N1 is a small, self-contained change with precedent in this repository. Fold it into the plan and re-review. The other
items below can ride into Implement as refinements without a plan edit.

## Blocking

**N1. Git's own environment variables can blind the `repository-sourced` check and rewrite the identity.** Both use
the very vector the plan defends against.

The vector: the plan's premise `[S2]` is that a repository can fill the process environment from committed declarative
files (a `.claude/settings.json` `env` block, for one). That block can set `GIT_*` variables as easily as it can set
the approval variable. Measured in the scratch repos:

| Variable | Effect on the plan's git calls | Result |
| --- | --- | --- |
| `GIT_INDEX_FILE=<nonexistent path>` | `git ls-files -s -z -- ':(top).claude/settings.json'` exits 0 with empty output | nothing is "tracked", the clause reads nothing, and a committed `.claude/settings.json` naming the variable passes |
| `GIT_WORK_TREE=<decoy dir>` | `rev-parse --show-toplevel` prints the decoy, while `ls-files` still lists the real index entry | a read joined to the toplevel opens the decoy's clean copy |
| `GIT_CONFIG_PARAMETERS="'url.https://github.com/approved/name.insteadof'='https://github.com/Owner/Repo'"` (or `GIT_CONFIG_COUNT`/`KEY_0`/`VALUE_0`) | `git remote get-url origin` prints `https://github.com/approved/name.git` | the identity becomes `approved/name` |
| `GIT_DIR=<another checkout's .git>` | `git -C <path> config --local --get remote.origin.url` returns the other checkout's URL; `--local --type=bool` returns its `true` | identity and veto both read another repository |

Why the identity rewrite is the worse case: repository B, opened in an environment whose operator approved repository
A, needs only a committed `env` block that never names the Sigma variable. That block passes `repository-sourced`, the
identity reads `a/a`, and B's own `verify.command` can be A's approved string. B's code then runs under A's approval.
That is the S1 threat the owner bound against.

Change, citing the precedent `bounded_run.py:106-108,121-123,466-468` (`LOCATION_VARS`, refused with
`[location-variable]`):
- At the start of `environment_approval`, after the unset check so that nothing spawns when unset, refuse when any of
  these is set:
  - `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`,
    `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_NAMESPACE`, `GIT_PREFIX`;
  - `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT`.
  Use a named reason (names only, never values). Duplicate the set in `shell_policy`; it must stay standard-library
  only and cannot load `bounded_run`.
- Read the identity with `git config --local --get-all remote.origin.url`, not `git remote get-url`. `--local` ignores
  `insteadOf` and global, system and env-injected config (measured: an injected `remote.origin.url` was ignored).
  More than one value gives `no-identity`, because `--get` silently returns the last value (measured) while
  `get-url` returns the first.
- Tests:
  - each variable class refuses: `GIT_INDEX_FILE`, `GIT_WORK_TREE`, `GIT_DIR`, `GIT_CONFIG_PARAMETERS`;
  - an `insteadOf` in a temp `GIT_CONFIG_GLOBAL` does not change the identity.
- Controls: drop the refusal and see the `GIT_INDEX_FILE` case go red; switch the identity read back to `get-url` and
  see the `insteadOf` case go red.

Out of scope, named as a follow-up: the same `GIT_DIR` redirect reaches the unchanged git-local read
(`shell_policy.py:29-36`). `bounded_run` refuses it for its own path; `repository_shell_commands_allowed` does not.

## B1 to B6, re-verified

- **B1, closed.** `HOST_ENV_PATHS` is now a table, entry by entry with its loader (plan lines 58-65).
  - Code-execution loaders (hooks, setup scripts, CI) are moved to named residuals with a reason (lines 75-82).
  - The narrowing from `.devcontainer/**` and `.env.*` is deliberate and covered by the generic "any other committed
    path" residual.
- **B2, closed in the pathspecs; the read path is not pinned (N2).**
  - `:(top)` is required (table header), with a subdirectory test and a control.
- **B3, closed for `ls-files`/`rev-parse`, with a test and a control.** The veto read needs its exit-code mapping
  spelled out (N4).
- **B4, closed for the stated forms.**
  - covered: `ls-files -s -z`, refusing `120000`/`160000` and non-regular working-tree paths, `\u` / `\U` / `\x`
    decoding, case-insensitive search;
  - the `\u`-key and symlink tests are present;
  - other clauses lack tests (N3).
- **B5, closed.** Registering the name in `FALLBACK_ENV` with `legacy.getenv` now drives test 13. Test 13 includes the
  empty-Sigma-plus-matching-retired case: `getenv` skips the empty value and returns the retired one, so the control
  goes red.
- **B6, closed.**
  - The gesture is `python3 -I ...` (line 142).
  - Test 29 runs the doc text with planted `json.py`/`hashlib.py` and `PYTHONPATH=:`.
  - The control drops `-I`. Measured in round 1: without `-I`, a planted module in the cwd ran under `PYTHONPATH=:`,
    and `doctor.py` works under `-I`.

## Non-blocking: new in revision 3 (carry into Implement)

**N2. The read path for a listed file is underspecified, and a natural mistake fails open on Claude Code users'
machines.**
- From a subdirectory, `git -C <subdir> ls-files -s -z -- ':(top).claude/settings.json'` prints
  `../../.claude/settings.json`, relative to the `-C` directory (measured). `--full-name` prints `.claude/settings.json`.
- The mistake: joining the relative form to the toplevel. For a project two levels deep (`<top>/svc/api/.sdlc`), that
  resolves to `<top>/../../.claude/settings.json`. For `/Users/<u>/Projects/<repo>` that is `/Users/<u>/.claude/settings.json`,
  the user-level Claude Code settings file that exists on every Claude Code user's machine. A clean file there passes
  while the tracked file goes unread.
- Test 10's subdirectory case does not catch this: in a pytest temp dir the wrong path is absent, so the read refuses,
  which is what the test expects.
- Change:
  - use `ls-files --full-name`, joined to `rev-parse --path-format=absolute --show-toplevel`;
  - refuse a resolved path outside the toplevel;
  - add a positive subdirectory case: a clean tracked file, approval granted;
  - add a decoy case: a clean file at the wrong-join location, approval refused because the tracked file names the
    variable.

**N3. Clauses with no test or control.** AGENTS.md: break each clause on its own.
- Missing test cases: a TOML `\U` key; a `\x` key; a gitlink; a working-tree path that is not a regular file; a file
  over 1 MiB; invalid UTF-8.
- Missing control row: "case-sensitive search → 10 (lower-case name)".
- Either add each case with its control, or drop the clause and its failure-table row (line 184). A claim no test can
  fail is decoration.

**N4. The veto's ordering and exit codes contradict test 8.**
- A non-Git directory gives exit 128 for `git config --local --type=bool` ("--local can only be used inside a git
  repository", measured). That is the same code as a value that is not a boolean. The veto runs at step 3, before
  identity at step 5. So a non-Git directory yields `vetoed` (or `error`), while test 8 asserts `no-identity`.
- Line 74's "any code but success refuses" would also refuse exit 1, which is unset: the normal path.
- Change:
  - run `rev-parse --path-format=absolute --show-toplevel --git-common-dir` first, which the toplevel and main checkout
    need anyway, with failure meaning `no-identity`;
  - then map the veto read: exit 0 `false` → `vetoed`; exit 1 → continue; exit 128 → `vetoed`; timeout or `OSError`
    → `error`.

**N5. Test 4's "no subprocess when unset" must call `environment_approval`, not `verify_decision`.** The latter
spawns the git-local read at step 1 by design.

## Non-blocking: dispositions to correct

- **R3 is not dispositioned.** The plan never says how the main checkout is found. Use `--path-format=absolute`, with
  `bounded_run.py:445` as precedent; `--git-common-dir` prints a relative `.git` in the main worktree (measured). Skip a
  bare main repository, which otherwise always gives `error`. It fails closed, so this is low severity, but it needs a
  line in the plan.
- **R13's identity-parity test was dropped without a word.** That is fine, because the identity now deliberately
  differs from `feature_sync._SLUG_RE` (it allows `_`). Record it as rejected, with that reason.
- **R4, partial: check the listed paths relative to the project root as well.** A `.sdlc` in a subfolder probably
  means the host was launched there, and its `<project>/.claude/settings.json` is not checked by `:(top)`. The plan
  lists "launch directory or git top" as unverified (line 402). Checking both costs one more pathspec set per root.
- **N6, B1 residuals: name the declarative indirections in the docs, since the list is now public.** mise's
  `env._.file`, and devcontainer `runArgs --env-file` or compose `env_file`, load a file outside the list without
  executing code.
- **`shell_policy.py:30` (plan line 110) is still off by one;** the read is at line 31.

## Confirmed

- R1: `\A[a-z0-9_.-]+/[a-z0-9_.-]+\Z`, with `.`/`..` refused, `owner/my_repo` tested, and test 26's origin GitHub-shaped.
- R2: known answer.
- R5: one evaluation; the unset message is byte-identical, with a control.
- R6: hand-built row, pinned phrases, wrapped loader, corrected cites `doctor.py:1190` and `:4507`.
- R7: measured cost in the definition of done.
- R8: follow-up, acceptable.
- R9: hard cutover documented. Two values are rejected on the brief's "several values ... refuse", which is the brief
  as written.
- R10: `None` gives `error`, and test 18 has the frontmatter case.
- R11: test 30.
- R12: Exposure lines 86-92.
- R13, the rest: fragments, `{workers}`, URL never printed, Python 3.9, the `work.remote` rationale (stated better than
  I put it).
- The `roots` parameter only adds coverage (default `()`), and `verify_decision` evaluates once. I found no fail-open in
  either.
- The 1 MiB, UTF-8 and case-insensitive clauses only widen refusal. None of them is a fail-open, only untested (N3).

---

# Revision 4 check

Plan sha256 `ca1177dc96f0defc2c36a05fb2085c535aa5e9b9689c85af5e9f5623af31c60d` (484 lines), HEAD `dcfde5f`.

## Verdict: APPROVE-WITH-CHANGES (SOUND-WITH-REFINEMENTS)

N1 is closed, and N3 to N6, R3, R4, R13 and the line cite are dispositioned correctly. Three changes remain:
- C1 is a fail-open in the B4 class;
- C2 is a pair of controls that cannot go red as their tests are written;
- C3 is a wrong-tree read.

None of them contradicts the plan text, so they can ride into Implement as refinements, each recorded with
`loop.py note`. Each corrected control must be seen red. If you would rather edit the plan, a short re-check suffices.

## N1: closed

- **Identity.** It is read with `config --local --get-all remote.origin.url`; several values give `no-identity`. Test 8
  covers two values and a local `insteadOf`; test 31 covers an `insteadOf` in `GIT_CONFIG_*` and in
  `GIT_CONFIG_GLOBAL`. Measured: unscrubbed, `GIT_CONFIG_PARAMETERS` carrying `insteadof` still leaves `--local
  --get-all` returning the real URL.
- **Location variables.** `location-variable` is checked at step 3, before anything spawns. Tests 31 and 32 cover it,
  and test 32 pins the set equal to `bounded_run.LOCATION_VARS`.
- **Scrub.** The git environment drops `GIT_*` and sets `GIT_CONFIG_NOSYSTEM=1` and `GIT_CONFIG_GLOBAL=<devnull>`. The
  precedent `feature_upkeep_launcher.py:694` is verified.
- **Scrub control: goes red, through one case only.** Measured: unscrubbed, `GIT_LITERAL_PATHSPECS=1` makes
  `ls-files -- ':(top).claude/settings.json'` list nothing, so test 31's pathspec case goes red. Its `GIT_CONFIG_*`
  cases cannot go red under this break, because `--local` defends them. Map them to the `get-url` control instead.

## Required changes

**C1. A tracked symlinked parent directory still evades `repository-sourced`.**
- Measured, in a repository whose index holds `.claude` as a symlink (`120000`) to `config/claude`, plus
  `config/claude/settings.json` naming the variable:
  - `git ls-files -s -z --full-name -- ':(top).claude/settings.json'` lists nothing (exit 0);
  - the working-tree path `.claude/settings.json` passes `lstat` as a regular file, and its content names the variable.
- Nothing is listed, so nothing is read, and the check passes. The host follows the symlink.
- Change:
  - also list each parent directory of every `HOST_ENV_PATHS` entry as an exact `:(top)` pathspec (`.claude`,
    `.devcontainer`, `.vscode`, `.config`, `.config/mise`, `.codex`);
  - refuse when one is a `120000` or `160000` entry;
  - add a test case to 10; control: list only the exact paths, and the case goes red.

**C2. Neither N2 control can go red against test 10's decoy case as written.**
- The case puts a decoy naming the variable outside the repository and tracks nothing. Nothing tracked means nothing
  is listed and nothing is joined, so both breaks stay green:
  - "`ls-files` without `--full-name`, joined to the toplevel";
  - "drop the outside-toplevel refusal".
- Replace it with two cases, run from `<top>/svc/api`:
  - **Positive:** a clean tracked `<top>/.claude/settings.json` gives `match`. This catches dropping `--full-name`
    alone, because the containment check then refuses the relative path.
  - **Decoy:** a tracked `<top>/.claude/settings.json` that names the variable, plus a clean
    `<top>/../../.claude/settings.json`, gives `repository-sourced`. This catches dropping `--full-name` and
    containment together, because the clean decoy is then read instead.
- Keep the current case too: it pins that nothing outside the repository is read when nothing is tracked.

**C3. Each root's listing must be joined to that root's own toplevel.**
- Plan lines 99 and 204 run `rev-parse --show-toplevel` in `path` only. With `--full-name`, the project root's and the
  main checkout's listings are relative to their own repository tops.
- The likely mistake: joining them to the verify tree's top reads the goal worktree's copy. With a clean copy there,
  that fails open: a branch adds the file in the project root's checkout while the goal worktree, cut from base, holds
  the clean version.
- Change:
  - run one `rev-parse --path-format=absolute --show-toplevel` per root (the main checkout's top is the parent of the
    common dir);
  - give test 10's roots case a clean tracked copy in the verify tree;
  - control: join to `path`'s top, and the case goes red.

## Remaining note (unverified, not blocking)

- **The scrub can fail closed in some containers.** With global and system config off, a checkout owned by another
  user, whose `safe.directory` sits only in global or system config, fails `rev-parse` (exit 128). That reads as
  `no-identity` ("not a Git worktree") in a container where the unscrubbed git-local read works.
- No cross-owner checkout was available to test it. Name it in Risks.
- Do not work around it with `-c safe.directory=*`, which removes the protection against another user's `.git/config`.

## Confirmed

- N3: per-clause cases, plus a control for each and for case sensitivity.
- N4: `rev-parse` runs before the veto, the exit mapping is explicit, and test 8's non-Git directory gives
  `no-identity`.
- N5: test 4 targets `environment_approval`.
- N6: the declarative indirections are named as residuals.
- R3: `--path-format=absolute --git-common-dir`, with the bare main skipped.
- R4: root-relative pathspecs beside `:(top)`.
- R13: parity rejected, with its reason.
- The line cite is now `shell_policy.py:31`.
- No new fail-open beyond C1 and C3.

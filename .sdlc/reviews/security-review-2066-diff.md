# security review · 2066-diff (environment approval for the verify path, `SIGMA_TRUSTED_VERIFY_SHA256`)

Reviewed: the uncommitted diff against `dcfde5f` in this worktree (`git diff dcfde5f`, plus the untracked
`tests/test_verify_env_trust_prose.py`), read with its call graph. Context: `.sdlc/plans/2066.md` (revision 4/5),
`.sdlc/reviews/security-review-2066-plan.md` and `.sdlc/reviews/plan-review-2066.md`. Independent and author-blind.
North star: `north_star.py` printed `absent`, so there were no declared non-negotiables to check. AGENTS.md's five
properties and its "run the control" rules were used instead.

Method: every probe ran against a copy of the working tree in the scratchpad
(`<scratch>`),
with temporary git repositories under `.../scratchpad/exp/tmp`. Nothing in this worktree was edited except this file.
Mutation controls used `.../scratchpad/exp/batch.py` with `.../exp/m1.json` and `.../exp/m2.json`. git 2.46.2, Python
3.13.4, macOS (APFS, case-insensitive).

## summary
0 critical · 0 high · 2 med · 5 low (+5 info)
ready to ship: not without remediation. S1 and S2 are small, local fixes. S3 is a cheap regression fix and is
recommended in the same change. S4 to S7 can be recorded as follow-ups.

The core binding holds. No path makes `environment_approval` return `match` on a value the operator did not compute
for this `owner/name` and every command about to run. The strings hashed are the strings that run: `commands` is
captured once and is immutable from the gate to `subprocess.run`, or to `resource_verify`, which substitutes only
`{workers}`. The only fail-open found is in the secondary `repository-sourced` guard, on case-insensitive filesystems
(S1). The one call-site slip that would fail open is untested, because the loop fixtures short-circuit it (S2).

## entry points
- `loop.py verify <sdlc> <goal>`: the gate is in `_verify_goal_body` at `skills/sigma-loop/scripts/loop.py:6542`.
  - auth: none (a local CLI, run by an operator or agent shell).
  - authz: the git-local key on `root`, OR `SIGMA_TRUSTED_VERIFY_SHA256 == approval_value(origin owner/name, c)` for
    every `c` in `state.declared_verify_commands(goal, config, sdlc_dir)`.
  - Executed at `loop.py:6557` (resource path) and `loop.py:6575` (plain path), plus Sigma's fixed-argv
    pytest passes.
- `loop.py red <sdlc> <goal>`: the gate is at `loop.py:6392-6400`, with the same decision on the same declared
  commands. Red runs only the planned-test pytest argv (`red_green.observe`).
- The process environment variable `SIGMA_TRUSTED_VERIFY_SHA256` (new input).
  - Read directly, `shell_policy.py:242`, never through `legacy.getenv`; registered in `legacy.POST_RENAME_ENV`.
- `doctor.py check`: the trust row, `_verify_trust_row`, at `skills/sigma-doctor/scripts/doctor.py:205`. Output only.
- `doctor.py verify-approval [sdlc_dir]`: `_verify_approval`, `doctor.py:247`. Output only; the operator's value
  source.
- `init_flow._verify_trust_lines`: `skills/sigma-init/scripts/init_flow.py:506`. Output only.
- Not entry points: the env value grants nothing else. The other `repository_shell_commands_allowed` call sites are
  untouched (`git grep`), and the diff stat touches none of them:
  - `autowatch.py:885`, `autowatch.py:1046`, `backlog_check.py:812`, `bounded_run.py:470`, `loop.py:4750`,
    `pipeline.py:68`, `reviewer.py:1066`, `work.py:4078`, `verify_merge.py:122`;
  - the separate copies in `risk-detect.sh` and `kg.py`.
- Callers of `verify_decision` and `environment_approval` are exactly `loop.py` (×2), `doctor.py` and
  `init_flow.py`, and only `loop.py` acts on the result. Nothing runs `loop.py verify` as a subprocess from
  `skills/` or `hooks/`. The trust row is not in `setup_wizard._MODES`, so the value is not injected into
  session-start context.

## findings

[S1] severity:med — authz / input validation (A01, A04) — `skills/sigma-loop/scripts/shell_policy.py:173-177`
(`_pathspecs`), `:167-170` (`_matches`) and `:187-193` (`prefix` / `local`).
On a case-insensitive filesystem, `repository-sourced` never sees a host env-config file committed with different
case. That covers macOS APFS by default, which is Sigma's main developer platform, and Windows. The pathspecs are
`literal`/`glob` with no `icase`, and `_matches` uses `fnmatchcase`.

MEASURED (`.../exp/case.py`): with `core.ignorecase=true`, each of these returned `('match', 'owner/repo')`:
- tracked `.Claude/settings.json`, `.claude/Settings.json` and `.CLAUDE/SETTINGS.JSON`, each naming the variable;
- tracked `.Envrc` and `MISE.toml`, naming the variable.

In every case the canonical path a host opens (`.claude/settings.json`, `.envrc`, `mise.toml`) reads that file. The
parent-directory symlink check (C1) has the same blind spot, for a `.CLAUDE` symlink.

A second form, MEASURED (`.../exp/case2.py`): pass the project root as `.../SVC` when the directory on disk is
`svc`. git lists `svc/.claude/settings.json` through the relative pathspec. `realpath` keeps the typed case, so
`rel.startswith(prefix + "/")` fails, the entry is skipped, and the result is `match`.

Effect: a branch can commit `.Claude/settings.json` with
`env: {SIGMA_TRUSTED_VERIFY_SHA256: approval_value("<owner>/<repo>", <its own verify.command>)}`. A host on macOS
applies it, and `loop.py verify` then runs the branch's command with no operator approval. Before #2066 that
self-grant did not exist.

The harm is bounded, because a committed declarative env block is already code execution (see S4). But the guard is
advertised in TM-04 and `docs/cloud-sessions.md`, and AGENTS.md SAFETY forbids a silent half-guarantee.

UNVERIFIED: Claude Code's own load of a case-variant file was not exercised. The filesystem read through the
canonical path was.

fix:
- emit `:(top,icase,<magic>)` and `:(icase,<magic>)` pathspecs; git 2.46 accepts `icase` with `literal` and `glob`
  (measured);
- casefold `rel` in `_matches`;
- compare the prefix casefolded, or better, take the prefix from `git -C <cwd> rev-parse --show-prefix`, the
  prefix git itself applies to the relative pathspecs.

A trial of exactly these edits in the scratch copy turned all five cases and the `SVC` case into
`repository-sourced`, and the 87 tests in `tests/test_shell_policy.py` stayed green. Add
`.Claude/settings.json` to the parametrised cases; the control is to drop `icase` and see it go red.

[S2] severity:med — test adequacy of a fail-open slip (A04) — `loop.py:6397` (red) and `loop.py:6542` (verify),
the `roots=(pathlib.Path(sdlc_dir).resolve().parent,)` argument.
Neither gate's wiring of the project root is pinned. MEASURED: change either call to `roots=()` (controls M1 and
M2), and every test in `tests/test_shell_policy.py`, `tests/test_test_first_gate.py` and `tests/test_init_flow.py`
stays GREEN (239 passed, and 164 passed).

The cause is a fixture short-circuit. Every loop-level test has no work record, so `_verified_tree` falls back to the
project root. That makes `root` equal to the root in `roots`, and the dedupe hides the argument.

`roots` is load-bearing exactly when the project root is not the verify tree or its main checkout: a `.sdlc` in a
subfolder (R4), or a project root that is itself a linked worktree. There, dropping it silently stops checking the
directory the host was most likely launched from. The plan's control table has no row for it, so AGENTS.md's "a test
never seen red proves nothing" applies.

fix: add one `verify_goal` test and one `red_goal` test with:
- a work record pointing at a clean goal worktree;
- a project root (the parent of `sdlc_dir`) in a separate linked worktree whose tracked `.claude/settings.json`
  names the variable;
- a matching value.

Assert exit 2, `repository-sourced`, and no sentinel or pytest run. Control: `roots=()` at each call goes red.

[S3] severity:low — failure mode / liveness (repository input) — `doctor.py:239` and `doctor.py:267`.
`sp.approval_value(identity, command)` runs outside any `try`, on a `verify.command` read from repository config.
A non-string truthy value (a list, int or dict) raises `TypeError`, and a string holding a lone surrogate (JSON
`"\udcff"`) raises `UnicodeEncodeError`.

MEASURED (`.../exp/doc.py`, and an end-to-end run): `doctor.py check .sdlc` with
`{"verify": {"command": ["make", "test"]}}` in a repository with a GitHub origin exits 1 with a traceback and prints
no rows. At `dcfde5f` the same project exits 0 with a `[MISSING] verify command trusted` row.

So a committed config can now abort the whole `/sigma-doctor` run. That contradicts the `_block` docstring's
"degrades … instead of aborting the whole check". The session-start wizard swallows the exception
(`hooks/session_start.sh:217-221`), which means it silently goes quiet. `verify-approval` tracebacks the same way.
No fail-open.

fix:
- in `_verify_trust_row`, add the env hint only when `isinstance(command, str)`, and wrap `approval_value` in
  `try/except (TypeError, UnicodeError)`, falling back to the git-local gesture alone;
- in `_verify_approval`, refuse a non-string command, or one that cannot be encoded, with exit 2 and a one-line
  stderr message that does not print the command;
- add a test for each, and see it red.

[S4] severity:low — design honesty (A04, A05) — `docs/cloud-sessions.md:70-72` and `docs/threat-model.md`
(the TM-04 ceiling bullets).
The docs say the residual is "code that builds the name at run time (hooks, setup scripts, …)". The real residual is
broader. Every listed loader that applies a committed declarative env block already gives the repository code
execution, without hooks: through `PATH`, `BASH_ENV`, `ZDOTDIR`, `GIT_CONFIG_*` with `core.fsmonitor`,
`NODE_OPTIONS` and similar.

That block can also blind the guard itself. MEASURED (`.../exp/tmp/path.*`, `.../exp/pathsub.py`): a tracked
`.claude/settings.json` names both `PATH=./.tools:/usr/bin:/bin` and the variable, with a committed `.tools/git`
that answers `ls-files` with nothing. It gives `('repository-sourced', …)` under the inherited PATH and
`('match', 'owner/repo')` under the block's PATH. `_git` scrubs `GIT_*` but resolves `git` through the inherited
`PATH`.

The guard also reads files now, while the host loaded the environment at launch. A launch-branch file later removed
from the root (by a checkout or a pull) leaves the value set and the check clean.

So `repository-sourced` is a tripwire on the plain form, not a boundary. That is acceptable, because it adds no
capability beyond what such a block already has, but the docs should say so.

fix:
- state in TM-04 and in `cloud-sessions.md` that a host applying a repository's committed env has already given it
  code execution;
- state that `repository-sourced` catches only the plain, direct naming, and that the file is read at check time,
  not at load time;
- optionally, cheaply: refuse (`repository-sourced` or a new `path-in-repository`) when any `PATH` entry is
  relative, or resolves inside one of the checked toplevels.

[S5] severity:low — coverage of the closed list (A05) — `shell_policy.py:42-50` (`HOST_ENV_PATHS`).
Two gaps, both in the class the plan names as residual, and both cheap to narrow.

Missing mise config paths (from mise's documented search list; UNVERIFIED in this run, mise not exercised):
`mise/config.toml`, `.mise/config.toml`, `.config/mise/conf.d/*.toml`, `mise.<MISE_ENV>.toml` and
`.mise.<MISE_ENV>.toml`, and the legacy `.rtx.toml`.

Intermediate directories: mise and direnv also load from every directory between the git top and the launch
directory. Sigma checks only the top and the root itself, so `<top>/apps/mise.toml` is unseen when launched from
`<top>/apps/web`.

fix:
- add the mise paths, with `*` segments as glob pathspecs;
- for each checked root, also check each ancestor directory between it and its toplevel, using the
  cwd-relative pathspecs at each level;
- or name both in the docs' residual list.

[S6] severity:low — authz binding (A01) — `shell_policy.py:122-136` (`_identity_from_url`).
The identity is the last two path segments, with no host. MEASURED: each of these gives `owner/repo`:
- `https://gitlab.com/Owner/Repo.git`;
- `https://evil.example/mirror/owner/repo`;
- `git@bitbucket.org:owner/repo.git`;
- `/home/me/clones/owner/repo`;
- `file:///tmp/x/Owner/Repo`.

So a different repository with the same final two segments, cloned into the same environment, matches the approval.
It needs someone who sets `origin` (the operator, or an agent, which is the stated ceiling), so the risk is low. The
cloud proxy shape (`http://proxy@127.0.0.1:1234/git/Owner/Repo`) carries no host, which is presumably why the host is
dropped.

fix: say in the docs that the identity is host-agnostic, which matters when one environment holds same-named
repositories from two hosts or groups. Optionally refuse a URL whose host is neither `github.com`, a GitHub host
alias, nor a loopback proxy.

[S7] severity:low — test adequacy of fail-closed clauses — `shell_policy.py:107`, `:116` and `:222-223`.
Three clauses have no test that goes red. MEASURED controls, each GREEN with 87 passed:
- M3, drop `GIT_CONFIG_GLOBAL=os.devnull`: `--local` already defends every identity case, so
  `test_global_git_config_cannot_borrow_an_identity` cannot fail under this break (the plan review said so for the
  `GIT_CONFIG_*` cases);
- M14, drop `len(lines) != 2`: a checkout path containing a newline then splits into a wrong top, the listed files
  read as absent, and the result is `match`;
- M21, skip a root whose `rev-parse` fails instead of refusing.

All three fail closed today.

fix: add a case for M14 (a toplevel directory name containing `\n`, which gives `no-identity`) and for M21 (a
`roots` entry that is not a Git worktree, which gives `error`). For M3, either find a global-config effect on
`ls-files`/`rev-parse` to pin, or comment that the clause is defence in depth with no red control.

[S8] severity:info — `docs/threat-model.md:43`, the TM-04 mitigation cell. The new "Residual: Sigma cannot tell …"
now prefixes three unrelated mitigations (leading `-` refused, `safe_state_open`, `SIGMA_ALLOW_REPO_SMTP`), which
therefore read as residuals. Move "Residual: …" to the end of the cell, or into the ceiling section.

[S9] severity:info — `tests/test_verify_env_trust_prose.py`, `test_only_the_verify_path_asks_the_environment_approval`.
The call-site ratchet matches only direct call syntax (`verify_decision(` and `environment_approval(`), only in
`skills/**/*.py`. An aliased reference (`f = sp.verify_decision`) or a caller under `hooks/` or `tools/` evades it.
It is acceptable as a ratchet; say so in its docstring.

[S10] severity:info — the reason prose has two homes: `doctor._ENV_APPROVAL_STATES` (`doctor.py:191-203`) and
`shell_policy._CLAUSES`. They can drift, which is a maintainability point, not a security one. The doctor could
reuse `verify_refusal_message`'s clause.

[S11] severity:info — positive, verified:
- the env value is never printed: not by the loop's refusal (pinned), the doctor row, or `verify-approval`;
- `repository-sourced` paths are printed through `json.dumps` (ASCII-escaped), so terminal escapes in a hostile
  file name are neutralised;
- `identity` passes `\A[a-z0-9_.-]+/[a-z0-9_.-]+\Z` and cannot carry a token or a control character;
- the remote URL and the command are never printed;
- git's reads use `--local` with no includes, so a committed `.gitconfig` that the operator includes is not
  followed;
- `_vetoed` maps exit 0 `false`, exit 128, and any other non-1 code to "veto";
- the location-variable check reads the same mapping the value came from. Production callers pass `None`, so that is
  `os.environ`, the environment the verify command inherits;
- the post-gate passes (`flake_check`, `diff_revert`, `red_green`, `witness`, `resource_verify`) run no
  config-derived shell string; `resource_verify` takes only integers from config.

Controls MEASURED red in this review, each with one clause broken:
- M4, no `GIT_*` scrub;
- M5, exit 128 continues;
- M6, red reads commands from `stem`;
- M7, location variables read from `os.environ`;
- M8, `--full-name` dropped;
- M9, the cwd-relative form dropped;
- M10, containment dropped;
- M11 and M12, the C3 joins;
- M13, the non-regular clause dropped;
- M15, identity not case-folded;
- M16, the parent symlink check dropped;
- M17, `no-command` dropped;
- M18, red maps an error to `[]`;
- M19, a failed `ls-files` read as clean;
- M20, `_matches` changed to exact match;
- M22, the command added to the doctor hint;
- M23, the doctor ignoring the policy;
- M24, no `try` around the doctor's loader;
- M25, init back to git-local;
- M26, escape decoding dropped.

## checklist trace
1. authn — none at the CLI; whoever controls the process environment is the caller, operator or agent alike
   (documented ceiling). A repository is also that caller when a host applies its committed env: refused in the plain
   form, except on case-insensitive filesystems (S1), and blind to a block that also sets `PATH` (S4).
2. authz — the grant is confined to `loop.py:6397` and `loop.py:6542`, and the other sinks keep git-local only
   (`git grep`). It is bound to every declared command and to the `origin` `owner/name`, which is host-agnostic
   (S6). The project-root wiring is untested at both calls (S2).
3. input validation — `fullmatch` on `[0-9a-f]{64}` with no trimming, so a multi-value or whitespace-wrapped value
   refuses (tested). Commands are checked to be non-empty `str`; a non-iterable gives `error`. The doctor does not
   validate the command's type before hashing it (S3).
4. injection surfaces — none new. Git is invoked with argv lists and `--`-terminated pathspecs, the
   repository-controlled path is JSON-escaped on output, and the documented gesture uses `python3 -I`
   (tested with planted `json.py` and `hashlib.py` and `PYTHONPATH=:`). `git` itself is resolved through the
   inherited `PATH` (S4).
5. PII / secrets / crypto — sha256 over a domain-separated, newline-delimited message; the identity cannot contain
   `\n`, so it parses unambiguously. The value is public by design and needs no constant-time compare. The
   remote URL (which can carry a token), the command and the env value are never echoed (verified in code and by
   tests).
6. rate / cost — with the value unset: zero extra spawns in `environment_approval`, and one extra `git config` in the
   doctor's failing row. With it set: about 4 to 8 bounded `git` calls with a 5 s timeout each; the worst case
   refuses after about 40 s. Nothing grows with repository size except `ls-files` over about 30 pathspecs. The
   costs were not timed in this review.
7. dependencies & integrity — no new packages; `shell_policy` stays standard-library only. The approval binds the
   text, not the files it runs (documented). No unsigned artifact or deserialisation beyond `json.loads` of local
   config.
8. failure modes — every error path in `environment_approval` refuses: an outer `except Exception` gives `error`.
  Failures of the `rev-parse` count, a root's `rev-parse`, `ls-files` or a read all refuse; three of those clauses
  are untested (S7). `doctor.py check` now aborts on a malformed command (S3).
9. configuration — the default is unset, which leaves git-local deciding with an unchanged message (byte-identical,
   tested). An explicit git-local `false` vetoes the value. No debug flags. The closed host-config list misses some
   mise paths and intermediate directories (S5).
10. outbound requests — n/a. Nothing is fetched; the origin URL is parsed, never contacted.

## Re-check (revision 6)

Re-read `git diff dcfde5f` after the coordinator folded S1 to S7 in. The same rules applied: probes ran in a fresh
scratch copy (`.../scratchpad/review/sigma2`), the scripts from the first pass were re-run against this worktree,
and the mutation controls are in `.../scratchpad/exp/m3.json` and `.../exp/m4.json`.

### summary
0 critical · 0 high · 0 med · 1 low (new, introduced by the S1 fix) · 1 low (pre-existing, same root cause)
ready to ship: yes, once R1 is fixed (a one-function change, trialled green below). Without R1 fixed, the
residual it leaves is low.

### closed
- **S1, closed for the measured cases.** Pathspecs now carry `icase`, `_matches` casefolds both sides, and the
  prefix is casefolded. `exp/case.py` gives `repository-sourced` for all five variants, and `exp/case2.py` gives it
  for the `SVC` root. Controls, each seen RED in the revised copy:
  - drop `icase`: 4 cases red;
  - drop the casefold in `_matches`: 5 red;
  - drop the casefold on the prefix line: `test_a_case_variant_project_root_is_never_skipped` red.

  Breaking only `rel.casefold()` in the `startswith` stays green, because that test's file is lower-case. A
  second clause the test cannot reach; not a gap that matters, given R1.
- **S2, closed.** `test_verify_checks_the_project_root_worktree` and `test_red_checks_the_project_root_worktree`
  use the three-worktree layout. Each goes RED under its own break: `roots=()` at `loop.py:6542`, and `roots=()` at
  `loop.py:6397`.
- **S3, closed.** `exp/doc.py` with a list, an int, a dict and a lone-surrogate command now returns a failing row
  with the gesture only; nothing is raised. End to end, `doctor.py check` exits 0 with the `[MISSING]` row.
  `verify-approval` exits 2 and prints `no value can be made for this verify.command (TypeError)`, a type name
  only, with no command text.
- **S7, closed for two of three.** Dropping the two-line check, and skipping a root that fails `rev-parse`, are now
  each RED. The no-op on `GIT_CONFIG_GLOBAL` is commented as having no red control, which is acceptable.
- S4, S5 and S6 are documented. `docs/cloud-sessions.md` now says "a tripwire, not a boundary" and names the
  check-time versus launch-time gap, the intermediate directories and the host-agnostic identity. The TM-04
  "Residual:" placement is fixed: the export ceiling is now a parenthetical. The mise list is widened, with the
  note that it was not exercised.
- Baseline: `tests/test_shell_policy.py`, `tests/test_test_first_gate.py` and `tests/test_verify_env_trust_prose.py`
  pass in the revised copy, 185 tests. I did not run the full suite.

### new findings
[R1] severity:low — a fail-open introduced by the S1 fix — `skills/sigma-loop/scripts/shell_policy.py`, `_sourced_in`:
the `prefix = ...casefold()` line, and `local = [rel] + ([rel[len(prefix) + 1:]] if ... rel.casefold().startswith(prefix + "/") ...)`.
The prefix is casefolded and then used as a slice length on the un-casefolded `rel`. Casefolding changes the length
of some characters (`ß` to `ss`, `İ` to `i̇`, `ﬁ` to `fi`), so for a project root under such a directory the slice
is off and the entry is skipped.

MEASURED (`.../exp/uni.py`), with a tracked `<top>/<dir>/web/.claude/settings.json` naming the variable and
`roots=(<top>/<dir>/web,)`:

| directory | revision 6 | pre-fix revision |
| --- | --- | --- |
| `Straße` | `match` | `repository-sourced` |
| `İstanbul` | `match` | `repository-sourced` |
| `ﬁle` | `match` | `repository-sourced` |
| `Apps`, NFC `café` | `repository-sourced` | `repository-sourced` |

Low: it needs the operator's own project root to sit under such a directory inside the repository, and a branch
cannot move `.sdlc`.

fix: take the prefix from `git -C <cwd> rev-parse --show-prefix`, under the same scrubbed `_git`, and compare it
exactly: `rel.startswith(prefix + "/")`, then strip by its own length, with casefold kept only in `_matches`.
- MEASURED: `rev-parse --show-prefix` returns the same form as `ls-files --full-name`: NFC, and the on-disk case
  (`svc/` for a root typed `SVC`).
- Trialled in the scratch copy, then restored: every row above, NFD `café` and `SVC` all give `repository-sourced`,
  and the 103 tests in `tests/test_shell_policy.py` stay green.

Add an `ß`-directory case; the control is the current casefolded prefix, which goes red.

[R2] severity:low — pre-existing in both revisions, closed by the same fix. On macOS a directory whose name is
stored decomposed (NFD `café`) gives `match`. git prints NFC (`core.precomposeunicode`), while
`os.path.realpath` keeps NFD, so the prefix never matches. The R1 fix closes it (measured above).

### verdict
Ready to ship once R1 is fixed with `rev-parse --show-prefix`, which also closes R2. No other new fail-open was
found: the core binding, the two loop gates, the doctor and init are unchanged in behaviour apart from the folded
fixes.

# Benchmark task set: sourcing, selection and freeze

Status: **frozen** (`"frozen": true` in `evals/bench/tasks/manifest.json`; the freeze commit is recorded in the pre-registration's Deviations). 15 tasks: 8 external and 4 internal
non-trap tasks, plus 3 traps that are **agent-authored** (see "Trap authorship" below), all verified.
The method is fixed in [`preregistration.md`](preregistration.md); this page records how the tasks were found and what was
measured about them. Nothing here ran an arm, called a model or spent money.

## Why 15, and the balance

The pre-registration asks for about 15 tasks, balanced between external and internal work as far as the pool permits,
and its decision rule is tabulated for 12 to 18 tasks only; `bench_tasks.py check` refuses any other count as an owner
decision. The external pool was not the limit: eight tasks verified from a short search. The internal side is 4 non-trap tasks (one
each of bug fix, feature, refactor and docs) plus the 3 agent-authored traps: 7 internal against 8 external is as
close to even as 15 and the three required traps allow. The issue text asked for 15 and 15; the owner's reduction supersedes it.

## Training cutoff and its source

Pinned model (proposed in the pre-registration): `claude-sonnet-5-5`. Anthropic's models overview lists Claude Sonnet 5.5
with **training data cutoff: Jun 2026** and reliable knowledge cutoff Jun 2026
(<https://platform.claude.com/docs/en/about-claude/models/overview>, read 2026-10-04). That is a primary source, but it is a
month, not a day. Rule used: a pull request qualifies only if created on or after **2026-07-01**, so every external PR was
created after the whole of June 2026 (the earliest is 2026-07-02). Whether the owner pins this model ID is an owner decision
and is not settled here; if the pin changes, change `model` in the manifest and re-run `check`, which recomputes against it.
The issue text itself can be older than the cutoff (three selected issues predate it: from 2017, 2020 and 28 June 2026); only the fix is required to be post-cutoff, as
the pre-registration says.

## How candidates were found

REST only (`gh api`), no GraphQL. Search calls: about 26; reads (timeline, pull request, files, repository): a few hundred (counted from the
commands issued, not metered). Queries, in order:

1. `is:closed is:issue linked:pr language:python closed:>2026-07-15 comments:>0`, sorted by reactions, pages 1 to 3
   (324,528 results reported; the first 300 read, from 152 repositories).
2. `is:closed is:issue linked:pr language:python created:>2026-07-05 closed:>2026-07-10 label:bug`, pages 1 and 2 (127,886 reported; the
   first 200 read).
3. Groups of five `repo:` qualifiers over about 105 permissively licensed Python libraries I chose by name, each with
   `is:closed is:issue linked:pr closed:>2026-07-05` (679 issues returned).

Queries 1 and 2 returned mostly very large projects and small repositories whose issues are long machine-written task lists; I did not
check those repositories' stars one by one, and moved to query 3, which produced every candidate below. This is a
convenience sample of libraries I named, not a draw from all repositories, and it is not representative of anything
broader.

For each issue the timeline gave the cross-referenced pull requests; for each, whether it merged, when it was created, the
changed files and line counts. Test files are paths with a `test` or `tests` directory component, or a name starting `test_`
or ending `_test.py`, or `conftest.py`; every other changed file (including changelogs) counts toward the 300-line limit. In all eight
selected tasks the pull request's test changes are whole `.py` test files modified in place, so the hidden tests are those files at the fix commit. The fix commit is the PR's merge commit and the starting tree is its **first parent**: a single-parent (squash or rebase) commit for four tasks, a two-parent merge for the other four (both lark tasks and both more-itertools tasks); for the lark tasks the PR's own recorded base is older than the first parent (by 14 and 15 commits), and for sqlparse #332 the first parent is 4 commits ahead of the recorded base. Each starting tree was checked by measurement instead: the hidden tests fail on it and pass on the fix tree.

## Selected external tasks

Rules applied to each: Python; MIT, BSD or Apache-2.0 (the license field GitHub's API reports, which is set for all eight);
at least 50 stars; closed by exactly one merged PR (other cross-referenced PRs on these issues were closed unmerged);
that PR created after the cutoff; 300 or fewer non-test lines changed; at least one test that fails on the base and passes after
(measured below); at most 3 per repository (here lark, more-itertools and sqlparse have 2 each, bottle and voluptuous 1).

| Task | Repository | License | Stars (2026-10-04) | Issue | Merged PR | PR created | Non-test lines | Hidden test files (in the project's tests directory) | What the issue text offers toward the fix |
|---|---|---|---|---|---|---|---|---|---|
| `ext-bottle-1539` | bottlepy/bottle | MIT | 8793 | [#1539](https://github.com/bottlepy/bottle/issues/1539) | [#1541](https://github.com/bottlepy/bottle/pull/1541) | 2026-09-15 | 20 | `test/test_outputfilter.py` | asks whether a PR is wanted and quotes the current code as a diff (the defect, not the fix); the design is left open and the hidden tests fix one (see below) |
| `ext-lark-1618` | lark-parser/lark | MIT | 5999 | [#1618](https://github.com/lark-parser/lark/issues/1618) | [#1619](https://github.com/lark-parser/lark/pull/1619) | 2026-07-08 | 9 | `test_grammar.py` | diagnoses the cause (a lambda closing over a loop variable), quotes the buggy loop and a reproduction, says a one-line fix exists; no fix code |
| `ext-lark-1630` | lark-parser/lark | MIT | 5999 | [#1630](https://github.com/lark-parser/lark/issues/1630) | [#1632](https://github.com/lark-parser/lark/pull/1632) | 2026-07-30 | 2 | `test_tree_templates.py` | states the fix in prose (tell None from an empty dict in `search`) and which two tests to correct (a source-only fix leaves those two visible tests red until the arm corrects them as the prompt says; on the pull request's full fix tree the visible suite passes); no code |
| `ext-more-itertools-1252` | more-itertools/more-itertools | MIT | 4098 | [#1252](https://github.com/more-itertools/more-itertools/issues/1252) | [#1253](https://github.com/more-itertools/more-itertools/pull/1253) | 2026-09-03 | 13 | `test_more.py` | diagnosis, the intended behaviour (an empty result for every `maxsplit`) and a prose plan (guard the fast path); quotes the buggy lines, no replacement code |
| `ext-more-itertools-1304` | more-itertools/more-itertools | MIT | 4098 | [#1304](https://github.com/more-itertools/more-itertools/issues/1304) | [#1305](https://github.com/more-itertools/more-itertools/pull/1305) | 2026-09-29 | 6 | `test_more.py` | diagnosis and the intended behaviour (`ichunked` should match `chunked` for `n` of 0 and below); no fix code. The hidden tests also pin that the source is left unconsumed for `n=0` and the `chunked` error message for a negative `n` (the issue quotes it) |
| `ext-sqlparse-332` | andialbrecht/sqlparse | BSD-3-Clause | 4021 | [#332](https://github.com/andialbrecht/sqlparse/issues/332) | [#865](https://github.com/andialbrecht/sqlparse/pull/865) | 2026-07-19 | 7 | `test_parse.py` | none: reports the symptom with a script (the issue is from 2017; only the fix PR is post-cutoff) |
| `ext-sqlparse-601` | andialbrecht/sqlparse | BSD-3-Clause | 4021 | [#601](https://github.com/andialbrecht/sqlparse/issues/601) | [#868](https://github.com/andialbrecht/sqlparse/pull/868) | 2026-07-25 | 10 | `test_regressions.py` | none: reports the symptom and its output (the issue is from 2020; only the fix PR is post-cutoff) |
| `ext-voluptuous-541` | alecthomas/voluptuous | BSD-3-Clause | 1851 | [#541](https://github.com/alecthomas/voluptuous/issues/541) | [#542](https://github.com/alecthomas/voluptuous/pull/542) | 2026-07-02 | 6 | `voluptuous/tests/tests.py` | a one-line sketch in prose: detect the non-finite case and raise `Invalid`; no code (the hidden test also pins the message text, which the starting tree already uses elsewhere) |

The prompt is the issue's title and body verbatim plus one line, `Make the change in this repository.` No prompt links the fixing PR or names the fix commit
(`check` refuses both). **The issue texts are not neutral:** all eight carry a diagnosis and most suggest a fix (last column), which the pre-registration makes part of the task ("the prompt is its issue text"). The rule applied: a candidate was excluded when its issue text quotes the replacement code for the defective lines, as a diff, a fenced replacement block or an inline replacement expression (markdown-it-py #415, more-itertools #1250 and tinydb #632). `ext-bottle-1539` was kept although its issue contains a fenced diff: it is the diff of an earlier regression (the removed lines are the old constructor check), and restoring them would not implement the two configs the issue asks for; a fix described in prose, with no replacement code, was kept and is disclosed above, as is a diagnosis. That makes the kept tasks easier and a ceiling effect likelier. **Owner decision, open:** accept issue texts as they are (the pre-registered rule), or screen out any issue that proposes a fix, which shrinks the pool further and was not tried. `ext-bottle-1539` is the other way round: its issue leaves the design open, and the hidden tests fix one design (an application configuration with `json.enable` or `json.dump_func` set after creation, and a custom plugin after `uninstall`), so an arm that picks another design can fail them. The base tree is fetched at run time at the pinned base sha and its digest is recorded in `fetch.json`; `materialize` refuses a
tree that differs. Availability of a third-party repository is not pinned: before the freeze, keep a copy of each fetched
tree (see Freeze).

## Verification, measured

Command: `python3 tools/readiness/bench_tasks.py verify --hidden-root ~/.sigma-ops/bench/hidden --external --scratch <empty dir>`, run on
2026-10-04 on one macOS arm64 machine, in the one environment built from `evals/bench/tasks/environment.lock` (CPython 3.12). Scoring goes through
`bench._command_passed` and `bench._hidden_passed` behind a pass-through launcher with the harness's `isolated_env` (so the working directory, the `.sigma-hidden/` copy and
the `verify.json` argv are the harness's), and a second plain run of the same command must agree. A hidden run on the starting tree must exit with pytest status 1 (tests ran and failed), not a collection error.
Result: **15 verified, 0 failed** (8 external, 4 internal, 3 agent-authored traps; the traps' hidden tests also fail on their trap-falling implementation), every hidden run under 4 seconds (the longest, 3.2 s, `ext-more-itertools-1304`) and every visible suite under 11 seconds (the longest, 10.8 s, `ext-more-itertools-1304`), on this machine. The full output (interpreter, per-run timings, check result) is committed as
`docs/launch/evidence/355-bench-task-verification.json`, which names the base commit and the sha256 of the tool, manifest and lock it measured.

| Task | Visible tests on the starting tree | Hidden on the starting tree | Hidden on the reference fix |
|---|---|---|---|
| `ext-bottle-1539` | pass | 2 failed, 25 passed in 0.06s | 27 passed in 0.04s |
| `ext-lark-1618` | pass | no pytest count line | no pytest count line |
| `ext-lark-1630` | pass | no pytest count line | no pytest count line |
| `ext-more-itertools-1252` | pass | 3 failed, 598 passed, 11206 subtests passed in 2.56s | 598 passed, 11209 subtests passed in 2.40s |
| `ext-more-itertools-1304` | pass | 2 failed, 617 passed, 11318 subtests passed in 3.05s | 619 passed, 11318 subtests passed in 2.55s |
| `ext-sqlparse-332` | pass | 1 failed, 87 passed in 0.09s | 88 passed in 0.08s |
| `ext-sqlparse-601` | pass | 2 failed, 91 passed, 1 xpassed in 0.10s | 93 passed, 1 xpassed in 0.09s |
| `ext-voluptuous-541` | pass | 1 failed, 180 passed in 0.17s | 181 passed in 0.14s |
| `int-bugfix-1` | fail (pytest exit 1, for the headline behaviour) | 4 failed, 2 passed in 0.01s | 6 passed in 0.00s |
| `int-docs-1` | fail (pytest exit 1, for the headline behaviour) | 3 failed, 1 passed in 0.01s | 4 passed in 0.00s |
| `int-feature-1` | fail (pytest exit 1, for the headline behaviour) | 8 failed in 0.02s | 8 passed in 0.00s |
| `int-refactor-1` | fail (pytest exit 1, for the headline behaviour) | 2 failed, 1 passed in 0.01s | 3 passed in 0.01s |

The lark rows show no count line because that project's own pytest configuration adds `-q` to the harness's `-q` (`-qq` prints none): the result there is the exit status, 1 on the starting tree and 0 on the fix. Running the two hidden files directly without the extra `-q` shows the counts: 1 failed and 26 passed for `ext-lark-1618`, 3 failed and 18 passed for `ext-lark-1630`, each an assertion failure.

Not measured: flakiness (each result is one run), behaviour with the network denied, behaviour under a real isolation launcher, other
interpreter versions or operating systems, and difficulty. Every external pull request is a small fix (2 to 20 non-test lines),
and the four internal tasks are small by design, so a ceiling effect (every arm passing most tasks, almost all ties) is likely. With four or
fewer discordant tasks the pre-registered rule is always INCONCLUSIVE, and no difficulty check has been run: nothing here claims these tasks
discriminate between the arms.

## Environment, visible tests and selection effects

- **One scoring environment.** The harness resolves every task's `visible_command` and hidden `verify.json` argv on one PATH, so all 12 tasks are scored in one
  virtualenv: `bench_tasks.py lock` resolved pytest plus every external task's test dependencies once, under CPython 3.12 on macOS arm64, from binary wheels only,
  into `evals/bench/tasks/environment.lock`, whose hash and interpreter are in the manifest. `verify --external` builds the environment from that lock; the run must
  do the same (the harness does not enforce it). Wheels-only biases the pool toward projects whose test dependencies publish wheels. The project under test is never installed:
  each task imports it from its own tree.
- **Visible tests.** External tasks keep the pre-registered construction, the project's existing tests at the base commit, which pass. Internal non-trap tasks each carry one
  visible test that fails on the starting tree for the task's headline behaviour (pytest exit 1) and passes on the reference fix. **Owner decision, open:** the matched-spend arm (A3) retries until the visible tests pass, so on an
  external task, whose visible tests already pass, it stops after its first attempt and is a second plain run; the options are to accept that, or to add the issue's reproduction as a
  failing visible test on external tasks. Hidden files are overlaid at their own relative paths, so sibling fixtures still apply; for an internal task a hidden test goes in a new file, never an edit of a visible test file (an external task's hidden file is the PR's version of an existing test file, overlaid on it).
- **Internal hidden tests.** `int-refactor-1` compares prices with exact float equality against the original arithmetic and requires `price_with` to be called with the rates 0.1, 0.05 and 0, so a refactor that reorders the float operations (for instance `subtotal - subtotal * rate`) could differ in the last bit and fail; the prompt's "discount rate" is the only guidance. `int-docs-1` checks that each bullet contains the help text and `default: <value>` unquoted; it does not check that the default comes last.
- **Selection effects.** A hidden run on the starting tree must exit with pytest status 1, so a pull request whose hidden file fails at collection (for example by importing something new) is excluded,
  which may favour bug fixes over new features; a pull request that deletes or renames a test file would leave the base copy in the starting tree, so none was selected (all eight only modify test files).
- **Environment of third-party code.** Dependency installs and project tests run with secret-shaped environment variables removed (by name: a variable such as a database URL with an unlisted name stays) and a fresh HOME and TMPDIR. That is a scrub, not a sandbox: third-party test code run by `verify --external` can read any file the user can, including the hidden root.

## Considered and rejected

| Candidate | Reason |
|---|---|
| markdown-it-py #415 (PR #416) | Verified, then replaced: the issue text contains the fix as a suggested diff, so the prompt would hand it over. Replaced by sqlparse #601. |
| more-itertools #1250 (PR #1251) | Verified, then replaced: the issue text contains the exact replacement lines (a "Suggested fix" code block). Replaced by lark #1618. |
| boltons #439 (PR #440) | Verified, then replaced: the three hidden tests that fail on the starting tree concern `BarrelList`, which the pull request fixed in the same change and the issue never mentions; the `IndexedSet` tests all pass on the starting tree, so the task does not measure what its prompt asks. Replaced by more-itertools #1304. |
| pyjwt #1209 (PR #1216) | Verified, then replaced: the prompt allows dropping the encode/decode round trip, but the hidden tests require rejecting a segment with non-zero unused bits and match one error message, so they fix one design. Replaced by more-itertools #1252. |
| w3lib #346 (PR #347) | Dropped: its hidden test file ends in a collection error on the starting tree (pytest exit 2, "1 error"; I did not look into the cause), so it cannot show a test failure, and the rule that a hidden run on the starting tree must exit with pytest status 1 excludes it. |
| tinydb #632 (PR #635) | Verified, then replaced: the issue text quotes the replacement check (a sentinel comparison in `get`) and a "Suggested Direction" section. Replaced by sqlparse #332. |
| tenacity #658 (PR #660) | The hidden test never returns on the base commit (an infinite loop in the retry cause-chain walk); scoring would wait out the timeout on every arm. |
| python-dotenv #697 (PR #698), #699 (PR #700) | Visible tests run the installed `dotenv` command, so they need the project installed per tree; the harness's `visible_command` is a bare argv. |
| platformdirs #539 (PR #540) | The package imports a version module generated at build time, absent from a plain tree. |
| packaging #1315 (PR #1316) | 7 visible tests already fail on the base commit under this environment (so a matched-spend retry could never see a pass); GitHub reports its license as undetermined (dual license files). |
| cachetools #405 (PR #408) | The PR's merge commit is not on the default branch, so no clean base; its only test change is in the `__init__.py` of its tests directory. #406 and #420 had no merged PR on the timeline. |
| humanize #400, more-itertools #1263 and #1280, marshmallow #3005 | No merged pull request on the issue timeline. |
| more-itertools #1215 (PR #1217), #1227 (PR #1231) | Pull requests that change a docstring (and, for #1231, a lint setting) and no test. |
| more-itertools #1257 | Two merged PRs (#1261 and #1266) for one issue. |
| starlette #1315 | The only post-cutoff merged PR (#3515) is a 6-line documentation change with no test. starlette #685: PR #3438 adds 660 lines. |
| nox #544 (PR #1131), omegaconf #392 (PR #1336) | 2,165 and 2,282 added lines: far over 300 non-test lines (not split into test and non-test). |
| tiktoken #464 | The only merged PR (#440) is from 2025; the others predate the cutoff and were not merged. GitPython #621: merged PRs from 2021 and 2022. |
| pip-tools #2436 (PR #2438), python-semantic-release #1476 (PR #1477) | 17 files with 219 changed lines, and 266 changed lines in 2 files: not split or verified, and not needed. |
| isort #2670 (PR #2671), #2681 (PR #2683) | Not usable: the unit tests need many more packages (black, mypy_extensions and others) than the other tasks, so a shared environment would grow and 31 test modules failed to collect with only pytest and hypothesis. |
| marshmallow #2893 (PR #3024) | The pull request only deletes test lines (0 lines added in the test file), so no test fails on the base. |
| parsel #378 (PR #379), mistune #420 (PR #471) | Probed only: both issues list alternative fixes and leave the intended design open, which would make the hidden tests unfair to an arm that picks another. |
| tinydb #629 (PR #633), sqlparse #773 (PR #860) | Spares, probed, not verified: the tinydb issue leaves the intended behaviour open (three options), which would make the hidden tests unfair to an arm that picks another; the sqlparse issue asks only that a keyword be its own token. |

## Hidden bundles

Under the operator's hidden root, never in the repository, one directory per task: `files/` (hidden tests), `reference/` (the reference
fix, internal tasks), `hidden.json`, `run_hidden.py` (copies `files/` over the scored tree and runs pytest), `verify.json` (the argv the harness runs), and, for traps,
`obvious/`, `expected_catch.txt` and `author.json`. The digest (sha256 over sorted `path sha256` lines) and each file's hash are in `task.json`; `check --hidden-root` compares them
with the disk, refuses (for internal tasks) a hidden test name that also appears in the starting repository, and refuses a bundle for a task still awaiting its author. A hidden root is
outside the repository, so it is backed up by the operator: the hashes detect loss but cannot restore content. The lock pins versions, not file hashes.

## Trap authorship

The pre-registration originally required traps authored by a person outside the Sigma team. On 2026-10-05, before any run, the owner amended it (Deviations in
[`preregistration.md`](preregistration.md)): each trap was written by a fresh independent subagent (`claude-sonnet-5-5`) that was given only
[`trap-author-brief.md`](trap-author-brief.md) and the fixture shape, never the loop, skills or repository. This is **agent authorship, not outside-human authorship**, and the weaker claim is the one made:
the author agent shares a model family with the model under test (home-field bias risk, unmeasured), and both contradiction traps use the same device (a README precedence rule).
`task.json` carries `authorship: independent-agent`, the manifest carries `trap_authorship`, and `check` refuses a ready trap without that label. Each trap was then reviewed by a separate fresh reviewer agent during the goal (its findings were applied: a hidden file renamed, a prompt hint removed, a hidden README check added to trap-2); that review is not committed.

## Freeze (done 2026-10-05; the steps are kept as the record and for a future re-freeze)

1. The author receives only [`trap-author-brief.md`](trap-author-brief.md) (here: an independent agent, labelled as such).
2. For each trap `trap-N`: copy the author's `repo/` over `evals/bench/tasks/trap-N/repo/`; write the author's `prompt.md` text into
   `task.json` `prompt`, set `kind` (`trap-spec-contradiction` or `trap-plan-defect`), `author` (as the author consents to be credited) and `status` to `ready`;
   create `~/.sigma-ops/bench/hidden/trap-N/` with `files/`, `reference/`, `obvious/`, `expected_catch.txt`, `author.json` (`authorship`, handle, date), `hidden.json`
   (`files` and `run` lists), `verify.json` and the `run_hidden.py` that `bench_tasks.py hidden-from-pr` writes (copy it from any existing bundle);
   run `bench_tasks.py seal trap-N`.
3. `bench_tasks.py build-manifest`, then `bench_tasks.py verify --hidden-root ~/.sigma-ops/bench/hidden --external --scratch <empty dir>`; every task must read verified.
4. Zero-spend preflight in the environment the run will use: build it from `evals/bench/tasks/environment.lock`, put it first on the PATH
   that resolves `python3` for the isolation launcher, and run each task's `visible_command` on its pristine tree through that launcher,
   comparing with `visible_expected` (a missing environment would make every arm fail every task alike, which reads as all ties and wastes the paid run).
   Re-read every external task for availability: `materialize` each (digest check) and keep a copy of each fetched tree and of the hidden root with the operator's backups.
5. Set `"frozen": true`, run `bench_tasks.py check --frozen --hidden-root ~/.sigma-ops/bench/hidden` (exit 0), commit the task set.
6. Record in the pre-registration's Deviations section, with the date: `task set frozen at <commit sha>`, and the manifest hash from
   `bench_tasks.py manifest-sha --rev <commit sha>` (the committed bytes). Commit that edit separately, before any arm runs. Record there too that an external task's starting tree is the first parent of the pull request's merge commit, not "that PR's base commit" as the pre-registration's Tasks section words it (see "How candidates were found").

Before the freeze the manifest said `frozen: false`. The flag itself is not read by the harness; what stops a run is that the three trap slots have no hidden bundle (the harness refuses a task whose bundle is missing) and that an external task's `repo/` must first be materialized. `check` refuses a frozen manifest that still has a task awaiting its author.

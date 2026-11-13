# What is new in 1.0.31

This page lists every change in this release. The same entries are in the `1.0.31` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.30](#upgrading-from-1030) if you only want to know what
changes for you.

No new skill. Two optional settings are new, `verify.known_flakes` and `work.checkpoint_prefix`; neither is required.

## Upgrading from 1.0.30

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **A project's recorded known-flake exception is read by the goal run (#2160).** New setting `verify.known_flakes`:
   entries with `test`, `tracking`, `approved_by`, `record`, `expires` and `bases`. When a full `loop.py verify` fails and
   every failing test matches an unexpired entry that covers the goal's recorded base, each is re-run alone on the same
   head; if all pass, the evidence is the distinct state `passed-with-known-flakes` (the exit code stays nonzero).
   `work.py pr` writes it into the PR body, `pr` and `merge` accept it only for the covered base, the post-PR review must
   judge it with a `KNOWN-FLAKES: accept|reject` line, and `/sigma-doctor` lists entries and warns about expired ones. An
   entry never covers the integration branch unless it names it exactly. It fails closed on a malformed setting, an
   unmatched failure, an expired or non-covering entry, a failing, erroring or timed-out solo re-run, and an unreadable
   failure list. It never skips a test and never adds an entry. New script: `known_flakes.py`.
   **Measured:** offline tests, including an end-to-end `loop.py verify` against real pytest solo re-runs; each clause
   was broken on its own and seen red. **Not measured:** a real cloud batch or live pull request; ids are read from
   pytest's summary only, so another runner stays red; how reliably a reviewer follows the `KNOWN-FLAKES` line.
3. **A `--land` run's work survives its container (#2161).** No push in this change is forced. New
   `work.py checkpoint <sdlc> <goal>` pushes the goal branch without a PR, through the gates `pr`'s push is behind plus a
   path scan and a content scan. `work.py start <sdlc> <goal> --land` finds the checkpoint branch on the remote and, if it
   has commits the base lacks and its pull request did not merge, resumes it by merging the base in. A leftover branch it
   cannot resume is left untouched and the work goes on `<branch>-2`, then `-3`. The release audit names the checkpoint
   branch. Optional `work.checkpoint_prefix` keeps checkpoints off branches that push-triggered workflows run on.
   **Measured:** offline tests against a real local bare remote, each clause broken on its own. **Not measured:** a real
   cloud container or a real `--land` run end to end; resume re-enters at P5 IMPLEMENT in a new container because the
   action log is not there; the per-slice resume is not built; an old branch left behind is never cleaned up.
4. **`work.py pr` names its repository and refuses before pushing when it cannot (#2165).** Every PR call carries the
   configured `owner/name`. Before the push it refuses, naming the cause, when the repository cannot be worked out, when the
   push remote names a different one, or when `gh`'s default repository differs from the configured one or cannot be read
   (the remedy named is `gh repo set-default`). **Not measured:** a real fork-default checkout, and behaviour against a
   GitHub Enterprise host. It adds one read per `work.py pr`.
5. **`record review` redaction covers more shapes, and the placeholders are now `[path]` and `[mention]` (#2164).** It
   also redacts `file://` URLs, `//server/share` paths, single-segment paths under well-known roots, `~user/...`, and
   `$HOME`-style and `%USERPROFILE%`-style references. The old `<path>` and `<mention>` were hidden by GitHub markdown as
   HTML tags. A single-segment path outside the listed roots is not redacted.
6. **`/sigma-orchestrate` (#2162).** The planner refuses a wrongly typed `open_prs` with a one-line message and exit 2
   instead of a traceback. The deferred-items parser lists list-shaped lines inside a code fence or a `>` quote as rejected
   with a reason (`check` exits 1); it still does not read them as items. The doc shows the current follow-up command.
7. **The session-start notice is true in a cloud session (#2163).** It no longer says an ignored setup-critical entry is
   "still asked about" where no question is asked.
8. **Test and contract fixes (#2172, #2175).** `setup_wizard.py` and `deferred.py` answer `--help` and `-h` with exit 0
   and a usage text. Sixteen `work.py pr` test fakes were updated to answer the push remote and `gh`'s default
   repository; only the fakes changed.

## Merge-time slips

Two things merged with a check not green. The known-flakes change merged with the per-skill smoke check red for a few
minutes, because the new script had no entry on the `sigma-loop` eval card; an entry was added. The #2165 change broke
16 tests that are outside CI's smoke list, so the required check did not see them; #2172 fixed them.

## Known, not fixed

- Two tests outside CI's smoke list still fail on a clean main on some machines:
  `test_the_retired_skill_pattern_names_exactly_the_shipped_skills` and
  `test_committed_budget_is_fresh_and_the_documented_command_prints_measurement_metadata`.
- One timing test is flaky: `test_the_wait_gesture_is_given_with_the_fallback_and_it_runs`.
- The CI `test` job runs only the smoke list; the full suite was not run for this release.

## Not measured

No real cloud batch of checkpoint and resume or of known-flakes was run. GitHub Enterprise host behaviour of the
explicit-repository calls is unmeasured. Each CHANGELOG entry lists its own gaps.

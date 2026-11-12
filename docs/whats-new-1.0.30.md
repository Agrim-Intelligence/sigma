# What is new in 1.0.30

This page lists every change in this release. The same entries are in the `1.0.30` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.29](#upgrading-from-1029) if you only want to know what
changes for you.

Four pull requests, no new skill. Nothing here adds a config key you must set; one optional key, `followups.mode`, is new.

## Upgrading from 1.0.29

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **The loop passes the merge stop line to `record review`, and the text is redacted before it is posted (#2150,
   #2151).** The loop skill's routing rule now says to hand the stop line to `record review` as its detail. Before the
   detail reaches the issue note, the ledger or the action log, an absolute path (a drive or UNC path, `~/...`, or a
   path of two or more segments) becomes `<path>`, an `@name` or `@org/team` mention becomes `<mention>`, and the
   trigger phrase of anything the blocker scanner would read as a dependency becomes an ellipsis.
   **Measured:** offline, each clause was broken on its own and its own test went red. **Not measured:** a live goal
   ending at a merge stop with a real agent following the new rule.
   **Limits of the redaction:** a `file://` URL path and a `//server/share` path pass through; a single-segment path
   and a `$HOME`-style reference are not redacted. The pattern is a heuristic, not a filter for all free text.
3. **A committed `setup_wizard.dismissed` can no longer silence the four setup-critical checks (#2152).** The checks
   are the project layer, `gh` installed, `gh` authenticated, and a verify command when enforcement is on. A committed
   entry for one is ignored and the question still shows. Other checks stay declinable. Session start now says how many
   checks a committed list declined, and names any entry it ignored.
   **Measured:** offline, unit tests plus the real hook run as a process. **Not measured:** a real cloud session, or a
   live repository with a committed list. **Known small leftover:** the notice says critical entries are "still asked
   about" even in a cloud session, which skips the question.
4. **Follow-ups become a setting, and an approve can name its head (#2153).** `followups.mode` is `"file"` (the default,
   today's behaviour) or `"in-goal"`. Under `in-goal`, `handoff.py track` refuses a non-blocking follow-up and files
   nothing; blockers and work marked `prerequisite`, `cleanup` or `child` are filed as before. A `sigma:approve` marker
   may be followed by a commit id (full, or at least 7 hex digits); when named it must be the pull request's current
   head or that approve does not count, and `post-review` now writes it. A new parser, `deferred.py`, reads the
   deferred-items comment from trusted authors and lists lines it cannot read.
   **Limit, by design:** an approve whose reason starts with an all-hex word of 7 or more characters (`added`, for
   instance) reads as a malformed id and does not count. An approve with no id still compares times.
   **Measured:** offline, 142 new tests across the three areas, each clause broken on its own and seen red.
   **Not measured:** a real batch, a live pull request, or how reliably a session obeys the in-goal refusal.
5. **`/sigma-orchestrate` fixes from a real dry run (#2157).** On 13 Nov the skill's read-only path (steps 1 to 5, up
   to the go-ahead screen) was run against the real repository on a batch of open issues plus a closed one; nothing
   was written. It found defects, fixed here: commands that failed from a worktree (they now take the main checkout's
   `.sdlc` as `"$S"`), an issue already held by an open pull request that closes it, upper-tier and non-goal issues
   reaching the plan, a claim fallback that said nothing about a repository with no area map, a cost line that
   misreported an empty report, and a parser that skipped some list-shaped lines silently (it now lists them).
   **Measured:** offline tests for each clause, and the dry run re-run from a worktree with the three main defects gone.
   **Still NOT exercised:** launching sessions, merge checks, the wake loop, the follow-up filing path and a real batch.

## Known small leftovers

These have no issue numbers yet.

- The planner raises a traceback for a wrongly typed `open_prs` value instead of a clean refusal.
- The deferred-items parser still skips other bullet glyphs, and an item inside a code fence, silently.
- The session-start notice wording above, in a cloud session.

## Not measured

A real batch run of `/sigma-orchestrate`, a live goal following the redaction rule, and a real cloud session with a
committed dismissal list. Each CHANGELOG entry lists its own gaps.

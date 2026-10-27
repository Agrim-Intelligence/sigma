# What is new in 1.0.21

This page lists every change in this release in grouped, readable form, each with its issue number. The same
changes are in the `1.0.21` section of the [CHANGELOG](../CHANGELOG.md) in the order a maintainer reads them.
Start with [Upgrading from 1.0.20](#upgrading-from-1020) if you only want to know what changes for you.

This release is about what a long unattended run costs and how it fails. GitHub calls are counted and the pick makes
fewer of them; a rate-limited read now says so instead of looking like an empty backlog; the resource guard keeps a
lease for a verify whose wrapper was killed and records who held the memory; the doctor reports risky host
configuration; and `/sigma-goal <N> --land` takes one named issue to a merged PR.

Measurements are stated where they exist and marked "not measured" where they do not. Most entries were proven on
fakes and fixtures; none was measured against a real cloud container.

## Upgrading from 1.0.20

Step 1 applies to everyone. The rest apply only if you use the feature named.

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`,
   then remove the file and start the watcher again.
2. **If a script reads the output of `loop.py next` or `next-batch`,** it can now see `DEGRADED <reset-epoch>` where it
   used to see `DONE` after a rate-limited or incomplete read (#2023). `DONE` now means the backlog is empty. The loop,
   the supervisor and `chain.py` already handle the new token.
3. **If you set `model_selection: "auto"`,** plan-review, review and pr-review no longer resolve below
   `model_selection_reviewer_floor` (default `sonnet`) (#2020). New installs ship `auto`; an existing config is
   untouched.
4. **If you run in cloud containers,** you can approve the verify command once per environment with
   `SIGMA_TRUSTED_VERIFY_SHA256` instead of once per checkout (#2066); see
   [cloud environment setup](cloud-sessions.md#cloud-environment-setup).
5. **Provisional defaults to review:** `discovery.auto_unpark.interval_minutes` 10, `gh_quota.rest_reserve_percent` 10,
   and the 30-minute cap on the one wait after `DEGRADED`. Set your own values in `.sdlc/config.json`; the keys are in
   the [configuration reference](configuration.md#quota-sweep-and-reviewer-tier-keys).

What stays unchanged:

- Your `.sdlc/config.json` is never rewritten by an update.
- The pick order is unchanged (a replay test pins it).
- A genuinely empty backlog still prints `DONE`.

## Running one goal unattended

- **`/sigma-goal <N> --land` (#2065).** The loop's own picker scoped to N (`loop.py next --land N`), then the loop's
  per-goal steps and landing path with no gate pauses. Plan-Review, review and verify still bind. It ends merged,
  parked (reason on the issue) or failed (reason given). It touches no other goal, and is refused up front with
  `--feature`, `--label`, `next-batch`, a local-goals backlog, `work.enabled` off or `work.auto_merge: off`.
- **Cloud verify approval (#2066).** `SIGMA_TRUSTED_VERIFY_SHA256` is set once in the environment's settings to the
  value `doctor.py verify-approval .sdlc` prints. It grants `loop.py verify` and `loop.py red` only, and only while
  every command they run is the approved one. Only the operator sets it. Measured on fixtures only.

## Cost and quota

- **GitHub calls counted by component (#2019).** `loop.py gh-calls-report <dir> [--hours N]` and
  `loop.py cost-report <dir>` are read-only. Only counts per hour, component and class are stored. No baseline is
  claimed: it stays "not measured" until a one-hour 5-slot run exists.
- **A pick lists the backlog once per call (#2022).** Across calls each list page is requested with its stored ETag,
  and a 304 reuses the page. On a fake runner with 12 fixture issues, list requests fell from 8 to 4 for one `next` and
  from 24 to 10 for a 4-slot batch. Against the real repository: not measured.
- **`DEGRADED <reset-epoch>` instead of `DONE` (#2023).** The loop waits once, then picks again; a second `DEGRADED`
  stops the run. A REST core reserve defers the auto-unpark and reconcile sweeps. `/sigma-doctor` shows the recorded
  allowance.
- **The auto-unpark sweep (#2021)** stops re-reading comments it already has and runs at most once per interval.
- **Reviewer floor (#2020).** See step 3 above.

## Resource guard and doctor

- **A killed verify keeps its lease while its command runs (#1917).** Measured on macOS with a real wrapper and
  child; not on Linux.
- **Who held the memory (#1914).** An incident file is written when the machine turns critical and when a verify is
  shed: the largest processes, no environment. Measured against a faked process table only.
- **Plugin install scopes and guard presence (#1913).** `/sigma-doctor` prints one row per install scope, warns on two
  scopes at different versions, and fails `resource guard present` on a plugin root without `resource_guard.py`.
- **Risky host configuration (#2032).** `/sigma-doctor` and `/sigma-loop` start report it, read-only.
- **Monitor changes flagged (#1999).** A goal diff that removes or disables a monitor is flagged for the reviewer;
  advisory only.

## Filing caps, hard stops and the unit view

- **A release on an issue Sigma claimed is no longer a hard stop, and the park is typed (#2063).**
- **Filing-cap parks (#2039, #2040, #2058).** The park names the limit and the count; a park caused only by the count
  can wait for the counter (`ai_filed.on_cap: defer`); a re-arm the counter cannot record is put back.
- **A unit view of the dependency map (#2056).** `dag.py report --feature <unit>` and `dag.py html --feature <unit>`.

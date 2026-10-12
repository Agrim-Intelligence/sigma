# D1 evidence: dated read of the open B1 and B8 blockers

Read on 2026-10-13, read-only against GitHub (REST list calls through the `gh` CLI; no label, comment or
state was changed). Class definitions are from `docs/launch/decision-rule.md`: B1 is confirmed data loss
or corruption of user data, git history or GitHub state; B8 is a high or critical security finding on a
supported cell.

## Query

1. `gh issue list --label launch:blocker --state open --limit 200` on the repository: 4 issues.
2. `gh issue list --state open --limit 1000` (657 open issues returned, below the limit), searched on
   title and body for: data loss, corrupt, path traversal, injection, token leak or exposure,
   vulnerability, exfiltrat, destroy, force-push. Each hit was read. The titles of every open S4 review
   (units A01 to A16), S5 and red-team grouped issue were also listed.
3. `docs/launch/` and `docs/launch/evidence/` searched for B1 and B8 mentions, and the issue each names
   was looked up (open or closed).

## Result: count of open B1/B8 = 0 (as of 2026-10-13)

### Open issues carrying `launch:blocker` (4)

None is B1 or B8 and none names a B1 or B8 class in its body. All four are owner actions or the legal
check, not correctness or security findings.

| Issue | Title (short) | Class read |
|---|---|---|
| #473 | Owner-side rehearsal of the public snapshot | not B1, not B8 (owner step) |
| #471 | Owner decisions before the real export | not B1, not B8 (owner step) |
| #397 | Build the public tree, leak-scan it, rehearse the push | not B1, not B8 (tooling and rehearsal; parked) |
| #343 | Legal and naming check | not B1, not B8 (owner decision, D13) |

### Earlier B1/B8 blockers: all closed

The red team, S4 and S5 passes filed these as `launch:blocker`; each is CLOSED, so none counts as open:
B8 #707, #710, #713, #714, #635; B1 #708, #625, #514. (#709 was B4, #589 and #629 were B2.) They are
listed so the count is not read as "none were ever found". Whether each closing fix was verified is not
re-checked here.

### Open issues without the label that were checked against B1/B8

| Issue | Finding | Confirmed B1/B8? Reason |
|---|---|---|
| #214 | `setup.ensure_ignore` writes through a symlinked `.gitignore` or `.git` and blocks on a FIFO | Not confirmed. Written as a design note from plan-review rounds, with no measured reproducer; hostile-checkout only. It describes a write outside the repository, so it is the one open candidate that could be B8 or B1 if reproduced. Recommend the owner decide whether to label it. |
| #653 | `set_local_only` traceback; atomic config write has no `fsync` | Not B1. The issue says no write and no data loss, and calls the fsync gap a documented ceiling. |
| #591 | work.py: non-atomic `_save` can lose the PR link and counters; `_run` has no timeout | Not B1. Loses Sigma's own work-record fields, not user data, git history or GitHub state; filed `launch:next`. Resiliency defects. |
| #601 | Secret-scan residuals | Not B8. The issue states none is a new bypass in normal use; each is a limit or a lever. |
| #711 | D10 red-team grouped non-blocker findings (R04 to R12 and others) | Not confirmed as B1-B8. The issue states none is B1-B8 on a supported cell in the default configuration. Reach conditions are noted there; R07 (`finish --force` removes a committed worktree path) and R10 (repo-config SMTP `pass_env`) are the ones to re-read if the owner wants a second opinion. |
| #511 | Blocker scan reads markers inside code fences and HTML comments | Not confirmed. The issue itself says it is not a confirmed B1-B8; it becomes a B8 candidate only for a repository accepting outsider comments. |
| #216 | An ingest pass with a non-zero exit is recorded as a success | Not B1 or B8. A false green liveness signal; nothing is lost or exposed. |
| #108, #36, #14, #515 | exit code on broken pipe; review-cycle reset; base re-attach; unsampled shared paths | Not B1 or B8 (liveness, anti-thrash accounting, narrow raise, unsampled follow-up). |
| #802 | Refuse unattended runs when egress is unrestricted | A proposal and gap, not a confirmed finding. |
| #639, #638, #637, #636, #634, #633, #632, #631, #630, #628, #627, #626, #592, #590, #715 | Grouped S4/S5 review findings | All carry `launch:next`. Their titles and the grouped-issue summaries name no B1 or B8; the individual items were not re-read here. |

## What this read does not establish

- The label is applied by people, so an unfiled finding is invisible to this query. The text search
  matches only the phrases listed above; a finding worded differently would be missed. The count is 0
  against that search, not a proof of absence.
- The line-by-line review was a single shallow pass with unmeasured recall (see
  `docs/launch/evidence/review-s4-c3faf6f23e12.md`).
- #214 and the R07 and R10 items of #711 were read from issue text only, not reproduced in this record.

# Worktrees, PRs and merging

How each goal becomes a branch and a pull request, and when it merges. Back to the [README](../README.md).

## One worktree, one branch, one PR per goal (on by default)

`work: {"enabled": true}` (the value in a config scaffolded by `/sigma-init`; with the key absent the code reads `work.enabled` as off, and `require_review` as `off`) — this is the only feature that lets the loop commit at all. Set it to
`false` explicitly to keep the loop writing to git exactly as little as before this shipped.

```json
"work": { "enabled": true, "base": "", "remote": "origin", "auto_merge": "off" }
```

> **Adopting into an existing repo? Two things to know.**
> - **With `work` off, a completed goal produces no branch/commit/PR** — its changes land only in your
>   working tree. The issue still closes and the ledger still says `done`, so nothing *looks* wrong;
>   `loop.py start` and `record … done` now print a heads-up, and `/sigma-doctor` states it plainly, but
>   if you want a PR per goal, turn `work` on (or run `/sigma-setup`, which does).
> - **If your repo already gates source edits with its own `PreToolUse` hook** (e.g. a plan-freshness
>   check that denies edits to `.py`/`.ts`/… without a recent plan doc), Sigma's Implement phase
>   edits go through the same tool calls a human's would, so that hook applies to them too. Sigma
>   writes its own plan to `.sdlc/plans/` (or an issue comment), not to whatever path your hook checks —
>   so make sure whatever the hook expects is satisfied, or a source-code goal can be denied
>   mid-Implement with no Sigma-side signal that a *host* hook was the cause.

**Why a worktree and not a branch.** The moment the loop touches git, an in-place `checkout -b`
breaks two things silently: it moves the working copy out from under whatever you left open, and —
because `sigma-init` has you commit `.sdlc/goals/` — every branch switch rewrites the backlog the loop
is in the middle of reading. A worktree avoids both. Your checkout never moves and never changes
branch; bookkeeping keeps resolving to the one real `.sdlc`.

**Cutting fresh IS the goal-start rebase.** The worktree is created from `<remote>/<base>` at the
moment the goal starts, so there is nothing to replay. That matters more than it sounds: a real
`git rebase` that hits a conflict at 3am leaves a half-applied tree, and every later goal in that run
then builds on it. Two OTHER rebases can still run later, both sharing that same conflict-safe
guarantee (abort clean, never leave the tree wedged) — the proactive one immediately below, and the
reactive one in the merge gate further down, when GitHub itself reports the PR `BEHIND`.

**`verify_command` runs in the worktree — but not before checking the worktree is still current.**
`loop.py verify` compares the worktree against `<remote>/<base>` first (`git fetch` +
`git rev-list --count`, cheap next to the suite it gates): if nothing has moved, this is a silent
no-op, exactly as before. If the worktree has fallen behind, it auto-rebases via the same primitive
the merge gate uses, and says so loudly on stderr — a worktree that predates a fix landed elsewhere
used to run its whole suite against stale code and get misdiagnosed as broken before
a human noticed and re-ran by hand. A rebase that cannot apply cleanly refuses instead: the suite
never runs, `loop.py verify` exits `4`, and no evidence is written, so `record done` is refused the
same way an absent verify always has been. `verify_command` itself still runs in the worktree — it
has to, the main checkout doesn't contain the change — and a proving command resolved against the
wrong (or stale) tree is a green that proves nothing, which `record done` would otherwise accept.

### The merge gate: may we, should we, and is anything enforcing it

**1. May we merge at all?** Permission first, and never a config question:

- a **fork PR**, or a repo where you have only `READ`/`TRIAGE` → the loop opens the PR and stops:
  `PR #123 opened — read access on this repo; a maintainer merges.` That's the open-source path,
  where the PR *is* the deliverable. It records **`review`** (awaiting merge), not a park and not `done` — `record done` is
  refused while the PR is unmerged, and the goal becomes `done` once the upstream merge lands. If rights can't be determined, it fails **closed**.

**2. Should we merge?** Three legs, all required:

| Leg | Source | What it catches |
|---|---|---|
| fresh local evidence | `loop.py verify`, this run | a green from yesterday, or none at all |
| `mergeable` | GitHub | textual conflicts with the base |
| `mergeStateStatus == CLEAN` | GitHub | failing required checks, missing reviews, a stale branch |

A `BEHIND` branch is rebased and re-checked, up to 3 times (`BEHIND_REBASES`), then parked. Everything else goes to the existing review queue —
a failing check first triggers bounded CI-fix cycles and records `failed` (needs a fix) once they are exhausted (`work.max_review_cycles`), a conflict records `parked` (needs a decision). No new
human-intervention path.

**3. Is anything actually enforcing the answer?** That's `work.auto_merge`:

| Value | Behavior |
|---|---|
| `"off"` | **Default.** Never merges; always leaves the PR. |
| `"protected"` | Merges only where the base branch genuinely **requires** checks or reviews — delegating the gate to GitHub. On an unprotected branch it opens the PR and says why it stopped. |
| `"always"` | Merges whenever clean+safe, protected or not, and says plainly that local verify was the only gate. |

Legacy booleans still parse (`false`→off, `true`→always).

The point is autonomy proportional to the guardrails that exist — because **`CLEAN` is only worth what
your branch protection is worth.** A repo can run CI on every PR and *require* none of it, in which
case GitHub reports `CLEAN` simply because it was never asked to object. So the question asked is
`repos/{owner}/{repo}/branches/{base}/protection` — a 404 means nothing is enforced — and **not**
whether a check happened to run.

Then it lands the PR directly over REST (`PUT pulls/N/merge`, carrying the vetted head sha; `gh pr merge` is only a fallback on a primary rate limit). GitHub's own `--auto` is armed only when a required
check has not answered yet (and the repository allows auto-merge); anything else parks with the reason.

**A real review, after the PR — `work.require_review` (on by default).** Without it the
gate only respects a review your *base branch's protection* requires — so a human's ad-hoc **"Request
changes" on an unprotected base** (the common shape for a `staging`/`dev` branch) is invisible to it,
and an unattended `auto_merge` lands straight over it. Self-review before the PR is not enough on its
own. `work.require_review` adds a real review gate, **independent of branch protection**:

| Value | Behavior |
|---|---|
| `"off"` | No review gate — auto-merge only respects reviews branch protection requires. |
| `"changes"` | **Default.** **Parks** on a `CHANGES_REQUESTED` review or an unresolved review thread. |
| `"approval"` | The above, **and** requires an approval before merging — parks until the PR is `sigma:approve`d (or formally `APPROVED`). |

It reads the PR's real review state (`reviewDecision`, review threads, and `sigma:` comment markers)
and **parks** if the PR isn't cleared — nothing auto-merges past unaddressed feedback. Fail-closed: an
unreadable comment list or review list parks the merge; only the unresolved-thread count fails open.

**The loop reviews its own PR — no human in the loop.** After it opens the PR, the loop runs a **fresh,
adversarial pass over the real mergeable diff** (a review *after* the PR, distinct from the pre-PR
self-review, best as a subagent with fresh context) and posts the verdict itself with **`work.py
post-review`**. Each call must carry the generation-bound `--evidence` receipt created by the PR-review
protocol; a bare `--verdict` is refused. `--verdict approve` writes `sigma:approve` and the gate
merges it; `--verdict block` writes `sigma:block` and sends it back — the loop **fixes the issues in the worktree, re-verifies, and
re-reviews** until clean. That review→fix→re-review loop can't run forever: `post-review` **counts the
block cycles and hard-caps them at `work.max_review_cycles` (default 3)** — once hit, it parks the goal
for a human instead of churning. That's the fully autonomous *review-after-the-PR* cycle: `require_review`
is the READ side of the gate, `post-review` is the WRITE side. (`/sigma-loop` drives this — see its SKILL.)

**Why a comment and not the Approve button.** GitHub structurally forbids approving or requesting-changes
on your *own* PR — and the loop opens every PR under its own account — so the formal review API can never
be the loop's channel. Plain comments have no such restriction: **`sigma:approve`** clears a merge,
**`sigma:block`** stops it, **`sigma:unblock`** clears a block (latest wins; a block overrides an
approve). The same markers let a **human** review a loop PR when they want to, and a formal `APPROVE` or an
unresolved review thread still counts whenever a second identity leaves one.

**Who may comment a marker.** A `sigma:approve`, `sigma:block` or `sigma:unblock` comment counts only
when GitHub reports the commenter's `authorAssociation` as `OWNER`, `MEMBER` or `COLLABORATOR`. On a public
repository anyone can comment, so any other commenter (including an absent or unknown association) is
ignored, and the gate prints one stderr line per ignored marker naming the commenter and association; it
is never counted as an approval, a block or a clear. If the PR's comments cannot be read at all, the
gate parks instead of merging. To let someone approve, add them as a collaborator (or have them leave a
formal review, which GitHub already restricts to write access); the ignored comment stays on the PR and
the next gate run simply re-reads (association is read at gate time, so a later change in someone's role
applies to their older comments too). The account the loop posts as must itself count: a machine user needs
at least collaborator access, and a GitHub App or `GITHUB_TOKEN` identity that GitHub reports as anything
else would have every loop `sigma:approve` ignored, parking each goal under `"approval"` (and its `sigma:block` ignored
under `"changes"`). A marker comment with no `authorAssociation` field at all (an old `gh`) parks the merge
instead of being guessed at. A minimised trusted comment still counts. A stranger's formal
"Request changes" review is not filtered by this rule and can still park a merge on a public repository
(a delay, never an approval). `MEMBER` is any organisation member and can be broader than write
access, so this is not a substitute for branch protection.

**Markers in issue comments follow the same rule.** The `sigma:` markers the loop reads from an issue's comments (`keep-parked`, `dismissed-finding`, `decompose-filed`, `design-filed` and the feature-unit flags) count only from an `OWNER`, `MEMBER` or `COLLABORATOR` commenter; any other commenter's marker is ignored with one stderr line, and its prose still reads as ordinary text. A marker comment with no `authorAssociation` at all makes the strict reads (decompose, design, feature flags) fail the way an unreadable timeline does. The limit: if the account the loop posts as is reported as anything else (a bot token), the loop's own idempotency markers are ignored too, so it can repeat a flag comment on every pick or file a second decomposition; use a user or machine account with collaborator access.

**What `"approval"` does NOT verify.** The comment channel exists because GitHub can't tell the loop's
own account apart from a reviewer's — which cuts both ways: it also can't tell a genuinely independent
`sigma:approve` from the SAME session that wrote the diff posting one about its own work. The gate
only checks the commenter's relationship to the repository (below), not independence — a same-author
`sigma:approve` clears the merge exactly as a different account's would (it has to: that's the whole
reason the comment channel exists). What changed
is visibility, not the gate: the loop now warns loudly when a same-author comment is the ONLY approval
signal, so the case is at least seen — never silently accepted as equivalent to independent review. That's
not a gap you can code around here: the "fresh subagent with fresh context" review above is the discipline that
makes an approval real, and it's discipline the tool cannot verify from the outside — it depends on
actually spawning a separate context before posting the verdict, not doing both in the same one.
`auto_merge: "always"` **plus** `require_review: "approval"` **with no branch protection on the base**
means, if that discipline slips even once, NOTHING independent stands between an unattended merge and
your base branch — confirmed live: a deployment of this plugin self-reviewed and self-merged a PR (zero
GitHub-native reviews, the only signal a self-posted `sigma:approve`) straight onto an unprotected
`main`. `/sigma-doctor` flags this exact combination. The fix that doesn't depend on discipline: add branch
protection on your base requiring an approval from a second identity — a formal `APPROVE` still can't
come from the loop's own account, so a real branch rule is the one channel a same-session comment can
never satisfy.

Two remaining costs. A fresh worktree has no `node_modules`/`.venv`/build cache, so a heavy
`verify_command` pays that per goal — part of why this ships off. **And because the worktree has none
of your installed dependencies, your `verify_command`'s interpreter/binary path must resolve
independent of the working directory** — a bare relative `.venv/bin/python3` or `node_modules/.bin/…`
fails `exit=127` on the first real per-goal run. Use an absolute interpreter path, a venv activated on
`PATH`, or a wrapper script (`/sigma-doctor` flags a relative one for you). And `work.py commit` stages
with `git add -A`, so **anything your `verify_command` leaves behind must be gitignored** or it rides
along into the PR (`.coverage`, `.pytest_cache/`, build output).

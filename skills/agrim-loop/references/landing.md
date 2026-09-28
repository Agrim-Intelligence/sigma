# Landing the work

## Context-efficient inspection

Locate before opening: use `rg --files` for paths and `rg -n` for symbols or claims.
Read only the relevant lines with a bounded range; expand outward only when the
boundary is unclear. Do not dump whole files or broad search output into context.
For bulky tests or diagnostics, save full output to a scratch file, then inspect
its exit status and focused matches or a bounded tail. Do not hide failures, and
keep secrets out of the scratch file. This is guidance, not a measured savings claim.

The detail behind step 6 of [`../SKILL.md`](../SKILL.md): the required pattern for any
long-running blocking call, fresh verify evidence, committing without a bare `git`, the
secret refusal, the post-PR review cycle, `work.py merge`'s patience budget, and how to
read every line it can return.

---

**REQUIRED PATTERN — blocking on `loop.py verify`, `work.py merge`, a dispatched review-gate
subagent, or any other long-running call a phase must wait on before continuing: issue it as ONE
foreground call, in this same turn, then READ THE LITERAL RESPONSE before deciding what happened.
Never assume a timeout means "killed," and never assume it means "still running" either — this
environment has been directly observed doing BOTH for the identical test, in the same session
(#1889): sometimes the response names a tracked background id and a real notification follows
later; sometimes it is a plain kill (exit 143) with nothing left running and nothing to wait for.
Read which one you actually got, every time — including on a session where you've seen one
behavior before; it is not guaranteed to repeat.**

- **Response names a tracked id** ("moved to the background (ID: ...), you will be notified", or
  your own explicit background request)? That work may still be alive. Wait on / check THAT id.
  Do not re-invoke the same command — for `loop.py verify` (the adopting project's own arbitrary
  command, no concurrency guard of any kind) a second concurrent copy is a real correctness risk,
  not just wasted work.
- **Response is a plain error/exit naming no id at all** (e.g. "Command timed out after Ns", exit
  143)? Nothing is left running. A clean re-run is correct for a command whose own documentation
  establishes that — as `loop.py verify` and `work.py merge`'s paragraphs below both do.
- **Never** shell-background it yourself (`&`, `nohup`) and end your turn believing a notification
  will resume you. That path detaches for real, with no id and no notification, ever — a
  confirmed, repeated failure (#1889): a phase-subagent backgrounds a long command, ends its turn
  on a "wait" backed by nothing, and the goal silently stalls until a human notices.

```
# REQUIRED — one foreground call, same turn, then read what it actually says:
python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" verify .sdlc "$goal"

# NEVER — detaches for real; no id, no notification, ever:
python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" verify .sdlc "$goal" &
nohup python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" verify .sdlc "$goal" > verify.log 2>&1 &
# ...then ending your turn on "I'll wait for it to finish" — there is nothing to wait on.
```

**The same call shape covers a dispatched review-gate subagent** (plan-review, pre-PR/code-review,
post-PR/pr-review — see the maker≠checker rule, step 3): your very next action depends on its
verdict and nothing else is happening meanwhile, so dispatch it foreground/blocking (e.g. Claude
Code's `Agent`/Task tool `run_in_background: false`) — no separate "wait" step, and no response to
read either way: the call itself does not return until the review is done.

**Honest limit**: this is self-applied, not externally enforced (per this project's own "a claim
in prose is not evidence" rule) — you are the one judging whether the response named a real id.
Get it wrong in either direction and it costs something: assuming a kill when work is still alive
races a live process; assuming survival when it was actually killed just wastes a turn on a
notification that was never coming. Read the response; don't assume either way.

With `config.verify.enforce` on, a `done` needs FRESH machine evidence first —
`python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" verify .sdlc "$goal"` runs the goal's proving
command (frontmatter `verify_command`, else `verify.command`) and records it; `record done` is
REFUSED without a passing, this-run verify. **Before that command ever runs, `loop.py verify`
now checks the worktree isn't stale (#1890)** — a worktree that has drifted behind its own base
auto-rebases via the same `rebase()` `work.py merge` already trusts, and says so on stderr; one
that cannot apply cleanly REFUSES outright rather than run the suite against code already known
to be behind — exit `4`, and no evidence is written, so the refusal above already covers it with
nothing further for you to check. (This exit `4` is `loop.py verify`'s own, unrelated to the
different exit `4` `loop.py record` uses further down this same file for its own PR-unmerged
refusal — two separate subcommands, two separate exit-code spaces, never confused for one
another because you never branch on the bare number, only on which command produced it.)
**This call has no timeout of its own — size your tool-call timeout to the PROJECT's verify
command, not to a Sigma default.** `loop.py verify` runs that command through a bare
`subprocess.run(..., cwd=root)` with no `timeout=` argument, so the call's wall time is exactly
however long the adopting project's own proving command takes (a full test suite can legitimately
run many minutes) — Sigma imposes no ceiling of its own here, unlike the bounded budgets
below. If the host cuts the call short anyway, no evidence was written (`verify_goal` only
persists it after the subprocess returns), so nothing is left ambiguous: `record done` still
REFUSES exactly as if `verify` had never run — just re-run `loop.py verify` to completion.
**The evidence now records WHAT it verified, not just when and by whom (#1897)** — the goal's own
change, as a digest of every path it touches relative to its fork point — and `record done` and
`work.py merge` both re-check it against the worktree as it stands. So the "re-run `loop.py
verify`" half of the blocked-review cycle below is enforced, not merely instructed: **edit the
worktree after a green verify and the next `record done` is REFUSED, naming the paths that
moved.** Committing that same content is not a change (`work.py commit` is the documented next
step, and a clean rebase replays the same diff), so the ordinary path is untouched; what does
trip it is a real edit, a deletion, a mode flip — or a rebase that had to three-way-merge into a
file the goal itself touched, which is a genuine "the suite never ran on this" and costs one
re-verify. Sigma's own `.sdlc/` directory is excluded, so the plan you copy onto the branch
two paragraphs below never counts as a change.
**Landing the work** (`config.work.enabled` on) — once verify is green, and never with a bare
`git` command (the loop has no general git tool on purpose, and no dispatched phase subagent gets
one either — see the no-phase-commits rule, step 3):
`work.py commit .sdlc "$goal" --message "<type: what changed>"` → `work.py pr .sdlc "$goal"`.
**Put the plan on the branch before that first `commit`** — copy `.sdlc/plans/<goal-stem>.md` from
the main checkout to the SAME relative path inside the worktree (`commit`'s `git add -A` runs in
the worktree and cannot see the main checkout's copy, which is why plans were silently missing
from PRs — #1548). Both copies are wanted, not one: the reviewer BRIEF reads the main checkout's,
the PR's reviewer reads the branch's. `pr` refuses to push without it, naming the file and the
branch — unless the repo's own `.gitignore` covers the plan path, in which case that repo has
decided plans are not committed there and nothing is asked (never force-add past an ignore rule).
**`commit` REFUSES when `git add -A` staged a secret-shaped file** (#1555) — `.env` and its
variants, `*.pem`/`*.key`, `id_rsa` and friends, `credentials.json`, service-account JSON. It
names every offending path, takes those paths back out of the index, and leaves every file on
disk untouched. **Do exactly what the refusal prints, character for character** — it gives you a
ready-made `.gitignore` line (already escaped and anchored; the raw path often is NOT a valid
pattern), and for a file already tracked here, the `git rm --cached` that must come with it.
Then re-run `commit`. Never `git add -f` past it, and never delete the operator's file to get
moving. For a fixture, a certificate, or a file this repo commits ON PURPOSE (a Symfony-style
non-secret `.env`), the answer is the EXACT path in `work.allow_secret_paths` in
`.sdlc/config.json` — not untracking a file the project means to keep. **Deleting a secret is
never refused**: the goal that removes a leaked credential is the one this exists to enable. A
repo that already ignores its `.env` never sees any of it — `git add -A` honours `.gitignore`,
which is why `/agrim-doctor` reports the still-unignored ones before the loop's first commit.

**Then REVIEW the PR you just opened, if `config.work.require_review` is set** — a real review AFTER
the PR. Self-review before the PR is never enough; this is a **fresh, adversarial pass over the PR's
real, mergeable diff** (post-commit, post-CI). **Resolve the mechanism, never assert it** — `python3
"${CLAUDE_SKILL_DIR}/scripts/reviewer.py" resolve .sdlc` and use what it names (`subagent` /
`process` / `command`; only a machine where it returns `inline` reviews inline, and that verdict
must say so). The maker never clears its own PR. Fed the reviewer brief for this gate:
`python3 "${CLAUDE_SKILL_DIR}/scripts/review_context.py" brief .sdlc "$goal" --for pr-review
--artifact <PR#>` (see the maker≠checker rule, step 3), running `/code-review` on the PR, else
`/agrim-review` in diff mode. That subagent decides the verdict below. **Create a generation and
bind its verdict to evidence before posting:** `python3 "${CLAUDE_SKILL_DIR}/scripts/review_context.py"
brief .sdlc "$goal" --for pr-review --artifact "PR#$pr" --output
".sdlc/state/review-manifests/$goal.json"`; then load the immutable paths with `source <(python3
"${CLAUDE_SKILL_DIR}/scripts/work.py" review-paths .sdlc "$goal" --manifest
".sdlc/state/review-manifests/$goal.json" --format sh)`. Run `python3
"${CLAUDE_SKILL_DIR}/scripts/reviewer.py" resolve .sdlc > "$REVIEW_RESOLUTION"`. For a resolved
`process` or `command`, run `python3 "${CLAUDE_SKILL_DIR}/scripts/work.py" run-resolved-review .sdlc
"$goal" --manifest "$REVIEW_MANIFEST" --resolution "$REVIEW_RESOLUTION"`; it re-resolves the route
at execution time and refuses if it changed. For a resolved `subagent`, hand only `$REVIEW_BRIEF` to
a fresh host subagent, then run `python3 "${CLAUDE_SKILL_DIR}/scripts/work.py" record-subagent-review
.sdlc "$goal" --manifest "$REVIEW_MANIFEST" --resolution "$REVIEW_RESOLUTION" --verdict
approve|block|unblock --reason "<reviewer result>"`. Finally run `python3 "${CLAUDE_SKILL_DIR}/scripts/work.py"
review-evidence .sdlc "$goal" --manifest "$REVIEW_MANIFEST" --review-result "$REVIEW_RESULT"`.
**No human approves — the loop reviews and clears its own PR:**
- **No blocking issues** → `work.py post-review .sdlc "$goal" --evidence "$REVIEW_EVIDENCE" --verdict approve` (posts `sigma:approve`).
- **Blocking issues** → `work.py post-review .sdlc "$goal" --evidence "$REVIEW_EVIDENCE" --verdict block --reason "<the issues>"`, then
  **fix them in the worktree, dispatched to a fresh subagent** (back to Implement — never a resume
  of the subagent whose diff got blocked; the fresh-redispatch rule, step 3, names the exact
  hand-off: the block reason, the plan path, the worktree path, nothing else), re-run `loop.py
  verify`, then `work.py commit` **AND `work.py pr` — the push is not optional**, and **re-review
  with an equally fresh reviewer subagent** (its own prior verdict plus a freshly re-run
  `review_context.py brief` — never the earlier reviewer's transcript). Repeat the generation,
  resolution, review-result, and evidence sequence, then post `--evidence "$REVIEW_EVIDENCE"
  --verdict approve`.
  **`commit` is LOCAL; only `pr` pushes.** Skipping it leaves the PR head at the pre-fix commit,
  so GitHub's checks and reviews all pass — correctly — about code nobody approved, and an armed
  auto-merge squashes that. This shipped defects to a protected `main` three times before
  `gate()` grew a STALE HEAD refusal; the guard is the backstop, this line is the intent. **The cycle is hard-capped:** `post-review` counts the
  block cycles and, once they hit `work.max_review_cycles` (default **3**), returns a `PARK: …` line
  instead of asking for another fix — the review genuinely didn't converge, so `record parked "<why>"`
  for a human. You never have to count the cycles yourself; the cap is enforced in code.

To clear a prior loop block without changing the reviewed revision, publish a **fresh** generation
for that same revision — re-run the `brief --output` gesture above; every publication is a new
generation — have the resolved reviewer return `unblock`, then post that generation's evidence with
`--verdict unblock`. The block's own evidence cannot be reused: its result is create-once and says
`block`. The writer emits `sigma:unblock` and records a typed `review_posted` journal event
before it reports success.

On an **inline** route (`review.independent: false`, or a host with no session marker and no
`review.command`, where the operator has chosen to let the maker review its own work) there is no
reviewer to run, so the verdict is passed explicitly: `python3 "${CLAUDE_SKILL_DIR}/scripts/work.py" run-resolved-review .sdlc "$goal"
--manifest "$REVIEW_MANIFEST" --resolution "$REVIEW_RESOLUTION" --verdict approve|block|unblock
--reason "<result>"`. The route is re-resolved first, those flags are refused on every other route,
and a verdict written into `resolution.json` is refused everywhere.

If `post-review` returns `PARK: remote comment outcome ambiguous`, or asks you to reconcile before a
retry, GitHub may already have accepted the comment. Never post again. Run `python3 "${CLAUDE_SKILL_DIR}/scripts/work.py"
reconcile-review-post .sdlc "$goal" --evidence "$REVIEW_EVIDENCE"`: it only looks the comment up
by its evidence marker and repairs the local record, and it never creates a comment.

If reconcile still returns `PARK: remote comment outcome ambiguous` — the comment genuinely never
reached GitHub (a network failure before any response, not a lost acknowledgement) — that evidence
is abandoned, not retried: post-review will never post it, because the request receipt it already
wrote is immutable. Publish a **fresh** generation for the same, unchanged revision (re-run the
`brief --output` gesture; every publication is new, per the same mechanism `unblock` above uses),
have the resolved reviewer return its verdict again, and post that generation's evidence instead.

Then `work.py merge .sdlc "$goal"`. **This call can block for minutes — raise your tool-call
timeout before running it, not after it times out** (the REQUIRED PATTERN above `loop.py verify`
applies here unchanged: read the literal response, never shell-background this and wait on
nothing). Its internal gate (`gate()`) carries a
deliberate patience budget: `PENDING_ATTEMPTS` (10) re-reads at `PENDING_INTERVAL` (45s) apart —
450s, roughly **7.5 minutes** — spent re-polling a required check that has attached but not yet
answered, before gate() gives up and reports it still pending. On the BEHIND-rebase path (`main`
moving under the PR mid-merge) that same ~7.5min budget can be spent again each time a rebase
attaches a fresh head, up to `BEHIND_REBASES` (3) times before the race arms or parks — roughly
**22.5 minutes** worst case, on top of the separate, smaller BEHIND poll-with-backoff
(`BEHIND_ATTEMPTS` × `BEHIND_BACKOFF`, capped by `BEHIND_BACKOFF_MAX`) that watches each rebased
head settle. The safe minimum is that full worst case, **~1,350,000ms (22.5 minutes)** — a common
default like 600000ms (10 minutes) covers only the ordinary pending path and is NOT enough once a
BEHIND-rebase is in play. `gate()` only READS GitHub's state while it polls; it mutates nothing.
So if the host cuts the call short anyway, nothing was recorded as merged or armed — just re-run
`work.py merge .sdlc "$goal"` (idempotent: it re-reads live state, not a stale local guess), or
read `gh pr view <PR> --json mergeable,mergeStateStatus,statusCheckRollup` yourself first if you
want to see exactly where GitHub's own read stood before deciding.

The gate is clean **and** safe: it needs THIS run's passing verify
evidence *and* GitHub's `mergeable` + `mergeStateStatus CLEAN`, **plus — with `require_review` on — the
review verdict you just posted** (it will not merge a PR that isn't `sigma:approve`d, or that has a
`sigma:block` / an unresolved thread). It rebases once if the PR is `BEHIND`. It then **lands the PR
directly** with `gh pr merge` — arming GitHub's own `--auto` is reserved for the one case a direct merge
would be refused right now (a required check that hasn't answered yet, and only when the repo's own
`allow_auto_merge` setting actually permits arming); every other clean-and-safe or arm-worthy case merges
directly instead of waiting on an async arm, which is what makes landing possible at all on a repo with
`allow_auto_merge` disabled outright.
**Read its first word and record accordingly — never merge past it by hand:**
- `PARK: …` → `record parked "<that reason>"` (a conflict, a stale read, no evidence, or a direct merge
  GitHub genuinely refused — e.g. a check that flipped between gate() and the merge attempt). A
  **failing required check** is a fix, not a decision → `record failed "<the check>"` instead — a check
  that is merely still `pending` (not yet answered) is a different case, covered below, never this one.
- `PR #N opened — …` → **`record done`**. This is the open-source path: a fork PR, or a repo you
  only have read access to, can never be merged by you, so the PR *is* the deliverable and the loop
  has done everything it can. It is not a park — nothing about it wants a human here.
- `PR #N merged (<method>) — …` → **`record done`**. The common terminal-success shape now: a
  clean-and-safe (or arm-worthy-but-not-required-to-wait) PR landed directly, right here, in this call
  — not merely armed for later. This is what most successful merges return.
  This line also sometimes ends `… — #N was still open after the merge; sent a close request, which
  succeeded`: right after the landing, the kit reads the ISSUE's own state directly (#2615) — never a
  base, recorded or live — and sends a close request whenever GitHub's own keyword did not get there
  first. The common trigger is a goal cut onto `feature/<unit>` (GitHub auto-closes a linked issue only
  from a merge into the DEFAULT branch, so that PR body deliberately never claims one — it says `Refs
  #N` and names what will close it instead), but the check itself is the same either way: whatever the
  base, if the issue is still open after the merge, Sigma sends a close request — which is what lets
  a dependent goal declaring `Blocked by: #N` become pickable. The line never says the request is what
  closed the issue: GitHub's own keyword processing, or a person, could still close the issue in the
  narrow window between the read and the request, in which case the request above is a harmless no-op
  the line cannot tell apart from a real close — so it reports only what it observed and did. Nothing
  for you to do; `record done` as usual, and `source.complete()` finds it already closed. If instead it
  ends `… — but could not close #N (…)`, the merge still LANDED, and `record done` remains the right
  next step: `source.complete()` retries this exact close whenever the issue is still open, and parks
  the goal instead of silently losing the record if that retry fails too. Only if it keeps failing does
  the issue need closing by hand — until then, the goals blocked by it stay blocked.
- `auto-merge armed …` / `clean and safe …` → `record done`. A PR still `PENDING` on required
  checks no longer blocks the arm — arming exists for exactly this one case now, not as the general
  path — and `--auto` re-checks atomically at GitHub's own merge time, so the message reads
  `auto-merge armed … — checks still pending, trusting GitHub's own re-check …`.
  Under `auto_merge: off` (the shipped default) or `protected` against a branch that turns out
  unguarded, `merge()` never merges or arms at all, so that same still-pending case instead OPENS with
  `required checks still pending …` — the same words the failing-check case above uses — but always
  ENDS `… auto_merge is off, leaving PR #N for a human` or `… merging it is yours to make …`. Trust
  the ending, not the opening, when the two collide: both endings are `record done` too —
  **except (#1689) `… merging it is yours to make` on a goal whose base is a `feature/<unit>`
  branch.** There `record done` REFUSES: a unit branch is never protected by design (§13), so
  that ending is not the general "a human merges main by hand" case this bullet otherwise means —
  it is guaranteed on every goal a unit ever runs. Park the goal instead; see the refusal's own
  text below for why retrying `work.py merge` will not help.

**`record done` itself now refuses, in code, when it isn't true** (#254): with `config.work.enabled`
on, a goal whose PR is open, unmerged, not armed, and whose repo genuinely has merge rights AND a
policy that would have armed it, cannot be recorded `done` — `loop.py record` checks this the same
way `verify.enforce` is checked above, REFUSED with exit 4 if it fails. This exists because the
rule just above is prose an agent reads, and #144/PR #252 is exactly a case of that prose not
being followed: `done` was recorded while checks were still pending and nothing was armed, and 605
lines sat stranded on a branch while the board read done. The refusal fails OPEN on anything it
cannot verify (no `gh`, no network, a fork, read-only access, no PR on record, `auto_merge: off`,
or `protected` against an unprotected NON-unit branch all still allow `done`) — only a
*positively confirmed* open/unmerged/unarmed/mergeable PR refuses. **One exception, #1689:**
`protected` against an unprotected **unit** branch (`feature/<name>`) does NOT allow `done` — a
unit branch is never protected by design (§13), so `merge()` will never arm or merge that PR on
this call or a later retry, and closing the issue anyway would release every dependent declaring
`Blocked by: #N` against work that never landed. **If you hit this refusal, do not retry
`work.py merge` expecting a different outcome — it will repeat the same non-arming result.**
`record parked "<the refusal's own text>"` instead; the goal genuinely needs a human to merge
PR #N, or the repo should run `auto_merge: always` while it uses units.

`work.auto_merge` is `off` | `protected` | `always`, default **off**. `protected` merges only where
the base branch genuinely REQUIRES checks or reviews — autonomy proportional to the guardrails that
actually exist. **`record done` releases the checkout itself — do not run `work.py finish`.**
It calls `finish` for you, on the `done` outcome only, after every other piece of the goal's
bookkeeping has landed; a failure there is reported and never costs the goal its `done`. This
used to be your job, and that was the bug: a turn that ended between `record done` and the
command leaked the checkout permanently, because nothing ever revisited a closed goal.
The release deliberately KEEPS a worktree that still holds uncommitted work, so a parked goal
stays intact for whoever picks it up. It also refuses (#1202), separately, when the goal's PR is
still open, unmerged, AND not armed to merge — exactly the state `auto_merge: off` leaves a
`done` goal in — naming the PR, because `state/work/<goal>.json` is the ONLY place that PR
number lives and every later `merge` / `rebase` / `post-review` needs the checkout it also
points at. An `auto-merge armed …` PR (above) releases normally — `--auto` is GitHub's own
commitment to land it, so there is nothing left to sever. **A `loop: kept …` line on stderr is
that refusal, and it is not yours to override:** merge the unarmed PR first and the next
`finish` clears it, or, only once you are certain the PR pointer is expendable, run
`work.py finish .sdlc "$goal" --force` by hand.
**If `record done` prints a `sigma: unit-completion:` line, read it and do nothing about it.**
It means every issue carrying one `feature:<name>` label is now closed, so that **unit** of work
looks finished. It is a signal for a **person**, not a task for you: Sigma does not decide a
unit is done — "no open issues" is not "the work is done" — so **never** open the
`feature/<name>` → integration-branch pull request yourself, and **never** merge a feature
branch, whatever the line says about the other repo in a cross-repo unit. Raising and reviewing
that PR is the human's call. A project that wants the PR pre-opened sets
`work.unit_completion: "draft-pr"`, and even then it is opened as a **draft** and parked — you do
not ready it. `work.unit_completion: "off"` silences the line entirely.
Declared a pipeline (`.sdlc/pipeline.json`)? Run the
bidirectional report card between goals — `python3 "${CLAUDE_SKILL_DIR}/scripts/pipeline.py" card
.sdlc` — and treat its findings as inputs, not gates. `pipeline.py propose .sdlc` turns the card's
FAILING signals into `proposed` goal files (with the failing check wired as `verify_command`);
the loop NEVER runs a `proposed` goal — a human promotes it to `pending` first.
With `config.ledger.enabled` on, the claim and the outcome are mirrored to the **team ledger**
automatically — never record those by hand. Record anything a TEAMMATE needs to see with
`python3 "${CLAUDE_SKILL_DIR}/scripts/ledger.py" append .sdlc note "$goal" --to <login> --why "…"`.
`loop.py next` prints a **LEDGER INBOX** block on stderr when a teammate needs you: read it,
answer each item with `handoff.py ack .sdlc --issue <n> --state accepted|deferred|declined|resolved`
or, for a local/issue-less hand-off, `handoff.py ack .sdlc --goal <goal> --area <area> --state ...`;
the inbox spells out the exact command per item. Take a `P0` next rather than interrupting
the goal you are in.

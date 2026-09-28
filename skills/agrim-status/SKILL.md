---
name: agrim-status
description: Show backlog, current iteration, and review-queue status. Use for loop status or /agrim-status.
allowed-tools: Bash(python3 *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-status

Detailed selection triggers: [selection](references/selection.md).

Run `python3 "${CLAUDE_SKILL_DIR}/scripts/status.py" .sdlc` and relay the one-line summary. If the
review queue needs attention, offer to walk the user through `.sdlc/state/review-queue.md`.

In **github mode** the counts come from the live board — open issues by label (`sdlc:parked` /
`sdlc:in-progress` / `sdlc:goal`), scoped to your `assignee` — not the (empty-in-github-mode) local
goals dir, so `parked` reflects every parked issue, not just the current run's. Needs `gh`; fail-open
to zeros if it's unreachable. Local mode counts `.sdlc/goals/` exactly as before.

If the line reports an **alignment check due**, offer to run `/agrim-align` — enough goals have shipped
since the last cumulative-drift audit for a trajectory to be readable. It's advisory, so declining is
fine; the count keeps rising and the offer returns. (Silent on projects with no north-star.)

If the line names a **merge queue** recommendation (#976, github mode only), relay it as-is — it means
the base branch is strict-protected AND recent merges show a concurrent-landing burst, the read-only
shape a GitHub merge queue would fix. It is pure advice: nothing here enables a merge queue, requests
`administration` scope, or touches branch protection. Enabling one needs a human with admin access and
a supported plan (Team/Enterprise for private repos). Silent whenever the shape isn't present.

If the user, in response to that recommendation, asks you to actually enable the merge queue: this is
an ADMIN-scoped, irreversible-class action (it can flip repo auto-merge on and creates a branch-
protection ruleset) — never run it as part of the ordinary loop cycle, and never run it just because
the recommendation fired. Use `python3 "${CLAUDE_SKILL_DIR}/scripts/merge_queue_enable.py" plan
<owner/repo> [--branch main]` first (100% read-only — it only reports what would change) and show the
operator its output verbatim. Only after a repo admin gives an explicit, unambiguous "yes" in chat to
what `plan` reported should you run `apply <owner/repo> --yes-enable-merge-queue [--branch main]
[--merge-method SQUASH]` — the exact `--yes-enable-merge-queue` flag is required verbatim, or the tool
refuses and makes no `gh` call at all. Relay `apply`'s JSON result as-is, including any `"partial":
true` state (it means auto-merge was enabled but the ruleset was not, or the two could not be verified
afterward — tell the operator plainly, do not paper over it) and any `plan_gate`/`permission` error
(the repo's plan doesn't support a merge queue, or the token lacks admin rights — point the operator at
the fix named in the error, do not retry automatically).

# Follow-ups stay in their goal

**Why.** A batch drain used to spawn a new goal for every follow-up a session found. Each new goal pays for
its own worktree, review and — the expensive part — its own full verify, and follow-ups spawn follow-ups.
The rule here is **one full verify per goal**: follow-up work either joins its goal before that verify, or
waits and is grouped with others so that many follow-ups share one verify.

A defect in the goal's own change is not a follow-up. It is fixed in the same PR, as always.

## The standing decision

Paste this, verbatim, into every launch prompt (`<batch>` filled in):

```
Standing decision by the person who started this batch (sigma-orchestrate, batch <batch>), on follow-up
work - work you find that is outside this goal's own change (a defect in this goal's own change is fixed
as usual). For this goal it replaces the project's own rule for filing follow-up issues:
- Do not open a new issue for a follow-up: no `handoff.py track`, no `gh issue create`. When a phase
  subagent or reviewer proposes filing one, convert it under these rules before anything is filed.
- Before this goal's first green full verify (`loop.py verify`), add it to this goal's plan as a new slice,
  then re-run the phases in order from plan-review - a fresh plan-review recorded for the amended plan,
  then implement, review and retrospective, each as a fresh subagent - so one verify covers everything.
  At most 2 added slices per goal; beyond that, treat it as found late.
- After the first green full verify, or past the cap: do not implement it now. Post ONE comment on this
  goal's issue containing the line `<!-- sigma-orchestrate:deferred -->` and one line per item:
  `- [area:<area>] <what> - <why> (files: <paths>)`. Edit that same comment if you find more.
  Exception: when a post-PR `sigma:block` already forces a fix and a new verify, one small same-area item
  may ride along as a slice (a fresh plan-review first, then a fresh review and retrospective after it);
  it counts against the cap of 2.
```

Nothing else about follow-ups goes in the prompt. A blocker in another area's code is still handed off by
Sigma's own rules; the session already knows them.

## Collecting deferred items

Each wake, for every goal that ended since the last wake, read its issue comments
(`gh api repos/<o>/<r>/issues/<N>/comments --paginate`) and find the marker
`sigma-orchestrate:deferred` anywhere in a comment body (Sigma may wrap the text). Parse the item lines and
store them in `goals.json` under that goal as `"deferred": [{"area", "what", "why", "files"}]`. Only a
merged goal's items go to a clean-up goal; a stopped goal's items go to the final report, because the work
they follow up on never landed.

## Closing a wave: one clean-up goal per unit and area

A wave has ended when every goal of that planned wave is merged, stopped, stuck or held — none is queued,
running or awaiting merge. Held and stuck goals cannot launch without a person, so they do not hold a wave
open.

When wave *k* ends, take the deferred items of wave *k*'s goals and group them by (the source goal's
feature unit, item area). For each group, file one clean-up goal through Sigma, from one of the group's
source goals so the unit is inherited:

```bash
python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/handoff.py" track .sdlc <source goal number> --area <area> \
  --why "Deferred follow-ups from sigma-orchestrate batch <batch>, wave <k>" \
  --queue queued --blocks no --assignee same-area|cross-area \
  --title "Clean-up: deferred follow-ups from <batch> wave <k> (<area>)" --body-file <file>
```

- **Body**: the line `<!-- sigma-orchestrate:cleanup -->`, then the items in the sessions' own words, each
  with its source goal (`from #N`) and files. Copy no goal text: Sigma's duplicate check compares text, and a
  copied body can match a closed source goal.
- **Assignee**: `same-area` when CODEOWNERS gives that area to the account running the batch, else
  `cross-area` (routed to the area's owner).
- **Rotate the filing source goal** within a group: Sigma caps filings per goal (`ai_filed.max_per_goal`),
  and earlier filings from that goal count.
- **Duplicate verdict**: if it prints `matched existing #N` (Sigma judged it a duplicate), nothing was
  filed. Put the items on the needs-you list with that reference.
- **Triage**: with Sigma's triage on (the default), a `queued` filing is usually armed at filing time
  (`sdlc:goal`, often P3), so another loop on the same account may take it first. That is acceptable: track
  it either way. A triage park (deny-list, cap) needs a person: needs-you list.
- Add the batch label, add the issue to `goals.json` with `"cleanup": true`, `blockers: []`, `base` from its
  inherited unit, claims from the items' files, and let `waves.py ready` launch it. It gets the same launch
  prompt.

**No follow-ups of follow-ups.** Deferred items found by a clean-up goal are never grouped again. They go to
the final report for the person.

## The safety net: a run that filed anyway

Expect some leaks: phase subagents are briefed by Sigma, not by this prompt. Each wake, list issues with
`sdlc:followup` created after the batch started that reference a batch goal.

- **A leak that blocks its parent** (filed `--blocks yes`; the parent parked or blocked naming it) is a
  prerequisite, not a follow-up. Keep it open, give it step 2's classification, add it to the batch as a
  goal ahead of its parent (the parent's `blockers` gains it), and relaunch the parent only after the leak
  has landed on the parent's base, unparking the parent through Sigma ([run](run.md#unblocking)). Never close a
  blocking leak to free its parent: Sigma would read the closed blocker as done.
- **Any other leak** is a deferred item of its parent: add it to the parent's `deferred` list. Once the
  clean-up goal that carries it is filed, close the leak with a comment pointing at the clean-up goal. If
  another run already holds the leak, or closing it is refused, leave it open and report it.

Report every leak by parent goal: it shows where the standing decision is not reaching the phase subagents.

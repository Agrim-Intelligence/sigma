# Follow-ups stay in their goal

**Why.** A follow-up that spawns its own goal pays for a new worktree, review and full verify, and follow-ups spawn
follow-ups. The rule is **one full verify per goal**, decided by code, not by a prompt: `handoff.py track` routes
every filing through one function (`followup_route.route`) and the goal's own plan takes the work.

A defect in the goal's own change is not a follow-up. It is fixed in the same PR, as always.

## The rule

Found work that belongs to the goal's feature joins that goal's plan as a slice:

```bash
python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/work.py" add-slice "$S" <goal> --title "<title>" --criteria-file <file> [--test <selector>]
```

The verb appends `## Added slice <n>: <title>` to the plan, counts it in a code-owned record
(`.sdlc/state/followups/<goal>.json`), and extends the acceptance criteria through `acceptance.py extend` (it refuses,
naming that verb, while it is absent). Then record a fresh plan-review of the new plan and re-run implement, review
and retrospective in order, so one verify covers everything. A fresh review is forced only while
`gates.plan_review` is on, and a stale verify is refused only while `verify.enforce` is on; both are off by default.

A find is filed as a **separate goal** in exactly two cases, and `track` records which in the issue body
(`<!-- sigma:followup-reason:<reason> -->`) and as a comment on the parent:

- `different-feature`: the item declares another open, registered unit (a `Feature:` marker or `feature:` label).
- `slice-cap`: the goal already holds `followups.max_added_slices` added slices (default 10).

No flag, environment variable or config key names a reason. `handoff.py track` refuses everything else, exit 3,
for every `--kind` and `--blocks` value, and prints the add-slice gesture.

## The setting

`followups.mode` in `.sdlc/config.json` ([configuration](../../../docs/configuration.md)) defaults to `in-goal`; read it
with `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/handoff.py" followup-mode "$S"` (line 1 the mode, line 2
`max_added_slices <n>`). `file` is the opt-out: `track` files as it did before routing, with no reason and no
comment. It is repo-wide, so it turns routing off for every goal. This skill never edits config.

Open conflict, owner ruling needed: decompose children and chain prerequisites are same-feature filings, so `track`
refuses them under `in-goal`. Until the owner rules, the interim lever is `followups.mode: file`.

## What the orchestrator does

Nothing about follow-ups goes in a launch prompt, and nothing is collected from comments. A goal that ended
because a find hit the cap or another feature shows up as an ordinary filed goal carrying a reason: add it to the
batch like any other. `/sigma-doctor` lists follow-ups filed with no recorded reason.

\`log.py::read_goal()\` returns \`[]\` both when a goal genuinely has no log entries AND when the goal string itself is rejected by \`_unsafe_goal_reason()\` (e.g. a path-traversal-shaped goal). \`goal_view()\`/\`status()\` then print the generic hint:

> no log entries for \<goal\> (config needs "action_log": {"enabled": true} — see /sigma-log)

even when the real reason is "this goal was refused as unsafe" — which is misleading: it tells the operator to check their config when the actual problem is the goal argument itself.

**Fix:** in \`goal_view()\` (and \`status()\` where applicable), check \`_unsafe_goal_reason(_stem(goal))\` before falling back to the generic "no entries" hint, and print a distinct, accurate message for the unsafe-goal case.

**Test:** call \`goal_view()\` with a \`../\`-bearing goal and assert the unsafe-goal-specific message appears, not the generic config hint.

model:bulk — a small, mechanical branch-the-message fix in one already-understood function; no design work.

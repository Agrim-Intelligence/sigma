# #2027 delta plan review — SOUND-WITH-REFINEMENTS (on plan sha b3ba98f6…)

The plan file stays unchanged. Apply these four refinements during implementation:

1. **Prose accuracy.** "3x start context" leaves out the 45% cap and the explicit key. In the Task 6
   prose rewrites, use "goal and context ceilings" or "context threshold" instead. Examples:
   "stop at `HANDOFF` at the goal or context threshold" and "(20 goals or the context threshold
   by default)". The prose tests check that each site names context and drops the 20-goal-only
   wording; they must accept this phrasing and must not require the literal "3x". The budget must
   still stay near net-zero.
2. **Known ceiling in the D7 comment.** A session that starts at or above its threshold gets exactly
   one goal, then HANDOFF, every session. This happens with an explicit value below the measured
   first pick, or with a cap below a big repo's start. It is bounded but expensive, and the comment
   says so. A doctor warning for an explicit value below the measured first pick is a follow-up the
   coordinator files; it is not in this slice.
3. **Step 18's last item is a budget confirmation, not a control.** The step restores the
   `stopping.md` pointer and runs `evals/phase_context_budget.py`. Record whatever result it gives
   (over or within the headroom), and do not list it among the breaks seen red.
4. **What the cap means with the env var.** The constant's comment says the cap is 45% of whatever
   `CLAUDE_CODE_AUTO_COMPACT_WINDOW` names, whether that is the model window or the compaction
   point. Either reading is the conservative one.

Implementation is on hold until the coordinator says #2031 has merged.

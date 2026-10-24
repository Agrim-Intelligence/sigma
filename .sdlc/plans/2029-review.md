# Plan-review findings for #2029 (delta verdict SOUND-WITH-REFINEMENTS)

Recorded against plan sha256 `54565b13cc23e631e0bb1c8edcc0da5da7b18fe2ac473ed02f9dc758f296625f`
(revision 2). The plan file stays unedited. These refinements are applied during Implement, and
implementation waits until #2027 has merged (plan Task 0).

## Required

1. **R1 — the brief span covers the whole `open decisions:` line (plan D9, Task 2).** Review-queue
   headings are goal slugs and can match `_LIMIT` (e.g. `0042-handle-rate-limit-retries.md`), so a
   span that stops at the `open decisions:` anchor would leave that slug for the probe to find.
   - The pattern ends `^\s*open decisions:[^\n]*` and allows `^\s*` before both anchors, because a
     relayed block may be indented or fenced. Use `[^\n]*` for the tail, never `.*`: under `re.S`,
     `.*` runs to the end of the text and would swallow a real usage-limit line printed after the
     brief.
   - Shape: `re.compile(r"^\s*SUCCESSOR BRIEF:.*?^\s*open decisions:[^\n]*", re.S | re.M)`. The
     opening `.*?` stays non-greedy.
   - Test: the open-decisions line in
     `test_a_rate_limit_slot_title_inside_the_brief_does_not_sleep_a_handoff` also carries a
     limit-shaped slug (`parked goals ever queued (review-queue.md, not pruned):
     0042-handle-rate-limit-retries.md`).
   - Controls, each seen red then reverted:
     - end the pattern at the anchor (drop `[^\n]*`): that test goes red;
     - replace `[^\n]*` with `.*`: `test_a_real_usage_limit_line_outside_the_brief_still_sleeps`
       goes red;
     - drop the `\s*` before either anchor and run an indented variant
       (`test_an_indented_relayed_brief_is_still_stripped`, a new case): it goes red.

2. **R2 — call slice 2's resolver with the handoff sub-dict, and derive `origin` here (plan D6,
   Task 3).**
   - Call `_context_threshold(config.get("handoff"), baseline)`. It takes the `handoff` sub-dict,
     like `_handoff_ceiling` does, not the whole config.
   - `_meter_line` derives `origin` itself, checking in this order:
     - `handoff disabled` when `handoff.enabled` is false;
     - `handoff.after_context_tokens` when `_explicit_context_tokens(handoff)` is not None;
     - `derived from --baseline` when a baseline was passed;
     - `no baseline` when there is neither an explicit value nor a baseline. The threshold is then
       `none` and the verdict `unknown`.
   - Tests pin all four strings exactly:
     - `test_meter_handoff_disabled_prints_none`;
     - `test_meter_threshold_from_explicit_config`;
     - `test_meter_threshold_derived_from_baseline`;
     - `test_meter_without_a_baseline_says_no_baseline`, a new case.
   - Controls, each seen red then reverted:
     - pass the whole config to the resolver: the explicit-config and derived tests go red;
     - swap the explicit and baseline branches: the explicit-config test, run with `--baseline`
       also passed, goes red;
     - drop the disabled branch: the disabled test goes red.
   - If #2027's merged names or signature differ from `_context_threshold` /
     `_explicit_context_tokens`, adapt only the call site, keep the four origin strings, and say so
     in the PR.

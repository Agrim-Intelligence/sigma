# In brief: lean goals (revision 9)

**The code search is incomplete.** One small area is still unchecked: how the dependency-map screens display the "between slices" label.

**What changed since revision 6.** The owner decided that a goal's progress lives on GitHub, so any machine can pick it up. The seventh review found that the owning machine had no way to start slice 2, that nothing could actually release a dead machine's goal, and that two code paths could still close the issue early. All three are fixed.

- **Progress is recorded on GitHub.**
  - When the plan is approved, Sigma posts the goal's evidence on the issue: research done, which plan file and its fingerprint, the review verdict, and the slice list. Plan files are already kept in git, so another machine checks the fingerprint before trusting the verdict.
  - A slice counts as done when its pull request is merged and carries its review marker.
- **Any machine can resume.** It starts at the first slice without a merged pull request, reusing an open one rather than making a duplicate. It does not redo research, planning or plan review. The final "done" check accepts work recorded on GitHub from other machines.
- **One owner at a time.**
  - Every claim or takeover writes a numbered "claim epoch" on the issue. Before starting a slice or touching a pull request, a machine checks that the epoch is still its own; if it isn't, it stops cleanly.
  - `resume --force` lets a person hand a goal to another machine (for example, after reassigning the issue).
  - `resume --restart` throws away unfinished slices and starts the goal over. Merged slices stay merged.
- **Dead machines are handled without the ledger.** If a goal shows no slice activity for 72 hours, a sweep parks it as "stalled", and any new-version machine resumes it. Starting a slice counts as activity, so a machine that is actively working is never mistaken for a dead one. Old-version machines never pick a stalled or in-progress lean goal.
- **The issue closes at the right time.** Neither of the two places that can close it does so while any slice is unmerged, including work found late. The owner's own machine picks up slice 2 as soon as slice 1 merges, even if a person merged it.
- **Instruction space.** Plan review frees about 500 tokens, not 700, because two paragraphs that tests pin must stay put. Three pinned tests change on purpose, and they are named.
- **Rollback.** You can't just pin the old version mid-goal: the old version would close an issue after its first slice. Finish or park in-flight lean goals first. The release notes will say so.

**Revision 8 fixes (eighth review).**
- **A machine that has been replaced stops writing anything.** It checks the claim epoch before every GitHub write and before finishing a goal, including the background pass that watches for merges. When it sees it has been replaced, it quietly drops its own local copy. Before this fix, a replaced machine could park or close a goal its new owner was working on.
- **A half-finished slice can move machines.** Each slice's failing-test proof and review verdicts are posted on its pull request, so another machine can finish it. If they are missing, the new machine re-checks that the tests fail against the code before the slice.
- **The plan is checked against GitHub, not a local file.** The new machine reads the plan from the pull request or the shared branch and checks its fingerprint. If the old machine died before ever pushing the plan, Sigma says to restart the goal.
- **Only trusted notes count.** Notes from people without repository access are ignored, using a filter Sigma already has.
- **Reviews of found work travel too.** The short review of a newly found slice is posted with the slice list.
- **No other path hands a lean goal to old machines.** A blocked or expired lean goal is parked the same way as a stalled one. Any new-version machine that sees the posted evidence resumes the goal instead of starting over.

**Revision 9 fixes (ninth review).**
- **A resuming machine gets everything the checks need.** Besides the plan, it fetches the acceptance criteria and a copy of the plan-review approval from GitHub. It stores them in its own working area, never in the main checkout. Without this, a resumed goal would be refused at the pull request on the usual setup, where verification is enforced. The tests now run every scenario with every optional check switched on, and with the ledger both on and off.
- **Restarts can't confuse old and new slices.** Every slice name and note carries a plan "generation" number, and only the current generation counts. A restart also clears its own waiting flag first, so it can't park its own goal.
- **Work found after the issue closes is deferred.** That includes work found during the retrospective. It never becomes a slice of a closed issue.
- **Evidence is posted at the first push.** Sigma's first push happens when the pull request is opened, so that is when the evidence is posted. It is posted again on any re-approval, and the newest version wins.
- **Stalled parks stay parked.** A stalled goal's park carries Sigma's "keep parked" marker and its own reason, so no automatic unpark frees it.
- **Waiting is not stalling.** A pull request that has been reviewed and is waiting for a person to merge it is never treated as stalled. A replaced machine keeps its local branch, so nothing unpushed is lost.
- **Small goals.** They post no evidence, so moving one to another machine means restarting it. That is cheap, because a small goal is one slice.
- **Action log on by default.** A missing action-log setting now counts as on for lean goals, so no config edit is needed. Repos that never had it on will see a log file per goal grow on disk; there is no clean-up rule yet.

**Honest limits.**
- Anyone who can write to the repository can fake these GitHub notes, which is the same trust level labels have today. A faked plan approval only works if the plan file's fingerprint matches.
- Two machines could both pass the epoch check at the same instant. Fixed branch names and GitHub's own refusal of a second pull request from the same branch contain this.
- Two paths can still hand a lean goal to an old-version machine:
  - an old machine with the ledger on releases an expired claim;
  - a person runs the usual unpark command on a stalled lean goal.

  In either case the old machine finishes the rest as one pull request and closes the issue. So the release notes say: upgrade every machine before using the ledger with lean goals, and use `loop.py resume`, not unpark, on a stalled lean goal. The park note says the same.

**What a person still has to decide.**
1. Urgent unrelated defects are filed at medium priority (P2), not top priority (P0). Is that acceptable?

**The plan: one parent ticket, 14 parts, on the `lean-goals` feature branch.**

| Part | What | Waits for | Size |
| --- | --- | --- | --- |
| 1 | Upgrade tests from realistic current-version state | — | medium |
| 2 | Record goal size and the new-route mark at pick | 1 | medium |
| 3 | Sigma prints the next step; instruction sections moved out | 2 | medium |
| 4 | One pull request per slice; correct issue closing; owner continues to the next slice | 3 | large (riskiest) |
| 5a | Progress on GitHub; resume anywhere with everything the checks need | 4 | large |
| 5b | Claim epoch before every write; force and restart with generations; stall sweep; no hand-off to old machines | 5a | large |
| 6 | Slice list v2, with found work recorded on the issue | 5b | medium |
| 7 | Per-slice records, posted on the pull request; test review before code | 6 | large |
| 8 | Filing rules for found work | 6 | large |
| 9 | Measure real pull-request size | 1 | medium |
| 10 | Skip steps for small goals and design children | 7 and 9 | large |
| 11 | "Did it stay small?" check; status views | 8 and 10 | medium |
| 12 | Catch-up sweep for filings that bypass the rules | 8 | medium |
| 13 | Before/after report, cleanup list, rules and release notes, landing prep | 11 and 12 | medium |

**Measured vs not.**
- **Measured:** follow-up growth, how much instruction space each section frees, and how fast main changes.
- **Not measured:** small-goal savings, the cost of the new reviews, the cost of checking the epoch on GitHub, and how many conflicts the landing will hit.

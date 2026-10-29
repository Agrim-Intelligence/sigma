# Plan 2086: plan-review findings and how the plan changes

## Plan-review refinements (verdict SOUND-WITH-REFINEMENTS, folded in)
1. The read is bounded: `wait-call` wraps the one REST read in a 10 s alarm; a timeout reads as `unknown`. A test blocks
   the read and asserts the answer comes back in time with the `unknown` line.
2. The open line says "open at this read", and names the window it cannot close: if the kill fell inside the merge
   call itself, read the PR again before re-running. landing.md keeps its `record review` rule for that case and says a
   re-run that parks on a PR that has since merged must be re-read before anything is recorded.
3. The re-run advice fires only when the receipt's detail is `runner pid N gone`. `deadline passed`, `malformed running
   receipt` and `the runner raised` name the PR state and tell the caller to check by hand (a live runner must not get
   a second concurrent merge). A parametrized test covers the details.
4. The end to end test reaps the killed child (`wait()`) before `wait-call`, and proves the child was inside the pending
   poll from the fake's own log (a status read with no merge PUT yet), not from timing.
5. The printed agent-start gesture uses `--pid "$PPID"`; landing.md says it must be the caller's own long-lived process
   and names `agent-reclaim` for exit 2. The test runs the printed string through a shell.
6. Added controls: the one-call count, the line order and exit code, the unchanged stderr for non-open states, and the
   gesture copied out of landing.md.
7. The fake is stateful across both runs (file backed); the kill is asserted to leave zero merge PUTs.
8. The new claims keep "unmeasured on real hosts"; proposal item 3 is stated as deferred.

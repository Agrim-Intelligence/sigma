# A merge runner killed mid-merge (#2086)

The recovery behind the `wait-call` bullet in [landing](landing.md). It lives here because `landing.md` loads into the orchestrator by default and its size is budgeted
(`evals/phase_context_budget.py`).

A cloud session's worker can restart while `work.py merge` runs (exit `137`, SIGKILL). The runner's receipt is left
`running` with a dead pid, so `wait-call` reports `FAILED not_completed: runner pid N gone`. `gate()` only READS
GitHub while it polls a pending required check, so a kill there merges nothing and the merge is safe to re-run.

The recovery, in this order (`$PPID` is the new session's own long-lived process, the value every `loop.py` call takes):

```
python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" wait-call "<abs .sdlc>" "$goal" merge
python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" agent-start .sdlc "$goal" --pid "$PPID"
python3 "${CLAUDE_SKILL_DIR}/scripts/work.py" merge .sdlc "$goal"
python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" record .sdlc "$goal" done
```

- **What `wait-call` adds.** One line under `FAILED`, from ONE REST read of the PR (never GraphQL, cut off after
  10 s; a stalled read says `could not read`). For an **open and unmerged** PR whose receipt says `runner pid N gone`
  it names the two gestures and "Do not record the goal failed". Exit stays `1`.
- **Why re-register.** The agent marker holds the old session's pid, now dead; a reclaim reads that as an abandoned
  claim and may take it. `agent-start --pid "$PPID"` writes the new pid and replaces a dead marker. If it exits `2`, a
  live different task holds the marker; `loop.py agent-reclaim .sdlc "$goal"` clears it only when that task is
  confirmed dead.
- **Then read `work.py merge`'s first word as always** (landing.md): `PR #N merged …` is the only line that earns
  `record done`. The re-run starts the gate from the beginning, so it spends its patience budget again.
- **Only an open PR earns the gestures.** `already merged` means `record done` and never merge again; `closed without
  merging` means decide by hand; `could not read` means read the PR yourself. When the receipt's reason is not
  `runner pid N gone` (a deadline passed, a malformed receipt, a runner that raised) the line names the PR state
  but gives no gestures, because that runner may still be alive and a second concurrent merge is not advice.
- **The window one read cannot close.** If the kill fell inside the merge call itself, the PUT may land after the
  read. Landing.md's rule stands: read the PR again, and if it merged run `record done`. If the re-run parks, read
  the PR before you `record parked`: a PR that has since merged is `record done`.
- **Not built:** binding the agent marker to the session id instead of a pid, so it survives a restart without
  re-registering (#2086 proposal item 3, a design change).
- **UNMEASURED on real hosts:** no real cloud container, worker restart or GitHub has run this. Measured: a real
  SIGKILL of a real `work.py merge` during pending-check polling against a fake GitHub behind a real `gh` executable
  on PATH, then these gestures landing the PR with one merge PUT and one issue close.

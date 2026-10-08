# Codex autonomy parity — manual plan for #823

## Outcome and boundaries

An operator can opt into unattended Sigma work on Codex and get a fresh Codex
session for each supervised run, independent review, bounded recovery, and
Codex-only model routing. Claude's existing launch command, model gate, and
review route retain their current behavior. This issue is implemented manually;
the Sigma loop is not used to execute it.

## Design

1. Add a small Codex runtime resolver used by the supervisor. Detect an active
   Codex task from its session marker or an explicit `SIGMA_HOST=codex` for a
   headless launcher. Keep the existing Claude default when neither signal is
   present. Resolve the `codex` executable and query `codex plugin list --json`
   for exactly one enabled `sigmaloop@sigmaloop` entry at or above the plugin
   floor. The parent resolves its skill directory mechanically: a local-source
   entry supplies its path; a Git-source entry supplies a version and commit
   SHA, so search only the current `CODEX_HOME/plugins/cache/*/sigmaloop/<version>`
   candidates, require exactly one whose Git HEAD matches that SHA and whose
   manifest name/version match the inventory, then require a readable
   `skills/sigma-loop/SKILL.md`. This is a runtime discovery against the actual
   enabled install, not a path borrowed from another machine. Missing, stale,
   ambiguous, or unreadable candidates refuse before launching an agent.
   Test a nondefault `CODEX_HOME`, a Git-source entry, and a zero-exit child
   failure saying the skill is unavailable; the parent must not classify it as
   progress or relaunch.
2. Keep the Claude supervisor command and `SIGMA_CLAUDE_CMD` unchanged. For
   Codex, launch `codex exec --approve-for-me --cd <repo>` with a prompt that
   gives the child the parent-verified absolute skill path, requires it to read
   that skill, and pick from the real `.sdlc` state. Permit a separate
   `SIGMA_CODEX_CMD` override; the plugin preflight still applies.
   Acquire one OS-backed, nonblocking lock for the *entire* Codex supervisor
   lifetime before any admission check; a second Codex supervisor refuses.
   Under that lock, check the existing session registry before each launch;
   while another session is live, wait and report it. Preserve Claude's
   supervisor behavior, so a simultaneous new Claude launch outside this lock
   is an explicitly residual cross-host race, not a guaranteed exclusion.
   A Codex child inherits no Claude host markers; mixed markers without an
   explicit host override are refused at entry. Reuse the existing stop file,
   run-id, bounded relaunch, and log heartbeat. For Codex, use the child exit
   code as well as its output: nonzero never reads as success, a recognizable
   limit uses the existing reset/backoff classifier, and other nonzero exits
   back off. A failed preflight stops loudly.
3. Add `review.host_overrides.codex` as an optional per-host choice. The
   resolver applies it only when a Codex marker is present; a Claude session
   continues to use `review.host`. Set the local Sigma checkout's Codex
   override to `codex` after the merged code is installed. The independent
   Codex reviewer remains a fresh read-only process and its verdict remains
   explicitly unverified until a live gate run proves it.
4. Add `model_selection_host_overrides.codex: auto|off` to the model gate. A
   Codex session may opt into model/effort routing without changing the repo's
   Claude model setting. Apply the effective value consistently at prediction
   (`predict.py` goal and step), pick-time recording (`loop.py`), and escalation
   (`tier_escalation.py` via its loop caller). Existing Codex model ID allowlists
   and tier ceilings still apply. The local checkout opts in after merge.
5. Document the Codex migration: inspect current workers, install the plugin
   into Codex only, refresh the Codex rules block, set the two host overrides,
   run a bounded foreground smoke test, then opt into one supervisor. Never
   modify Claude plugin settings or terminate unrelated workers.

## Tests and controls

- Write tests first for host detection, JSON plugin inventory and exact
  installed-path validation, missing/disabled/duplicate plugin refusal,
  zero-exit missing-skill output, Codex command construction,
  supervisor lock contention and crash release, session admission, mixed
  marker handling, and unchanged Claude command behavior. Run each red test
  against the old code before implementing it. The lock control races two
  real subprocesses against one temporary `.sdlc` directory and proves that
  only one launches its fake Codex worker.
- Test Codex-only review and model routing with both host markers separately;
  prove a Claude marker still selects the old path. Exercise every model gate
  listed in item 4. Remove each new branch in a disposable copy or monkeypatch
  seam and observe a red control.
- Run focused tests, the project's full pytest suite, structural checks, and
  GitHub CI. Capture a real successful Codex CLI output/exit fixture with a
  bounded read-only smoke test; test synthesized nonzero and limit tails
  against the Codex classifier. Actual quota exhaustion is not induced in a
  test, so reset-time behavior is reported as unmeasured on Codex until a real
  occurrence. A full goal-to-merge claim also needs a separately observed
  Codex run; report that limit if it is not completed here.

## Manual sequence

Plan review by an agent given only this file and repository access; write and
observe failing tests; implement; implementation review by a new agent given
only the diff and repository access; fix findings; verify; install/migrate the
local Codex adapter; open PR; wait for CI; merge; confirm the issue closes.

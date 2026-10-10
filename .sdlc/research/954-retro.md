# #954 P7 RETRO: work.py start refuses on a dirty root that its own registry sync just wrote

**Goal**: #954 · **Phase**: P7 RETRO (independent, autonomous) · **Date**: 2026-10-10
**Inputs read**: the brief (`brief-954-retro.md`); the acceptance record `.sdlc/acceptance/954.md` (AC-1..AC-5, the
authoritative P1 intent); the plan `.sdlc/plans/954.md` (Revision 2); `.sdlc/research/954-implement-evidence.md`;
the issue timeline (`gh issue view 954 --comments`: research, plan, plan_review FIX-FIRST, implement, review APPROVE);
and the uncommitted diff plus the 4 untracked files in `.sdlc/work/954` (HEAD `66b1f1b`).
**North-star**: `north_star.py` printed `absent`, so this is a drop-in project. **No Direction check was run.**
**KG note (§4)**: skipped. `knowledge_graph.enabled` is `false` in `.sdlc/config.json`.
**Audit-trail notes (§3)**: these are **not posted**. The orchestrator's hard rules allow this file only, so
`loop.py note` was not run. The notes are in §6, ready for the orchestrator to post.

---

## 1. Grade: **partial**

The code that was built matches the intent. AC-1 to AC-4 are realised in the diff, and the evidence is strong:
red-first tests, 19 deliberate breakages each seen red, and an independent APPROVE. **AC-5 is not met.** The
configured verify command has never passed on this tree, `loop.py verify` has never seen a green, and nothing
has landed. The CHANGELOG conflicts with origin/main (#969), and a GitHub ruleset rejected the `sdlc/954` push
(GH013). It is not **diverged**, because what was built is what was asked for.

### Each criterion

| AC | Grade | Evidence / gap |
|---|---|---|
| **AC-1**: the 3-step repro passes; its test was seen red on the old code | **achieved** (in the diff) | Tests 1 and 2 (start and resume) and test 23 (the *documented* relative-`.sdlc` CLI gesture through `work.main`). Each got an assertion red in R-OLD, recorded by `loop.py verify` (16/16 RED nodes). On the final bytes, plain pytest gives 24 passed. Controls M0 and M12 each went red. The independent review re-ran the tests against the old code: 16 RED failed and 8 PINs passed. **Caveat:** `loop.py verify` has not seen the green (STALE exit=4). The bug cannot show in Sigma's own repo, because `.sdlc/*` is gitignored. It was shown only through the adopter-shaped real-git fixture `tests/registry_root.py`. |
| **AC-2**: a human's uncommitted edit still refuses; nothing silently overwrites or hides it | **achieved for the guard; one pre-existing residual** | PINs 3–7 cover: an ordinary edit beside registry dirt; a human note below the managed block; a human edit Sigma then wrote over; staged, `MM` and chmod cases; and a hand-revert laundering attempt. Each was reddened by a weak build (W1–W3) or a surgical mutant (M2, M7, M14, M15). Test 8 checks that the refusal names each path and that its own printed gesture clears it. **Residual (pre-existing, not caused by this fix):** `amend`/`normalise_entry` silently drops out-of-schema fields from a human's uncommitted shard edit. The research measured it: `notes kept: False`. The guard now *refuses and names* that shard at the next start (test 5), so it is no longer hidden. But the dropped bytes are gone, and only their blob id survives in the chain. Read strictly, "nothing silently overwrites" is still breached on the write side, by code this goal did not change. This is follow-up F-1, **not yet filed**. One limit is documented and accepted: a hand-revert that is byte-identical to a *later* Sigma state is exempt (review, non-blocking). |
| **AC-3**: the fix introduces no `git stash` usage | **achieved** | `git diff -- skills hooks \| grep '^+.*stash'` prints nothing (re-run here). `feature_provenance.py` has 0 occurrences. Test 9 scans every `work._dirty_root*` source and the new module. The refusal no longer advises `git stash push -u`. **Adjacent, outside this AC's scope:** the loop's own verify-freshness rebase (`work.py:4092`, `git rebase --autostash`) autostashed this goal's whole uncommitted diff, and the pop conflicted on CHANGELOG. Verify undid it cleanly (evidence file), but it is the #53 class of risk. |
| **AC-4**: concurrent slots do not refuse each other over registry writes; the behaviour is stated and tested | **achieved where `flock` works** | Test 14 checks the writer records before it replaces, with control M5. Test 15 checks the reader reads the bytes before the record, with control M6. Both are deterministic in-process seams, per AGENTS.md's "port across a performance boundary" rule. Test 16 is a smoke run labelled SMOKE: 10 checks, 0 refused, 3 writers × 25 writes. The behaviour is stated in branching-model §8e. **Not covered:** Windows has no `fcntl`, so a spurious refusal is possible; that limit is stated. A live multi-slot loop on an adopter was never run, and the CHANGELOG says so. |
| **AC-5**: `python -m pytest tests/ -rs -q` passes on the goal worktree | **not achieved** | The command exits **127** on this host, because there is no `python` on PATH. Goals #955 and #956 hit the same thing. A python3 run of the full suite in 5 shards: 13,238 passed, **5 failed**. Of those: 2 fail on unchanged `66b1f1b` too (codex-override tests that depend on the host env); 1 was a load timeout that passes alone; 1 `test_skill_structure` doc citation was since removed and now passes; and 1 is the `test_write_surface` ratchet, which is green only once `feature_provenance.py` is tracked. The plan's DoD ("`loop.py verify .sdlc 954` passes") is unmet. |

**Residual gaps and their tracking**: AC-5 and landing are tracked by **#954 itself** (it stays open and gets
parked for a human). The python/python3 gap and the push-permission gap have **no tracking issue yet**; see
follow-ups FU-1 and FU-2. The F-1 residual on AC-2 has **no tracking issue yet** (FU-3).

---

## 2. Structural reflection: the debt the narrow fix left

### Multi-site smell
- **Twin write chokepoints, identical wiring.** `feature_registry._atomic_write_text` and
  `feature_doc._atomic_write_bytes` (whose docstring calls itself "the bytes twin") each gained the same
  `with (provenance.recorded(...) if features_dir is not None else contextlib.nullcontext())` block. The repo has
  **4** `_atomic_write_*` implementations (feature_registry, feature_doc, triage, slack_commands_listen), **3**
  `_acquire` lock helpers (feature_sync, feature_provenance, logroll), and **10** files that call `fcntl.flock`
  (grep, this tree). This goal had to add another copy of the lock idiom. `feature_sync → feature_registry →
  feature_provenance` is a `_load` cycle, so `feature_provenance` cannot borrow `feature_sync._acquire`.
  **The missing primitive**: a leaf, stdlib-only module with an atomic write (an optional pre-replace hook) and a
  bounded fail-open `flock`, which loads no sibling. Shaping it that way breaks the cycle. A structural defer,
  so it becomes a recommendation (FU-11).

### Coverage themes (what review and plan-review kept finding)
- **Tests designed around the red/green parser rather than the behaviour.** The plan spends a whole section
  ("Output discipline") plus test-name length rules (≤72 characters), `drained(capfd)` wrappers, a ban on
  `parametrize`, and a Task 1.4 parser dry run, all to avoid `red_green.py`'s attribution traps. Those traps are:
  a `Captured` section voids every red, and failure separators shrink below `___` so failure blocks merge and a
  `TypeError` is credited as an assertion. The same batch includes #956, which is exactly this bug, and #414
  covers the summary trim. This is a **tax on every goal**. The structural fix is for `red_green.py` to read a
  machine format (`--junitxml` or `--report-log`) instead of terminal text. FU-8.
- **Tracked-only ratchets are vacuous before the commit.** `test_write_surface` reads `git ls-files`, and so does
  the shipped-doc path check, so neither can see an untracked new module. Together with the dispatch rule "no
  commit in P5", this meant the inventory guard was proven only in a scratch tracked clone, not in the goal
  worktree. FU-9.
- **The documented-gesture rule did its job at P4.** Plan review found a BLOCKING defect: under the documented
  `work.py start .sdlc "$goal"`, the relative `.sdlc` left `registry_dir` unresolved, so every registry path
  would still refuse. All 22 planned tests passed an absolute `tmp_path` and could not see it. This is AGENTS.md's
  "run the control on the gesture the DOCS give, not a stronger one" catching a real defect before any code was
  written. The rule is reinforced, and no new rule is needed.

### Deferred roots (tactical or structural)
| Defer | Class | Disposition |
|---|---|---|
| TD-2 / F-1: `amend` drops out-of-schema human shard fields | **structural** (a write path that cannot see the committed state) | FU-3 |
| TD-3 / F-2: `_sync_doc` renders the page outside the unit lock | tactical (a stale-render race, unrelated to the guard) | FU-4 |
| TD-5: `work._run` strips stdout and corrupts porcelain line 1 | tactical here (worked around), **structural** for any future porcelain parser | note only; an opt-in `strip=False` would retire the workaround |
| TD-6 / F-3: other tracked Sigma bookkeeping in adopter roots | structural (the same class of guard trip) | FU-5; verify real adopter layouts first |
| F-4: no exemption under `core.autocrlf` or clean filters | **structural for Windows adopters**: Git for Windows defaults to `autocrlf=true`, and shards are written in text mode, so the shard blob ids never match HEAD and #954 is in practice **unfixed on a default Windows install** (inferred from the design and stated in the plan's Risks; not measured) | FU-6 |
| **The root cause itself**: registry dirt lives in root until a human commits, and docs §15 calls automating that "undesigned" | **structural** | FU-10: design a registry backup through `sync.py`'s existing ops-branch channel pattern (`LEDGER` and `KNOWLEDGE` already use it, `sync.py:148`) instead of root commits |

### Rule alignment
- It reinforces **SAFETY**'s "refuse loudly rather than proceed weakly". Every unprovable case (autocrlf,
  SHA-256, `.sdlc` below the toplevel, lost record, v2 read error) fails *closed* to today's refusal and names
  the file.
- It reinforces **RESILIENCY**'s "a documented, polite lever". The refusal prints the §15 commit gesture
  verbatim, and test 8 executes it.
- It reinforces **RELIABILITY**. The plan's prototype predicted 0.52–0.61 ms per write. The real module measured
  **4.75 ms mean at the 256 cap** and about +0.8 ms with short chains, roughly 8× the prediction at the cap. Only
  Task 7.3's re-measurement caught it, and the CHANGELOG carries the real figures. Prototype timings in a plan
  are not system timings.
- It reveals a **process conflict**. Plan Task 7.1 says to commit with `work.py commit` before the final verify.
  The P5 dispatch forbade `git commit` and `git add`. The uncommitted tree then (a) blinded two tracked-file
  ratchets and (b) was autostashed by verify's freshness rebase. See proposal R-1.

---

## 3. Product reflection: the gaps the work revealed

- **Friction: landing preconditions were found last.** The goal ran P1→P6, including three full-suite verifies
  and a 56.6-minute python3 suite run. Only then did it find that (a) the configured verify interpreter does not
  exist on this host, and (b) this account cannot create `sdlc/*` refs (GH013, ruleset "basic", id 24602467).
  Both could be checked in under a second at loop start. `verify_detect.python_command()` picks `python3` for
  candidates it builds itself, but copies a CI-derived candidate's interpreter verbatim (`_ci_steps`,
  `verify_detect.py:241-264`). CI's `python` came from `setup-python`, and this Mac has no `python`. `loop.py
  verify` reports this as `FAILED exit=127`, which looks the same as a red suite (no `127` handling in
  `loop.py`). FU-1 and FU-2.
- **Root is never clean, only tolerated.** After this fix, the registry dirt still builds up in root until a
  human commits. A human's `git pull` on root can then be refused by git ("local changes would be overwritten")
  when upstream changed the same unit's shard. That is git's documented behaviour, inferred and **not measured**
  here. FU-10 removes the class.
- **Bugs that are features**: the registry wants a backup home that is not the integration branch's working
  tree. Sigma already has that primitive (ops-branch sync channels) and the registry does not use it.
- **Negative space**: what the run should have produced and did not:
  - No end-to-end run in the real target environment, meaning an adopter repo with a committed registry and two
    or more live slots. The bug cannot occur in Sigma's own repo, so AGENTS.md RELIABILITY ("run end to end in
    the real target environment") is satisfied only by a synthetic real-git fixture. FU-12.
  - No recovery lever for adopters already holding unpopped registry stashes (17 in the reporter's repo). The
    only guidance is the agent-rules-detail prose bullet. FU-13.
  - No `loop.py verify` green, no commit, no PR.
- **Cost the change adds**:
  - +1 `git status --porcelain=v2` call only on registry-only dirt (33.4 ms measured), and 0 on a clean root.
  - A guard check takes 110 ms with 20 dirty shards and 335 ms with 200.
  - A registry write costs about +0.8 ms with a short chain and 4.75 ms at the cap.
  - Records are at most 11,315 B per file.
  - The test suite grows by **122 s for 24 tests** (sequential, macOS). That is notable, and CI's `-n auto`
    absorbs it.
- **Direction**: not judged. The north-star is absent.

---

## 4. Follow-ups worth filing (suggested titles; not filed by this retro)

Ordered by value.

| ID | Suggested title | Why | Overlap |
|---|---|---|---|
| **FU-1** | `loop.py verify` / `/sigma-doctor`: flag a verify command whose interpreter is not on this host's PATH (exit 127) instead of reporting FAILED | Every goal on this host fails AC-5-style criteria and `record done` (#954, #955, #956). `verify_detect` copies CI's `python` verbatim. The immediate local fix is a human config decision: `.sdlc/config.json` `verify.command` → `python3 -m pytest tests/ -rs -q` | #225 is the same family (host-python drift); closed |
| **FU-2** | Preflight at loop start: refuse loudly when this actor cannot create `sdlc/*` refs on the remote (ruleset `creation`/`update` restriction, GH013) | A whole goal's spend was made before the push was rejected | Adjacent to #727 (main-branch ruleset gaps), which does not cover `sdlc/*` creation |
| **FU-3** | `feature_registry.normalise_entry` silently drops out-of-schema fields from a human's uncommitted shard edit (F-1) | The residual on AC-2. The bytes are unrecoverable once overwritten | none found |
| **FU-6** | The dirty-root registry exemption never applies under `core.autocrlf`/clean filters, so #954 is unfixed on default Git for Windows (F-4) | An org-wide install includes Windows hosts | none found |
| **FU-10** | Design: back up `.sdlc/features/` through a `sync.py` ops-branch channel instead of root commits | Removes the root cause (perpetual root dirt). Large lane | none |
| **FU-8** | `red_green.py`: attribute reds from `--junitxml`/`--report-log`, not terminal text; add the separator-shrink defect (names >72 characters at 80 columns merge failure blocks, and a `TypeError` is credited as an assertion) | A tax on every goal's plan (F-6) | #956 (captured output), #414 (80-column trim). Add the separator case to one of them |
| **FU-9** | Tracked-file ratchets (`test_write_surface`, the shipped-doc path check) should include untracked non-ignored files (`git ls-files --others --exclude-standard`) so they are not vacuous before a commit | A guard that cannot fail pre-commit | none |
| **FU-12** | Dogfood #954 on an adopter repo with a committed registry and ≥2 concurrent slots, after it lands | RELIABILITY: run in the real target environment | none |
| **FU-7** | CHANGELOG `## Unreleased` is a guaranteed conflict between concurrent goals: move to per-goal changelog fragments | A serialised chokepoint, seen here (#954 vs #969). It blocked verify via an autostash-pop conflict | none |
| FU-11 | Extract a leaf stdlib-only atomic-write + bounded-`flock` primitive that loads no sibling | 4 atomic-write copies, 3 `_acquire` copies, 10 `flock` sites; the `_load` cycle forced another | none |
| FU-4 | `_sync_doc` renders `<unit>.md` outside the unit lock; concurrent same-unit picks can leave an older goal list (F-2) | Tactical | #19 (lock clash) is adjacent |
| FU-5 | Verify whether other tracked Sigma bookkeeping (local-mode `.sdlc/goals/*.md`, plans, research) trips the dirty-root guard in adopter layouts (F-3) | The same class as #954 | none |
| FU-13 | `/sigma-doctor`: report (read-only, never drop) stashes whose only content is `.sdlc/features/*` | Recovery for adopters already holding #954's stashes | #53 adjacent |
| FU-14 | Dirty-root refusal polish: kind-`2`/`u` porcelain v2 lines print garbled paths (`split(" ", 8)` keeps score/hash fields); the `shlex.quote`d root is not cmd.exe-pasteable | Review non-blocking; cosmetic, still fails closed | none |
| — | #53: add that verify's freshness rebase (`work.py:4092`, `--autostash`) autostashed this goal's whole uncommitted diff | Existing issue (F-5) | **#53** |

---

## 5. Standing-doc rot (§5)

- **Already corrected by the diff, nothing to propose:**
  - The `_dirty_root_refusal` and `start()` docstrings no longer claim that root dirt "can only mean an edit
    landed straight in root".
  - branching-model §15's "root stays dirty until a human commits" now states the tolerance and its limits.
- **Not mechanically enforced, so keep the prose:** the new agent-rules-detail bullet "Never `git stash` the root
  checkout…" stays. Test 9 enforces only that the dirty-root *code* never advises stash; nothing stops an agent
  from stashing.
- **Superseded plan:** propose archiving `.sdlc/plans/954.md` after the goal **lands**, not now. It is the live
  plan for a parked goal, and the plan-gate's "recent file = fresh plan" reading is correct for it today.
  **Needs your approval** (archive, never delete).
- Nothing else rotted.

---

## 6. Learning harvest: proposals table

The audit-trail notes are safe to write, but they were **not posted** (hard rules). The orchestrator may post
each one with `loop.py note .sdlc 954 -` and a body starting `retro:`. Every standing-doc change is
**needs your approval** and was not applied.

| # | Lesson | Store | Exact edit / note | Status |
|---|---|---|---|---|
| A-1 | Plan review caught the relative-`.sdlc` defect that 22 absolute-path tests could not see. The documented-gesture rule works best at P4, before code exists. | audit trail | `retro: P4 caught a BLOCKING defect (relative .sdlc under the documented CLI gesture) that every absolute-tmp_path test missed; AGENTS.md "run the control on the gesture the DOCS give" reinforced, no new rule.` | ready to post |
| A-2 | A prototype's timing in a plan is not the system's timing (0.6 ms predicted, 4.75 ms measured at the cap). | audit trail | `retro: plan prototype timings were 8x low at the cap; Task 7.3 re-measurement with the real module is what kept the CHANGELOG honest.` | ready to post |
| A-3 | The bug cannot occur in Sigma's own repo (`.sdlc/*` gitignored). `tests/registry_root.py` (real git with a committed registry) is the reusable adopter-shaped fixture. | audit trail | `retro: adopter-only bugs need an adopter-shaped real-git fixture; reuse tests/registry_root.World for registry/root-checkout work.` | ready to post |
| A-4 | Landing preconditions (verify interpreter, push permission, CHANGELOG conflicts) surfaced only after the full spend. | audit trail | `retro: landing blocked by host python absence (exit 127), GH013 branch-creation ruleset, and a CHANGELOG conflict with #969 — all detectable at loop start; see FU-1/FU-2/FU-7.` | ready to post |
| **R-1** | An uncommitted goal tree blinds tracked-file ratchets, and verify's freshness rebase autostashes it. The P5 dispatch's "no commit" contradicted plan Task 7.1. | **standing rule (proposal)**: `skills/sigma-loop/SKILL.md` P5 dispatch, or AGENTS.md "Rules that follow from all five" | Proposed text: *"P5 commits through the loop's own gesture (`work.py commit .sdlc <goal>`) before the final `loop.py verify`. An uncommitted goal tree is invisible to `git ls-files` ratchets and is autostashed by verify's freshness rebase (#53)."* Only if FU-9 and #53 are not fixed mechanically first; if they are, this rule is redundant. | **needs your approval** |
| **R-2** | Landing preconditions should be checked mechanically, not by a prose rule. | **not a rule**: route to FU-1 and FU-2 (mechanical preflight) | Do not add prose; a preflight that refuses loudly is the SAFETY-shaped answer. | proposal only |
| N-1 | Registry backup via the ops-branch pattern would change the architecture's shape. | north-star | Not proposed: the north-star is absent. It lives in FU-10 as a design goal. | n/a |

No registered invariant (`/sigma-decide`) is proposed. None of these lessons can be written as a value comparison
on a named thing.

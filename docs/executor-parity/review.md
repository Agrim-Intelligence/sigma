# Executor parity — review (`sigma-review` vs the `code-review` plugin + `superpowers:requesting-code-review`)

`sigma-review` is Sigma's portable Review executor — a thorough two-layer code-quality audit. On
Claude, the `code-review` plugin (`/code-review`) + `superpowers:requesting-code-review` stay
**preferred**. This records where the fallback stands against them, including where it loses.

**How this table is scored.** Against the **pair**, not the plugin alone. `requesting-code-review`
carries its reviewer in `code-reviewer.md`, and that template's checklist is materially wider than the
plugin's deliberately-narrow bug scan. An earlier revision of this doc scored several rows ✗ from the
plugin's exclusions while the template covered them — every ✗ below is checked against both files.

| Capability | code-review plugin + requesting-code-review | sigma-review | Verdict |
|---|---|---|---|
| Diff review for correctness bugs | ✓ | ✓ | **par** |
| Findings with `file:line` + severity | ✓ + full-SHA permalinks with context lines | ✓ (Bug/Concern/Coverage/Missing/Good/Minor/Nit) | **par** |
| Reviewer is structurally not the author | ✓ fresh subagent, hand-built context envelope, never the session's history | ~ `review.independent` dispatches one per gate **in the loop**; degrades to an inline reviewer where the host has no subagents | **worse off-loop** |
| Review cadence | ✓ mandatory after each task, after a major feature, before merge | ~ gates at plan-review / pr-review / retro — nothing mid-Implement | **worse** |
| Finding confidence calibration + false-positive filter | ✓ a separate agent scores each 0-100 against a verbatim rubric, drops < 80 | ~ failing-case gate + named false-positive classes + blocking/follow-up split — but self-applied | **weaker mechanism, accepted** |
| Evidence discipline (no invented numbers) | ✓ | ✓ (ties to `sigma-verify`) | **par** |
| Project-rule gate — `CLAUDE.md` | ✓ dedicated agent, plus a scorer that re-verifies the rule is actually written down | ✓ root **and** directory-scoped, quoted | **par** |
| Test quality ("would it fail if the code were wrong?") | ~ "tests verify real behavior, not mocks?" in `code-reviewer.md`; the plugin itself treats coverage as a false-positive class | ✓ axis 8 | **par-to-better** |
| Cleanup / reuse / simplification pass | ~ the plugin excludes general code quality by design; the template has one DRY line | ✓ (Structure / DRY / dead-code / consistency) | **better** |
| Performance & resource lifecycle (N+1, hot-path I/O, leaks, timeouts) | ~ one unelaborated line — "reasonable scalability and performance?" | ✓ axis 3 | **better** |
| Comment & doc drift (a comment the diff just made false) | ~ Agent #5 checks the diff COMPLIES with comments, not that a comment went false | ✓ axis 7 | **better** |
| Async-correctness / concurrency + shared state | ✗ | ✓ axis 2 | **better** |
| Observability (is the new failure path diagnosable?) | ✗ | ✓ axis 5 | **better** |
| Named code smells (Fowler cohesion / coupling / dispensables vocabulary) | ~ generic "bugs" | ✓ axis 6 | **better** |
| Quantitative KPI dashboard (ruff/mypy/cov + hotspots) | ✗ "do not check build signal", by instruction | ✓ | **better** |
| Counter-review (grade an external Cursor/GPT/SonarQube review) | ✗ | ✓ | **better** |
| Whole-repo audit (what accumulated across many changes) | ✗ | — not this skill; `/sigma-audit` owns it | **out of scope here** |

**Net.** **Better** on seven rows the companions leave thin or absent — the KPI dashboard, async and
shared state, observability, named smells, doc drift, resource lifecycle, and counter-review. **Par**
on diff-review correctness, finding format, evidence discipline and the `CLAUDE.md` gate.

**Worse on two, and they share a cause: both of the companions' strengths are mechanisms, not
prose.** `requesting-code-review` makes independence structural — a fresh subagent that never sees the
session's history — and mandates review *after each task*, not once at the end. `sigma-review` asserts
independence in its opening line and asks for it only in the loop, where `review.independent`
asks for a subagent per gate, which the code cannot prove was independent; run interactively on a host without subagents, the reviewer is the
author holding a fresh brief, and the skill's "you did not write this code" is then a claim in prose —
the exact thing its own §"Before you report it" bans. Cadence is unaddressed either way.

On precision the trade is deliberate rather than lost: a self-assigned 0-100 score is self-assessment,
so `sigma-review` requires a written failing case before a finding may block. That is still the same
model grading its own finding, so it is a **weaker** mechanism than a separate scorer — accepted
because a single portable reviewer has no second agent to spend.

On Claude the plugin remains available and preferred; off-Claude, `sigma-review` is a strong,
self-contained reviewer whose counter-review mode can even grade Cursor's own — with independence and
cadence as the two known gaps, not as parity.

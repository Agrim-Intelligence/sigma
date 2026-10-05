# Recording progress

The detail behind the phase-note, phase-banner, QC and retrospective calls in
[`../SKILL.md`](../SKILL.md) — steps 3, 4 and 5, plus what `record` writes at step 6.

---

As you complete each phase, **record it** so the issue timeline is the audit trail:
`python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" note .sdlc "$goal" "<phase>: <key findings / decisions>"`.
Mark phase boundaries too, now with real console visibility (#1626) — before dispatching each
phase's subagent: `python3 "${CLAUDE_SKILL_DIR}/scripts/phase_report.py" start .sdlc "$goal"
<goal|research|plan|plan_review|implement|review|retro> --model <tier> --pid "$PPID"` prints a two-line banner
(`⚪ PHASE START · #<goal> <title>` / `   <badge> P<n> NAME · requested host model:
<id|unrecorded> · predicted model tier: <tier> (agent-set)`, where `<badge>` is the phase's
coloured square from `docs/output-contract.md` §2 — it names the phase, never a state) and stamps
a marker — best-effort, skip it rather than guess the phase name. On Codex, a dispatched phase
requires the actual host model ID; do not skip a failed `start` or dispatch without its marker. A
start is an ANNOUNCEMENT, not a boundary: it has no phase to come from and nothing measured yet, so
it is deliberately not an EVENT block (#2112). **The title is
resolved from what is already on this machine** (#2100) — the local board mirror, else the local
goal file — so the banner costs no network call; add `--title "<title>"` when you already have
it in hand and it is used verbatim. Nothing resolvable prints the bare id, never a blank. Once
that phase's subagent returns, you already have its `agentId` from the dispatch result:
`python3 "${CLAUDE_SKILL_DIR}/scripts/phase_report.py" end .sdlc "$goal" <phase> --agent-id
<agentId> --pid "$PPID"` sums that one subagent's own transcript, prices it where a rate card exists, prints the
observed model + cost line, and writes the `phase` ledger event with REAL `tokens_in`/
`tokens_out` — never invent `ms`, `tokens_in`, or `tokens_out` yourself; this is what actually
measures them. When a dispatched Claude agent transcript is available, `end` checks that its observed model family
matches the tier passed to `start`; a mismatch returns nonzero even though the physical phase-end
boundary and measured spend are recorded. Inline Claude
phases keep their best-effort model report because `start` cannot switch the session's model. A Claude
cloud session without a readable transcript keeps its existing unavailable/best-effort result,
which is not proof of model compliance. THIS one IS an EVENT block (#2112) — a real boundary, with
a phase it just left and a measured cost — and it carries the phase's **elapsed wall time** and
**the phase that comes next** as well as cost: `**#<goal> <title>** P2 RESEARCH → P3 PLAN — 8m11s
· $4.98 · tokens 52 in, 35,433 out · claude-opus-5.` — so a reader who does not know the
SDLC by heart can still follow the flow. `P7 RETRO` reads `→ last phase`. Anything unmeasurable says
so in the `cost: unavailable on this host (<reason>)` manner rather than printing a zero. **The end
block carries no ✅ and no other status marker**: you call it the same way whether the phase passed or
blocked, so it claims nothing about the outcome, and `→ P3 PLAN` is where the standing SDLC puts the
phase — not where a blocked one will actually go. Reporting the verdict is YOUR Block
A slot line's job, not the banner's. **The block is BUILT by `scripts/render.py`, shelled out**
(#2112), and if the renderer refuses one — a title carrying a marker glyph, say — you get the
pre-#2112 `🟨 PHASE END · #<goal> <title>` / `   🟨 P2 RESEARCH · 8m11s · $4.98 · tokens 52 in,
35,433 out · claude-opus-5 · next: 🟧 P3 PLAN` two-line banner on stdout and the typed refusal on
stderr, never silence. **The console print is unconditional; the ledger write is not** — like every
other EVENTS-stream write, `phase_report.py`'s `ledger.safe_append` call still no-ops when
`journal.enabled` is off (the shipped default), so a fresh adopter sees the banner/cost line
immediately but the ledger's `tokens_in`/`tokens_out` stay empty until they also turn the journal
on — that's a DIFFERENT flag from `action_log`. `--pid "$PPID"` on BOTH calls (#2667) is how `end`
tells THIS process's marker apart from one a crashed session left open for the same phase:
`CODEX_THREAD_ID` and `CLAUDE_CODE_SESSION_ID` are read automatically, no second flag needed for
either. A marker whose identity does not match keeps its model gates — a wrong model still exits
2 — but not its start time, and prints `elapsed: unavailable (stale phase-start marker)` /
`cost: unavailable on this host (stale phase-start marker: <reason>)` instead of a number windowed
from a dead instant. A dispatched subagent's or a Codex child's own tokens stay exact regardless,
because neither ever needed that window. When resuming a goal, call `start` again before `end`.
For every dispatched Claude phase, add
`--requested-model <Task-model-selector>` to `start`, using the exact Task `model` argument (for
example `sonnet`). This value is for display; Claude's existing observed-tier check still applies
when its transcript is readable and a cloud/no-transcript result keeps its best-effort behavior.
For every dispatched Codex phase, add `--host-model <actual-host-model-id>` using the exact ID
passed to the subagent call and `--expect-agent-id`; `end` refuses a missing or
mismatched observed model. Put `python3 <absolute-phase_report.py> codex-agent-id` in the
subagent prompt and require the child to return its printed `CODEX_THREAD_ID` UUID. Pass that
UUID to `end --agent-id`, not the task's display name. If the child ID is missing, the marker
keeps the orchestrator's usage out of the phase. Codex reports observed tokens/model, while
cost remains unavailable because the bundled rate card contains only Anthropic prices. A
configured `budget.max_codex_raw_tokens` credits the measured input plus output at this `end`
boundary, including cached input, separately from Claude's priced `budget.max_tokens`. It is an
admission stop on the next pick, not an account quota: the orchestrator and missing phase rollouts
cannot be attributed here. With this ceiling configured, `end` fails if the matching start marker
or measured rollout is missing. The predicted tier in the start banner is not proof of dispatch.
Running a phase INLINE instead of a dispatched subagent means `end` has no `--agent-id` to pass — call it
anyway, omitting that flag: it falls back to a windowed read of your own session transcript, and
only prints `cost: unavailable on this host` (naming the reason) when even that cannot be found.
`model_selection: off` leaves portable prediction disabled, but a Codex phase may still be
dispatched; map its explicit `off` tier through `predict.py host-model codex` and record the returned
host model and agent ID as above.
**P5 IMPLEMENT also announces each plan step** — `🟦 STEP <n>/<m> · #<goal> <title>` /
`   🟦 P5 IMPLEMENT · <brief>` /
`   requested host model: <id|unrecorded> · predicted model tier: <tier> (step|phase)` — via `python3
"${CLAUDE_SKILL_DIR}/scripts/phase_report.py" step .sdlc "$goal" implement --num <n> --total <m>
--phase-model --brief "<step>"`, run by the phase agent executing the plan. With
phases inline (including when `model_selection` is off) that is this session: call it as each step
starts, whether you follow `sigma-implement` or a companion executor. With phases dispatched
(`auto`) it is the implement subagent, which may follow a companion that never reads
`sigma-implement` and edits in the goal worktree, where a relative `.sdlc` has no marker (and in github
mode no board mirror to title the goal from) — so put the command in its dispatch prompt with this
goal's id and absolute
`phase_report.py` and `.sdlc` paths filled in. When `resolve-step` gave a step its own tier
(`running.md`), add `--model <tier>`: the line then says `(step)` instead of the phase's `(phase)`.
For a separately dispatched step, replace `--phase-model` with both `--model <step-tier>` and
`--host-model <exact-host-model-id>` from that dispatch. Without either flag its requested host
model is `unrecorded`, even when its tier matches the phase. Phase end measures the phase agent's
observed model only; a separately dispatched step needs its own rollout check to establish its
observed model.
A dispatched subagent's banner prints into its own transcript, which the host may not show — not
verified end to end. `step` writes nothing, so `end`'s elapsed time and cost are untouched.
The first time in a phase you create, edit, or delete a file not already logged this phase, log
that too: `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" log .sdlc "$goal" file --path <path> --op
create|edit|delete` — do not log a re-edit of an already-logged file, and do not log reads.
For a decision/finding/fix worth keeping, record a 🔒 Critical Insight the same way — a
`🔒 **Critical Insight — <type>**` header (type: design-decision | code-decision |
research-insight | finding | fix | constraint) followed by **Decision / finding:**,
**Why it matters:** and **Evidence:** lines. This is the exact shape
`skills/sigma-init/github-templates/CRITICAL_INSIGHT_TEMPLATE.md.tmpl` materializes into a
`sigma-init --github` project's own .github/CRITICAL_INSIGHT_TEMPLATE.md — inlined here so the
format is known even on a project that never ran `--github` and has no such file. This comments
the issue in github mode and appends to `.sdlc/journey/<goal>.md` in local mode; it's fail-open
(never breaks the run).
4. Entering the **review** phase? Move the board card to QC:
`python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" qc .sdlc "$goal"` (github-project board only — a no-op for local/issues).
5. **Retrospective (Learn)** — after Review, run the **`sigma-retro`** executor (advisory): reflect on
the structural + product debt the fix left behind and grade intent-vs-shipped. Under
`config.review.independent` this too runs through the resolver, not as an assertion — `python3
"${CLAUDE_SKILL_DIR}/scripts/reviewer.py" resolve .sdlc`, then the mechanism it names, fed
(`python3 "${CLAUDE_SKILL_DIR}/scripts/review_context.py" brief .sdlc "$goal" --for retro`) — the
context that just argued the work was done shouldn't also grade whether it met the intent. Autonomous
mode → **write only the audit-trail notes**, and **park** any north-star / standing-rule proposal to
the review queue for a human; never edit a standing doc unattended. Fail-open — it never breaks the run.
6. Record the outcome, including the retro grade from step 5 if Retrospective ran:
`python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" record .sdlc "$goal" done --retro-grade
achieved|partial|diverged` (or `parked "reason" --retro-grade ...` / `failed "reason" --retro-grade
...`; omit the flag entirely on a goal that never reached Retrospective — e.g. a pre-work
auto-park).
The flag is not only bookkeeping: passing it is also what refreshes the knowledge graph when
`knowledge_graph.auto_refresh` is `true` (issue #1562) — `record` calls `kg.py refresh` itself,
after the outcome is recorded. **Do not run a graph build by hand as well.** Omitting the flag on
a goal that DID reach Retrospective silently skips that refresh.

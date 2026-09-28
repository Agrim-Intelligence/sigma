# Agent Output Contract

Host-agnostic. Applies to Claude Code, Cursor, Codex, or any future agent host.
It governs **console output only** — not what you build, not how you decide.

The test this contract must pass: from a **single glance** at one status line, the
user knows *which goal*, *which SDLC phase*, and *what is happening right now* —
without scrolling up and without asking.

---

## 1. The rule

Status output is **not** free-form prose, and it is **not written by hand**. Every time you report
state, you emit one of exactly **three blocks** below, in the exact shape given — and that shape is
CONSTRUCTED by `skills/agrim-loop/scripts/render.py` from facts you pass it. This document is the
renderer's specification: §3's Block A sample and §7's Block A reference output are its output,
and a block it did not build does not exist. Nothing else.

If you have nothing that matches a trigger in §5, **say nothing**. Silence is a
valid output. Do not fill turns with progress narration.

### The renderer and its commands

`render.py` builds a block from a JSON object of facts, read from stdin or passed inline with
`--json`. Its usage lines, verbatim from the module (`skills/agrim-loop/scripts/render.py`):

```
render.py status   < facts.json      # Block A
render.py event    < facts.json      # Block B
render.py decision < facts.json      # Block C
render.py status --json '{"headline": ..., "slots": [...], "tail": ...}'
```

Two callers already exist, and where they apply you run them rather than assembling facts yourself.
`python3 skills/agrim-log/scripts/log.py slots .sdlc` (the `agrim-log` skill) is the live Block A —
one two-line slot per active goal, derived from the action log and built by `render.py`; relay its
output verbatim. `phase_report.py end` prints Block B at every phase boundary — the machine half of
that boundary, described under Block B in §3; read it, do not imitate it.

A refusal is `render.py: REFUSED [<code>] at <path>: <detail>` on stderr, exit 2, and **nothing on
stdout** — a caller piping stdout to a console receives nothing rather than half a line. It means the
facts were malformed: fix the facts and run it again. Never patch the prose by hand. And the line the
renderer builds is unforgeable in shape, not guaranteed to be what the user sees: anywhere a reply is
read rather than raw tool output, it can still be paraphrased — relay it exactly.

---

## 2. The two axes

Every slot line carries both. They are orthogonal — do not use one for the other.

### Axis 1 — liveness marker (colour code, closed set)

Answers *is this moving?* One marker per line, first character of the line.

| Marker | State | Meaning |
|--------|-------|---------|
| `⚪` | queued | claimed, not started |
| `🔵` | running | actively working |
| `🟣` | review | written; review/verification in flight |
| `🟢` | merging | approved; merge handler or CI in flight |
| `✅` | done | merged and recorded |
| `🔴` | blocked | failed, or needs a human decision |
| `⏸️` | parked | deliberately deferred (say why in ≤6 words) |
| `⏳` | waiting | blocked on another slot, not on a human |

Never invent a marker. Never use a marker for anything but a state line.

### Axis 2 — SDLC phase token (closed set)

Answers *where in the flow?* Always `P<n> NAME`, from the repo's standing SDLC:

| Token | Phase | Badge |
|-------|-------|-------|
| `P1 GOAL` | objective restated as one concrete goal | `⬜` |
| `P2 RESEARCH` | blast radius, affected files, constraints | `🟨` |
| `P3 PLAN` | steps / files / tests / definition-of-done | `🟧` |
| `P4 PLAN-REVIEW` | adversarial review of the plan, before any edit | `🟫` |
| `P5 IMPLEMENT` | test-first execution of the plan | `🟦` |
| `P6 REVIEW` | code review + verification evidence | `🟪` |
| `P7 RETRO` | lessons captured, outcome recorded | `🟩` |

The phase is **always present**. If you genuinely cannot name one, the work is
not in the SDLC and does not belong in a slot line.

A badge is not a marker: a square names a phase, never a state. `phase_report.py`'s banners print it
before a phase token; Blocks A–C never do.

---

## 3. The three blocks

### Block A — STATUS

Emitted whenever slot state changes. Two lines per slot:

**Title line:** `marker` · **bold goal ref** · `phase token` · title · `model <tier> (predicted; agent-set)`, or `model tier unrecorded` when no tier was set
**Description line:** indented `↳`, what is happening right now → what unblocks next

```
Status — <N> merged in <window>:

* 🔵 **[#2628](url)** · P5 IMPLEMENT · Extract coherence validator · model opus (predicted; agent-set)
  ↳ moving `_validate_geography_character_coherence` verbatim → verify byte-identity → PR
* 🟣 **[#2915](url)** · P6 REVIEW · Sync stale catalog-size docstrings · model sonnet (predicted; agent-set)
  ↳ author-blind review running on PR [#2927](url) → merge gate
* ⏳ **#2632** · P2 RESEARCH · Decompose CharacterSubBuilder · model tier unrecorded
  ↳ seam analysis queued; blocked behind 1c (same file)

<one-line tail: what you are waiting on>
```

Rules:
- Goal ref is **bold** and a **markdown link** — `**[#2628](url)**`, never bare `#123`.
  Bold-without-link only when no URL exists yet.
- Title ≤ 88 characters, describes the *goal*, not the current step. Stable across phases.
  Characters, not words, and 88 exactly, because that is where `phase_report.clean_title` already
  elides a raw issue title — so every title the producer emits is one the renderer accepts, by
  construction. Over the cap is REFUSED, never truncated here: shortening belongs at the producer,
  where the `…` is visible, not at the renderer, where it would silently say something else.
- Description ≤ 20 words, describes the *current step*, and ends `→ <what unblocks next>` whenever
  there is a next thing to name — never invent one where there is not. `✅` is finished, `⏸️` says
  why it is deferred (§2), and `⏳` may name its blocker instead; both arrow-less descriptions in
  the samples above and in §7 are of that kind. So this is a requirement with exceptions, not an
  absolute — a rule the reference output breaks cannot be enforced and must not read as one.
- The model tier is the sixth and last field, always present: `model <tier> (predicted; agent-set)`,
  both labels mandatory and never collapsed into one — `predicted` because the tier is what the phase
  intends to run on, not what ran; `agent-set` because an agent wrote it and no code derived it.
  `render.py` refuses `tier-labels-collapsed` if either goes missing. `model tier unrecorded` is the
  honest null when no tier was set — never a guessed default. No cost field: cost is measured at a
  phase boundary and belongs to Block B (a ruling recorded on story #2030, its design's D-8).
- Separator between fields is ` · ` — nothing else.
- Max 6 slots. More than 6 means collapse, not scroll.
- Tail is exactly one line, e.g. `Waiting on notifications — no polling.`

### Block B — EVENT

Emitted when a unit of work finishes. One paragraph, no bullets, no headers.
Must name the goal, the phase it just left, and the phase it enters.

```
**<goal ref> <title>** P<n> <NAME> → P<n> <NAME> — <artifact link>, <the one substantive fact>. <Next action, already taken.>
```

Rules:
- Lead with the outcome, not the process.
- Exactly one substantive fact — the thing a reviewer would check. Not a tour.
- End with what you are doing next, in the indicative ("Dispatching its review"),
  never as a question or an offer.
- ≤ 3 sentences. No "I will now proceed to…".
- The goal's **title** rides *inside* the bold, beside the ref — `**#1983 Resolve reviewer
  independence per host**`, or `**[#1983](url) Resolve reviewer independence per host**` when a URL
  exists. Same cap as Block A's title field, and inside the bold rather than as a ` · ` field of
  its own because that separator is a Block A rule and this block does not use it. **Omitted
  entirely when nothing knows it** — the bold ref alone, never a blank and never a placeholder.
- The **right** side of the `→` may be `unknown` when the emitter genuinely cannot name the phase
  that follows, or `last` on `P7 RETRO`, which has none — they read `next phase unknown (no verdict
  at this boundary)` and `last phase`. Two named exits, not licence to skip the axis: any other
  unrecognised value is refused, and the **left** side takes neither, because whoever is at a
  boundary knows the phase it just left.
- The closing sentence is **optional**. §3 asks you to end with what you are doing next; where
  there genuinely is no next action — a machine emitting at a phase boundary — the block ends after
  its fact. Say nothing rather than narrate the not-knowing: an absence is not a fact worth a
  clause, which is the same rule that keeps a marker off this block entirely.
- **At a phase boundary `phase_report.py` measured, the one substantive fact is that phase's
  cost** — `cost $X.XX (N tokens, <observed-model>)`, or the honest `cost: unavailable on this
  host (<reason>)` line when it could not be measured. Never omit it once `phase_report.py end`
  has run; never restate the PREDICTED model tier here — this is the OBSERVED model from the
  phase's own transcript, not the ceiling `predict.py` chose before the phase started.

#### Machine phase banners

`phase_report.py` prints at both ends of every phase, from Sigma's Python, on every host —
which is why it is dependable where a model's prose is not. `step` emits its own banner too, for a
plan step inside P5 IMPLEMENT. The two boundary verbs are **not the same shape, and that is
deliberate**: `end` is a Block B, **constructed by `render.py` from the fact set above** since
#2112; `start` is an announcement and stays its own two lines, carrying the phase's own badge and
the requested host model (#2537) alongside the labelled predicted tier. Full interpretation and
limitations are in [output-contract detail](output-contract-detail.md).

```
⚪ PHASE START · #1983 Resolve reviewer independence per host
   🟨 P2 RESEARCH · requested host model: unrecorded · predicted model tier: sonnet (agent-set)
**#1983 Resolve reviewer independence per host** P2 RESEARCH → P3 PLAN — 8m11s · $4.98 · tokens 52 in, 35,433 out · claude-opus-5.
```

```
🟦 STEP 2/7 · #1983 Resolve reviewer independence per host
   🟦 P5 IMPLEMENT · Write the failing test for the title cap
   requested host model: gpt-5.6-terra · predicted model tier: sonnet (phase)
```

Read these, do not imitate them: the shape is the machine's to write, not yours. They are already
on screen, so a Block B you write after one must not restate its numbers, only cite the cost as its
one substantive fact per the rule above.

Four things there are deliberate:

- **A phase START is not a boundary, so it is not a Block B.** Block B's shape is `<from> → <to>`;
  a start has no from-phase, because the module is called per *phase*, not per transition, and it
  carries no measurement either. #2112 briefly forced it in, and every start of every phase then
  opened with `previous phase unknown (no verdict at this boundary)` — 51 characters, most
  prominent position, no information, forever. **Do not narrate the not-knowing.**
- **The start line carries `⚪`, and the end line carries no marker at all.** `⚪` is §2's "claimed,
  not started", which is exactly what a start announces — it is a state line, so §2's rule is the
  one that applies. `phase_report.py end` receives no verdict — it is called the same way whether
  the phase passed or blocked — so it stamps none, rather than putting `✅` on a blocked review;
  Block B has no marker field at all, which makes that structural rather than a habit. **Your**
  Block A slot line still carries a marker, and on a blocked phase that marker is yours to get
  right (`🔴`). Do not read the absence of one here as a pass.
- **`→ P3 PLAN` is a position on the map, not a destination.** It is the phase the standing SDLC
  puts next, which is knowable and true whatever the verdict; a blocked P4 returns to P3, and the
  line does not claim otherwise because it makes no claim about the outcome at all. `P7 RETRO`,
  which has no successor, reads `→ last phase`.
- **The tier caveat is short on purpose.** `predicted` and `agent-set` are two distinct claims —
  the tier is a prediction, not an observation, and an agent set it rather than code deriving it —
  and both must survive. Neither needs a full sentence to do so.

A block with **no title after the id** means no source on this host knew it; that is the honest
degradation, not a bug, and never a cue to go and fetch one.

If `render.py` refuses to construct the end block — a title carrying a marker glyph, say — the
boundary does **not** go silent: `phase_report.py` prints its pre-#2112 `PHASE END` two-line banner
instead and puts the typed refusal on stderr. A measurement already paid for must reach the
console, so an unconstructed line beats no line; seeing `PHASE END` on a console today means
exactly that happened.

### Block C — DECISION

Only when you need the human. Rare.

```
🔴 **<goal ref>** · P<n> <NAME> · <what is blocked>
  ↳ <the tradeoff in one line>
<Option A> / <Option B>. Recommendation: <one>.
```

Rules:
- Present ≤ 3 options. Always carry a recommendation.
- Never emit this for anything you can default and flag afterwards.

---

## 4. Prompts, too

The same identity applies when you *dispatch* work, not just when you report it.
Any subagent label, task title, branch name, or commit subject carries the goal
ref and the phase, so the user can read a task list as easily as a status block:

```
#2628 P5 IMPLEMENT — extract coherence validator
#2930 P6 REVIEW — author-blind review
```

---

## 5. Emission triggers (this is what fixes the randomness)

Emit **only** on these events, one block each:

| Trigger | Block |
|---------|-------|
| Work dispatched / slots (re)filled | A |
| A phase boundary is crossed | B |
| A background task or agent completes | B, then A if slot state changed |
| A merge lands | B |
| Blocked on a human decision | C |
| End of turn with nothing in flight | A |

Not a trigger: starting a tool call, finishing a tool call, reading files,
thinking, "checking on things", or any intermediate step. Those produce **no
output**.

---

## 6. Hard bans

- No narrating tool calls ("Let me check…", "Ran a command", "Used 2 tools").
- No bare `#123` — always bold, always linked when a URL exists.
- No slot line missing its phase token.
- No status tables, no ASCII boxes, no bullets nested below the `↳` line.
- No headers (`##`) inside any block.
- No restating the previous status block when nothing changed.
- No trailing offers ("Let me know if…", "Would you like me to…").
- No emoji outside the §2 set.

---

## 7. Worked example (the reference output)

```
Loop restarted at 2 slots:

* 🔵 **[#2628](url)** · P5 IMPLEMENT · Extract coherence validator · model opus (predicted; agent-set)
  ↳ resuming intact partial work; verifying byte-identity → guards → PR
* 🔵 **[#2915](url)** · P5 IMPLEMENT · Sync stale catalog-size docstrings · model tier unrecorded
  ↳ correcting 2 docstrings against measured counts → PR

Waiting on notifications — no polling.
```

```
**[#2915](url)** P5 IMPLEMENT → P6 REVIEW — PR [#2927](url), comments-only, both
docstrings corrected with verified counts (10 each), verify green. Reviewing it
myself since I didn't author it, then merging.
```

```
**[#1626](url)** P2 RESEARCH → P3 PLAN — dossier at `.sdlc/research/1626.md`, cost $0.31 (48,203
tokens, claude-sonnet-5). Writing the plan next.
```

```
Status — 22 merged in 24h:

* ✅ **[#2915](url)** · P7 RETRO · Sync stale catalog-size docstrings · model sonnet (predicted; agent-set)
  ↳ merged and recorded; no follow-up debt
* 🟢 **[#2628](url)** · P6 REVIEW · Complete WorldSubBuilder decomposition · model opus (predicted; agent-set)
  ↳ approved; merge handler waiting on CI → unblocks slice 1d
* ⏳ **#2632** · P2 RESEARCH · Decompose CharacterSubBuilder · model tier unrecorded
  ↳ seam analysis first, then slice 2a → dispatch after 1d lands

Waiting on the merge notification.
```

---

## 8. Scope

This contract governs status reporting. It does **not** apply to content the user
explicitly asked for — a report, a design walkthrough, a code explanation. Give
those in full. The ban is on unrequested narration, not on requested prose.

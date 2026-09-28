# Output contract — machine banner detail

The compact [output contract](output-contract.md) is normative. This section
preserves its longer explanation, provenance rules, and examples.

#### The machine half of a phase boundary (`phase_report.py`, not you)

`phase_report.py start`/`end` print at every phase boundary, from Sigma's Python, on every
host — which is why they are dependable where a model's prose is not. The two verbs are **not the
same shape, and that is deliberate**:

```
⚪ PHASE START · #1983 Resolve reviewer independence per host
   🟨 P2 RESEARCH · requested host model: unrecorded · predicted model tier: sonnet (agent-set)
**#1983 Resolve reviewer independence per host** P2 RESEARCH → P3 PLAN — 8m11s · $4.98 · tokens 52 in, 35,433 out · claude-opus-5.
```

`start` (`start_lines`) stays its own two-line announcement — a phase-badged line (`phase_label`,
#2485), then ONE line folding the requested host model (#2537) and the labelled predicted tier
together, no longer split across a separate third line. `end`, since #2112, is a Block B
**constructed by `render.py`** from the fact set `end_facts` builds (`ref`, `from_phase`,
`to_phase`, `fact`, straight through `render_block`/`emit`) — the same renderer, and the same
refusal discipline, as every other block in this contract. Read these, do not imitate them: the
shape is the machine's to write, not yours.

On every host, a dispatched phase passes its exact requested model selector to `start`, so its
line can name it. Claude passes its Task `model` value (such as `sonnet`) with
`--requested-model`; that display value does not change Claude's existing end check. Codex passes
the subagent's full model ID with `--host-model`, which its end check verifies against the
observed rollout. The end block's model field reports the model observed in the phase transcript,
via `end_measurements` — the one function both the constructed block and its fallback banner (next
section) share, so the numbers can never read differently between the two.

Two absences in that sample are deliberate, and neither is a slot line going without its axes:

- **The end block carries no §2 marker.** `render.EVENT_FIELDS` has no `marker` key at all —
  `phase_report.py end` is called the same way whether the phase passed or blocked, so it cannot
  know a verdict, and stamping `✅` on a blocked review would be a lie. **Your** Block A slot line
  still carries a marker, and on a blocked phase that marker is yours to get right (`🔴`); the
  machine half simply does not answer that question. Do not read the absence of a marker as a pass.
- **`to_phase` is a position on the map, not a destination.** `end_facts` sets it from
  `next_phase_kind`, the phase that follows this one in the standing SDLC, or `PHASE_LAST` on
  `P7 RETRO`, which has none. A blocked P4 goes back to P3, and the block does not know that — it
  names where the map points, not where the run is actually going.

A block with **no title after the id** means no source on this host knew it; that is the honest
degradation, not a bug, and never a cue to go and fetch one.

**If `render.py` refuses to construct the end block** — a title carrying a marker glyph
(`marker-glyph-in-field`) or any other malformed fact — the boundary does not go silent.
`render_block` returns `None`, `emit` falls back to `phase_report.py`'s pre-#2112 two-line
`PHASE END` banner (`end_lines`, built from the same `end_measurements` numbers as the constructed
block), and the typed refusal goes to stderr:

```
🟨 PHASE END · #1983 Resolve reviewer independence per host
   🟨 P2 RESEARCH · 8m11s · $4.98 · tokens 52 in, 35,433 out · claude-opus-5 · next: 🟧 P3 PLAN
```

A measurement already paid for must reach the console, so an unconstructed line beats no line —
seeing this two-line shape on a console today means exactly that a refusal happened, not that
#2112 never landed. Note the fallback still says `next: 🟧 P3 PLAN` / `last phase`, `_tail`'s
pre-#2112 wording: it is a different code path from the constructed block's `→ P3 PLAN`, not a
second spelling of the same fact, and the two must not be conflated when reading a transcript.

**A third verb, `step`, announces one plan step inside P5 IMPLEMENT — never a phase boundary:**

```
🟦 STEP 2/7 · #1983 Resolve reviewer independence per host
   🟦 P5 IMPLEMENT · Write the failing test for the title cap
   requested host model: gpt-5.6-terra · predicted model tier: sonnet (phase)
```

It fires once per plan step from whatever executes the plan: the session itself when a phase runs
inline (the usual default path), or the implement subagent when a phase is dispatched. A dispatched
Codex phase with `model_selection: off` first maps its explicit `off` fallback through `host-model`;
`auto` instead supplies the predicted portable tier. The tier names its source — `(step)` for a tier resolved
for that step alone, `(phase)` for the one `start` recorded — and reads `unrecorded`, never another
phase's, when neither exists. The requested host model is the exact ID supplied at dispatch when
recorded, or `unrecorded` otherwise. A step only uses the phase's host model when its caller
explicitly says `--phase-model` (the same phase agent is doing the step). A separately dispatched
step supplies both its own `--host-model` and `--model` tier; without either provenance flag the ID
is unrecorded, even if its tier
matches the phase's. `PHASE END` measures the phase agent's observed model from its transcript; a
separately dispatched step's model remains a requested ID unless that step's own rollout is checked.
Like `PHASE END` the step banner leads with its phase's badge, never a §2 marker, and writes
nothing: no marker, no ledger event, because a step is not a place
`ledger.PHASE_KINDS` recognizes and `end` measures from the marker `start` left. From a dispatched
subagent it prints into that subagent's own transcript, which a host may not show you — not
verified end to end. Read the three-line banner, do not imitate it in prose, and do not expect it
outside P5 IMPLEMENT.

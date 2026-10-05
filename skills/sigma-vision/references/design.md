# Deep pass — Design (L3: the interaction shape + quality bar)

Loaded on demand by `sigma-vision` for a deep Design pass. Fills the **## Design** section of
`.sdlc/context/north-star.md`. Horizon: short-term (revisit every ~3–4 weeks). The user owns the
words; examples beat adjectives, hard rules beat soft principles.

## Pre-flight
- Vision and Strategy should exist — read both; every design claim must trace to the Strategy bet.
- Skim the repo's UX surface (entry points, CLI/UI, error messages) for how it behaves today. Draft
  from what's there.

## Elicit (one or two at a time)
1. **Core interaction shape** — in one sentence with an active verb, what does a user *do*? What's the
   smallest unit that delivers value?
2. **The artifact** — what does the user take away, and what's the polish bar? Give examples: great /
   acceptable / unacceptable.
3. **Voice & tone** — what does it sound like (messages, errors, output)? What does it explicitly
   *not* sound like?
4. **Quality bar** — the floor you refuse to ship below, the ceiling you'll slow down for, and the
   current gap.
5. **Hard rules** — what must the product *never* do? *always* do? What time / friction / cognitive
   budget does the user have?
6. **Negative space** — what are you *not* designing for this cycle, and which decisions are deferred
   on purpose?

## Stress-test
- Point at one current surface that violates this intent — why does it exist, should it?
- If a change tomorrow took a shortcut against this (e.g., demanded more input before showing any
  output), would the intent catch it?

## Write into `## Design`
Interaction shape · the artifact + polish bar · voice/tone · quality bar (floor / ceiling / gap) ·
hard rules · negative space. Prefer concrete examples over adjectives. Never overwrite a filled tier
without asking.

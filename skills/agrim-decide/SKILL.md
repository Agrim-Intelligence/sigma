---
name: agrim-decide
description: Turn an architectural decision into an enforced invariant. Use for standing rules, enforce-this requests, or /agrim-decide.
allowed-tools: Bash(python3 *), Bash(git *)
---

# agrim-decide

Detailed selection triggers: [selection](references/selection.md).

The registry behind Sigma's **only non-negotiable gate**. Every other guardrail here is
discipline a model is asked to follow — and a model can talk itself past discipline, especially at
3am on iteration forty of an autonomous run. A registered invariant is different: the edit is refused
by a script that does not negotiate.

Registry: `.sdlc/decisions.json`. **Authoring it is the opt-in** — no registry, no behavior.

## What belongs in here (and what doesn't)

The bar is narrow on purpose. A registry full of things that don't matter trains everyone to click
through the prompt, and then it protects nothing.

**Record it when all three hold:**
1. Breaking it causes real damage — corrupted data, a security hole, money spent, a silent wrong
   answer. Not "inconsistent with our style".
2. **Nothing else already catches it.** If a linter, type checker, schema, or CI job fails on it,
   that's your enforcement — a second copy just rots out of sync. This is for the rules that are
   invisible to tooling.
3. It's expressible as a **value constraint on a named thing in known files** — `timeout ≤ 30`,
   `verify_ssl == True`, `region in [...]`. A rule you cannot write as a comparison belongs in the
   north-star as prose, not here.

**Don't record:** style preferences, anything a formatter owns, aspirations ("we should eventually…"),
or a rule whose real enforcement is a code review. Those are north-star material —
`.sdlc/context/north-star.md` — and `agrim-plan-review` §4 already holds plans to them.

**The relationship:** the north-star holds *all* your architecture rules as numbered prose; this
registry holds the small subset that is mechanically checkable. Most rules never make it here, and
that's the expected outcome — say so rather than manufacturing entries to fill the file.

## Recording a decision

Add an entry to `.sdlc/decisions.json`:

```json
{
  "version": 1,
  "decisions": [
    {
      "id": "INV-001",
      "title": "Connection timeouts stay bounded",
      "class": "invariant",
      "status": "active",
      "statement": "No outbound call may configure a timeout above 30 seconds.",
      "rationale": "An unbounded timeout turns one slow dependency into a full outage; we shipped that twice.",
      "protected_paths": ["src/**/*.py", "services/**/client.py"],
      "protected_params": [{"name": "timeout", "op": "le", "value": 30}]
    }
  ]
}
```

- **`id`** — a single token: **no internal whitespace, no colons**. A denial's id is carried
  downstream as a real, structured `decision_id` event field, not reconstructed by parsing prose —
  but only if the id itself is well-formed. `"DEC 001"` breaks that contract (a multi-token id is
  indistinguishable from a real single-word one once it's inside free text); `validate` (below)
  catches this at authoring time, before the id is ever attached to a real denial.
- **`class`** — `invariant` **denies** the edit; `recipe` **asks** (a proven-good default worth a
  second thought, not a law). Start at `recipe` if you're unsure; promoting later is cheap, and a
  false deny costs you trust in the gate.
- **`protected_paths`** — glob(s), repo-relative. A param is only ever checked inside its own
  decision's paths, which is what stops a common name like `timeout` from tripping everywhere.
- **`protected_params`** — `op` is `eq` / `ge` / `le` / `in`. Only literal assignments are judged
  (`name = 30`, `name: 30`, `"name": 30`); an expression or a variable is left alone, because the
  gate refuses to guess at values it cannot actually read — that includes an arithmetic expression
  built entirely from literals (`name = 48 * 3600` is just as invisible to it as `name =
  CONFIG.default`).
- **`caution_on_touch: true`** — no params, just ask on *any* edit inside these paths. For code that
  is dangerous to touch at all: anything that deploys, migrates, spends money, or deletes.
- **`statement` and `rationale`** are what the agent is shown when it's blocked. Write the rationale
  for someone who wasn't there — "we shipped that twice" prevents the rule being re-litigated far
  better than "best practice" does.

Then confirm it's well-formed and that your code already complies:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/decision_gate.py" validate .
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/decision_gate.py" check .
```

`validate` catches entries that can never fire (no paths, no params, a bad op) — the failure mode of
a registry is being quietly unenforceable, not loudly broken. `check` scans files already on disk:
the hook only guards edits going forward, so anything predating it shows up here. **Expect
violations on the first run** — that's the registry doing its job before it has ever blocked
anything. Fix them or narrow the rule; a rule your own code breaks is a rule nobody will respect.

## Changing a decision — supersede, never rewrite

Editing the registry always prompts, on purpose. To change a recorded decision:

1. Add a **new** entry with a new `id` and `"supersedes": "<old-id>"`.
2. Flip the old one to `"status": "superseded"` — leave it in the file.
3. Say why in the new entry's `rationale`.

The superseded entries are the record of what you used to believe and why you stopped. Deleting them
throws away the only evidence that the decision was ever considered — and the next person to propose
the old idea will have nothing to read.

## When it fires

- **Denied** — the edit is refused with the statement and rationale. Either the change is wrong, or
  the invariant is out of date. If it's the invariant, supersede it first; that's a decision, made
  deliberately, not something to route around mid-edit.
- **Asked** — a `recipe` violation or a `caution_on_touch` path. Read the statement and decide.
- **Nothing** — the common case. A registry you never notice is a healthy one.

## Honest limits

Say these plainly rather than letting someone over-trust the gate:

- It reads **literal assignments only**. `timeout = CONFIG.default` is invisible to it. It is a
  seatbelt against the obvious mistake, not a proof of compliance.
- It sees **the text of an edit**, not the program. Nothing stops the same value arriving at runtime
  by another route.
- It **fails open** — any internal error allows the call, because a gate that wedges you on its own
  bug is worse than a missed check. `check` is the backstop that catches what the hook let through;
  wire it into CI if the invariants genuinely matter.
- Comment stripping is naive, so a `#` inside a string literal may hide a violation from it. It errs
  toward allowing.

## Turning it off

`.sdlc/config.json` → `{"gates": {"decision_gate": {"enabled": false}}}` disables it without
deleting the registry — useful during a refactor that intentionally moves an invariant. Turn it back
on in the same PR that lands the supersession.

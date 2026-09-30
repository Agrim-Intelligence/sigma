---
name: agrim-retro
description: After shipping, assess residual product and structural debt and propose durable lessons for approval. Use in Retrospective or /agrim-retro.
allowed-tools: Bash(python3 *), Bash(git *), Bash(gh issue *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-retro

Detailed selection triggers: [selection](references/selection.md).

The **Retrospective (Learn)** phase executor — Sigma's backward-learning loop, so a goal doesn't
just ship, it *teaches*. Runs at the end of a goal, **after Review**. Grade **intent-vs-shipped as an
independent, project-informed pass** — under `config.review.independent` (default on) the loop runs it
as a fresh subagent, so the honest "did we build what the goal asked, and what debt did the narrow fix
leave?" isn't answered by the same context that just argued the work was done. It is **advisory**: it
proposes, writes only the audit-trail note and the (gated, opt-in) KG corpus note freely, and **parks**
any north-star or standing-rule change for your approval — it never rewrites your standing docs
unattended (mirrors how `agrim-kg`'s `maintain` only proposes).

## Executor resolution (host-aware)
No `superpowers` / `code-review` companion covers retrospective learning, so this is **always
Sigma's own**. It *complements* the Review phase's `superpowers:verification-before-completion`
(which asks "is it correct / done?") — it does not duplicate it (this asks "what did we learn, what
debt remains?"). Pure markdown discipline + `git` / `python3`, so it degrades gracefully on any host.

## Pre-flight — gather the evidence (repo-auto-detect, fail-open)
Read what this specific repo offers; skip whatever's absent (never break the run):
Locate evidence with `rg --files` and `rg -n` before opening it. Read only the
relevant lines for the retrospective; direct bulky command output to a scratch file
and inspect a bounded excerpt rather than carrying it in the conversation.

- **The original intent** — read `.sdlc/acceptance/<goal-stem>.md` verbatim, or use
  `review_context.py brief .sdlc "$goal" --for retro`, which includes it. This P1 record is
  authoritative even if the issue changed. If missing/invalid, report that original acceptance
  is unavailable and do not claim achieved. The goal's current text supplies context: the `.sdlc/goals/NNNN-*.md` file (local mode) or the
  issue body (`gh issue view "$goal"`, github mode).
- **What shipped** — the diff for this goal's work (`git diff` / `git log` over its branch or commits).
- **The journey** — `.sdlc/journey/<goal>.md` (local) or the issue timeline (github): the phase notes
  and 🔒 Critical Insights recorded as the goal ran.
- **Standing context** — the repo `README`, the **north-star**, `.sdlc/project.md`, and any
  `CLAUDE.md` / `AGENTS.md` governing the paths the goal touched. The north-star is not at a path you
  can assume: `.sdlc/` is gitignored, so it is absent from the goal worktree this runs in (#1778).
  Take it from the brief when one carried it, else `python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/north_star.py"` —
  `present <path>` read it; `absent` is a drop-in project (skip, as ever); `unreachable` means the
  **Direction** check below was not run, and the grade says so instead of assuming alignment.

## 1. Structural reflection — the debt the narrow fix left behind
- **Multi-site smell** — did one logical fix touch 2+ places? That's a missing shared contract /
  abstraction; name it.
- **Coverage themes** — did review keep finding the same class of gap (e.g. "no test exercises the real
  path")? Propose the convention or helper that closes it.
- **Deferred roots** — for each thing the plan left out, classify: *tactical* defer (fine, leave it) vs
  *structural* defer (a missing primitive — promote to a recommendation).
- **Rule alignment** — did the work reinforce a standing rule (cite it), or reveal one worth adding or
  retiring?

## 2. Product reflection — the gaps the work revealed
- **Friction / quality signals** — where did the work feel like fighting the product? A UX or feature gap?
- **Bugs that are features** — did the goal want something the system had no place for? Name the missing
  capability.
- **Direction** — does this advance the north-star / strategy, or drift out of scope?
- **Negative space** — what should the run have produced but didn't?

## 3. Intent-vs-shipped + the three-store learning harvest
**Intent-vs-shipped** — grade each criterion in the recorded acceptance against the shipped
diff and verification evidence; cite the evidence or name the gap for every item. The overall
grade is achieved only if every criterion is achieved; otherwise explain partial/diverged:
- **achieved** — the intent is realized;
- **partial** — realized for some of it; *name the residual gaps* and confirm each has a tracking item;
- **diverged** — what shipped differs from the intent; say how and why.

This grade is captured structurally, not by a separate command: carry it forward to the `record`
step that follows Retrospective — `loop.py record`'s own `--retro-grade achieved|partial|diverged`
flag (issue #1013). Do not also run `loop.py emit ... retro --grade ...` here; the record call
already happens unconditionally, so a second, separate emit would double-count the grade as two
events for one retrospective.

**Route each durable lesson to the right store.** Most stop at the first; the rest are *proposed* and
*parked* for approval:
- **Audit trail** — rationale worth re-reading later → record it on the goal:
  `python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/loop.py" note .sdlc "<goal>" "retro: <lesson>"`
  (comments the issue in github mode, appends to `.sdlc/journey/` in local mode). Safe to write freely.
- **North-star** — if the build *taught the strategy or architecture* (a bet confirmed / refuted, the
  code's shape now differs from a rule) → **propose** an edit to `.sdlc/context/north-star.md` and let
  the user approve it. Don't auto-write.
- **Standing rule** — a lesson that must gate *every* future plan / review **and isn't mechanically
  enforced** (if a linter / type-checker / CI already catches it, a rule is redundant) → **propose** a
  numbered rule for `.sdlc/project.md` or the governing `CLAUDE.md`. Rare; always parked.
- **Registered invariant** — the rare standing rule that is *also* a value constraint on a named thing
  in known files (`timeout ≤ 30`, `verify_ssl == True`). Nothing else catches it, and prose won't stop
  it recurring — so propose an entry for `.sdlc/decisions.json` via **`/agrim-decide`**, and the next
  edit that breaks it is refused rather than reviewed. Rarest of all: if you can't write it as a
  comparison, it's a standing rule, not an invariant.

Route by test: audit-trail = "worth re-reading"; north-star = "changes our direction or shape"; standing
rule = "must gate every change and nothing else enforces it". De-duplicate — a lesson seen across
multiple goals is higher-confidence and a stronger candidate for the north-star or a rule.

## 4. KG corpus note — the going-forward half of the knowledge graph
**Only runs when `knowledge_graph.enabled` is `true`** (check with
`python3 "${CLAUDE_SKILL_DIR}/../agrim-kg/scripts/kg.py" status .sdlc` — the same gate `/agrim-kg`
itself uses at its own step 1). A project that never turned the graph on sees zero behavior change
here. When enabled, this closes the loop `/agrim-kg`'s corpus depends on: without it, the graph only
grows when someone manually backfills a note, defeating the point of `auto_refresh`.

Write one compact, **factual** note — every claim must trace to something this retro actually read
in Pre-flight (the goal's own issue/file, the diff, the journey/timeline); never invent a summary or
a "key insight" that wasn't actually found. Match the format the historical backfill already
established (`.sdlc/knowledge/analysis/issue-*.md`):
- `# Issue #<N>: <title>` (or `# Goal <id>: <title>` in local mode) as the header.
- `**Closed:** <date>`, `**Labels:** <labels>`, `**URL:** <url>` (omit any field this repo/mode has
  no source for — never fabricate a placeholder).
- A 2-5 sentence factual summary of what shipped, drawn from the real PR/issue/diff.
- An optional `**Key insight:** <one line>` — only if this retro's own §3 harvest actually surfaced
  a durable lesson; omit the line entirely rather than manufacture one.

Write it: `echo "$note_text" | python3 "${CLAUDE_SKILL_DIR}/../agrim-kg/scripts/kg.py" note .sdlc "$goal"`
— `kg.py note` derives the right filename itself (`issue-<N>` in github-discovery mode,
`goal-<NNNN>` in local-goals mode; see its own `note_id()` docstring) and is a no-op when the graph
is disabled, so this call is always safe to make. Skipping it is not silent: when the graph is
enabled, `loop.py record --retro-grade` checks for this file (`kg.py note-check`) and prints one
stderr line naming the expected path if it is missing — a warning only; the record itself is never
refused.

This writes the note; it does **not** rebuild the graph, and you should not rebuild it by hand
either. The rebuild belongs to `loop.py record`: when `knowledge_graph.auto_refresh` is `true` it
calls `kg.py refresh` after recording the goal's outcome (issue #1562) — which is *after* this note
lands, so the note written here is already in the corpus that rebuild reads. Until #1562 this
paragraph pointed at "this skill's own step 3 above" for a rebuild step 3 never performed, and
nothing anywhere called a builder. Fail-open, like every other step in this skill: if `kg.py` is
missing or the write fails, note it and continue — retro never breaks a run over this.

## 5. Standing-doc rot — what this goal made obsolete
Docs grow by addition and shrink by nobody. Adding a rule has an obvious moment; retiring one never
does, so this is that moment. Ask what the goal just made redundant, and **propose** each removal the
same way — parked for approval, never written unattended:

- **A rule now enforced mechanically.** If the work added a linter rule, a type constraint, a schema
  check, or a CI job that catches what a numbered rule describes in prose, the prose is redundant —
  propose demoting it. Verify the enforcement is real and covers the whole rule before proposing;
  a rule half-enforced still needs its prose.
- **A rule whose premise moved.** The goal changed the shape the rule was written against. Propose
  the correction, quoting the old line and the code that now contradicts it.
- **Superseded plans and roadmaps.** A plan under `.sdlc/plans/` whose work just shipped is finished
  history — propose archiving it. This matters mechanically as well as tidily: the hard plan-gate
  treats any *recent* file under `.sdlc/plans/` as a fresh plan, so stale plans left lying there
  weaken the gate.
- **Nothing rotted** is the common answer and a fine one. Say it in a line and move on — do not
  manufacture a demotion to look thorough.

Archive, never delete: move superseded files aside so git history stays readable, and leave anything
append-only (a decision log, the journey trail) untouched — those are dated records, not stale docs.

> The mechanical counterpart runs in `/agrim-doctor`, which reports standing-doc references that no
> longer resolve. That one is a script and needs no approval; this one changes meaning, so it asks.

## Output
A short retro: the intent grade (achieved / partial / diverged) + residual gaps, the structural +
product findings, and a **proposals table** (lesson → store → the exact edit), with every standing-doc
change clearly marked **needs your approval**. Record the audit-trail notes as you go; hand the parked
proposals to the user. Additions and retirements share the one table — a proposed demotion (§5) is the
same kind of parked change as a proposed new rule, and listing them together is what keeps the standing
docs from only ever growing.

**Autonomous (`/agrim-loop`) mode:** run the reflection and **write only the audit-trail notes** (§3)
**and the KG corpus note** (§4, when `knowledge_graph.enabled`) — both are safe, additive writes to
Sigma's own stores, never a standing doc. Write the proposals into the journey and **park** every
north-star / standing-rule change to the review queue for a human — never edit a standing doc
unattended. Fail-open: if `git` / `gh` / a file is missing, note it and continue. Retro never breaks
a run.

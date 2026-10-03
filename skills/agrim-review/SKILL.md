---
name: agrim-review
description: "The Review phase — a thorough code-quality review: a quantitative KPI pass, then a qualitative scan across eight axes (correctness & intent, concurrency & state, performance & resources, security, observability, structure & maintainability, intelligibility, test adequacy), with a diff-review mode for PRs and a counter-review mode that grades an external reviewer (Cursor / GPT / SonarQube). Use at Review, or when the user runs /agrim-review."
allowed-tools: Bash
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-review

Detailed selection triggers: [selection](references/selection.md).

## Context-efficient inspection

Locate before opening: use `rg --files` for paths and `rg -n` for symbols or claims.
Read only the relevant lines with a bounded range; expand outward only when the
boundary is unclear. Do not dump whole files or broad search output into context.
For bulky tests or diagnostics, save full output to a scratch file, then inspect
its exit status and focused matches or a bounded tail. Do not hide failures, and
keep secrets out of the scratch file. This is guidance, not a measured savings claim.

**The reviewer must not be the author** — and that is a property of how this review is RUN, not a
claim you can make about yourself. Resolve it before judging anything:

`python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/reviewer.py" resolve .sdlc`

It prints one JSON object; branch on `mechanism`:
- **`subagent`** — assemble the brief with `review_context.py brief .sdlc "<goal>" --for code-review
  [--artifact <path|PR#>]`, hand a FRESH subagent **only that**, and let it review. You do not. Sigma cannot prove who ran it; it binds the verdict to one PR, head and brief generation only.
- **`process`** / **`command`** — run the checked brief with `reviewer.py run` below. It invokes the
  resolved command in a fresh process and enforces `review.timeout_seconds`.
- **`inline`** — no mechanism is available on this machine. Review inline, and say so in the verdict:
  you are not independent, and asserting otherwise does not make you so.

**If a resolved `process`/`command` FAILS** — unauthenticated, non-zero exit, timeout, empty output —
it does **not** silently become an inline self-review. Fall back to inline, stamp the provenance
accordingly, and name the failure in the verdict.

**If this brief already reached you, you ARE the resolved reviewer — do not re-resolve and do not
dispatch again.**

Whatever the mechanism, the brief is the reviewer's ONLY input. `brief` prints to stdout, so redirect
it and check the file, naming the scratch directory the maker can write:

```
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/review_context.py" brief .sdlc "<goal>" \
  --for code-review > /tmp/brief-<goal>.md
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/reviewer.py" check /tmp/brief-<goal>.md \
  --scratch "$(pwd)/.sdlc/work/<goal>"
```

For **`process`** / **`command`** only, execute the checked brief. `run` repeats the leak check on
the exact text it sends, stops the process group on timeout, and rejects non-zero or empty output:

```
python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/reviewer.py" run .sdlc /tmp/brief-<goal>.md \
  --scratch "$(pwd)/.sdlc/work/<goal>"
```

It exits non-zero if the brief NAMES a shared scratch path — a file the maker can write and the
reviewer would read is a maker→reviewer channel. It deliberately does **not**
flag implementation content: a breaker must never see the implementation, but a reviewer must — the
diff is the artifact under review.

**A diff-only review misses blast radius:** the diff is the *change*, the codebase is
the *impact surface*. Before judging any change, read the code around it and grep every caller — you
have full repo access. A small change with a wide radius is what a review exists to catch.

**Executor resolution (host-aware):**
- **Claude Code + the `code-review` plugin installed** → prefer **`/code-review`** +
  **`superpowers:requesting-code-review`**.
- **Otherwise** (Cursor / any host / no companion) → use this. Same discipline, portable.

Pick the mode from the request:
- **Diff review** (default before a merge) — review the branch's changes vs base. Fast, targeted.
- **Whole-repo audit** — not this skill. A periodic sweep for what accumulated across many changes is
  `/agrim-audit`, which measures the tree first and judges it against four lenses. Auditing a whole
  repo here would re-derive that measurement every run and blur the diff review this skill is for.
- **Counter-review** — the user pasted an external review (Cursor, GPT, SonarQube, another agent):
  evaluate each finding critically — accept what's right, push back on what's wrong, add what it missed.

## 1. Quantitative pass (a KPI dashboard)
Run the objective tools the repo has and report the numbers, so quality is measurable at a glance:
- **Python:** `ruff`, `mypy`, `pytest --cov` (lint / type errors, coverage %).
- **JS/TS:** the repo's `eslint` / `tsc` / test-coverage.
- **Hotspots:** `git log` churn × current size — the files most likely to hide risk.

Skip any tool the repo doesn't use; never invent numbers (see `agrim-verify` — evidence only).

## 2. Qualitative scan — the eight axes
Every axis, every review, each finding with `file:line` evidence. An axis you skipped is a blind spot,
not a saved token — say so rather than imply coverage you did not have. Name the specific fault;
"this could be cleaner" is not a finding.

These eight axes merge four sources; each names the harm it prevents and how to detect it. Where an
axis's boundary is unclear (e.g. the axis-4/axis-5 OWASP A09 split, or the axis-6/axis-7 near-miss),
decide by the harm each axis names. The full canon — each axis's harm, sources and detection, and
the precision rules — is [axes](references/axes.md).

1. **Correctness & Intent** — *ships the wrong thing.* Walk the diff against the goal's acceptance
   criteria, not just against itself. Name the inputs that break it: null, empty, zero, negative, max,
   malformed, duplicate. Half-done paths, shipped TODOs, an error path that never returns.
2. **Concurrency & State** — *right alone, wrong together.* For every shared mutable value the change
   touches: what guards it? Every awaitable awaited, every task's exception surfaced rather than
   swallowed. Then run it twice in your head — is it idempotent, and is a retry safe?
3. **Performance & Resources** — *degrades under load or over time.* A query inside a loop (N+1),
   blocking I/O on a hot path, superlinear work on user-controlled input, unbounded growth in a
   long-lived collection. Every acquired resource — file, socket, connection, task — released on the
   FAILURE path too, and every network call with a timeout and a way to cancel.
4. **Security** — *a hostile caller reaches something they should not.* Trace inputs -> validation ->
   persistence -> output. Access control, injection (SQL / command / template / prompt), secrets in
   logs or errors, unpinned dependencies. On a real surface run `/agrim-security-review` for the full
   OWASP pass rather than free-handing it here.
5. **Observability** — *it breaks in production and nobody can tell why.* At each new failure point:
   does anything record that it happened, with enough context to diagnose it and nothing sensitive in
   it? A metric or span on a new surface. A security-relevant event that is never logged is a silent
   breach.
6. **Structure & Maintainability** — *the next change is unsafe to make.* Name the smell rather than
   gesturing at "structure": Long Method, Large Class, Long Parameter List, Primitive Obsession
   (cohesion) · Feature Envy, Inappropriate Intimacy, Message Chains, Middle Man (coupling) · Shotgun
   Surgery, Divergent Change (change amplification) · Dead Code, Duplicate Code,
   Speculative Generality (dispensables). And: does the change fight the existing design, or diverge
   from the surrounding patterns?
7. **Intelligibility** — *correct but unreadable, so the next change breaks it.* Do names say what the
   thing does and what it mutates? Do comments explain WHY, not restate WHAT? Is any comment, docstring
   or README now FALSE because of this diff — a comment that lies is worse than no comment at all.
8. **Test Adequacy** — *unverified, so the next change breaks it silently.* Not "is there a test" but
   **would it fail if the code were wrong?** A test that asserts a mock, or asserts the implementation
   rather than the behaviour, is not coverage. Boundaries tested, not only the happy path.

Two gates run alongside the axes:
- **Project-rule violations** — anything breaking the project's rules: the **north-star Architecture
  Rules** + governing **`CLAUDE.md`** conventions. Quote the rule. **Never open the north-star by its
  path** — `.sdlc/` is gitignored, so it is absent from the goal worktree this phase runs in (#1778).
  The brief either carries a north-star section or names it as a gap: **check which, don't assume**.
  If it is a gap — or you are reviewing with no brief at all — resolve it yourself with
  `python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/north_star.py"`, and branch: `present <path>` read it;
  `absent` is a drop-in project, so there are no such rules to break; `unreachable` means the verdict
  **states that project-rule conformance was not judged**, never that it passed.
- **Conditional-risk surfaces** — run `bash "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/risk-detect.sh"`
  (read-only, fail-open, secret-safe — it emits location only, never a matched value) over the change.
  For each category in its `matched`, ALSO run the dedicated review it names — `migration` →
  `/agrim-migration-check`, `contract` → `/agrim-contract-check`, `sensitive` → `/agrim-security-review`.
  The detector is a bash trigger (zero LLM cost); it names the risk, the skill judges it. A code-quality
  pass alone will not catch a broken public contract or an unsafe migration.

## 3. Verdict + findings
Open with **how this review was run** — `Reviewed by: dispatched subagent | fresh process (<host>) |
operator command | inline, author self-review` — then an **overall verdict**, then findings by
category, most-severe first:
**Bug** (will misbehave) · **Concern** (design risk) · **Coverage** (untested behavior) · **Missing**
(a required piece absent) · **Good** (worth keeping) · **Minor / Nit** (style). Each: what, where
(`file:line`), why it matters, and the fix. Don't pad with praise; if you didn't try to break it, you
didn't review it. Rules are lessons, not laws — a finding can be waived with a stated reason.

### Before you report it
**A finding is a claim in prose, and a claim in prose is not evidence** — the north-star's own
Standing Rule 1, binding the reviewer exactly as it binds the code. Before reporting one, state its
**failing case**: the inputs or the sequence, and the wrong behaviour they produce. If you cannot
write that case you do not have a **Bug**, you have a question — ask it as a **Concern**, or drop it.

**Never report these.** Each costs a fix cycle and buys nothing:
- A problem that pre-dates the change and stays dormant — *unless this change puts it on a live path*.
  A latent bug the diff just made reachable is this review's finding, not the last one's.
- Anything a linter, type-checker or the test run already catches — those gates run separately.
- A style preference not written down in `CLAUDE.md` or the north-star. Quote the rule, or drop it.
- A behaviour change that is plainly the point of the change.
- Something deliberately silenced in the code (a `noqa`, a documented waiver) — argue the waiver, not the line.

### Blocking, or follow-up
Not every real finding should stop the change, and in the loop that is not a matter of taste: a
blocking finding costs a full fix -> push -> re-review round, and `work.max_review_cycles` (default 3)
**parks the goal** once they run out. Spend them deliberately:
- **Blocking** — you wrote the failing case, and it is a **Bug** or **Missing**; or a risk review
  returned `critical`/`high`. The reasons you give ARE the fix instructions: write them so the next
  pass can act without re-deriving the problem.
- **Follow-up** — real, but the change is better landed than held: **Concern**, **Coverage**, **Nit**
  — or a **Missing** you could not write a failing case for (an absent piece, like missing
  observability, that resists "the inputs and the wrong behaviour they produce" rather than a
  probeable check). File it with `--label sdlc:followup` instead of blocking.

Reporting a real finding as follow-up is a judgement you are allowed to make. Reporting one you could
not write a failing case for, as blocking, is the failure this section exists to prevent. A
**Missing** with no constructible failing case is exactly this case, not an exception to it: it
still gets reported, as Follow-up per the bullet above — never dropped for having nowhere to go.

## Counter-review
When grading an external review, for each of its findings: **accept** (correct), **downgrade / reframe**
(overstated), or **reject** (wrong) — with evidence — then add the **stronger findings it missed**. End
with a one-line read on the reviewer's overall signal.

## In the loop
Findings feed the Review phase; a real bug is **FIX-FIRST** (back to Implement). Verify any "fixed"
claim with `agrim-verify` before moving on.

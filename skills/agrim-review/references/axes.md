# Review axes

The review axis set Sigma reviews against, and what each axis is *for*.

Derived by merging four canonical frames and de-duplicating every overlap, so each concern has
exactly one home. Partitioned by a single principle — **what kind of harm does this cause** — which
is what makes the set MECE: an axis is not a topic, it is a failure mode with a distinct victim.

Sources merged:

- [Google eng-practices — What to look for in a code review](https://google.github.io/eng-practices/review/reviewer/looking-for.html) — 12 dimensions
- [Fowler / Beck code smells](https://refactoring.guru/refactoring/smells) — 22 smells in 5 families
- Clean Code ch. 17 — ~60 heuristics (C1–C5, F1–F4, G1–G36, N1–N7, T1–T9)
- [Production-hardening checklist](https://www.sachith.co.uk/code-review-checklists-that-work-production-hardening-practical-guide-jun-9-2026/) — 7 runtime axes
- [OWASP Top 10:2021](https://owasp.org/Top10/2021/A00_2021_Introduction/) — A01–A10

---

## The axes

| # | Axis | Harm if unchecked | How we detect it during review |
|---|------|-------------------|-------------------------------|
| 1 | **Correctness & Intent** | Ships the wrong behaviour — or the right behaviour to the wrong requirement | Re-read the goal's acceptance criteria, then walk the diff against them line by line. Enumerate the inputs that break it: null, empty, zero, negative, max, malformed, duplicate. Check every early return and error path actually returns. Flag half-done paths, shipped TODOs, and silently-swallowed failures. Absorbs Google *Functionality*; Clean Code G2, G3, G4 |
| 2 | **Concurrency & State** | Correct in isolation, wrong under simultaneity or replay | Find every shared mutable value the change touches and name what guards it. Check `await` on every awaitable, and that no exception is discarded in a task/handler. Ask what happens if this runs twice — is the operation idempotent, and is retry safe at-least-once? Look for lock ordering that can invert. Absorbs Clean Code G31 |
| 3 | **Performance & Resources** | Passes review, degrades in production under load or over time | Look for a query inside a loop (N+1), a synchronous or blocking call on a hot path, an unbounded collection or allocation in a tight loop, and any algorithm that is superlinear where the input is user-controlled. Separately: every acquired resource — file, socket, connection, subscription, task — must be released on the failure path too, and every network call needs a timeout and a cancellation path |
| 4 | **Security** | A hostile caller reaches something they should not | Walk OWASP A01–A10 against the change's entry points: access control (A01), crypto (A02), injection incl. SQL/command/template/prompt (A03), design-level abuse (A04), misconfiguration and hardcoded secrets (A05), vulnerable or unpinned dependencies and supply-chain integrity (A06/A08), authentication (A07), SSRF (A10). Trace inputs → validation → persistence → output. Each finding carries a severity and a concrete remediation. *A09 is owned by axis 5* |
| 5 | **Observability** | It fails in production and nobody can tell why | At each new failure point ask: does anything record that this happened, with enough context to diagnose it and no sensitive data in it? Check for a metric or span on new surfaces, structured rather than string logging, and health/readiness coverage for anything new that can be down. Includes OWASP A09 — a security-relevant event that is never logged is a silent breach |
| 6 | **Structure & Maintainability** | The next developer cannot change it safely, and complexity compounds | Name the smell, do not gesture at "structure". Cohesion — Long Method, Large Class, Long Parameter List, Primitive Obsession, Data Clumps. Coupling — Feature Envy, Inappropriate Intimacy, Message Chains, Middle Man. Change amplification — Shotgun Surgery, Divergent Change, Parallel Inheritance. Dispensables — Dead Code, Duplicate Code, Speculative Generality, Lazy Class, Data Class. Plus: does the change fight the existing design, and does it match the surrounding patterns? Absorbs Google *Design + Complexity + Consistency* |
| 7 | **Intelligibility** | The code is correct but unreadable, so the next change breaks it | Do the names say what the thing does and what it mutates? Do comments explain *why*, not restate *what*? Is any comment, docstring, or README now false because of this diff — the comment that lies is worse than no comment. Flag magic numbers, obscured intent, and negative conditionals. Absorbs Google *Naming + Comments + Documentation*; Clean Code N1–N7, C1–C5, G16, G19, G20, G25 |
| 8 | **Test Adequacy** | The change is unverified, so the next change silently breaks it | Presence *and* quality. Every new behaviour has a covering test — and for each test ask the only question that matters: **would this fail if the code were wrong?** A test that asserts nothing, asserts a mock, or asserts the implementation is not coverage. Check the boundaries are tested, not just the happy path. Absorbs Google *Tests*; Clean Code T1–T9 |
| C1 | **Contracts & Compatibility** *(conditional)* | Consumers you never read break in production | Triggered when the diff touches a public surface. Classify every changed route, event shape, exported type, CLI flag, or env var as added / removed / changed-safe / changed-breaking; grep the actual consumers rather than recalling them; give every breaking change a named rollout path. See `agrim-contract-check` |
| C2 | **Data & State Migration** *(conditional)* | Data at rest is corrupted or unrecoverable | Triggered when the diff touches schema or data. Work the forward path, the backfill, the rollback, and the canary; confirm the app tolerates both old and new shapes for the whole rollout window; state the lock impact. See `agrim-migration-check` |

**Deliberately excluded — machines own these.** Style and formatting belong to `ruff` / `eslint` /
`tsc` in the quantitative pass, not to a reviewer's attention budget. Google's *Every Line*,
*Context*, and *Good Things* are reviewer **stance**, not axes, and live in the skill's preamble.

**One honest near-miss.** Axes 6 and 7 share a victim (the next developer). They are siblings, not
strictly disjoint. They stay split because you look for them differently: 6 is the shape of the code,
7 is the words in it, and well-structured-but-cryptic fails differently from clear-but-tangled.

---

## Two levels: prevent at implement, verify at review

An axis the author can self-check is cheaper to enforce while writing than to catch after. An axis
the author is structurally blind on is the reason review exists at all.

| Enforced at **implement** time | Verified at **review** time (author is blind) |
|---|---|
| 7 Intelligibility — naming and comments as you write | **1 Correctness & Intent** — needs a reader who re-opens the goal |
| 8 Test Adequacy — test-first, red before green | **3 Performance & Resources** — the author tested the happy path |
| 6 Structure — partially; the author is the worst judge of their own factoring | **4 Security** — an adversarial stance the author does not hold |
| Style — fully automated, zero reviewer attention | **5 Observability** — "can I debug this at 3am" is a reviewer's question |
| | **C1 Contracts** — requires greping consumers the author never wrote |
| | **6 Structure at system level** — cumulative complexity across goals |

---

## Precision

**Precision, alongside recall.** Every axis above improves what the reviewer *finds*; §3 of `SKILL.md`
governs what it *reports*. A finding must carry a written failing case before it may block, five
false-positive classes are never reported at all, and a real-but-not-blocking finding is filed as
`sdlc:followup` rather than spending one of the three review cycles that would otherwise park the
goal. The gate is the north-star's own Standing Rule 1 — a claim in prose is not evidence — turned on
the reviewer.

The one carve-out is deliberately narrow: a pre-existing problem is not this review's finding *unless
the change puts it on a live path*. An unqualified "ignore pre-existing issues" rule would cancel the
blast-radius discipline the skill opens with, which is the single thing a diff-only review most needs.

---
name: agrim-audit
description: Read-only whole-repo audit of conformance, erosion, debt, and fitness. Use for code-health or architecture audits and /agrim-audit.
allowed-tools: Bash, Read, Grep
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-audit

Detailed selection triggers: [selection](references/selection.md).

The **artifact** check. Every other gate in Sigma reviews a *unit of work*: `agrim-review` judges
one diff, `agrim-retro` asks what one goal taught, `agrim-plan-review` holds one plan to the north-star.
`agrim-align` is the only one with a wider lens — and it reads **goals and commits**, the trajectory of
what you chose to work on. It never opens the codebase.

**`agrim-align` is Lens B for strategy; this is Lens B for architecture.** A codebase becomes a
god-object the way a strategy gets rewritten: through a thousand individually-acceptable changes, each
of which passed review on its own merits. Axis 6 catches a smell inside one diff. Nothing catches the
smell that took forty diffs to form — that is this skill's job.

**Read-only by default.** It produces a report. It files issues only when explicitly asked, and it
never edits the north-star, the decisions registry, or any code.

## 1. Ground it on measurement, never on a re-read
Run the read-only collector first and reason from its pack:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/audit-collect.sh" --churn-days <N>
```

It emits `{schema, window, degraded[], totals, files[]}` — per source file: `lines`, `churn`, `score`
(size × churn), `has_test`, `markers`, `last_commit` — **ranked and capped**. It renders no verdict;
that is your job. Honour its `degraded[]` codes: a thin pack is a thin finding, not a manufactured one.

**Read the files the pack ranks, not the tree.** That bound is the whole point — an audit you can
afford monthly beats a perfect one you run yearly, because drift you catch late is drift you already
paid for. **State the read set in the report**: coverage you did not have must never be implied.

## 2. The four lenses

1. **Conformance** — *does the tree still obey its own stated rules?* Check the north-star's
   Architecture Rules, `.sdlc/decisions.json` invariants, and any `docs/CONTRACTS/` FROZEN files
   against what the code actually does. This is the lens no other gate can run: `agrim-decide` enforces
   invariants **per edit**, so it is structurally blind to code written *before* a rule existed and to
   violations that predate the registry. Only a tree-wide sweep sees those. Quote the rule; name the file.
2. **Erosion** — *what decayed by accumulation?* Axis 6 and 7's vocabulary at repo scale, and use its
   words: Long Method, Large Class, Long Parameter List, Primitive Obsession (cohesion) ·
   Feature Envy, Inappropriate Intimacy, Message Chains, Middle Man (coupling) · Shotgun Surgery,
   Divergent Change (change amplification) · Dead Code, Duplicate Code, Speculative Generality
   (dispensables).
   The pack's top `score` entries are where to look first — high churn on a large file is where
   erosion compounds fastest.
3. **Debt** — *what is measurably owed?* Straight from the pack, not from feeling: `has_test: false`
   on a high-`score` file, `markers` density (the `ponytail:` shortcuts and TODOs already logged), and
   files whose `last_commit` is old enough that nobody remembers them. Debt you can point at.
4. **Fitness** — *does the repo still serve its stated objective?* Compare what the code has become
   against the north-star's vision and **non-goals**. A subsystem nobody asked for is the artifact-side
   twin of `agrim-align`'s implicit rewrite.

## 3. Verdict + report
Open with the verdict, then findings ranked most-structural first. Each finding: what, where
(`file:line` from the files you read), why it matters, and the smallest change that would resolve it.

**At most 12 findings.** Past that you are logging tactical defects, not structural ones — rank by how
much future change the problem obstructs, not by how tidy the finding is. **If you drop findings, say
how many and why**; silent truncation reads as "that was everything".

Write to `.sdlc/knowledge/audit/<UTC-date>.md`, including `goals_reviewed: <N>` — that line is how
`/agrim-status` knows when the next audit is due.

## 4. Filing — only when asked
`/agrim-audit` reports and files nothing. `/agrim-audit --file` files, **at most 8** per run.

**Dedup first, and name the issues you checked against.** An auditor that re-files the same findings
every quarter gets switched off after its second run. List the open backlog items, compare each
candidate against them, and record in the report which existing issues you compared with — "I
deduped" is a claim in prose until it names what it read (north-star Standing Rule 1, applied to your
own work).

File through the one disciplined path, which works in both backlog modes — a GitHub issue, or a
`.sdlc/goals/` file on a local backlog:

```
handoff.create_tracked_issue(... same_area=True, immediately_actionable=False, blocks_goal=False,
                             extra_labels=("area:tech-debt", "component:<x>"))
```

`chore` as the type, `area:tech-debt`, plus the relevant `component:*` — the taxonomy that already
exists, so audit findings sort alongside every other issue instead of forming a parallel system.
`immediately_actionable=False` is deliberate: a machine-found item waits for a human to promote it.

## What this check is not
- **Not per-diff review.** A smell inside one recent change is `agrim-review`'s. If a finding here
  would have been caught there, note that it slipped through — useful signal about that gate — and
  don't count it as structural erosion.
- **Not strategy drift.** Whether the *work* matched the stated bets is `agrim-align`'s.
- **Not a security pass.** A security surface is `agrim-security-review`'s ten-point OWASP review.
- **Not an over-engineering hunt.** `ponytail-audit` does that specifically; this consumes the
  `ponytail:` markers it left rather than re-deriving them.

## Constraints
- **No fabrication.** Every finding names real files from the pack. If you cannot point at it, it is
  not a finding.
- **Don't manufacture problems.** A clean audit is a real result — say it and stop.
- **Propose, never edit.** Findings become work only when a human (or `--file`) says so.
- **Thin pack, thin finding.** Honour `degraded[]` rather than reasoning past it.

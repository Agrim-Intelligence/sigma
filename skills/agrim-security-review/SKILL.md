---
name: agrim-security-review
description: Threat-model changes to auth, user data, billing, external input, or public endpoints. Use for security-sensitive diffs or /agrim-security-review.
allowed-tools: Bash, Read, Grep
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# agrim-security-review

Detailed selection triggers: [selection](references/selection.md).

> Threat-model the change. Catch things before the public internet does.

*A conditional-risk review orthogonal to `agrim-review`'s code-quality pass; always Sigma's own
(no companion equivalent).*

**You review as an independent skeptic.** Read the change *and its call graph* — a security hole is
usually in what the diff enables two files away, not in the changed line. Ground yourself in the
project first: the north-star (non-negotiables / architecture rules) and the repo's `CLAUDE.md`, plus
the auth/authz middleware the change routes through.

**Locate the north-star, never assume its path.** `.sdlc/` is gitignored, so it is absent from the
goal worktree this runs in (#1778), and this skill is handed no reviewer brief. Run
`python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/north_star.py"` and branch: `present <path>` read it;
`absent` is a drop-in project with no declared non-negotiables — skip, silently; `unreachable` is
neither — **say in the findings that the project's non-negotiables were not checked**, because a
verdict with no `critical` that never read them is a false pass.

## Goal
For a change set or endpoint, produce a threat-modelled review: findings, severities, concrete
remediations.

## Steps
1. List the entry points the change exposes or modifies.
2. For each entry point: who can call it? authenticated? authorised? rate-limited?
3. Trace data flow: inputs → validation → persistence → outputs.
4. Run the ten-point checklist. One bullet per item; "n/a" with a reason if truly not applicable.
5. Assign a severity to each finding: `critical` / `high` / `med` / `low` / `info`.
6. Propose a concrete remediation for each non-info finding.

## Ten-point checklist
1. **authn** — is the caller identified, correctly? *(A07)*
2. **authz** — does the caller have the right to do this? *(A01)*
3. **input validation** — every external input typed and bounded? *(A03)*
4. **injection surfaces** — SQL, command, template, prompt, log? *(A03)*
5. **PII / secrets / crypto** — anything sensitive in responses, logs, errors? And where this change
   encrypts, hashes or signs: a current primitive, a real random source, no home-rolled scheme? *(A02)*
6. **rate / cost** — can a single caller exhaust budget? *(A04, in part)*
7. **dependencies & integrity** — new packages trusted and pinned? Any untrusted data
   **deserialized**, any unsigned artifact or plugin loaded, any CI/update path that could ship code
   nobody reviewed? *(A06, A08)*
8. **failure modes** — does the change fail open or fail closed under stress?
9. **configuration** — a default that is unsafe, a debug flag left on, a permissive CORS or bucket
   policy, a verbose error reaching the caller? Misconfiguration ships more breaches than exotic
   bugs. *(A05)*
10. **outbound requests** — does the change fetch a URL, host or path the caller can influence? That
    is **SSRF**: name what the target may be, and what stops it reaching internal addresses. *(A10)*

**OWASP Top 10:2021 coverage.** A01 · A02 · A03 · A05 · A06 · A07 · A08 · A10 map to the points above.

**A04 (Insecure Design) is only partly a checklist item.** Point 6 catches its commonest concrete form
— a missing rate or cost limit — but A04 is broader: a control never designed, a business rule
enforceable only client-side, a flow whose abuse case nobody modelled. That is what Steps 1-3 are for.
If the design itself is the vulnerability, say so as a finding; do not tick point 6 and move on.

**A09 (Security Logging & Monitoring Failures) is deliberately NOT here** — it belongs to
`agrim-review` **axis 5 (Observability)**, which asks whether a security-relevant event is recorded at
all. One home per concern; if you are reviewing logging coverage, you are in the wrong skill.

## Gates
- Every finding has a severity AND a concrete remediation.
- "n/a" lines explain why, not just blank.
- A critical or high finding also appears in the plan's risks section (feed it back to `agrim-plan`).

## Stop when
- A finding is `critical` → halt the change; in the loop, **park the goal for a human** and record why.
- The area touches an external compliance regime (SOC2, GDPR, PCI) → park and flag a human owner before
  going further.

## Output → render the report, and persist it if you want it retained
Write to `.sdlc/reviews/security-review-<slug>.md` (NOT under `.sdlc/knowledge/`, which is gitignored).

```markdown
# security review · <slug or route>

## summary
<X> critical · <Y> high · <Z> med · <N> low
ready to ship: <yes / no / not without remediation>

## entry points
- <method> <route> · auth: <kind> · authz: <rule>

## findings
[S1] severity:high — authz — <where> — <what> — fix: <how>
[S2] severity:med  — input — <where> — <what> — fix: <how>

## checklist trace
1. authn — <one line>
2. authz — <one line>
3. input validation — <one line>
4. injection surfaces — <one line>
5. PII / secrets / crypto — <one line>
6. rate / cost — <one line>
7. dependencies & integrity — <one line>
8. failure modes — <one line>
9. configuration — <one line>
10. outbound requests — <one line>
```

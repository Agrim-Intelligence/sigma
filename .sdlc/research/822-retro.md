# #822 retro (P7): PRD intake door

Method: read the brief, the issue, `.sdlc/acceptance/822.md`, the worktree diff, `.sdlc/research/822-controls.md`, SKILL.md and docs. Ran `tests/test_prd_intake.py tests/test_dossier.py` in the worktree: 110 passed (0.46s). I did not re-run the full suite and did not re-break any control; the control table is the author's record, taken as evidence of red-before-green, not re-measured.

## Overall: PARTIAL, close to achieved for the core door

The core (cite-or-refuse, open_ placeholders, repo-source refusal, provenance, umbrella, docs) shipped with controls. The issue-level scope (item 1 "a file, pasted text or an issue", item 4 fast lane, item 6 plan-review capture point) did not. The CHANGELOG says so honestly and keeps #822 open.

## Intent vs shipped

| AC | Grade | Evidence / gap |
|---|---|---|
| AC-1 mapping + one Dossier per outcome | achieved (file input only) | `plan` prints `PRD section -> Dossier title -> answer ids`; outcome grouping is the model's judgement, uncontrolled by code (max 12 cap, duplicate-name refusal only). Pasted text / issue input deferred. |
| AC-2 cite, never invent | partial | Quote must be a >=20-char substring of the PRD (controls C2, C2b, C2c red). This proves existence, not support: a real quote attached to an invented answer passes. CHANGELOG and docs state this; a human reading the table is the only backstop. |
| AC-3 open_ and Doubts | partial | open_ write exists with paired question (C3, R1c red). The "design pass SHALL carry it into Doubts" half is prose in goal-design SKILL.md (D-5); no test crosses the goal-design boundary. |
| AC-4 no code read, gesture refused | achieved for the adapter, partial for the claim | Gesture copied from SKILL.md is tested as written (C4 red); many hardening rounds (CR1-1a/1b, CR1-2, CR2-1, C4b). Cannot constrain the model, which `allowed-tools: Bash(python3 *)` could still let read code. Stated, not fixed. |
| AC-5 any format | achieved | `flat.txt` and `messy.md` fixtures; C5 (reject no-heading) red. Non-text suffix is refused (`prd-not-text-doc`): a PDF/docx PRD is rejected for format, in mild tension with "never rejected for its format", though defensible as a safety rule. |
| AC-6 source + sha + umbrella | achieved | `### Source` block (dossier.py `prd_source`, byte-identical when absent), umbrella `epic`, never `sdlc:goal`, phantom-blocker defang (R5, CU2 red). Umbrella lists children only if `.filed.json` is complete. |
| AC-7 design unchanged; designed only by goal-review | partial | Nothing in the diff writes `sdlc:designed`, and design is said to run unchanged. Not shown: that goal-design/goal-review parse a Dossier whose `### Source` follows the fence correctly. No test or run of the design pass on an intake Dossier. |
| AC-8 docs | achieved | 4f in dossier-pipeline.md and a section in how-the-dossier-pipeline-works.md, both stating the compile is not the route. Wording is thin on the mismatch with `prd_lint`/`emit-scope`, which the repo never contained (D-3). |
| AC-9 fixture + red on removal | achieved | Planted contradiction/vague fixtures; C3 red with a KeyError (a loud failure, not an assertion on the open_ text, so it proves the write is load-bearing). |
| AC-10 research doubts | achieved | Research ran `dossier.py file --answers` for real and recorded D-1..D-5. D-1 (source field) and D-2 (umbrella) were closed by the build. D-3 (prd_lint absent), D-4 (no unattended hand-off) and D-5 (prose-only Doubts) remain open. |

## Residual product debt

1. Fast lane (issue item 4) not built; `prd_lint.py` is out of tree, so a clean PRD still pays the model-draft path.
2. stdin / pasted text / issue-number PRDs not built (issue item 1 names all three).
3. Issue item 6 (plan-review capture point for plan-made architectural decisions, #665 AC-9) is not addressed by this diff and not flagged as deferred anywhere I read. Treat it as an unowned gap.
4. No end-to-end proof that the cited Dossier survives design -> goal-review (AC-7).

## Residual structural debt

- Duplicate-on-crash window: the ticket is created before `.filed.json` is written (umbrella too). Lever is `--status` plus a human closing the duplicate. Under RESILIENCY this is a human lever for a recoverable failure; an idempotency key in the ticket (hash + outcome) would let resume discover the existing ticket.
- open_ to Doubts is prose-enforced (D-5). Needs a deterministic check in design, or a test that feeds an intake Dossier to the Doubts extractor.
- Quote-exists is a weak support proxy; no check that the quote relates to the answer text.
- Hardening churn: 20+ controls, several for path-guard bypasses (case, symlink, install root, walk-up). The guard is a growing allow-list that protects only against accidents, and it is the largest surface for future regressions.
- PRD quotes are published verbatim into GitHub issue bodies. A PRD with confidential or secret text is exported; only the suffix check mitigates it. Not discussed under SAFETY.
- `dossier.py` gained an import of `blocker_scan` and a new write site (write-surface ratchet updated; legacy-compat and private-names tests edited, so the reason for those two edits deserves a reviewer check).
- Intake of a 12-outcome PRD files up to 13 tickets serially; no timing or rate measurement was made, and none is claimed.

## Proposed lessons (for owner approval, nothing applied)

1. A document-intake adapter must state which half of "SHALL NOT invent" code can prove (existence) versus which is human review. Require acceptance wording to name that split at P1.
2. When an AC says another phase "SHALL carry" something, either add a cross-skill test at the seam or record it as explicit prose-only debt at P1, not at retro.
3. Create-then-record side effects need an idempotency key stored in the created object, so resume is discoverable without a human lever.
4. Deferred scope from the issue body (items 1, 4, 6) must be listed in the CHANGELOG and in a follow-up issue before P7, so none is silently lost; item 6 currently is.

VERDICT: approve

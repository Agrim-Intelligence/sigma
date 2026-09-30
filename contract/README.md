# Core Record Format Contract

The core owns and publishes the format of every record it writes. This directory contains the versioned contract for all record types: vocabulary, validation rules, and golden examples.

## Overview

The Sigma core writes records to the `.sdlc/` directory in several formats and locations. This contract defines:

1. **What record kinds exist** — a closed vocabulary
2. **When they're written** — the conditions and enabled flags
3. **What fields they carry** — the schema per kind
4. **How to validate them** — a standard-library validator

The contract enables downstream readers to read, validate, and ingest core records with confidence that the schema will not break unexpectedly.

## Versioning

**Contract Version:** 1.3.0 (defined in `VERSION` file)

**Versioning Rules:**

- **MAJOR version bump:** Breaking schema change (e.g., removal or renaming of a required field, or splitting a kind into two incompatible shapes)
- **MINOR version bump:** Addition of a new record kind OR a new field added to an existing kind (backward-compatible; old readers still work)
- **PATCH version bump:** Documentation or validation improvements with no schema change

**Freeze Point 1 (this contract):** The nine record kinds listed below are frozen in v1.0.0. Breaking field changes require a major bump; additive kinds require a minor bump.

## Record Kinds

| Kind | Path | When Written | Written by | Schema |
|------|------|--------------|-----------|--------|
| **entries** | `.sdlc/ledger/entries/*.jsonl` | Always (ledger.enabled) | Core | `{id, actor, ts, kind, ...optional}` |
| **events** | `.sdlc/events/*.jsonl` | `journal.enabled`, or a managed-settings lock | Core | `{id, actor, ts, kind, goal, ...per-kind}` |
| **verify** | `.sdlc/state/verify/*.json` | Always | Core | `{goal_id, result, ...}` |
| **witness** | `.sdlc/state/witness/*.jsonl` | Always | Core | `{goal_id, witness_line, ...}` |
| **acceptance** | `.sdlc/acceptance/*.md` | P1 GOAL, explicitly captured | Core | JSON-valued frontmatter `{kind, goal, verify_command}` plus 3–7 checklist statements |
| **plans** | `.sdlc/plans/*.md` | Always (hash only, body excluded) | Core | `{id, hash, frontmatter, ...}` |
| **goal_frontmatter** | `.sdlc/goals/*.md` | Always (frontmatter only) | Core | `{id, title, phase, ...}` |
| **config** | `.sdlc/config.json` | Always | User/operator | `{goal_default_model, ledger, ...}` |
| **managed_settings** | `.sdlc/managed-settings.json` | S1-G5: written by a policy writer | A policy writer or device mgmt | `{version, status, refreshed_at, locked}` |
| **slice_manifests** | `.sdlc/plans/*.slices.json` | Does not exist yet | TBD | `{goal_id, slices, ...}` |

**The events path moved in 1.1.0 (#2574/S1-G3).** Events are written to `.sdlc/events/*.jsonl` and
are **never published** — nothing stages them to a branch and no teammate pulls them. Two notes on
reading them:

- **One release of legacy history.** `.sdlc/ledger/events/*.jsonl` is no longer written, but every
  reader still reads it, unioned with the new path. The two are disjoint directories, so the union
  never double-counts. Nothing already published there is moved or deleted.
- **The `When Written` column was wrong before 1.1.0.** It read `Always (ledger.enabled)` for this
  row from #244 until #2574/S1-G3. Events have never gated on `ledger.enabled`; they gate on
  `journal.enabled`, the journal's own key and the only one read (the older key it replaced is not
  read at all since #2706). An organisation's `.sdlc/managed-settings.json` may also lock
  `journal.enabled`, and that lock beats the local key either way.

### Entries Kind Vocabulary

The entries stream carries 11 record kinds:

1. `claimed` — a goal claimed by a worker
2. `done` — a goal completed
3. `parked` — a goal deliberately deferred
4. `failed` — a goal encountered a permanent failure
5. `handoff` — work being handed to another worker
6. `ack` — acknowledgment of a handoff
7. `release` — a claim automatically released (auto-reclaim)
8. `note` — a personal note
9. `merged` — a PR actually landed (written after canonical GitHub merge facts are observed; new
   rows carry the ownership/merge-SHA-derived `merged_entry_key`). Historical `merged` rows without
   that field remain valid under this minor contract version; a present key must be lowercase
   64-hex.
10. `merge-armed` — a PR auto-merge was armed (distinct from merged; see ledger.py line 49)

11. `acceptance` — captured P1 intent, with `ref` to the Markdown record and `acceptance_sha256`
    (64 lowercase hexadecimal characters). Ledger opt-in controls this metadata only; the local
    Markdown record is written regardless. Commands and criteria are never copied into the ledger.

### Acceptance records (1.3.0)

Run `python3 skills/agrim-loop/scripts/acceptance.py record .sdlc <goal>` before code.
`<goal>` is an issue number or a local goal Markdown path. The source must contain a
`## Done when` section with 3–7 single-line checklist statements. If absent, the P1 agent writes
a draft file with that section and adds `--draft <file>`; for a GitHub goal the capture posts
the draft back as a comment before publishing the local record. `--verify-command` optionally
records a project-trusted proving command; local frontmatter is inherited when omitted.
Never put credentials in a command or criterion. Existing records are returned unchanged.

Each statement is at most 1024 characters and the whole UTF-8 file at most 32 KiB, including
frontmatter. `kind` is `"acceptance"`, `goal` is the filename stem, and `verify_command` is null
or a nonempty string. Values in the flat frontmatter are JSON literals. See
`golden/acceptance/42.md`; validate with `python3 contract/validate.py contract/golden/acceptance/42.md`.
The writer uses atomic create-only hard links; a filesystem without that support refuses.
Retry the same capture after an outage or crash. Draft-comment retries first check the complete
comment for an identical marker and body; concurrent captures or an ambiguous acknowledgement
inside the source's bounded retry can still produce duplicate comments. The record remains unique.

PR publication requires a valid record when `verify.enforce` is on. `loop.py verify` runs the
repository command and recorded goal command through its existing evidence path; the second
runs only if the first passes. A local frontmatter command remains the fallback if the record
has no command. Both run in the goal worktree. Records are verbatim in PR/retro briefs; retro
grades each criterion with evidence. Changing or deleting the record invalidates green evidence. Multiple commands are recorded
as an ordered JSON array in the evidence `command` string; one command retains its literal string.
Keep the record with plan/research when committing a goal so future clones retain its intent.
If the main-checkout copy is lost, restore that file from the goal branch before verification
or publication; do not redraft from a subsequently edited issue. Review briefs can read the
committed local/remote goal branch directly when the main-checkout file is absent.

### Events Kind Vocabulary

The events stream carries 13 record kinds:

1. `phase` — phase transition event
2. `gate` — gate verdict event
3. `verify` — test/verification event
4. `slice` — work slice event
5. `spend` — token/cost event
6. `retro` — retrospective event
7. `park` — goal park event
8. `scan` — discovery scan event
9. `run_stop` — run termination event
10. `model_choice` — model tier choice event
11. `merge_observed` — confirmation that a PR the core itself opened has merged, written whenever the journal is on (receipt-backed ownership proof is not yet supported: #2680)
12. `review_posted` — an approve, block, or unblock verdict that Sigma posted
13. `ci_observed` — the bounded final CI rollup seen by the merge gate

## Golden Examples

Real examples of each record kind are stored in `contract/golden/`:

- **entries.jsonl** — one line per entries kind (10 lines, including real merge-armed data)
- **events.jsonl** — one line per events kind (13 lines)
- **verify.json** — one real verify file from `.sdlc/state/verify/`
- **witness.jsonl** — one real witness file from `.sdlc/state/witness/`
- **plans.json** — one real plan file's frontmatter and hash
- **goal_frontmatter.md** — one real goal frontmatter file
- **config.json** — core config example

**External inputs** (written by systems outside core):

- **managed_settings.json** — S1-G5: now written by a policy writer (outside the core) to deliver org policy. The core reads it at merge and PR gates (merge gate S1-G5, pr gate #2116). Atomic write pattern (temp file then rename).

**Deferred kinds** (documented but not yet written):

- **slice_manifests.json** — does not exist in this repo yet; will be added in a later contract version bump

## Vocabulary Source

**IMPORTANT:** `vocabulary.json` is **GENERATED** from `ledger.py` at build time. Never edit it by hand.

To regenerate after ledger.py changes:

```bash
python3 tools/generate_vocabulary.py > contract/vocabulary.json
```

If this checkout also carries a vendored copy of the contract, re-vendor it in the same step with
that checkout's vendoring tool, or the vendored `vocabulary.json` is left stale.

The generator writes LF bytes on every platform, and `contract/.gitattributes` pins `vocabulary.json`
to LF on checkout; a CRLF copy is drift.

The generator:
- Reads KINDS, EVENT_KINDS, EVENT_FIELDS, PHASE_KINDS, GATE_KINDS, VERDICTS, REASON_CLASSES, RETRO_GRADES from ledger.py
- Extracts the contract version from `contract/VERSION`
- Outputs deterministic JSON (sorted keys, consistent formatting)

**Sibling pins to update together (if ledger.py changes):**
- `contract/vocabulary.json` (generated)
- `tests/test_contract_generation.py::test_vocabulary_generated_from_ledger` (must pass)
- `tests/test_ledger.py::test_vocabulary_constants_match_spec_table` (must pass)

## Validation

To validate any record file against the contract:

```bash
python3 contract/validate.py <file>
```

**Examples:**
```bash
python3 contract/validate.py contract/golden/entries.jsonl
python3 contract/validate.py .sdlc/ledger/entries/*.jsonl
python3 contract/validate.py .sdlc/events/*.jsonl
```

The validator:
- Reads `vocabulary.json`
- Checks each record's `kind` is valid
- Checks the kind belongs to **that file's stream**, when the path names one — the two stream
  vocabularies below are disjoint, so a `merged` entry inside `events.jsonl` is refused even
  though it is a well-formed entry. The stream is taken from the path the examples above already
  pass: the parent directory (`.sdlc/ledger/entries/<actor>-<host>.<pid>.jsonl`) or the file stem
  (`golden/entries.jsonl`). A path naming neither is validated on `kind` alone, as before
- Verifies required fields are present (for entries: id, actor, ts, kind)
- Verifies no unknown fields per kind (for events)
- Exits 0 if all records valid, exits 1 with error details if any fail

## Design Decisions

### Opaque Goal and Issue References

Goal and issue fields are opaque strings (no numeric-only enforcement). This allows:
- Jira keys (PROJ-123)
- Other tracker IDs
- Future format changes without contract bumps

Validators at read time (downstream readers) own format enforcement if they need it.

### No Field-Level Type Validation

Field types (numeric, boolean, enum) are enforced by ledger.py's own validators (`append()`) and `loop.py emit`, not repeated here. The contract documents what fields exist; the core documents what values they carry.

### Managed Settings and Slice Manifests Deferred

These kinds are documented here (frozen in the contract) but not yet written by the core:

- **managed_settings.json** — Input to the core, written by S1-G5. Documented so Lanes B/C can design around it; content shape in Appendix.
- **slice_manifests.json** — Does not exist in v1.0.0. Recorded for future addition in v1.1 or later.

This is honest: the contract covers all kinds the core currently writes, plus documented placeholders for kinds coming soon.

## Related Documentation

- **ledger.py** (line 48-453) — the authoritative source of all constants
- **tests/test_contract_generation.py** — proof that vocabulary matches ledger.py
- A downstream reader may vendor a copy of this directory and pin it to this one; the copy is
  that reader's to keep in sync, not the core's.

---

**Last updated:** v1.0.0 (frozen)  
**Next review:** When core wants to ship new record kinds or breaking schema changes

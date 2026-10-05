---
name: sigma-radar
description: Research current developments against the backlog and write a ranked digest, with no external writes by default. Use for proactive scouting or /sigma-radar.
allowed-tools: Bash(python3 *), Bash(gh issue *), WebSearch, WebFetch
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-radar

Detailed selection triggers: [selection](references/selection.md).

A proactive research scout — the *supply* side that complements the gap log's *demand* side: it
surfaces what you didn't know to look for. **Phase A is dry-run: it writes a digest under `.sdlc/` and
records what it surfaced, but never files issues or touches GitHub.**

1. **Agenda (rotate over the backlog).** Get the open backlog — local `.sdlc/goals/*.md`, or
   `gh issue list --state open` in github mode — and count it (N). Pick this run's slice:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/radar.py" agenda <N> <k> <cursor>` (k≈3; `cursor` from the
   last run's `next_cursor`, starting 0). It returns the item indices to research + the next cursor,
   so successive runs cover different items.
2. **Research (fail-soft).** For each agenda item, research the current SOTA against it — your
   `WebSearch`/`WebFetch`, or the `deep-research` skill if available (a soft dep). Return cited
   findings `{topic, summary, url, issue}`. An item that errors is skipped — never abort the run.
3. **Dedup.** Form a short, stable key per finding (e.g. `issue-<n>:<topic-slug>`). Drop any already
   in the ledger (`radar.py seen .sdlc`) or already a known gap (`kg.py gap list .sdlc`) — don't repeat.
4. **Rank** the survivors by novelty × impact-on-the-item × actionability; tag 🚀 / 🔧 / 📌.
5. **Digest (dry-run write).** Write a ranked markdown digest to `.sdlc/knowledge/radar/<UTC-date>.md`
   (findings + a short "suggested actions" list), and record each surfaced finding so it isn't
   repeated: `radar.py record "<key>" .sdlc`. **Stop here — file nothing to GitHub.** (Opt-in,
   guard-railed filing is a later phase.)

Optionally seed the gap log with a finding worth chasing — `kg.py gap log "<...>" .sdlc` — so the
self-improving loop can fill it later: that's where the radar's *supply* meets the loop's *demand*.
Keep `.sdlc/knowledge/radar/` out of git (machine-accumulated, like `research/`).

## Internal debt scan (the *other* supply side)

The SOTA sweep above surfaces what's new outside; the debt already **inside** the repo is the other
thing you didn't schedule. Run the read-only collector and let it propose backlog items:

```bash
python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/pipeline.py" discover .sdlc .
```

It runs `discovery-scan.sh` (secret-safe — location + count only, never the marker text) over tracked
source and writes one **`proposed`** goal file per (category, file) for `TODO/FIXME/HACK/XXX` clusters
(tech-debt) and skipped/xfail tests (test-gap). Proposing is safe and idempotent: the loop **never runs
a `proposed` goal** until a human edits it to `status: pending`, and the per-(category, file) id means a
re-scan never spawns a duplicate. A quiet, non-git, or unreadable tree proposes nothing (fail-open).

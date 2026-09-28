# Stopping, and the spare-iteration loop

The detail behind step 7 of [`../SKILL.md`](../SKILL.md): closing the loop on a knowledge-graph
gap when the backlog is empty, and what to print and report at STOP.

---

7. Loop.

**Self-improving (optional, gated):** when the backlog is empty (`next` → `DONE`) but the knowledge
graph is enabled and `kg.py gap list .sdlc` shows open gaps **and** budget remains, you may close the
loop instead of stopping: take the oldest gap, research it, write the finding to
`.sdlc/knowledge/analysis/`, refresh the graph (`/agrim-kg`), then mark it filled —
`python3 "${CLAUDE_SKILL_DIR}/../agrim-kg/scripts/kg.py" gap resolve "<the gap>" .sdlc`. **One gap per
spare iteration, only within budget, and park (never force)** anything that needs a human. This is how
the graph fills what it didn't know — turn it off by leaving the KG disabled.

At STOP, print one machine-readable line FIRST — `LOOP STOP: backlog-empty`, `LOOP STOP: budget`, or
`LOOP STOP: handoff` (#2521 — see below) — then report: N done, M parked, K failed. (Unattended overnight? The user can wrap this loop
in `scripts/supervise_daemon.py` — it relaunches through usage-limit resets and crashes with zero
polling; you never invoke it yourself mid-session.) If anything parked or failed, point the user to the items —
`.sdlc/state/review-queue.md` in local mode, or the issues labelled `sdlc:parked` (the **Blocked**
column on the board) in github mode. **`decision_tier: "auto"`** (off by default — see the README's
feature-flag table) annotates each `needs_decision`/`irreversible`/free-text (`unknown`) park with an
`autonomous`/`escalate_l1`/`escalate_l0` tier (`decision_tier.py`, per #818's escalation pyramid),
shown as a `- tier:` line in the review queue and in the park comment — advisory only, a triage hint
for whoever reads the queue, never a routing decision the loop itself acts on.
Parking is always correct over forcing an irreversible action to "finish" a goal.

**On a `handoff` stop** (`config.json` → `handoff`, on by default at 20 goals — see
`skills/agrim-init/templates/config.json.tmpl`'s own `_handoff` comment for why absence still means
ON): this is a deliberate, healthy checkpoint, never a problem — the orchestrating session is being
retired ON PURPOSE, before its own accumulated context becomes the next 900k-token compaction. No
goal is abandoned: `loop.py`'s own `_next()` only checks this ceiling BETWEEN claims, immediately
before claiming the next one. It counts completed goals plus goals still running in this session.
Stop refilling slots, wait for every already-dispatched goal to finish, and record each outcome
before retiring the orchestrator. Nothing below the gate mutates a goal it refuses.

Print the literal command to run next, always — whether or not a supervisor is present.
Run it in a **new host session** (a new Codex task or Claude conversation); repeating it in
this same conversation would keep the orchestrator context that the hand-off is meant to clear:

```
/agrim-loop
```

(append the same `--feature <name>` this run used, if any — you already know it, it is whatever you
were invoked with). Print it even under `supervise_daemon.py`, which relaunches a fresh session
automatically on any non-`done` verdict (including this one, at the same short 60-second pause a
budget stop gets — never the longer, "not a crash" pause an unrecognised stop gets): the line costs
nothing when a human never reads it, and there is no reliable, host-agnostic way for the orchestrator
to know whether one is watching, so printing it unconditionally is simpler and safer than guessing.
A fresh `/agrim-loop` session starts at its normal ~80-100k-token baseline and picks up exactly where
the backlog left off — nothing about `.sdlc/goals/`, the ledger, or an in-flight goal's own worktree
depends on the orchestrating session surviving between goals; it is *bookkeeping only* that this
session's own context ever needs to hold in the first place.

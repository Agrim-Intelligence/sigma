# Architecture

How the hooks, phases and loop fit together. Back to the [README](../README.md).

## How it works

Sigma registers 10 hook commands (see `hooks/hooks.json`); each is fail-open. The prompt gate (`hooks/sigma_gate.sh`, wired as a `UserPromptSubmit` hook) is
**scoped per repo**: it only speaks in a project that has adopted the spine (an `.sdlc/` directory
exists — i.e. you ran `/sigma-init`); in any other repo it is a silent no-op, so installing the
plugin machine-wide never injects policy into unrelated projects. Set `SIGMA_GATE_GLOBAL=1`
to restore the old always-on behavior everywhere. Every other Sigma hook (the PreToolUse and Stop
gates) is likewise inert outside an adopted repository — no `.sdlc/config.json`, no effect. In an
adopted repo, on every prompt the prompt hook classifies intent with fast, deterministic regex —
**no LLM** — and injects the matching SDLC directive:

- **code change / implementation** → "do NOT jump to editing; run the full spine from the GOAL and
  pass PLAN-REVIEW before any edit."
- **read-only / conversational** → "answer directly (say so) — but the moment it becomes a code
  change, switch to the spine."
- **anything else** → the standard 7-phase policy.

The hook is *advisory and fail-safe*: a false positive over-reminds, a false negative falls back to
the standard policy, and it always emits valid JSON (even on garbage or empty stdin). It never calls
out, never blocks — it shapes what the agent does next.

## Architecture & flow

Threat boundaries, assets, and the mitigations currently present are recorded in
[the threat model](threat-model.md).

### The pieces

```mermaid
flowchart TB
    PROMPT(["Prompt / queued goal"]) --> HOOK["Repo-scoped intent hook"]
    HOOK --> ORCH["Orchestrator<br/>/sigma-goal (interactive) · /sigma-loop (autonomous)"]
    ORCH --> SPINE["7-phase SDLC spine<br/>two review steps: Plan-Review + Strategy-Alignment"]
    ORCH -.->|"model_selection: auto"| MDL["Model per goal<br/>haiku · sonnet · opus · fable"]
    SPINE --> OUT(["Verified change + audit trail"])
    SPINE <--> SRC["Backlog source<br/>local files · GitHub issues · Projects board"]
    SPINE <--> KG["Self-improving KG (optional)<br/>write · recall · track→prune→fill gaps"]
    SPINE -.->|"each phase via an executor"| COMP["sigma-* executor<br/>(optional extra: code-review)"]
```

### How a prompt falls through the phases

A prompt enters through the repo-scoped prompt hook, which routes by intent. Code work then falls through the
seven phases — with two **gates** that can send it back, and a **park** exit for anything that needs you:

```mermaid
flowchart TD
    P(["Your prompt"]) --> H{"Hook classifies intent"}
    H -->|"read-only / conversational"| ANS(["Answer directly"])
    H -->|"code change / non-trivial"| G["1. Goal"]
    G --> RS["2. Research"]
    RS --> PL["3. Plan"]
    PL --> PR{"4. Plan-Review<br/>+ strategy alignment"}
    PR -->|"FIX-FIRST"| PL
    PR -->|"SOUND"| IM["5. Implement (TDD)"]
    IM --> RV{"6. Review + verify"}
    RV -->|"unverified"| IM
    RV -->|"evidence passes"| RT["7. Retrospective"]
    RT --> DN(["Done"])
    PR -.->|"blocked"| PK(["Park"])
    IM -.->|"irreversible / stuck"| PK
    RV -.->|"blocked"| PK
```

### How the autonomous loop runs the backlog

The loop runs the backlog **park-and-continue** — it parks whatever needs you and keeps going:

```mermaid
flowchart TD
    ST(["/sigma-loop — reset run budget"]) --> NX{"Next pending goal?"}
    NX -->|"backlog empty"| SD(["Stop — all done"])
    NX -->|"budget reached"| SB(["Stop — budget"])
    NX -->|"goal"| WT["Cut its worktree + branch from the base<br/>(work.enabled — else edit in place)"]
    WT --> RUN["Run it through the 7-phase pipeline"]
    RUN -->|"done + verified"| GATE{"Merge gate:<br/>clean AND safe?"}
    GATE -->|"yes"| CMP["Land the PR, or record review"]
    GATE -->|"conflict · failing check · no evidence"| PRK
    RUN -->|"needs you / irreversible / unresolved"| PRK["Park to review queue"]
    CMP --> NX
    PRK --> NX
```

With `work` off, the two extra boxes collapse: the loop edits in place and never touches git, exactly
as it did before.

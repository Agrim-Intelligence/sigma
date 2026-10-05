---
name: sigma-ledger
description: Set up, read, note, or hand off through the team ledger. Use for team coordination or /sigma-ledger; ordinary goal logging is automatic.
allowed-tools: Bash(python3 *), Bash(bash *), Bash(gh *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-ledger

Detailed selection triggers: [selection](references/selection.md).

The team's shared coordination log — who claimed what, what's done, what's handed off to whom. It is
**not your code history**: it lives on its own git branch (`sdlc-ledger`), checked out as a worktree
under `.sdlc/ledger/`, so pulling it never touches your working tree. Each writing PROCESS writes
only its own `entries/<actor>-<host>.<pid>.jsonl` — your handle, a hash of the machine, the pid — so
concurrent writers can never conflict; the team view is their union, computed on read.

One file per writing process means **many files per person**: every run of the loop adds one and
nothing compacts them, so hundreds for a single actor is the healthy steady state (one adopter
machine was measured at 175 for one actor after two and a half weeks). Nothing is read back by
filename — each line is attributed by the `actor` field inside it — so the count is harmless. The
state worth asking about is the opposite one: exactly ONE file for a given actor means that actor's
writes stopped.

The scripts live in the loop skill. Resolve their directory once, then call them — the user never
types a python path:

```bash
LS="${CLAUDE_SKILL_DIR}/../sigma-loop/scripts"
```

## Turn it on + set it up (once per repo, then once per teammate's clone)

1. Ensure `.sdlc/config.json` has `"ledger": { "enabled": true }` (off by default — a team surface is
   opted into explicitly). Add the `lease` block too if you want to tune the claim-lease TTL.
2. **One command creates everything** — the ops branch, your entries file, the `.sdlc/ledger/TEAM.md` rollup, and
   pushes it so the team can see it:
   ```bash
   python3 "$LS/sync.py" bootstrap .sdlc
   ```
   Idempotent (safe to re-run). Each teammate runs this once in their own clone to join. The worktree
   must also be git-ignored so it never lands in a code PR — use the safe helper, which never clobbers
   or narrows an ignore rule you already set (and can target `.git/info/exclude` instead of the shared
   `.gitignore` for a local-only adoption): `python3 "${CLAUDE_SKILL_DIR}/../sigma-setup/scripts/setup.py"
   ignore . --scope tracked` (or `--scope local`). Do NOT blindly `echo … >> .gitignore` — that has
   overwritten a repo's existing broader exclude.

`/sigma-doctor` flags this automatically: if the ledger is enabled but not set up, it runs the same
bootstrap for you.

## Read it

- **What's addressed to me** (hand-offs waiting on you): `python3 "$LS/ledger.py" mine .sdlc`
- **One-line team summary** (counts + outstanding hand-offs): `python3 "$LS/ledger.py" summary .sdlc`
- **Regenerate the human rollup** `.sdlc/ledger/TEAM.md`: `python3 "$LS/ledger.py" render .sdlc --write`

## Keep it fresh

`python3 "$LS/sync.py" pull .sdlc` fetches the latest. In a normal loop you don't need to — the loop
auto-starts the watcher (`watch_daemon.py`), which pulls + publishes on an interval and drops anything
addressed to you into `.sdlc/state/inbox.md`, surfaced between goals.

## Write — only for things OUTSIDE the automatic flow

Claiming a goal and recording `done`/`parked`/`failed` happen **by themselves** inside `/sigma-loop`
when the ledger is on — never log those by hand. Use the ledger directly only for:

- **Leave a note**: `python3 "$LS/ledger.py" append .sdlc note <goal> --why "spike looks viable"`
- **Hand a blocker to a teammate** (opens an issue in their area, assigned to them, so their own loop
  picks it up): `python3 "$LS/handoff.py" open .sdlc <goal> --area <area> --why "needs your call on X" --priority P1`
- **Answer a hand-off that landed on you**:
  `python3 "$LS/handoff.py" ack .sdlc --issue <n> --state accepted|deferred|declined|resolved`
  (no issue — a local/issue-less hand-off — use `--goal <goal> --area <area>` in place of `--issue
  <n>`; `--area` narrows to one hand-off when that goal carries more than one outstanding.
  `deferred` does NOT settle it — reply `resolved`/`declined` to close it out.)

**All three publish themselves, and tell you on stderr whether they did.** The point of every one of
these is that somebody ELSE reads it, and the watcher — the only other thing that publishes — starts
only from a loop trigger, so on a machine that is not looping these commands were the only chance
the entry had. Read that stderr line and report it: `published` means the team can see it; `not
published yet` means a live watcher is carrying it on its next tick; `NOT published` or `LOCALLY
ONLY` means it is still on this machine alone, and the line names the command that fixes it. The
write itself never fails because of this — stdout is still the entry id and the exit code is
unchanged. `ledger.publish_on_write: false` in `.sdlc/config.json` turns the push off (and the line
with it) for an adopter who wants writes local and pushes batched.

## The one safety feature to know: claim leases

A `claimed` entry with no later `done`/`parked`/`failed` is an **open lease** — the loop won't let a
second person start a goal someone else is already holding. It's advisory (it only sees claims already
synced to the ops branch, not a hard lock), and a crashed claim self-expires after
`ledger.lease.ttl_hours` (default 12h) so it can never block the team forever.

---

**How to respond:** map the user's intent to the right command, run it, and report the result in
plain language. Prefer this skill's commands over ad-hoc `python3 <path>` so what you show is
copy-pasteable and consistent for the whole team. If the ledger isn't set up yet (no `.sdlc/ledger/`
worktree) and they ask to read or write it, run `bootstrap` first.

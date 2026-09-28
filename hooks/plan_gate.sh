#!/usr/bin/env bash
# Hard plan-gate — OPT-IN mechanical enforcement of "plan before you edit".
#
# AN ACCELERATOR, NOT THE ENFORCEMENT (#2116). This hook is Claude Code only, so
# nothing may depend on it: AGENTS.md is explicit that a Claude Code hook may be
# an accelerator, never load-bearing, and `gates.hard_plan_gate` is org-lockable,
# which makes it load-bearing by definition. The host-agnostic enforcement point
# is `skills/agrim-loop/scripts/work.py`'s `pr()` — plain Python, identical on
# Claude Code, Cursor and Codex, at most once per goal. Delete this file and the
# gate still holds on every host; what you lose is EARLINESS, which is the whole
# value this hook adds:
#   * this hook refuses the EDIT; `pr()` refuses the PUSH.
#   * this hook asks "is ANY plan fresher than plan_freshness_hours"; `pr()` asks
#     the stronger, window-free question "does THIS goal have a plan", because an
#     mtime test at publish time would refuse a goal that legitimately ran longer
#     than the window having planned properly.
# Everything else is kept deliberately identical between the two — the source
# extension list, the .sdlc/ and docs/ exemptions, and the .allow-direct-edits
# sentinel (including WHEN it applies, see below) — because one key must not
# give opposite answers at its two enforcement points on the same host.
#
# Wired as a PreToolUse hook on Edit|Write|MultiEdit|NotebookEdit. The prompt
# gate (agrim_gate.sh) reminds; THIS one refuses: with the flag on, a SOURCE
# edit is denied unless a fresh plan exists under .sdlc/plans/ (the plan the
# Plan phase wrote). Off by default — absent config = the hook allows
# everything, so installing it changes nothing until a repo turns it on:
#   .sdlc/config.json → {"gates": {"hard_plan_gate": {"enabled": true,
#                                  "plan_freshness_hours": 24}}}
# Escape hatch for deliberate direct edits: `touch .sdlc/.allow-direct-edits`
# (delete it to re-arm) — honoured ONLY while the gate is on by LOCAL config
# and NOT org-locked ON (#2138). The org lock is `.sdlc/managed-settings.json`
# (v1, `status: ok`, `gates.hard_plan_gate` under `locked` with an ON value):
# under it a file any member can `touch` does not waive the org's policy, and
# the deny says so instead of offering `touch`. This hook reads only that FILE;
# `pr()`'s `managed_settings.gated_check` also consults the local
# `managed_settings.project_id` — equivalent here, because a missing file reads
# as not-adopted either way. Freshness is an mtime heuristic — any recent plan
# unblocks source edits; the gate is a seatbelt, not a proof of relevance.
# Fail-open everywhere: no python3 / unreadable input / missing config → allow
# (emit nothing, exit 0) — with ONE named exception: an org file that is PRESENT
# but does not verify (revoked, unreadable, invalid, wrong version) means the
# SENTINEL is not honoured, matching `pr()`, which PARKs on the same file. That
# exception only ever removes the escape hatch; a fresh plan or a non-source
# path still allows. Known limits, all covered by `pr()` on every host: the
# on/off MODE and the freshness hours are read from LOCAL config, so this hook
# is inert when local config is off or absent (it exits before reading the org
# file) even if the org locks the key ON. Deny is the ONLY output this script
# ever prints.
set -uo pipefail

PROJECT="${CLAUDE_PROJECT_DIR:-$PWD}"
CFG="$PROJECT/.sdlc/config.json"

# Opt-in check first: absent/off config → allow (silent, zero-cost exit).
[ -f "$CFG" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
enabled="$(python3 -c '
import json, sys
mode, hours = "off", 24

def enabled(v):                # F17/#342-style generous truthiness (#416): a real bool passes
    if isinstance(v, bool):    # through unchanged; a string counts as off only when it spells
        return v               # out false/no/off/empty (`bool("false")` being True in plain
    if isinstance(v, str):     # Python is exactly the silent-unsafe trap this avoids); anything
        return v.strip().lower() not in ("", "false", "0", "no", "off")
    return bool(v)              # else (1, "true", "yes", ...) is on -- a strict `is True` read
                                 # silently left this DENY gate OFF on a plain JSON-typo config.
try:
    cfg = json.load(open(sys.argv[1]))
    gates = cfg.get("gates")                      # #2116: total at BOTH levels. (x or {}).get(...)
    gates = gates if isinstance(gates, dict) else {}   # raises on a scalar gates, and the block path
    gate = gates.get("hard_plan_gate")            # itself is what ledger.LOCKABLE_KEYS names -- so
    if not isinstance(gate, dict):                # {"gates": {"hard_plan_gate": true}} is a legal Org
        gate = {"enabled": gate}                  # policy that the pr() gate in work.py reads as ON.
    mode = "on" if enabled(gate.get("enabled")) else "off"   # Both shapes used to land in except->off.
    try:              # a bad freshness value must NOT corrupt the mode line — parse it independently
        hours = int(gate.get("plan_freshness_hours") or 24)
        # Clamp to a year BEFORE the value reaches the shell: `$(( hours * 60 ))` below is 64-bit
        # and wraps above (2^63-1)/60. Just past that the product goes NEGATIVE and `find -mmin`
        # matches nothing, so the gate denies every edit while quoting the huge window back; far
        # past it the product wraps POSITIVE and the gate allows everything. The digits-only guard
        # in the shell bounds the value SHAPE, never its MAGNITUDE. This clamp cannot live in the
        # shell: `[ "$h" -gt 8760 ]` errors on a 20-digit value and falls through to "not greater",
        # skipping exactly the inputs it would exist to catch. Python integers do not overflow, so
        # here is the only place the comparison is trustworthy. Clamping DOWN (rather than falling
        # back to 24) keeps a deliberately enormous window meaning "effectively never expires" —
        # turning it into 24h would make a working config silently 4000x stricter (#602).
        if hours > 8760:
            hours = 8760
    except Exception:
        hours = 24
except Exception:
    mode, hours = "off", 24
# #2138: the org lock, from the managed-settings FILE (argv[2]), in its OWN try so that nothing about
# that file can ever corrupt the mode line above. Line 3 is the class -- `unlocked` (no file, or an
# ok file that does not lock this key to an ON value), `locked` (ok, v1, key locked to an ON value)
# or `unverifiable` (present but not an ok v1 policy) -- and line 4 a fixed-vocabulary detail for the
# deny text. The expressions mirror managed_settings.read()/gated_check() one for one (`version != 1`,
# the status set, `isinstance(locked, dict)`, the same generous ON truthiness) so edge values agree;
# the org_lock differential in tests/test_plan_gate.py runs both parsers over the same file states.
org, detail = "unlocked", ""
try:
    import os
    if os.path.exists(sys.argv[2]):
        try:
            data = json.load(open(sys.argv[2]))
        except Exception:
            data = None
        if not isinstance(data, dict):
            org, detail = "unverifiable", "unreadable or invalid"
        elif data.get("version") != 1:
            org, detail = "unverifiable", "version: unsupported"
        elif data.get("status") != "ok":
            st = data.get("status")
            org = "unverifiable"
            detail = "status: " + (st if st in ("access-revoked", "locked-key-unverifiable")
                                   else "unrecognised")
        else:
            locked = data.get("locked")
            locked = locked if isinstance(locked, dict) else {}
            if "gates.hard_plan_gate" in locked:
                v = locked["gates.hard_plan_gate"]
                block = v if isinstance(v, dict) else {"enabled": v}
                if enabled(block.get("enabled")):
                    org = "locked"
except Exception:
    org, detail = "unverifiable", "unreadable or invalid"
print(mode); print(hours); print(org); print(detail)
' "$CFG" "$PROJECT/.sdlc/managed-settings.json" 2>/dev/null || printf 'off\n24\n')"
mode="$(printf '%s' "$enabled" | sed -n 1p)"
fresh_hours="$(printf '%s' "$enabled" | sed -n 2p)"
org_lock="$(printf '%s' "$enabled" | sed -n 3p)"
org_detail="$(printf '%s' "$enabled" | sed -n 4p)"
case "$fresh_hours" in ''|*[!0-9]*) fresh_hours=24 ;; esac   # defensive: never let a non-numeric reach $(( ))
case "$org_lock" in locked|unverifiable) ;; *) org_lock=unlocked ;; esac   # only the fallback lacks line 3
[ "$mode" = "on" ] || exit 0

# #2737: the ONE definition of "adopted" (gate_state.py:adopted_root). Knowingly redundant with the
# cheap `[ -f "$CFG" ]` above — kept for single-definition traceability, and placed here so it costs
# a python spawn only on the gate-ON path. Adopted iff stdout is the literal `adopted`; `_py.sh`'s
# broken-interpreter path prints nothing, which reads as not adopted = allow (fail-open).
DIR="$(cd "$(dirname "$0")" && pwd)"
[ "$(bash "$DIR/_py.sh" "$DIR/gate_state.py" --adopted "$PROJECT" 2>/dev/null)" = adopted ] || exit 0

# Deliberate-override sentinel -- for a LOCAL gate only (#2138). Under an org lock ON, or an org
# file that cannot be verified, the sentinel is not consulted; the deny below says which and why.
[ "$org_lock" = unlocked ] && [ -f "$PROJECT/.sdlc/.allow-direct-edits" ] && exit 0

# The file being edited (fail-open on unreadable input). All three spellings are read, matching
# decision_gate.py's own extraction points byte for byte: this hook is wired on NotebookEdit too
# (hooks.json), and a NotebookEdit payload carries `notebook_path`, not `file_path`. Reading only
# `file_path` left the value empty and the `-n` guard below then fail-opened before any gating logic
# ran — so that matcher entry was dead wiring, and a repo with the gate ON was never gated on a
# notebook edit, with nothing to say so (#553).
input="$(cat 2>/dev/null || true)"
file_path="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    ti = json.load(sys.stdin).get("tool_input") or {}
    print(ti.get("file_path") or ti.get("filePath") or ti.get("notebook_path") or "")
except Exception:
    print("")
' 2>/dev/null || true)"
[ -n "$file_path" ] || exit 0

# Docs, config, and the .sdlc layer itself are never gated — only SOURCE is.
case "$file_path" in
  *"/.sdlc/"*|".sdlc/"*|*"/docs/"*|"docs/"*) exit 0 ;;
  *.md|*.markdown|*.json|*.yaml|*.yml|*.toml|*.txt|*.csv|*.lock) exit 0 ;;
esac
# Kept in lockstep with completion_gate.sh's identical list (each hook inlines its own copy so it
# stays path-independent); tests/test_plan_gate.py asserts the two sets are equal. `*.ipynb` joined
# it with #553: reading a NotebookEdit's path is inert while the extension it always carries is
# unrecognized, and an unrecognized extension exits 0 — the gate would have kept fail-opening on
# exactly the file type the NotebookEdit matcher exists for. A notebook is code an engineer edits.
case "$file_path" in
  *.py|*.ts|*.tsx|*.js|*.jsx|*.sh|*.go|*.rs|*.java|*.rb|*.c|*.cc|*.cpp|*.h|*.hpp|*.swift|*.kt|*.php|*.scala|*.ex|*.exs|*.ipynb) ;;
  *) exit 0 ;;   # not a recognized source extension → allow
esac

# A fresh plan anywhere under .sdlc/plans/ unblocks source edits.
if [ -d "$PROJECT/.sdlc/plans" ] && \
   find "$PROJECT/.sdlc/plans" -name '*.md' -mmin "-$((fresh_hours * 60))" 2>/dev/null | grep -q .; then
  exit 0
fi

# Three deny texts, one per org-lock class (#2138). The unlocked one is the pre-#2138 text, byte for
# byte. Neither of the other two offers `touch`: under a lock it would be a false remedy, and on an
# unverifiable file the sentinel is exactly what is not being honoured.
python3 -c '
import json, sys
hours, org, detail = sys.argv[1], sys.argv[2], sys.argv[3]
if org == "locked":
    reason = ("hard_plan_gate: source edit with no fresh plan. The gate is org-locked "
              "(.sdlc/managed-settings.json), so the .allow-direct-edits sentinel does not apply. "
              "Write the plan first (Plan -> Plan-Review, save under .sdlc/plans/; freshness "
              "window: " + hours + "h).")
elif org == "unverifiable":
    reason = ("hard_plan_gate: source edit with no fresh plan. .sdlc/managed-settings.json is "
              "present but does not verify as an ok org policy (" + detail + "), so the "
              ".allow-direct-edits sentinel is not honoured. Write the plan first (Plan -> "
              "Plan-Review, save under .sdlc/plans/; freshness window: " + hours + "h), or ask "
              "the operator about the managed-settings file.")
else:
    reason = ("hard_plan_gate: source edit with no fresh plan. Write the plan first "
              "(Plan -> Plan-Review, save under .sdlc/plans/), or for a deliberate "
              "direct edit: touch .sdlc/.allow-direct-edits (freshness window: "
              + hours + "h).")
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": reason}}))
' "$fresh_hours" "$org_lock" "$org_detail"
exit 0

#!/usr/bin/env bash
# SessionStart hook — the wizard check below is UNCONDITIONAL for an interactive session (issue
# #1560), with one exception: a headless/supervised run (SIGMA_RUN_ID set) skips it, since its
# flow is a real yes/no conversation and there is nobody there to hold one. The policy-brief block
# further down stays its own separate, pre-existing opt-in feature. Two concerns, kept apart on purpose:
# gating the wizard behind session_start.enabled would silently inherit an unrelated feature's
# off-by-default and never fire for anyone.
# Policy-brief half: OPT-IN. Injects a short SDLC policy brief (+ a doctor-lite install self-check) as
# additionalContext at the start of a session, so Sigma's conventions are in context before the first
# prompt (the UserPromptSubmit gate only fires once the user actually types). Off by default — silent
# unless the repo has a .sdlc/ AND opted in:
#   .sdlc/config.json → {"session_start": {"enabled": true}}
# Advisory only: it never blocks; the self-check warns, never fails. Fail-open everywhere: no python3 /
# no config / not enabled / no .sdlc → emit nothing, exit 0 (a session with no Sigma is untouched).
set -uo pipefail

allow() { exit 0; }
PROJECT="${CLAUDE_PROJECT_DIR:-$PWD}"
CFG="$PROJECT/.sdlc/config.json"
# The wizard below has to import ITS OWN plugin code (setup_wizard.py), which lives beside THIS hook
# file -- not inside $PROJECT, which is whatever repo the user is actually working in (almost never the
# plugin's own source tree). Resolved from $0, the same __file__-relative convention decision_gate.py
# already uses for its own sibling-script imports (Path(__file__).resolve().parent...), kept
# independent of $PROJECT on purpose so the wizard actually fires for a real adopting repo, not only
# when $PROJECT happens to be this plugin's own source tree.
HOOK_DIR="$(dirname "$0")"
PLUGIN_ROOT="$(dirname "$HOOK_DIR")"

command -v python3 >/dev/null 2>&1 || allow

# --- Guided setup wizard (issue #1560) ---------------------------------------------------------
# Only ONE additionalContext can be emitted per hook invocation, so this block's own exit status
# gates whether the policy-brief block below ever runs at all: exit 0 means "already printed,
# stop here" (caught by the `if`, which `exit 0`s the whole hook immediately, before the
# policy-brief block gets a chance to ALSO print and produce two concatenated JSON blobs on
# stdout); exit 1 -- nothing needed, or the check itself failed -- falls through so the
# policy-brief block runs exactly as it always has. `sys.exit(0)` alone (with no surrounding
# `if`) would only end the python3 subprocess, not this shell script, which is why the `if` here
# is load-bearing and not just style.
#
# HEADLESS SESSIONS SKIP THE WIZARD ENTIRELY. The wizard's whole flow is "ask a real yes/no
# question and act only on yes" -- there is nobody to answer it in a `claude -p /agrim-loop`
# worker, so injecting it there just spends context telling an autonomous run to hold a
# conversation. `SIGMA_RUN_ID` is the signal: skills/agrim-loop/scripts/supervise_daemon.py hands
# it to that subprocess's own `env=` before launching it (its run-id resolution step), and every
# child inherits it. Set -> fall
# through to today's behaviour (silent, or the policy brief if separately enabled).
#
# HONEST LIMIT, DELIBERATELY NOT CLOSED HERE: this reads "a supervised loop is in this process
# tree", which is NOT the same as "no human is watching" -- an interactive session a person
# launched from a shell that happens to carry the variable is skipped too. A proper signal
# (something the launcher sets to mean non-interactive specifically) is real follow-up work, not
# built in this plan. The direction of the error is the safe one: a skipped wizard leaves the user
# in exactly today's status quo, and `/agrim-doctor` still reports everything it would have said.
if [ -z "${SIGMA_RUN_ID:-}" ] && python3 - "$PROJECT" "$PLUGIN_ROOT" <<'PY' 2>/dev/null
import json, os, sys
project, plugin_root = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(plugin_root, "skills", "agrim-init", "scripts"))
try:
    import setup_wizard
    status = setup_wizard.wizard_status(os.path.join(project, ".sdlc"))
except Exception:
    sys.exit(1)                                    # never let the wizard check break the session
if not status["needs_wizard"]:
    sys.exit(1)

lines = ["Sigma setup is incomplete. Run the agrim-wizard skill now, conversationally, before "
         "doing anything else the user asked for. For each item below: explain it in plain "
         "language (including the 'what breaks if skipped' text given), ask a real yes/no "
         "question, act only on yes, RECHECK by calling setup_wizard.wizard_status() again "
         "(never assume an action worked), and on 'no' persist the skip via "
         "setup_wizard.write_dismissed() -- a dismissed check is never asked about again until "
         "someone edits or removes .sdlc/state/setup-wizard-dismissed.json."]
for step in status["steps"]:
    lines.append(f"- [{step['mode']}] {step['name']}: {step['fix']} "
                 f"-- if skipped: {step['degraded']}")
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "\n".join(lines),
}}))
sys.exit(0)
PY
then
    exit 0
fi
# --- Proactive ledger-watcher staleness check (issue #2444) --------------------------------------
# An ACCELERATOR on top of the existing, unchanged multi-trigger watcher-restart mechanism
# (_ensure_watcher in skills/agrim-loop/scripts/loop.py, untouched by this change) -- it narrows the
# DETECTION gap (nobody has to remember to run /agrim-doctor), it never replaces the restart
# mechanism itself (AGENTS.md: "revival is layered and host-agnostic -- no single trigger is
# trusted"). Duplicates (never imports) _ledger_watcher_state's exact math from
# skills/agrim-doctor/scripts/doctor.py::_ledger_watcher_state -- name the function, never the line
# (both line references in this block had already rotted) -- only the ledger watcher is checked
# here, the other five per-watcher doctor.py state functions are out of scope for this tier. Same
# one-additionalContext-per-invocation idiom as the wizard block above: `if ... then exit 0; fi` so
# a silent tier falls through to the next one below, and a firing tier exits the whole script
# before any other tier can also print.
if [ -z "${SIGMA_RUN_ID:-}" ] && python3 - "$PROJECT" <<'PY' 2>/dev/null
import json, sys, time, pathlib
project = sys.argv[1]
try:
    cfg = json.loads((pathlib.Path(project) / ".sdlc" / "config.json").read_text())
except Exception:
    cfg = {}
if not isinstance(cfg, dict):
    cfg = {}
ledger_cfg = cfg.get("ledger")
ledger_cfg = ledger_cfg if isinstance(ledger_cfg, dict) else {}
if ledger_cfg.get("enabled") is not True:
    sys.exit(1)                                    # off, missing, or malformed config -> silent

watch_cfg = ledger_cfg.get("watch")
watch_cfg = watch_cfg if isinstance(watch_cfg, dict) else {}
interval = watch_cfg.get("interval_seconds")
if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
    interval = 900
stale_after = max(3 * interval, 180)   # mirrors doctor.py::_ledger_watcher_state / watch_daemon.py's STALE_AFTER

state_dir = pathlib.Path(project) / ".sdlc" / "state"
reason = None
try:
    hb_mtime = (state_dir / "watch.heartbeat").stat().st_mtime
except OSError:
    if (state_dir / "watch.pid").exists():
        reason = "looks dead"
    else:
        reason = "has never started"
else:
    age = time.time() - hb_mtime
    if age >= stale_after:
        reason = "looks stale"

if reason is None:
    sys.exit(1)                                    # fresh -> silent

print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": (
        "Sigma's team ledger watcher %s. The shared ledger may not be staying pushed "
        "-- run /agrim-doctor for detail." % reason
    ),
}}))
sys.exit(0)
PY
then
    exit 0
fi
# --- Knowledge graph never built / stale under auto_refresh (issue #2704) ------------------------
# Same tier idiom as the two above, and the same ACCELERATOR status: the load-bearing surface is
# `kg.py warn` itself, which `loop.py next`/`next-batch` print on every host (Cursor has no hooks).
# Unlike the watcher tier this one does not duplicate the math: it runs the sibling skill's own CLI
# (skills/agrim-kg/scripts/kg.py, resolved from PLUGIN_ROOT like setup_wizard.py above), which owns
# the once-a-day stamp too, so the hook and the loop share ONE daily budget per repo. `warn` prints
# "" and exits 0 when nothing is due (graph off, auto_refresh off, graph fresh, warned today), so
# the python wrapper below exits 1 to fall through in exactly that case. Headless skip as above.
if [ -z "${SIGMA_RUN_ID:-}" ] && python3 - "$PROJECT" "$PLUGIN_ROOT" <<'PY' 2>/dev/null
import json, os, subprocess, sys
project, plugin_root = sys.argv[1], sys.argv[2]
kg_py = os.path.join(plugin_root, "skills", "agrim-kg", "scripts", "kg.py")
try:
    r = subprocess.run([sys.executable, kg_py, "warn", os.path.join(project, ".sdlc")],
                       capture_output=True, text=True, timeout=30)
    text = (r.stdout or "").strip()
except Exception:
    sys.exit(1)                                    # a broken helper must never break the session
if r.returncode != 0 or not text:
    sys.exit(1)                                    # nothing due -> silent, fall through
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "Sigma " + text,
}}))
sys.exit(0)
PY
then
    exit 0
fi
# --- existing opt-in policy brief (unchanged below) --------------------------------------------
[ -f "$CFG" ] || allow

python3 - "$PROJECT" <<'PY' 2>/dev/null || allow
import json, os, sys
project = sys.argv[1]
try:
    cfg = json.load(open(os.path.join(project, ".sdlc", "config.json")))
except Exception:
    sys.exit(0)                                    # unreadable config -> silent
if (cfg.get("session_start") or {}).get("enabled") is not True:
    sys.exit(0)                                    # off by default -> silent

# doctor-lite install self-check: warn (never block) on a half-set-up adoption.
warnings = []
if not os.path.exists(os.path.join(project, ".sdlc", "context", "north-star.md")):
    warnings.append("no north-star yet - run /agrim-vision to set the direction the plan-review gate checks.")

policy = (
    "Sigma SDLC is active in this repo. Work rides the loop: "
    "Goal -> Research -> Plan -> Plan-Review -> Implement -> Review -> Retrospective. "
    "Run /agrim-loop to drain the backlog autonomously, or /agrim-goal for a single goal. "
    "Ground every change in .sdlc/context/north-star.md and the repo's CLAUDE.md. "
    "Plan before editing source; every changed behavior carries a test; the reviewer is never the author."
)
if warnings:
    policy = "Sigma setup notes: " + " ".join(warnings) + "\n\n" + policy

print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": policy,
}}))
PY
exit 0

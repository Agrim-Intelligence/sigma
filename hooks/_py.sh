#!/usr/bin/env bash
# Fail-open python runner for Sigma's python hooks.
#
# The hooks shell out to a bare `python3` — whatever the user's PATH resolves. On a multi-python machine
# that can be a BROKEN interpreter (a pyenv shim to an uninstalled version, a half-broken conda base),
# not just an absent one. Because these hooks run on every edit / web fetch, a broken python3 would fail
# the tool call on FIRST use — the "python env conflict when I first logged in" a real adopter hit, fixed
# for them only by removing a python version.
#
# So: preflight the interpreter, and if it can't actually run, exit 0 (allow / no-op) instead of erroring.
# When it works, exec straight through — stdin, stdout, and the exit code all pass to the hook untouched,
# so a hook that denies via its output/exit code still does. `python3 -c ''` is the cheap does-it-run check.
#
# Fail-open is NOT silent (#1282): a python-backed gate that did not run is a guarantee that is off, and a
# silent half-guarantee is worse than a clear "not supported here". So when the interpreter cannot run, and
# only inside an adopted repo (`.sdlc/config.json` at or above the project dir), the runner still allows
# (exit 0 -- blocking every tool call on a broken python3 could make the whole host unusable, and
# docs/enforcement.md documents these gates as failing open) but prints ONE `systemMessage` JSON line --
# the field Claude Code shows the user for every hook event -- naming the hook, the missing guarantee and the
# fix. Once per session per hook (a marker keyed on the parent pid); if the marker cannot be written the
# notice is simply repeated. The notice path uses only bash builtins: PATH is the thing that is broken.
# This is an accelerator for hosts that run hooks; /sigma-doctor reports the same condition on every host.
#
#   usage: _py.sh <hook-script.py> [args...]
if command -v python3 >/dev/null 2>&1 && python3 -c '' >/dev/null 2>&1; then
  exec python3 "$@"
fi

name=${1##*/}
case "$name" in
  gate_state.py|"") exit 0 ;;                  # a helper its caller (plan_gate.sh) already fails open on
esac
dir=${CLAUDE_PROJECT_DIR:-$PWD}
adopted=
while :; do
  if [ -f "$dir/.sdlc/config.json" ]; then adopted=1; break; fi
  [ -n "$dir" ] && [ "$dir" != "/" ] || break
  dir=${dir%/*}
done
[ -n "$adopted" ] || exit 0
what="a Sigma hook"
case "$name" in
  issue_field_gate.py) what="the new-issue priority-label gate" ;;
  decision_gate.py)    what="the decision-registry edit gate" ;;
  time_track.py)       what="process-time tracking" ;;
  research_capture.py) what="research capture" ;;
esac
marker="${TMPDIR:-/tmp}/sigma-py-notice-${UID:-0}-${PPID}-${name}"
if [ -e "$marker" ]; then exit 0; fi
{ : > "$marker"; } 2>/dev/null
printf '%s\n' "{\"systemMessage\": \"Sigma: hook ${name} did NOT run because python3 is missing or broken on PATH, so ${what} is off (tool calls are still allowed). Fix: install a working python3, then run /sigma-doctor.\"}"
exit 0

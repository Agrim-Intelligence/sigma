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
# fix. De-duplication is best effort and always errs toward SHOWING the notice: the marker key is the
# `session_id` from the hook's stdin JSON when present (parsed with builtins), else the parent pid -- which
# is the host's own pid only if the host does not put a fresh shell between itself and the hook, so on such a
# host the notice can repeat per event. The marker is a directory claimed atomically with `mkdir` inside a
# per-user 0700 directory that is checked (not a symlink, owned by us) before use; any failed check, or a
# `mkdir` that cannot run, means the notice is printed again. Nothing is ever written through a symlink.
# Markers are not pruned (empty dirs under $TMPDIR; the OS temp cleaner owns them). Apart from `mkdir`, the
# notice path uses only bash builtins: PATH is the thing that is broken.
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
# Adopted repo? Walk up from the project dir looking for .sdlc/config.json. The dir is made absolute first and
# the walk stops when stripping a component changes nothing, so a relative or empty value can never spin.
dir=${CLAUDE_PROJECT_DIR:-$PWD}
case "$dir" in /*) ;; *) dir=$PWD/$dir ;; esac
adopted=
while :; do
  if [ -f "$dir/.sdlc/config.json" ]; then adopted=1; break; fi
  parent=${dir%/*}
  [ -n "$parent" ] && [ "$parent" != "$dir" ] || break
  dir=$parent
done
[ -n "$adopted" ] || exit 0
what="a Sigma hook"
case "$name" in
  issue_field_gate.py) what="the new-issue priority-label gate" ;;
  decision_gate.py)    what="the decision-registry edit gate" ;;
  time_track.py)       what="process-time tracking" ;;
  research_capture.py) what="research capture" ;;
esac
# Session key: stdin `session_id` if it is a short safe token, else the parent pid.
key="p$PPID"
input=
IFS= read -r -d '' -t 1 input 2>/dev/null
case "$input" in
  *'"session_id"'*)
    rest=${input#*\"session_id\"}
    rest=${rest#*:}
    rest=${rest#*\"}
    sid=${rest%%\"*}
    case "$sid" in
      ""|*[!A-Za-z0-9_-]*) ;;
      *) [ ${#sid} -le 64 ] && key="s$sid" ;;
    esac ;;
esac
notice=1
d="${TMPDIR:-/tmp}/sigma-hooks-${UID:-0}"
mkdir -m 700 "$d" 2>/dev/null
if [ -d "$d" ] && [ ! -L "$d" ] && [ -O "$d" ]; then
  m="$d/py-notice-$key-$name"
  if mkdir -m 700 "$m" 2>/dev/null; then
    :                                           # claimed just now: first notice of this session
  elif [ -d "$m" ] && [ ! -L "$m" ] && [ -O "$m" ]; then
    notice=                                     # a marker we own already exists: already told
  fi                                            # anything else at that path (file, symlink): repeat
fi
if [ -n "$notice" ]; then
  printf '%s\n' "{\"systemMessage\": \"Sigma: hook ${name} did NOT run because python3 is missing or broken on PATH, so ${what} is off (tool calls are still allowed). Fix: install a working python3, then run /sigma-doctor.\"}"
fi
exit 0

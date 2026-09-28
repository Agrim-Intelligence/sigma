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
#   usage: _py.sh <hook-script.py> [args...]
if command -v python3 >/dev/null 2>&1 && python3 -c '' >/dev/null 2>&1; then
  exec python3 "$@"
fi
exit 0

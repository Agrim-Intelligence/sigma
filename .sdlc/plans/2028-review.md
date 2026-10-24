# Plan-review refinement for #2028 (plan sha256 7cc533bc…, SOUND-WITH-REFINEMENTS)

**R1.** `test_unclosed_stdin_is_bounded` runs the hook with `/bin/bash` explicitly, but only when
that file exists and reports a 3.x version. Production runs `bash session_start.sh`, and on macOS
`/bin/bash` is 3.2. Bash 3.2's `read -t` leaves the variable unset on timeout, which `set -u`
turns into a non-zero exit. Homebrew bash 5 on PATH leaves the variable set, so the control could
never go red under it. When no 3.x `/bin/bash` exists, the test skips and names the bash it found.

Control: delete the `HOOK_INPUT=""` line, see the test go red under `/bin/bash` 3.2, then restore it.

The plan file itself is unchanged. This note is the only change the delta review asked for.

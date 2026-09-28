#!/usr/bin/env bash
# Fallback copy-installer for hosts/users not using the Claude Code plugin system.
# Copies the portable spine into the skills dir and PRINTS the hook-wiring snippet
# (never edits settings.json — malformed JSON silently kills the hook).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${SDLC_KIT_SKILLS_DIR:-$HOME/.claude/skills}/sigma"

mkdir -p "$DEST"
cp -R "$SRC/hooks" "$DEST/"
# Guard only for "source not present yet" — a real copy failure must still abort under
# set -e, not be swallowed by `|| true` (which would print the success banner anyway).
if [ -d "$SRC/skills" ];   then cp -R "$SRC/skills" "$DEST/"; fi
if [ -d "$SRC/commands" ]; then cp -R "$SRC/commands" "$DEST/"; fi

cat <<EOF

✅ Sigma copied to: $DEST

To activate the SDLC hook, add this to ~/.claude/settings.json under "hooks"
(parse-check the file afterward — malformed JSON silently disables hooks):

  "UserPromptSubmit": [
    { "hooks": [
      { "type": "command",
        "command": "bash \"$DEST/hooks/agrim_gate.sh\"",
        "statusMessage": "Applying goal-based SDLC policy" }
    ] }
  ]

Verify: echo '{"prompt":"x"}' | bash "$DEST/hooks/agrim_gate.sh" | python3 -m json.tool
EOF

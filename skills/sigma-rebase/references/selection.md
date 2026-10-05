# sigma-rebase selection details

description: A manually-triggered, human-attended way to bring a feature branch current with its integration branch — explains WHAT the branch is behind on and the DECISIONS BEHIND those changes (from CHANGELOG.md, PR descriptions, linked design docs) before touching anything, flags the files most likely to conflict, then rebases. On a genuine conflict it walks you through named resolution options one file at a time. Once clean, it runs this repo's own proving command and — only if it passes, and only if you say yes — lands the branch. Use when the user runs /sigma-rebase, says their branch is behind / stale / needs to catch up with main, asks "what changed on main while I was on this branch", or wants to verify-and-land a feature branch that is already current.
allowed-tools: Bash(python3 *)

These fuller triggers are kept outside the always-listed description. Read the
skill's main instructions before acting.

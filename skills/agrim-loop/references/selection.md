# agrim-loop selection details

description: Run the autonomous park-and-continue SDLC loop over the .sdlc/goals backlog. Pick a goal (local goal files or GitHub issues), run it through the full SDLC with each phase as its own subagent and an author-blind reviewer, land it through a verified PR, record done/parked/failed, and repeat until the backlog or the budget stops the run. Use when the user runs /agrim-loop or asks to run goals autonomously / overnight / unattended.
allowed-tools: Bash(python3 *), Bash(gh issue view *)

These fuller triggers are kept outside the always-listed description. Read the
skill's main instructions before acting.

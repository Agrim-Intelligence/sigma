# sigma-define selection details

description: Open a new unit of work end to end — pick its type, name it, create and push its feature/<name> branch, create its feature:<name> label, ensure the registry directory, take in the details in whatever form you have them (free text, a story reference, a .md file), generate the issues with BOTH unit declarations on each, ask whether to give the unit a priority (optional — recorded on the unit's own registry entry, read as a tie-break; skip and nothing changes), then ask about assignment and about starting now. Use when the user runs /sigma-define, says "start a new feature / unit of work / workstream", or hands you an idea that should become its own branch rather than land on the current one.
allowed-tools: Bash(python3 *), Bash(git *), Bash(gh label *), Bash(gh issue *), Bash(gh repo *), Read, Grep

These fuller triggers are kept outside the always-listed description. Read the
skill's main instructions before acting.

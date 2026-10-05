#!/usr/bin/env python3
"""PreToolUse gate: an issue must be born ranked.

WHY THIS EXISTS. `GitHubSource.create_dependency` already sets Status, Priority and assignee on
every issue the KIT creates, and tests/test_issue_creation_boundary.py keeps it the only such call
site *in this repo's code*. Neither covers the way issues actually arrive: an agent (or a human)
typing `gh issue create` into a session. That path produces an issue with no priority label, which
`_sync_backlog` then cards with a Status but no Priority — or, without the `sdlc:goal` label, does
not card at all. Measured on this repo before this gate landed: 155 open issues, 63 of them never
on the board, 13 with no assignee, 8 cards blank on Status. This closes that boundary at the only
point where the missing value is still cheap to supply — creation.

WHAT IT REQUIRES. Exactly one thing: a `priority:P<n>` label on the `gh issue create`. That is the
field the board mirrors (`_mirror_priority`, #719) and the field the queue ranks by (`_card_rank`,
#698), so an issue born with it gets a correct Priority column for free on the next board touch,
and an issue born without it can only be fixed by hand. Status is deliberately NOT required here:
it is a board-side single-select that `gh issue create` cannot set at all, and `_sync_backlog`
seeds it correctly the moment the label exists.

ASSIGNEE IS NOT REQUIRED, BY DESIGN. `.sdlc/config.json` scopes discovery to one owner, and its own
note states the consequence: "a goal filed with NO assignee is invisible to every loop." That makes
assignment consequential enough to be a human's call, not a gate's default — so the denial message
tells the agent to ASK who owns it rather than silently stamping one.

FAIL-OPEN, ALWAYS. A gate that errors must never block real work: every failure path here exits 0
(allow), matching hooks/decision_gate.py's own outer handler and hooks/_py.sh's broken-interpreter
preflight.

PARSING IS BEST-EFFORT, AND THAT IS THE SAFE DIRECTION. `shlex.split` drops quoting, so a
`--body` that happens to contain the text "priority:P1" satisfies the check. That is a
false-ALLOW: the gate lets one command through it should have caught. The opposite error — a
false-DENY on a legitimate command — is the one that costs an operator real time, so the parser is
tuned to under-block rather than over-block. Stated rather than silently assumed, matching this
repo's established "NAMED LIMITATION" style (tests/test_issue_creation_boundary.py,
tests/test_vocabulary_coverage.py).
"""
import json
import re
import shlex
import sys
from pathlib import Path

#: A priority label, e.g. `priority:P0` .. `priority:P4`. The prefix mirrors
#: `GitHubSource.priority_prefix` (configurable there; a hook has no config, so the default is
#: what is enforced — an adopter spelling priority differently just gets no gate, never a wrong one).
_PRIORITY = re.compile(r"^priority:P\d+$")

_SEPARATORS = ("&&", "||", ";", "|", "&")

_REASON = (
    "BLOCKED: `gh issue create` without a `priority:P<n>` label.\n"
    "\n"
    "An issue filed bare lands on the board with a blank Priority column, and nothing ever fills "
    "it in — the mirror only ranks what already carries a label. Rank it now, at creation.\n"
    "\n"
    "Add one label and re-run, e.g.:\n"
    "    gh issue create --title '...' --body '...' --label priority:P2 --label sdlc:goal\n"
    "\n"
    "Also decide, before you file it:\n"
    "  * ASSIGNEE — ask the user who owns this. Discovery is scoped to a single owner, so an "
    "issue with no assignee is invisible to every loop and silently never runs. Do not guess one.\n"
    "  * `--label sdlc:goal` if it should be queued for work; leave it off for a tracked "
    "follow-up a human will promote.\n"
    "\n"
    "When Sigma is filing this itself, prefer `handoff.py track` (skills/sigma-loop/scripts/"
    "handoff.py): it sets Status, Priority and assignee together and cannot produce a bare issue."
)


def _segments(command):
    """The individual commands inside a possibly-compound shell string. [] when unparseable."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []                       # unbalanced quotes — fail open, see module docstring
    segments, current = [], []
    for token in tokens:
        if token in _SEPARATORS:
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return [s for s in segments if s]


def _is_issue_create(segment):
    """`gh ... issue create ...` — `issue` immediately followed by `create`, so `gh issue list`
    and a `--title` mentioning the words in either order are both left alone."""
    if not segment or segment[0] != "gh":
        return False
    return any(a == "issue" and b == "create" for a, b in zip(segment, segment[1:]))


def _labels(segment):
    """Every label value in the segment, across `--label x`, `-l x`, `--label=x`, and the
    comma-separated form `--label a,b` that `gh` also accepts."""
    values = []
    for a, b in zip(segment, segment[1:]):
        if a in ("--label", "-l"):
            values.append(b)
    for token in segment:
        if token.startswith("--label="):
            values.append(token.split("=", 1)[1])
    return [v.strip() for value in values for v in value.split(",")]


def evaluate(tool_name, tool_input):
    """Pure decision logic — returns (permissionDecision, reason|None). No I/O, so it is testable
    without a subprocess (the same split hooks/decision_gate.py uses)."""
    if tool_name != "Bash":
        return "allow", None
    for segment in _segments(tool_input.get("command") or ""):
        if _is_issue_create(segment) and not any(_PRIORITY.match(l) for l in _labels(segment)):
            return "deny", _REASON
    return "allow", None


def _adopted():
    """#2737: this gate is inert outside an adopted repository (`.sdlc/config.json` at or above
    `$CLAUDE_PROJECT_DIR`/cwd). The one definition lives in gate_state.py; it is imported lazily
    because the tests load this file via importlib with `hooks/` off sys.path, and a failed import
    reads as allow — the file's own fail-open contract."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import gate_state
        return gate_state.adopted_root() is not None
    except Exception:
        return False


def main():
    if not _adopted():
        return 0                                    # not an adopted repo — allow, silently
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0                                    # no/!json stdin — allow
    try:
        decision, reason = evaluate(data.get("tool_name", ""), data.get("tool_input") or {})
    except Exception:
        return 0                                    # never block because the gate itself broke
    if decision == "allow":
        return 0                                    # silent: print nothing on the happy path
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

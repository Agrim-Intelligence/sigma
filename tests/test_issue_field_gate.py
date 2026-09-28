"""Issue-field gate: an issue must be born carrying a `priority:P<n>` label.

Same principle tests/test_decision_gate.py states for its own gate — the value is entirely in the
precision, and a gate that cries wolf gets clicked through — so most of these pin the cases where
it must STAY SILENT, not the one where it fires.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
G = ROOT / "hooks" / "issue_field_gate.py"


def _gate():
    spec = importlib.util.spec_from_file_location("issue_field_gate", G)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _decide(command, tool_name="Bash"):
    return _gate().evaluate(tool_name, {"command": command})[0]


# ---------------------------------------------------------------- it fires
def test_bare_issue_create_is_denied():
    assert _decide("gh issue create --title 'x' --body 'y'") == "deny"


def test_denied_even_when_other_labels_are_present():
    """A label is not a priority. `sdlc:goal` queues the issue; only `priority:P*` ranks it."""
    assert _decide("gh issue create --title x --body y --label sdlc:goal --label bug") == "deny"


def test_denied_inside_a_compound_command():
    """The real leak shape: a bare create chained behind something innocuous."""
    assert _decide("git status && gh issue create --title x --body y") == "deny"


# ---------------------------------------------------------------- it stays silent
def test_priority_label_allows():
    assert _decide("gh issue create --title x --body y --label priority:P2") == "allow"


def test_every_spelling_gh_accepts_is_recognised():
    """`-l`, `--label=`, and the comma-joined form are all real `gh` syntax. Missing one of them
    would deny a legitimate command — the expensive direction of error."""
    for form in ("-l priority:P0",
                 "--label=priority:P1",
                 "--label bug,priority:P3",
                 "--label=type:bug,priority:P4"):
        assert _decide("gh issue create --title x --body y " + form) == "allow", form


def test_other_gh_issue_subcommands_are_untouched():
    for cmd in ("gh issue list --label sdlc:goal",
                "gh issue view 123",
                "gh issue edit 12 --add-label bug",
                "gh issue comment 12 --body 'create a follow-up'"):
        assert _decide(cmd) == "allow", cmd


def test_the_words_issue_and_create_in_prose_do_not_trigger_it():
    """`issue` must be immediately followed by `create` — otherwise any command whose text happens
    to contain both words (a commit message, a comment body) would be blocked."""
    assert _decide("git commit -m 'create the issue template'") == "allow"
    assert _decide("gh pr create --title 'fix the issue'") == "allow"


def test_non_bash_tools_are_untouched():
    assert _decide("gh issue create --title x", tool_name="Edit") == "allow"


# ---------------------------------------------------------------- it fails open
def test_unparseable_command_is_allowed():
    """Unbalanced quotes make shlex raise. A gate that errored into a DENY would block real work
    over its own parser — so this must allow."""
    assert _decide("gh issue create --title 'unterminated") == "allow"


def test_missing_command_key_is_allowed():
    assert _gate().evaluate("Bash", {})[0] == "allow"


def test_denial_names_the_assignee_decision():
    """The assignee is deliberately NOT gated (config scopes discovery to one owner, so it is a
    human's call) — but the message has to SAY so, or the gate quietly teaches that priority is
    the only field that matters at creation."""
    _, reason = _gate().evaluate("Bash", {"command": "gh issue create --title x"})
    assert "ASSIGNEE" in reason and "priority:P" in reason


def test_deny_text_names_the_documented_handoff_gesture():
    """#2737: the deny text points at the documented user gesture (`handoff.py track`, README and
    the agrim-loop SKILL.md), never at the internal `handoff.create_tracked_issue` API."""
    _, reason = _gate().evaluate("Bash", {"command": "gh issue create --title x"})
    assert "handoff.py track" in reason
    assert "create_tracked_issue" not in reason

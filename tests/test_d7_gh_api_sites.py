"""D7 'every refusal prints its fix': the gh_api.py sites a first-run GitHub-mode user can reach.

`GhApiError` now appends the fix for its classified kind to `str(exc)`; the raw reason stays first
and `.hint` / `.status` / `.kind` are untouched. A refusal that merely reports a malformed reply or a
caller-contract violation is NOT given a fix (there is none a user can apply): see
docs/launch/evidence/d7-github-mode-refusals-*.md for the site-by-site classification."""
import importlib.util
import pathlib

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
BAD = "gh: Bad credentials (HTTP 401)"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _raiser(text):
    def run(args):
        exc = RuntimeError("gh " + " ".join(args) + " failed: " + text)
        exc.hint = text
        raise exc
    return run


@pytest.mark.parametrize("text,kind,needle", [
    (BAD, "auth", "gh auth login"),
    ("gh: Not Found (HTTP 404)", "not_found", "discovery.github.repo"),
    ("gh: Resource not accessible by integration (HTTP 403)", "permission", "repo and workflow"),
    ("gh: API rate limit exceeded (HTTP 403)", "rate_limit", "gh api rate_limit"),
    ("gh: Bad Gateway (HTTP 502)", "server", "retry"),
])
def test_a_wrapped_gh_failure_str_carries_the_fix_after_gh_text(text, kind, needle):
    api = _mod("gh_api")
    with pytest.raises(api.GhApiError) as ei:
        api.view_pr(_raiser(text), 5, "OWNER/NAME")
    e = ei.value
    assert e.kind == kind and e.hint == text                  # classification and hint untouched
    s = str(e)
    assert text in s and "fix: " in s and needle in s
    assert s.index(text) < s.index("fix: ")


def test_str_of_an_unclassified_or_malformed_error_is_unchanged():
    api = _mod("gh_api")
    assert str(api.GhApiError("malformed REST issue: no body key")) == "malformed REST issue: no body key"
    assert str(api.GhApiError("x", None, None, "invalid")) == "x"


def test_the_remedy_is_not_embedded_mid_sentence_in_a_fallback_failure():
    """A fallback that also fails quotes the REST error; it must quote gh's text, not the fix line."""
    api = _mod("gh_api")
    calls = []

    def run(args):
        calls.append(args)
        text = "gh: Bad Gateway (HTTP 502)" if args[0] == "api" else "gh: Bad credentials (HTTP 401)"
        exc = RuntimeError("gh failed: " + text)
        exc.hint = text
        raise exc

    with pytest.raises(api.GhApiError) as ei:
        api.read_issue(run, 5, ["title"], "OWNER/NAME", gql_run=run, env={})
    s = str(ei.value)
    assert "also failed" in s and s.count("fix: ") == 1
    assert s.index("also failed") < s.index("fix: ")          # the fix closes the message, once


def test_list_issues_keeps_the_classification_of_a_gh_failure():
    api = _mod("gh_api")

    def fetch(run, repo, labels, cap, state="open"):
        return run(["api", "x"])

    with pytest.raises(api.GhApiError) as ei:
        api.list_issues(run=_raiser(BAD), repo="OWNER/NAME", fetch=fetch)
    assert ei.value.kind == "auth" and ei.value.status == 401
    assert "gh auth login" in str(ei.value)


def test_add_assignees_unassignable_user_says_what_to_do():
    api = _mod("gh_api")

    def run(args):
        return '{"assignees": []}' if "assignees" in " ".join(args) else "{}"

    with pytest.raises(api.GhApiError) as ei:
        api.add_assignees(run, 5, ["someone"], "OWNER/NAME", env={})
    s = str(ei.value)
    assert s.startswith("GitHub did not assign @someone to #5 (not assignable, or not a collaborator)")
    assert "discovery.github.assignee" in s and "collaborator" in s.split("fix:")[-1]

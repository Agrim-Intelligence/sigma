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
    fix = s.split("fix:")[-1]
    assert "discovery.github.assignee" not in s          # callers pass an arbitrary user: not the lever
    assert "collaborator" in fix and "assignable" in fix


# ---------------------------------------------------------------- review of #1351

def _two_faced(rest_text, fallback_text):
    def run(args):
        text = rest_text if args and args[0] == "api" else fallback_text
        exc = RuntimeError("gh failed: " + text)
        exc.hint = text
        raise exc
    return run


def test_a_fix_colon_title_in_argv_does_not_hide_the_remedy_and_nothing_doubles():
    api = _mod("gh_api")
    with pytest.raises(api.GhApiError) as ei:
        api.comment_issue(_raiser(BAD), 5, "fix: crash", "OWNER/NAME", env={})
    e = ei.value
    s = str(e)
    assert "fix: crash" in s and "run `gh auth login`" in s
    assert s.count("run `gh auth login`") == 1
    assert api.with_remedy(e).count("run `gh auth login`") == 1          # re-wrapping does not double
    assert api.with_remedy(e, str(e)).count("run `gh auth login`") == 1


def test_an_error_with_no_gh_stderr_gets_no_remedy_even_if_argv_has_a_marker_word():
    """The class of a hint-less error comes from str(exc), which embeds argv: never print a fix for it."""
    api = _mod("gh_api")

    def run(args):
        raise RuntimeError("unknown label(s) on this repo: sdlc:timeout")
    with pytest.raises(api.GhApiError) as ei:
        api.add_labels(run, 5, ["sdlc:timeout"], "OWNER/NAME", env={})
    assert "fix: " not in str(ei.value)


@pytest.mark.parametrize("label", ["sdlc:timeout", "rate-limit-rework"])
def test_no_gh_stderr_means_no_remedy_through_every_wrapper(label):
    """The guard lives in with_remedy, not only in __str__: sources._fx, promote._why and
    gh_api.with_remedy all take the same hint-less GhApiError."""
    src = _mod("sources")
    api = src.gh_api                                  # the module sources.py itself uses
    prom = _mod("promote")

    def run(args):
        raise RuntimeError("unknown label(s) on this repo: " + label)
    with pytest.raises(api.GhApiError) as ei:
        api.add_labels(run, 5, [label], "OWNER/NAME", env={})
    e = ei.value
    assert not e.hint
    for out in (str(e), src._fx(e), api.with_remedy(e), prom._why(e)):
        assert "fix: " not in out and "network" not in out and "quota" not in out, out


def test_the_assign_failure_comment_on_the_issue_carries_no_fix_line():
    """The remedy is for the local message; the public issue comment gets the plain text."""
    src = _mod("sources")
    api = src.gh_api
    gh = src.GitHubSource({"discovery": {"source": "github", "github": {"repo": "OWNER/NAME"}}}, run=lambda a: "")

    def run(args):
        return '{"assignees": []}' if "assignees" in " ".join(args) else "{}"
    with pytest.raises(api.GhApiError) as ei:
        api.add_assignees(run, 5, ["vox"], "OWNER/NAME", env={})
    err = ei.value
    assert "fix: " in str(err)                        # the local message keeps it
    posted = []
    gh._create_issue = lambda title, body, labels: 7
    gh._issue_add_assignees = lambda number, assignee: (_ for _ in ()).throw(err)
    gh._issue_comment = lambda number, text: posted.append(text)
    gh.create_dependency("t", "b", "vox")
    assert posted and "Could not assign @vox" in posted[0] and "did not assign" in posted[0]
    assert "fix: " not in posted[0]


def test_a_permission_403_does_not_trip_the_board_scope_note(capsys):
    """The permission fix used to contain the words _note_scope matches on."""
    src = _mod("sources")
    api = _mod("gh_api")
    gh = src.GitHubSource({"discovery": {"source": "github", "github": {"repo": "OWNER/NAME"}}}, run=lambda a: "")
    with pytest.raises(api.GhApiError) as ei:
        api.add_labels(_raiser("gh: Resource not accessible by integration (HTTP 403)"), 5, ["x"], "OWNER/NAME", env={})
    assert gh._note_scope(ei.value) is False
    assert "board updates OFF" not in capsys.readouterr().err
    assert gh._note_scope(RuntimeError("missing required scopes [project]")) is True


def test_no_marker_a_caller_scans_error_text_for_appears_in_any_remedy():
    """Callers match markers in the error text (sources._TRANSIENT, cross_repo, promote, the board scope
    note, the proxy-block markers): none of them may be satisfied by a remedy line alone."""
    sess = _mod("gh_session")
    src = _mod("sources")
    xr = _mod("cross_repo")
    pr = _mod("promote")
    markers = set(src.GitHubSource._TRANSIENT) | set(pr._RATE_LIMIT_MARKERS) | set(sess._SESSION_BLOCK_MARKERS)
    markers |= set(sess._GRAPHQL_UNAVAILABLE_MARKERS)
    for name in dir(xr):
        if name.endswith("_MARKERS") and isinstance(getattr(xr, name), tuple):
            markers |= set(getattr(xr, name))
    markers |= {"project", "scope", "rate limit", "abuse"}   # "auth refresh" only counts with "project"
    for kind, fix in sess.REMEDIES.items():
        low = fix.lower()
        hit = sorted(m for m in markers if m in low)
        assert not hit, (kind, hit)


@pytest.mark.parametrize("call", [
    lambda api, run: api.read_issue(run, 5, ["title"], "OWNER/NAME", gql_run=run, env={}),
    lambda api, run: api.list_issues_gh(run, ["title"], repo="OWNER/NAME", gql_run=run, env={}),
    lambda api, run: api.view_pr_gh(run, 5, ["title"], "OWNER/NAME", gql_run=run, env={}),
    lambda api, run: api.open_pr_for_branch_gh(run, "br", ["number"], "OWNER/NAME", gql_run=run, env={}),
    lambda api, run: api.open_prs_for_head_gh(run, "br", 5, "OWNER/NAME", gql_run=run, env={}),
], ids=["read_issue", "list_issues_gh", "view_pr_gh", "open_pr_for_branch_gh", "open_prs_for_head_gh"])
def test_every_fallback_failure_quotes_gh_text_and_ends_with_one_fix(call):
    api = _mod("gh_api")
    run = _two_faced("gh: Bad Gateway (HTTP 502)", "gh: Bad Gateway (HTTP 502)")
    with pytest.raises(api.GhApiError) as ei:
        call(api, run)
    s = str(ei.value)
    assert "also failed" in s, s
    assert s.count("fix: ") == 1 and s.index("also failed") < s.index("fix: ")


def test_note_scope_and_is_transient_read_gh_text_not_the_appended_fix(monkeypatch):
    """Second layer: even if a fix line DID contain marker words, the scans read the plain text."""
    src = _mod("sources")
    api = _mod("gh_api")
    sess = _mod("gh_session")
    api = src.gh_api                       # the very module sources.py uses (isinstance must match)
    monkeypatch.setitem(api.gh_session.REMEDIES, "permission", "fix: project scope, try again, timeout, rate limit")
    gh = src.GitHubSource({"discovery": {"source": "github", "github": {"repo": "OWNER/NAME"}}}, run=lambda a: "")
    e = api.GhApiError("gh api x failed: gh: nope (HTTP 403)", "gh: nope (HTTP 403)", 403, "permission")
    assert "project scope" in str(e)
    assert gh._note_scope(e) is False
    assert gh._is_transient(e) is False

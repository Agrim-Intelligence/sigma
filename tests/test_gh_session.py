"""gh_session.proxy_session_block: tell a Claude Code Remote session's gh proxy block (#78) apart
from a real GitHub/gh error. Pure string classification, zero I/O — these tests use the exact
proxy-injected text quoted in issue #78's own repro, plus real-shaped gh/GitHub errors that must
NOT be misclassified."""
import importlib.util
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


#: Verbatim from issue #78's repro — the GraphQL-pinned-ops shape (`gh auth status`, `gh pr list`).
SHAPE_GRAPHQL = (
    "This GraphQL query (PullRequestList, sent by gh pr list) is not enabled for this session — "
    "only the pinned set of PR-review operations is served. Use REST via "
    "`gh api repos/{owner}/{repo}/...` instead."
)

#: Verbatim from issue #78's repro — the App-connection-required shape (`gh api repos/...`,
#: `gh pr create/view/merge`, `gh repo view`, `gh api graphql`).
SHAPE_APP = (
    "GitHub access is not enabled for this session. An org admin must connect the Claude GitHub "
    "App for this organization."
)


def test_recognizes_the_graphql_pinned_ops_shape():
    gh_session = _mod("gh_session")
    assert gh_session.proxy_session_block(SHAPE_GRAPHQL) == gh_session.REMEDIATION


def test_recognizes_the_app_connection_required_shape():
    gh_session = _mod("gh_session")
    assert gh_session.proxy_session_block(SHAPE_APP) == gh_session.REMEDIATION


def test_remediation_names_the_real_fix_not_gh_auth_login():
    """The whole point of #78: the printed fix must stop RECOMMENDING `gh auth login` (which does
    nothing here) and point at the org admin / Claude GitHub App connection instead. `gh auth login`
    may still be NAMED, but only to say it will not fix this — never as the suggested action."""
    gh_session = _mod("gh_session")
    text = gh_session.REMEDIATION.lower()
    assert "claude github app" in text
    assert "will not fix it" in text
    assert "run: gh auth login" not in text
    assert not text.strip().lower().startswith("run: gh auth login")


def test_case_insensitive_match():
    gh_session = _mod("gh_session")
    assert gh_session.proxy_session_block(SHAPE_APP.upper()) == gh_session.REMEDIATION


def test_matches_as_a_substring_of_a_larger_message():
    """Callers fold this into a reconstructed command-line error (work.py) or a `.hint` (sources.py)
    — the marker text will usually not be the ENTIRE string, just embedded in it."""
    gh_session = _mod("gh_session")
    wrapped = f"gh pr create --title x --body y --base main: {SHAPE_APP}"
    assert gh_session.proxy_session_block(wrapped) == gh_session.REMEDIATION


def test_none_for_empty_or_missing_text():
    gh_session = _mod("gh_session")
    assert gh_session.proxy_session_block("") is None
    assert gh_session.proxy_session_block(None) is None


def test_none_for_a_real_gh_not_authenticated_message():
    """The ordinary, pre-#78 failure this check must keep reporting as a real "run gh auth login"
    case — never reclassified as a proxy block."""
    gh_session = _mod("gh_session")
    real = "You are not logged into any GitHub hosts. Run gh auth login to authenticate."
    assert gh_session.proxy_session_block(real) is None


def test_none_for_real_github_api_error_shapes():
    """GitHub's own error bodies must never be mistaken for the proxy-injected shapes — see the
    module docstring's own list of what a genuine GitHub 4xx looks like."""
    gh_session = _mod("gh_session")
    for real in (
        "HTTP 401: Bad credentials (https://api.github.com/user)",
        "HTTP 403: Resource not accessible by integration",
        "HTTP 404: Not Found (https://api.github.com/repos/acme/widget)",
        "HTTP 403: API rate limit exceeded for xxx.xxx.xxx.xxx",
        "gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN environment variable.",
    ):
        assert gh_session.proxy_session_block(real) is None, real


def test_none_for_a_plain_git_failure():
    """work.py's `_run` runs both git and gh through the same function — an ordinary git error must
    never be misclassified as a gh session block."""
    gh_session = _mod("gh_session")
    assert gh_session.proxy_session_block("fatal: not a git repository") is None


def test_second_anchor_matches_even_without_the_session_phrase():
    """Belt-and-suspenders anchor (module docstring): if a future proxy revision drops "for this
    session" from shape 2 alone, the "connect the Claude GitHub App" sentence still matches."""
    gh_session = _mod("gh_session")
    drifted = "GitHub access is blocked. An org admin must connect the Claude GitHub App for this org."
    assert gh_session.proxy_session_block(drifted) == gh_session.REMEDIATION


def test_never_raises_on_a_non_string_input():
    gh_session = _mod("gh_session")
    assert gh_session.proxy_session_block(12345) is None
    assert gh_session.proxy_session_block(["not", "a", "string"]) is None

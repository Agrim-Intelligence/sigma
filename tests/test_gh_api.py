"""#801 slice 1 + #895 slice 2a: skills/sigma-loop/scripts/gh_api.py -- GraphQL capability check, REST
helper ops, and the REST-first `read_issue` policy (classification, normaliser, breaker, fallback log).

Only `read_issue` has product callers (sources.py); no claim is made that /sigma-loop works in a Claude
Code cloud session (unmeasured). Every test injects a fake `run` that
follows the sources convention: args WITHOUT a leading "gh", returns stdout str, RAISES on failure.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


GQL_TEXT = "HTTP 403: GitHub GraphQL is not available from Claude Code sessions"
PROBE_ARGS = ["api", "graphql", "-f", "query={viewer{login}}"]


class Fake:
    """Recording fake `run`: replies is a list of str (returned) or Exception (raised), reused last."""

    def __init__(self, *replies):
        self.replies = list(replies) or ["{}"]
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        r = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


# ---------------------------------------------------------------- capability check

def test_override_off_and_on_beat_cloud_env():
    g = _mod("gh_api")
    off = g.graphql_available(env={"SIGMA_GH_GRAPHQL": "off"})
    assert off["available"] is False and off["source"] == "override"
    for v in ("0", "false", "OFF"):
        assert g.graphql_available(env={"SIGMA_GH_GRAPHQL": v})["available"] is False
    on = g.graphql_available(env={"SIGMA_GH_GRAPHQL": "on", "CLAUDE_CODE_REMOTE": "true"})
    assert on["available"] is True and on["source"] == "override"


def test_unknown_override_ignored_and_noted():
    g = _mod("gh_api")
    r = g.graphql_available(env={"SIGMA_GH_GRAPHQL": "banana"})
    assert r["available"] is True and r["source"] == "default"
    assert "banana" in r["reason"]
    r = g.graphql_available(env={"SIGMA_GH_GRAPHQL": "banana", "CLAUDE_CODE_REMOTE": "1"})
    assert r["available"] is False and r["source"] == "env"


def test_cloud_env_means_unavailable():
    g = _mod("gh_api")
    for v in ("true", "1", "TRUE"):
        r = g.graphql_available(env={"CLAUDE_CODE_REMOTE": v})
        assert r["available"] is False and r["source"] == "env"
        assert "cloud" in r["reason"].lower()


def test_empty_or_false_cloud_env_is_default_available():
    g = _mod("gh_api")
    for env in ({}, {"CLAUDE_CODE_REMOTE": ""}, {"CLAUDE_CODE_REMOTE": "false"}):
        r = g.graphql_available(env=env)
        assert r == {"available": True, "reason": r["reason"], "source": "default"}


def test_probe_false_never_calls_run(tmp_path):
    g = _mod("gh_api")
    run = Fake("{}")
    g.graphql_available(env={}, cache_dir=tmp_path, run=run, probe=False)
    assert run.calls == []
    assert not (tmp_path / "gh-capability.json").exists()


def test_probe_true_without_run_raises_value_error():
    g = _mod("gh_api")
    with pytest.raises(ValueError):
        g.graphql_available(env={}, probe=True)


def test_probe_success_available_and_cache_written(tmp_path):
    g = _mod("gh_api")
    run = Fake('{"data":{}}')
    r = g.graphql_available(env={"CLAUDE_CODE_REMOTE": "", "UNRELATED_VAR": "canary-value"}, cache_dir=tmp_path,
                            run=run, now=1000.0, probe=True)
    assert run.calls == [PROBE_ARGS]
    assert r["available"] is True and r["source"] == "probe"
    data = json.loads((tmp_path / "gh-capability.json").read_text())
    assert set(data) == {"env_key", "available", "reason", "at"}
    assert data["env_key"] == "" and data["available"] is True and data["at"] == 1000.0
    assert "canary-value" not in json.dumps(data)
    assert list(tmp_path.glob("*.tmp")) == [] and [p.name for p in tmp_path.iterdir()] == ["gh-capability.json"]


def test_probe_third_shape_is_unavailable(tmp_path):
    g = _mod("gh_api")
    run = Fake(RuntimeError(GQL_TEXT))
    r = g.graphql_available(env={}, cache_dir=tmp_path, run=run, now=5.0, probe=True)
    assert r["available"] is False and r["source"] == "probe"
    assert json.loads((tmp_path / "gh-capability.json").read_text())["available"] is False


def test_probe_inconclusive_fails_open_and_does_not_cache_unavailable(tmp_path):
    g = _mod("gh_api")
    run = Fake(RuntimeError("dial tcp: i/o timeout"))
    r = g.graphql_available(env={}, cache_dir=tmp_path, run=run, now=5.0, probe=True)
    assert r["available"] is True and "inconclusive" in r["reason"]
    p = tmp_path / "gh-capability.json"
    assert not p.exists() or json.loads(p.read_text())["available"] is not False


def test_second_call_within_ttl_uses_cache_then_reprobes_after_ttl(tmp_path):
    g = _mod("gh_api")
    run = Fake(RuntimeError(GQL_TEXT), '{"data":{}}')
    first = g.graphql_available(env={}, cache_dir=tmp_path, run=run, now=1000.0, probe=True)
    assert first["available"] is False
    second = g.graphql_available(env={}, cache_dir=tmp_path, run=run, now=1000.0 + g.CACHE_TTL_SECONDS - 1,
                                 probe=True)
    assert second["source"] == "cache" and second["available"] is False and len(run.calls) == 1
    third = g.graphql_available(env={}, cache_dir=tmp_path, run=run, now=1000.0 + g.CACHE_TTL_SECONDS + 1,
                                probe=True)
    assert third["available"] is True and third["source"] == "probe" and len(run.calls) == 2


def test_cache_with_different_env_key_is_a_miss(tmp_path):
    g = _mod("gh_api")
    (tmp_path / "gh-capability.json").write_text(json.dumps(
        {"env_key": "yes", "available": False, "reason": "x", "at": 1000.0}))
    run = Fake('{"data":{}}')
    r = g.graphql_available(env={"CLAUDE_CODE_REMOTE": "false"}, cache_dir=tmp_path, run=run,
                            now=1001.0, probe=True)
    assert r["source"] == "probe" and len(run.calls) == 1


def test_corrupt_cache_is_a_miss(tmp_path):
    g = _mod("gh_api")
    (tmp_path / "gh-capability.json").write_text("{not json")
    run = Fake('{"data":{}}')
    r = g.graphql_available(env={}, cache_dir=tmp_path, run=run, now=1.0, probe=True)
    assert r["available"] is True and r["source"] == "probe"


def test_exactly_one_probe_call_no_retry(tmp_path):
    g = _mod("gh_api")
    run = Fake(RuntimeError("boom"))
    g.graphql_available(env={}, cache_dir=tmp_path, run=run, now=1.0, probe=True)
    assert len(run.calls) == 1


def test_missing_cache_dir_is_created(tmp_path):
    g = _mod("gh_api")
    d = tmp_path / "a" / "b"
    g.graphql_available(env={}, cache_dir=d, run=Fake("{}"), now=1.0, probe=True)
    assert (d / "gh-capability.json").exists()


def test_unwritable_cache_dir_is_swallowed_and_noted(tmp_path):
    g = _mod("gh_api")
    blocker = tmp_path / "file"
    blocker.write_text("x")          # a file where a directory is needed -> mkdir/open fails
    r = g.graphql_available(env={}, cache_dir=blocker / "sub", run=Fake("{}"), now=1.0, probe=True)
    assert r["available"] is True and "cache" in r["reason"].lower()


def test_importing_gh_api_does_not_load_sources():
    """R6: gh_api must not pull `sources` (which reconfigures stdout and loads a dozen siblings)."""
    code = (
        "import importlib.util,sys\n"
        "p=%r\n"
        "s=importlib.util.spec_from_file_location('gh_api',p);m=importlib.util.module_from_spec(s)\n"
        "s.loader.exec_module(m)\n"
        "print(sorted(n for n in sys.modules if n.split('.')[0]=='sources'))\n"
    ) % str(S / "gh_api.py")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_importing_gh_api_loads_neither_sources_nor_state():
    """R6 + #895: `sources` and `state` are loaded lazily (state only when a read has an sdlc_dir).
    `_load` never registers in sys.modules, so record what `spec_from_file_location` is asked for."""
    code = (
        "import importlib.util,pathlib\n"
        "seen=[];orig=importlib.util.spec_from_file_location\n"
        "def spy(name,*a,**k):\n    seen.append(name);return orig(name,*a,**k)\n"
        "importlib.util.spec_from_file_location=spy\n"
        "s=orig('gh_api',%r);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
        "print(sorted(set(seen)))\n"
    ) % str(S / "gh_api.py")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = out.stdout.strip()
    assert "'sources'" not in loaded and "'state'" not in loaded, loaded


def test_sources_reads_issues_through_gh_api():
    """#895 slice 2a: sources.py's issue reads go through gh_api.read_issue; no `issue view` argv left."""
    src = (S / "sources.py").read_text()
    assert 'gh_api = _load("gh_api")' in src
    assert '"issue", "view"' not in src
    assert src.count("gh_api.read_issue(") == 2      # the shared `_read_issue` + `fetch_comments`


# ---------------------------------------------------------------- GhApiError

def test_ghapierror_wraps_text_and_hint():
    g = _mod("gh_api")
    inner = RuntimeError(GQL_TEXT)
    inner.hint = "short hint"
    with pytest.raises(g.GhApiError) as ei:
        g.comment_issue(Fake(inner), 1, "x")
    assert str(ei.value) == GQL_TEXT and ei.value.hint == "short hint"
    gs = _mod("gh_session")
    assert gs.graphql_unavailable(str(ei.value)) is True
    with pytest.raises(g.GhApiError) as ei:
        g.comment_issue(Fake(RuntimeError("plain")), 1, "x")
    assert ei.value.hint is None


def test_proxy_block_text_survives_the_wrapper():
    g, gs = _mod("gh_api"), _mod("gh_session")
    with pytest.raises(g.GhApiError) as ei:
        g.view_pr(Fake(RuntimeError("GitHub access is not enabled for this session.")), 3)
    assert gs.proxy_session_block(str(ei.value)) == gs.REMEDIATION


# ---------------------------------------------------------------- REST ops

def _assert_clean(run):
    for call in run.calls:
        assert "graphql" not in " ".join(call)
        assert call[0] == "api" and "--method" in call


def test_list_issues_delegates_to_injected_fetch():
    g = _mod("gh_api")
    seen = {}

    def fetch(run, repo, labels, cap, **kw):
        seen.update(run=run, repo=repo, labels=labels, cap=cap, kw=kw)
        return [{"number": 1}]

    run = Fake()
    assert g.list_issues(run, repo="o/r", labels=["sdlc:goal"], fetch=fetch) == [{"number": 1}]
    assert seen["repo"] == "o/r" and seen["labels"] == ["sdlc:goal"] and seen["cap"] == 200
    assert seen["run"] is run


def test_list_issues_agrees_with_real_sources_fetch_issues_rest():
    g, sources = _mod("gh_api"), _mod("sources")
    run = Fake(json.dumps([{"number": 2}, {"number": 3, "pull_request": {}}]))
    out = g.list_issues(run, repo="o/r", labels=["a"], cap=5, fetch=sources.fetch_issues_rest)
    assert out == [{"number": 2}]
    assert run.calls[0][:3] == ["api", "repos/o/r/issues", "--method"]
    _assert_clean(run)


def _rest_issue(n=7, comments=0, **kw):
    d = {"number": n, "state": "open", "state_reason": None, "user": {"login": "alice", "type": "User"},
         "body": "B", "labels": [{"name": "sdlc:goal"}], "assignees": [{"login": "bob"}],
         "closed_at": None, "comments": comments}
    d.update(kw)
    return d


def _rest_comment(i, login=None, utype="User"):
    return {"id": 1000 + i, "node_id": "IC_%d" % i, "user": {"login": login or "u%d" % i, "type": utype},
            "body": "c%d" % i, "created_at": "2026-01-01T00:%02d:%02dZ" % (i // 60, i % 60),
            "author_association": "OWNER"}


class RestFake:
    """Routes REST argv: the issue GET, `/comments` pages by `page=`, and fallback `issue view`."""

    def __init__(self, issue=None, comments=(), fail=None, gql=None, issue_raw=None, pages=None):
        self.issue = issue if issue is not None else _rest_issue(comments=len(comments))
        self.comments, self.fail, self.gql = list(comments), fail, gql
        self.issue_raw, self.pages_raw = issue_raw, pages or {}
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[0] == "issue":
            if isinstance(self.gql, Exception):
                raise self.gql
            return self.gql if self.gql is not None else '{"state": "OPEN", "labels": []}'
        if self.fail is not None:
            raise self.fail
        if args[1].endswith("/comments"):
            page = int([a for a in args if a.startswith("page=")][0][5:])
            if page in self.pages_raw:
                return self.pages_raw[page]
            return json.dumps(self.comments[(page - 1) * 100:page * 100])
        return self.issue_raw if self.issue_raw is not None else json.dumps(self.issue)

    def page_calls(self):
        return [c for c in self.calls if c[0] == "api" and c[1].endswith("/comments")]

    def gql_calls(self):
        return [c for c in self.calls if c[0] == "issue"]

    def rest_calls(self):
        return [c for c in self.calls if c[0] == "api"]


def test_view_issue_fetches_issue_and_bounded_comments():
    g = _mod("gh_api")
    run = RestFake(comments=[_rest_comment(i) for i in range(130)])
    out = g.view_issue(run, 7, repo="o/r", comments="all")
    assert out["number"] == 7 and len(out["comments"]) == 130
    assert run.calls[0] == ["api", "repos/o/r/issues/7", "--method", "GET"]
    assert run.calls[1][:3] == ["api", "repos/o/r/issues/7/comments", "--method"]
    assert "per_page=100" in run.calls[1] and "page=2" in run.calls[2] and len(run.calls) == 3
    _assert_clean(run)


def test_view_issue_tail_reads_only_needed_pages():
    g = _mod("gh_api")
    run = RestFake(comments=[_rest_comment(i) for i in range(150)])
    out = g.view_issue(run, 7, repo="o/r", comments=100)
    assert [c["body"] for c in out["comments"]] == ["c%d" % i for i in range(50, 150)]
    assert len(run.page_calls()) == 2


def test_view_issue_comment_paging():
    g = _mod("gh_api")
    many = [_rest_comment(i) for i in range(150)]
    run = RestFake(comments=many)
    assert len(g.view_issue(run, 7, comments="all")["comments"]) == 150 and len(run.calls) == 3
    run = RestFake(comments=many)
    out = g.view_issue(run, 7, comments=20)["comments"]
    assert [c["body"] for c in out] == ["c%d" % i for i in range(130, 150)]
    assert len(run.page_calls()) <= 2
    run = RestFake(comments=many)
    g.view_issue(run, 7, comments="none")
    assert len(run.calls) == 1
    run = RestFake(comments=[])
    assert g.view_issue(run, 7, comments="all")["comments"] == [] and len(run.calls) == 1
    run = RestFake(comments=[_rest_comment(i) for i in range(30)])
    assert len(g.view_issue(run, 7, comments=50)["comments"]) == 30


@pytest.mark.parametrize("fake", [
    lambda: RestFake(issue_raw="null"),
    lambda: RestFake(issue=_rest_issue(comments=5), pages={1: "null"}),
    lambda: RestFake(issue=_rest_issue(comments=5), pages={1: "{}"}),
    lambda: RestFake(issue=_rest_issue(comments="5")),
], ids=["null-issue", "null-page", "dict-page", "str-count"])
def test_view_issue_rejects_malformed_payloads(fake):
    g = _mod("gh_api")
    with pytest.raises(g.GhApiError) as ei:
        g.view_issue(fake(), 7, comments="all")
    assert ei.value.kind == "other"


# ---------------------------------------------------------------- classification (#895)

RATE = "gh: API rate limit exceeded for user ID 1. (HTTP 429)"
PROXY = "GitHub access is not enabled for this session."


def _err(hint=None, text=None, cause=None):
    exc = RuntimeError(text if text is not None else "gh api repos/o/r/issues/7 --method GET failed: %s" % hint)
    if hint is not None:
        exc.hint = hint
    if cause is not None:
        exc.__cause__ = cause
    return exc


CLASSIFY_ROWS = [
    ("429", _err(RATE), (429, "rate_limit")),
    ("403-rate", _err("gh: You have exceeded a secondary rate limit. (HTTP 403)"), (403, "rate_limit")),
    ("403-permission", _err("gh: Resource not accessible by integration (HTTP 403)"), (403, "permission")),
    ("401", _err("gh: Bad credentials (HTTP 401)"), (401, "auth")),
    ("404", _err("gh: Not Found (HTTP 404)"), (404, "not_found")),
    ("410", _err("gh: This issue was deleted (HTTP 410)"), (410, "other")),
    ("422", _err("gh: Validation Failed (HTTP 422)"), (422, "invalid")),
    ("502", _err("gh: Server Error (HTTP 502)"), (502, "server")),
    ("dial-tcp", _err('Get "https://api.github.com/x": dial tcp: lookup api.github.com: no such host'),
     (None, "transport")),
    ("tls-timeout", _err('Get "https://api.github.com/x": net/http: TLS handshake timeout'), (None, "transport")),
    ("deadline", _err("context deadline exceeded"), (None, "transport")),
    ("eof", _err('Get "https://api.github.com/x":\nEOF'), (None, "transport")),
    ("timeout-expired-cause", _err(text="boom", cause=subprocess.TimeoutExpired(["gh"], 120)),
     (None, "transport")),
    ("proxy", _err(PROXY), (None, "proxy")),
    ("proxy-gql", _err(GQL_TEXT), (None, "proxy")),
    ("proxy-429", _err(PROXY + " (HTTP 429)"), (None, "proxy")),
    ("proxy-502", _err(PROXY + " (HTTP 502)"), (None, "proxy")),
    ("proxy-timed-out", _err(PROXY + " request timed out"), (None, "proxy")),
    ("404-timeout-svc", _err("gh: Not Found (HTTP 404)",
                             text="gh api repos/acme/timeout-svc/issues/7 --method GET failed: gh: Not Found (HTTP 404)"),
     (404, "not_found")),
    ("404-timeout-svc-no-hint", _err(text="gh api repos/acme/timeout-svc/issues/7 failed: gh: Not Found (HTTP 404)"),
     (404, "not_found")),
    ("404-timeout-in-hint", _err("gh: Not Found (HTTP 404)\nupstream request timeout"), (404, "not_found")),
    ("403-rate-limiter-repo", _err("gh: Resource not accessible by integration (HTTP 403)",
                                   text="gh api repos/acme/rate-limiter/issues/7 failed: gh: Resource not "
                                        "accessible by integration (HTTP 403)"), (403, "permission")),
]


@pytest.mark.parametrize("exc,expected", [r[1:] for r in CLASSIFY_ROWS], ids=[r[0] for r in CLASSIFY_ROWS])
def test_classify_table(exc, expected):
    g = _mod("gh_api")
    assert g.classify(exc) == expected


def test_call_fills_status_and_kind():
    g = _mod("gh_api")
    with pytest.raises(g.GhApiError) as ei:
        g.view_pr(Fake(_err(RATE)), 3)
    assert (ei.value.status, ei.value.kind, ei.value.hint) == (429, "rate_limit", RATE)


# ---------------------------------------------------------------- normaliser (#895)

def test_to_gh_shape_fields():
    g = _mod("gh_api")
    issue = _rest_issue(state="closed", state_reason="not_planned", closed_at="2026-01-02T00:00:00Z",
                        comments=[_rest_comment(1)])
    out = g.to_gh_shape(issue, ["state", "stateReason", "author", "closedAt", "body", "labels",
                                "assignees", "comments"])
    assert out == {"state": "CLOSED", "stateReason": "NOT_PLANNED", "author": {"login": "alice"},
                   "closedAt": "2026-01-02T00:00:00Z", "body": "B", "labels": [{"name": "sdlc:goal"}],
                   "assignees": [{"login": "bob"}],
                   "comments": [{"id": "IC_1", "author": {"login": "u1"}, "body": "c1",
                                 "createdAt": "2026-01-01T00:00:01Z", "authorAssociation": "OWNER"}]}
    assert g.to_gh_shape(_rest_issue(), ["state"]) == {"state": "OPEN"}
    assert g.to_gh_shape(_rest_issue(), ["stateReason"]) == {"stateReason": None}
    assert g.to_gh_shape(_rest_issue(body=None), ["body"]) == {"body": ""}
    with pytest.raises(ValueError):
        g.to_gh_shape(_rest_issue(), ["title"])


def test_to_gh_shape_refuses_missing_body():
    """A REST issue with NO body key must raise, never read as "" -- append_to_body would then
    overwrite the real body with only its marker."""
    g = _mod("gh_api")
    issue = _rest_issue()
    del issue["body"]
    with pytest.raises(g.GhApiError):
        g.to_gh_shape(issue, ["body"])


def test_to_gh_shape_bot_author():
    """Issue author `x[bot]`/Bot -> `app/x`, gh's spelling (MEASURED once, #895 step 9). Comment
    authors stay unmapped (plan B3; gh shows a bare `x`, mapping is a follow-up)."""
    g = _mod("gh_api")
    issue = _rest_issue(user={"login": "sigma-bot[bot]", "type": "Bot"},
                        comments=[_rest_comment(1, login="sigma-bot[bot]", utype="Bot")])
    out = g.to_gh_shape(issue, ["author", "comments"])
    assert out["author"] == {"login": "app/sigma-bot"}
    assert out["comments"][0]["author"] == {"login": "sigma-bot[bot]"}
    assert g.to_gh_shape(_rest_issue(), ["author"])["author"] == {"login": "alice"}


# ---------------------------------------------------------------- read_issue policy (#895)

def test_read_issue_rest_first_success():
    g = _mod("gh_api")
    run = RestFake()
    out = g.read_issue(run, 7, ["state", "labels"], "o/r", env={})
    assert out == {"state": "OPEN", "labels": [{"name": "sdlc:goal"}]}
    assert run.gql_calls() == [] and len(run.calls) == 1


TRANSIENT = [("429", RATE), ("403-rate", "gh: You have exceeded a secondary rate limit. (HTTP 403)"),
             ("502", "gh: Server Error (HTTP 502)"), ("dial-tcp", "dial tcp: i/o timeout")]


@pytest.mark.parametrize("hint", [t[1] for t in TRANSIENT], ids=[t[0] for t in TRANSIENT])
def test_read_issue_falls_back_on_transient(hint):
    g = _mod("gh_api")
    run = RestFake(fail=_err(hint), gql='{"state": "OPEN", "labels": [], "extra": 1}')
    out = g.read_issue(run, 7, ["state", "labels"], "o/r", env={})
    assert out == {"state": "OPEN", "labels": []}
    assert run.gql_calls() == [["issue", "view", "7", "--repo", "o/r", "--json", "state,labels"]]


CLIENT = [("401", "gh: Bad credentials (HTTP 401)"),
          ("403-permission", "gh: Resource not accessible by integration (HTTP 403)"),
          ("404", "gh: Not Found (HTTP 404)"), ("422", "gh: Validation Failed (HTTP 422)")]


@pytest.mark.parametrize("hint", [c[1] for c in CLIENT], ids=[c[0] for c in CLIENT])
def test_read_issue_never_falls_back_on_client_errors(hint):
    g = _mod("gh_api")
    run = RestFake(fail=_err(hint))
    with pytest.raises(g.GhApiError):
        g.read_issue(run, 7, ["state"], "o/r", env={})
    assert run.gql_calls() == []


@pytest.mark.parametrize("hint", [PROXY + " (HTTP 429)", PROXY + " (HTTP 502)", PROXY + " request timed out"],
                         ids=["proxy-429", "proxy-502", "proxy-timed-out"])
def test_read_issue_never_falls_back_on_proxy_block(hint):
    g = _mod("gh_api")
    run = RestFake(fail=_err(hint))
    with pytest.raises(g.GhApiError) as ei:
        g.read_issue(run, 7, ["state"], "o/r", env={})
    assert run.gql_calls() == [] and ei.value.kind == "proxy"


@pytest.mark.parametrize("env", [{"CLAUDE_CODE_REMOTE": "true"}, {"SIGMA_GH_GRAPHQL": "off"}],
                         ids=["cloud", "override-off"])
def test_read_issue_skips_fallback_in_cloud_session(env):
    g = _mod("gh_api")
    run = RestFake(fail=_err(RATE))
    with pytest.raises(g.GhApiError) as ei:
        g.read_issue(run, 7, ["state"], "o/r", env=env)
    assert run.gql_calls() == [] and ei.value.kind == "rate_limit" and ei.value.hint == RATE


def test_read_issue_at_most_one_fallback():
    g = _mod("gh_api")
    fb = _err("gh: Server Error (HTTP 503)", text="gh issue view failed")
    run = RestFake(fail=_err("gh: Server Error (HTTP 502)"), gql=fb)
    with pytest.raises(g.GhApiError) as ei:
        g.read_issue(run, 7, ["state"], "o/r", env={})
    assert len(run.rest_calls()) == 1 and len(run.gql_calls()) == 1
    assert ei.value.kind == "server" and ei.value.hint == "gh: Server Error (HTTP 503)"
    assert "502" in str(ei.value) and "503" in str(ei.value)


def test_read_issue_fallback_degrades_malformed_json_like_todays_parsers():
    g = _mod("gh_api")
    run = RestFake(fail=_err(RATE), gql="<html>")
    assert g.read_issue(run, 7, ["body"], None, env={}) == {}


def test_read_issue_comment_limit_reads_the_tail():
    g = _mod("gh_api")
    run = RestFake(comments=[_rest_comment(i) for i in range(150)])
    out = g.read_issue(run, 7, ["comments"], "o/r", env={}, comment_limit=10)
    assert [c["body"] for c in out["comments"]] == ["c%d" % i for i in range(140, 150)]


# ---------------------------------------------------------------- breaker + log (#895)

def _breaker(sdlc):
    return json.loads((sdlc / "state" / "gh-rest-breaker.json").read_text())


def _fail_reads(g, sdlc, times, start=1000.0, env=None, hint="gh: Server Error (HTTP 502)"):
    runs = []
    for i in range(times):
        run = RestFake(fail=_err(hint))
        g.read_issue(run, 7, ["state"], "o/r", env=env or {}, sdlc_dir=sdlc, now=start + i)
        runs.append(run)
    return runs


def test_breaker_opens_after_three_failures(tmp_path):
    g = _mod("gh_api")
    _fail_reads(g, tmp_path, 3)
    b = _breaker(tmp_path)
    assert b["consecutive"] == 3 and b["opened_at"] == 1002.0 and b["last_kind"] == "server"
    run = RestFake()
    assert g.read_issue(run, 7, ["state"], "o/r", env={}, sdlc_dir=tmp_path, now=1003.0) == {"state": "OPEN"}
    assert run.rest_calls() == [] and len(run.gql_calls()) == 1


def test_breaker_half_opens_after_cooldown(tmp_path):
    g = _mod("gh_api")
    _fail_reads(g, tmp_path, 3)
    run = RestFake()
    g.read_issue(run, 7, ["state"], "o/r", env={}, sdlc_dir=tmp_path, now=1002.0 + 301)
    assert len(run.rest_calls()) == 1 and run.gql_calls() == []
    assert _breaker(tmp_path)["consecutive"] == 0
    # the cool-down is the operator's lever; unparseable/negative falls back to the default
    assert g._cooldown({"SIGMA_GH_BREAKER_COOLDOWN": "10"}) == 10
    assert g._cooldown({"SIGMA_GH_BREAKER_COOLDOWN": "x"}) == 300 == g._cooldown({"SIGMA_GH_BREAKER_COOLDOWN": "-1"})


def test_breaker_reopens_after_failed_half_open_probe(tmp_path):
    g = _mod("gh_api")
    _fail_reads(g, tmp_path, 3)
    _fail_reads(g, tmp_path, 1, start=1002.0 + 301)
    assert _breaker(tmp_path)["opened_at"] == 1303.0
    run = RestFake()
    g.read_issue(run, 7, ["state"], "o/r", env={}, sdlc_dir=tmp_path, now=1304.0)
    assert run.rest_calls() == []


@pytest.mark.parametrize("content", ["{not json", "[]", '{"consecutive": "x", "opened_at": "y"}'])
def test_corrupt_breaker_file_is_closed(tmp_path, content):
    g = _mod("gh_api")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "gh-rest-breaker.json").write_text(content)
    run = RestFake()
    assert g.read_issue(run, 7, ["state"], "o/r", env={}, sdlc_dir=tmp_path, now=5.0) == {"state": "OPEN"}
    assert len(run.rest_calls()) == 1


def test_breaker_open_without_fallback_still_tries_rest(tmp_path):
    g = _mod("gh_api")
    _fail_reads(g, tmp_path, 3)
    run = RestFake()
    out = g.read_issue(run, 7, ["state"], "o/r", env={"SIGMA_GH_GRAPHQL": "off"}, sdlc_dir=tmp_path, now=1003.0)
    assert out == {"state": "OPEN"} and len(run.rest_calls()) == 1 and run.gql_calls() == []


def test_no_sdlc_dir_never_writes(tmp_path, monkeypatch):
    g = _mod("gh_api")
    monkeypatch.chdir(tmp_path)
    for _ in range(4):
        g.read_issue(RestFake(fail=_err(RATE)), 7, ["state"], "o/r", env={}, now=1.0)
    assert list(tmp_path.rglob("*")) == []


def test_fallback_log_is_capped_and_secret_free(tmp_path):
    g = _mod("gh_api")
    secret = "gh: rate limit ghp_SECRETTOKEN body=PRIVATE (HTTP 429)"
    _fail_reads(g, tmp_path, g.FALLBACK_LOG_CAP + 5, env={"SIGMA_GH_BREAKER_COOLDOWN": "0"}, hint=secret)
    text = (tmp_path / "state" / "gh-fallback.json").read_text()
    entries = json.loads(text)
    assert len(entries) == g.FALLBACK_LOG_CAP
    assert "SECRET" not in text and "PRIVATE" not in text and "repos/" not in text
    assert set(entries[-1]) == {"ts", "op", "number", "kind", "status", "fell_back", "why"}
    assert entries[-1]["kind"] == "rate_limit" and entries[-1]["status"] == 429 and entries[-1]["fell_back"]


def test_stderr_once_per_breaker_open(tmp_path, capsys):
    g = _mod("gh_api")
    _fail_reads(g, tmp_path, 3)
    first = [l for l in capsys.readouterr().err.splitlines() if l.startswith("sigma: gh REST")]
    assert len(first) == 3 and "breaker open" in first[-1]
    for i in range(5):
        g.read_issue(RestFake(), 7, ["state"], "o/r", env={}, sdlc_dir=tmp_path, now=1003.0 + i)
    assert [l for l in capsys.readouterr().err.splitlines() if l.startswith("sigma: gh REST")] == []


def test_comment_issue_argv():
    g = _mod("gh_api")
    run = Fake('{"id":1}')
    assert g.comment_issue(run, 9, "hello", repo="o/r") == {"id": 1}
    assert run.calls == [["api", "repos/o/r/issues/9/comments", "--method", "POST", "-f", "body=hello"]]


def test_add_labels_argv_and_placeholder_repo():
    g = _mod("gh_api")
    run = Fake("[]")
    g.add_labels(run, 4, ["sdlc:goal", "feature:x"])
    assert run.calls == [["api", "repos/{owner}/{repo}/issues/4/labels", "--method", "POST",
                          "-f", "labels[]=sdlc:goal", "-f", "labels[]=feature:x"]]


def test_remove_label_quotes_name_and_404_is_noop():
    g = _mod("gh_api")
    run = Fake("[]")
    g.remove_label(run, 4, "sdlc:goal", repo="o/r")
    assert run.calls == [["api", "repos/o/r/issues/4/labels/sdlc%3Agoal", "--method", "DELETE"]]
    assert g.remove_label(Fake(RuntimeError("HTTP 404: Label does not exist")), 4, "x") is None
    with pytest.raises(g.GhApiError):
        g.remove_label(Fake(RuntimeError("HTTP 500: server error")), 4, "x")


def test_create_issue_argv_and_pins_feature_label_bypass():
    """R5: create_issue is a plain REST create; it does NOT carry GitHubSource._run's feature-label
    refusal. Pinned here so migration slice 3 must consciously preserve that refusal."""
    g = _mod("gh_api")
    run = Fake('{"number": 10}')
    out = g.create_issue(run, "T", "B", labels=["feature:x", "sdlc:goal"], repo="o/r")
    assert out == {"number": 10}
    assert run.calls == [["api", "repos/o/r/issues", "--method", "POST", "-f", "title=T", "-f", "body=B",
                          "-f", "labels[]=feature:x", "-f", "labels[]=sdlc:goal"]]


def test_close_issue_argv():
    g = _mod("gh_api")
    run = Fake("{}")
    g.close_issue(run, 5, repo="o/r")
    assert run.calls == [["api", "repos/o/r/issues/5", "--method", "PATCH", "-f", "state=closed"]]
    run = Fake("{}")
    g.close_issue(run, 5, repo="o/r", reason="not_planned")
    assert run.calls[0][-2:] == ["-f", "state_reason=not_planned"]


def test_create_view_pr_argv():
    g = _mod("gh_api")
    run = Fake('{"number": 11}')
    assert g.create_pr(run, "T", "B", head="sdlc/1", base="main", repo="o/r") == {"number": 11}
    assert run.calls == [["api", "repos/o/r/pulls", "--method", "POST", "-f", "title=T", "-f", "body=B",
                          "-f", "head=sdlc/1", "-f", "base=main"]]
    run = Fake('{"number": 11, "state": "open"}')
    assert g.view_pr(run, 11, repo="o/r")["state"] == "open"
    assert run.calls == [["api", "repos/o/r/pulls/11", "--method", "GET"]]


def test_merge_pr_argv_sha_guard_and_no_auto():
    g = _mod("gh_api")
    run = Fake('{"merged": true}')
    g.merge_pr(run, 11, repo="o/r")
    assert run.calls == [["api", "repos/o/r/pulls/11/merge", "--method", "PUT", "-f", "merge_method=squash"]]
    run = Fake('{"merged": true}')
    g.merge_pr(run, 11, merge_method="rebase", sha="abc123", repo="o/r")
    assert run.calls[0][-4:] == ["-f", "merge_method=rebase", "-f", "sha=abc123"]
    assert not any("auto" in a for a in run.calls[0])


def test_failures_wrap_without_retry_and_unparseable_json_raises():
    g = _mod("gh_api")
    calls = [
        lambda r: g.comment_issue(r, 1, "x"), lambda r: g.add_labels(r, 1, ["a"]),
        lambda r: g.create_issue(r, "t", "b"), lambda r: g.close_issue(r, 1),
        lambda r: g.create_pr(r, "t", "b", "h", "b"), lambda r: g.view_pr(r, 1),
        lambda r: g.merge_pr(r, 1), lambda r: g.view_issue(r, 1),
    ]
    for call in calls:
        run = Fake(RuntimeError("transport"))
        with pytest.raises(g.GhApiError):
            call(run)
        assert len(run.calls) == 1
        run = Fake("<html>not json")
        with pytest.raises(g.GhApiError):
            call(run)


def test_only_the_probe_mentions_graphql():
    src = (S / "gh_api.py").read_text()
    assert src.count('"graphql"') == 1

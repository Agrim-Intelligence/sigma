"""#697: launch hardening 1/2. Five small fixes from the 2026-10-06 severity review:
(a) issue-comment `sigma:` markers are honoured only from OWNER/MEMBER/COLLABORATOR (#650 item 5),
(b) a negative `loop.py spend` count is refused (#632), (c) `gh pr merge` carries
`--match-head-commit` (#638 item 2), (d) the review-thread read pages past 100 (#638 item 3),
(e) the local webhook POST ignores proxy environment variables (#627 item 1).

Every test here asserts on a value (a set, an argv, a counter), never on an exception type alone, so
each is assertion-red against the unfixed code."""
import http.server
import importlib.util
import json
import pathlib
import subprocess
import sys
import threading


import gqlfake
import test_decompose_check as tdc
import test_work as tw

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


GH_CONFIG = {"discovery": {"source": "github", "github": {"repo": "acme/widget"}}}
DISMISS = "<!-- sigma:dismissed-finding kind=blocked-by ref=7 -->"


def _comment(i, body, association, login="someone"):
    row = {"id": f"c{i}", "author": {"login": login}, "body": body,
           "createdAt": f"2026-01-01T00:00:0{i}Z"}
    if association is not None:
        row["authorAssociation"] = association
    return row


def _comments_run(comments):
    def view(n, fields):
        return {"comments": comments, "labels": []}

    def run(args):
        rest = gqlfake.rest_issue(args, view)  # REST-first read (#895); `issue view` is the fallback
        if rest is not None:
            return rest
        assert args[:2] == ["issue", "view"], args
        return json.dumps(view(args[2], None))
    return run


# ----------------------------------------------------------------------------------- (a) markers


def test_untrusted_dismissed_finding_marker_is_ignored_and_a_trusted_one_is_honoured(capsys):
    bc = _mod("backlog_check")
    stranger = [_comment(1, DISMISS, "NONE", "stranger-fake")]
    raw = bc._fetch_scrubbed_comments(".sdlc", GH_CONFIG, {"ref": "5"}, run=_comments_run(stranger))
    assert bc._dismissed_findings("\n".join(raw)) == set()
    err = capsys.readouterr().err
    assert "stranger-fake" in err and "NONE" in err
    owner = [_comment(1, DISMISS, "OWNER", "the-owner")]
    raw = bc._fetch_scrubbed_comments(".sdlc", GH_CONFIG, {"ref": "5"}, run=_comments_run(owner))
    assert bc._dismissed_findings("\n".join(raw)) == {("blocked-by", "7")}


def test_untrusted_keep_parked_marker_does_not_exempt_and_is_reported_loudly(capsys):
    au = _mod("auto_unpark")
    bc = au.backlog_check
    marker = "<!-- %s -->" % au.KEEP_PARKED_MARKER
    stranger = [_comment(1, "leave it " + marker, "CONTRIBUTOR", "stranger-fake")]
    raw = bc._fetch_scrubbed_comments(".sdlc", GH_CONFIG, {"ref": "5"}, run=_comments_run(stranger))
    assert au._is_exempt(raw) is False
    err = capsys.readouterr().err
    assert "stranger-fake" in err and "CONTRIBUTOR" in err
    for association in ("OWNER", "MEMBER", "COLLABORATOR"):
        trusted = [_comment(1, "leave it " + marker, association)]
        raw = bc._fetch_scrubbed_comments(".sdlc", GH_CONFIG, {"ref": "5"}, run=_comments_run(trusted))
        assert au._is_exempt(raw) is True, association


def test_strict_read_drops_untrusted_marker_comments_and_keeps_plain_ones(capsys):
    sources = _mod("sources")
    comments = [
        _comment(1, "<!-- sigma:decompose-filed #9 -->", "NONE", "stranger-fake"),
        _comment(2, "<!-- sigma:design-filed #9 -->", "OWNER", "the-owner"),
        _comment(3, "an ordinary remark from a stranger", "NONE", "stranger-fake"),
    ]
    source = sources.GitHubSource(GH_CONFIG, run=_comments_run(comments))
    bodies = [c["body"] for c in source.fetch_comments_strict("5")["comments"]]
    assert bodies == ["<!-- sigma:design-filed #9 -->", "an ordinary remark from a stranger"]
    assert "stranger-fake" in capsys.readouterr().err


def test_strict_read_with_a_marker_comment_missing_authorassociation_raises():
    sources = _mod("sources")
    comments = [_comment(1, "<!-- sigma:decompose-filed #9 -->", None)]
    source = sources.GitHubSource(GH_CONFIG, run=_comments_run(comments))
    refused = None
    try:
        source.fetch_comments_strict("5")
    except ValueError as exc:
        refused = str(exc)
    assert refused is not None and "authorAssociation" in refused
    plain = [_comment(1, "no marker in this one", None)]
    source = sources.GitHubSource(GH_CONFIG, run=_comments_run(plain))
    assert [c["body"] for c in source.fetch_comments_strict("5")["comments"]] == ["no marker in this one"]


def test_decompose_check_ignores_a_strangers_decompose_filed_marker(tmp_path):
    lp = _mod("loop")
    sources = lp._load("sources") if hasattr(lp, "_load") else _mod("sources")
    stranger = [_comment(1, "<!-- sigma:decompose-filed #9 -->", "NONE", "stranger-fake")]
    real = sources.GitHubSource(GH_CONFIG, run=_comments_run(stranger))

    class Source(tdc._FakeSource):
        def fetch_comments_strict(self, goal):
            return real.fetch_comments_strict(goal)

    base = tdc._file_cfg(tmp_path)
    cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
    src = Source(title="a huge multi-phase goal", body=tdc._EPIC_BODY, issue_number="901",
                 labels=[{"name": "area:engine"}, {"name": "priority:P0"}])
    result = lp.decompose_check(base, "7", cfg, src)
    assert "already filed" not in result
    assert "decomposition filed as #901" in result


# ------------------------------------------------------------------------------------ (b) spend


def _sdlc_dir(tmp_path):
    d = tmp_path / ".sdlc"
    d.mkdir()
    (d / "config.json").write_text("{}")
    return str(d)


def test_spend_cli_refuses_a_negative_count_and_leaves_run_tokens_alone(tmp_path):
    state = _mod("state")
    d = _sdlc_dir(tmp_path)
    for n in ("900", "-500"):
        proc = subprocess.run([sys.executable, str(S / "loop.py"), "spend", d, n],
                              capture_output=True, text=True)
        if n == "900":
            assert proc.returncode == 0, proc.stderr
        else:
            assert proc.returncode == 2
            assert "negative" in proc.stderr and "-500" in proc.stderr
            assert "Traceback" not in proc.stderr
    assert state.load_cursor(d)["run_tokens"] == 900


def test_add_tokens_rejects_a_negative_count(tmp_path):
    state = _mod("state")
    d = _sdlc_dir(tmp_path)
    state.add_tokens(d, 40)
    refused = None
    try:
        state.add_tokens(d, -10)
    except ValueError as exc:
        refused = str(exc)
    assert refused is not None and "negative" in refused
    assert state.load_cursor(d)["run_tokens"] == 40


# ------------------------------------------------------------------------- (c) match-head-commit


def test_merge_passes_match_head_commit_on_the_direct_merge_and_the_arm(tmp_path):
    work = tw.work
    sha = tw.HEAD_SHA
    d = tw._sdlc(tmp_path)
    goal = tw._started(d)
    tw._evidence(d, goal)
    run = tw._runner(tw._rights() + tw._protected(checks=("ci",), reviews=1)
                     + [("pr view", tw._view())])
    out = work.merge(d, tw.GUARDED, goal, run=run, sleep=tw.NOSLEEP)
    assert out.startswith("PR #7 merged")
    assert f"gh pr merge 7 --squash --match-head-commit {sha}" in run.calls

    d = tw._sdlc(tmp_path / "arm")
    goal = tw._started(d)
    tw._evidence(d, goal)
    run = tw._runner(tw._rights() + tw._protected(checks=("ci", "slow")) + tw._auto_merge_allowed(True)
                     + [("pr view", tw._mixed(("ci", "SUCCESS"), ("slow", "")))])
    out = work.merge(d, tw.GUARDED, goal, run=run, sleep=tw.NOSLEEP)
    assert out.startswith("auto-merge armed on PR #7")
    assert f"gh pr merge 7 --auto --squash --match-head-commit {sha}" in run.calls


# -------------------------------------------------------------------------------- (d) threads


def test_unresolved_threads_pages_past_one_hundred():
    work = tw.work
    pages = {
        None: {"nodes": [{"isResolved": True}] * 100,
               "pageInfo": {"hasNextPage": True, "endCursor": "C1"}},
        "C1": {"nodes": [{"isResolved": False}, {"isResolved": False}, {"isResolved": True}],
               "pageInfo": {"hasNextPage": False, "endCursor": None}},
    }
    seen = []

    def run(cwd, argv):
        line = " ".join(str(a) for a in argv)
        if "repo view" in line:
            return "acme/widget"
        after = next((a[len("a="):] for a in argv if str(a).startswith("a=")), None)
        seen.append(after)
        return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": pages[after]}}}})

    assert work._unresolved_threads({"worktree": "w", "pr": "7"}, run) == 2
    assert seen == [None, "C1"]


# ---------------------------------------------------------------------------------- (e) webhook


def test_local_webhook_post_bypasses_a_configured_proxy(monkeypatch):
    hits = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append((self.path, self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for name in ("http_proxy", "HTTP_PROXY"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{server.server_port}")
        for name in ("no_proxy", "NO_PROXY"):
            monkeypatch.delenv(name, raising=False)
        cn = _mod("channel_notify")
        assert cn._local_webhook_url("http://localhost:9/hook", {}) is True
        cn._post("http://localhost:9/hook", {"issue": "1", "kind": "mention", "goal": "1", "id": "x"})
    finally:
        server.shutdown()
    assert hits == []


def test_remote_webhook_with_the_opt_in_keeps_the_proxy(monkeypatch):
    """Regression guard for (e), not a red test: the proxy bypass is for loopback hosts only, so an
    operator who opted in to a remote webhook keeps the egress proxy they configured."""
    hits = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for name in ("http_proxy", "HTTP_PROXY"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{server.server_port}")
        for name in ("no_proxy", "NO_PROXY"):
            monkeypatch.delenv(name, raising=False)
        cn = _mod("channel_notify")
        cn._post("http://hooks.example.test/hook", {"issue": "1", "kind": "mention", "goal": "1", "id": "x"})
    finally:
        server.shutdown()
    assert len(hits) == 1


def test_retired_brand_marker_from_a_stranger_is_ignored_too(capsys):
    au = _mod("auto_unpark")
    legacy = au.legacy
    old = "<!-- %s:keep-parked -->" % legacy.RETIRED
    assert legacy.has_marker(old, au.KEEP_PARKED_MARKER)
    stranger = [_comment(1, "leave it " + old, "NONE", "stranger-fake")]
    raw = au.backlog_check._fetch_scrubbed_comments(".sdlc", GH_CONFIG, {"ref": "5"},
                                                     run=_comments_run(stranger))
    assert au._is_exempt(raw) is False
    assert "stranger-fake" in capsys.readouterr().err
    owner = [_comment(1, "leave it " + old, "OWNER")]
    raw = au.backlog_check._fetch_scrubbed_comments(".sdlc", GH_CONFIG, {"ref": "5"},
                                                     run=_comments_run(owner))
    assert au._is_exempt(raw) is True


def test_unpark_brief_ignores_a_strangers_dismissed_finding():
    u = _mod("unpark")
    body = "blocked by #7 " + DISMISS
    view = json.dumps({"number": 5, "title": "t", "body": "", "labels": [{"name": "sdlc:parked"}],
                       "state": "OPEN",
                       "comments": [_comment(1, body, "NONE", "stranger-fake")]})

    def issue(n, fields):                       # REST-first read (#895): 5 is the parked goal, 7 its blocker
        return json.loads(view) if int(n) == 5 else {"state": "OPEN", "stateReason": ""}

    def run(args):
        rest = gqlfake.rest_issue(args, issue)
        return rest if rest is not None else ""

    b = u.brief(".sdlc", GH_CONFIG, 5, run=run)
    assert [x["ref"] for x in b["blockers"]] == ["7"]


def test_record_phase_end_rejects_a_negative_budget_amount(tmp_path):
    state = _mod("state")
    d = _sdlc_dir(tmp_path)
    state.add_tokens(d, 40)
    refused = None
    try:
        state.record_phase_end(d, "attempt-1", 1.0, budget_tokens=-25)
    except ValueError as exc:
        refused = str(exc)
    assert refused is not None and "negative" in refused
    assert state.load_cursor(d)["run_tokens"] == 40


def _thread_run(pages, calls):
    def run(cwd, argv):
        line = " ".join(str(a) for a in argv)
        if "repo view" in line:
            return "acme/widget"
        after = next((a[len("a="):] for a in argv if str(a).startswith("a=")), None)
        calls.append(after)
        page = pages[after]
        if isinstance(page, Exception):
            raise page
        return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": page}}}})
    return run


def test_unresolved_threads_keeps_the_count_when_a_later_page_fails_and_blocks_at_the_bound(capsys):
    work = tw.work
    first = {"nodes": [{"isResolved": False}] * 3,
             "pageInfo": {"hasNextPage": True, "endCursor": "C1"}}
    calls = []
    run = _thread_run({None: first, "C1": RuntimeError("gh went away")}, calls)
    assert work._unresolved_threads({"worktree": "w", "pr": "7"}, run) == 3
    endless = {"nodes": [{"isResolved": True}],
               "pageInfo": {"hasNextPage": True, "endCursor": "C"}}
    calls = []
    run = _thread_run({None: endless, "C": endless}, calls)
    assert work._unresolved_threads({"worktree": "w", "pr": "7"}, run) >= 1
    assert "page" in capsys.readouterr().err


def test_the_comment_marker_trust_set_equals_the_pr_comment_trust_set():
    assert _mod("sources").TRUSTED_ASSOCIATIONS == tw.work._TRUSTED_ASSOCIATIONS


def test_merge_parks_instead_of_passing_an_empty_head_commit(tmp_path, monkeypatch):
    """The belt behind gate(): a merge whose vetted head is empty must PARK, never emit
    `--match-head-commit ""`."""
    work = tw.work
    d = tw._sdlc(tmp_path)
    goal = tw._started(d)
    tw._evidence(d, goal)
    run = tw._runner(tw._rights() + tw._protected(checks=("ci",), reviews=1)
                     + [("pr view", tw._view())])
    real_gate = work.gate

    def gate_without_head(*args, **kwargs):
        ok, verdict, data = real_gate(*args, **kwargs)
        return ok, verdict, {k: v for k, v in data.items() if k != "headRefOid"}

    monkeypatch.setattr(work, "gate", gate_without_head)
    out = work.merge(d, tw.GUARDED, goal, run=run, sleep=tw.NOSLEEP)
    assert out.startswith("PARK:") and "headRefOid" in out
    assert not [c for c in run.calls if "pr merge" in c]

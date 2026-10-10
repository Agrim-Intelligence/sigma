"""#398: the read-only repository-settings verifier.

The documented gesture (docs/launch/repo-settings.md) is the `--fixture` command run as a subprocess;
the first two tests run exactly that. No test calls GitHub. The fixtures under
tests/fixtures/repo_settings/ are reconstructions of the documented REST shapes, not live captures.

Controls (each was broken once and seen red, see the PR): make `required-checks` return PASS
unconditionally; make `enforce-admins` or `no-required-reviews` do the same; let `request()` accept
a POST; add an `import urllib` to the module.
"""
import ast
import copy
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "readiness" / "verify_repo_settings.py"
FIXTURES = ROOT / "tests" / "fixtures" / "repo_settings"
REPO = "example-org/example-repo"


def _mod():
    assert SCRIPT.exists(), "tools/readiness/verify_repo_settings.py does not exist"
    spec = importlib.util.spec_from_file_location("verify_repo_settings", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cli(*args):
    assert SCRIPT.exists(), "tools/readiness/verify_repo_settings.py does not exist"
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          timeout=60, env={"PATH": "/usr/bin:/bin", "GH_TOKEN": "invalid"})


def _protected():
    return json.loads((FIXTURES / "protected.json").read_text())


def _unprotected():
    return json.loads((FIXTURES / "unprotected.json").read_text())


def _results(recorded, team="sigma-maintainers"):
    mod = _mod()

    def get(path, repo):
        entry = recorded.get(path.replace("repos/" + repo, "repos/{repo}", 1))
        return (0, None) if entry is None else (entry["status"], entry["body"])
    results, error, _ = mod.judge(get, REPO, team)
    assert error is None, error
    return {name: ok for name, ok, _ in results}


PROT = "repos/{repo}/branches/main/protection"


def test_unprotected_fixture_fails_with_a_fix_for_every_failure():
    run = _cli("OWNER/NAME", "--fixture", str(FIXTURES / "unprotected.json"))
    assert run.returncode == 1, run.stdout + run.stderr
    lines = run.stdout.splitlines()
    failed = [l for l in lines if l.startswith("FAIL ")]
    assert [l.split(":")[0] for l in failed] == [
        "FAIL branch-protection", "FAIL required-checks", "FAIL enforce-admins",
        "FAIL no-force-push-or-deletion", "FAIL auto-merge-allowed", "FAIL secret-scanning",
        "FAIL push-protection", "FAIL private-vulnerability-reporting",
        "FAIL workflow-token-read-only", "FAIL maintainers-team"]
    for index, line in enumerate(lines):
        if line.startswith("FAIL "):
            assert lines[index + 1].startswith("  fix: "), line
    assert [l.split(":")[0] for l in lines if l.startswith("PASS ")] == ["PASS no-required-reviews"]


def test_protected_fixture_passes_every_setting():
    run = _cli("OWNER/NAME", "--fixture", str(FIXTURES / "protected.json"))
    assert run.returncode == 0, run.stdout + run.stderr
    lines = run.stdout.splitlines()
    assert len(lines) == 11 and all(l.startswith("PASS ") for l in lines), run.stdout


def test_each_missing_setting_fails_alone():
    def drop_check(r):
        rsc = r[PROT]["body"]["required_status_checks"]
        rsc["contexts"] = rsc["contexts"][:-1]
        rsc["checks"] = rsc["checks"][:-1]

    def admins_off(r):
        r[PROT]["body"]["enforce_admins"]["enabled"] = False

    def reviews_on(r):
        r[PROT]["body"]["required_pull_request_reviews"] = {"required_approving_review_count": 1}

    def force_push(r):
        r[PROT]["body"]["allow_force_pushes"]["enabled"] = True

    def deletion(r):
        r[PROT]["body"]["allow_deletions"]["enabled"] = True

    def auto_merge(r):
        r["repos/{repo}"]["body"]["allow_auto_merge"] = False

    def scanning(r):
        r["repos/{repo}"]["body"]["security_and_analysis"]["secret_scanning"]["status"] = "disabled"

    def push_protection(r):
        r["repos/{repo}"]["body"]["security_and_analysis"]["secret_scanning_push_protection"]["status"] = "disabled"

    def pvr(r):
        r["repos/{repo}/private-vulnerability-reporting"]["body"]["enabled"] = False

    def token_write(r):
        r["repos/{repo}/actions/permissions/workflow"]["body"]["default_workflow_permissions"] = "write"

    def token_approves(r):
        r["repos/{repo}/actions/permissions/workflow"]["body"]["can_approve_pull_request_reviews"] = True

    def team_rights(r):
        r["repos/{repo}/teams"]["body"][0]["permission"] = "triage"

    def team_absent(r):
        r["repos/{repo}/teams"]["body"] = []

    cases = [(drop_check, "required-checks"), (admins_off, "enforce-admins"),
             (reviews_on, "no-required-reviews"), (force_push, "no-force-push-or-deletion"),
             (deletion, "no-force-push-or-deletion"), (auto_merge, "auto-merge-allowed"),
             (scanning, "secret-scanning"), (push_protection, "push-protection"),
             (pvr, "private-vulnerability-reporting"), (token_write, "workflow-token-read-only"),
             (token_approves, "workflow-token-read-only"), (team_rights, "maintainers-team"),
             (team_absent, "maintainers-team")]
    for mutate, setting in cases:
        recorded = _protected()
        mutate(recorded)
        got = _results(recorded)
        assert [n for n, ok in got.items() if not ok] == [setting], (mutate.__name__, got)


def test_required_reviews_and_a_missing_check_fail():
    recorded = _protected()
    rsc = recorded[PROT]["body"]["required_status_checks"]
    only_checks = {"checks": [{"context": c, "app_id": 15368} for c in
                              ["test", "lint"]]}
    recorded[PROT]["body"]["required_status_checks"] = dict(rsc, contexts=[], **only_checks)
    assert all(_results(recorded).values())          # `checks` alone, plus an extra name, still passes
    recorded[PROT]["body"]["required_status_checks"]["checks"].pop(0)
    assert _results(recorded)["required-checks"] is False
    recorded = _protected()
    recorded[PROT]["body"]["required_pull_request_reviews"] = {"required_approving_review_count": 2}
    assert _results(recorded)["no-required-reviews"] is False
    recorded[PROT]["body"]["required_pull_request_reviews"] = {"required_approving_review_count": 0}
    assert _results(recorded)["no-required-reviews"] is True


def test_planted_post_is_refused_and_nothing_is_sent():
    mod = _mod()
    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        raise AssertionError("a process was started")

    for method in ("POST", "PUT", "PATCH", "DELETE", "get", "HEAD"):
        with pytest.raises(mod.RefusedWrite):
            mod.request(method, "repos/" + REPO, runner)
    assert calls == []
    # the same refusal through the CLI path: a client that tries a write ends in exit 2, no output
    def planted(path, repo):
        return mod.request("POST", path, runner)
    out = io.StringIO()
    assert mod.main([REPO], get=planted, out=out) == 2 and out.getvalue() == ""


def test_every_gh_call_is_a_plain_get():
    mod = _mod()
    argvs = []

    def runner(argv, **kwargs):
        argvs.append(argv)
        body = {"repos/" + REPO: {"default_branch": "main"}}.get(argv[-1], {})
        return subprocess.CompletedProcess(argv, 0, stdout="HTTP/2.0 200 OK\r\nX: y\r\n\r\n" + json.dumps(body))

    mod.main([REPO], get=lambda path, repo: mod.request("GET", path, runner), out=io.StringIO())
    assert len(argvs) == 5
    for argv in argvs:
        assert argv[:5] == ["gh", "api", "--include", "--method", "GET"], argv
        assert len(argv) == 6 and argv[5].startswith("repos/" + REPO), argv
    paths = sorted(a[5][len("repos/" + REPO):] for a in argvs)
    assert paths == sorted(["", "/branches/main/protection", "/private-vulnerability-reporting",
                            "/actions/permissions/workflow", "/teams"])


def test_path_allowlist_refuses_graphql_traversal_and_bad_slugs():
    mod = _mod()
    never = lambda *a, **k: (_ for _ in ()).throw(AssertionError("process started"))
    for path in ("graphql", "repos/a/b/issues", "repos/a/b?per_page=1", "repos/../b", "repos/a/../b",
                 "repos/a/b/branches/../protection", "repos/./b", "user", "/repos/a/b", "repos/a/b/git/refs",
                 "repos/a/b/branches/x?y/protection", "repos/a/b/branches/-x/protection/../../hooks"):
        with pytest.raises(mod.RefusedWrite):
            mod.request("GET", path, never)
    for slug in ("a", "a/b/c", "../b", "a/..", "a b/c", "a/b;ls", "a/b?x=1", "", "./b", "a/."):
        assert mod.main([slug], get=never, out=io.StringIO()) == 2, slug
    # a hostile default_branch from the response is not put into a path
    for branch in ("../../x", "a?b", "-flag", "a/b", "", "a#b", "a b"):
        recorded = _protected()
        recorded["repos/{repo}"]["body"]["default_branch"] = branch

        def get(path, repo, recorded=recorded):
            assert "protection" not in path, path
            entry = recorded.get(path.replace("repos/" + repo, "repos/{repo}", 1))
            return (0, None) if entry is None else (entry["status"], entry["body"])
        results, error, _ = mod.judge(get, REPO)
        assert results == [] and error and "no setting was judged" in error, branch


def test_unreadable_answers_fail_rather_than_pass():
    mod = _mod()
    # 403 and a generic (non-admin) 404 on protection are FAIL with an honest reason, never PASS or exit 2
    for status, body in ((403, {"message": "Forbidden"}), (404, {"message": "Not Found"})):
        recorded = _protected()
        recorded[PROT] = {"status": status, "body": body}
        got = _results(recorded)
        assert got["branch-protection"] is False and got["required-checks"] is False
    recorded = _protected()
    recorded[PROT] = {"status": 404, "body": {"message": "Not Found"}}

    def get(path, repo):
        entry = recorded.get(path.replace("repos/" + repo, "repos/{repo}", 1))
        return entry["status"], entry["body"]
    results, _, _ = mod.judge(get, REPO)
    detail = dict((n, d) for n, _, d in results)["branch-protection"]
    assert "lacks admin rights" in detail
    # missing security_and_analysis (a non-admin token), garbage bodies, an unreadable repository
    recorded = _protected()
    del recorded["repos/{repo}"]["body"]["security_and_analysis"]
    got = _results(recorded)
    assert got["secret-scanning"] is False and got["push-protection"] is False
    # a 200 whose body is not the expected shape is not an answer: exit 2, nothing judged
    for key in ("repos/{repo}/private-vulnerability-reporting", "repos/{repo}/actions/permissions/workflow",
                "repos/{repo}/teams", PROT):
        recorded = _protected()
        recorded[key] = {"status": 200, "body": "garbage"}
        with pytest.raises(Exception) as caught:      # `_results` loads its own copy of the module
            _results(recorded)
        assert type(caught.value).__name__ == "_NoAnswer", key
    results, error, _ = mod.judge(lambda path, repo: (404, {"message": "Not Found"}), REPO)
    assert results == [] and "no setting was judged" in error
    assert mod.main([REPO], get=lambda path, repo: (404, None), out=io.StringIO()) == 2
    # a non-zero gh exit with a parseable status line is still read as that status
    run = lambda argv, **kw: subprocess.CompletedProcess(
        argv, 1, stdout='HTTP/2.0 404 Not Found\r\nA: b\r\n\r\n{"message":"Branch not protected"}')
    assert mod.request("GET", "repos/" + REPO, run) == (404, {"message": "Branch not protected"})
    assert mod.request("GET", "repos/" + REPO, lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="")) == (0, None)


def test_the_module_has_one_subprocess_call_site():
    assert SCRIPT.exists(), "tools/readiness/verify_repo_settings.py does not exist"
    source = SCRIPT.read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"argparse", "json", "re", "subprocess", "sys"}, imported
    banned = {"os.system", "os.popen", "os.exec", "os.spawn", "__import__", "eval", "exec", "importlib"}
    assert not [b for b in banned if b in source]
    refs = [n for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "subprocess"
            and n.attr not in {"PIPE", "DEVNULL", "run", "SubprocessError"}]
    assert refs == []
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name) and n.value.id == "subprocess" and n.attr == "run"]
    # the one default `runner or subprocess.run` inside request(); the only call is `runner(...)` in request()
    assert len(uses) == 1
    request = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "request")
    calls = [n for n in ast.walk(request) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "runner"]
    assert len(calls) == 1
    argv = next(n for n in ast.walk(request) if isinstance(n, ast.Assign) and n.targets[0].id == "argv")
    assert [ast.literal_eval(e) for e in argv.value.elts[:5]] == ["gh", "api", "--include", "--method", "GET"]
    assert isinstance(argv.value.elts[5], ast.Name) and argv.value.elts[5].id == "path"
    for other in tree.body:
        if isinstance(other, ast.FunctionDef) and other.name != "request":
            assert not [n for n in ast.walk(other) if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name) and n.func.id == "runner"], other.name


def test_a_failed_process_or_fixture_exits_2_not_1_and_asks_once():
    mod = _mod()
    out = io.StringIO()
    for raised in (FileNotFoundError("gh"), subprocess.TimeoutExpired("gh", 30), OSError("boom")):
        def runner(*a, raised=raised, **k):
            raise raised
        assert mod.main([REPO], get=lambda p, r: mod.request("GET", p, runner), out=out) == 2
    assert out.getvalue() == ""
    assert _cli("OWNER/NAME", "--fixture", str(FIXTURES / "missing.json")).returncode == 2
    bad = FIXTURES.parent / "repo_settings_not_json.txt"
    bad.write_text("{not json")
    try:
        assert _cli("OWNER/NAME", "--fixture", str(bad)).returncode == 2
    finally:
        bad.unlink()
    asked = []
    recorded = _protected()

    def get(path, repo):
        asked.append(path)
        entry = recorded.get(path.replace("repos/" + repo, "repos/{repo}", 1))
        return entry["status"], entry["body"]
    assert mod.main([REPO], get=get, out=io.StringIO()) == 0
    assert asked.count("repos/" + REPO) == 1


def test_a_trailing_newline_is_not_part_of_a_path_or_slug():
    mod = _mod()
    never = lambda *a, **k: (_ for _ in ()).throw(AssertionError("process started"))
    with pytest.raises(mod.RefusedWrite):
        mod.request("GET", "repos/a/b\n", never)
    assert mod.main(["a/b\n"], get=never, out=io.StringIO()) == 2
    assert mod.main([REPO, "--team", "x\n"], get=never, out=io.StringIO()) == 2


def test_malformed_answers_exit_2_never_1_or_a_traceback():
    mod = _mod()
    good = _protected()
    shapes = []
    for key, value in ((PROT, ["x"]), ("repos/{repo}", "x"), (PROT, "x"),
                       ("repos/{repo}/teams", [["x"]]), ("repos/{repo}/actions/permissions/workflow", [1])):
        recorded = copy.deepcopy(good)
        recorded[key] = value
        shapes.append(recorded)
    recorded = copy.deepcopy(good)
    recorded[PROT]["body"]["required_status_checks"] = ["x"]
    shapes.append(recorded)
    recorded = copy.deepcopy(good)
    recorded[PROT]["body"]["required_status_checks"]["contexts"] = [["x"]]
    shapes.append(recorded)
    shapes.append([])
    for index, recorded in enumerate(shapes):
        path = FIXTURES.parent / ("repo_settings_malformed_%d.txt" % index)
        path.write_text(json.dumps(recorded))
        try:
            run = _cli("OWNER/NAME", "--fixture", str(path))
        finally:
            path.unlink()
        assert run.returncode == 2 and "Traceback" not in run.stderr and run.stdout == "", (index, run.stderr)
    out = io.StringIO()
    assert mod.main([REPO], get=lambda p, r: (_ for _ in ()).throw(TypeError("x")), out=out) == 2
    assert out.getvalue() == ""


def test_no_answer_mid_run_is_exit_2_not_a_fail_with_a_fix():
    mod = _mod()
    recorded = _protected()

    def get(path, repo):
        if path.endswith("/teams"):
            return 0, None
        entry = recorded.get(path.replace("repos/" + repo, "repos/{repo}", 1))
        return entry["status"], entry["body"]
    out = io.StringIO()
    assert mod.main([REPO], get=get, out=out) == 2 and out.getvalue() == ""


def test_only_judgeable_statuses_are_judged_everything_else_is_exit_2():
    mod = _mod()
    for endpoint in ("/branches/main/protection", "/private-vulnerability-reporting",
                     "/actions/permissions/workflow", "/teams"):
        for status in (401, 422, 429, 500, 502, 503, None, "200", True, 200.0, 201, 302):
            recorded = _protected()

            def get(path, repo, status=status):
                if path.endswith(endpoint):
                    return status, {"message": "x"}
                entry = recorded.get(path.replace("repos/" + repo, "repos/{repo}", 1))
                return entry["status"], entry["body"]
            out = io.StringIO()
            assert mod.main([REPO], get=get, out=out) == 2, (endpoint, status)
            assert out.getvalue() == ""
        # 403 and 404 are the token-without-admin answers: judged, reported as FAIL with a reason
        for status in (403, 404):
            def get(path, repo, status=status):
                if path.endswith(endpoint):
                    return status, {"message": "Not Found"}
                entry = _protected().get(path.replace("repos/" + repo, "repos/{repo}", 1))
                return entry["status"], entry["body"]
            out = io.StringIO()
            assert mod.main([REPO], get=get, out=out) == 1, (endpoint, status)


def test_the_default_live_path_runs_one_plain_get_per_endpoint_with_a_timeout(monkeypatch):
    mod = _mod()
    seen = []
    recorded = _protected()

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        path = argv[-1]
        entry = recorded[path.replace("repos/" + REPO, "repos/{repo}", 1)]
        text = "HTTP/2.0 %d X\r\nA: b\r\n\r\n%s" % (entry["status"], json.dumps(entry["body"]))
        return subprocess.CompletedProcess(argv, 0 if entry["status"] == 200 else 1, stdout=text)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    out = io.StringIO()
    assert mod.main([REPO], out=out) == 0
    assert len(seen) == 5
    for argv, kwargs in seen:
        assert argv[:5] == ["gh", "api", "--include", "--method", "GET"] and len(argv) == 6
        assert argv[5].startswith("repos/" + REPO) and kwargs.get("timeout") == 30
        assert kwargs.get("stdin") == subprocess.DEVNULL and kwargs.get("shell") is None


def test_a_different_team_with_maintain_rights_does_not_satisfy_the_team_check():
    recorded = _protected()
    recorded["repos/{repo}/teams"]["body"][0]["slug"] = "someone-else"
    assert _results(recorded)["maintainers-team"] is False
    assert _results(recorded, team="someone-else")["maintainers-team"] is True


def test_code_owner_reviews_block_the_loop_even_with_a_zero_count():
    recorded = _protected()
    recorded[PROT]["body"]["required_pull_request_reviews"] = {
        "required_approving_review_count": 0, "require_code_owner_reviews": True}
    assert _results(recorded)["no-required-reviews"] is False


def test_an_existing_team_with_wrong_rights_is_offered_only_the_grant():
    mod = _mod()
    recorded = _protected()
    recorded["repos/{repo}/teams"]["body"][0]["permission"] = "triage"
    path = FIXTURES.parent / "repo_settings_wrong_rights.txt"
    path.write_text(json.dumps(recorded))
    try:
        run = _cli("OWNER/NAME", "--fixture", str(path))
    finally:
        path.unlink()
    fixes = [l for l in run.stdout.splitlines() if l.startswith("  fix: ")]
    assert run.returncode == 1 and len(fixes) == 1 and "-X PUT orgs/OWNER/teams/" in fixes[0]
    assert len(mod.fix_commands("maintainers-team", "OWNER/NAME", "main", "t")) == 2


def _fixture_cli(recorded, name, *extra, env=None):
    path = FIXTURES.parent / name
    path.write_text(json.dumps(recorded))
    try:
        return subprocess.run([sys.executable, str(SCRIPT), "OWNER/NAME", "--fixture", str(path), *extra],
                              capture_output=True, text=True, timeout=60,
                              env=env or {"PATH": "/usr/bin:/bin", "GH_TOKEN": "invalid"})
    finally:
        path.unlink()


def test_odd_shaped_fields_never_traceback_or_pass():
    recorded = _protected()
    recorded["repos/{repo}"]["body"]["security_and_analysis"]["secret_scanning"]["status"] = {"a": 1}
    run = _fixture_cli(recorded, "repo_settings_odd_status.txt")
    assert run.returncode == 1 and "Traceback" not in run.stderr
    assert "FAIL secret-scanning" in run.stdout
    for value in ("yes", None, 1, {}):
        for key in ("allow_force_pushes", "allow_deletions"):
            recorded = _protected()
            recorded[PROT]["body"][key] = value
            assert _results(recorded)["no-force-push-or-deletion"] is False, (key, value)


def test_output_that_cannot_be_written_exits_2_not_1():
    recorded = _protected()
    recorded["repos/{repo}/teams"]["body"][0]["permission"] = "triag\u00e9"
    env = {"PATH": "/usr/bin:/bin", "GH_TOKEN": "invalid", "PYTHONIOENCODING": "ascii"}
    run = _fixture_cli(recorded, "repo_settings_non_ascii.txt", env=env)
    assert run.returncode == 2 and "Traceback" not in run.stderr and run.stdout == ""
    mod = _mod()

    class Closed(io.StringIO):
        def write(self, text):
            raise BrokenPipeError("closed")
    assert mod.main([REPO], get=lambda p, r: (lambda e: (e["status"], e["body"]))(
        _protected()[p.replace("repos/" + REPO, "repos/{repo}", 1)]), out=Closed()) == 2


def test_an_empty_fixture_argument_does_not_go_live():
    run = subprocess.run([sys.executable, str(SCRIPT), "OWNER/NAME", "--fixture", ""], capture_output=True,
                         text=True, timeout=60, env={"PATH": "/nonexistent", "GH_TOKEN": "invalid"})
    assert run.returncode == 2 and "No such file or directory: ''" in run.stderr, run.stderr   # the fixture path, not `gh`


def test_a_token_without_admin_rights_is_not_told_that_no_review_is_required():
    recorded = _protected()
    recorded[PROT] = {"status": 404, "body": {"message": "Not Found"}}
    got = _results(recorded)
    assert got["no-required-reviews"] is False and got["branch-protection"] is False

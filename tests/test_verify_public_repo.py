"""`tools/verify_public_repo.py`: each check passes and fails over a REST fake (#397, Task 1c).

Every test opens with `mod = _tool()`, which asserts the tool exists, so an absent tool is an
AssertionError red and never an import error. `gh` is reached only through the injected `run`
fake, which records every argv and (after the call) is checked to be an API GET: the argv is
`["gh", "api", <endpoint>]` and carries none of the flags that would make it a write.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "verify_public_repo.py"

# Literal on purpose: the test file, not the tool, is the record of which checks exist and in which order.
CHECKS = ("visibility", "default-branch", "single-commit", "root-commit", "branches", "tags",
          "issues", "pulls", "tree", "ci-run", "ci-legs")

SCHEMA = "sigma.public-tree-report/v1"
REPO = "acme/demo"
BRANCH = "main"
COMMIT = "c0" * 20          # 40 hex
OTHER = "d1" * 20
TREE = "7e" * 20
OTHER_TREE = "5a" * 20
RUN_ID = 77

E_REPO = "repos/%s" % REPO
E_COMMITS = "repos/%s/commits?sha=%s&per_page=2" % (REPO, BRANCH)
E_BRANCHES = "repos/%s/branches?per_page=100" % REPO
E_TAGS = "repos/%s/tags?per_page=100" % REPO
E_ISSUES = "repos/%s/issues?state=all&per_page=1" % REPO
E_PULLS = "repos/%s/pulls?state=all&per_page=1" % REPO
E_GITCOMMIT = "repos/%s/git/commits/%s" % (REPO, COMMIT)
E_RUNS = "repos/%s/actions/runs?head_sha=%s&per_page=100" % (REPO, COMMIT)
E_JOBS = "repos/%s/actions/runs/%d/jobs?per_page=100" % (REPO, RUN_ID)

FORBIDDEN = ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input", "--jq")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_COUNTER = [0]


def _tool():
    assert TOOL.exists(), "tools/verify_public_repo.py is missing"
    _COUNTER[0] += 1
    return _load(TOOL, "verify_public_repo_under_test_%d" % _COUNTER[0])


def _report(**over):
    report = {
        "schema": SCHEMA,
        "verdict": "VERIFIED",
        "finalise": "done",
        "generated_at": "2026-10-02T00:00:00Z",
        "source": {"commit": OTHER, "tree": OTHER_TREE, "root": "src", "plugin_version": "1.0.0"},
        "export": {"out": "out", "files": 3, "bytes": 30, "modes": {"100644": 3},
                   "planned_tree_git": TREE, "planned_tree_python": TREE, "actual_tree_disk": TREE,
                   "export_tree": TREE, "commit": COMMIT,
                   "message": "Initial public snapshot (Sigma 1.0.0)",
                   "author": "sigma-public-snapshot <noreply@users.noreply.github.com>",
                   "date": "1700000000 +0000"},
    }
    report.update(over)
    return report


def _write_report(tmp_path, report=None, name="report.json"):
    path = tmp_path / name
    path.write_text(json.dumps(_report() if report is None else report), encoding="utf-8")
    return path


def _world():
    return {
        E_REPO: {"private": True, "default_branch": BRANCH},
        E_COMMITS: [{"sha": COMMIT, "parents": []}],
        E_BRANCHES: [{"name": BRANCH}],
        E_TAGS: [],
        E_ISSUES: [],
        E_PULLS: [],
        E_GITCOMMIT: {"sha": COMMIT, "tree": {"sha": TREE}},
        E_RUNS: {"workflow_runs": [{"id": RUN_ID, "path": ".github/workflows/ci.yml",
                                    "status": "completed", "conclusion": "success",
                                    "head_sha": COMMIT}]},
        E_JOBS: {"jobs": [{"id": i, "status": "completed", "conclusion": "success"} for i in range(5)]},
    }


class Gh:
    """The injected `run`. Answers a known endpoint from the world, anything else with a failure."""

    def __init__(self, world):
        self.world = world
        self.calls = []
        self.violations = []

    def __call__(self, args, input_text=None, timeout=60):
        args = list(args)
        self.calls.append(args)
        if args[:2] != ["gh", "api"] or len(args) < 3:
            self.violations.append(("not an api call", args))
            return 1, "", "unexpected command"
        if any(a in FORBIDDEN or a.startswith("--method") for a in args[3:]) or input_text is not None:
            self.violations.append(("write-shaped argv", args))
        answer = self.world.get(args[2])
        if answer is None:
            self.violations.append(("endpoint not in the world", args))
            return 1, "", "gh: Not Found (HTTP 404)"
        if isinstance(answer, tuple):
            return answer
        return 0, json.dumps(answer), ""

    @property
    def endpoints(self):
        return [c[2] for c in self.calls if len(c) > 2]


def _run(mod, tmp_path, world, report=None, extra=(), visibility="private", capsys=None):
    gh = Gh(world)
    report_path = _write_report(tmp_path, report)
    argv = ["verify_public_repo.py", "--repo", REPO, "--report", str(report_path),
            "--expect-visibility", visibility] + list(extra)
    rc = mod.main(argv, run=gh)
    out = capsys.readouterr()
    return rc, out.out, out.err, gh


def _lines(stdout):
    """-> ([(status, check, detail)], summary line). Every check line must be `ok|FAIL <check> <detail>`."""
    lines = stdout.splitlines()
    assert lines, "no output"
    parsed = []
    for line in lines[:-1]:
        m = re.match(r"^(ok|FAIL) (\S+) (\S.*)$", line)
        assert m, "malformed check line: %r" % line
        parsed.append((m.group(1), m.group(2), m.group(3)))
    return parsed, lines[-1]


def _mutate(check, world):
    if check == "visibility":
        world[E_REPO]["private"] = False
    elif check == "default-branch":
        world[E_REPO]["default_branch"] = "trunk"
    elif check == "single-commit":
        world[E_COMMITS] = [{"sha": COMMIT, "parents": []}, {"sha": OTHER, "parents": [{"sha": COMMIT}]}]
    elif check == "root-commit":
        world[E_COMMITS] = [{"sha": COMMIT, "parents": [{"sha": OTHER}]}]
    elif check == "branches":
        world[E_BRANCHES] = [{"name": BRANCH}, {"name": "dev"}]
    elif check == "tags":
        world[E_TAGS] = [{"name": "v1"}]
    elif check == "issues":
        world[E_ISSUES] = [{"number": 1}]
    elif check == "pulls":
        world[E_PULLS] = [{"number": 1}]
    elif check == "tree":
        world[E_GITCOMMIT] = {"sha": COMMIT, "tree": {"sha": OTHER_TREE}}
    elif check == "ci-run":
        world[E_RUNS]["workflow_runs"][0]["conclusion"] = "failure"
    elif check == "ci-legs":
        world[E_JOBS]["jobs"] = world[E_JOBS]["jobs"][:4]
    else:
        raise AssertionError("no mutation for %r" % check)
    return world


def test_checks_literal_matches_the_tool():
    mod = _tool()
    assert tuple(mod.CHECKS) == CHECKS


def test_all_checks_pass(tmp_path, capsys):
    mod = _tool()
    rc, out, err, gh = _run(mod, tmp_path, _world(), capsys=capsys)
    assert not gh.violations, gh.violations
    assert rc == 0, (out, err)
    assert err == ""
    parsed, summary = _lines(out)
    assert [c for _, c, _ in parsed] == list(CHECKS)
    assert all(status == "ok" for status, _, _ in parsed), out
    assert summary == "verify_public_repo: 11 ok, 0 failed"


@pytest.mark.parametrize("check", ["visibility", "default-branch", "single-commit", "root-commit",
                                   "branches", "tags", "issues", "pulls", "tree", "ci-run", "ci-legs"])
def test_each_check_fails_alone(check, tmp_path, capsys):
    mod = _tool()
    rc, out, err, gh = _run(mod, tmp_path, _mutate(check, _world()), capsys=capsys)
    assert not gh.violations, gh.violations
    assert rc == 1, (out, err)
    assert "REFUSED" not in err
    parsed, summary = _lines(out)
    assert [c for _, c, _ in parsed] == list(CHECKS)
    failed = [c for status, c, _ in parsed if status == "FAIL"]
    # a failed ci-run leaves nothing to count legs of, so ci-legs fails with it (without a query)
    expected = ["ci-run", "ci-legs"] if check == "ci-run" else [check]
    assert failed == expected, out
    assert summary == "verify_public_repo: %d ok, %d failed" % (11 - len(expected), len(expected))
    if check == "ci-run":
        assert E_JOBS not in gh.endpoints


def test_checks_names_in_this_file_cover_every_mutation():
    mod = _tool()
    assert tuple(mod.CHECKS) == CHECKS
    for check in CHECKS:
        assert _mutate(check, _world()) != _world(), check


def test_check_to_condition_mapping_over_the_pinned_endpoints(tmp_path, capsys):
    """R6: single-commit = exactly one commit AND its sha is export.commit; root-commit = no parents;
    tree and ci-run query export.commit."""
    mod = _tool()
    # one commit, but not the exported one: single-commit fails
    world = _world()
    world[E_COMMITS] = [{"sha": OTHER, "parents": []}]
    rc, out, err, gh = _run(mod, tmp_path, world, capsys=capsys)
    parsed, _ = _lines(out)
    assert rc == 1 and not gh.violations, (out, err, gh.violations)
    assert [c for s, c, _ in parsed if s == "FAIL"] == ["single-commit"], out


def test_all_pass_queries_exactly_the_pinned_endpoints(tmp_path, capsys):
    mod = _tool()
    rc, out, err, gh = _run(mod, tmp_path, _world(), capsys=capsys)
    assert rc == 0, (out, err)
    assert sorted(gh.endpoints) == sorted([E_REPO, E_COMMITS, E_BRANCHES, E_TAGS, E_ISSUES, E_PULLS,
                                           E_GITCOMMIT, E_RUNS, E_JOBS])
    assert len(gh.calls) == len(set(gh.endpoints)), "an endpoint was queried twice"


def test_ci_run_must_be_the_ci_workflow_and_completed(tmp_path, capsys):
    mod = _tool()
    for runs in ([{"id": RUN_ID, "path": ".github/workflows/other.yml", "status": "completed",
                   "conclusion": "success", "head_sha": COMMIT}],
                 [{"id": RUN_ID, "path": ".github/workflows/ci.yml", "status": "in_progress",
                   "conclusion": None, "head_sha": COMMIT}],
                 []):
        world = _world()
        world[E_RUNS] = {"workflow_runs": runs}
        rc, out, err, gh = _run(mod, tmp_path, world, capsys=capsys)
        parsed, _ = _lines(out)
        assert rc == 1 and not gh.violations, (out, err)
        assert [c for s, c, _ in parsed if s == "FAIL"] == ["ci-run", "ci-legs"], out


def test_a_failed_leg_fails_ci_legs_only(tmp_path, capsys):
    mod = _tool()
    world = _world()
    world[E_JOBS]["jobs"][2]["conclusion"] = "failure"
    rc, out, err, gh = _run(mod, tmp_path, world, capsys=capsys)
    parsed, summary = _lines(out)
    assert rc == 1 and not gh.violations, (out, err)
    assert [c for s, c, _ in parsed if s == "FAIL"] == ["ci-legs"], out
    assert summary == "verify_public_repo: 10 ok, 1 failed"


def test_legs_flag_sets_the_expected_job_count(tmp_path, capsys):
    mod = _tool()
    world = _world()
    world[E_JOBS]["jobs"] = world[E_JOBS]["jobs"][:3]
    rc, out, err, gh = _run(mod, tmp_path, world, extra=["--legs", "3"], capsys=capsys)
    assert rc == 0 and not gh.violations, (out, err)
    parsed, summary = _lines(out)
    assert summary == "verify_public_repo: 11 ok, 0 failed"
    # and the default of 5 rejects the same three jobs
    capsys.readouterr()
    rc2, out2, err2, gh2 = _run(mod, tmp_path, copy.deepcopy(world), capsys=capsys)
    parsed2, _ = _lines(out2)
    assert rc2 == 1 and [c for s, c, _ in parsed2 if s == "FAIL"] == ["ci-legs"], out2


def test_expect_visibility_public_accepts_a_public_repository_and_rejects_a_private_one(tmp_path, capsys):
    mod = _tool()
    world = _world()
    world[E_REPO]["private"] = False
    rc, out, err, gh = _run(mod, tmp_path, world, visibility="public", capsys=capsys)
    assert rc == 0 and not gh.violations, (out, err)
    capsys.readouterr()
    rc2, out2, err2, gh2 = _run(mod, tmp_path, _world(), visibility="public", capsys=capsys)
    parsed, _ = _lines(out2)
    assert rc2 == 1 and [c for s, c, _ in parsed if s == "FAIL"] == ["visibility"], out2


def test_absent_commit_404_is_a_tree_failure_not_a_refusal(tmp_path, capsys):
    mod = _tool()
    world = _world()
    world[E_GITCOMMIT] = (1, "", "gh: Not Found (HTTP 404)")
    rc, out, err, gh = _run(mod, tmp_path, world, capsys=capsys)
    assert not gh.violations, gh.violations
    assert rc == 1, (out, err)
    assert "REFUSED" not in err
    parsed, _ = _lines(out)
    assert [c for s, c, _ in parsed if s == "FAIL"] == ["tree"], out
    assert any(line.startswith("FAIL tree commit absent") for line in out.splitlines()), out


@pytest.mark.parametrize("stderr", ["gh: Not Found (HTTP 404)", "gh: Git Repository is empty. (HTTP 409)"])
def test_missing_branch_404_and_empty_repository_409_are_single_commit_failures(stderr, tmp_path, capsys):
    """R6: `commits?sha=<missing>` answers 404 and an EMPTY repository answers 409: FAIL lines on
    single-commit, never a gh-failed refusal."""
    mod = _tool()
    world = _world()
    world[E_COMMITS] = (1, "", stderr)
    rc, out, err, gh = _run(mod, tmp_path, world, capsys=capsys)
    assert rc == 1, (out, err)
    assert "REFUSED" not in err
    parsed, summary = _lines(out)
    failed = [c for s, c, _ in parsed if s == "FAIL"]
    assert "single-commit" in failed, out
    assert [c for _, c, _ in parsed] == list(CHECKS)
    assert summary.startswith("verify_public_repo: ") and summary.endswith(" failed")


@pytest.mark.parametrize("report", [
    {"verdict": "NOT-VERIFIED"},
    {"verdict": "REJECTED"},
    {"finalise": "pending"},
])
def test_report_not_verified_refuses_before_any_gh_call(report, tmp_path, capsys):
    mod = _tool()
    rc, out, err, gh = _run(mod, tmp_path, _world(), report=_report(**report), capsys=capsys)
    assert rc == 2, (out, err)
    assert out == ""
    assert err.startswith("verify_public_repo: REFUSED [report-not-verified]"), err
    assert len(err.strip().splitlines()) == 1
    assert gh.calls == []


def test_report_schema_mismatch_refuses(tmp_path, capsys):
    mod = _tool()
    rc, out, err, gh = _run(mod, tmp_path, _world(), report=_report(schema="sigma.public-tree-report/v0"),
                            capsys=capsys)
    assert rc == 2, (out, err)
    assert out == ""
    assert err.startswith("verify_public_repo: REFUSED [report-schema]"), err
    assert gh.calls == []


def test_gh_failure_refuses_with_empty_stdout(tmp_path, capsys):
    mod = _tool()
    world = _world()
    world[E_REPO] = (1, "", "gh: Server Error (HTTP 500)")
    rc, out, err, gh = _run(mod, tmp_path, world, capsys=capsys)
    assert rc == 2, (out, err)
    assert out == ""
    assert err.startswith("verify_public_repo: REFUSED [gh-failed]"), err
    assert len(err.strip().splitlines()) == 1


def test_bad_slug_refuses_before_any_gh_call(tmp_path, capsys):
    mod = _tool()
    gh = Gh(_world())
    path = _write_report(tmp_path)
    rc = mod.main(["verify_public_repo.py", "--repo", "not a slug", "--report", str(path),
                   "--expect-visibility", "private"], run=gh)
    cap = capsys.readouterr()
    assert rc == 2
    assert cap.out == ""
    assert cap.err.startswith("verify_public_repo: REFUSED [bad-slug]"), cap.err
    assert gh.calls == []


def test_windows_refuses_first(tmp_path, capsys, monkeypatch):
    mod = _tool()
    monkeypatch.setattr(mod, "_platform_is_windows", lambda: True)
    gh = Gh(_world())
    rc = mod.main(["verify_public_repo.py", "--repo", REPO, "--report", str(tmp_path / "none.json"),
                   "--expect-visibility", "private"], run=gh)
    cap = capsys.readouterr()
    assert rc == 2
    assert cap.out == ""
    assert cap.err.startswith("verify_public_repo: REFUSED [windows]"), cap.err
    assert gh.calls == []


def test_calls_are_get_only(tmp_path, capsys):
    mod = _tool()
    rc, out, err, gh = _run(mod, tmp_path, _world(), capsys=capsys)
    assert rc == 0, (out, err)
    assert gh.calls, "no gh call was made"
    assert not gh.violations, gh.violations
    for call in gh.calls:
        assert call[:2] == ["gh", "api"], call
        assert len(call) == 3, call
        assert not any(flag in call for flag in FORBIDDEN), call

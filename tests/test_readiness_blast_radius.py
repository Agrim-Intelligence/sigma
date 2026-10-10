"""Hermetic controls for the blast-radius drill (#351). Hand-written REST event JSON; no network.

The tool is loaded lazily through `tool()` so its absence is an attributable AssertionError.
"""
import copy
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
READINESS = ROOT / "tools" / "readiness"
INVENTORY = ROOT / "docs" / "launch" / "write-surface.json"
REPO = "acme/sigma-drill-9"
MAIN, SEED, MERGE, FEAT = "a" * 40, "b" * 40, "c" * 40, "d" * 40


def _load(name):
    path = READINESS / (name + ".py")
    assert path.is_file(), "%s is not built yet" % path.name
    sys.path.insert(0, str(READINESS))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tool():
    return _load("blast_radius")


def drive_tool():
    return _load("blast_radius_drive")


def baseline(setup_push=True):
    return {"schema": "sigma.blast-radius-baseline/1", "repo": REPO, "default_branch": "main",
            "default_tip": MAIN, "refs": {"main": MAIN}, "pulls": {"3": SEED}, "setup_push": setup_push,
            "event_ids": ["e0"], "issue_event_ids": ["i0"], "timeline_ids": ["t0"],
            "unrelated": {"number": 1, "state": "open", "title": "unrelated", "labels": ["priority:P3"],
                          "comments": 0, "updated_at": "2026-10-04T15:05:08Z",
                          "created_at": "2026-10-04T15:05:07Z", "assignees": [], "milestone": None,
                          "reactions": None}}


def push(i, ref, head, before, **extra):
    payload = {"ref": ref, "head": head, "before": before, "size": 1}
    payload.update(extra)
    return {"id": i, "type": "PushEvent", "created_at": "2026-10-04T16:00:00Z", "payload": payload}


def clean(run):
    """A clean fixture: run `always` merges one PR and deletes its sdlc branch; run `off` leaves it open."""
    pulls = [{"number": 3, "merged_at": "x", "merge_commit_sha": SEED, "base": {"ref": "main"}}]
    events = [push("e1", "refs/heads/sdlc/4", FEAT, ZERO())]
    refs = [{"ref": "refs/heads/main", "object": {"sha": MAIN}}]
    ievents, compare = [], {}
    if run == "always":
        pulls.append({"number": 5, "merged_at": "y", "merge_commit_sha": MERGE, "base": {"ref": "main"},
                      "head": {"ref": "sdlc/4"}})
        events += [push("e2", "refs/heads/main", MERGE, MAIN),
                   {"id": "e3", "type": "PullRequestEvent", "created_at": "2026-10-04T16:01:00Z",
                    "payload": {"action": "merged", "number": 5, "pull_request": {"number": 5,
                                                                                  "base": {"ref": "main"}}}},
                   {"id": "e4", "type": "DeleteEvent", "created_at": "2026-10-04T16:01:01Z",
                    "payload": {"ref_type": "branch", "ref": "sdlc/4"}}]
        ievents = [{"id": "i1", "event": "merged", "commit_id": MERGE, "issue": {"number": 5},
                    "created_at": "2026-10-04T16:01:00Z"},
                   {"id": "i2", "event": "head_ref_deleted", "issue": {"number": 5},
                    "created_at": "2026-10-04T16:01:01Z"}]
        refs[0]["object"]["sha"] = MERGE
        compare = {"%s...%s" % (MAIN, MERGE): {"status": "ahead", "total_commits": 1}}
    else:
        pulls.append({"number": 5, "merged_at": None, "base": {"ref": "main"}})
    return {"repo": {"default_branch": "main"}, "events": events, "issue_events": ievents, "refs": refs,
            "pulls": pulls, "compare": compare, "timeline": [],
            "issue": {"number": 1, "state": "open", "title": "unrelated",
                      "labels": [{"name": "priority:P3"}], "comments": 0,
                      "updated_at": "2026-10-04T15:05:08Z", "created_at": "2026-10-04T15:05:07Z"}}


def ZERO():
    return "0" * 40


MERGE_PUT_REC = {"event": "subprocess.Popen", "program": "gh", "noun": "api", "action": "x", "method": "PUT",
                 "endpoint": "repos/{owner}/{repo}/pulls/5/merge", "repo": "{owner}/{repo}", "args": ["api", "x"]}
MERGE_CLI_REC = {"event": "subprocess.Popen", "program": "gh", "noun": "pr", "action": "merge", "number": "5",
                 "args": ["pr", "merge"]}


def capture(run, **over):
    """The launches of a clean run, as the audit hook records them."""
    rec = [
        {"event": "subprocess.Popen", "program": "git", "verb": "push", "remote": "origin",
         "refspecs": ["main"], "destructive": False, "args": ["push", "-u"]},
        {"event": "subprocess.Popen", "program": "gh", "noun": "issue", "action": "create",
         "args": ["issue", "create"]},
        {"event": "subprocess.Popen", "program": "git", "verb": "push", "remote": "origin",
         "refspecs": ["sdlc/4"], "destructive": False, "args": ["push", "-u"]},
        {"event": "subprocess.Popen", "program": "gh", "noun": "api", "action": "x", "method": "POST",
         "endpoint": "repos/{owner}/{repo}/pulls", "repo": "{owner}/{repo}", "args": ["api", "x"]},
        {"event": "subprocess.Popen", "program": "gh", "noun": "pr", "action": "comment", "number": "5",
         "args": ["pr", "comment"]},
        {"event": "subprocess.Popen", "program": "gh", "noun": "pr", "action": "view", "args": ["pr", "view"]},
    ]
    if run == "always":
        # #895 4b-1: the code-goal merge is the REST PUT now (`gh pr merge` only as the rate-limit fallback)
        rec += [MERGE_PUT_REC,
                {"event": "subprocess.Popen", "program": "gh", "noun": "api", "action": "x", "method": "DELETE",
                 "endpoint": "repos/{owner}/{repo}/git/refs/heads/sdlc/4", "repo": "{owner}/{repo}",
                 "args": ["api", "x"]}]
    return rec


_n = [0]


def run_assert(tmp_path, run, fixture, cap=None, base=None, inventory=INVENTORY):
    mod = tool()
    _n[0] += 1
    tmp_path = tmp_path / ("r%d" % _n[0])
    d = tmp_path / "ev"
    d.mkdir(parents=True)
    for name, data in fixture.items():
        (d / (name + ".json")).write_text(json.dumps(data))
    cap_file, base_file, out = tmp_path / "cap.jsonl", tmp_path / "base.json", tmp_path / "out.json"
    cap_file.write_text("".join(json.dumps(r) + "\n" for r in (cap if cap is not None else capture(run))))
    base_file.write_text(json.dumps(base or baseline()))
    rc = mod.main(["assert", REPO, "--capture", str(cap_file), "--inventory", str(inventory), "--run", run,
                   "--baseline", str(base_file), "--json", str(out), "--events-dir", str(d)])
    return rc, {a["id"]: a for a in json.loads(out.read_text())["assertions"]} if out.exists() else {}


def failed(results):
    return sorted(k for k, v in results.items() if v["ok"] is False)


def test_clean_fixture_passes_every_assertion_for_both_runs(tmp_path):
    for run in ("always", "off"):
        rc, res = run_assert(tmp_path, run, clean(run))
        assert rc == 0 and failed(res) == [], res


def test_a1_counts_exactly_the_merges_the_run_allows(tmp_path):
    (tmp_path / "a").mkdir()
    rc, res = run_assert(tmp_path / "a", "off", clean("always"), cap=capture("off"))
    assert "A1" in failed(res) and rc == 1
    (tmp_path / "b").mkdir()
    rc, res = run_assert(tmp_path / "b", "always", clean("off"))
    assert "A1" in failed(res)


def test_a1_counts_only_prs_outside_the_baseline_so_run2_expects_zero(tmp_path):
    # PR 3 merged in run 1 is in the baseline pulls; run 2 (off) must see zero new merges.
    rc, res = run_assert(tmp_path, "off", clean("off"))
    assert res["A1"]["ok"] is True and "0 new merged" in res["A1"]["detail"]


def test_a1_fails_on_a_default_branch_push_that_is_not_the_merge(tmp_path):
    fx = clean("always")
    fx["events"].append(push("e9", "refs/heads/main", "9" * 40, MERGE))
    rc, res = run_assert(tmp_path, "always", fx)
    assert "A1" in failed(res)


def test_a1_fails_on_a_push_to_a_ref_that_is_neither_default_nor_sdlc(tmp_path):
    fx = clean("off")
    fx["events"].append(push("e9", "refs/heads/feature/x", "9" * 40, ZERO()))
    rc, res = run_assert(tmp_path, "off", fx)
    assert "A1" in failed(res)


def test_a2_fails_on_a_forced_push_flag(tmp_path):
    fx = clean("always")
    fx["events"][1]["payload"]["forced"] = True
    rc, res = run_assert(tmp_path, "always", fx)
    assert "A2" in failed(res)


def test_a2_fails_on_a_diverged_push_that_carries_no_forced_flag(tmp_path):
    fx = clean("always")
    assert "forced" not in fx["events"][1]["payload"]
    fx["compare"]["%s...%s" % (MAIN, MERGE)] = {"status": "diverged", "total_commits": 1}
    rc, res = run_assert(tmp_path, "always", fx)
    assert "A2" in failed(res)
    # the push event's own compare, with the branch itself healthy (so the branch-ancestry check cannot mask it)
    fx = clean("always")
    fx["events"][1]["payload"]["before"] = "e" * 40
    fx["compare"]["%s...%s" % ("e" * 40, MERGE)] = {"status": "diverged"}
    (tmp_path / "z").mkdir()
    rc, res = run_assert(tmp_path / "z", "always", fx)
    assert failed(res) == ["A2"], res
    fx = clean("always")
    fx["compare"] = {}                                   # an unreadable compare fails closed too
    (tmp_path / "x").mkdir()
    rc, res = run_assert(tmp_path / "x", "always", fx)
    assert "A2" in failed(res)


def test_a3_fails_on_a_non_sdlc_branch_delete_and_on_a_tag_delete(tmp_path):
    for n, payload in enumerate(({"ref_type": "branch", "ref": "feature/x"}, {"ref_type": "tag", "ref": "sdlc/4"})):
        fx = clean("always")
        fx["events"].append({"id": "d%d" % n, "type": "DeleteEvent", "created_at": "z", "payload": payload})
        (tmp_path / str(n)).mkdir()
        rc, res = run_assert(tmp_path / str(n), "always", fx)
        assert "A3" in failed(res)


def test_a3_fails_when_a_baseline_branch_is_gone(tmp_path):
    base = baseline()
    base["refs"]["release"] = "7" * 40
    rc, res = run_assert(tmp_path, "off", clean("off"), base=base)
    assert "A3" in failed(res)


def test_a4_fails_on_an_issue_event_and_on_a_comment_event_for_the_unrelated_issue(tmp_path):
    fx = clean("off")
    fx["issue_events"].append({"id": "i9", "event": "labeled", "issue": {"number": 1},
                               "created_at": "2026-10-04T16:00:00Z"})
    rc, res = run_assert(tmp_path, "off", fx)
    assert "A4" in failed(res)
    fx = clean("off")
    fx["events"].append({"id": "e9", "type": "IssueCommentEvent", "created_at": "2026-10-04T16:00:00Z",
                         "payload": {"issue": {"number": 1}}})
    (tmp_path / "x").mkdir()
    rc, res = run_assert(tmp_path / "x", "off", fx)
    assert "A4" in failed(res)
    fx = clean("off")
    fx["timeline"] = [{"event": "cross-referenced", "created_at": "2026-10-04T16:00:00Z"}]
    (tmp_path / "y").mkdir()
    rc, res = run_assert(tmp_path / "y", "off", fx)
    assert "A4" in failed(res)


def test_a4_fails_when_the_unrelated_issue_snapshot_moved(tmp_path):
    fx = clean("off")
    fx["issue"]["updated_at"] = "2026-10-04T16:30:00Z"
    rc, res = run_assert(tmp_path, "off", fx)
    assert "A4" in failed(res)


def test_a4_ignores_the_seed_events_but_not_a_later_one(tmp_path):
    fx = clean("off")
    fx["events"].append({"id": "late", "type": "IssuesEvent", "created_at": "2026-10-04T15:05:07Z",
                         "payload": {"action": "opened", "issue": {"number": 1}}})
    rc, res = run_assert(tmp_path, "off", fx)
    assert res["A4"]["ok"] is True


def test_unsettled_events_exit_3_and_never_pass_a3(tmp_path):
    fx = clean("always")
    fx["issue_events"] = []                 # the lag-free timeline has not shown the merge yet
    rc, res = run_assert(tmp_path, "always", fx)
    assert rc == 3 and res["A3"]["ok"] is None and failed(res) == []


def test_a5_classifies_launches_with_the_write_surface_grammar():
    mod = tool()
    base = baseline()
    label, rules, bad, _ = mod.classify({"program": "git", "verb": "push", "remote": "origin",
                                         "refspecs": ["sdlc/1"], "args": ["push"]}, REPO, base)
    assert (label, rules, bad) == ("git push", {"git-push"}, [])
    label, rules, bad, _ = mod.classify({"program": "git", "verb": "push", "remote": "origin", "destructive": True,
                                         "refspecs": ["sdlc/1"], "args": ["push"]}, REPO, base)
    assert rules == {"git-push", "git-destructive"} and bad == []
    label, rules, bad, _ = mod.classify({"program": "gh", "noun": "pr", "action": "merge", "number": "5", "args": []}, REPO, base)
    assert rules == {"gh-pr"} and bad == []
    label, rules, bad, _ = mod.classify({"program": "gh", "noun": "api", "action": "x", "method": "DELETE",
                                         "endpoint": "repos/{owner}/{repo}/git/refs/heads/sdlc/1",
                                         "repo": "{owner}/{repo}", "args": []}, REPO, base)
    assert rules == {"gh-api-write"} and bad == []
    for read in ({"noun": "pr", "action": "view"}, {"noun": "auth", "action": "token"},
                 {"noun": "api", "action": "x", "method": "GET", "endpoint": "repos/a/b"}):
        assert tool().classify(dict(read, program="gh", args=[]), REPO, base)[1:3] == (set(), [])
    assert tool().classify({"program": "shell", "args": ["shell-form"]}, REPO, base)[3] == "opaque"
    # an api endpoint with a query string and a git push under -C are both read from the structured fields
    assert tool().classify({"program": "gh", "noun": "api", "method": "POST", "endpoint":
                            "repos/%s/pulls?head=acme:sdlc/1" % REPO, "repo": REPO, "args": []}, REPO, base)[2] == []


def test_a5_fails_closed_on_an_unclassified_gh_launch():
    mod = tool()
    for rec in ({"noun": "repo", "action": "delete"}, {"noun": "project", "action": "item-edit"},
                {"noun": "release", "action": "create"}, {"noun": "issue", "action": "delete"}):
        assert mod.classify(dict(rec, program="gh", args=[]), REPO, baseline())[2], rec


def test_a5_verb_allowlist_and_target_checks():
    mod, base = tool(), baseline()
    other = mod.classify({"program": "gh", "noun": "pr", "action": "comment", "repo": "acme/other", "args": []},
                         REPO, base)[2]
    assert other
    unrelated = mod.classify({"program": "gh", "noun": "issue", "action": "comment", "number": "1", "args": []},
                             REPO, base)[2]
    assert unrelated
    evil = mod.classify({"program": "gh", "noun": "pr", "action": "comment", "repo": "acme/evil-" + REPO.split("/")[1],
                         "args": []}, REPO, base)[2]
    assert evil                                           # a suffix match must not be taken for the drill
    patch = mod.classify({"program": "gh", "noun": "api", "method": "PATCH", "endpoint": "repos/{owner}/{repo}/pages",
                          "repo": "{owner}/{repo}", "args": []}, REPO, base)[2]
    assert patch
    assert mod.classify({"program": "gh", "noun": "api", "method": "PATCH", "endpoint": "repos/{owner}/{repo}/issues/1",
                         "number": "1", "repo": "{owner}/{repo}", "args": []}, REPO, base)[2]


def test_a5_catches_a_push_delete_a_colon_refspec_and_a_gh_api_delete():
    mod, base = tool(), baseline()
    for specs in (["sdlc/x:main"], [":main"], ["+main"], ["feature/x"], [], ["HEAD:feature/x"]):
        rec = {"program": "git", "verb": "push", "remote": "origin", "refspecs": specs, "args": ["push"]}
        assert mod.classify(rec, REPO, base)[2], specs
    for specs in (["sdlc/x"], ["HEAD:sdlc/x"], ["main"]):
        rec = {"program": "git", "verb": "push", "remote": "origin", "refspecs": specs, "args": ["push"]}
        assert mod.classify(rec, REPO, base)[2] == [], specs
    assert mod.classify({"program": "git", "verb": "push", "remote": "origin", "refspecs": ["main"],
                         "args": []}, REPO, baseline(setup_push=False))[2]
    assert mod.classify({"program": "git", "verb": "push", "remote": "https://github.com/acme/other.git",
                         "refspecs": ["sdlc/x"], "args": []}, REPO, base)[2]
    assert mod.classify({"program": "gh", "noun": "api", "method": "DELETE", "endpoint":
                         "repos/{owner}/{repo}/git/refs/heads/main", "repo": "{owner}/{repo}", "args": []},
                        REPO, base)[2]


def test_a5_fails_when_the_capture_lacks_the_launches_the_run_implies(tmp_path):
    cap = [r for r in capture("always") if r is not MERGE_PUT_REC]
    rc, res = run_assert(tmp_path, "always", clean("always"), cap=cap)
    assert "A5" in failed(res) and "WITNESS" in res["A5"]["detail"]
    (tmp_path / "x").mkdir()
    rc, res = run_assert(tmp_path / "x", "off", clean("off"), cap=[])      # a hook that saw no children at all
    assert "A5" in failed(res)
    (tmp_path / "y").mkdir()
    rc, res = run_assert(tmp_path / "y", "off", clean("off"), cap=capture("always"))   # a merge in an `off` run
    assert "A5" in failed(res)


def test_a5_the_rest_merge_put_is_a_merge_launch_both_ways(tmp_path):
    """#895 4b-1: a REST `PUT pulls/N/merge` witnesses an `always` run and is a violation in an `off` run,
    exactly like the `gh pr merge` it replaced (which still counts: the CLI fallback)."""
    rc, res = run_assert(tmp_path, "always", clean("always"))
    assert "A5" not in failed(res), res["A5"]["detail"]
    (tmp_path / "x").mkdir()
    off_put = capture("off") + [MERGE_PUT_REC]
    rc, res = run_assert(tmp_path / "x", "off", clean("off"), cap=off_put)
    assert "A5" in failed(res) and "run off but the capture holds a merge" in res["A5"]["detail"]
    (tmp_path / "y").mkdir()
    cli = [MERGE_CLI_REC if r is MERGE_PUT_REC else r for r in capture("always")]
    rc, res = run_assert(tmp_path / "y", "always", clean("always"), cap=cli)
    assert "A5" not in failed(res), res["A5"]["detail"]
    (tmp_path / "z").mkdir()
    get = [dict(r, method="GET", endpoint="repos/{owner}/{repo}/pulls/5") if r is MERGE_PUT_REC else r
           for r in capture("always")]
    rc, res = run_assert(tmp_path / "z", "always", clean("always"), cap=get)
    assert "A5" in failed(res) and "WITNESS" in res["A5"]["detail"]          # a read is not a merge


def _inventory_without(tmp_path, rule):
    data = json.loads(INVENTORY.read_text())
    data["entries"] = [e for e in data["entries"] if e["rule"] != rule]
    path = tmp_path / "inventory-without.json"
    path.write_text(json.dumps(data))
    return path


def test_a5_fixture_control_inventory_without_gh_pr_fails(tmp_path):
    rc, res = run_assert(tmp_path, "always", clean("always"), inventory=_inventory_without(tmp_path, "gh-pr"))
    assert "A5" in failed(res) and "gh-pr" in res["A5"]["detail"]


EVIDENCE = sorted((ROOT / "docs" / "launch" / "evidence").glob("blast-radius-2*.json"))


def test_a5_control_inventory_without_gh_pr_fails_on_the_real_run1_launches(tmp_path):
    assert EVIDENCE, "the committed evidence of the real drill is missing"
    run1 = json.loads(EVIDENCE[-1].read_text())["runs"][0]
    assert run1["run"] == "always" and run1["launch_counts"]
    mod = tool()
    caps = [dict(l, event="subprocess.Popen") for l in run1["launches"]]   # what the real assertion read
    assert caps and len(caps) == sum(run1["launch_counts"].values()) - run1["opaque_shell_launches"]
    base = baseline()
    base.update(run1["baseline_facts"])
    ok, _ = mod.evaluate_capture(caps, INVENTORY, "always", base, run1["repo"])
    assert ok["ok"] is True, ok
    bad, _ = mod.evaluate_capture(caps, _inventory_without(tmp_path, "gh-pr"), "always", base, run1["repo"])
    assert bad["ok"] is False and "gh-pr" in bad["detail"]


def test_documented_assert_gesture_exits_1_on_a_failure_and_0_on_a_clean_run(tmp_path):
    docfile = ROOT / "docs" / "launch" / "blast-radius.md"
    assert docfile.is_file(), "the drill is not documented"
    doc = docfile.read_text()
    gesture = next(l for l in doc.splitlines() if l.strip().startswith("python3 tools/readiness/blast_radius.py assert"))
    assert "--baseline" in gesture and "--events-dir" not in gesture

    def run(fixture, name):
        d = tmp_path / name
        (d / "ev").mkdir(parents=True)
        for k, v in fixture.items():
            (d / "ev" / (k + ".json")).write_text(json.dumps(v))
        (d / "cap.jsonl").write_text("".join(json.dumps(r) + "\n" for r in capture("always")))
        (d / "base.json").write_text(json.dumps(baseline()))
        argv = gesture.strip().split()
        sub = {"OWNER/sigma-drill-NAME": REPO, "LOG": str(d / "cap.jsonl"), "BASELINE": str(d / "base.json"),
               "OUT": str(d / "out.json")}
        argv = [sub.get(a, a) for a in argv]
        argv = [str(ROOT / a) if a.startswith("tools/") else a for a in argv]
        argv = [sys.executable if a == "python3" else a for a in argv]
        return subprocess.run(argv + ["--events-dir", str(d / "ev")], capture_output=True, text=True).returncode

    assert run(clean("always"), "clean") == 0
    bad = clean("always")
    bad["events"][1]["payload"]["forced"] = True
    assert run(bad, "bad") == 1


def test_drive_refuses_a_bad_name_a_public_repo_a_foreign_origin_and_ci_before_any_write(tmp_path):
    drv = drive_tool()
    info = {"full_name": REPO, "private": True, "fork": False, "archived": False}
    drv.validate(REPO, info, {})
    for repo, i, env in (("acme/other", info, {}), ("acme/sigma-drill-9\n", info, {}),
                         (REPO, dict(info, private=False), {}), (REPO, dict(info, fork=True), {}),
                         (REPO, dict(info, archived=True), {}), (REPO, dict(info, full_name="acme/else"), {}),
                         (REPO, info, {"CI": "true"})):
        with pytest.raises(drv.br.Refusal):
            drv.validate(repo, i, env)
    # the CLI refuses before it reads GitHub or creates the workdir
    work = tmp_path / "w"
    rc = drv.main(["drive", "acme/not-a-drill", "--run", "off", "--workdir", str(work),
                   "--baseline-out", str(tmp_path / "b.json")], rest_factory=lambda r: pytest.fail("read"), environ={})
    assert rc == 2 and not work.exists()
    # a workdir that exists is refused too
    class Rest:
        def repo_info(self):
            return info
    work.mkdir()
    rc = drv.main(["drive", REPO, "--run", "off", "--workdir", str(work), "--baseline-out", str(tmp_path / "b.json")],
                  rest_factory=lambda r: Rest(), environ={})
    assert rc == 2


def test_run2_setup_clones_and_never_pushes_a_non_empty_repository():
    drv = drive_tool()
    assert drv.needs_setup("") is True and drv.needs_setup("\n") is True
    assert drv.needs_setup("%s\trefs/heads/main\n" % MAIN) is False
    src = (READINESS / "blast_radius_drive.py").read_text()
    assert "force" not in src.replace("--force-with-lease", "").lower().replace("forced", "")


def test_drive_environment_carries_the_hook_gh_dir_gh_repo_and_no_token_in_evidence(tmp_path, monkeypatch):
    drv = drive_tool()
    oc = drv._oc()
    monkeypatch.setattr(drv.shutil, "which", lambda name: "/opt/x/bin/gh")
    token = "gh" + "p_" + "T" * 20
    env = drv.child_env(oc, tmp_path, tmp_path / "bin", REPO, token,
                        {"SIGMA_EGRESS_LOG": "/l.jsonl", "PYTHONPATH": "/hook"})
    assert env["SIGMA_EGRESS_LOG"] == "/l.jsonl" and env["PYTHONPATH"] == "/hook"
    assert env["GH_REPO"] == REPO and "/opt/x/bin" in env["PATH"].split(":")
    assert env["GIT_CONFIG_KEY_0"].endswith(".helper")
    # the capture hook never records the environment
    hook = (READINESS / "egress" / "sitecustomize.py").read_text()
    assert "environ" not in hook.replace("os.environ.get(\"SIGMA_EGRESS_LOG\")", "")
    # the written drill summary holds step names, return codes and seconds, never output tails
    assert "stdout_tail" not in (READINESS / "blast_radius_drive.py").read_text()


def test_drive_generated_text_never_references_the_unrelated_issue():
    import re
    drv = drive_tool()
    texts = [drv.GOAL_BODY, drv.APPROVE, drv.NO_TESTS] + [f(r) for f in (drv.goal_title, drv.work_file)
                                                       for r in ("always", "off")]
    assert not any(re.search(r"#\d", t) for t in texts)


def test_the_write_surface_scanner_reports_the_drivers_own_write_sites():
    ws = _load("write_surface")
    assert (READINESS / "blast_radius_drive.py").is_file(), "the driver is not built yet"
    found = ws.scan_paths(ROOT, [READINESS / "blast_radius_drive.py"])
    rules = {(f["function"], f["rule"]) for f in found}
    assert ("drive", "gh-issue") in rules and ("drive", "gh-pr") in rules and ("drive", "git-push") in rules


def test_a2_fails_on_a_rewritten_default_branch_even_when_the_feed_has_no_push_event(tmp_path):
    """The real events feed carried no PushEvent at all; the branch itself is the lag-free witness."""
    fx = clean("always")
    fx["events"] = [e for e in fx["events"] if e["type"] != "PushEvent"]
    rc, res = run_assert(tmp_path, "always", fx)
    assert rc == 0 and res["A2"]["ok"] is True
    fx["compare"]["%s...%s" % (MAIN, MERGE)] = {"status": "diverged", "total_commits": 1}
    rc, res = run_assert(tmp_path, "always", fx)
    assert "A2" in failed(res) and "A1" in failed(res)
    fx["compare"]["%s...%s" % (MAIN, MERGE)] = {"status": "ahead", "total_commits": 2}   # an extra commit landed
    rc, res = run_assert(tmp_path, "always", fx)
    assert "A1" in failed(res)


def test_the_documented_closed_and_merged_event_shape_is_also_a_merge(tmp_path):
    fx = clean("always")
    for e in fx["events"]:
        if e["type"] == "PullRequestEvent":
            e["payload"] = {"action": "closed", "pull_request": {"number": 5, "merged": True, "merge_commit_sha": MERGE,
                                                                 "base": {"ref": "main"}}}
    rc, res = run_assert(tmp_path, "always", fx)
    assert rc == 0, res


def _hook():
    spec = importlib.util.spec_from_file_location("egress_hook", READINESS / "egress" / "sitecustomize.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gh_rec(line):
    words = line.split()
    return dict(program="gh", args=words[:2], **_hook()._gh_fields(words))


def test_a5_rejects_a_program_the_flow_may_not_launch():
    mod = tool()
    cap = capture("off") + [{"event": "subprocess.Popen", "program": "curl", "args": ["-X", "DELETE"]}]
    res, _ = mod.evaluate_capture(cap, INVENTORY, "off", baseline(), REPO)
    assert res["ok"] is False and "curl" in res["detail"]


def test_hook_spellings_that_hid_the_method_the_repo_or_the_number_are_parsed():
    mod, base = tool(), baseline()
    for line in ("api -X=DELETE repos/acme/sigma-drill-9/git/refs/heads/main",
                 "api -p issues repos/acme/sigma-drill-9/git/refs/heads/main -X DELETE",
                 "pr merge 7 -Rother/repo", "pr merge 7 --repo=other/repo",
                 "pr merge https://github.com/other/repo/pull/7",
                 "issue comment https://github.com/%s/issues/1 -b x" % REPO, "issue comment #1 -b x",
                 "issue close 01"):
        assert mod.classify(_gh_rec(line), REPO, base)[2], line
    assert mod.classify(_gh_rec("pr merge 7 -R %s" % REPO), REPO, base)[2] == []


def test_a5_target_check_is_host_and_owner_strict():
    mod = tool()
    for target in ("evil.example/acme/sigma-drill-9", "https://evil.example/acme/sigma-drill-9", "other/sigma-drill-9",
                   "x" + REPO, "https://github.com/acme/sigma-drill-9-extra"):
        assert mod._drill_ok(target, REPO) is False, target
    for target in (REPO, REPO.upper(), "https://github.com/%s.git" % REPO, "git@github.com:%s.git" % REPO,
                   "https://x:y@github.com/%s" % REPO, "{owner}/{repo}"):
        assert mod._drill_ok(target, REPO) is True, target


def test_hook_never_records_push_credentials():
    rec = _hook()._git_fields(["push", "-o", "x=y", "https://u:TOKEN@github.com/%s.git" % REPO, "sdlc/x"])
    assert "TOKEN" not in json.dumps(rec) and rec["refspecs"] == ["sdlc/x"]


def test_a2_checks_forced_on_a_push_back_to_a_known_baseline_tip(tmp_path):
    fx = clean("off")
    fx["events"].append(push("e9", "refs/heads/main", MAIN, SEED, forced=True))
    rc, res = run_assert(tmp_path, "off", fx)
    assert "A2" in failed(res)
    fx = clean("off")
    fx["events"].append(push("e9", "refs/heads/main", MAIN, SEED))       # no flag: the compare must speak
    fx["compare"]["%s...%s" % (SEED, MAIN)] = {"status": "behind"}
    rc, res = run_assert(tmp_path, "off", fx)
    assert "A2" in failed(res)


def test_a5_graphql_mutation_and_label_clone_are_not_waved_through():
    mod, base = tool(), baseline()
    # a mutation is the flow's own lifecycle label swap: it needs the inventory rule and is counted, not waved through
    label, rules, bad, note = mod.classify({"program": "gh", "noun": "api", "method": "POST", "endpoint": "graphql",
                                            "graphql": True, "mutation": True, "args": []}, REPO, base)
    assert rules == {"graphql-mutation"} and bad == [] and note == "gql_mut"
    cap = capture("off") + [{"event": "subprocess.Popen", "program": "gh", "noun": "api", "method": "POST",
                             "endpoint": "graphql", "graphql": True, "mutation": True, "args": []}]
    ok, _ = mod.evaluate_capture(cap, INVENTORY, "off", base, REPO)
    assert ok["ok"] is True
    data = json.loads(INVENTORY.read_text())
    data["entries"] = [e for e in data["entries"] if e["rule"] != "graphql-mutation"]
    stripped = pathlib.Path(__import__("tempfile").mkdtemp()) / "inv.json"
    stripped.write_text(json.dumps(data))
    ok, _ = mod.evaluate_capture(cap, stripped, "off", base, REPO)
    assert ok["ok"] is False and "graphql-mutation" in ok["detail"]
    assert mod.classify({"program": "gh", "noun": "label", "action": "clone", "args": []}, REPO, base)[2]


def test_a5_a_destructive_push_to_the_default_branch_fails_even_as_the_only_main_push():
    mod, base = tool(), baseline()
    for flag in ("--force", "--delete", "--force-with-lease"):
        rec = _hook()._git_fields(["push", flag, "origin", "main"])
        assert rec["destructive"] is True
        assert mod.classify(dict(rec, program="git", args=[]), REPO, base)[2], flag


def test_a1_fails_when_the_merged_pr_is_not_from_an_sdlc_branch_or_a_new_branch_is_not_sdlc(tmp_path):
    fx = clean("always")
    fx["events"] = [e for e in fx["events"] if e["type"] != "PushEvent" or e["payload"]["ref"] == "refs/heads/main"]
    fx["pulls"][-1]["head"] = {"ref": "feature/evil"}
    rc, res = run_assert(tmp_path, "always", fx)
    assert "A1" in failed(res)
    fx = clean("off")
    fx["refs"].append({"ref": "refs/heads/feature/x", "object": {"sha": "9" * 40}})
    rc, res = run_assert(tmp_path, "off", fx)
    assert "A1" in failed(res)


def test_a5_git_verbs_are_allowlisted_and_a_remote_cannot_be_redirected():
    mod, base = tool(), baseline()
    hook = _hook()
    for argv in (["-c", "alias.p=push --force", "p", "origin", "main"], ["remote", "set-url", "origin", "https://x/y"],
                 ["-c", "remote.origin.url=https://x/y", "push", "origin", "sdlc/4"], ["reset", "--hard", "HEAD~5"],
                 ["config", "remote.origin.url", "https://x/y"], ["config", "http.extraheader", "x"],
                 ["-c", "url.https://evil/.insteadOf=https://github.com/", "push", "origin", "sdlc/4"],
                 ["-c", "diff.external=evil", "rev-parse", "HEAD"], ["-c", "core.pager=evil", "log"]):
        rec = dict(program="git", args=argv[:2], **hook._git_fields(argv))
        assert mod.classify(rec, REPO, base)[2], argv
    for argv in (["remote", "get-url", "origin"], ["config", "--get", "remote.origin.url"], ["rev-parse", "HEAD"],
                 ["config", "user.name", "x"], ["config", "core.hooksPath", "hooks-dir"],
                 ["-c", "core.hooksPath=/nonexistent", "fetch", "origin"], ["-c", "log.showRoot=false", "rev-parse", "HEAD"], ["-c", "diff.external=", "-c", "core.pager=", "diff"]):
        rec = dict(program="git", args=argv[:2], **hook._git_fields(argv))
        assert mod.classify(rec, REPO, base)[2] == [], argv


def test_a5_api_endpoints_are_matched_by_method_and_shape():
    mod, base = tool(), baseline()
    hook = _hook()
    bad = ("api -X DELETE repos/{owner}/{repo}/git/refs/heads/sdlc/../main",
           "api -X DELETE repos/{owner}/{repo}/git/refs/heads/sdlc/..",
           "api -X PATCH repos/{owner}/{repo}/git/refs/heads/sdlc/4 -F force=true",
           "api -X DELETE repos/{owner}/{repo}/labels/bug", "api -X DELETE repos/{owner}/{repo}/git/refs/heads/main",
           "api -X POST repos/{owner}/{repo}/issues/01/comments -f body=x",
           "api graphql -F query=@q.graphql", "api -X POST issues -f title=x")
    for line in bad:
        rec = _gh_rec(line)
        problems = mod.classify(rec, REPO, base)[2]
        if line.startswith("api graphql"):
            assert rec["mutation"] is True and mod.classify(rec, REPO, base)[1] == {"graphql-mutation"}
            continue                  # needs the inventory rule and is counted; its target is invisible
        elif "issues/01" in line:
            assert rec["number"] == "1"
            problems = mod.classify(dict(rec, repo=REPO), REPO, baseline())[2]
        assert problems, line
    for line in ("api -X DELETE repos/{owner}/{repo}/git/refs/heads/sdlc/4", "api repos/{owner}/{repo}/pulls -X POST",
                 "api -X PATCH repos/{owner}/{repo}/issues/4 -f state=closed",
                 "api -X POST repos/{owner}/{repo}/issues/4/comments -f body=x"):
        assert mod.classify(_gh_rec(line), REPO, base)[2] == [], line
    # a value option must not be mistaken for the issue number
    assert _hook()._gh_fields("issue edit --add-label 2 1".split())["number"] == "1"


def test_a3_does_not_need_the_feed_delete_event_but_reads_the_pr_head_of_every_deleted_branch(tmp_path):
    """MEASURED on the real repo: a real sdlc/ delete produced no DeleteEvent in the repo events feed."""
    fx = clean("always")
    fx["events"] = [e for e in fx["events"] if e["type"] != "DeleteEvent"]
    rc, res = run_assert(tmp_path, "always", fx)
    assert rc == 0 and res["A3"]["ok"] is True
    fx["pulls"][-1]["head"] = {"ref": "feature/evil"}              # the deleted head branch was not sdlc/*
    rc, res = run_assert(tmp_path, "always", fx)
    assert "A3" in failed(res)


def test_a5_fails_closed_on_a_write_whose_number_or_target_cannot_be_read():
    mod, base = tool(), baseline()
    for line in ("issue close -c 7 1", "issue edit https://github.com/other/repo/issues/1#issuecomment-3 --add-label x",
                 "issue close https://github.com/other/repo/issues/1?x=1", "issue comment HTTPS://github.com/other/repo/issues/1 -b x",
                 "issue comment https://example.com/not-github -b x", "pr merge", "pr close"):
        assert mod.classify(_gh_rec(line), REPO, base)[2], line
    assert _hook()._gh_fields("issue close -c 7 1".split())["number"] == "1"
    assert mod.classify(_gh_rec("pr close 5"), REPO, base)[2] == []


def test_a5_flags_wrapper_programs_and_tag_pushes():
    mod = tool()
    for program in ("env", "xargs", "nohup", "ssh", "curl"):
        cap = capture("off") + [{"event": "subprocess.Popen", "program": program, "args": []}]
        res, _ = mod.evaluate_capture(cap, INVENTORY, "off", baseline(), REPO)
        assert res["ok"] is False and program in res["detail"], program
    cap = capture("off") + [{"event": "subprocess.Popen", "program": "make", "args": []}]
    assert mod.evaluate_capture(cap, INVENTORY, "off", baseline(), REPO)[0]["ok"] is True
    rec = dict(program="git", args=[], **_hook()._git_fields(["push", "origin", "--tags", "sdlc/4"]))
    assert rec["tags"] is True and mod.classify(rec, REPO, baseline())[2]


def test_hook_records_every_program_name_but_never_its_arguments(tmp_path):
    fake = tmp_path / "xargs"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    env = dict(__import__("os").environ, PATH=str(tmp_path) + ":" + __import__("os").environ["PATH"])
    log = tmp_path / "l.jsonl"
    hook = READINESS / "egress"
    subprocess.run([sys.executable, "-c", "import subprocess; subprocess.run(['xargs', 'gh', 'repo', 'delete', 'SECRET'])"],
                   env=dict(env, SIGMA_EGRESS_LOG=str(log), PYTHONPATH=str(hook)), check=True)
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert any(r.get("program") == "xargs" and r.get("args") == [] for r in rows) and "SECRET" not in log.read_text()


def test_a3_fails_on_a_deleted_non_sdlc_pr_head_even_before_the_timeline_event_arrives(tmp_path):
    fx = clean("off")
    fx["pulls"].append({"number": 6, "merged_at": None, "state": "closed", "base": {"ref": "main"},
                        "head": {"ref": "feature/x"}})
    rc, res = run_assert(tmp_path, "off", fx)
    assert "A3" in failed(res)

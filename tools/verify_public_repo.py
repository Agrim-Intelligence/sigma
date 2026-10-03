#!/usr/bin/env python3
"""Read-only REST verifier for a pushed public-tree export (#397).

Proves, from GitHub's own answers, that a repository holds exactly the one commit the
builder reported, with its tree, no other branch and no tag, no issues or pull requests, and
CI green on every leg. Only branches and tags are queried: other refs (pull-request heads,
notes, custom namespaces) are not listed by this verifier. Every `gh` call is a GET through `gh api`; nothing is written anywhere.

    python3 tools/verify_public_repo.py --repo OWNER/NAME --report FILE \
        --expect-visibility private|public [--branch main] [--legs N]

Exit 0 = every check ok, 1 = a check failed, 2 = refusal (one stderr line, empty stdout).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from urllib.parse import quote

TOOL = "verify_public_repo"
SCHEMA = "sigma.public-tree-report/v1"
CI_PATH = ".github/workflows/ci.yml"
CHECKS = ("visibility", "default-branch", "single-commit", "root-commit", "branches", "tags",
          "issues", "pulls", "tree", "ci-run", "ci-legs")

_NOT_A_REPO = "fatal: not a git repository (or any of the parent directories)"
_GIT_SCRUB = ("GIT_DIR", "GIT_WORK_TREE", "GIT_CEILING_DIRECTORIES", "GIT_INDEX_FILE",
              "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
              "GIT_NAMESPACE")
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class Refused(Exception):
    def __init__(self, code, detail=""):
        Exception.__init__(self, code)
        self.code, self.detail = code, detail


def _platform_is_windows():
    return os.name == "nt"


def _real_run(args, input_text=None, timeout=60):
    """-> (rc, stdout, stderr). A timeout is rc 124, a binary that cannot be run rc 127. git children
    get `_GIT_SCRUB` removed, `GIT_DISCOVERY_ACROSS_FILESYSTEM=1` and `LC_ALL=C`."""
    args = [str(a) for a in args]
    env = None
    if args and args[0] == "git":
        env = {k: v for k, v in os.environ.items() if k not in _GIT_SCRUB}
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"
        env["LC_ALL"] = "C"
    feed = {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env, **feed)
    except subprocess.TimeoutExpired:
        return 124, "", "%s: timed out after %ss" % (args[0], timeout)
    except OSError:
        return 127, "", "%s: cannot be run (is it installed?)" % args[0]
    return proc.returncode, proc.stdout, proc.stderr


# ------------------------------------------------------------------------------ the report

def _load_report(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError) as exc:
        raise Refused("report-unreadable", "%s: %s" % (path, type(exc).__name__))
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise Refused("report-schema", "expected schema %s" % SCHEMA)
    if report.get("verdict") != "VERIFIED" or report.get("finalise") != "done":
        raise Refused("report-not-verified", "the report is not a finalised VERIFIED build")
    export = report.get("export")
    if not isinstance(export, dict):
        raise Refused("report-schema", "no export object")
    commit, tree = export.get("commit"), export.get("export_tree")
    if not (isinstance(commit, str) and _SHA_RE.match(commit)
            and isinstance(tree, str) and _SHA_RE.match(tree)):
        raise Refused("report-schema", "export.commit and export.export_tree must be 40 hex")
    return commit, tree


# ------------------------------------------------------------------------------ gh reads

class Gh:
    """GET-only reads. Every call is one `["gh", "api", <endpoint>]` through the injected `run`."""

    def __init__(self, run):
        self.run = run

    def get(self, endpoint, soft=()):
        """-> (data, None) on success; (None, stderr) when rc != 0 and a `soft` marker is in it;
        any other failure refuses `gh-failed`."""
        rc, out, err = self.run(["gh", "api", endpoint])
        if rc != 0:
            if any(marker in (err or "") for marker in soft):
                return None, err
            first = next((l for l in (err or "").splitlines() if l.strip()), "")
            raise Refused("gh-failed", "%s: gh exit %d%s" % (endpoint.split("?")[0], rc,
                                                              (": " + first[:200]) if first else ""))
        try:
            return json.loads(out), None
        except ValueError:
            raise Refused("gh-failed", "%s: unreadable JSON" % endpoint.split("?")[0])


def _verify(gh, repo, branch, legs, expect, commit, tree):
    r = "repos/%s" % repo
    b = quote(branch, safe="")
    results = {}

    def put(check, ok, detail):
        results[check] = (ok, detail)

    info, _ = gh.get(r)
    info = info if isinstance(info, dict) else {}
    private = info.get("private")
    if isinstance(private, bool):
        visibility = "private" if private else "public"
        put("visibility", visibility == expect, "repository is %s, expected %s" % (visibility, expect))
    else:                       # no boolean `private` field says nothing: never default to public
        put("visibility", False, "repos/%s answered no boolean private field, expected %s" % (repo, expect))
    default = info.get("default_branch")
    put("default-branch", default == branch, "default branch is %s, expected %s" % (default, branch))

    commits, err = gh.get("%s/commits?sha=%s&per_page=2" % (r, b), soft=("404", "409"))
    if commits is None:
        why = "branch %s absent or repository empty" % branch
        put("single-commit", False, why)
        put("root-commit", False, "no commit to inspect")
    else:
        commits = commits if isinstance(commits, list) else []
        shas = [c.get("sha") for c in commits if isinstance(c, dict)]
        put("single-commit", len(shas) == 1 and shas[0] == commit,
            "%d commit(s) listed, first %s, expected exactly %s" % (len(shas), shas[0] if shas else "none", commit))
        first = commits[0] if commits and isinstance(commits[0], dict) else {}
        parents = first.get("parents")
        put("root-commit", isinstance(parents, list) and parents == [],
            "first commit has %s parent(s)" % (len(parents) if isinstance(parents, list) else "unknown"))

    for check, endpoint, noun in (("branches", "%s/branches?per_page=100", "branch"),
                                  ("tags", "%s/tags?per_page=100", "tag"),
                                  ("issues", "%s/issues?state=all&per_page=1", "issue"),
                                  ("pulls", "%s/pulls?state=all&per_page=1", "pull request")):
        data, _ = gh.get(endpoint % r)
        count = len(data) if isinstance(data, list) else -1
        want = 1 if check == "branches" else 0
        put(check, count == want, "%d %s(s) listed, expected %d" % (count, noun, want))

    data, err = gh.get("%s/git/commits/%s" % (r, commit), soft=("404",))
    if data is None:
        put("tree", False, "commit absent from the repository")
    else:
        got = ((data.get("tree") or {}).get("sha") if isinstance(data, dict) else None)
        put("tree", got == tree, "tree is %s, expected %s" % (got, tree))

    runs, _ = gh.get("%s/actions/runs?head_sha=%s&per_page=100" % (r, commit))
    listed = runs.get("workflow_runs") if isinstance(runs, dict) else None
    listed = listed if isinstance(listed, list) else []
    ci = [x for x in listed if isinstance(x, dict) and x.get("path") == CI_PATH]
    good = [x for x in ci if x.get("status") == "completed" and x.get("conclusion") == "success"]
    if good:
        put("ci-run", True, "%s completed with success" % CI_PATH)
    else:
        put("ci-run", False, "no completed successful run of %s for the commit (%d run(s) of it)" % (CI_PATH, len(ci)))

    if not good:
        put("ci-legs", False, "not queried: no successful ci run")
    else:
        jobs, _ = gh.get("%s/actions/runs/%s/jobs?per_page=100" % (r, good[0].get("id")))
        listed = jobs.get("jobs") if isinstance(jobs, dict) else None
        listed = listed if isinstance(listed, list) else []
        bad = [j for j in listed if not (isinstance(j, dict) and j.get("status") == "completed"
                                         and j.get("conclusion") == "success")]
        put("ci-legs", len(listed) == legs and not bad,
            "%d job(s) listed, expected %d, %d not successful" % (len(listed), legs, len(bad)))
    return results


# ------------------------------------------------------------------------------ main

def _parser():
    p = argparse.ArgumentParser(prog="verify_public_repo.py", description=__doc__.split("\n")[0])
    p.add_argument("--repo", required=True, help="OWNER/NAME of the pushed export")
    p.add_argument("--report", required=True, help="the builder's finalised VERIFIED report (.json)")
    p.add_argument("--expect-visibility", required=True, choices=("private", "public"),
                   help="visibility the repository must have: private or public")
    p.add_argument("--branch", default="main", help="the branch holding the export (default main)")
    p.add_argument("--legs", type=int, default=5, help="expected CI jobs (default 5, the ci.yml matrix)")
    return p


def main(argv, run=None):
    args = _parser().parse_args(argv[1:])
    run = run or _real_run
    try:
        if _platform_is_windows():
            raise Refused("windows", "this tool is POSIX only")
        if not _REPO_RE.match(args.repo):
            raise Refused("bad-slug", "--repo must be OWNER/NAME")
        commit, tree = _load_report(args.report)
        results = _verify(Gh(run), args.repo, args.branch, args.legs, args.expect_visibility, commit, tree)
    except Refused as exc:
        print("%s: REFUSED [%s] %s" % (TOOL, exc.code, exc.detail), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the contract: an unexpected failure is [internal]
        print("%s: REFUSED [internal] %s" % (TOOL, type(exc).__name__), file=sys.stderr)
        return 2
    failed = 0
    lines = []
    for check in CHECKS:
        ok, detail = results[check]
        failed += 0 if ok else 1
        lines.append("%s %s %s" % ("ok" if ok else "FAIL", check, detail))
    lines.append("%s: %d ok, %d failed" % (TOOL, len(CHECKS) - failed, failed))
    print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

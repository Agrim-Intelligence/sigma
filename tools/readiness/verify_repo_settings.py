#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read-only check of a GitHub repository's launch settings (#398). It never changes a setting.

USAGE: verify_repo_settings.py OWNER/NAME [--team SLUG] [--fixture FILE]
EXIT: 0 = every setting PASS; 1 = at least one FAIL (on answers it could judge); 2 = anything else:
bad arguments, a refused request, a failed `gh` call, an unusable answer, or a repository that could not
be read (nothing was judged). `--help` also exits 0.

Every call goes through `request()`, which refuses any method but GET and any path that is not one of
five exact endpoint shapes, and is the only place a process is started (`gh api --include --method GET`).
`--fixture FILE` reads recorded responses from disk instead and starts nothing. The fix commands it
prints are copied into docs/launch/repo-settings.md (a test keeps the two equal); this script never
runs them, and the owner runs them.
"""
import argparse
import json
import re
import subprocess
import sys

REQUIRED_CHECKS = ("test",)  # the PR/merge subset job; the five-leg `full` job is nightly, not required
DEFAULT_TEAM = "sigma-maintainers"
TEAM_RIGHTS = ("maintain", "admin")
SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_PART = r"[A-Za-z0-9_.-]+"
ALLOWED_PATHS = tuple(re.compile(pattern) for pattern in (
    rf"repos/{_PART}/{_PART}",
    rf"repos/{_PART}/{_PART}/branches/{_PART}/protection",
    rf"repos/{_PART}/{_PART}/private-vulnerability-reporting",
    rf"repos/{_PART}/{_PART}/actions/permissions/workflow",
    rf"repos/{_PART}/{_PART}/teams",
))  # matched with fullmatch: a trailing newline is not part of any path


class _NoAnswer(Exception):
    """GitHub (or the fixture) gave no answer at all to one read: nothing can be judged."""


class RefusedWrite(Exception):
    """A request that is not a plain GET on an allowed path. Nothing was sent."""


def request(method, path, runner=None):
    """Return (status, body) for one GET. The single choke point for everything this script sends."""
    if method != "GET":
        raise RefusedWrite("refused: %s %s (this tool sends GET requests only)" % (method, path))
    if {".", ".."} & set(path.split("/")) or not any(p.fullmatch(path) for p in ALLOWED_PATHS):
        raise RefusedWrite("refused: GET %s is not one of the endpoints this tool reads" % path)
    argv = ["gh", "api", "--include", "--method", "GET", path]
    runner = runner or subprocess.run
    done = runner(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                  text=True, timeout=30, check=False)
    return _parse_http(done.stdout or "")


def _parse_http(text):
    split = re.search(r"\r?\n\r?\n", text)
    head, body = (text[:split.start()], text[split.end():]) if split else (text, "")
    match = re.match(r"HTTP/\S+\s+(\d{3})", head)
    status = int(match.group(1)) if match else 0
    try:
        return status, json.loads(body) if body.strip() else None
    except ValueError:
        return status, None


def fixture_client(path_to_json):
    """A client that answers from a recorded file. Keys use `{repo}` where the slug goes."""
    with open(path_to_json, encoding="utf-8") as handle:
        recorded = json.load(handle)

    def get(path, repo):
        key = path.replace("repos/" + repo, "repos/{repo}", 1)
        entry = recorded.get(key)
        if entry is None:
            return 0, None
        return entry["status"], entry["body"]
    return get


def live_client(runner=None):
    return lambda path, repo: request("GET", path, runner)


def _fix_protection(repo, branch):
    body = {"required_status_checks": {"strict": False, "contexts": list(REQUIRED_CHECKS)},
            "enforce_admins": True, "required_pull_request_reviews": None, "restrictions": None,
            "allow_force_pushes": False, "allow_deletions": False}
    return ["echo '%s' | gh api -X PUT repos/%s/branches/%s/protection --input -"
            % (json.dumps(body, separators=(",", ":")), repo, branch)]


def fix_commands(setting, repo, branch, team, team_exists=False):
    """The command(s) that fix one setting, exactly as docs/launch/repo-settings.md prints them.
    A team that already exists needs only the grant, so `team_exists` drops the create line."""
    org = repo.split("/")[0]
    if setting in {"branch-protection", "required-checks", "enforce-admins", "no-required-reviews",
                   "no-force-push-or-deletion"}:
        return _fix_protection(repo, branch)
    return {
        "auto-merge-allowed": ["gh api -X PATCH repos/%s -F allow_auto_merge=true" % repo],
        "secret-scanning": ["echo '{\"security_and_analysis\":{\"secret_scanning\":{\"status\":\"enabled\"}}}'"
                            " | gh api -X PATCH repos/%s --input -" % repo],
        "push-protection": ["echo '{\"security_and_analysis\":{\"secret_scanning_push_protection\":"
                            "{\"status\":\"enabled\"}}}' | gh api -X PATCH repos/%s --input -" % repo],
        "private-vulnerability-reporting": ["gh api -X PUT repos/%s/private-vulnerability-reporting" % repo],
        "workflow-token-read-only": ["gh api -X PUT repos/%s/actions/permissions/workflow"
                                     " -f default_workflow_permissions=read"
                                     " -F can_approve_pull_request_reviews=false" % repo],
        "maintainers-team": ([] if team_exists else
                             ["gh api -X POST orgs/%s/teams -f name=%s -f privacy=closed" % (org, team)])
        + ["gh api -X PUT orgs/%s/teams/%s/repos/%s -f permission=maintain" % (org, team, repo)],
    }[setting]


def _enabled(block):
    return isinstance(block, dict) and block.get("enabled") is True


def _disabled(block):
    """True only for `{"enabled": false}`; a missing or odd-shaped block is not proof it is off."""
    return isinstance(block, dict) and block.get("enabled") is False


def _status(body, key):
    block = (body.get("security_and_analysis") or {}).get(key) if isinstance(body, dict) else None
    return block.get("status") if isinstance(block, dict) else None


def judge(get, repo, team=DEFAULT_TEAM):
    """Return (results, error, branch). A result is (setting, ok, detail); error is set when nothing can be judged."""
    status, info = get("repos/" + repo, repo)
    if status != 200 or not isinstance(info, dict) or not info.get("default_branch"):
        return [], "cannot read repository %s (HTTP %s); no setting was judged" % (repo, status), None
    branch = info["default_branch"]
    if not BRANCH.fullmatch(str(branch)):
        return [], "default branch name %r is not one this tool reads; no setting was judged" % (branch,), None
    results = []

    def add(setting, ok, detail):
        results.append((setting, bool(ok), str(detail)))

    def answered(path, shape):
        """(status, body) when GitHub gave a judgeable answer, else raise _NoAnswer (exit 2).

        Judgeable: 200 with a body of the expected shape, or 404 or 403 (a token without admin
        rights reads as 404 or 403 on these endpoints; that is reported as FAIL with the reason).
        Anything else, a status that is not an integer, or a 200 of the wrong shape, is not an answer."""
        status, body = get(path, repo)
        if isinstance(status, bool) or not isinstance(status, int):
            raise _NoAnswer("no answer from GitHub for %s; no setting was judged" % path)
        if status == 200 and isinstance(body, shape) or status in (403, 404):
            return status, body
        raise _NoAnswer("unusable answer from GitHub for %s (HTTP %s); no setting was judged" % (path, status))

    pstatus, prot = answered("repos/%s/branches/%s/protection" % (repo, branch), dict)
    if pstatus == 200 and isinstance(prot, dict):
        add("branch-protection", True, "`%s` is protected" % branch)
        rsc = prot.get("required_status_checks") or {}
        names = set(rsc.get("contexts") or []) | {c.get("context") for c in rsc.get("checks") or []
                                                  if isinstance(c, dict)}
        missing = [n for n in REQUIRED_CHECKS if n not in names]
        add("required-checks", not missing,
            "all %d required" % len(REQUIRED_CHECKS) if not missing else "missing: " + ", ".join(missing))
        add("enforce-admins", _enabled(prot.get("enforce_admins")),
            "applies to administrators" if _enabled(prot.get("enforce_admins"))
            else "administrators can bypass the required checks")
        reviews = prot.get("required_pull_request_reviews")
        count = (reviews.get("required_approving_review_count") or 0) if isinstance(reviews, dict) else 0
        owners = isinstance(reviews, dict) and reviews.get("require_code_owner_reviews") is True
        none = not isinstance(reviews, dict) or (count == 0 and not owners)
        add("no-required-reviews", none,
            "no review required" if none
            else "a review is required (approvals: %s, code owners: %s); the loop cannot supply one"
            % (count, owners))
        off = _disabled(prot.get("allow_force_pushes")) and _disabled(prot.get("allow_deletions"))
        add("no-force-push-or-deletion", off,
            "neither allowed" if off else "force push or deletion is allowed, or not reported as off")
    elif pstatus == 404:
        unprotected = isinstance(prot, dict) and prot.get("message") == "Branch not protected"
        add("branch-protection", False, "`%s` is not protected" % branch if unprotected
            else "`%s` is not protected, or this token lacks admin rights (HTTP 404)" % branch)
        add("required-checks", False, "no protection, so no required checks")
        add("enforce-admins", False, "no protection")
        add("no-required-reviews", unprotected,
            "no protection, so no review is required" if unprotected
            else "cannot tell whether a review is required (no admin rights?)")
        add("no-force-push-or-deletion", False, "no protection, so force push and deletion are allowed")
    else:
        for setting in ("branch-protection", "required-checks", "enforce-admins", "no-required-reviews",
                        "no-force-push-or-deletion"):
            add(setting, False, "could not read branch protection (HTTP %s)" % pstatus)

    add("auto-merge-allowed", info.get("allow_auto_merge") is True,
        "enabled" if info.get("allow_auto_merge") is True else "disabled; the loop cannot arm a merge")
    for setting, key in (("secret-scanning", "secret_scanning"),
                         ("push-protection", "secret_scanning_push_protection")):
        value = _status(info, key)
        add(setting, value == "enabled",
            "enabled" if value == "enabled" else (value or "not visible (an admin token is needed)"))
    vstatus, vbody = answered("repos/%s/private-vulnerability-reporting" % repo, dict)
    on = vstatus == 200 and isinstance(vbody, dict) and vbody.get("enabled") is True
    add("private-vulnerability-reporting", on,
        "enabled" if on else ("disabled" if vstatus == 200 and isinstance(vbody, dict)
                              else "unreadable (HTTP %s; an admin token is needed)" % vstatus))
    wstatus, wbody = answered("repos/%s/actions/permissions/workflow" % repo, dict)
    read_only = (wstatus == 200 and isinstance(wbody, dict)
                 and wbody.get("default_workflow_permissions") == "read"
                 and wbody.get("can_approve_pull_request_reviews") is False)
    add("workflow-token-read-only", read_only,
        "read, cannot approve pull requests" if read_only else (
            "token is %s, can approve pull requests: %s" % (wbody.get("default_workflow_permissions"),
                                                            wbody.get("can_approve_pull_request_reviews"))
            if wstatus == 200 and isinstance(wbody, dict) else "unreadable (HTTP %s)" % wstatus))
    tstatus, teams = answered("repos/%s/teams" % repo, list)
    right = [t for t in teams if isinstance(t, dict) and t.get("slug") == team] \
        if tstatus == 200 and isinstance(teams, list) else []
    good = bool(right) and right[0].get("permission") in TEAM_RIGHTS
    add("maintainers-team", good,
        "%s has %s" % (team, right[0].get("permission")) if good
        else ("team %s is on the repository with %s, not maintain" % (team, right[0].get("permission"))
              if right else "team %s is not on the repository (first 30 teams read)" % team))
    return results, None, branch


def render(results, repo, branch, team):
    lines = []
    for setting, ok, detail in results:
        lines.append("%s %s: %s" % ("PASS" if ok else "FAIL", setting, detail))
        if not ok:
            lines.extend("  fix: " + cmd for cmd in fix_commands(
                setting, repo, branch, team, team_exists=detail.startswith("team %s is on the" % team)))
    return lines


def main(argv=None, get=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Read-only check of a repository's launch settings.")
    parser.add_argument("repo", help="OWNER/NAME of the repository to read")
    parser.add_argument("--team", default=DEFAULT_TEAM, help="maintainers team slug (default %(default)s)")
    parser.add_argument("--fixture", help="read recorded responses from this JSON file; no GitHub call is made")
    args = parser.parse_args(argv)
    if not SLUG.fullmatch(args.repo) or {".", ".."} & set(args.repo.split("/")):
        print("refused: %r is not OWNER/NAME" % args.repo, file=sys.stderr)
        return 2
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.team):
        print("refused: %r is not a team slug" % args.team, file=sys.stderr)
        return 2
    try:
        if get is None:
            get = fixture_client(args.fixture) if args.fixture is not None else live_client()
        results, error, branch = judge(get, args.repo, args.team)
    except (RefusedWrite, _NoAnswer) as exc:
        print(exc, file=sys.stderr)
        return 2
    except Exception as exc:                  # noqa: BLE001 - any failure to judge is exit 2, never 1 or a traceback
        print("could not judge: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    if error:
        print(error, file=sys.stderr)
        return 2
    try:
        text = "".join(line + "\n" for line in render(results, args.repo, branch, args.team))
        out.write(text)
        out.flush()
    except (OSError, UnicodeError, ValueError) as exc:
        print("could not write the result: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    return 1 if any(not ok for _, ok, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())

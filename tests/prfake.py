"""Shared REST fakes for the PR reads `gh_api.view_pr_gh` makes (#895 slice 4a-1). A shim, not a test
file (mirror of `gqlfake.py`); imported as a plain sibling module.

The seven migrated PR read sites no longer issue `gh pr view <n> --json <fields>`; they GET
`repos/{owner}/{repo}/pulls/<n>` (plus `issues/<n>/comments` pages for the review-gate marker scan)
and convert. A REST argv carries no `--json`, so ONE pull body answers EVERY field a caller asks for:
`rest_pull` builds that body from gh-shaped inputs, as a SUPERSET that also satisfies the readers
that were already REST (`pr_landing_state`'s `merged`/`merged_at`, the merge receipt's `node_id`,
`merge_commit_sha`, `head.ref`, `base.repo.node_id`).

`pull_get(n)` is the substring `_runner`-style handlers key on. It names the `--method GET` the
gh_api helper always passes, which `pr_landing_state`'s bare `gh api .../pulls/<n>` does not, so a
test can answer the two readers differently when it needs to (and with ONE body when it does not).
"""
import json
import re

ABSENT = object()            # pass as a value to DROP that REST key (absent, not null)
HEAD = "0" * 40              # matches test_work.HEAD_SHA, the default both sides report
BASE_REPO = "acme/app"


def pull_get(n=7):
    """The substring identifying gh_api's REST read of PR `n` (`api repos/{owner}/{repo}/pulls/<n> --method GET`)."""
    return "pulls/%s --method GET" % n


def comments_get(n=7):
    """The substring identifying gh_api's REST comment-page read for PR `n`."""
    return "issues/%s/comments --method GET" % n


def is_pr_get(argv, n=7):
    """True when `argv` (a list, or the space-joined line) is gh_api's REST read of PR `n`."""
    line = argv if isinstance(argv, str) else " ".join(str(a) for a in argv)
    return pull_get(n) in line


def check_runs_get(sha=HEAD):
    """The substring identifying gh_api's REST check-runs read for commit `sha` (#895 4a-2)."""
    return "commits/%s/check-runs --method GET" % sha


def status_get(sha=HEAD):
    """The substring identifying gh_api's REST combined-status read for commit `sha` (#895 4a-2)."""
    return "commits/%s/status --method GET" % sha


_MERGEABLE = {"MERGEABLE": True, "CONFLICTING": False, "UNKNOWN": None}


def rest_pull(number=7, state="OPEN", autoMergeRequest=None, isCrossRepository=False, headRefOid=HEAD,
              headRefName="sdlc/7", title="", body="", author="sigma", mergedAt=None, closedAt=None,
              mergeable="MERGEABLE", mergeStateStatus="CLEAN", **extra):
    """A REST pull body from gh-shaped inputs, as a JSON string. `state` OPEN / CLOSED / MERGED map to
    REST `state` + `merged` + `merged_at`; any other value is passed through lowercased (a malformed
    state the converter must refuse). `isCrossRepository=None` models a deleted fork (`head.repo`
    null). `author=None` omits `user`. `mergeable` MERGEABLE / CONFLICTING / UNKNOWN map to REST
    true / false / null (any other value passes through raw) and `mergeStateStatus` is lower-cased into
    `mergeable_state` (#895 4a-2); the defaults make every body a valid CLEAN gate read too, so ONE body
    serves merge_rights and the gate. Any value may be `ABSENT` to drop its key; `extra` overrides
    or adds raw REST keys."""
    s = str(state).upper()
    merged = s == "MERGED"
    rest_state = {"OPEN": "open", "CLOSED": "closed", "MERGED": "closed"}.get(s, str(state).lower())
    if isCrossRepository is None:
        head_repo = None
    else:
        head_repo = {"full_name": "fork/app" if isCrossRepository else BASE_REPO, "node_id": "R_head"}
    d = {
        "number": number, "node_id": "PR_%s" % number, "title": title, "body": body,
        "state": rest_state, "merged": merged,
        "merged_at": (mergedAt or "2026-01-02T00:00:00Z") if merged else mergedAt,
        "closed_at": closedAt if closedAt is not None else ("2026-01-02T00:00:00Z" if s != "OPEN" else None),
        "created_at": "2026-01-01T00:00:00Z",
        "merge_commit_sha": headRefOid if merged else None,
        "auto_merge": autoMergeRequest,
        "user": {"login": author, "type": "User"} if author is not None else None,
        "head": {"sha": headRefOid, "ref": headRefName, "repo": head_repo},
        "base": {"ref": "main", "repo": {"full_name": BASE_REPO, "node_id": "R_1"}},
        "mergeable": _MERGEABLE.get(mergeable, mergeable),
        "mergeable_state": (mergeStateStatus.lower() if isinstance(mergeStateStatus, str)
                            else mergeStateStatus),
    }
    d.update(extra)
    for k in [k for k, v in d.items() if v is ABSENT]:
        del d[k]
    if d.get("head") is not None and isinstance(d["head"], dict):
        for k in [k for k, v in d["head"].items() if v is ABSENT]:
            del d["head"][k]
    return json.dumps(d)


def rest_comments(rows):
    """gh-shaped comment rows (`{"body", "author": {"login"}, "authorAssociation"?}`) -> one REST
    issue-comments page (JSON string). A row without `authorAssociation` yields no
    `author_association` key, so the reader's "no association" park can be exercised."""
    out = []
    for i, r in enumerate(rows):
        c = {"id": 3000 + i, "node_id": "IC_%d" % i, "body": r.get("body") or "",
             "user": {"login": (r.get("author") or {}).get("login")},
             "created_at": "2026-01-01T00:00:%02dZ" % (i % 60)}
        if "authorAssociation" in r:
            c["author_association"] = r["authorAssociation"]
        out.append(c)
    return json.dumps(out)


def rest_check_runs(pairs, total=None):
    """gh-shaped `(name, conclusion)` pairs -> one REST check-runs page (JSON string). Conclusion "" means
    still running: `in_progress` with a null conclusion; anything else is `completed` with that
    conclusion lower-cased. `total` overrides `total_count` (a truncated page)."""
    runs = []
    for i, (name, conclusion) in enumerate(pairs):
        runs.append({"id": 900 + i, "name": name,
                     "status": "completed" if conclusion else "in_progress",
                     "conclusion": conclusion.lower() if conclusion else None,
                     "details_url": "https://github.com/acme/app/runs/%d" % (900 + i)})
    return json.dumps({"total_count": len(runs) if total is None else total, "check_runs": runs})


def rest_statuses(rows, total=None):
    """gh-shaped `(context, state)` pairs -> one REST combined-status page (JSON string). The combined
    `state` is set to "pending" on purpose: the reader must never use it."""
    statuses = [{"id": 800 + i, "context": c, "state": st.lower(), "target_url": "https://ci.example/%d" % i}
                for i, (c, st) in enumerate(rows)]
    return json.dumps({"state": "pending", "total_count": len(statuses) if total is None else total,
                       "statuses": statuses})


def gate_handlers(pull, checks=(), statuses=(), head=HEAD):
    """The three `_runner` handlers one settled gate round reads, in order: the pull, then the check runs
    and statuses for `head` (which must be the pull's own head.sha)."""
    return [(pull_get(7), pull), (check_runs_get(head), rest_check_runs(checks)),
            (status_get(head), rest_statuses(statuses))]


# ---- #895 4a-2 PR B: the review list and the design-PR list/detail/files reads ----------------------------

def reviews_get(n=7):
    """The substring identifying gh_api's REST review-page read for PR `n`. It does NOT contain
    `pull_get(n)` (`pulls/7 --method GET`), so a `_runner` list may hold both."""
    return "pulls/%s/reviews --method GET" % n


def rest_reviews(rows):
    """Reviews -> ONE REST review page (JSON string), oldest first. A row is `(login, STATE)` or a dict of
    raw REST keys overriding the defaults; `login=None` models a deleted account (`user` null). Ids and
    `submitted_at` ascend with the row index, so list order IS chronological order. `ABSENT` drops a key."""
    out = []
    for i, r in enumerate(rows):
        login, state = r if isinstance(r, tuple) else (None, None)
        d = {"id": 5000 + i, "node_id": "PRR_%d" % i, "state": state,
             "user": {"login": login, "type": "User"} if login is not None else None,
             "submitted_at": "2026-01-01T00:%02d:00Z" % (i % 60)}
        if isinstance(r, dict):
            d.update(r)
        out.append(d)
    for d in out:
        for k in [k for k, v in d.items() if v is ABSENT]:
            del d[k]
    return json.dumps(out)


def gh_reviews(rows):
    """The SAME history as `rest_reviews(rows)` (`(login, STATE)` tuples) in `gh pr view --json reviews`
    shape, list order = review order: the fallback half of the parity test."""
    return json.dumps({"reviews": [
        {"author": {"login": login} if login is not None else None, "state": state,
         "submittedAt": "2026-01-01T00:%02d:00Z" % (i % 60)} for i, (login, state) in enumerate(rows)]})


PULLS_LIST = "pulls?head="


def files_get(n=42):
    """The substring identifying gh_api's REST file-page read for PR `n`."""
    return "pulls/%s/files --method GET" % n


def rest_files(paths):
    return json.dumps([{"filename": p, "status": "added", "sha": "f" * 40} for p in paths])


DESIGN_PATHS = (".sdlc/design/9.md", ".sdlc/design/9-in-brief.md")


def design_pull(number=42, branch="sdlc/9", paths=DESIGN_PATHS, changed=None, cross=False, **kw):
    """A REST `pulls/<n>` body for a design PR: `rest_pull` plus `html_url` and `changed_files` (list rows
    carry null for both mergeable and changed_files, MEASURED, so only this detail read answers them)."""
    return json.loads(rest_pull(number=number, headRefName=branch, isCrossRepository=cross,
                                html_url="https://x/%d" % number,
                                changed_files=len(paths) if changed is None else changed, **kw))


def design_handlers(prs, branch="sdlc/9"):
    """`_runner` handlers for the REST design-PR read: the list (`prs` = dicts for `design_pull`, each with
    an optional `"files"` raw JSON string override), then per PR the detail and the file page. Every
    fixture derives from one `design_pull` with one deviation."""
    rows = [{"number": p["number"], "mergeable": None, "mergeable_state": None, "changed_files": None}
            for p in prs]
    h = [(PULLS_LIST, json.dumps(rows))]
    for p in prs:
        kw = {k: v for k, v in p.items() if k != "files"}
        pull = design_pull(branch=branch, **kw)
        h.append((pull_get(p["number"]), json.dumps(pull)))
        h.append((files_get(p["number"]), p.get("files", rest_files(kw.get("paths", DESIGN_PATHS)))))
    return h


# ---- #895 slice 4b-1: the code-goal merge is a REST PUT ----------------------------------------------------

def merge_put(n=7):
    """The substring identifying gh_api's REST merge of PR `n` (`api repos/{owner}/{repo}/pulls/<n>/merge
    --method PUT ...`). `pr_landing_state`'s bare `gh api repos/{owner}/{repo}/pulls/<n>` read is a
    SUBSTRING of that line, so a `_runner` list must put this handler FIRST."""
    return "pulls/%s/merge --method PUT" % n


def landing_read(n=7):
    """The substring of `pr_landing_state`'s ONE REST read of PR `n`. It also matches the PUT and the
    gh_api GET, so rely on handler ORDER (`merge_put` and `pull_get` first)."""
    return "api repos/{owner}/{repo}/pulls/%s" % n


def rest_merged(sha="m" * 40):
    """The documented 2xx body of `PUT pulls/N/merge` (DERIVED from GitHub docs, UNMEASURED live)."""
    return json.dumps({"merged": True, "sha": sha, "message": "Pull Request successfully merged"})


_MERGE_RE = re.compile(r"pulls/\d+/merge")


def merge_calls(calls):
    """Every call that could MERGE a PR: the gh CLI `pr merge` (arm, fallback, design merge) AND the
    REST `pulls/N/merge` PUT. A "never merged" guard keyed on `pr merge` alone is vacuous since 4b-1."""
    return [c for c in calls if "pr merge" in c or _MERGE_RE.search(c)]

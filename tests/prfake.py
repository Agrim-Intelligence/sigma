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


def rest_pull(number=7, state="OPEN", autoMergeRequest=None, isCrossRepository=False, headRefOid=HEAD,
              headRefName="sdlc/7", title="", body="", author="sigma", mergedAt=None, closedAt=None,
              **extra):
    """A REST pull body from gh-shaped inputs, as a JSON string. `state` OPEN / CLOSED / MERGED map to
    REST `state` + `merged` + `merged_at`; any other value is passed through lowercased (a malformed
    state the converter must refuse). `isCrossRepository=None` models a deleted fork (`head.repo`
    null). `author=None` omits `user`. Any value may be `ABSENT` to drop its key; `extra` overrides
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

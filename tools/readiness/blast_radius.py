#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Blast-radius assertions for the autonomy drill (readiness dimension D10, goal 351). READ-ONLY.

The drill (`blast_radius_drive.py`) runs Sigma's scripted flow against a throwaway repository; this
tool reads what that repository and the egress capture say happened and evaluates five assertions:

  A1  the default branch moved only through the goal's merged PR (run `always`: exactly one new merge;
      run `off`: none), and no push went anywhere but the default branch or `sdlc/*`
  A2  no force-push to the default branch (the REST events payload carries no `forced` flag, so every
      push is also checked by REST compare: `before` must be an ancestor of `head`)
  A3  only `sdlc/*` branches were deleted
  A4  the seeded unrelated issue was not touched (events, timeline, snapshot)
  A5  every `gh`/`git` launch in the capture is a known read or a write the inventory has a rule for,
      within an allowlist of verbs, aimed at the drill repository only

    blast_radius.py baseline REPO --unrelated N --setup-push yes|no --json OUT
    blast_radius.py assert   REPO --capture LOG --inventory INV --run always|off --baseline FILE --json OUT
                             [--events-dir DIR]

`baseline` is taken AFTER setup and before the drill starts; `assert` counts only what is not in it.
`--events-dir` loads recorded REST responses (`repo.json events.json issue_events.json timeline.json
refs.json issue.json pulls.json compare.json`) instead of reading GitHub; tests use only that.

EXIT (assert): 0 = every assertion passed and the events are settled; 1 = an assertion failed;
2 = bad arguments (`blast_radius.py: REFUSED [code]: ...` on stderr, nothing on stdout);
3 = nothing failed but some history had not reached GitHub's events feed yet (UNVERIFIED, never a pass).

WHAT THIS CANNOT SEE. The capture is the audit hook's: Python `subprocess` launches only. A shell-form
launch is counted (`opaque_shell_launches`), `gh api graphql` cannot be told reads from mutations
(`graphql_launches`), and a launch aimed only through `GH_REPO` or the working directory has no explicit
target in the capture (`placeholder_launches`). A1-A4 are measured from GitHub, not from the capture, so
they do not depend on it. A5 compares rule categories plus verbs and targets, not code sites.
Stdlib only; no write except the caller-supplied `--json`.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import write_surface  # noqa: E402  (the scanner's own verb sets: one grammar, not two)

ZERO = "0" * 40
REPO_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}/sigma-drill-[A-Za-z0-9._-]*")
PLACEHOLDER = "{owner}/{repo}"
#: What the scripted flow may write, verb by verb. Narrower than the inventory, which only says a rule exists.
ALLOWED_GH = {("issue", "create"), ("issue", "comment"), ("issue", "close"), ("issue", "edit"),
              ("pr", "create"), ("pr", "comment"), ("pr", "merge"), ("pr", "edit"), ("pr", "ready"), ("pr", "close"),
              ("label", "create")}
READS = {"issue": {"view", "list", "status"}, "pr": {"view", "list", "status", "checks", "diff", "checkout"},
         "project": {"view", "list", "field-list", "item-list"}, "label": {"list"}, "repo": {"view", "clone"},
         "auth": {"status", "token"}}
#: (method, endpoint tail) the flow may write over `gh api`. Ref deletes only for one sdlc/ branch name.
API_TAIL = [("POST", re.compile(r"issues(/\d+/comments)?|pulls|labels")),
            ("PATCH", re.compile(r"issues/\d+|pulls/\d+")),
            ("PUT", re.compile(r"pulls/\d+/merge")),
            ("POST", re.compile(r"issues/\d+/labels")),
            ("DELETE", re.compile(r"issues/\d+/labels/[^/]+|git/refs/heads/sdlc/[^/]+"))]
MERGE_PUT_RE = re.compile(r"/pulls/\d+/merge$")
GIT_VERBS = {"add", "branch", "checkout", "clone", "commit", "config", "diff", "fetch", "init", "log", "ls-files",
             "ls-remote", "merge-base", "push", "remote", "rev-list", "rev-parse", "show", "status", "switch",
             "symbolic-ref", "worktree", "stash", "restore", "cat-file", "for-each-ref", "describe", "reflog"}
#: git config keys that can redirect a remote, alias a verb, or run something else. Init's one-time unset of a
#: stale `core.hooksPath` (#614) and the `-c core.hooksPath=<empty dir>` hardening are not among them.
#: Programs that can exec gh or git without the hook recording a second launch, and that the flow has no use for.
WRAPPERS = {"env", "xargs", "nohup", "sudo", "doas", "su", "ssh", "timeout", "nice", "time", "command", "exec",
            "busybox", "stdbuf", "setsid", "script", "osascript", "open", "node", "perl", "ruby", "curl", "wget",
            "pip", "pip3", "codex", "claude", "cursor-agent"}
#: Keys that run a program: allowed only when the override is EMPTY, which disables them (Sigma's own hardening).
EXEC_KEYS = {"core.askpass", "gpg.program", "diff.external", "core.pager", "core.sshcommand", "core.gitproxy",
             "core.fsmonitor"}
CONFIG_REDIRECT = ("core.askpass", "gpg.program", "diff.external", "remote.", "url.", "alias.", "core.sshcommand", "core.gitproxy", "credential.", "http.",
                   "include", "core.fsmonitor", "core.pager", "core.editor.", "pager.", "protocol.", "ssh.")


class Refusal(Exception):
    def __init__(self, code, detail):
        self.code = code
        super().__init__("REFUSED [%s]: %s" % (code, detail))


# ------------------------------------------------------------------ reading GitHub (REST only)

def _gh_json(path, paginate=False):
    cmd = ["gh", "api"] + (["--paginate"] if paginate else []) + [path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        raise Refusal("rest-read", "gh api %s: %s" % (path.split("?")[0], proc.stderr.strip()[:200]))
    text, out, i, dec = proc.stdout.strip(), [], 0, json.JSONDecoder()
    while i < len(text):                      # --paginate prints one JSON document per page
        doc, i = dec.raw_decode(text, i)
        out.extend(doc) if isinstance(doc, list) else out.append(doc)
        while i < len(text) and text[i].isspace():
            i += 1
    return out[0] if len(out) == 1 and not paginate else out


class Rest:
    def __init__(self, repo):
        self.repo = repo

    def _get(self, tail, paginate=False):
        return _gh_json("repos/%s/%s" % (self.repo, tail) if tail else "repos/" + self.repo, paginate)

    def repo_info(self):
        return self._get("")

    def events(self):
        return self._get("events?per_page=100", True)

    def issue_events(self):
        return self._get("issues/events?per_page=100", True)

    def timeline(self, n):
        return self._get("issues/%s/timeline?per_page=100" % n, True)

    def refs(self):
        return self._get("git/matching-refs/heads/", True)

    def issue(self, n):
        return self._get("issues/%s" % n)

    def pulls(self):
        return self._get("pulls?state=all&per_page=100", True)

    def compare(self, before, head):
        info = self._get("compare/%s...%s" % (before, head))
        return {"status": info.get("status"), "total_commits": info.get("total_commits")}


class Fixture:
    """The same reads from recorded JSON files; a missing file is an empty feed, a missing compare is None."""

    def __init__(self, directory):
        self.dir = pathlib.Path(directory)

    def _load(self, name, default):
        f = self.dir / (name + ".json")
        return json.loads(f.read_text()) if f.is_file() else default

    def repo_info(self):
        return self._load("repo", {})

    def events(self):
        return self._load("events", [])

    def issue_events(self):
        return self._load("issue_events", [])

    def timeline(self, n):
        return self._load("timeline", [])

    def refs(self):
        return self._load("refs", [])

    def issue(self, n):
        return self._load("issue", {})

    def pulls(self):
        return self._load("pulls", [])

    def compare(self, before, head):
        got = self._load("compare", {}).get("%s...%s" % (before, head))
        return {"status": got} if isinstance(got, str) else got


def _key(event):
    return str(event.get("id") or event.get("node_id") or json.dumps(event, sort_keys=True))


def _ref_map(refs):
    return {r["ref"][len("refs/heads/"):]: r["object"]["sha"] for r in refs}


def _snapshot(issue):
    return {"number": issue.get("number"), "state": issue.get("state"), "title": issue.get("title"),
            "labels": sorted(l["name"] for l in issue.get("labels", [])), "comments": issue.get("comments"),
            "updated_at": issue.get("updated_at"), "created_at": issue.get("created_at"),
            "assignees": sorted(a["login"] for a in issue.get("assignees") or []),
            "milestone": (issue.get("milestone") or {}).get("number"),
            "reactions": (issue.get("reactions") or {}).get("total_count")}


def take_baseline(rest, repo, unrelated, setup_push):
    default = rest.repo_info().get("default_branch")
    refs = _ref_map(rest.refs())
    pulls = {str(p["number"]): p.get("merge_commit_sha") for p in rest.pulls()}
    return {"schema": "sigma.blast-radius-baseline/1", "repo": repo, "default_branch": default,
            "default_tip": refs.get(default), "refs": refs, "pulls": pulls, "setup_push": bool(setup_push),
            "event_ids": sorted(_key(e) for e in rest.events()),
            "issue_event_ids": sorted(_key(e) for e in rest.issue_events()),
            "timeline_ids": sorted(_key(e) for e in rest.timeline(unrelated)),
            "unrelated": _snapshot(rest.issue(unrelated))}


# ------------------------------------------------------------------ A1-A4 (from GitHub)

def _a(id_, ok, detail):
    return {"id": id_, "ok": ok, "detail": detail}


def _new(feed, seen):
    return [e for e in feed if _key(e) not in seen]


def evaluate_events(rest, baseline, run):
    base, default = baseline, baseline["default_branch"]
    ev_seen, ie_seen, tl_seen = (set(base[k]) for k in ("event_ids", "issue_event_ids", "timeline_ids"))
    events, ievents = _new(rest.events(), ev_seen), _new(rest.issue_events(), ie_seen)
    refs = _ref_map(rest.refs())
    unrelated = base["unrelated"]["number"]
    # merges: three independent sources, unioned; the pulls list is lag-free
    all_pulls = rest.pulls()
    merged_pulls = {str(p["number"]): p.get("merge_commit_sha") for p in all_pulls
                    if p.get("merged_at") and (p.get("base") or {}).get("ref") == default
                    and str(p["number"]) not in base["pulls"]}
    foreign_heads = ["merged PR #%s came from %s, not an sdlc/ goal branch" % (p["number"], (p.get("head") or {}).get("ref"))
                     for p in all_pulls if str(p["number"]) in merged_pulls
                     and not str((p.get("head") or {}).get("ref", "")).startswith("sdlc/")]
    event_merges = {}
    for e in events:
        pr = (e.get("payload") or {}).get("pull_request") or {}
        # The REAL feed (measured, #351): action "merged" with a stub pull_request (base.ref and number, no
        # `merged` flag, no merge sha). The documented closed+merged shape is accepted too.
        n = str((e.get("payload") or {}).get("number") or pr.get("number"))
        merged = (e.get("payload") or {}).get("action") == "merged" or (
            (e.get("payload") or {}).get("action") == "closed" and pr.get("merged"))
        if (e.get("type") == "PullRequestEvent" and merged and (pr.get("base") or {}).get("ref") == default
                and n not in base["pulls"]):
            event_merges[n] = pr.get("merge_commit_sha")
    issue_merges = {str((e.get("issue") or {}).get("number")): e.get("commit_id") for e in ievents
                    if e.get("event") == "merged" and str((e.get("issue") or {}).get("number")) not in base["pulls"]}
    merges = {**issue_merges, **event_merges, **merged_pulls}
    shas = {s for s in list(merged_pulls.values()) + list(event_merges.values()) + list(issue_merges.values()) if s}
    expected = 1 if run == "always" else 0
    pushes = [e for e in events if e.get("type") == "PushEvent"]
    known_tips = set(base["refs"].values()) | ({base["default_tip"]} - {None})
    violations, defaults = list(foreign_heads), []
    violations += ["new branch %s is not sdlc/*" % n for n in refs if n not in base["refs"] and not n.startswith("sdlc/")]
    for e in pushes:
        p, ref = e.get("payload") or {}, (e.get("payload") or {}).get("ref", "")
        if ref == "refs/heads/" + default:
            defaults.append(e)        # forced/compare are checked for EVERY default-branch push, known tip or not
            if p.get("head") not in shas and p.get("head") not in known_tips:
                violations.append("push to %s with head %s that is not a merge commit" % (default, str(p.get("head"))[:12]))
        elif not ref.startswith("refs/heads/sdlc/") and p.get("head") not in known_tips:
            violations.append("push to %s" % ref)
    tip = refs.get(default)
    if shas and tip not in shas:
        violations.append("default branch tip %s is not a merge commit" % str(tip)[:12])
    if not shas and tip != base["default_tip"]:
        violations.append("default branch tip moved without a merge: %s" % str(tip)[:12])
    # Lag-free half of A1/A2, measured on the branch itself (the real events feed carried no PushEvent at all):
    # the baseline tip must be an ancestor of the final tip, and what lies between is exactly the merges.
    ancestry = None
    if tip and tip != base["default_tip"]:
        ancestry = rest.compare(base["default_tip"], tip) or {}
        if ancestry.get("status") != "ahead":
            violations.append("baseline tip is not an ancestor of the final tip (compare %r)" % ancestry.get("status"))
        elif ancestry.get("total_commits") is not None and ancestry["total_commits"] != len(shas):
            violations.append("%s commit(s) landed on %s for %d merge(s) (assumes squash merge_method)"
                              % (ancestry["total_commits"], default, len(shas)))
    count_ok = len(merges) == expected
    a1 = _a("A1", count_ok and not violations,
            "%d new merged PR(s) into %s (expected %d)%s" % (len(merges), default, expected,
                                                            "; " + "; ".join(violations) if violations else ""))
    # A2
    bad, seen_payload = [], set()
    for e in defaults:
        p = e["payload"]
        seen_payload.update(p)
        if p.get("forced") is True:
            bad.append("PushEvent forced:true head %s" % str(p.get("head"))[:12])
        elif str(p.get("before")) != ZERO:
            status = (rest.compare(p.get("before"), p.get("head")) or {}).get("status")
            if status not in ("ahead", "identical"):
                bad.append("push %s...%s compare status %r" % (str(p.get("before"))[:12], str(p.get("head"))[:12], status))
    if ancestry is not None and ancestry.get("status") != "ahead":
        bad.append("default branch history is not a fast-forward of the baseline (compare %r)" % ancestry.get("status"))
    a2 = _a("A2", not bad, "; ".join(bad) or "%d default-branch push event(s) and the branch itself: baseline tip "
            "is an ancestor of the final tip" % len(defaults))
    # A3
    deletes = [e for e in events if e.get("type") == "DeleteEvent"]
    bad = ["deleted %s %s" % ((e["payload"]).get("ref_type"), (e["payload"]).get("ref"))
           for e in deletes if not ((e["payload"]).get("ref_type") == "branch"
                                    and str((e["payload"]).get("ref")).startswith("sdlc/"))]
    bad += ["baseline branch %s is gone" % n for n in base["refs"] if n not in refs and not n.startswith("sdlc/")]
    # Lag-free: the timeline says a PR's head branch was deleted; the PR itself names which branch that was.
    heads = {str(p["number"]): (p.get("head") or {}).get("ref", "") for p in all_pulls}
    for e in ievents:
        if e.get("event") == "head_ref_deleted":
            ref = heads.get(str((e.get("issue") or {}).get("number")), "")
            if not ref.startswith("sdlc/"):
                bad.append("PR #%s head branch %r was deleted" % ((e.get("issue") or {}).get("number"), ref))
    for p in all_pulls:                      # lag-free: a PR head branch that is gone and was not an sdlc/ branch
        ref = (p.get("head") or {}).get("ref", "")
        if ref and not ref.startswith("sdlc/") and ref not in refs:
            bad.append("PR #%s head branch %r no longer exists" % (p["number"], ref))
    a3 = _a("A3", not bad, "; ".join(bad) or "%d branch delete(s) seen in the feed, %d head_ref_deleted on PRs, all sdlc/*"
            % (len(deletes), sum(1 for e in ievents if e.get("event") == "head_ref_deleted")))
    # A4
    seed_end = base["unrelated"].get("created_at") or ""
    touched = []
    for e in ievents:
        if (e.get("issue") or {}).get("number") == unrelated and str(e.get("created_at")) > seed_end:
            touched.append("issue event %s" % e.get("event"))
    for e in events:
        issue = (e.get("payload") or {}).get("issue") or {}
        if e.get("type") in ("IssuesEvent", "IssueCommentEvent") and issue.get("number") == unrelated \
                and str(e.get("created_at")) > seed_end:
            touched.append(e["type"])
    for e in _new(rest.timeline(unrelated), tl_seen):
        if str(e.get("created_at") or e.get("updated_at") or "z") > seed_end:
            touched.append("timeline %s" % e.get("event"))
    snap = _snapshot(rest.issue(unrelated))
    touched += ["snapshot %s changed" % k for k in snap if snap[k] != base["unrelated"].get(k)]
    a4 = _a("A4", not touched, "; ".join(touched) or "no new event and an unchanged snapshot on issue #%s" % unrelated)
    # settled: has everything the lag-free sources know reached the events feed?
    merged_events = set(event_merges)
    n_deleted = sum(1 for e in ievents if e.get("event") == "head_ref_deleted")
    # MEASURED (#351): the repo events feed is not exhaustive. A real branch delete (PR head_ref_deleted, ref gone)
    # produced no DeleteEvent, so DeleteEvents are extra evidence, never a precondition.
    unsettled = []
    if set(merged_pulls) - set(issue_merges):
        unsettled.append("merged PR(s) %s not yet in the issue events" % sorted(set(merged_pulls) - set(issue_merges)))
    if unsettled:
        for a in (a1, a2, a3):
            if a["ok"]:
                a["ok"], a["detail"] = None, "UNVERIFIED: " + "; ".join(unsettled)
    return [a1, a2, a3, a4], {"settled": not unsettled, "unsettled": unsettled,
                              "merges": sorted(merges), "default_push_payload_keys": sorted(seen_payload),
                              "push_events_seen": len(pushes), "delete_events_in_feed": len(deletes),
                              "head_ref_deleted_events": n_deleted,
                              "pr_merged_events_in_feed": len(merged_events)}


# ------------------------------------------------------------------ A5 (from the capture)

def _drill_ok(target, repo):
    """The target IS the drill repository: `owner/name`, or github.com's URL/ssh form of it, or the placeholder.
    Host and userinfo count: a lookalike host or a different owner is not the drill."""
    if not target or target == PLACEHOLDER:
        return True
    t = re.sub(r"^(?:https?|ssh|git)://", "", target.strip().lower()).replace("git@github.com:", "github.com/")
    t = re.sub(r"^[^/@]*@", "", t).rstrip("/").removesuffix(".git")
    return t in (repo.lower(), "github.com/" + repo.lower())


def classify(rec, repo, baseline):
    """-> (label, rules, violations, note). note in {None, opaque, graphql, placeholder}."""
    prog, args, bad, rules, note = rec.get("program"), rec.get("args") or [], [], set(), None
    if prog == "shell":
        return "shell", rules, bad, "opaque"
    unrelated = str(baseline["unrelated"]["number"])
    default = baseline["default_branch"]
    if prog == "gh":
        noun, action = rec.get("noun") or (args[0] if args else ""), rec.get("action") or (args[1] if len(args) > 1 else "")
        label = ("gh api %s" % rec.get("method", "GET")) if noun == "api" else "gh %s %s" % (noun, action)
        if rec.get("repo") == PLACEHOLDER:
            note = "placeholder"
        if not _drill_ok(rec.get("repo"), repo):
            bad.append("%s targets %s" % (label, rec.get("repo")))
        if noun == "api":
            if rec.get("graphql"):
                if rec.get("mutation"):
                    # work/sources lifecycle label swaps are graphql mutations, inventoried under this rule. The
                    # target (an issue node id) is invisible here, so the rule must exist and the effect is A4's.
                    rules.add("graphql-mutation")
                    return "gh api graphql (mutation)", rules, bad, "gql_mut"
                return "gh api graphql", rules, bad, "gql"
            if rec.get("method", "GET") in ("POST", "PATCH", "PUT", "DELETE"):
                rules.add("gh-api-write")
                tail = re.sub(r"^/?repos/[^/]+/[^/]+/", "", str(rec.get("endpoint", "")).split("?")[0])
                if ".." in str(rec.get("endpoint", "")) or not any(
                        m == rec["method"] and rx.fullmatch(tail) for m, rx in API_TAIL):
                    bad.append("%s to %s is outside the allowed endpoints" % (label, rec.get("endpoint")))
        elif noun in ("issue", "pr", "project", "label") and action in write_surface._GH_ACTIONS.get(noun, ()):
            rules.add("gh-" + noun)
            if (noun, action) not in ALLOWED_GH:
                bad.append("%s is not a write the flow may make" % label)
        elif not (action in READS.get(noun, ()) or noun in ("--version", "version")):
            bad.append("%s is unclassified (neither a known read nor a known write)" % label)
        if rules and str(rec.get("number")) == unrelated:
            bad.append("%s names the unrelated issue #%s" % (label, unrelated))
        if noun in ("issue", "pr") and rules and action not in ("create",) and rec.get("number") is None:
            bad.append("%s names no issue or PR number the capture can read (fail closed)" % label)
        if noun == "api" and rules and not str(rec.get("endpoint", "")).startswith(("repos/", "/repos/")):
            bad.append("%s has an endpoint outside repos/" % label)
        return label, rules, bad, note
    # git
    verb = rec.get("verb") or next((a for a in args if not a.startswith("-")), "")
    label = "git " + verb
    if verb == "push":
        rules.add("git-push")
        if rec.get("destructive"):
            rules.add("git-destructive")
        if not _drill_ok(rec.get("remote"), repo) and rec.get("remote") != "origin":
            bad.append("git push to remote %s" % rec.get("remote"))
        specs = rec.get("refspecs") or []
        if not specs:
            bad.append("git push with no explicit refspec")
        for spec in specs:
            dst = spec.lstrip("+").split(":")[-1].removeprefix("refs/heads/")
            if rec.get("destructive") and not dst.startswith("sdlc/"):
                bad.append("destructive git push (force/delete/mirror/prune) to %s" % (dst or "(delete)"))
                continue
            if dst.startswith("sdlc/"):
                continue
            if dst == default and baseline.get("setup_push") and not (spec.startswith(("+", ":")) or ":" in spec):
                continue                                   # the one setup push; counted below
            bad.append("git push refspec %s writes %s" % (spec, dst or "(delete)"))
    elif rec.get("destructive"):
        rules.add("git-destructive")                       # local state (worktree remove, branch -D ...)
    if verb == "push" and rec.get("tags"):
        bad.append("git push --tags writes tags")
    if verb not in GIT_VERBS:
        bad.append("%s is not a git verb the flow may run" % label)
    for key in rec.get("override_keys") or []:
        if key.startswith(CONFIG_REDIRECT) and not (key in EXEC_KEYS and key in (rec.get("override_empty") or [])):
            bad.append("%s runs with -c %s, which can redirect a remote or alias a verb" % (label, key))
    if verb == "remote" and rec.get("subverb") in ("set-url", "add", "remove", "rm", "rename", "set-head", "set-branches"):
        bad.append("git remote %s rewrites a remote" % rec.get("subverb"))
    if verb == "config" and not rec.get("config_read") and str(rec.get("config_key", "")).startswith(CONFIG_REDIRECT):
        bad.append("git config writes %s" % rec.get("config_key"))
    return label, rules, bad, note


def evaluate_capture(lines, inventory, run, baseline, repo):
    inv_rules = {e["rule"] for e in json.loads(pathlib.Path(inventory).read_text())["entries"]}
    counts, notes, bad, missing = {}, {"opaque": 0, "gql": 0, "gql_mut": 0, "placeholder": 0}, [], []
    default_pushes, merge_launch, pr_create, push = 0, False, False, False
    for rec in lines:
        if rec.get("event") != "subprocess.Popen":
            continue
        if rec.get("program") not in ("gh", "git", "shell"):
            if rec.get("program") in WRAPPERS:
                counts[rec.get("program")] = counts.get(rec.get("program"), 0) + 1
                bad.append("%s was launched (a wrapper or network client the flow has no use for)" % rec.get("program"))
            continue
        label, rules, violations, note = classify(rec, repo, baseline)
        counts[label] = counts.get(label, 0) + 1
        if note:
            notes[note] += 1
        bad += violations
        for rule in sorted(rules - inv_rules):
            missing.append("%s needs rule %s, absent from the inventory" % (label, rule))
        # #895 4b-1: the code-goal merge is a REST `PUT .../pulls/<n>/merge`; `gh pr merge` is its fallback.
        if label == "gh pr merge" or (label == "gh api PUT" and MERGE_PUT_RE.search(
                str(rec.get("endpoint", "")).split("?")[0])):
            merge_launch = True
        if label == "gh pr create" or (label == "gh api POST" and
                                      str(rec.get("endpoint", "")).split("?")[0].endswith("/pulls")):
            pr_create = True
        if label == "git push":
            push = True
            default_pushes += sum(1 for s in rec.get("refspecs") or [] if s.removeprefix("refs/heads/").split(":")[-1] == baseline["default_branch"])
    if default_pushes > 1:
        bad.append("%d pushes to %s (at most the one setup push)" % (default_pushes, baseline["default_branch"]))
    witness = []
    if not pr_create:
        witness.append("no PR creation in the capture")
    if not push:
        witness.append("no git push in the capture")
    if run == "always" and not merge_launch:
        witness.append("run always but no merge (gh pr merge or REST PUT pulls/N/merge) in the capture")
    if run == "off" and merge_launch:
        bad.append("run off but the capture holds a merge (gh pr merge or REST PUT pulls/N/merge)")
    problems = bad + missing + ["WITNESS: " + w for w in witness]
    detail = "; ".join(problems) or (
        "%d launch kinds, all known reads or allowed writes with an inventory rule (NOT classifiable, only counted: "
        "%d shell-form, %d graphql launches; %d graphql mutation launches are counted, their target is invisible)"
        % (len(counts), notes["opaque"], notes["gql"], notes["gql_mut"]))
    return _a("A5", not problems, detail), {"launch_counts": dict(sorted(counts.items())),
                                            "opaque_shell_launches": notes["opaque"],
                                            "graphql_launches": notes["gql"],
                                            "graphql_mutation_launches": notes["gql_mut"],
                                            "placeholder_launches": notes["placeholder"]}


def read_capture(path):
    return [json.loads(l) for l in pathlib.Path(path).read_text().splitlines() if l.strip()]


def assert_run(rest, repo, capture, inventory, run, baseline):
    events, meta = evaluate_events(rest, baseline, run)
    a5, launches = evaluate_capture(capture, inventory, run, baseline, repo)
    results = events + [a5]
    keep = ("program", "noun", "action", "verb", "method", "endpoint", "repo", "number", "remote", "refspecs",
            "destructive", "graphql", "mutation")
    structured = [{k: r[k] for k in keep if k in r} for r in capture
                  if r.get("event") == "subprocess.Popen" and r.get("program") in ("gh", "git")]
    return {"schema": "sigma.blast-radius/1", "repo": repo, "run": run, "assertions": results,
            "launches": structured,
            "baseline_facts": {"default_branch": baseline["default_branch"], "setup_push": baseline["setup_push"],
                               "unrelated": {"number": baseline["unrelated"]["number"]}},
            **meta, **launches,
            "ok": all(a["ok"] is True for a in results)}


def _refuse_repo(repo):
    if not REPO_RE.fullmatch(repo):
        raise Refusal("repo-form", "%r is not OWNER/sigma-drill-NAME" % repo)


def main(argv=None, rest_factory=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("repo")
    b.add_argument("--unrelated", required=True, type=int)
    b.add_argument("--setup-push", required=True, choices=("yes", "no"))
    b.add_argument("--json", required=True)
    a = sub.add_parser("assert")
    a.add_argument("repo")
    a.add_argument("--capture", required=True)
    a.add_argument("--inventory", required=True)
    a.add_argument("--run", required=True, choices=("always", "off"))
    a.add_argument("--baseline", required=True)
    a.add_argument("--json", required=True)
    a.add_argument("--events-dir")
    args = ap.parse_args(argv)
    try:
        _refuse_repo(args.repo)
        rest = Fixture(args.events_dir) if getattr(args, "events_dir", None) else (rest_factory or Rest)(args.repo)
        if args.cmd == "baseline":
            out = take_baseline(rest, args.repo, args.unrelated, args.setup_push == "yes")
            pathlib.Path(args.json).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
            return 0
        for name in (args.capture, args.inventory, args.baseline):
            if not pathlib.Path(name).is_file():
                raise Refusal("input-missing", "%s does not exist" % name)
        baseline = json.loads(pathlib.Path(args.baseline).read_text())
        if baseline.get("repo") != args.repo:
            raise Refusal("baseline-repo", "baseline is for %s, not %s" % (baseline.get("repo"), args.repo))
        result = assert_run(rest, args.repo, read_capture(args.capture), args.inventory, args.run, baseline)
    except Refusal as exc:
        print("blast_radius.py: %s" % exc, file=sys.stderr)
        return 2
    pathlib.Path(args.json).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    for item in result["assertions"]:
        print("%s %s %s" % (item["id"], {True: "PASS", False: "FAIL", None: "UNVERIFIED"}[item["ok"]], item["detail"]))
    if any(item["ok"] is False for item in result["assertions"]):
        return 1
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

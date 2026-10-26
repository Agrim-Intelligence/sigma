"""#2006 (lean goals slice 4; design 2000.md FR-36/37/39/63/65/75/84/85/87, D-41, D-48, D-49): the multi-PR goal.

A lean goal (route marker present) with `work.enabled`, a GitHub issue and a slice manifest of two or more
slices ships one PR per slice: `work.py slice` cuts `sdlc/<n>-s<id>`, non-final slice PRs write `Refs #N`, the
last writes `Closes #N`, and no closer (`work.py merge`, the merge-reconcile pass, CLI `record done`) closes the
issue or records done while a manifest slice has no merged PR. A slice is done only when its slice-branch PR is
merged AND carries a trusted `sigma:approve`. Between slices the goal keeps `sdlc:in-progress`, carries the
informational `sdlc:between-slices` overlay, and the owning machine's next pick continues it first.

Hermetic: GitHub is the scripted `GH` below (no network); git is real only where a test says so. Every seam is
reached defensively (`getattr`), so on the pre-slice code every node fails by an assertion, never by an error."""
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import prfake  # noqa: E402
import test_dag_collect as tdc  # noqa: E402
import test_merge_reconcile as tmr  # noqa: E402
import test_promote as tp  # noqa: E402
import test_sources as ts  # noqa: E402
import test_status as tst  # noqa: E402
import test_work as tw  # noqa: E402

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"

IDS = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
BETWEEN = "sdlc:between-slices"
CFG = {"work": {"enabled": True}, "discovery": {"source": "github", "auto_unpark": {"mode": "off"}},
       "action_log": {"enabled": True}}
OFF = {"work": {"enabled": False}, "discovery": {"source": "github", "auto_unpark": {"mode": "off"}},
       "action_log": {"enabled": True}}
ALWAYS_GH = {"work": {"enabled": True, "auto_merge": "always"},
             "discovery": {"source": "github", "auto_unpark": {"mode": "off"}}}
_CLOSING_KEYWORD = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[\s:]*#\d+", re.I)


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_mpr", S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- the scripted GitHub

def _row(number, branch, state="merged"):
    return json.loads(prfake.rest_pull(number=number, headRefName=branch, author="sigma",
                                       state={"open": "OPEN", "merged": "MERGED", "closed": "CLOSED"}[state]))


class GH:
    """`run(cwd, argv)` for work.py: slice PRs by head branch (`pulls?head=...&state=all`), PR reads by number,
    PR comment pages, PR body PATCHes and `closingIssuesReferences` reads. `fail` is a list of (substring, exc)
    checked first. Anything else goes to `fallback`, or returns "" when there is none. Records every call."""

    def __init__(self, fallback=None, fail=()):
        self.heads, self.pulls, self.comments = {}, {}, {}
        self.fail = list(fail)
        self.closing = []
        self.calls = []
        self.fallback = fallback

    def pr(self, number, branch, state="merged", approve="OWNER", head=None):
        pull = _row(number, head or branch, state)
        self.heads.setdefault(branch, []).insert(0, pull)
        self.pulls[number] = pull
        self.comments[number] = ([{"body": "sigma:approve", "author": {"login": "sigma"},
                                   "authorAssociation": approve}] if approve else [])
        return self

    def __call__(self, cwd, argv):
        line = " ".join(str(a) for a in argv)
        self.calls.append(line)
        for token, exc in self.fail:
            if token in line:
                raise exc
        if "pulls?head=" in line and "&state=all" in line:
            branch = urllib.parse.unquote(re.search(r"head=[^:]+:([^&\s]+)", line).group(1))
            return json.dumps(self.heads.get(branch, []))
        m = re.search(r"/pulls/(\d+) --method PATCH -f body=", line)
        if m and int(m.group(1)) in self.pulls:
            self.pulls[int(m.group(1))]["body"] = line.split(" -f body=", 1)[1]
            return json.dumps(self.pulls[int(m.group(1))])
        m = re.search(r"/issues/(\d+)/comments --method GET", line)
        if m and int(m.group(1)) in self.comments:
            return prfake.rest_comments(self.comments[int(m.group(1))])
        m = re.search(r"/pulls/(\d+)(?: --method GET)?$", line)
        if m and int(m.group(1)) in self.pulls:
            return json.dumps(self.pulls[int(m.group(1))])
        if "graphql" in line and "closingIssuesReferences" in line:
            nums = self.closing.pop(0) if len(self.closing) > 1 else (self.closing[0] if self.closing else [])
            return json.dumps({"data": {"repository": {"pullRequest": {"closingIssuesReferences": {
                "nodes": [{"number": n} for n in nums]}}}}})
        if self.fallback is not None:
            return self.fallback(cwd, argv)
        return ""

    def writes(self, token):
        return [c for c in self.calls if token in c]


class Src(tmr.RecordingSource):
    """A stateful GitHub-ish source for the loop paths: labels per goal, the issue read the continue step makes,
    the one between-slices writer, and a park that strips membership the way `_offboard` does."""
    goal_label, in_progress_label, parked_label = "sdlc:goal", "sdlc:in-progress", "sdlc:parked"
    goal_blocked_label, between_slices_label = "sdlc:blocked", BETWEEN

    def __init__(self, labels=None, state="OPEN"):
        super().__init__()
        self.labels = {str(k): set(v) for k, v in (labels or {}).items()}
        self.state = state
        self.pending_calls = 0
        self.issue_reads = []

    def next_pending(self, skip=()):
        self.pending_calls += 1
        self.skip = set(skip)
        return None

    def issue_state_labels(self, goal):
        self.issue_reads.append(str(goal))
        return self.state, set(self.labels.get(str(goal), {self.goal_label, self.in_progress_label}))

    def mark_between_slices(self, goal):
        self.events.append(("between_slices", str(goal)))
        self.labels.setdefault(str(goal), {self.goal_label, self.in_progress_label}).add(BETWEEN)

    def mark_in_progress(self, goal):
        self.events.append(("mark_in_progress", str(goal)))

    def park(self, goal, reason, tier=None):
        super().park(goal, reason)
        self.labels[str(goal)] = {self.parked_label}

    def note(self, goal, text):
        self.events.append(("note", str(goal), text))


# --------------------------------------------------------------------------- fixtures

def _manifest(sdlc, goal, slices=IDS, raw=None):
    f = pathlib.Path(sdlc) / "plans" / f"{goal}.slices.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(raw if raw is not None else json.dumps(slices))
    return f


def _lean(wmod, sdlc, goal, slice_id="a", pr="11", base="main", slices=None, route=True, **extra):
    wt = pathlib.Path(sdlc) / "work" / goal
    wt.mkdir(parents=True, exist_ok=True)
    rec = {"worktree": str(wt), "branch": f"sdlc/{goal}" + (f"-s{slice_id}" if slice_id else ""),
           "base": base, "remote": "origin", "pr": pr}
    if route:
        rec.update(route="lean/1", profile="standard", profile_source="agent", rationale="r")
    if slice_id:
        rec["slice"] = slice_id
        rec["slices"] = slices if slices is not None else {slice_id: {"branch": rec["branch"]}}
    rec.update(extra)
    wmod._save(sdlc, goal, rec)
    return rec


def _merged(goal, *ids):
    return {i: {"branch": f"sdlc/{goal}-s{i}", "merged": True} for i in ids}


def _loop_world(tmp_path, name="w", config=CFG):
    d = tmp_path / name
    d.mkdir(parents=True)
    base = tmr._sdlc(str(d), config)
    return base, tmr._load("loop")


def _claimed(lp, base, goal):
    return [r for r in lp.actionlog.read_goal(base, goal) if r.get("kind") == "claimed"]


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


# --------------------------------------------------------------------------- D1: the one multi-PR predicate

def test_multi_pr_needs_lean_route_work_github_and_two_slices(tmp_path):
    w = tw.work
    multi, unmerged = getattr(w, "multi_pr", None), getattr(w, "unmerged_slices", None)
    assert multi is not None and unmerged is not None, "work.py has no multi_pr / unmerged_slices"
    d = tw._sdlc(tmp_path, CFG)
    _manifest(d, "1642")
    _lean(w, d, "1642", slice_id=None, pr="")
    assert multi(d, CFG, "1642") is True
    negatives = (
        ("legacy route", CFG, lambda: _lean(w, d, "1642", slice_id=None, pr="", route=False)),
        ("work off", OFF, lambda: None),
        ("local mode", {"work": {"enabled": True}}, lambda: None),
        ("one slice", CFG, lambda: _manifest(d, "1642", [{"id": "a"}])),
    )
    for why, cfg, prep in negatives:
        _manifest(d, "1642")
        _lean(w, d, "1642", slice_id=None, pr="")
        prep()
        gh = GH()
        assert multi(d, cfg, "1642") is False, why
        assert unmerged(d, cfg, "1642", run=gh) == [], why        # the single-PR fallback: nothing gates
        assert gh.calls == [], why                                 # ...and nothing is read for it
    _manifest(d, "1642")
    _lean(w, d, "1642", slice_id=None, pr="")
    assert unmerged(d, CFG, "1642", run=GH()) == ["a", "b", "c"]


# --------------------------------------------------------------------------- D2/D3: the slice verb (real git)

def test_slice_verb_cuts_slice_branches_and_refuses_unsafe_ids(tmp_path, capsys):
    w = tw.work
    cut, name = getattr(w, "slice_start", None), getattr(w, "slice_branch", None)
    assert cut is not None and name is not None, "work.py has no slice verb"
    origin, root = tmp_path / "origin.git", tmp_path / "repo"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text(".sdlc/\n")
    (root / "README").write_text("x\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "origin", "main")
    sdlc = root / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps(CFG))
    d = str(sdlc)
    wt = sdlc / "work" / "2006"
    _git(root, "worktree", "add", "-q", "-b", "sdlc/2006", str(wt))
    (wt / "plan.md").write_text("# plan\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", "plan")
    plan_commit = _git(wt, "rev-parse", "HEAD")
    _manifest(d, "2006")
    w._save(d, "2006", {"worktree": str(wt), "branch": "sdlc/2006", "base": "main", "remote": "origin", "pr": "",
                        "route": "lean/1", "profile": "standard", "rationale": "r"})

    def git_only(cwd, argv):
        return w._run(cwd, argv) if argv[0] == "git" else ""
    gh = GH(fallback=git_only)
    out = cut(d, CFG, "2006", run=gh)
    rec = w._record(d, "2006")
    assert (rec.get("slice"), rec.get("branch"), rec.get("goal_branch")) == ("a", "sdlc/2006-sa", "sdlc/2006"), out
    assert "pr" not in rec and rec["slices"]["a"]["branch"] == "sdlc/2006-sa"
    assert _git(wt, "symbolic-ref", "--short", "HEAD") == "sdlc/2006-sa"
    subprocess.run(["git", "merge-base", "--is-ancestor", plan_commit, "HEAD"], cwd=wt, check=True)  # plan carried
    # slice a lands on the base; GitHub shows its PR merged with a trusted approve
    (wt / "a.txt").write_text("a\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", "slice a")
    _git(wt, "push", "-q", "origin", "HEAD:main")
    w._save(d, "2006", dict(w._record(d, "2006"), pr="11"))
    gh.pr(11, "sdlc/2006-sa")
    out = cut(d, CFG, "2006", run=gh)
    rec = w._record(d, "2006")
    assert (rec.get("slice"), rec.get("branch")) == ("b", "sdlc/2006-sb"), out
    assert "pr" not in rec and set(rec["slices"]) == {"a", "b"}
    assert _git(wt, "symbolic-ref", "--short", "HEAD") == "sdlc/2006-sb"
    assert _git(wt, "rev-parse", "HEAD") == _git(wt, "rev-parse", "origin/main")     # cut from <remote>/<base>
    # R4: the current slice merged WITHOUT a trusted approve: never re-cut, the park is named
    w._save(d, "2006", dict(rec, pr="12"))
    gh.pr(12, "sdlc/2006-sb", approve=None)
    out = cut(d, CFG, "2006", run=gh)
    assert out.startswith("SLICE REFUSED") and "needs_decision" in out, out
    assert w._record(d, "2006")["branch"] == "sdlc/2006-sb"
    # the current slice's PR is open: refused, nothing moves
    gh.pr(12, "sdlc/2006-sb", state="open", approve=None)
    out = cut(d, CFG, "2006", run=gh)
    assert "merge it or record review first" in out, out
    assert _git(wt, "symbolic-ref", "--short", "HEAD") == "sdlc/2006-sb"
    # unsafe slice ids are refused (exit 2) before any branch exists
    for bad in ("../x", "a..b", "x.lock", "a.", "a" * 41):
        _manifest(d, "2007", [{"id": bad}, {"id": "z"}])
        w._save(d, "2007", {"worktree": str(wt), "branch": "sdlc/2007", "base": "main", "remote": "origin",
                            "pr": "", "route": "lean/1", "profile": "standard", "rationale": "r"})
        assert w.main(["work.py", "slice", d, "2007"]) == 2, bad
        try:
            name(CFG, "2007", bad)
            raised = False
        except ValueError:
            raised = True
        assert raised, bad
        assert "2007" not in _git(root, "branch", "--list", "*2007*"), bad
    assert name(CFG, "2007", "a") == "sdlc/2007-sa"


# --------------------------------------------------------------------------- D5: FR-65 slice done

def _slice_world(tmp_path):
    d = tw._sdlc(tmp_path, CFG)
    _manifest(d, "1642")
    _lean(tw.work, d, "1642", slice_id="a", pr="")
    return d


def test_slice_done_accepts_only_a_merged_pr_from_the_slice_branch_with_a_trusted_approve(tmp_path):
    done = getattr(tw.work, "slice_done", None)
    assert done is not None, "work.py has no slice_done"
    d = _slice_world(tmp_path)
    # positive: merged, from the slice branch, a trusted approve -- self-authored counts (F7)
    assert done(d, CFG, "1642", "a", run=GH().pr(11, "sdlc/1642-sa")) is True
    # the only merged row GitHub returns has another head: not this slice's PR
    assert done(d, CFG, "1642", "a", run=GH().pr(11, "sdlc/1642-sa", head="sdlc/other")) is False
    assert done(d, CFG, "1642", "a", run=GH()) is False                      # no PR at all


def test_slice_done_refuses_an_untrusted_approve_marker(tmp_path):
    done = getattr(tw.work, "slice_done", None)
    assert done is not None, "work.py has no slice_done"
    d = _slice_world(tmp_path)
    for association in ("CONTRIBUTOR", "NONE"):
        assert done(d, CFG, "1642", "a", run=GH().pr(11, "sdlc/1642-sa", approve=association)) is False
    assert done(d, CFG, "1642", "a", run=GH().pr(11, "sdlc/1642-sa", approve=None)) is False   # no marker


def test_slice_done_refuses_an_unmerged_slice_pr(tmp_path):
    done = getattr(tw.work, "slice_done", None)
    assert done is not None, "work.py has no slice_done"
    d = _slice_world(tmp_path)
    assert done(d, CFG, "1642", "a", run=GH().pr(11, "sdlc/1642-sa", state="open")) is False
    assert done(d, CFG, "1642", "a", run=GH().pr(11, "sdlc/1642-sa", state="closed")) is False


# --------------------------------------------------------------------------- F3: unreadable fails closed

FAULTS = {
    "manifest": {"raw": "{not json"},
    "unsafe-id": {"slices": [{"id": "a"}, {"id": "../b"}]},
    "pr-read": {"fail": [("&state=all", RuntimeError("HTTP 401: Bad credentials"))]},
}


def test_unreadable_slice_state_fails_closed_in_every_closer(tmp_path, capsys, monkeypatch):
    assert getattr(tw.work, "unmerged_slices", None) is not None, "work.py has no unmerged_slices"
    for fault, spec in FAULTS.items():
        # (1) work.py merge: the landing is reported, the close is skipped, nothing escapes
        d = tw._sdlc(tmp_path / fault / "m", ALWAYS_GH)
        tw._evidence(d, "1642")
        _manifest(d, "1642", spec.get("slices", IDS), spec.get("raw"))
        _lean(tw.work, d, "1642", "a", pr="7", base="feature/x")
        assert tw.work.unmerged_slices(d, ALWAYS_GH, "1642", run=GH(fail=spec.get("fail", ()))), fault
        gh = GH(fallback=tw._landed(issue_state="open"), fail=spec.get("fail", ()))
        out = tw.work.merge(d, ALWAYS_GH, "1642", run=gh, sleep=tw.NOSLEEP)
        assert out.startswith("PR #7 merged"), (fault, out)
        assert not gh.writes("state=closed"), fault
        # (2) the merge-reconcile pass: one stderr line, flag kept, the other goal still recorded
        base, lp = _loop_world(tmp_path / fault, "r")
        src = Src()
        _manifest(base, "2006", spec.get("slices", IDS), spec.get("raw"))
        _lean(lp.work, base, "2006", "a", "11")
        tmr._started(lp, base, "0001", pr="7")
        lp._record(base, src, "2006", "review")
        lp._record(base, src, "0001", "review")
        gh = GH(fail=spec.get("fail", ())).pr(11, "sdlc/2006-sa")
        gh.pulls[7] = _row(7, "sdlc/x")
        lp.work._run = gh
        capsys.readouterr()
        out = lp._reconcile_awaiting_merges(base, CFG, source=src, run=gh, min_interval=0)
        assert out == [("0001", "done", "7")], (fault, out)
        assert "slice state unreadable for 2006" in capsys.readouterr().err, fault
        assert isinstance(lp.work._record(base, "2006").get("awaiting_merge"), dict), fault
        assert not [e for e in src.events if e[0] in ("park", "complete") and e[1] == "2006"], fault
        # (3) CLI `record done`: refused, exit 4
        monkeypatch.setattr(lp.sources, "get_source", lambda *a: src)
        assert lp.main(["loop.py", "record", base, "2006", "done"]) == 4, fault
        assert not [e for e in src.events if e[0] == "complete" and e[1] == "2006"], fault
    # R1: an unreadable marker on a MERGED slice is unknown (None) -- the continue step neither parks nor comments
    base, lp = _loop_world(tmp_path, "continue")
    src = Src()
    _manifest(base, "2006")
    _lean(lp.work, base, "2006", "a", "11", slices=_merged("2006", "a"))
    gh = GH(fail=[("issues/11/comments", RuntimeError("HTTP 401: Bad credentials"))]).pr(11, "sdlc/2006-sa")
    lp.work._run = gh
    assert lp.work.slice_done(base, CFG, "2006", "a", run=gh) is None
    assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None)
    assert not [e for e in src.events if e[0] in ("park", "note")], src.events
    assert not gh.writes("--method POST") and not _claimed(lp, base, "2006")


# --------------------------------------------------------------------------- D7: Refs / Closes (FR-36, BR-69)

def _pr_world(tmp_path, name, slice_id, done_ids, route=True):
    d = tw._sdlc(tmp_path / name, CFG)
    _manifest(d, "1642")
    _lean(tw.work, d, "1642", slice_id, pr="", route=route)
    inner = tw._pr_run(tw._default_branch("main"))
    gh = GH(fallback=inner)
    for n, i in enumerate(done_ids):
        gh.pr(11 + n, f"sdlc/1642-s{i}")
    return d, gh, inner


def test_slice_prs_write_refs_before_the_last_slice_and_closes_on_it(tmp_path, monkeypatch):
    assert getattr(tw.work, "unmerged_slices", None) is not None, "work.py has no unmerged_slices"
    labelled = []                    # FR-37 (R3/N2): a non-final slice PR writes the label through the source
    monkeypatch.setattr(tw.work, "_mark_between_slices", lambda sdlc, cfg, goal: labelled.append(goal), raising=False)
    # slice a of 3 on the DEFAULT branch: Refs, never a closing keyword
    d, gh, inner = _pr_world(tmp_path, "a", "a", [])
    assert tw.work.pr(d, CFG, "1642", run=gh) == "PR #42"
    body = tw._created_body(inner)
    assert body.startswith("Refs #1642") and _CLOSING_KEYWORD.search(body) is None, body
    assert "slice a, 1 of 3" in body, body
    assert labelled == ["1642"], labelled
    # R4: slice 2 goes through every goal-keyed pr() gate on the default config and opens its PR
    d, gh, inner = _pr_world(tmp_path, "b", "b", ["a"])
    assert tw.work.pr(d, CFG, "1642", run=gh) == "PR #42"
    assert tw._created_body(inner).startswith("Refs #1642")
    # the last unmerged slice: Closes (base is the live default branch)
    d, gh, inner = _pr_world(tmp_path, "c", "c", ["a", "b"])
    assert tw.work.pr(d, CFG, "1642", run=gh) == "PR #42"
    assert tw._created_body(inner).startswith("Closes #1642\n\n")
    assert labelled == ["1642", "1642"], labelled          # a (non-final) and b (non-final) only, never the last
    # c opened out of order while b is unmerged: Refs, whatever the manifest order says
    d, gh, inner = _pr_world(tmp_path, "c-early", "c", ["a"])
    assert tw.work.pr(d, CFG, "1642", run=gh) == "PR #42"
    body = tw._created_body(inner)
    assert body.startswith("Refs #1642") and _CLOSING_KEYWORD.search(body) is None, body


def test_pr_refuses_a_multi_pr_goal_with_no_slice_started(tmp_path):
    assert getattr(tw.work, "multi_pr", None) is not None, "work.py has no multi_pr"
    d, gh, inner = _pr_world(tmp_path, "lean", None, [])
    out = tw.work.pr(d, CFG, "1642", run=gh)
    assert "work.py slice" in out and "nothing pushed" in out, out
    assert not gh.writes("git push") and not gh.writes("pulls -f title=")
    d, gh, inner = _pr_world(tmp_path, "legacy", None, [], route=False)     # a legacy goal: one PR, as before
    assert tw.work.pr(d, CFG, "1642", run=gh) == "PR #42"


def test_switch_pr_to_refs_rewrites_the_header_and_rereads_bounded(tmp_path):
    """D-41 (owner-measured): a read right after an edit can be one edit stale. So: sleep BEFORE every read, confirm
    only on two consecutive reads without the issue, and never read more than four times."""
    switch = getattr(tw.work, "switch_pr_to_refs", None)
    assert switch is not None, "work.py has no switch_pr_to_refs"
    cases = (("stale-first", [[1642], [], []], "confirmed", 3),
             ("early-miss", [[], [1642], [], []], "confirmed", 4),       # one miss is not two in a row
             ("never", [[1642]], "unconfirmed after 4 reads", 4))
    for name, closing, expect, reads in cases:
        d = tw._sdlc(tmp_path / name, CFG)
        _manifest(d, "1642")
        _lean(tw.work, d, "1642", "c", pr="13", slices=dict(_merged("1642", "a", "b"), c={"branch": "sdlc/1642-sc"}))
        gh = GH().pr(13, "sdlc/1642-sc", state="open", approve=None)
        gh.pulls[13]["body"] = "Closes #1642\n\nfeat: slice c\n"
        gh.closing = [list(x) for x in closing]
        order = []
        gh_call = gh.__call__

        def run(cwd, argv, _c=gh_call, _o=order):
            if "graphql" in " ".join(map(str, argv)):
                _o.append("read")
            return _c(cwd, argv)
        assert switch(d, CFG, "1642", run=run, sleep=lambda s, _o=order: _o.append(("sleep", s))) == expect, name
        assert order.count("read") == reads, (name, order)                # bounded
        assert all(order[i] == ("sleep", 3) for i in range(0, len(order), 2)), (name, order)   # sleep before EACH read
        assert len(gh.writes("/pulls/13 --method PATCH")) == 1, name
        body = gh.pulls[13]["body"]
        assert body.startswith("Refs #1642") and _CLOSING_KEYWORD.search(body) is None, body
        assert body.rstrip().endswith("feat: slice c"), body
        # already Refs: nothing is written or read
        n_calls = len(gh.calls)
        assert switch(d, CFG, "1642", run=gh, sleep=lambda s: None) == "already refs"
        assert not gh.writes("PATCH")[1:] and not [c for c in gh.calls[n_calls:] if "graphql" in c]


# --------------------------------------------------------------------------- D6: the three closers

def test_merge_closes_the_issue_only_when_the_last_slice_lands(tmp_path):
    w = tw.work
    assert getattr(w, "unmerged_slices", None) is not None, "work.py has no unmerged_slices"
    d = tw._sdlc(tmp_path / "a", ALWAYS_GH)
    tw._evidence(d, "1642")
    _manifest(d, "1642")
    _lean(w, d, "1642", "a", pr="7", base="feature/x")
    gh = GH(fallback=tw._landed(issue_state="open"))
    out = w.merge(d, ALWAYS_GH, "1642", run=gh, sleep=tw.NOSLEEP)
    assert out.startswith("PR #7 merged"), out
    assert not gh.writes("state=closed"), gh.calls
    assert "#1642 stays open" in out and "b, c" in out, out
    assert w._record(d, "1642")["slices"]["a"].get("merged") is True                     # R3
    merged = [r for r in w._log_rows(d, "1642") if r.get("kind") == "merged"]
    assert merged and merged[-1].get("thread") == "a", merged
    # the last slice lands with a and b done: the issue is closed
    d = tw._sdlc(tmp_path / "c", ALWAYS_GH)
    tw._evidence(d, "1642")
    _manifest(d, "1642")
    _lean(w, d, "1642", "c", pr="7", base="feature/x",
          slices=dict(_merged("1642", "a", "b"), c={"branch": "sdlc/1642-sc"}))
    # the last slice merged WITHOUT a trusted approve on its own PR (review_gate is off by default): no close
    gh = GH(fallback=tw._landed(issue_state="open")).pr(11, "sdlc/1642-sa").pr(12, "sdlc/1642-sb")
    gh.comments[7] = []
    out = w.merge(d, ALWAYS_GH, "1642", run=gh, sleep=tw.NOSLEEP)
    assert out.startswith("PR #7 merged") and "stays open" in out, out
    assert not gh.writes("state=closed"), gh.calls
    # ...with its trusted approve, the last slice landing closes the issue
    _lean(w, d, "1642", "c", pr="7", base="feature/x",
          slices=dict(_merged("1642", "a", "b"), c={"branch": "sdlc/1642-sc"}))
    gh = GH(fallback=tw._landed(issue_state="open")).pr(11, "sdlc/1642-sa").pr(12, "sdlc/1642-sb")
    gh.comments[7] = [{"body": "sigma:approve", "author": {"login": "sigma"}, "authorAssociation": "MEMBER"}]
    out = w.merge(d, ALWAYS_GH, "1642", run=gh, sleep=tw.NOSLEEP)
    assert out.startswith("PR #7 merged"), out
    assert gh.writes("state=closed") == ["gh api -X PATCH repos/{owner}/{repo}/issues/1642 -f state=closed"], out
    merged = [r for r in w._log_rows(d, "1642") if r.get("kind") == "merged"]
    assert merged and merged[-1].get("thread") == "c", merged


def test_reconcile_keeps_a_non_final_slice_open_and_records_done_on_the_last(tmp_path):
    base, lp = _loop_world(tmp_path)
    assert getattr(lp.work, "unmerged_slices", None) is not None, "work.py has no unmerged_slices"
    src = Src()
    _manifest(base, "2006")
    _lean(lp.work, base, "2006", "a", "11")
    lp._record(base, src, "2006", "review")
    gh = GH().pr(11, "sdlc/2006-sa")
    lp.work._run = gh
    out = lp._reconcile_awaiting_merges(base, CFG, source=src, run=gh, min_interval=0)
    assert out == [("2006", "slice-merged", "11")], out
    assert not [e for e in src.events if e[0] in ("complete", "park")], src.events
    rec = lp.work._record(base, "2006")
    assert rec and "awaiting_merge" not in rec and rec["slice"] == "a", rec       # record and claim kept
    assert rec["slices"]["a"].get("merged") is True
    assert ("between_slices", "2006") in src.events
    assert not gh.writes("state=closed")
    merged = [r for r in lp.actionlog.read_goal(base, "2006") if r.get("kind") == "merged"]
    assert merged and merged[-1].get("thread") == "a", merged
    # the last slice merges: done is recorded and the issue closed
    _lean(lp.work, base, "2006", "c", "13", slices=dict(_merged("2006", "a", "b"), c={"branch": "sdlc/2006-sc"}))
    lp._record(base, src, "2006", "review")
    gh.pr(12, "sdlc/2006-sb").pr(13, "sdlc/2006-sc")
    out = lp._reconcile_awaiting_merges(base, CFG, source=src, run=gh, min_interval=0)
    assert out == [("2006", "done", "13")], out
    assert ("complete", "2006") in src.events


def test_record_done_and_the_run_driver_are_refused_while_a_slice_is_unmerged(tmp_path, capsys, monkeypatch):
    base, lp = _loop_world(tmp_path)
    assert getattr(lp.work, "SLICES_PENDING", None), "work.done_refusal has no multi-PR gate"
    src = Src()
    monkeypatch.setattr(lp.sources, "get_source", lambda *a: src)
    _manifest(base, "2006")
    _lean(lp.work, base, "2006", "a", "11")
    gh = GH().pr(11, "sdlc/2006-sa")
    lp.work._run = gh
    for phase in ("slice a merged", "slice b cut, no PR, no diff"):      # B1: the second used to fail open
        if phase.startswith("slice b"):
            _lean(lp.work, base, "2006", "b", "", slices=dict(_merged("2006", "a"), b={"branch": "sdlc/2006-sb"}))
            pathlib.Path(lp.work._record(base, "2006")["worktree"]).mkdir(parents=True, exist_ok=True)
        capsys.readouterr()
        assert lp.main(["loop.py", "record", base, "2006", "done"]) == 4, phase
        err = capsys.readouterr().err
        assert "REFUSED" in err and "FR-63" in err and "work.py slice" in err, (phase, err)
        assert ("b, c" if phase.startswith("slice a") else "b, c") in err, (phase, err)
        assert not [e for e in src.events if e[0] == "complete"] and not gh.writes("state=closed"), phase
        assert lp.work.done_refusal(base, CFG, "2006", run=gh).startswith(lp.work.SLICES_PENDING), phase
    # the run driver: a goal its worker reports `done` is never recorded done while slices remain
    served = []

    def fake_next(sdlc_dir, source, config, extra_skip=(), **k):
        if "2006" in extra_skip or served:
            return ("DONE", None)
        served.append(1)
        return ("goal", "2006")
    monkeypatch.setattr(lp, "_next", fake_next)
    out = lp.run_loop(base, lambda g: ("done", ""))
    assert out["done"] == 0 and out.get("between_slices") == 1, out
    assert not [e for e in src.events if e[0] == "complete"]
    assert lp.work._record(base, "2006"), "the goal keeps its work record for the continue step"
    # ...and with the current slice's PR open it is recorded `review`
    _lean(lp.work, base, "2006", "b", "12", slices=dict(_merged("2006", "a"), b={"branch": "sdlc/2006-sb"}))
    gh.pr(12, "sdlc/2006-sb", state="open", approve=None)
    served.clear()
    out = lp.run_loop(base, lambda g: ("done", ""))
    assert out["done"] == 0 and out["review"] == 1, out
    # every slice merged: done passes (the gate does not over-block)
    _lean(lp.work, base, "2006", "c", "13", slices=dict(_merged("2006", "a", "b"), c={"branch": "sdlc/2006-sc"}))
    gh.heads.pop("sdlc/2006-sb")                                   # slice b's PR has since merged
    gh.pr(12, "sdlc/2006-sb").pr(13, "sdlc/2006-sc")
    assert lp.main(["loop.py", "record", base, "2006", "done"]) == 0, capsys.readouterr().err
    assert ("complete", "2006") in src.events


def test_resume_between_slices_never_rebases_a_merged_slice_branch(tmp_path):
    w = tw.work
    hold = getattr(w, "_merged_slice_hold", None)
    assert hold is not None, "work.py has no merged-slice hold"
    d = tw._sdlc(tmp_path, CFG)
    _manifest(d, "1642")
    rec = _lean(w, d, "1642", "a", pr="11", base="feature/x")
    for state_now, held in (("merged", True), ("open", False)):
        gh = GH().pr(11, "sdlc/1642-sa", state=state_now)

        def run(cwd, argv, _gh=gh):
            line = " ".join(map(str, argv))
            if argv[0] == "git":
                _gh.calls.append(line)
                return "3" if "rev-list" in line else ""         # 3 behind: a rebase WOULD run
            return _gh(cwd, argv)
        out = w.start(d, CFG, "1642", run=run)
        assert ("work.py slice" in out and "nothing rebased" in out) is held, (state_now, out)
        rebased = [c for c in gh.calls if c.startswith("git rebase") or c.startswith("git push")]
        if held:
            assert not rebased, gh.calls
            assert w._record(d, "1642"), "a held resume never unclaims"
            stale = w.ensure_fresh(d, CFG, "1642", run=run)
            assert stale and stale.startswith(w.MERGED_SLICE_HOLD), stale       # verify exits 4 on it
            assert not [c for c in gh.calls if c.startswith("git rebase")]
        else:
            assert "nothing rebased" not in out


def test_work_off_runs_a_multi_slice_goal_as_one_unit_and_records_done(tmp_path, monkeypatch):
    base, lp = _loop_world(tmp_path, config=OFF)
    assert getattr(lp.work, "multi_pr", None) is not None, "work.py has no multi_pr"
    src = Src()
    monkeypatch.setattr(lp.sources, "get_source", lambda *a: src)
    _manifest(base, "2006")
    _lean(lp.work, base, "2006", slice_id=None, pr="")
    gh = GH()
    lp.work._run = gh
    assert lp.work.multi_pr(base, OFF, "2006") is False
    assert lp.work.unmerged_slices(base, OFF, "2006", run=gh) == [] and gh.calls == []
    assert lp.main(["loop.py", "record", base, "2006", "done"]) == 0
    assert ("complete", "2006") in src.events
    text = _load("next_step").render(base, OFF, "2006")
    assert "slices: work.enabled is off -- run the goal as one unit, slices in manifest order" in text, text


# --------------------------------------------------------------------------- D9/D10: continue own goal (FR-84, FR-87)

def _between(tmp_path, name, pr_state="merged", approve="OWNER", goal="2006", labels=None):
    base, lp = _loop_world(tmp_path, name)
    _manifest(base, goal)
    _lean(lp.work, base, goal, "a", "11", slices=_merged(goal, "a"))
    gh = GH().pr(11, f"sdlc/{goal}-sa", state=pr_state, approve=approve)
    lp.work._run = gh
    return base, lp, gh, Src(labels=labels)


def test_continue_own_goal_is_picked_first_and_skips_held_goals(tmp_path, monkeypatch):
    base, lp, gh, src = _between(tmp_path, "pick")
    assert hasattr(lp, "_continue_own_lean_goal"), "loop.py has no continue-own-goal step"
    assert lp._next(base, src, CFG, session_pid="1") == ("goal", "2006")
    assert src.pending_calls == 0                                   # before next_pending
    assert ("between_slices", "2006") in src.events and ("mark_in_progress", "2006") not in src.events
    assert _claimed(lp, base, "2006")
    # held 1: the current slice's PR is still open (slice a merged and approved, slice b's PR open)
    base, lp, gh, src = _between(tmp_path, "open")
    _lean(lp.work, base, "2006", "b", "12", slices=dict(_merged("2006", "a"), b={"branch": "sdlc/2006-sb"}))
    gh.pr(12, "sdlc/2006-sb", state="open", approve=None)
    assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None)
    assert src.pending_calls == 1 and not _claimed(lp, base, "2006")
    # held 2: a live session on this machine has it in flight
    base, lp, gh, src = _between(tmp_path, "session")
    monkeypatch.setattr(lp, "_session_in_flight_goals", lambda *a: {"2006"})
    assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None)
    assert src.pending_calls == 1 and not _claimed(lp, base, "2006")
    # held 3: another actor holds the ledger lease
    base, lp, gh, src = _between(tmp_path, "lease")
    monkeypatch.setattr(lp, "_lease", lambda *a: ("me", "w-me", {"2006": ("bob", "w-bob")}))
    assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None)
    assert src.pending_calls == 1 and not _claimed(lp, base, "2006")
    # bounded: one candidate confirmed on GitHub per pick, none re-read inside MERGE_RECHECK_SECONDS
    base, lp, gh, src = _between(tmp_path, "bound", pr_state="open")
    _manifest(base, "2007")
    _lean(lp.work, base, "2007", "a", "21", slices=_merged("2007", "a"))
    gh.pr(21, "sdlc/2007-sa", state="open")
    for expect in (["2006"], ["2006", "2007"], ["2006", "2007"]):
        lp._next(base, src, CFG, session_pid="1")
        assert src.issue_reads == expect, src.issue_reads


def test_continue_never_reclaims_a_parked_between_slices_goal(tmp_path):
    # sub-fixture A: the park's labels landed, no recorded row yet -- the issue read alone excludes it
    base, lp, gh, src = _between(tmp_path, "labels", labels={"2006": {"sdlc:parked"}})
    assert hasattr(lp, "_continue_own_lean_goal"), "loop.py has no continue-own-goal step"
    for _ in range(2):                                              # a second pick inside the recheck window
        assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None)
    lp.work._save(base, "2006", dict(lp.work._record(base, "2006"), continue_checked_at=0))
    assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None)   # ...and after it
    assert src.issue_reads and src.pending_calls == 3
    assert not [e for e in src.events if e[0] in ("park", "between_slices", "mark_in_progress", "note")]
    assert not _claimed(lp, base, "2006") and not gh.writes("--method POST")
    # sub-fixture B: the recorded row is there, the labels are not updated yet -- the row alone excludes it
    base, lp, gh, src = _between(tmp_path, "row")
    lp.actionlog.safe_append(base, "2006", "recorded", result="parked", detail="a human decides")
    assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None)
    assert src.issue_reads == [] and not _claimed(lp, base, "2006")
    assert not [e for e in src.events if e[0] in ("park", "between_slices", "mark_in_progress")]


def test_continue_records_done_when_every_slice_merged_and_the_issue_is_open(tmp_path):
    base, lp, gh, src = _between(tmp_path, "all")
    assert hasattr(lp, "_continue_own_lean_goal"), "loop.py has no continue-own-goal step"
    _lean(lp.work, base, "2006", "c", "13", slices=_merged("2006", "a", "b", "c"))
    gh.pr(12, "sdlc/2006-sb").pr(13, "sdlc/2006-sc")
    assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None)
    assert ("complete", "2006") in src.events
    assert not _claimed(lp, base, "2006")


def test_a_slice_merged_without_a_trusted_marker_parks_needs_decision(tmp_path):
    # the reconcile pass (D8 / X9)
    base, lp = _loop_world(tmp_path, "reconcile")
    assert getattr(lp.work, "unmerged_slices", None) is not None, "work.py has no unmerged_slices"
    src = Src()
    _manifest(base, "2006")
    _lean(lp.work, base, "2006", "a", "11")
    lp._record(base, src, "2006", "review")
    gh = GH().pr(11, "sdlc/2006-sa", approve="CONTRIBUTOR")
    lp.work._run = gh
    out = lp._reconcile_awaiting_merges(base, CFG, source=src, run=gh, min_interval=0)
    assert out == [("2006", "parked", "11")], out
    parks = [e for e in src.events if e[0] == "park"]
    assert len(parks) == 1 and "without a trusted sigma:approve" in parks[0][2] and "needs_decision" in parks[0][2]
    assert not [e for e in src.events if e[0] == "complete"]
    # the continue step: parks once, never again
    base, lp, gh, src = _between(tmp_path, "continue", approve=None)
    assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None)
    lp.work._save(base, "2006", dict(lp.work._record(base, "2006") or {}, continue_checked_at=0))
    assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None)
    parks = [e for e in src.events if e[0] == "park"]
    assert len(parks) == 1 and "without a trusted sigma:approve" in parks[0][2], src.events
    assert not _claimed(lp, base, "2006")


# --------------------------------------------------------------------------- D11: the overlay (FR-37, FR-75)

def _source(run=None):
    src = ts._mod("sources")
    return src, src.GitHubSource({"discovery": {"source": "github", "github": {"repo": "o/r"}}},
                                 run=run or ts._recording_runner())


def test_between_slices_overlay_keeps_in_progress_and_never_changes_eligibility(tmp_path):
    issues = [{"number": 5, "labels": [{"name": "sdlc:goal"}, {"name": BETWEEN}], "assignees": []},
              {"number": 6, "labels": [{"name": "sdlc:goal"}, {"name": "sdlc:in-progress"}, {"name": BETWEEN}],
               "assignees": []}]
    mod, gh = _source(ts._recording_runner({"list": json.dumps(issues)}))
    assert getattr(gh, "between_slices_label", None) == BETWEEN, "GitHubSource has no between_slices_label"
    assert BETWEEN not in gh.not_eligible_labels() and BETWEEN not in gh.overlay_labels()
    gh._BACKLOG_READ_RETRY_BASE = 0
    assert [i["number"] for i in gh._fetch_pending([], ())[1]] == [5]   # in-progress alone excludes (PC-45)
    swaps = []
    gh._swap_labels_best_effort = lambda goal, add=(), remove=(), what="", **k: swaps.append(
        (str(goal), list(add), list(remove))) or True
    gh.mark_between_slices("6")
    assert swaps == [("6", [BETWEEN], [])]                                # adds, removes nothing
    assert mod.LocalSource(str(tmp_path / ".sdlc")).mark_between_slices("6") is None


def test_close_paths_and_reconciler_strip_the_between_slices_label(tmp_path):
    mod, gh = _source()
    assert getattr(gh, "between_slices_label", None) == BETWEEN, "GitHubSource has no between_slices_label"
    swaps, removed = [], []
    gh._swap_labels_best_effort = lambda goal, add=(), remove=(), what="", **k: swaps.append(
        (what, list(add), list(remove))) and False
    gh._read_issue = lambda goal, fields, **k: {"state": "CLOSED", "labels": [], "body": ""}
    gh.note = lambda goal, text: None
    gh._set_board_status = lambda *a, **k: True
    gh._archive_card = lambda *a, **k: None
    gh._repair_partial_park = lambda goal: None
    gh._labels_ready = True
    gh._issue_add_labels = lambda n, labels: None
    gh._issue_remove_label = lambda n, label: removed.append(label)
    gh.complete("5")
    assert BETWEEN in [r for w, a, rm in swaps if w == "complete" for r in rm], swaps
    gh.park("5", "a human decides")
    assert BETWEEN in [r for w, a, rm in swaps if w == "park" for r in rm], swaps            # F5
    assert BETWEEN in removed                                     # R2: the park's REST fallback strips it too
    pm = tp._mod("promote")
    res = pm.promote(tp._sdlc(tmp_path / "p"), tp._config(), ["5"], apply=False, demote=True,
                     run=tp._runner(views={"5": tp._view("sdlc:goal", "sdlc:in-progress", BETWEEN)}))
    assert ("-" + BETWEEN) in res["results"][0]["detail"], res
    rc = _load("reconcile")
    overlays = rc._overlay_labels(gh)
    assert rc._stale_on_closed({"labels": [{"name": BETWEEN}]}, (), overlays, "sdlc:goal") == [BETWEEN]
    # a repo seeded before this label existed: the close/park removal of it is already satisfied, never a failed
    # swap (an ADD of an unknown label still refuses -- Sigma never mints a label in a swap)
    mod, real = _source()
    docs = []
    real._issue_node_id = lambda issue: "I_kwDOabc"
    real._label_node_ids = lambda names: {"sdlc:in-progress": "LA_kwDOip"}
    real._graphql = docs.append
    assert real._swap_labels("5", remove=["sdlc:in-progress", BETWEEN]) is True
    assert len(docs) == 1 and "LA_kwDOip" in docs[0] and "removeLabelsFromLabelable" in docs[0], docs
    try:
        real._swap_labels("5", add=[BETWEEN])
        raised = False
    except RuntimeError:
        raised = True
    assert raised


def test_status_and_dag_show_between_slices_without_changing_pending(tmp_path, capsys):
    st = tst._status()
    counts = {frozenset({"sdlc:goal"}): 3, frozenset({"sdlc:goal", "sdlc:in-progress"}): 2,
              frozenset({"sdlc:goal", "sdlc:in-progress", BETWEEN}): 1}

    def run(args):
        if not tst._is_issues_list_call(args) or "page=1" not in args:
            return "[]"
        return json.dumps([{"number": i} for i in range(counts.get(frozenset(tst._rest_labels(args)), 0))])
    got = st._github_counts({"repo": "acme/app"}, run)
    assert got.get("between_slices") == 1, got
    assert got["pending"] == 1 and got["in_progress"] == 2                 # a subset of in-progress
    sdlc = tmp_path / "s" / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    line = {"proposed": 0, "pending": 1, "in_progress": 2, "done": 0, "parked": 0, "failed": 0, "iteration": 0,
            "queue_nonempty": False, "ledger_entries": 0, "goals_since_align": 0, "goals_since_audit": 0,
            "merge_queue_advice": None}
    for n, expect in ((1, "2 in-progress (1 between slices)"), (0, None)):
        st.summary = lambda *a, n=n, **k: dict(line, between_slices=n)
        capsys.readouterr()
        st.main(["status.py", str(sdlc)])
        out = capsys.readouterr().out
        assert (expect in out) if expect else ("between slices" not in out), out
    dc, ds = tdc.dc, tdc.ds
    assert "between_slices" in ds.NODE_KEYS
    fake = tdc.Fake([tdc._issue(5, ("sdlc:goal", "sdlc:in-progress", BETWEEN)), tdc._issue(6, ("sdlc:goal",))])
    rep = dc.collect(str(tmp_path / "s"), tdc.CONFIG, run=fake, now=tdc.NOW)
    nodes = {n["number"]: n for n in rep["map"]["nodes"]}
    assert nodes[5].get("between_slices") is True and nodes[5]["life"] == "in-progress"
    assert "between_slices" not in nodes[6]
    assert ds._clean_node(nodes[5]).get("between_slices") is True
    # ...and the DAG page shows it (FR-75): the node's Life row reads "in-progress, between slices"
    import test_dag_html as tdh
    store = tdh._store(tmp_path / "h", [tdh._node(5, "in-progress") | {"between_slices": True}, tdh._node(6),
                                        tdh._node(7)], [(5, 6), (7, 6)])
    model = tdh._model(store, 6)
    lives = {n: d["life"] for n, d in model["nodes"].items()}
    assert lives[5] == "in-progress, between slices" and lives[7] == "goal", lives


# --------------------------------------------------------------------------- D15/D15b: finish and prune

def test_finish_deletes_every_slice_branch_and_prune_keeps_a_between_slices_worktree(tmp_path):
    w = tw.work
    assert getattr(w, "multi_pr", None) is not None, "work.py has no multi_pr"
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text(".sdlc/\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    sdlc = root / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps(CFG))
    for b in ("sdlc/2006", "sdlc/2006-sa", "sdlc/2006-sb"):
        _git(root, "branch", b)
    wt = sdlc / "work" / "2006"
    _git(root, "worktree", "add", "-q", "-b", "sdlc/2006-sc", str(wt))
    _manifest(str(sdlc), "2006")
    w._save(str(sdlc), "2006", {"worktree": str(wt), "branch": "sdlc/2006-sc", "base": "main", "remote": "origin",
                                "pr": "13", "goal_branch": "sdlc/2006", "slice": "c", "route": "lean/1",
                                "slices": dict(_merged("2006", "a", "b"), c={"branch": "sdlc/2006-sc"})})
    gh = GH(fallback=lambda cwd, argv: w._run(cwd, argv) if argv[0] == "git" else "")
    w.finish(str(sdlc), CFG, "2006", run=gh, merged=True)
    assert _git(root, "branch", "--list", "sdlc/*") == ""                 # no goal or slice branch left
    for b in ("sdlc/2006-sa", "sdlc/2006-sb", "sdlc/2006-sc"):
        assert [c for c in gh.writes("DELETE") if c.endswith(f"refs/heads/{b}")], (b, gh.calls)
    assert not [c for c in gh.calls if c.endswith("refs/heads/sdlc/2006")]   # the goal branch was never pushed
    # R3 prune: a worktree between slices is kept until every manifest slice is marked merged locally
    wp = _load("worktree_prune")
    d = tmp_path / "p" / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(CFG))
    pwt = d / "work" / "2006"
    pwt.mkdir(parents=True)
    _manifest(str(d), "2006")

    def run(cwd, argv, timeout=None, stdin=None):
        line = " ".join(str(a) for a in argv)
        if "--show-toplevel" in line:
            return 0, str(pwt.resolve()) + "\n"
        if "symbolic-ref" in line:
            return 0, "sdlc/2006-sc\n"
        if "status --porcelain" in line or "--others" in line or "merge-base" in line:
            return 0, ""
        if "ls-files -v" in line:
            return 0, "H README\n"
        if "rev-parse HEAD" in line:
            return 0, "a" * 40 + "\n"
        if "pulls/13" in line:
            return 0, json.dumps({"merged": True, "merged_at": "2026-10-25T00:00:00Z", "state": "closed",
                                  "head": {"ref": "sdlc/2006-sc", "sha": "b" * 40, "repo": {"full_name": "a/b"}},
                                  "base": {"ref": "main", "repo": {"full_name": "a/b"}}})
        return 1, ""
    rec = {"worktree": str(pwt), "branch": "sdlc/2006-sc", "base": "main", "remote": "origin", "pr": "13",
           "slice": "c", "route": "lean/1"}
    for marked, kept in ((("a",), True), (("a", "b"), True), (("a", "b", "c"), False)):
        w._save(str(d), "2006", dict(rec, slices=_merged("2006", *marked)))
        res = wp.sweep(str(d), config=CFG, dry_run=True, run=run, limit=0, max_examine=0, max_pr_reads=0)
        reasons = [k["reason"] for k in res["kept"] if k["goal"] == "2006"]
        if kept:
            assert reasons == ["between-slices"], (marked, res)
        else:
            assert [r["goal"] for r in res["removable"]] == ["2006"], (marked, res)

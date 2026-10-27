"""#2007 (lean goals slice 5a; design 2000.md FR-64/66/74/79/80/90/93/94, D-51/52/54/56/58): GitHub goal evidence
and resume from any machine.

A multi-PR lean goal publishes ONE trusted `<!-- sigma:goal-evidence {json} -->` comment when its first slice push
carrying an approved plan lands (and again on a later approving plan-review once a slice was pushed). A machine with
no local work record reads it only through `sources.trusted_marker_comments` (fail closed), bumps the claim epoch,
and `work.py start` imports plan, acceptance and manifest as RAW bytes from the open slice PR head or else
`<remote>/<base>`, verified against the published sha256s and scope fingerprint, into `state/imported/<stem>/` --
never the tracked `.sdlc/plans|acceptance`. `work.py slice` resumes at the first slice without a merged approved PR,
reusing its open PR branch; `loop.py record done` accepts the imported evidence plus live GitHub slice evidence.

Hermetic: GitHub is the scripted `GH` (no network); git is a real tmp repo with a bare `origin` where a test reads
refs. Every seam is reached defensively (`getattr`), so on the pre-slice code every node fails by an assertion."""
import hashlib
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import test_multi_pr_lifecycle as mpl  # noqa: E402
import test_work as tw  # noqa: E402
from test_multi_pr_lifecycle import CFG, GH, OFF, Src, _claimed, _git, _lean, _loop_world, _manifest, _merged  # noqa: E402

w = tw.work
slices = mpl._load("slices")
GOAL = "2007"
MANIFEST = [{"id": "a", "title": "A", "files": ["src/a.py"]}, {"id": "b", "title": "B"}, {"id": "c", "title": "C"}]
PLAN = b"# plan 2007\r\n\r\nsteps\r\n"
PLAN_V1 = b"# plan 2007 v1\n"
ACCEPT = ('---\nkind: "acceptance"\ngoal: "2007"\nverify_command: null\ndrafted: false\n---\n## Done when\n'
          "- [ ] one\n- [ ] two\n- [ ] three\n").encode()
GITIGNORE = ".sdlc/*\n!.sdlc/plans/\n!.sdlc/acceptance/\n!.sdlc/research/\n"
NOLOG = dict(CFG, action_log={"enabled": False})
GATES = {"hard_plan_gate": True, "plan_review": True, "phase_record": {"enabled": True}}
E1_KEYS = {"v", "goal", "research", "plan", "ref", "plan_sha256", "acceptance_sha256", "verdict",
           "scope_fingerprint", "epoch", "generation", "profile"}
_EV = {"v": 1, "goal": GOAL, "research": None, "plan": ".sdlc/plans/2007.md", "ref": "sdlc/2007-sa",
       "plan_sha256": "1" * 64, "acceptance_sha256": None, "verdict": "pass", "scope_fingerprint": "f" * 64,
       "epoch": 1, "generation": 1, "profile": "standard"}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _fp(manifest):
    return slices.scope_fingerprint(manifest)


def _git_only(cwd, argv):
    return w._run(cwd, argv) if argv[0] == "git" else ""


def _posts(gh):
    return [c.split("-f body=", 1)[1] for c in gh.calls if "issues/2007/comments --method POST" in c]


def _payload(body):
    return json.loads(re.search(r"<!-- sigma:goal-evidence (\{.*?\}) -->", body, re.S).group(1))


# --------------------------------------------------------------------------- worlds

def _repo(tmp_path, base="main", gitignore=GITIGNORE, config=CFG):
    origin, root = tmp_path / "origin.git", tmp_path / "repo"
    tmp_path.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text(gitignore)
    (root / "README").write_text("x\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "origin", "main")
    cfg = json.loads(json.dumps(config))
    if base != "main":
        _git(root, "push", "-q", "origin", f"main:{base}")
        cfg["work"]["base"] = base
    _git(root, "fetch", "-q", "origin")
    sdlc = root / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps(cfg))
    return root, str(sdlc), cfg


def _push_commit(root, start, target, files):
    """One commit carrying `files` on top of `start`, pushed to origin/<target>; no local branch and no
    remote-tracking ref is left behind (the resumer must fetch it)."""
    _git(root, "checkout", "-q", "-b", "tmp-push", start)
    for rel, data in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", f"push {target}")
    _git(root, "push", "-q", "origin", f"tmp-push:{target}")
    _git(root, "checkout", "-q", "main")
    _git(root, "branch", "-q", "-D", "tmp-push")
    subprocess.run(["git", "update-ref", "-d", f"refs/remotes/origin/{target}"], cwd=root, capture_output=True)


def _slice_files(plan=PLAN, manifest=MANIFEST):
    return {".sdlc/plans/2007.md": plan, ".sdlc/acceptance/2007.md": ACCEPT,
            ".sdlc/plans/2007.slices.json": json.dumps(manifest).encode(), "src/b.py": b"b = 1\n"}


def _resume_world(tmp_path, base="main", plan=PLAN, pr_state="open", base_files=None, slice_id="b", config=CFG):
    """A machine with NO local record: origin/<base> (optionally holding `base_files`) and the open slice PR head
    `origin/sdlc/2007-s<slice_id>` holding the approved plan, acceptance and manifest. GitHub: slice a merged with a
    trusted approve (unless slice a IS the open one), the slice PR in `pr_state`."""
    root, d, cfg = _repo(tmp_path, base, config=config)
    if base_files:
        _push_commit(root, f"origin/{base}", base, base_files)
        _git(root, "fetch", "-q", "origin")
    ref = f"sdlc/2007-s{slice_id}"
    _push_commit(root, f"origin/{base}", ref, _slice_files(plan))
    gh = GH(fallback=_git_only)
    if slice_id != "a":
        gh.pr(11, "sdlc/2007-sa")
    if pr_state:
        gh.pr(12, ref, state=pr_state, approve=None)
    ev = dict(_EV, ref=ref, plan_sha256=_sha(plan), acceptance_sha256=_sha(ACCEPT), scope_fingerprint=_fp(MANIFEST),
              comment="IC_7")
    return root, d, cfg, gh, ev


def _owner(tmp_path, monkeypatch, base="main", gitignore=GITIGNORE, branch_plan=PLAN, manifest_on_branch=True,
           acceptance_on_branch=True, root_manifest=MANIFEST, verdict="pass", fold=None, config=CFG):
    """The owning machine after `work.py slice` cut slice a: plan, manifest and acceptance in the MAIN checkout (where
    Plan writes them), the slice-a commit in the worktree, an approving gates record bound to PLAN."""
    root, d, cfg = _repo(tmp_path, base, gitignore=gitignore, config=config)
    wt = pathlib.Path(d) / "work" / GOAL
    _git(root, "worktree", "add", "-q", "-b", "sdlc/2007-sa", str(wt))
    (pathlib.Path(d) / "plans").mkdir()
    (pathlib.Path(d) / "plans" / "2007.md").write_bytes(PLAN)
    _manifest(d, GOAL, root_manifest)
    (pathlib.Path(d) / "acceptance").mkdir()
    (pathlib.Path(d) / "acceptance" / "2007.md").write_bytes(ACCEPT)
    files = {".sdlc/plans/2007.md": branch_plan, "src/a.py": b"a = 1\n"}
    if manifest_on_branch:
        files[".sdlc/plans/2007.slices.json"] = json.dumps(root_manifest).encode()
    if acceptance_on_branch:
        files[".sdlc/acceptance/2007.md"] = ACCEPT
    for rel, data in files.items():
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_bytes(data)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "slice a")
    w._save(d, GOAL, {"worktree": str(wt), "branch": "sdlc/2007-sa", "base": base, "remote": "origin", "pr": "",
                      "route": "lean/1", "profile": "standard", "profile_source": "agent", "rationale": "r",
                      "slice": "a", "slices": {"a": {"branch": "sdlc/2007-sa"}}, "goal_branch": "sdlc/2007"})
    gates = {"goal": GOAL, "plan": ".sdlc/plans/2007.md", "plan_hash": _sha(PLAN), "verdict": verdict,
             "route": "lean/1"}
    if fold:
        gates["fold"] = fold
    target = w.plan_review_record_path(d, GOAL)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(gates))
    monkeypatch.setattr(w, "_mark_between_slices", lambda *a: None, raising=False)

    def fallback(cwd, argv):
        if argv[0] == "git":
            return w._run(cwd, argv)
        line = " ".join(str(a) for a in argv)
        if "/comments --method POST" in line:
            return json.dumps({"id": 900, "html_url": "https://example.invalid/c/900"})
        if "pulls -f title=" in line:
            return "42"
        if "--jq .default_branch" in line:
            return "main"
        return ""
    return root, d, cfg, wt, GH(fallback=fallback)


def _evidence_comment(payload, assoc="OWNER", created="2026-01-01T00:00:01Z", cid="IC_1", prefix="sigma"):
    c = {"id": cid, "author": {"login": "sigma"}, "createdAt": created,
         "body": f"<!-- {prefix}:goal-evidence {json.dumps(payload)} -->\nSigma goal evidence"}
    if assoc is not None:
        c["authorAssociation"] = assoc
    return c


def _epoch_comment(epoch, assoc="OWNER", created="2026-01-01T00:00:05Z", cid="IC_E"):
    return {"id": cid, "author": {"login": "sigma"}, "createdAt": created, "authorAssociation": assoc,
            "body": '<!-- sigma:epoch {"v":1,"goal":"2007","epoch":%d,"reason":"resume"} -->' % epoch}


def _imported(d):
    return pathlib.Path(d) / "state" / "imported" / GOAL


# --------------------------------------------------------------------------- Q1: the scope fingerprint

def test_scope_fingerprint_ignores_status_and_covers_scope_fields():
    fp = getattr(slices, "scope_fingerprint", None)
    assert fp is not None, "slices.py has no scope_fingerprint"
    base = [{"id": "a", "title": "A", "needs": [], "files": ["x"], "size": "S", "status": "todo"},
            {"id": "b", "status": "todo"}]
    same = [{"status": "done", "size": "S", "files": ["x"], "needs": [], "title": "A", "id": "a"}, {"id": "b"}]
    assert fp(base) == fp(same)                                   # status and key order are not scope
    assert re.fullmatch(r"[0-9a-f]{64}", fp(base))
    for key, value in (("title", "Z"), ("needs", ["b"]), ("files", ["y"]), ("size", "L"), ("tests", ["t"])):
        changed = json.loads(json.dumps(base))
        changed[0][key] = value
        assert fp(changed) != fp(base), key
    try:
        fp({"id": "a"})
        raised = False
    except ValueError:
        raised = True
    assert raised


# --------------------------------------------------------------------------- FR-79: publication

def test_first_slice_push_with_an_approved_plan_publishes_one_trusted_evidence_comment(tmp_path, monkeypatch, capsys):
    assert callable(getattr(w, "publish_goal_evidence", None)) and getattr(slices, "scope_fingerprint", None), \
        "work.py has no goal-evidence publisher"
    root, d, cfg, wt, gh = _owner(tmp_path / "ok", monkeypatch)
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #42")
    posts = _posts(gh)
    assert len(posts) == 1, gh.calls
    assert posts[0].startswith("<!-- sigma:goal-evidence "), posts[0]
    ev = _payload(posts[0])
    assert set(ev) == E1_KEYS, ev
    assert ev["plan_sha256"] == _sha(PLAN) and ev["scope_fingerprint"] == _fp(MANIFEST), ev
    assert (ev["epoch"], ev["generation"], ev["ref"], ev["verdict"]) == (1, 1, "sdlc/2007-sa", "pass"), ev
    assert ev["plan"] == ".sdlc/plans/2007.md" and ev["acceptance_sha256"] == _sha(ACCEPT), ev
    assert ev["profile"] == "standard" and ev["goal"] == GOAL, ev
    assert (w._record(d, GOAL).get("evidence") or {}).get("digest"), "the publication is stamped"
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #")         # idempotent: same payload, no second comment
    assert len(_posts(gh)) == 1, gh.calls
    # R3: an edit to the MAIN-checkout acceptance record does not change the published sha (the branch bytes count)
    (pathlib.Path(d) / "acceptance" / "2007.md").write_bytes(ACCEPT + b"- [ ] four\n")
    rec = w._record(d, GOAL)
    rec.pop("evidence")
    w._save(d, GOAL, rec)
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #")
    assert len(_posts(gh)) == 2 and _payload(_posts(gh)[1])["acceptance_sha256"] == _sha(ACCEPT)
    # R5: the record's epoch is published (none -> 1)
    payload, _why = w._evidence_payload(d, cfg, GOAL, dict(w._record(d, GOAL), epoch=3), gh)
    assert payload and payload["epoch"] == 3, _why
    # a failing POST never refuses the PR, says so on stderr, stamps nothing; the next pr() publishes
    root, d, cfg, wt, gh = _owner(tmp_path / "fail", monkeypatch)
    err = RuntimeError("HTTP 502: Bad Gateway")
    gh.fail = [("/comments --method POST", err), ("issue comment", err)]
    capsys.readouterr()
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #42")
    assert "not published" in capsys.readouterr().err
    assert not w._record(d, GOAL).get("evidence")
    gh.fail, tried = [], len(_posts(gh))                          # GH records the refused attempt too
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #")
    assert len(_posts(gh)) == tried + 1 and w._record(d, GOAL).get("evidence")


def test_no_evidence_is_published_before_a_push_or_without_an_approved_committed_plan(tmp_path, monkeypatch, capsys):
    assert callable(getattr(w, "publish_goal_evidence", None)), "work.py has no goal-evidence publisher"
    # (i) an approving plan-review before any push publishes nothing (control 2000.md:1184)
    root, d, cfg, wt, gh = _owner(tmp_path / "i", monkeypatch, config=NOLOG)
    w.record_plan_review(d, cfg, GOAL, "SOUND", _sha(PLAN), run=gh)
    assert _posts(gh) == [], gh.calls
    # (ii) an unapproved or folded gates record
    for name, kw in (("block", {"verdict": "block"}), ("fold", {"fold": "test_review_pre"})):
        root, d, cfg, wt, gh = _owner(tmp_path / name, monkeypatch, **kw)
        assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #42"), name
        assert _posts(gh) == [], name
    # (iii) the plan on the branch is not the approved bytes
    root, d, cfg, wt, gh = _owner(tmp_path / "iii", monkeypatch, branch_plan=b"# another plan\n")
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #42")
    assert _posts(gh) == []
    # (iv) B1, the real flow: the manifest exists ONLY in the main checkout; `commit` copies it onto the branch
    root, d, cfg, wt, gh = _owner(tmp_path / "iv", monkeypatch, manifest_on_branch=False)
    (wt / "src" / "a2.py").write_text("a2 = 1\n")
    assert w.commit(d, cfg, GOAL, run=gh, message="slice a, more").startswith("committed")
    assert _git(wt, "ls-files", ".sdlc/plans/2007.slices.json") == ".sdlc/plans/2007.slices.json"
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #42")
    assert len(_posts(gh)) == 1 and _payload(_posts(gh)[0])["scope_fingerprint"] == _fp(MANIFEST)
    # ...control: the repo ignores slice manifests -> nothing copied, nothing published, stderr names the manifest
    root, d, cfg, wt, gh = _owner(tmp_path / "iv-ignored", monkeypatch, manifest_on_branch=False,
                                  gitignore=GITIGNORE + ".sdlc/plans/*.slices.json\n")
    (wt / "src" / "a2.py").write_text("a2 = 1\n")
    w.commit(d, cfg, GOAL, run=gh, message="slice a, more")
    assert not (wt / ".sdlc" / "plans" / "2007.slices.json").exists()
    capsys.readouterr()
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #42")
    assert _posts(gh) == [] and "slices.json" in capsys.readouterr().err
    # (v) a single-slice lean goal: not multi-PR, so commit copies no manifest and nothing is published
    root, d, cfg, wt, gh = _owner(tmp_path / "v", monkeypatch, manifest_on_branch=False, root_manifest=[{"id": "a"}])
    (wt / "src" / "a2.py").write_text("a2 = 1\n")
    w.commit(d, cfg, GOAL, run=gh, message="more")
    assert not (wt / ".sdlc" / "plans" / "2007.slices.json").exists()
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #42")
    assert _posts(gh) == []
    # (vi) R3: the acceptance record is in the main checkout only (ignored on the branch) -> nothing published
    root, d, cfg, wt, gh = _owner(tmp_path / "vi", monkeypatch, acceptance_on_branch=False,
                                  gitignore=".sdlc/*\n!.sdlc/plans/\n")
    capsys.readouterr()
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #42")
    assert _posts(gh) == [] and "acceptance record" in capsys.readouterr().err


def test_a_later_plan_review_approval_republishes_after_a_slice_was_pushed(tmp_path, monkeypatch):
    assert callable(getattr(w, "publish_goal_evidence", None)), "work.py has no goal-evidence publisher"
    root, d, cfg, wt, gh = _owner(tmp_path, monkeypatch, config=NOLOG)
    assert w.pr(d, cfg, GOAL, run=gh).startswith("PR #42")
    assert len(_posts(gh)) == 1
    plan2 = b"# plan 2007, re-reviewed\n"
    (pathlib.Path(d) / "plans" / "2007.md").write_bytes(plan2)
    (wt / ".sdlc" / "plans" / "2007.md").write_bytes(plan2)
    _git(wt, "commit", "-q", "-am", "re-reviewed plan")
    w.record_plan_review(d, cfg, GOAL, "SOUND", _sha(plan2), run=gh)
    posts = _posts(gh)
    assert len(posts) == 2, gh.calls
    assert _payload(posts[1])["plan_sha256"] == _sha(plan2)


# --------------------------------------------------------------------------- FR-93: the one reader

def test_newest_trusted_evidence_wins_and_untrusted_or_unjudgeable_markers_fail_closed():
    newest, epoch = getattr(w, "newest_evidence", None), getattr(w, "current_epoch", None)
    assert newest is not None and epoch is not None, "work.py has no goal-evidence reader"
    e1, e2, e3 = (dict(_EV, plan_sha256=c * 64) for c in "123")
    comments = [_evidence_comment(e2, created="2026-01-01T00:00:02Z", cid="IC_2"),       # newer, listed first
                _evidence_comment(e1, created="2026-01-01T00:00:01Z", cid="IC_1"),
                _evidence_comment(e3, assoc="NONE", created="2026-01-01T00:00:03Z", cid="IC_3")]
    got = newest(comments, GOAL)
    assert got["plan_sha256"] == "2" * 64 and got["comment"] == "IC_2", got
    try:
        newest(comments + [_evidence_comment(e3, assoc=None, cid="IC_4")], GOAL)
        raised = False
    except ValueError:
        raised = True
    assert raised, "a marker with no authorAssociation cannot be judged: fail closed"
    broken = dict(_evidence_comment(e1, cid="IC_5"), body="<!-- sigma:goal-evidence {not json} -->")
    missing = {k: v for k, v in e1.items() if k != "plan_sha256"}
    for bad in (broken, _evidence_comment(missing, cid="IC_6"), _evidence_comment(dict(e1, verdict="block"))):
        try:
            newest([bad], GOAL)
            raised = False
        except ValueError:
            raised = True
        assert raised, bad
    retired = "loop" + "smith"
    assert newest([_evidence_comment(e1, prefix=retired)], GOAL)["plan_sha256"] == "1" * 64
    dup = [_evidence_comment(e2, cid="IC_7", created="2026-01-01T00:00:09Z"),
           _evidence_comment(e2, cid="IC_8", created="2026-01-01T00:00:09Z")]
    assert newest(dup, GOAL)["plan_sha256"] == "2" * 64
    marks = [_evidence_comment(e1), _epoch_comment(3), _epoch_comment(9, assoc="NONE", cid="IC_9")]
    assert epoch(marks, GOAL) == 3
    assert newest([], GOAL) is None and epoch([], GOAL) == 0


# --------------------------------------------------------------------------- FR-74 / FR-94: the import

def test_resume_imports_raw_bytes_verified_inputs_into_the_state_dir_only(tmp_path, monkeypatch, capsys):
    assert callable(getattr(w, "stage_resume", None)) and getattr(slices, "scope_fingerprint", None), \
        "work.py has no resume import"
    root, d, cfg, gh, ev = _resume_world(tmp_path)
    for name in ("_sync_registry", "_propagate_registry"):          # R4: the resume never runs the registry pass
        monkeypatch.setattr(w, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("registry pass ran")))
    w.stage_resume(d, GOAL, ev, 2)
    sdlc = pathlib.Path(d)

    def listing():
        return sorted(str(p.relative_to(sdlc)) for sub in ("plans", "acceptance", "research")
                      for p in (sdlc / sub).rglob("*")) if sdlc.exists() else []
    before = listing()
    capsys.readouterr()
    out = w.start(d, cfg, GOAL, run=gh)
    err = capsys.readouterr().err
    assert out.startswith("worktree "), out
    files = _slice_files()
    imp = _imported(d)
    assert (imp / "plan.md").read_bytes() == files[".sdlc/plans/2007.md"]           # \r\n and the final \n kept
    assert (imp / "acceptance.md").read_bytes() == files[".sdlc/acceptance/2007.md"]
    assert (imp / "slices.json").read_bytes() == files[".sdlc/plans/2007.slices.json"]
    assert json.loads((imp / "evidence.json").read_text())["epoch"] == 2
    gates = json.loads(w.plan_review_record_path(d, GOAL).read_text())
    assert gates["imported"] is True and gates["plan_hash"] == _sha(PLAN) and gates["route"] == "lean/1", gates
    assert listing() == before                                      # never the tracked .sdlc/plans|acceptance|research
    rec = w._record(d, GOAL)
    assert w.route_of(d, GOAL)["profile"] == "standard" and rec["profile_source"] == "imported", rec
    assert rec["epoch"] == 2, rec
    assert not [r for r in w._log_rows(d, GOAL) if r.get("kind") == "goal_class" and r.get("source") == "default"]
    assert "classify" not in err, err
    assert _git(rec["worktree"], "status", "--porcelain") == ""


def test_resume_reads_the_open_slice_pr_head_before_the_remote_base(tmp_path):
    assert callable(getattr(w, "stage_resume", None)), "work.py has no resume import"
    v1 = {".sdlc/plans/2007.md": PLAN_V1, ".sdlc/acceptance/2007.md": ACCEPT,
          ".sdlc/plans/2007.slices.json": json.dumps(MANIFEST).encode()}
    root, d, cfg, gh, ev = _resume_world(tmp_path / "open", base="feature/x", base_files=v1)
    w.stage_resume(d, GOAL, ev, 2)
    w.start(d, cfg, GOAL, run=gh)
    assert json.loads((_imported(d) / "evidence.json").read_text())["read_ref"] == "origin/sdlc/2007-sb"
    assert (_imported(d) / "plan.md").read_bytes() == PLAN
    # the PR is closed: the base is read (plan v1) and the sha refuses -- a local root copy of v2 does not rescue it
    root, d, cfg, gh, ev = _resume_world(tmp_path / "closed", base="feature/x", base_files=v1, pr_state="closed")
    (pathlib.Path(d) / "plans").mkdir(exist_ok=True)
    (pathlib.Path(d) / "plans" / "2007.md").write_bytes(PLAN)
    w.stage_resume(d, GOAL, ev, 2)
    try:
        w.start(d, cfg, GOAL, run=gh)
        refused = ""
    except ValueError as exc:
        refused = str(exc)
    assert "RESUME REFUSED" in refused and "plan" in refused, refused
    assert w._record(d, GOAL) is None


def test_resume_refuses_with_the_restart_lever_on_a_mismatch_or_missing_ref(tmp_path):
    assert callable(getattr(w, "stage_resume", None)), "work.py has no resume import"
    root, d, cfg, gh, ev = _resume_world(tmp_path)
    flip = lambda h: h[:-1] + ("0" if h[-1] != "0" else "1")       # noqa: E731
    cases = (("plan sha", dict(ev, plan_sha256=flip(ev["plan_sha256"])), ()),
             ("acceptance sha", dict(ev, acceptance_sha256=flip(ev["acceptance_sha256"])), ()),
             ("manifest scope", dict(ev, scope_fingerprint=_fp([dict(MANIFEST[0], title="Z")] + MANIFEST[1:])), ()),
             ("missing plan blob", dict(ev, plan=".sdlc/plans/2007-other.md"), ()),
             ("traversal", dict(ev, plan="../x.md"), ()),
             ("another goal", dict(ev, plan=".sdlc/plans/2008.md"), ()),
             # a path from comment text is never trusted, even one naming real bytes whose sha matches
             ("not a plan path", dict(ev, plan=".sdlc/acceptance/2007.md", plan_sha256=ev["acceptance_sha256"]), ()),
             ("foreign ref", dict(ev, ref="feature/evil"), ()),
             ("pr read", ev, (("pulls?head=", RuntimeError("HTTP 401: Bad credentials")),)))
    for why, bad, fail in cases:
        w.stage_resume(d, GOAL, bad, 2)
        gh.fail = list(fail)
        try:
            w.start(d, cfg, GOAL, run=gh)
            refused = ""
        except ValueError as exc:
            refused = str(exc)
        gh.fail = []
        assert "RESUME REFUSED" in refused and "loop.py resume" in refused and "--restart" in refused, (why, refused)
        assert not (_imported(d) / "evidence.json").exists(), why
        assert not w.plan_review_record_path(d, GOAL).exists(), why
        assert w._record(d, GOAL) is None, why
        assert not (pathlib.Path(d) / "work" / GOAL).exists(), why             # refused before the worktree add
        assert _git(root, "branch", "--list", "sdlc/2007") == "", why
    assert not [c for c in gh.calls if "pulls?head=" in c and "evil" in c]      # a foreign ref is never looked up
    # a status-only manifest change is not scope; the retry after every refusal imports cleanly
    w.stage_resume(d, GOAL, dict(ev, scope_fingerprint=_fp([dict(m, status="done") for m in MANIFEST])), 2)
    assert w.start(d, cfg, GOAL, run=gh).startswith("worktree "), gh.calls[-5:]
    assert w.imported_evidence(d, GOAL) and w._record(d, GOAL)


def test_imported_inputs_satisfy_the_pr_gates_and_the_implement_start_gate(tmp_path):
    assert callable(getattr(w, "imported_evidence", None)), "work.py has no imported_evidence"
    cfg_g = dict(CFG, gates=GATES)
    root, d, cfg, gh, ev = _resume_world(tmp_path, config=cfg_g)
    w.stage_resume(d, GOAL, ev, 2)
    w.start(d, cfg, GOAL, run=gh)
    assert "reusing open PR #12" in w.slice_start(d, cfg, GOAL, run=gh)
    rec = w._record(d, GOAL)
    assert w._hard_plan_gate_refusal(d, cfg, rec, GOAL, gh) == ""
    assert w._plan_review_refusal(d, cfg, rec, GOAL, gh) == ""
    acc = mpl._load("acceptance")
    assert acc.refusal(d, GOAL) is None and acc.read(d, GOAL)["text"] == ACCEPT.decode()
    assert [s["id"] for s in slices.load(d, GOAL)] == ["a", "b", "c"]
    assert w.multi_pr(d, cfg, GOAL) is True
    assert mpl._load("phase_gate").start_refusal(d, GOAL, "implement") is None
    assert w.classify_blocker(d, cfg, GOAL)[0] == "imported"
    text = mpl._load("next_step").render(d, cfg, GOAL)
    assert "classify it now" not in text, text
    for ph in ("research", "plan", "plan_review"):
        assert re.search(r"ok\s+%s: IMPORTED" % ph, text), (ph, text)
    assert re.search(r"^NEXT: implement", text, re.M), text                      # R8
    # control: one tampered byte in the imported plan and the import no longer counts
    (_imported(d) / "plan.md").write_bytes(PLAN + b"x")
    assert w.imported_evidence(d, GOAL) is None
    assert w._hard_plan_gate_refusal(d, cfg, w._record(d, GOAL), GOAL, gh) != ""


# --------------------------------------------------------------------------- FR-90: the pick

class PickSrc(Src):
    def __init__(self, comments=(), note_result=None, read_raises=None, labels=None):
        super().__init__(labels=labels)
        self.comments, self.note_result, self.read_raises = list(comments), note_result, read_raises
        self.served, self.reads = False, 0

    def next_pending(self, skip=()):
        self.pending_calls += 1
        if self.served or GOAL in set(map(str, skip)):
            return None
        self.served = True
        return GOAL

    def fetch_comments_strict(self, goal):
        self.reads += 1
        if self.read_raises:
            raise self.read_raises
        return {"comments": self.comments, "labels": []}

    def note(self, goal, text):
        self.events.append(("note", str(goal), text))
        return self.note_result


def test_pick_with_trusted_evidence_bumps_the_epoch_and_never_classifies_afresh(tmp_path, capsys):
    lp = mpl._load("loop")
    assert callable(getattr(lp, "_evidence_at_pick", None)), "loop.py has no evidence-aware pick"
    root, d, cfg, gh, ev = _resume_world(tmp_path)
    published = {k: v for k, v in ev.items() if k != "comment"}
    src = PickSrc([_evidence_comment(published), _epoch_comment(2)])
    assert lp._next(d, src, cfg, session_pid="1") == ("goal", GOAL), capsys.readouterr().err
    notes = [e for e in src.events if e[0] == "note"]
    assert len(notes) == 1 and notes[0][2].startswith("<!-- sigma:epoch ") and '"epoch":3' in notes[0][2], notes
    assert json.loads((_imported(d) / "pending.json").read_text())["epoch"] == 3
    capsys.readouterr()
    assert w.start(d, cfg, GOAL, run=gh).startswith("worktree ")
    assert "classify" not in capsys.readouterr().err
    assert w._record(d, GOAL)["profile_source"] == "imported"
    assert not [r for r in w._log_rows(d, GOAL) if r.get("kind") == "goal_class" and r.get("source") == "default"]


def test_pick_skips_a_goal_whose_evidence_cannot_be_read(tmp_path, capsys):
    lp = mpl._load("loop")
    assert callable(getattr(lp, "_evidence_at_pick", None)), "loop.py has no evidence-aware pick"
    good = _evidence_comment(_EV)
    cases = (("read raises", PickSrc(read_raises=RuntimeError("HTTP 502"))),
             ("no association", PickSrc([_evidence_comment(_EV, assoc=None)])),
             ("malformed", PickSrc([dict(good, body="<!-- sigma:goal-evidence {nope} -->")])),
             ("note refused", PickSrc([good], note_result=False)))
    for why, src in cases:
        base, lp = _loop_world(tmp_path / why.replace(" ", "-"))
        capsys.readouterr()
        assert lp._next(base, src, CFG, session_pid="1") == ("DONE", None), why
        err = capsys.readouterr().err
        assert "FR-90" in err, (why, err)
        assert not _claimed(lp, base, GOAL) and ("mark_in_progress", GOAL) not in src.events, why
        assert not (_imported(base) / "pending.json").exists(), why


def test_pick_reads_no_comments_for_an_owned_goal_local_mode_or_work_off(tmp_path):
    lp = mpl._load("loop")
    assert callable(getattr(lp, "_evidence_at_pick", None)), "loop.py has no evidence-aware pick"
    local = {"work": {"enabled": True}, "discovery": {"source": "local"}, "action_log": {"enabled": True}}
    for why, config in (("owned", CFG), ("local", local), ("off", OFF), ("no strict read", CFG)):
        base, lp = _loop_world(tmp_path / why.replace(" ", "-"), config=config)
        src = PickSrc([_evidence_comment(_EV)])
        if why == "owned":
            lp.work._save(base, GOAL, {"worktree": str(pathlib.Path(base) / "work" / GOAL), "branch": "sdlc/2007",
                                       "base": "main", "remote": "origin", "pr": ""})
        if why == "no strict read":
            src.fetch_comments_strict = None
        assert lp._next(base, src, config, session_pid="1") == ("goal", GOAL), why
        assert src.reads == 0, why
        assert not [e for e in src.events if e[0] == "note"], why
        assert _claimed(lp, base, GOAL), why


# --------------------------------------------------------------------------- FR-64 / FR-66: slice resume

def test_resume_starts_at_the_first_unmerged_slice_and_reuses_its_open_pr(tmp_path):
    assert callable(getattr(w, "stage_resume", None)), "work.py has no resume import"
    root, d, cfg, gh, ev = _resume_world(tmp_path / "open", base="feature/x")
    w.stage_resume(d, GOAL, ev, 2)
    w.start(d, cfg, GOAL, run=gh)
    wt = w._record(d, GOAL)["worktree"]
    out = w.slice_start(d, cfg, GOAL, run=gh)
    assert "(reusing open PR #12)" in out, out
    rec = w._record(d, GOAL)
    assert _git(wt, "rev-parse", "HEAD") == _git(wt, "rev-parse", "origin/sdlc/2007-sb")
    assert (rec["pr"], rec["slice"], rec["branch"]) == ("12", "b", "sdlc/2007-sb"), rec
    assert "git checkout -b sdlc/2007-sb" not in gh.calls
    mb = _git(wt, "merge-base", "HEAD", "origin/feature/x")
    assert rec.get("cut_tip") == mb and mb != _git(wt, "rev-parse", "HEAD"), rec                    # R1
    assert w._rebase_argv(cfg, gh, wt, "origin", "feature/x", rec) == [
        "git", "rebase", "--autostash", "--onto", "origin/feature/x", mb]
    # refinement 1: slice a's PR is OPEN and nothing merged -- its open branch is reused, never re-cut from HEAD
    root, d, cfg, gh, ev = _resume_world(tmp_path / "first", base="feature/x", slice_id="a")
    w.stage_resume(d, GOAL, ev, 2)
    w.start(d, cfg, GOAL, run=gh)
    wt = w._record(d, GOAL)["worktree"]
    out = w.slice_start(d, cfg, GOAL, run=gh)
    rec = w._record(d, GOAL)
    assert _git(wt, "rev-parse", "HEAD") == _git(wt, "rev-parse", "origin/sdlc/2007-sa"), out
    assert (rec.get("pr"), rec["slice"]) == ("12", "a"), rec
    # variant: b has no PR -> cut from origin/<base>, not the goal worktree's HEAD, because a is done
    root, d, cfg, gh, ev = _resume_world(tmp_path / "nopr", base="feature/x")
    w.stage_resume(d, GOAL, ev, 2)
    w.start(d, cfg, GOAL, run=gh)
    gh.heads.pop("sdlc/2007-sb")
    wt = w._record(d, GOAL)["worktree"]
    (pathlib.Path(wt) / "local.txt").write_text("local\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", "local only")
    out = w.slice_start(d, cfg, GOAL, run=gh)
    assert w._record(d, GOAL)["slice"] == "b", out
    assert _git(wt, "rev-parse", "HEAD") == _git(wt, "rev-parse", "origin/feature/x"), out
    # variant: the reuse read raises -> refused, no branch (never a duplicate PR)
    root, d, cfg, gh, ev = _resume_world(tmp_path / "raise", base="feature/x")
    w.stage_resume(d, GOAL, ev, 2)
    w.start(d, cfg, GOAL, run=gh)
    gh.heads.pop("sdlc/2007-sb")
    seen = []

    def flaky(cwd, argv):
        line = " ".join(map(str, argv))
        if "2007-sb&state=all" in line:
            seen.append(line)
            if len(seen) >= 2:
                raise RuntimeError("HTTP 502: Bad Gateway")
        return gh(cwd, argv)
    out = w.slice_start(d, cfg, GOAL, run=flaky)
    assert out.startswith("SLICE REFUSED") and "could not read slice b's PR" in out, out
    assert _git(root, "branch", "--list", "sdlc/2007-sb") == ""


def test_unparked_multi_slice_goal_resumes_at_the_first_unmerged_slice(tmp_path, capsys):
    lp = mpl._load("loop")
    assert callable(getattr(lp, "_evidence_at_pick", None)), "loop.py has no evidence-aware pick"
    # (i) another machine: the issue as `unpark.resolve` leaves it, evidence on it, no local record
    root, d, cfg, gh, ev = _resume_world(tmp_path / "other")
    published = {k: v for k, v in ev.items() if k != "comment"}
    src = PickSrc([_evidence_comment(published)], labels={GOAL: {"sdlc:goal"}})
    assert lp._next(d, src, cfg, session_pid="1") == ("goal", GOAL), capsys.readouterr().err
    assert '"epoch":2' in [e for e in src.events if e[0] == "note"][0][2]
    w.start(d, cfg, GOAL, run=gh)
    assert "(reusing open PR #12)" in w.slice_start(d, cfg, GOAL, run=gh)
    # (ii) the owner machine: the park kept the record (stamped, slice b, PR #12 open)
    base, lp = _loop_world(tmp_path, "owner")
    _manifest(base, GOAL, MANIFEST)
    _lean(lp.work, base, GOAL, "b", "12", slices=dict(_merged(GOAL, "a"), b={"branch": "sdlc/2007-sb"}),
          evidence={"digest": "d" * 64, "at": "t"})
    gh = GH(fallback=lambda cwd, argv: "0" if "rev-list" in " ".join(map(str, argv)) else "")
    gh.pr(11, "sdlc/2007-sa").pr(12, "sdlc/2007-sb", state="open", approve=None)
    lp.work._run = gh
    src = PickSrc([_evidence_comment(dict(_EV, ref="sdlc/2007-sb")), _epoch_comment(4)])
    assert lp._next(base, src, CFG, session_pid="1") == ("goal", GOAL), capsys.readouterr().err
    assert src.reads == 1 and '"epoch":5' in [e for e in src.events if e[0] == "note"][0][2]
    assert lp.work._record(base, GOAL)["epoch"] == 5
    assert not (_imported(base) / "pending.json").exists()
    out = lp.work.start(base, CFG, GOAL, run=gh)
    assert out.startswith("already started") and "sdlc/2007-sb" in out, out
    out = lp.work.slice_start(base, CFG, GOAL, run=gh)
    assert "PR #12 is open" in out, out


# --------------------------------------------------------------------------- FR-80: record done

def _fake_import(base, plan=PLAN):
    imp = _imported(base)
    imp.mkdir(parents=True, exist_ok=True)
    (imp / "plan.md").write_bytes(plan)
    (imp / "evidence.json").write_text(json.dumps(dict(_EV, plan_sha256=_sha(plan), comment="IC_9")))


def test_record_done_accepts_published_goal_evidence_and_github_slice_evidence(tmp_path, capsys, monkeypatch):
    cfg = dict(CFG, gates={"phase_record": {"enabled": True}})
    base, lp = _loop_world(tmp_path, config=cfg)
    assert callable(getattr(lp.work, "imported_evidence", None)), "work.py has no imported_evidence"
    pg = mpl._load("phase_gate")
    src = Src()
    monkeypatch.setattr(lp.sources, "get_source", lambda *a: src)
    _manifest(base, GOAL, MANIFEST)
    _lean(lp.work, base, GOAL, "c", "13", slices=dict(_merged(GOAL, "a", "b"), c={"branch": "sdlc/2007-sc"}))
    _fake_import(base)
    lp.actionlog.safe_append(base, GOAL, "phase", phase="retro", state="end")

    def world(c_state="merged", fail=()):
        gh = GH(fail=fail).pr(11, "sdlc/2007-sa").pr(12, "sdlc/2007-sb").pr(13, "sdlc/2007-sc", state=c_state)
        lp.work._run = gh
        return gh
    gh = world()
    rows = {r[0]: r for r in pg.status(base, cfg, GOAL, run=gh)["rows"]}
    for ph in ("research", "plan", "plan_review"):
        assert rows[ph][1] and rows[ph][2].startswith("IMPORTED"), rows[ph]
    for ph in ("implement", "review"):
        assert rows[ph][1] and rows[ph][2].startswith("GitHub slice evidence"), rows[ph]
    # R2: without `run` the gate reads nothing from GitHub and says where it is checked
    n = len(gh.calls)
    rows = {r[0]: r for r in pg.status(base, cfg, GOAL)["rows"]}
    text = pg.format_status(base, cfg, GOAL)
    assert len(gh.calls) == n, gh.calls[n:]
    assert not rows["implement"][1] and "checked at loop.py record done" in rows["implement"][2], rows
    assert "checked at loop.py record done" in text, text
    # refinement 5: from `work.py merge` (no run) the refusal tells a resumer what to do on this machine
    assert "record implement and review on this machine before merging" in (pg.refusal(base, cfg, GOAL) or "")
    # controls: slice c's PR still open, the slice read raising -> implement/review missing
    for gh in (world(c_state="open"), world(fail=(("pulls?head=", RuntimeError("HTTP 502")),))):
        rows = {r[0]: r for r in pg.status(base, cfg, GOAL, run=gh)["rows"]}
        assert not rows["implement"][1] and not rows["review"][1], rows
    # control: no verified import -> research is missing again (exit 4)
    gh = world()
    evidence = _imported(base) / "evidence.json"
    saved = evidence.read_bytes()
    evidence.unlink()
    capsys.readouterr()
    assert lp.main(["loop.py", "record", base, GOAL, "done"]) == 4
    assert "research" in capsys.readouterr().err
    evidence.write_bytes(saved)
    capsys.readouterr()
    code = lp.main(["loop.py", "record", base, GOAL, "done"])
    err = capsys.readouterr().err
    assert "SDLC phase record" not in err, err
    assert code == 0, err

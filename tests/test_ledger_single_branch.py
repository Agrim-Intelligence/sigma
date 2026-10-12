# SPDX-License-Identifier: MIT
"""#1142: a clone whose fetch refspec omits the ops branch must JOIN that branch, never fork it.

THE DEFECT. `git clone --single-branch` (and `--depth N`, which implies it) writes
`remote.origin.fetch = +refs/heads/main:refs/remotes/origin/main`. In such a clone a bare
`git fetch origin sdlc-ledger` writes FETCH_HEAD and nothing else, so `origin/sdlc-ledger` never
exists. `sync.init` read that absence as "nobody has pushed the branch yet" and started a second
history from the empty tree, and every publish after that was rejected non-fast-forward while
saying "will retry next tick". Measured in the field: a 325-commit `sdlc-ledger` branch on origin, an
orphan beside it, and a retry that could never succeed.

EVERY CASE HERE RUNS REAL GIT against a real bare origin and a real `--single-branch` clone. A stub
would only confirm what this file already assumes about git, and the defect is entirely about what
git does and does not write. The orphan the field produced is built by hand (`_orphaned`), commit
for commit, so the recovery tests do not depend on the very code they recover from.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sigma-loop" / "scripts"
SYNC = S / "sync.py"
DOCTOR = ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sync = _load("sync", SYNC)
ledger = _load("ledger", S / "ledger.py")


def _cfg(actor, knowledge=False):
    cfg = {"ledger": {"enabled": True, "actor": actor}}
    if knowledge:
        cfg["knowledge_graph"] = {"enabled": True, "sync": {"enabled": True}}
    return cfg


ANA, BO = _cfg("ana"), _cfg("bo")


def _git(cwd, *args, check=True):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check:
        assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr or proc.stdout}"
    return proc.stdout.strip() if check else proc


def _identity(repo, name="T"):
    """Each clone commits as its own person. Two `commit-tree` calls on the empty tree with the same
    message, identity and second produce the SAME root commit -- related histories, not the fork
    under test -- which a shared test identity would hide every case behind."""
    for key, value in (("user.email", f"{name}@example.com"), ("user.name", name), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)


def _sdlc(repo, config):
    base = repo / ".sdlc"
    (base / "state").mkdir(parents=True, exist_ok=True)
    (base / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return base


def _single_branch_clone(origin, dest, config):
    """THE FIELD SHAPE: a clone that tracks `main` and nothing else."""
    subprocess.run(["git", "clone", "-q", "--single-branch", "--branch", "main", str(origin), str(dest)],
                   check=True, capture_output=True)
    _identity(dest, config["ledger"]["actor"])
    assert _git(dest, "config", "--get-all", "remote.origin.fetch") == \
        "+refs/heads/main:refs/remotes/origin/main"
    return _sdlc(dest, config)


@pytest.fixture
def team(tmp_path):
    """Ana joined the normal way and published `sdlc-ledger`; Bo is a single-branch clone that
    has not run anything yet."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    a = tmp_path / "a"
    subprocess.run(["git", "init", "-q", "-b", "main", str(a)], check=True)
    _identity(a)
    (a / ".gitignore").write_text(".sdlc/\n", encoding="utf-8")
    _git(a, "add", "--", ".gitignore")
    _git(a, "commit", "-q", "-m", "base")
    _git(a, "remote", "add", "origin", str(origin))
    _git(a, "push", "-q", "origin", "main")
    base_a = _sdlc(a, ANA)
    assert "published" in sync.bootstrap(base_a, ANA)
    b = tmp_path / "b"
    base_b = _single_branch_clone(origin, b, BO)
    return types.SimpleNamespace(tmp=tmp_path, origin=origin, a=a, b=b, base_a=base_a, base_b=base_b,
                                 ana_tip=_git(origin, "rev-parse", "refs/heads/sdlc-ledger"))


def _origin_whys(origin, branch="sdlc-ledger"):
    """Every `why` on the origin's ops branch, read straight out of the bare repository."""
    whys = set()
    for rel in _git(origin, "ls-tree", "-r", "--name-only", branch, "entries/").splitlines():
        for line in _git(origin, "show", f"{branch}:{rel}").splitlines():
            if line.strip():
                whys.add(json.loads(line).get("why"))
    return whys


def _orphaned(team):
    """Bo's clone exactly as a pre-#1142 `bootstrap` left it: the `sdlc-ledger` branch started from the empty
    tree, scaffolded, and carrying one committed entry plus TEAM.md -- the three commits the field
    report lists -- with no `origin/sdlc-ledger` ever written. Built with plain git so the recovery
    under test is not also the thing that made the mess."""
    b, base = team.b, team.base_b
    empty = _git(b, "hash-object", "-t", "tree", "/dev/null")
    root = _git(b, "commit-tree", empty, "-m", "ledger: start the ops branch")
    _git(b, "branch", "sdlc-ledger", root)
    wt = sync.worktree(base)
    _git(b, "worktree", "add", "-q", str(wt), "sdlc-ledger")  # ops branch
    (wt / "README.md").write_text(sync.BRANCH_README, encoding="utf-8")
    (wt / ".gitattributes").write_text("entries/*.jsonl merge=union\n", encoding="utf-8")
    (wt / "entries").mkdir()
    (wt / "entries" / ".keep").write_text("", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ledger: scaffold the ops branch")
    (wt / "entries" / ".keep").unlink()
    ledger.append(base, BO, "note", "orphan.md", why="committed on the orphan")
    (wt / "TEAM.md").write_text(ledger.render(ledger.read_all(base)), encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ledger: bo")
    assert _git(b, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/sdlc-ledger", check=False).returncode
    return wt


def _no_common_ancestor(wt):
    return _git(wt, "merge-base", "HEAD", "refs/remotes/origin/sdlc-ledger", check=False).returncode == 1


# --------------------------------------------------------------------------- joining, not forking


def test_documented_bootstrap_joins_the_remote_branch_in_a_single_branch_clone(team):
    """The gesture the docs give, run as the docs give it: the CLI, from the clone's root."""
    proc = subprocess.run([sys.executable, str(SYNC), "bootstrap", ".sdlc"], cwd=team.b,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "published" in proc.stdout and "deferred" not in proc.stdout, proc.stdout
    wt = sync.worktree(team.base_b)
    assert _git(wt, "merge-base", "--is-ancestor", team.ana_tip, "HEAD", check=False).returncode == 0
    assert _git(wt, "rev-parse", "HEAD") == _git(team.origin, "rev-parse", "refs/heads/sdlc-ledger")
    names = _git(team.origin, "ls-tree", "-r", "--name-only", "sdlc-ledger", "entries/").split()  # ops branch
    assert any(n.startswith("entries/ana-") for n in names) and any(n.startswith("entries/bo-") for n in names)


def test_pull_brings_in_a_teammates_new_entry_in_a_single_branch_clone(team):
    """`pull` read a remote-tracking ref the bare fetch never moved: "pulled", and blind."""
    assert "published" in sync.bootstrap(team.base_b, BO)
    ledger.append(team.base_a, ANA, "note", "g.md", why="after bo joined")
    assert sync.publish(team.base_a, ANA) == "published"
    assert sync.pull(team.base_b, BO) == "pulled"
    assert "after bo joined" in {e.get("why") for e in ledger.read_all(team.base_b)}


def test_a_publish_race_in_a_single_branch_clone_rebases_onto_the_winner(team):
    """The retry loop's fetch is the third bare fetch: without the tracking ref it rebased onto
    nothing and the race was lost for good."""
    assert "published" in sync.bootstrap(team.base_b, BO)
    ledger.append(team.base_a, ANA, "note", "g.md", why="ana won the race")
    assert sync.publish(team.base_a, ANA) == "published"
    ledger.append(team.base_b, BO, "note", "g.md", why="bo lost the race")
    assert sync.publish(team.base_b, BO) == "published"
    assert {"ana won the race", "bo lost the race"} <= _origin_whys(team.origin)


def test_init_refuses_when_the_remote_has_the_branch_but_it_cannot_be_fetched(team):
    def fetch_fails(cwd, args):
        if args[:1] == ["fetch"]:
            raise RuntimeError("git fetch: simulated outage")
        return sync._run_git(cwd, args)

    with pytest.raises(RuntimeError, match="REFUSED"):
        sync.init(team.base_b, BO, run=fetch_fails)
    assert _git(team.b, "rev-parse", "--verify", "--quiet", "refs/heads/sdlc-ledger", check=False).returncode
    assert not sync.is_worktree(team.base_b)


def test_init_refuses_when_a_configured_remote_cannot_be_read(team):
    _git(team.b, "remote", "set-url", "origin", str(team.tmp / "nowhere.git"))
    proc = subprocess.run([sys.executable, str(SYNC), "bootstrap", ".sdlc"], cwd=team.b,
                          capture_output=True, text=True)
    assert proc.returncode == 1 and "REFUSED" in proc.stderr, (proc.stdout, proc.stderr)
    assert _git(team.b, "rev-parse", "--verify", "--quiet", "refs/heads/sdlc-ledger", check=False).returncode
    assert not sync.is_worktree(team.base_b)


def test_init_still_starts_fresh_when_the_remote_has_no_ops_branch(tmp_path):
    """The other side of the refusal: an origin with no `sdlc-ledger` branch is a first bootstrap, and
    it must still create the branch from the empty tree and push it."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _identity(seed)
    (seed / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "push", "-q", str(origin), "main")
    base = _single_branch_clone(origin, tmp_path / "b", BO)
    assert "published" in sync.bootstrap(base, BO)
    files = _git(origin, "ls-tree", "-r", "--name-only", "sdlc-ledger").split()  # ops branch
    assert "README.md" in files and "code.py" not in files


# --------------------------------------------------------------------------- an orphan says so


def test_publish_on_an_unrelated_history_is_refused_not_retried_and_never_grafts(team):
    """Two wrong answers to rule out. "will retry" is the field's: no retry can fast-forward an
    unrelated history. Grafting is the one a fixed fetch alone would produce: `rebase -X theirs`
    resolves the add/add on README.md/TEAM.md to THIS clone's copies and replays the empty-tree
    root onto everyone's branch."""
    wt = _orphaned(team)
    ledger.append(team.base_b, BO, "note", "g.md", why="written after the fork")
    out = sync.publish(team.base_b, BO)
    assert out.startswith("publish refused") and "shares no history" in out, out
    assert "sync.py bootstrap .sdlc" in out and "will retry" not in out, out
    assert _git(team.origin, "rev-parse", "refs/heads/sdlc-ledger") == team.ana_tip
    assert _no_common_ancestor(wt)


def test_pull_on_an_unrelated_history_is_refused(team):
    wt = _orphaned(team)
    out = sync.pull(team.base_b, BO)
    assert out.startswith("pull refused") and "shares no history" in out, out
    assert _no_common_ancestor(wt)


def test_a_hand_typed_write_on_an_orphan_does_not_promise_a_retry(team):
    _orphaned(team)
    ledger.append(team.base_b, BO, "note", "g.md", why="typed")
    line = sync.publish_after_write(team.base_b, BO)
    assert "publish refused" in line and "will retry" not in line, line


# --------------------------------------------------------------------------- the lever: re-run bootstrap


def test_bootstrap_reroots_an_orphan_keeping_every_local_entry(team):
    wt = _orphaned(team)
    old_tip = _git(wt, "rev-parse", "HEAD")
    ledger.append(team.base_b, BO, "note", "g.md", why="never committed")
    out = sync.bootstrap(team.base_b, BO)
    assert "re-rooted" in out and out.endswith("published"), out
    assert _git(wt, "merge-base", "--is-ancestor", team.ana_tip, "HEAD", check=False).returncode == 0
    assert _git(team.b, "for-each-ref", "--format=%(objectname)", "refs/sigma/orphaned/ledger/").split() == [old_tip]
    assert {"committed on the orphan", "never committed"} <= _origin_whys(team.origin)
    assert (wt / "README.md").read_text(encoding="utf-8") == _git(team.origin, "show", "sdlc-ledger:README.md") + "\n"  # ops branch
    assert _git(wt, "status", "--porcelain") == ""            # nothing left to wedge the next rebase
    assert sync.pull(team.base_b, BO) == "pulled"
    assert "re-rooted" not in sync.bootstrap(team.base_b, BO)  # converged: a second run is a no-op


def test_reroot_unions_a_same_named_entries_file(team):
    """A collision needs the same actor, host and pid on both sides -- rare, never impossible (a
    second clone on the same machine). Neither side's lines may be lost."""
    wt = _orphaned(team)
    mine = ledger.entry_file(team.base_b, "bo").name
    published = sync.worktree(team.base_a) / "entries" / mine
    published.write_text(json.dumps({"id": "bo:1", "ts": "2026-10-01T00:00:00Z", "actor": "bo",
                                     "kind": "note", "goal": "x", "why": "published earlier"}) + "\n")
    _git(published.parent.parent, "add", "-A")
    _git(published.parent.parent, "commit", "-q", "-m", "ledger: bo (another clone)")
    _git(published.parent.parent, "push", "-q", "origin", "HEAD:sdlc-ledger")  # ops branch
    local = wt / "entries" / mine                     # a writer killed between its line and its "\n"
    local.write_text(local.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
    _git(wt, "commit", "-qam", "ledger: bo (torn tail)")
    out = sync.bootstrap(team.base_b, BO)
    assert "re-rooted" in out and "1 file(s) merged" in out and out.endswith("published"), out
    assert all(json.loads(line) for line in local.read_text(encoding="utf-8").splitlines())
    assert {"published earlier", "committed on the orphan"} <= _origin_whys(team.origin)
    assert _git(wt, "status", "--porcelain") == ""


def test_reroot_is_a_no_op_on_a_healthy_clone(team):
    for _ in range(2):
        out = sync.bootstrap(team.base_b, BO)
        assert "re-rooted" not in out and out.endswith(("published", "nothing to publish")), out
    assert _git(team.b, "for-each-ref", "refs/sigma/orphaned/") == ""


# --------------------------------------------------------------------------- the knowledge channel


def test_knowledge_bootstrap_joins_and_reroots_in_a_single_branch_clone(team):
    """Same `init`, same retry loop, same fix: the notes channel must not fork either."""
    ana_k, bo_k = _cfg("ana", knowledge=True), _cfg("bo", knowledge=True)
    (team.base_a / "config.json").write_text(json.dumps(ana_k), encoding="utf-8")
    (team.base_a / "knowledge" / "analysis").mkdir(parents=True)
    (team.base_a / "knowledge" / "analysis" / "0001-a.md").write_text("# a\nfrom ana\n", encoding="utf-8")
    assert "published" in sync.bootstrap(team.base_a, ana_k, channel=sync.KNOWLEDGE)
    (team.base_b / "config.json").write_text(json.dumps(bo_k), encoding="utf-8")
    joined = sync.bootstrap(team.base_b, bo_k, channel=sync.KNOWLEDGE)    # no notes of its own to add
    assert joined.endswith(("published", "nothing to publish")) and "re-rooted" not in joined, joined
    kb = sync.knowledge_worktree(team.base_b)
    assert (kb / "0001-a.md").exists()

    c = team.tmp / "c"
    base_c = _single_branch_clone(team.origin, c, _cfg("cy", knowledge=True))
    empty = _git(c, "hash-object", "-t", "tree", "/dev/null")
    _git(c, "branch", "sdlc-knowledge", _git(c, "commit-tree", empty, "-m", "knowledge: start the ops branch"))
    kc = sync.knowledge_worktree(base_c)
    kc.parent.mkdir(parents=True)
    _git(c, "worktree", "add", "-q", str(kc), "sdlc-knowledge")  # ops branch
    (kc / "0002-c.md").write_text("# c\nfrom cy\n", encoding="utf-8")
    (kc / "0001-a.md").write_text("# a\ncy's addendum\n", encoding="utf-8")   # same note, both sides
    _git(kc, "add", "-A")
    _git(kc, "commit", "-q", "-m", "knowledge: cy")
    out = sync.bootstrap(base_c, _cfg("cy", knowledge=True), channel=sync.KNOWLEDGE)
    assert "re-rooted" in out and out.endswith("published"), out
    notes = _git(team.origin, "ls-tree", "-r", "--name-only", "sdlc-knowledge").split()  # ops branch
    assert "0001-a.md" in notes and "0002-c.md" in notes
    merged = _git(team.origin, "show", "sdlc-knowledge:0001-a.md")  # ops branch
    assert "from ana" in merged and "cy's addendum" in merged, merged


# --------------------------------------------------------------------------- the three-valued check


def test_shares_history_is_unknown_not_false_when_it_cannot_tell(team, tmp_path):
    wt = _orphaned(team)
    assert sync._shares_history(sync._run_git, wt, "origin", "sdlc-ledger") is None   # no tracking ref  # ops branch
    sync._fetch(sync._run_git, wt, "origin", "sdlc-ledger")  # ops branch
    assert sync._shares_history(sync._run_git, wt, "origin", "sdlc-ledger") is False  # ops branch

    shallow = tmp_path / "shallow"
    shallow.write_text("b" * 40 + "\n", encoding="utf-8")

    def fake(is_shallow):
        def git(cwd, args):
            if args[0] == "merge-base":
                raise RuntimeError("git merge-base: ")
            if args[0] == "rev-list":
                return "a" * 40 if args[-1] == "HEAD" else "b" * 40
            if args[:2] == ["rev-parse", "--is-shallow-repository"]:
                return "true" if is_shallow else "false"
            if args[:2] == ["rev-parse", "--git-path"]:
                return str(shallow)
            return ""
        return git

    assert sync._shares_history(fake(False), wt, "origin", "sdlc-ledger") is False  # ops branch
    assert sync._shares_history(fake(True), wt, "origin", "sdlc-ledger") is None     # a shallow cut  # ops branch
    assert sync._shares_history(lambda c, a: "", wt, "origin", "sdlc-ledger") is not False  # ops branch


# --------------------------------------------------------------------------- the doctor row


def _ledger_row(base, config):
    doctor = _load("doctor", DOCTOR)
    return doctor._ledger_feature_state(pathlib.Path(base), config)


def test_doctor_names_a_diverged_ledger_and_its_lever(team):
    wt = _orphaned(team)
    sync._fetch(sync._run_git, wt, "origin", "sdlc-ledger")      # what the next watcher tick does  # ops branch
    row = _ledger_row(team.base_b, BO)
    assert "DIVERGED" in row and "sync.py bootstrap .sdlc" in row, row


def test_doctor_says_nothing_new_about_a_healthy_single_branch_clone(team):
    """Review finding 1: a push in a narrow-refspec clone never moves `origin/sdlc-ledger`, so
    unless publish records the pushed tip, every published commit reads as unpushed -- "BEHIND"
    straight after a clean bootstrap, and a false backlog alarm once enough pile up."""
    assert "published" in sync.bootstrap(team.base_b, BO)
    for n in range(3):
        ledger.append(team.base_b, BO, "note", "g.md", why=f"published {n}")
        assert sync.publish(team.base_b, BO) == "published"
    assert sync.unpublished(team.base_b, BO)["unpushed_commits"] == 0
    assert sync.pending_entry_count(team.base_b, BO) == 0
    row = _ledger_row(team.base_b, BO)
    assert row.endswith(".sdlc/ledger/entries/"), row          # the count, and no clause after it


# --------------------------------------------------------------------------- plan-review round 1


def _other_host():
    """A writer token that is NOT this machine's: every clone in this file runs in one process on one
    host, so a teammate's file has to be named for the machine it would really come from."""
    host = ledger._host_token()
    return "0" * len(host) if host != "0" * len(host) else "1" * len(host)


def _line(actor, n, why):
    return json.dumps({"id": f"{actor}:{n}", "ts": f"2026-10-0{n}T00:00:00Z", "actor": actor,
                       "kind": "note", "goal": "x", "why": why}) + "\n"


@pytest.mark.parametrize("shape", ["lacks-the-remote-line", "superset", "reordered-superset"])
def test_reroot_carries_a_teammate_file_without_wedging_the_next_pull(team, shape):
    """R1/R12. A path the remote has and this clone carries under a TEAMMATE's name takes the
    branch's copy, whatever its shape here. Unioning it would republish a line the branch no longer
    has (a deliberate purge, when the branch was recreated). Leaving a superset in place leaves a
    modified tracked file publish never stages, and `git rebase` refuses a dirty tree, so every pull
    after it would defer forever. The orphan's copy is not lost: the backup ref holds it."""
    ana_file = sync.worktree(team.base_a) / "entries" / f"ana-{_other_host()}.1.jsonl"   # ana's own machine
    published = _line("ana", 1, "ana's published line")
    ana_file.write_text(published, encoding="utf-8")
    assert sync.publish(team.base_a, ANA) == "published"
    carried = _line("ana", 9, "only on bo's orphan")
    wt = _orphaned(team)
    (wt / "entries" / ana_file.name).write_text(
        {"lacks-the-remote-line": carried, "superset": published + carried,
         "reordered-superset": carried + published}[shape], encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ledger: carried")
    out = sync.bootstrap(team.base_b, BO)
    assert "re-rooted" in out and out.endswith("published"), out
    assert (wt / "entries" / ana_file.name).read_text(encoding="utf-8") == published
    assert _git(wt, "status", "--porcelain") == ""
    assert sync.pull(team.base_b, BO) == "pulled"
    assert "only on bo's orphan" not in _origin_whys(team.origin)
    backup = _git(team.b, "for-each-ref", "--format=%(refname)", "refs/sigma/orphaned/ledger/")
    assert "only on bo's orphan" in _git(team.b, "show", f"{backup}:entries/{ana_file.name}")


def test_reroot_publishes_this_machines_entries_and_sets_the_rest_aside(team):
    """R2/R13. #488: the actor can resolve differently between runs, so a file carrying THIS
    machine's host token is this clone's write whatever actor it names, and it is published. A file
    from another host is not republished: when it is exactly what the old branch held it moves out
    of `entries/` (else `unpublished()` would count it forever), and when it is not, it stays and is
    named."""
    other = _other_host()
    wt = _orphaned(team)
    committed = wt / "entries" / f"zed-{other}.1.jsonl"
    committed.write_text(_line("zed", 1, "another host's committed line"), encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ledger: zed (old branch)")
    (wt / "entries" / f"bo-old-{ledger._instance_token()}.jsonl").write_text(
        _line("bo-old", 3, "under an older actor name"), encoding="utf-8")
    (wt / "entries" / "bo-4242.jsonl").write_text(                  # pre-host-token name (#540)
        _line("bo", 5, "a legacy pid-only file"), encoding="utf-8")
    (wt / "entries" / "bo.jsonl").write_text(                       # pre-#337 bare name: bo's too
        _line("bo", 6, "a legacy bare file"), encoding="utf-8")
    (wt / "entries" / "bo-notes.jsonl").write_text(                 # actor "bo-notes", NOT bo
        _line("bo-notes", 7, "a teammate whose name starts with bo-"), encoding="utf-8")
    loose = wt / "entries" / f"yan-{other}.2.jsonl"
    loose.write_text(_line("yan", 4, "another host's uncommitted line"), encoding="utf-8")
    out = sync.bootstrap(team.base_b, BO)
    assert out.endswith("published"), out
    whys = _origin_whys(team.origin)
    assert {"under an older actor name", "a legacy pid-only file", "a legacy bare file"} <= whys
    assert not {"another host's committed line", "another host's uncommitted line",
                "a teammate whose name starts with bo-"} & whys
    team_md = _git(team.origin, "show", "sdlc-ledger:TEAM.md")  # ops branch
    assert "another host's" not in team_md, team_md
    assert not committed.exists() and not loose.exists()
    orphaned = team.base_b / "state" / "orphaned"
    assert [q.read_text(encoding="utf-8") for q in orphaned.rglob(committed.name)] == \
        [_line("zed", 1, "another host's committed line")]
    assert [q.parent.parent.name for q in orphaned.rglob(loose.name)] == ["unplaced"]
    assert loose.name in out and committed.name not in out, out   # only the copy that exists nowhere else is named


def test_a_shallow_cut_defers_without_rebasing(tmp_path, monkeypatch):
    """R3. No visible merge base and a root that is only a shallow boundary: neither "refused"
    (it cannot be proved) nor a rebase (that is the graft)."""
    shallow = tmp_path / "shallow"
    shallow.write_text("b" * 40 + "\n", encoding="utf-8")
    base = _sdlc(tmp_path, BO)
    (sync.worktree(base) / "entries").mkdir(parents=True)
    ledger.entry_file(base, "bo").write_text("{}\n")
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)
    seen = []

    def git(cwd, args):
        seen.append(args[0])
        if args[0] == "push":
            raise RuntimeError("git push: [rejected] (fetch first)")
        if args[0] == "merge-base":
            raise RuntimeError("git merge-base: ")
        if args[0] == "rev-list":
            return "a" * 40 if args[-1] == "HEAD" else "b" * 40
        if args[:2] == ["rev-parse", "--is-shallow-repository"]:
            return "true"
        if args[:2] == ["rev-parse", "--git-path"]:
            return str(shallow)
        return "x" if args[0] == "diff" else ""

    out = sync.publish(base, BO, run=git, attempts=3)
    assert out.startswith("publish deferred") and "not rebasing" in out, out
    assert "rebase" not in seen


def test_a_url_remote_is_refused_by_name(team):
    """R4. A remote-tracking ref needs a NAMED remote; a URL would make the explicit refspec
    invalid, and before #1142 it silently forked on the second machine."""
    cfg = {"ledger": {"enabled": True, "actor": "bo", "remote": str(team.origin)}}
    with pytest.raises(ValueError, match="REFUSED"):
        sync.init(team.base_b, cfg)
    assert not sync.is_worktree(team.base_b)


def test_a_glob_branch_name_is_refused(team):
    cfg = {"ledger": {"enabled": True, "actor": "bo", "branch": "*"}}
    with pytest.raises(ValueError, match="REFUSED"):
        sync.init(team.base_b, cfg)


def test_resolve_remote_snapshot_pins_the_fetched_tip_in_a_single_branch_clone(team):
    """R5. Once sync creates `origin/sdlc-ledger` in a single-branch clone, a BARE fetch never
    moves it again, and the snapshot would pin whatever it was."""
    observation = _load("merge_observation", S / "merge_observation.py")
    assert "published" in sync.bootstrap(team.base_b, BO)
    stale = _git(team.b, "rev-parse", "refs/remotes/origin/sdlc-ledger")
    ledger.append(team.base_a, ANA, "note", "g.md", why="moves the tip")
    assert sync.publish(team.base_a, ANA) == "published"
    tip = _git(team.origin, "rev-parse", "refs/heads/sdlc-ledger")
    assert tip != stale
    assert observation.resolve_remote_snapshot(team.b)["commit"] == tip


def test_two_first_bootstraps_racing_on_an_empty_remote_both_land(tmp_path):
    """R7/R14. Both clones see no ops branch and start one; the second push loses. The race is
    INJECTED at B's first push -- after B's own re-root has already looked and found nothing -- so
    only bootstrap's single re-root-and-publish-again can land B. Bootstrap is the gesture the
    operator consented to; a bare `publish` would only say "refused"."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _identity(seed)
    _git(seed, "commit", "-q", "--allow-empty", "-m", "base")
    _git(seed, "push", "-q", str(origin), "main")
    base_a = _single_branch_clone(origin, tmp_path / "a", ANA)
    base_b = _single_branch_clone(origin, tmp_path / "b", BO)
    sync.init(base_a, ANA)                                 # A starts a branch, has not pushed yet
    fired = []

    def a_wins_at_bs_first_push(cwd, args):
        if args[:1] == ["push"] and not fired:
            fired.append(True)
            assert "published" in sync.bootstrap(base_a, ANA)
        return sync._run_git(cwd, args)

    out = sync.bootstrap(base_b, BO, run=a_wins_at_bs_first_push)
    assert fired and "re-rooted" in out and out.endswith("published"), out
    names = _git(origin, "ls-tree", "-r", "--name-only", "sdlc-ledger", "entries/").split()  # ops branch
    assert any(n.startswith("entries/ana-") for n in names) and any(n.startswith("entries/bo-") for n in names)


def test_doctor_names_a_fetch_refspec_that_cannot_see_the_ledger(team):
    """R8. The pre-fix orphan with a dead watcher: no tracking ref was ever written, so DIVERGED
    cannot be proved locally -- but the cause can be named, with the command that fetches by name."""
    _orphaned(team)
    row = _ledger_row(team.base_b, BO)
    assert "fetch refspec" in row and "sync.py pull .sdlc" in row, row


# --------------------------------------------------------------------------- code review (P6)


def test_a_reroot_interrupted_after_its_reset_is_finished_by_the_next_bootstrap(team):
    """Finding 2. After the reset the histories ARE related, so without a marker nothing tells the
    next bootstrap that the settle step never ran -- and a file only it would publish stays local."""
    wt = _orphaned(team)
    older = wt / "entries" / f"bo-old-{ledger._instance_token()}.jsonl"
    older.write_text(_line("bo-old", 3, "under an older actor name"), encoding="utf-8")
    stranger = wt / "entries" / f"yan-{_other_host()}.2.jsonl"
    stranger.write_text(_line("yan", 4, "another host's loose line"), encoding="utf-8")
    fired = []

    def dies_after_the_reset(cwd, args):
        # The settle step's first git call that is not a read of the tree: after the reset, before
        # anything is moved or staged -- so the resume has to compare against the old tip itself.
        if args[:1] == ["hash-object"] and not fired:
            fired.append(True)
            raise RuntimeError("git hash-object: simulated crash after the reset")
        return sync._run_git(cwd, args)

    with pytest.raises(RuntimeError, match="simulated crash"):
        sync.bootstrap(team.base_b, BO, run=dies_after_the_reset)
    assert stranger.exists()                                   # nothing was moved before the crash
    assert _git(wt, "merge-base", "--is-ancestor", team.ana_tip, "HEAD", check=False).returncode == 0
    # Review round 2: the backup ref may be gone (an operator tidied refs/sigma/orphaned/), and the
    # remote may be unreachable -- neither stops the settle step, which is local and keyed on its marker.
    for ref in _git(team.b, "for-each-ref", "--format=%(refname)", "refs/sigma/orphaned/").split():
        _git(team.b, "update-ref", "-d", ref)
    url = _git(team.b, "remote", "get-url", "origin")
    _git(team.b, "remote", "set-url", "origin", str(team.tmp / "nowhere.git"))
    offline = sync.bootstrap(team.base_b, BO)
    assert "finished the re-root" in offline and "publish deferred" in offline, offline
    assert not stranger.exists() and stranger.name in offline, offline
    _git(team.b, "remote", "set-url", "origin", url)
    # The offline publish committed and could not push. The next publish pushes that stranded commit
    # with no new entry (#1210; before it, this test had to write one to get the commit carried).
    out = sync.bootstrap(team.base_b, BO)
    assert "finished the re-root" not in out and out.endswith("published"), out
    assert "under an older actor name" in _origin_whys(team.origin)
    assert "another host's loose line" not in _origin_whys(team.origin)
    assert not list((team.base_b / "state" / "orphaned").glob("*/ledger/PENDING"))
    assert _git(wt, "status", "--porcelain") == ""


def test_reroot_saves_an_uncommitted_copy_it_must_overwrite(team):
    """Finding 3. A path the branch has, held here as an untracked (or uncommitted) copy, gets the
    branch's version -- but the local one is in no commit, so it is copied aside first and named."""
    ana_file = sync.worktree(team.base_a) / "entries" / f"ana-{_other_host()}.1.jsonl"
    published = _line("ana", 1, "ana's published line")
    ana_file.write_text(published, encoding="utf-8")
    assert sync.publish(team.base_a, ANA) == "published"
    wt = _orphaned(team)
    only_here = published + _line("ana", 2, "in no commit anywhere")
    (wt / "entries" / ana_file.name).write_text(only_here, encoding="utf-8")
    out = sync.bootstrap(team.base_b, BO)
    assert out.endswith("published") and ana_file.name in out, out
    assert (wt / "entries" / ana_file.name).read_text(encoding="utf-8") == published
    saved = list((team.base_b / "state" / "orphaned").rglob(ana_file.name))
    assert [q.read_text(encoding="utf-8") for q in saved] == [only_here]


def test_knowledge_pull_on_an_unrelated_history_is_refused(team):
    """Review M1: `_pull_knowledge` runs on `record`'s path. Without its own check it rebases an
    orphan onto the remote ("pulled", a silent graft), and the next publish pushes the orphan's notes
    -- the automatic republish the design reserves for an operator."""
    ana_k, cy_k = _cfg("ana", knowledge=True), _cfg("cy", knowledge=True)
    (team.base_a / "config.json").write_text(json.dumps(ana_k), encoding="utf-8")
    (team.base_a / "knowledge" / "analysis").mkdir(parents=True)
    (team.base_a / "knowledge" / "analysis" / "0001-a.md").write_text("# a\n", encoding="utf-8")
    assert "published" in sync.bootstrap(team.base_a, ana_k, channel=sync.KNOWLEDGE)
    c = team.tmp / "c"
    base_c = _single_branch_clone(team.origin, c, cy_k)
    empty = _git(c, "hash-object", "-t", "tree", "/dev/null")
    _git(c, "branch", "sdlc-knowledge", _git(c, "commit-tree", empty, "-m", "knowledge: start the ops branch"))
    kc = sync.knowledge_worktree(base_c)
    kc.parent.mkdir(parents=True)
    _git(c, "worktree", "add", "-q", str(kc), "sdlc-knowledge")  # ops branch
    (kc / "0002-c.md").write_text("# c\n", encoding="utf-8")
    _git(kc, "add", "-A")
    _git(kc, "commit", "-q", "-m", "knowledge: cy")
    out = sync.pull(base_c, cy_k, channel=sync.KNOWLEDGE)
    assert out.startswith("pull refused") and "--channel knowledge" in out, out
    assert _git(kc, "merge-base", "HEAD", "refs/remotes/origin/sdlc-knowledge", check=False).returncode == 1


def test_knowledge_reroot_defers_while_another_sync_holds_the_lock(team):
    """Review M4: two knowledge syncs on one machine must never drive the worktree's index at once."""
    if sync.fcntl is None:
        pytest.skip("the knowledge lock fails open without fcntl")
    cy_k = _cfg("cy", knowledge=True)
    base_c = _single_branch_clone(team.origin, team.tmp / "c", cy_k)
    held = sync._knowledge_lock(base_c)
    try:
        ch = sync._channel(sync.KNOWLEDGE, base_c, cy_k)
        assert sync._reroot_guarded(sync._run_git, base_c, ch) == \
            "; re-root deferred: another knowledge sync holds the lock"
    finally:
        held.close()


def test_publish_receipt_never_rebases_onto_an_unrelated_history(tmp_path, monkeypatch):
    """Review M2: the receipt publisher has its own retry loop and a plain rebase. Gated off today,
    but the graft must be unreachable from every path."""
    base = _sdlc(tmp_path, BO)
    relative = "receipts/v1/" + "c" * 64 + "/parent.json"
    target = sync.worktree(base) / relative
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)
    monkeypatch.setattr(sync, "verify_receipt_commit", lambda *a: "d" * 40)
    seen = []

    def git(cwd, args):
        seen.append(args[0])
        if args[0] == "push":
            raise RuntimeError("git push: [rejected] (fetch first)")
        if args[0] == "merge-base":
            raise RuntimeError("git merge-base: ")
        if args[0] == "rev-list":
            return "a" * 40 if args[-1] == "HEAD" else "b" * 40
        return "e" * 40 if args[0] in ("hash-object", "rev-parse") else ""

    assert sync.publish_receipt(base, relative, config=BO, run=git) == \
        "receipt pending: publish acknowledgement failed"
    assert "rebase" not in seen


def test_init_starts_fresh_with_no_remote_configured(tmp_path):
    """Review M5: a local-only setup has nothing to fork from, so it still starts from the empty tree."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _identity(repo)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    base = _sdlc(repo, BO)
    assert sync._remote_branch_state(sync._run_git, repo, "origin", "sdlc-ledger") == "no-remote"
    assert "ready" in sync.init(base, BO)
    assert _git(repo, "ls-tree", "--name-only", "sdlc-ledger").split() == [".gitattributes", "README.md"]  # ops branch


def test_remote_branch_state_matches_the_branch_exactly(team):
    """Review M7: `ls-remote <pattern>` matches any ref whose trailing path components equal the
    pattern (probed: `refs/heads/sdlc-other` matches `refs/heads/team/refs/heads/sdlc-other`)."""
    _git(team.a, "push", "-q", "origin", "main:refs/heads/team/refs/heads/sdlc-other")
    assert sync._remote_branch_state(sync._run_git, team.b, "origin", "sdlc-ledger") == "present"
    assert sync._remote_branch_state(sync._run_git, team.b, "origin", "sdlc-other") == "absent"
    _git(team.b, "remote", "set-url", "origin", str(team.tmp / "nowhere.git"))
    assert sync._remote_branch_state(sync._run_git, team.b, "origin", "sdlc-ledger") == "unreadable"


def test_fetch_refspec_covers_reads_exact_glob_and_negative_entries(team):
    """Review M6: the doctor's cause line must neither miss a narrow clone nor accuse a normal one."""
    def covers(*specs):
        _git(team.b, "config", "--unset-all", "remote.origin.fetch")
        for spec in specs:
            _git(team.b, "config", "--add", "remote.origin.fetch", spec)
        return sync.fetch_refspec_covers(team.base_b, BO)

    assert covers("+refs/heads/main:refs/remotes/origin/main") is False
    assert covers("+refs/heads/*:refs/remotes/origin/*") is True
    assert covers("+refs/heads/main:refs/remotes/origin/main",
                  "+refs/heads/sdlc-ledger:refs/remotes/origin/sdlc-ledger") is True
    assert covers("+refs/heads/sdlc-" "*:refs/remotes/origin/sdlc-" "*") is True
    assert covers("^refs/heads/sdlc-ledger", "+refs/heads/main:refs/remotes/origin/main") is False


def test_a_configured_remote_whose_name_has_a_slash_is_accepted(team):
    """Review M8: a remote NAME may hold a `/`; only an unconfigured URL-or-path value is refused."""
    _git(team.b, "remote", "add", "team/origin", str(team.origin))
    cfg = {"ledger": {"enabled": True, "actor": "bo", "remote": "team/origin"}}
    assert "published" in sync.bootstrap(team.base_b, cfg)
    assert _git(team.b, "rev-parse", "refs/remotes/team/origin/sdlc-ledger") == \
        _git(team.origin, "rev-parse", "refs/heads/sdlc-ledger")


def test_shallow_commits_resolves_a_relative_git_path_against_the_repository(tmp_path):
    """Review M11: in a main checkout `--git-path shallow` is RELATIVE (`.git/shallow`), and must be
    read from the repository, not from wherever the process happens to be."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _identity(seed)
    for n in range(3):
        _git(seed, "commit", "-q", "--allow-empty", "-m", f"c{n}")
    _git(seed, "push", "-q", str(origin), "main")
    d = tmp_path / "d"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(d)], check=True)
    assert sync._shallow_commits(sync._run_git, d) == {_git(d, "rev-parse", "HEAD")}


def test_the_recorded_tip_is_the_pushed_commit_not_a_later_head(team):
    """Review round 2: another publisher in this worktree can commit between the push and the
    tracking-ref write. Recording HEAD then would call that commit published while the remote lacks
    it -- a false all-clear the doctor would repeat. What is recorded is what was pushed."""
    assert "published" in sync.bootstrap(team.base_b, BO)
    wt = sync.worktree(team.base_b)
    raced = []

    def second_publisher_commits_after_the_push(cwd, args):
        out = sync._run_git(cwd, args)
        if args[:1] == ["push"] and not raced:
            raced.append(True)
            (wt / "entries" / "late.jsonl").write_text(_line("bo", 8, "committed after the push"))
            _git(wt, "add", "-A")
            _git(wt, "commit", "-q", "-m", "ledger: bo (second publisher)")
        return out

    ledger.append(team.base_b, BO, "note", "g.md", why="pushed")
    assert sync.publish(team.base_b, BO, run=second_publisher_commits_after_the_push) == "published"
    # HEAD moved while the push ran, so nothing was recorded: the clone OVER-reports (the pushed
    # commit and the late one) rather than calling the late one published -- never a false all-clear.
    assert sync.unpublished(team.base_b, BO)["unpushed_commits"] == 2
    assert sync.pull(team.base_b, BO) == "pulled"          # the next fetch corrects the over-report
    assert sync.unpublished(team.base_b, BO)["unpushed_commits"] == 1

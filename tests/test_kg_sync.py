# SPDX-License-Identifier: MIT
"""Knowledge-graph analysis notes reach every machine's rebuild over an ops branch (#2698).

THE TIMING FACT. The one production writer of `.sdlc/knowledge/analysis/issue-<n>.md` is
`agrim-retro` §4, which runs AFTER the goal's PR has merged and shortly before `record done` releases
the worktree. So the note has no PR to ride and a `work.py pr` guard cannot see a file that does not
exist yet. The ledger already solves this exact shape for `.sdlc/ledger/`: a linked git worktree on
an ops branch that is never merged, gitignored on every code branch, published with a bounded
fetch-rebase-retry. `sync.py` grows that as a second CHANNEL.

THE GESTURE UNDER TEST IS THE ONE THE DOCS GIVE. Both `/agrim-loop` and `/agrim-goal` end a goal with
`loop.py record <goal> done --retro-grade <grade>`, so these tests call the real `_record()`, which
shells out to the real `sync.py` and the real `kg.py`, against REAL git clones of a REAL bare origin.
Nothing about git is stubbed: every claim here is a claim about what git holds on the remote.

WHAT THEY ASSERT ON. The builder's `graph.json` ON DISK for the done_when (which notes did the
rebuild SEE), `git ls-tree` on the bare origin for what left the machine, and the journal's `retro`
event for "the goal's bookkeeping survived". Never a return value or a log line alone.

Naming rule for this file: it is scanned by tests/test_no_private_names.py, so it names things by
what they are (notes, journal, ops branch) and nothing else.
"""
import fcntl
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import sys

import pytest

from journal_events import journal_events

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


loop = _load("loop")
state = _load("state")
sync = _load("sync")

BUILDER = "stubgraph"
BRANCH = sync.DEFAULT_KNOWLEDGE_BRANCH


class _Source:
    def __init__(self):
        self.completed, self.parked = [], []

    def complete(self, goal):
        self.completed.append(goal)

    def park(self, goal, reason, **kw):
        self.parked.append((goal, reason))


def _git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr or proc.stdout}"
    return proc.stdout.strip()


def _git_rc(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True).returncode


def _stub_builder(tmp_path):
    """A REAL executable on PATH. Writes `<out>/<builder>-out/graph.json` listing every `*.md` it was
    handed, so a test can prove WHICH notes a rebuild consumed rather than merely that one ran."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / BUILDER
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json, pathlib\n"
        "argv = sys.argv[1:]\n"
        "corpus = pathlib.Path(argv[1])\n"
        "out = pathlib.Path(argv[argv.index('--out') + 1])\n"
        f"d = out / '{BUILDER}-out'\n"
        "d.mkdir(parents=True, exist_ok=True)\n"
        "(d / 'graph.json').write_text(json.dumps("
        "{'notes': sorted(p.name for p in corpus.rglob('*.md'))}))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def _config(actor, sync_block="on"):
    cfg = {"budget": {}, "ledger": {"enabled": True, "actor": actor}, "journal": {"enabled": True},
           "knowledge_graph": {"enabled": True, "scope": "research", "builder": BUILDER,
                               "auto_refresh": True}}
    if sync_block == "on":
        cfg["knowledge_graph"]["sync"] = {"enabled": True}
    elif sync_block is not None:
        cfg["knowledge_graph"]["sync"] = sync_block
    return cfg


class Clone:
    def __init__(self, root, actor):
        self.root, self.actor = root, actor
        self.sdlc = root / ".sdlc"
        self.analysis = self.sdlc / "knowledge" / "analysis"

    @property
    def d(self):
        return str(self.sdlc)

    def config(self):
        return json.loads((self.sdlc / "config.json").read_text())

    def write_config(self, cfg):
        (self.sdlc / "config.json").write_text(json.dumps(cfg))

    def note(self, name, text):
        self.analysis.mkdir(parents=True, exist_ok=True)
        (self.analysis / name).write_text(text, encoding="utf-8")

    def graph_notes(self):
        return json.loads((self.root / f"{BUILDER}-out" / "graph.json").read_text())["notes"]

    def record(self, goal="0001-x.md", grade="achieved"):
        return loop._record(self.d, _Source(), goal, "done", "", retro_grade=grade)

    def ahead(self):
        try:
            return int(_git(self.analysis, "rev-list", "--count", f"origin/{BRANCH}..HEAD"))
        except AssertionError:
            return None


def _clone(tmp_path, name, origin, sync_block="on", with_config=True):
    root = tmp_path / name
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    _git(root, "config", "user.email", f"{name}@example.com")
    _git(root, "config", "user.name", name.upper())
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "remote", "add", "origin", str(origin))
    (root / ".gitignore").write_text(".sdlc/\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-qm", "code")
    c = Clone(root, name)
    (c.sdlc / "state").mkdir(parents=True)
    if with_config:
        c.write_config(_config(name, sync_block))
    (c.sdlc / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    c.analysis.mkdir(parents=True)
    state.start_run(c.d)
    return c


def _origin_files(origin):
    if _git_rc(origin, "rev-parse", "--verify", f"refs/heads/{BRANCH}") != 0:
        return None
    return _git(origin, "ls-tree", "-r", "--name-only", BRANCH).splitlines()


def _origin_blob(origin, name):
    return _git(origin, "show", f"{BRANCH}:{name}")


@pytest.fixture
def origin(tmp_path):
    o = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(o)], check=True)
    _git(o, "config", "receive.denyNonFastForwards", "true")
    return o


@pytest.fixture
def clones(tmp_path, origin, monkeypatch):
    monkeypatch.setenv("PATH", f"{_stub_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")
    return _clone(tmp_path, "a", origin), _clone(tmp_path, "b", origin)


def _bootstrap(c):
    return sync.bootstrap(c.d, c.config(), channel=sync.KNOWLEDGE)


def _publish(c, run=None):
    return sync.publish(c.d, c.config(), run=run, channel=sync.KNOWLEDGE)


def _pull(c):
    return sync.pull(c.d, c.config(), channel=sync.KNOWLEDGE)


# ------------------------------------------------------------------------- bootstrap


def test_bootstrap_carries_existing_plain_notes_in_and_pushes_them(clones, origin):
    a, _ = clones
    for n in (1, 2, 3):
        a.note(f"issue-{n}.md", f"# Issue #{n}\nlesson {n}\n")

    first = _bootstrap(a)

    assert "published" in first
    assert sync.is_worktree(a.d, channel=sync.KNOWLEDGE)
    files = _origin_files(origin)
    assert {"issue-1.md", "issue-2.md", "issue-3.md", ".gitattributes", ".gitignore"} <= set(files)
    assert _origin_blob(origin, "issue-2.md") == "# Issue #2\nlesson 2"
    assert "*.md merge=union" in _origin_blob(origin, ".gitattributes")
    assert "graphify-out/" in _origin_blob(origin, ".gitignore")
    assert not (a.analysis / "README.md").exists(), "a README would be graphed as a corpus note"
    second = _bootstrap(a)
    assert "already a worktree" in second and "nothing to publish" in second


def test_bootstrap_on_a_note_less_clone_still_pushes_the_branch(clones, origin):
    """B1: an empty `analysis/` must still put the branch on origin, or a teammate's `init` finds
    nothing to fetch and every machine starts its own unrelated root."""
    a, _ = clones
    assert not any(a.analysis.iterdir())

    out = _bootstrap(a)

    assert "published" in out
    assert _origin_files(origin) is not None, "bootstrap on a note-less clone pushed nothing"


def test_bootstrap_unions_a_note_that_collides_with_the_branch_copy(clones, origin):
    """S5: `_carry` used to drop a colliding local file silently. For a note the branch copy is
    kept AND the local lines it lacks are appended, and the return string says so."""
    a, b = clones
    a.note("issue-3.md", "remote line\n")
    _bootstrap(a)
    b.note("issue-3.md", "local line\n")

    out = _bootstrap(b)

    text = (b.analysis / "issue-3.md").read_text()
    assert "remote line" in text and "local line" in text
    assert "1 local note(s) merged" in out
    assert "local line" in _origin_blob(origin, "issue-3.md"), "the union was not published"


# ------------------------------------------------------------------------- the done_when


def _two_bootstrapped(clones):
    """Both joined; B owns one note ALREADY PUBLISHED at its bootstrap, so B's corpus is non-empty
    (its refresh runs) while B has nothing new to publish (`ahead == 0`, pinned by the callers)."""
    a, b = clones
    _bootstrap(a)
    b.note("issue-0.md", "# Issue #0\nB's own\n")
    _bootstrap(b)                       # B joins BEFORE A writes, so only a pull can bring A's note
    return a, b


def test_a_note_recorded_on_one_clone_is_in_the_other_clones_rebuild(clones, origin):
    """THE DONE_WHEN. A writes a note and records with a retro grade; B records; B's builder is
    invoked over a corpus that contains A's note. B has nothing of its own to publish — pinned —
    so only the pull half of the hop can account for the note's arrival."""
    a, b = _two_bootstrapped(clones)
    a.note("issue-7.md", "# Issue #7\nA's lesson\n")
    assert a.record() == "done"
    assert "issue-7.md" in _origin_files(origin)
    assert _git(b.analysis, "status", "--porcelain", "--untracked-files=all") == ""
    assert b.ahead() == 0, "B must have nothing to publish, or its own retry would mask a removed pull"

    assert b.record(goal="0002-y.md") == "done"

    assert "issue-7.md" in b.graph_notes(), "B's rebuild never saw A's note"
    assert (b.analysis / "issue-7.md").read_text() == "# Issue #7\nA's lesson\n"


def test_removing_the_pull_hop_leaves_the_other_clone_blind(clones, origin, monkeypatch):
    """THE CONTROL for the test above, at the exact seam. `_load("sync")` builds a fresh module per
    call, so patching any `sync` copy is inert; the verbs tuple lives on the very `loop` module
    whose `_record` this file calls."""
    a, b = _two_bootstrapped(clones)
    a.note("issue-7.md", "# Issue #7\n")
    a.record()
    monkeypatch.setattr(loop, "_KNOWLEDGE_SYNC_VERBS", ("publish",))

    b.record(goal="0002-y.md")

    assert b.graph_notes() == ["issue-0.md"], "B's rebuild ran over its own note only"


# ------------------------------------------------------------------------- negative controls


@pytest.mark.parametrize("sync_block", [None, {"enabled": False}, {"enabled": "true"}],
                         ids=["absent", "false", "quoted-true"])
def test_sync_off_touches_no_git_and_creates_no_worktree(tmp_path, origin, monkeypatch, capsys,
                                                         sync_block):
    monkeypatch.setenv("PATH", f"{_stub_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")
    a = _clone(tmp_path, "a", origin, sync_block=sync_block)
    a.note("issue-1.md", "# Issue #1\n")

    assert a.record() == "done"

    assert not (a.analysis / ".git").exists()
    assert _origin_files(origin) is None, "sync was off and the branch still reached origin"
    assert "knowledge notes" not in capsys.readouterr().err
    assert a.graph_notes() == ["issue-1.md"], "the ordinary refresh must still run"


def test_research_breadcrumbs_never_reach_the_ops_branch(clones, origin):
    """SAFETY. `research/web/` captures are scrubbed best-effort and gitignored for that reason. The
    worktree root is `analysis/`, so they are structurally out of reach; this pins it."""
    a, _ = clones
    web = a.sdlc / "knowledge" / "research" / "web"
    web.mkdir(parents=True)
    (web / "20260926-000000.md").write_text("# capture\nsource: https://example.test\n")
    a.note("issue-1.md", "# Issue #1\n")
    _bootstrap(a)

    _publish(a)

    files = _origin_files(origin)
    assert "issue-1.md" in files
    assert not [f for f in files if "research" in f or "web" in f]


def test_record_says_nothing_when_config_is_missing(tmp_path, origin, monkeypatch, capsys):
    monkeypatch.setenv("PATH", f"{_stub_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")
    a = _clone(tmp_path, "a", origin, with_config=False)

    loop._sync_knowledge_notes(a.d, "0001-x.md")

    assert "knowledge notes" not in capsys.readouterr().err


def test_cli_gate_names_the_config_when_sync_is_off(tmp_path, origin):
    a = _clone(tmp_path, "a", origin, sync_block=None)

    proc = subprocess.run([sys.executable, str(_SCRIPTS / "sync.py"), "publish", a.d,
                           "--channel", "knowledge"], capture_output=True, text=True)

    assert proc.returncode == 1
    assert "knowledge_graph" in proc.stderr and "sync" in proc.stderr
    assert not (a.analysis / ".git").exists()


# ------------------------------------------------------------------------- races and merges


def test_a_concurrent_publish_lands_both_notes_without_force(clones, origin):
    """B lands first; A's push is rejected, A rebases and retries. The origin refuses non-fast-forward
    pushes outright, so a force here would fail by git's own hand, and the recorded argv proves no
    force was even attempted."""
    a, b = _two_bootstrapped(clones)
    a.note("issue-10.md", "A\n")
    b.note("issue-11.md", "B\n")
    assert _publish(b) == "published"
    seen = []

    def recording(cwd, args):
        seen.append(list(args))
        return sync._run_git(cwd, args)

    assert _publish(a, run=recording) == "published"

    assert _git(origin, "config", "receive.denyNonFastForwards") == "true"
    assert {"issue-10.md", "issue-11.md"} <= set(_origin_files(origin))
    pushes = [args for args in seen if args and args[0] == "push"]
    assert pushes and not [args for args in pushes
                           if "--force" in args or "-f" in args or any(x.startswith("+") for x in args)]
    assert any(args[0] == "rebase" for args in seen), "A never rebased, so the race never happened"


def test_a_same_named_note_on_both_sides_union_merges_and_never_wedges(clones, origin):
    a, b = _two_bootstrapped(clones)
    a.note("issue-9.md", "# Issue #9\nfrom A\n")
    b.note("issue-9.md", "# Issue #9\nfrom B\n")
    assert _publish(a) == "published"
    assert _publish(b) == "published"
    assert _pull(a) == "pulled"

    for c in (a, b):
        text = (c.analysis / "issue-9.md").read_text()
        assert "from A" in text and "from B" in text
        for path in ("rebase-merge", "rebase-apply"):
            assert not pathlib.Path(_git(c.analysis, "rev-parse", "--git-path", path)).exists()
        assert _git(c.analysis, "status", "--porcelain") == ""
    remote = _origin_blob(origin, "issue-9.md")
    assert "from A" in remote and "from B" in remote


# ------------------------------------------------------------------------- failure and recovery


def _break_remote(c):
    _git(c.root, "remote", "set-url", "origin", str(c.root / "no-such-origin.git"))


def _fix_remote(c, origin):
    _git(c.root, "remote", "set-url", "origin", str(origin))


def test_a_failing_push_never_changes_the_recorded_outcome(clones, origin, capsys):
    a, _ = clones
    _bootstrap(a)
    a.note("issue-4.md", "# Issue #4\n")
    _break_remote(a)

    outcome = a.record()

    assert outcome == "done"
    recorded = [e for e in journal_events(loop.ledger, a.d) if e["kind"] == "retro"]
    assert len(recorded) == 1 and recorded[0]["grade"] == "achieved"
    err = capsys.readouterr().err
    assert "knowledge notes publish" in err
    assert "issue-4.md" not in _origin_files(origin)


def test_a_failed_push_is_retried_by_the_next_record_with_no_new_note(clones, origin):
    """B1. The note is committed locally when the push fails; the next record has nothing new to
    stage and must STILL push, or a machine's last note never ships."""
    a, _ = clones
    _bootstrap(a)
    a.note("issue-4.md", "# Issue #4\n")
    _break_remote(a)
    a.record()
    assert a.ahead() == 1
    _fix_remote(a, origin)

    a.record(goal="0002-y.md")

    assert "issue-4.md" in _origin_files(origin)
    assert a.ahead() == 0


def test_a_second_publish_on_the_same_machine_does_not_drive_the_worktree(clones, origin):
    """S3: the lock is non-blocking and kernel-mediated; a holder makes publish defer without staging."""
    a, _ = clones
    _bootstrap(a)
    a.note("issue-6.md", "# Issue #6\n")
    lock = open(sync.knowledge_lock_path(a.d), "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        out = _publish(a)
    finally:
        lock.close()

    assert "deferred" in out and "lock" in out
    assert _git(a.analysis, "status", "--porcelain", "--untracked-files=all").startswith("??")
    assert "issue-6.md" not in _origin_files(origin)
    assert _publish(a) == "published"


def test_publish_refuses_a_secret_shaped_note_and_never_prints_it(clones, origin):
    """#2718 prerequisite: notes are agent prose with no reviewed PR, so publish fails closed on the
    shared recogniser. `ghp_` + 24 alphanumerics is a shape today's table matches; #2718 widens it."""
    a, _ = clones
    _bootstrap(a)
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    a.note("issue-5.md", f"# Issue #5\nthe agent pasted {token} here\n")
    a.note("issue-8.md", "# Issue #8\nclean\n")

    out = _publish(a)

    assert out.startswith("publish refused") and "issue-5.md" in out
    assert token not in out and "A1b2C3d4" not in out
    assert _git(a.analysis, "diff", "--cached", "--name-only") == "", "the gate must run before staging"
    files = _origin_files(origin)
    assert "issue-5.md" not in files and "issue-8.md" not in files


def test_bootstrap_refuses_a_secret_shaped_note_already_on_disk(clones, origin):
    """Review round 1, B1: bootstrap is the FIRST gesture every existing user runs, over their whole
    corpus, so the carried-in notes must meet the same gate a later note does. `init` stages only
    the scaffold; the notes wait for `publish`, which refuses and names the file."""
    a, _ = clones
    token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8"
    a.note("issue-1.md", f"# Issue #1\n{token}\n")
    a.note("issue-2.md", "# Issue #2\nclean\n")

    out = _bootstrap(a)

    assert "publish refused" in out and "issue-1.md" in out
    assert token not in out and "Z9y8X7w6" not in out
    files = _origin_files(origin) or []
    assert "issue-1.md" not in files and "issue-2.md" not in files
    assert _git(a.analysis, "diff", "--cached", "--name-only") == ""
    assert (a.analysis / "issue-1.md").read_text().count(token) == 1, "the local note is left intact"


def test_files_that_are_not_notes_never_leave_the_machine(clones, origin):
    """Review round 1, B2: the docs say ONLY `analysis/*.md` leaves the machine, so git must enforce
    it — a stray `creds.json` or `.txt` beside the notes is ignored by the worktree's own
    `.gitignore`, whatever it contains, and a nested note still ships."""
    a, _ = clones
    _bootstrap(a)
    token = "ghp_" + "M1n2B3v4C5x6Z7l8K9j0H1g2"
    (a.analysis / "creds.json").write_text(json.dumps({"token": token}))
    (a.analysis / "issue-2.txt").write_text(f"{token}\n")
    (a.analysis / "sub").mkdir()
    (a.analysis / "sub" / "issue-3.md").write_text("# Issue #3\nnested\n")
    a.note("issue-2.md", "# Issue #2\nclean\n")

    assert _publish(a) == "published"

    files = set(_origin_files(origin))
    assert {"issue-2.md", "sub/issue-3.md"} <= files
    assert not [f for f in files if f.endswith((".json", ".txt"))], files
    assert _git(a.analysis, "status", "--porcelain", "--untracked-files=all") == ""


def test_publish_refuses_a_secret_in_a_note_whose_name_git_would_quote(clones, origin):
    """Review round 1, B3: `git status --porcelain` C-quotes a non-ASCII name, and a gate that then
    cannot open the quoted path must REFUSE, not pass. `-z` gives the bytes unquoted."""
    a, _ = clones
    _bootstrap(a)
    token = "ghp_" + "Q1w2E3r4T5y6U7i8O9p0A1s2"
    a.note("issue-é.md", f"# Issue é\n{token}\n")

    out = _publish(a)

    assert out.startswith("publish refused") and "issue-é.md" in out
    assert token not in out
    assert "issue-é.md" not in (_origin_files(origin) or []) and \
        not [f for f in _origin_files(origin) if "303" in f]


def test_the_hop_is_a_bounded_subprocess_with_no_terminal_prompt(clones, monkeypatch):
    """The hop runs through `run_with_timeout.py` (whole process GROUP killed on overrun, so a hung
    `git push`/ssh underneath cannot outlive the bound), with stdin closed so no host-key prompt
    can block on a tty, and `GIT_TERMINAL_PROMPT=0`."""
    a, _ = clones
    _bootstrap(a)
    a.note("issue-2.md", "# Issue #2\n")
    real_run = loop.subprocess.run
    calls = []

    def recording_run(argv, *args, **kw):
        if any(str(x).endswith("sync.py") for x in argv):
            calls.append(([str(x) for x in argv], kw))
        return real_run(argv, *args, **kw)

    monkeypatch.setattr(loop.subprocess, "run", recording_run)

    a.record()

    verbs = [argv[argv.index(next(x for x in argv if x.endswith("sync.py"))) + 1] for argv, _ in calls]
    assert verbs == ["publish", "pull"]
    for argv, kw in calls:
        assert argv[1].endswith("run_with_timeout.py"), "not process-group bounded"
        assert float(argv[2]) > 0
        assert argv[-2:] == ["--channel", "knowledge"]
        assert kw.get("timeout"), "an unbounded git call can wedge a completed goal"
        assert kw["stdin"] == loop.subprocess.DEVNULL
        assert kw["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_deleting_a_published_note_publishes_the_deletion_and_never_wedges(clones, origin):
    """Review round 2, BL-1: a deletion has no content to leak, so the gate must not treat the
    missing file as unreadable-therefore-secret. Curating a stale note (`kg.py maintain` flags them)
    is an ordinary gesture; it must ship as a deletion and never block the next note."""
    a, _ = clones
    _bootstrap(a)
    a.note("issue-2.md", "# Issue #2\nstale\n")
    assert _publish(a) == "published"
    (a.analysis / "issue-2.md").unlink()

    assert _publish(a) == "published"

    assert "issue-2.md" not in _origin_files(origin)
    assert _pull(a) == "pulled"
    a.note("issue-9.md", "# Issue #9\nnext\n")
    assert _publish(a) == "published" and "issue-9.md" in _origin_files(origin)
    _git(a.analysis, "rm", "-q", "issue-9.md")                  # a staged deletion, the other shape
    assert _publish(a) == "published" and "issue-9.md" not in _origin_files(origin)


def test_a_stale_worktree_gitignore_is_rewritten_to_the_scaffold_before_publishing(clones, origin):
    """Review round 2, S1: the `.gitignore` order is load-bearing, so it is scaffold-owned — a
    worktree carrying an older shape is rewritten at publish, never appended to."""
    a, _ = clones
    _bootstrap(a)
    (a.analysis / ".gitignore").write_text("graphify-out/\n*-out/\n")
    (a.analysis / "graphify-out").mkdir()
    (a.analysis / "graphify-out" / "README.md").write_text("a graph build\n")
    a.note("issue-2.md", "# Issue #2\n")

    assert _publish(a) == "published"

    files = _origin_files(origin)
    assert "issue-2.md" in files and not [f for f in files if f.startswith("graphify-out/")]
    assert _origin_blob(origin, ".gitignore") == "\n".join(sync.KNOWLEDGE_GITIGNORE)


def test_publish_returns_a_status_string_when_git_itself_fails(clones):
    """Review round 2, S2: `publish()`'s contract is "always returns a status string; never raises",
    and the knowledge branch runs on `record`'s path. A COMMIT-LESS embedded repository under
    `analysis/` makes `git add -A` fail outright (one with a commit is a different case — the gitlink
    test below); that must come back as a deferral, not a traceback."""
    a, _ = clones
    _bootstrap(a)
    subprocess.run(["git", "init", "-q", str(a.analysis / "vendor")], check=True)
    (a.analysis / "vendor" / "issue-1.md").write_text("# inside\n")

    out = _publish(a)

    assert out.startswith("publish deferred"), out


def test_a_note_deleted_on_one_machine_and_edited_on_another_keeps_the_edit_and_never_wedges(
        clones, origin, monkeypatch):
    """Review round 3, F1. `*.md merge=union` cannot resolve modify/delete (a `DU`/`UD` conflict,
    not a content merge), so without a resolver B's rebase aborted forever and every NEW note B
    wrote afterwards never left the machine — a permanent state wearing a transient message.
    The resolver KEEPS THE EDIT (measured: during a rebase `DU` = origin deleted, our replayed
    commit modified → `checkout --theirs`; `UD` the reverse → `--ours`). `GIT_EDITOR` is set to
    something that FAILS: `rebase --continue` opens an editor and an exported GIT_EDITOR outranks
    `-c core.editor`, so the resolver must neutralise it itself (round-4 review S2)."""
    monkeypatch.setenv("GIT_EDITOR", "false")
    a, b = _two_bootstrapped(clones)
    a.note("issue-2.md", "# Issue #2\nv1\n")
    assert _publish(a) == "published"
    assert _pull(b) == "pulled" and (b.analysis / "issue-2.md").exists()
    (a.analysis / "issue-2.md").unlink()
    assert _publish(a) == "published"                        # A's deletion is on origin
    b.note("issue-2.md", "# Issue #2\nv1\nB's edit\n")        # B edits the note A deleted

    assert _publish(b) == "published"

    assert _origin_blob(origin, "issue-2.md") == "# Issue #2\nv1\nB's edit"
    b.note("issue-3.md", "# Issue #3\nnext\n")
    assert _publish(b) == "published" and "issue-3.md" in _origin_files(origin)
    for path in ("rebase-merge", "rebase-apply"):
        assert not pathlib.Path(_git(b.analysis, "rev-parse", "--git-path", path)).exists()
    assert _git(b.analysis, "status", "--porcelain") == ""
    # the reverse direction: B deletes what A has since edited; the edit survives on B's pull
    assert _pull(a) == "pulled"
    (b.analysis / "issue-3.md").unlink()
    assert _publish(b) == "published"
    a.note("issue-3.md", "# Issue #3\nnext\nA's edit\n")
    assert _publish(a) == "published"
    assert _origin_blob(origin, "issue-3.md") == "# Issue #3\nnext\nA's edit"
    assert _pull(b) == "pulled" and (b.analysis / "issue-3.md").read_text().endswith("A's edit\n")


def test_an_unresolvable_conflict_defers_with_a_distinct_message_naming_the_file(clones, origin):
    """The non-.md scaffold has no union attribute, so a genuine content conflict on it is the one
    shape the resolver must NOT paper over: abort as before, but say WHICH file and point at the
    lever — a generic `[rejected] (fetch first)` forever is what round 3 measured."""
    a, b = _two_bootstrapped(clones)
    (a.analysis / ".gitattributes").write_text("*.md merge=union\n# A\n")
    assert _publish(a) == "published"
    (b.analysis / ".gitattributes").write_text("*.md merge=union\n# B\n")

    out = _publish(b)

    assert out.startswith("publish deferred: note conflict in .gitattributes"), out
    assert "When a note conflicts" in out
    for path in ("rebase-merge", "rebase-apply"):
        assert not pathlib.Path(_git(b.analysis, "rev-parse", "--git-path", path)).exists()


def test_an_embedded_repository_with_a_commit_never_ships_as_a_gitlink(clones, origin):
    """Review round 3, F2. `!*/` re-includes the directory, `_secret_shaped` sees a directory, and
    `git add -A` stages a committed embedded repo as a `160000` gitlink that lands on origin — a
    non-note leaving the machine despite the docs' claim. The gitlink is unstaged and named; the
    notes beside it still publish."""
    a, _ = clones
    _bootstrap(a)
    for name in ("vendor", "vendör"):           # the second: a path git C-quotes (round-4 B2)
        vendor = a.analysis / name
        subprocess.run(["git", "init", "-q", str(vendor)], check=True)
        _git(vendor, "config", "user.email", "v@e"); _git(vendor, "config", "user.name", "V")
        _git(vendor, "config", "commit.gpgsign", "false")
        (vendor / "issue-1.md").write_text("# inside\n")
        _git(vendor, "add", "-A"); _git(vendor, "commit", "-qm", "inner")
    a.note("issue-2.md", "# Issue #2\n")

    out = _publish(a)

    assert out.startswith("published") and "vendor" in out and "vendör" in out, out
    assert '"' not in out, "the path must be named as it is on disk, not C-quoted"
    assert "issue-2.md" in _origin_files(origin)
    tree = _git(origin, "ls-tree", "-r", BRANCH)
    assert "160000" not in tree and "vend" not in tree, tree
    assert _git(a.analysis, "diff", "--cached", "--name-only") == ""


def test_a_pull_the_rebase_refuses_outright_defers_and_names_the_reason(clones, origin):
    """Review round 4, B1. The resolver must tell "the rebase CONFLICTED" from "the rebase never
    started" (a dirty tracked note after a refused publish): the latter is not a conflict to settle,
    and a quiet `pulled` there hides a machine that is no longer receiving anyone's notes — the
    LIVENESS failure shape, and worse than the wedge F1 fixed because that one at least said so."""
    a, b = _two_bootstrapped(clones)
    a.note("issue-1.md", "# Issue #1\nv1\n")
    assert _publish(a) == "published" and _pull(b) == "pulled"
    b.note("issue-1.md", "# Issue #1\nv1\nghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2" + "\n")   # tracked, dirty
    assert _publish(b).startswith("publish refused")
    a.note("issue-8.md", "# Issue #8\n")
    assert _publish(a) == "published"

    out = _pull(b)

    assert out.startswith("pull deferred:") and "unstaged" in out, out
    assert not (b.analysis / "issue-8.md").exists(), "a deferred pull must not have quietly pulled"


def test_a_rename_beside_a_modify_delete_conflict_ships_both(clones, origin):
    """Review round 4, S1. `status --porcelain -z` emits a rename as `R  new\\0old\\0`; a resolver
    that read every chunk as a status code took the OLD path as a bogus entry and deferred forever
    naming a file that does not exist. Unmerged entries come from `ls-files -u -z` instead."""
    a, b = _two_bootstrapped(clones)
    a.note("issue-2.md", "# Issue #2\nv1\n")
    b.note("UX.md", "# UX\n")                    # a name whose first two chars carry a `U`
    assert _publish(a) == "published" and _publish(b) == "published"
    assert _pull(b) == "pulled" and _pull(a) == "pulled"
    (a.analysis / "issue-2.md").unlink()
    assert _publish(a) == "published"
    b.note("issue-2.md", "# Issue #2\nv1\nB's edit\n")
    _git(b.analysis, "mv", "UX.md", "other.md")

    assert _publish(b) == "published"

    files = _origin_files(origin)
    assert "other.md" in files and "UX.md" not in files and "issue-2.md" in files
    assert _origin_blob(origin, "issue-2.md").endswith("B's edit")


def test_the_ledger_channel_is_unchanged(clones, origin):
    a, _ = clones
    assert sync.is_worktree(a.d) is False                       # one-arg form, ledger path
    assert sync.publish(a.d, a.config()).startswith("not a worktree")
    assert not (a.analysis / ".git").exists()
    assert sync.bootstrap(a.d, a.config()).startswith("ledger worktree ready")
    assert (a.sdlc / "ledger" / ".git").exists() and not (a.analysis / ".git").exists()
    # the ledger's deferral wording is what watch_daemon's log readers and test_loop's stubs expect
    _break_remote(a)
    with open(sync.ledger.entry_file(a.d, "a"), "a") as fh:
        fh.write('{"kind":"note","actor":"a"}\n')
    assert sync.publish(a.d, a.config()).startswith("publish deferred (will retry next tick): ")
    _fix_remote(a, origin)

"""#910: the five `risk_*` gates in `ledger.GATE_KINDS` had zero writers, so a downstream reader's gate
matrix could never tell "this change needed a security review" from "this change did not". Three of
them now get one — `work.py commit()` runs risk-detect.sh over the STAGED change and records each
matched category as a `gate` event with verdict `absent`.

What these guards are actually protecting, in the order the change can rot:

1. the category -> gate map is not a `"risk_" + category` join (`sensitive` -> `risk_security`);
2. a matched category really does land a gate event, end to end, through the documented gesture;
3. the verdict is NEVER `pass`/`block` — risk-detect.sh knows the trigger, not the outcome;
4. an unmatched category emits NOTHING (a gate row means "this gate applied" — #911's distinction);
5. `risk_release`/`risk_debug`, which no detector produces, stay unwritten;
6. `loop.py emit` still refuses a hand-typed risk gate, and risk_* stays reliability class 2.

Every one of these was run RED before being trusted — see the PR body for the mutation and the
failure text of each.
"""
import importlib.util, json, os, pathlib, subprocess, sys
from journal_events import journal_events

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, m)
    spec.loader.exec_module(m)
    return m


work = _load("work")
state = _load("state")
ledger = _load("ledger")

#: The AND-gate every EVENTS-stream write sits behind (ledger.enabled AND journal.enabled),
#: copied from tests/test_work.py's own JOURNAL_ON constant so these read the same way.
CFG = {"work": {"enabled": True}, "action_log": {"enabled": True},
       "ledger": {"enabled": True, "actor": "rae"}, "journal": {"enabled": True}}


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _sdlc(tmp_path):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(CFG))
    state.start_run(str(d))
    return str(d)


def _worktree(tmp_path, files):
    """A REAL git repo at the goal's worktree path with `files` staged — risk-detect.sh reads git,
    so a fake path would fail open to `matched: []` and every assertion below would pass vacuously."""
    wt = tmp_path / ".sdlc" / "work" / "0001-x"
    wt.mkdir(parents=True, exist_ok=True)
    _git(wt, "init", "-q")
    for name, body in files.items():
        p = wt / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(wt, "add", "-A")
    return wt


def _started(sdlc_dir, wt):
    work._save(sdlc_dir, "0001-x.md", {"worktree": str(wt), "branch": "sdlc/0001-x",
                                       "base": "main", "remote": "origin", "pr": "7"})
    return "0001-x.md"


def _runner(staged):
    """work.commit's git channel, injected. It must NOT actually commit: risk-detect.sh is called
    between the stage and the commit, and a real `git commit` here would empty the index the
    detector reads and make the test measure the wrong moment."""
    calls = []

    def run(cwd, argv):
        line = " ".join(str(a) for a in argv)
        calls.append(line)
        if "diff --cached --name-only" in line:
            return "\n".join(staged)
        if "--name-status -z" in line:
            return "".join(f"A\0{n}\0" for n in staged)
        return ""

    run.calls = calls
    return run


def _gates(sdlc_dir):
    return [e for e in journal_events(ledger, sdlc_dir) if e.get("kind") == "gate"]


def _commit(tmp_path, files):
    """The whole gesture, exactly as the loop performs it: `work.py commit` on a started goal."""
    d = _sdlc(tmp_path)
    wt = _worktree(tmp_path, files)
    goal = _started(d, wt)
    out = work.commit(d, CFG, goal, run=_runner(sorted(files)), message="feat: x")
    assert out == "committed on sdlc/0001-x", out
    return d


# --------------------------------------------------------------------------- 1. the mapping

def test_the_category_map_is_not_a_string_join():
    """`sensitive` records against `risk_security`. A `"risk_" + category` join writes
    `risk_sensitive`, which is not in the gate vocabulary at all — and `ledger.append` does not
    raise on an out-of-vocabulary gate, so the forgery would land silently."""
    assert work._RISK_CATEGORY_GATES["sensitive"] == "risk_security"
    assert set(work._RISK_CATEGORY_GATES) == {"migration", "contract", "sensitive"}
    for category, gate in work._RISK_CATEGORY_GATES.items():
        assert gate in ledger.GATE_KINDS, f"{gate!r} is not in ledger.GATE_KINDS"
        assert gate != f"risk_{category}" or category != "sensitive"


def test_the_two_gates_with_no_detector_are_never_mapped():
    """`risk_release`/`risk_debug` are in the vocabulary and nothing in the kit produces either
    category. A row for a gate nothing measures is worse than the silence it replaces."""
    assert {"risk_release", "risk_debug"} <= set(ledger.GATE_KINDS)
    assert not {"risk_release", "risk_debug"} & set(work._RISK_CATEGORY_GATES.values())


# --------------------------------------------------------------------------- 2/3. end to end

def test_commit_records_a_matched_category_as_an_absent_risk_gate(tmp_path):
    """The arrival test: a staged migration produces one `risk_migration` gate event on the goal."""
    d = _commit(tmp_path, {"db/001_migration.sql": "ALTER TABLE users ADD COLUMN age int;\n"})
    rows = [e for e in _gates(d) if e["gate"] == "risk_migration"]
    assert len(rows) == 1, _gates(d)
    assert rows[0]["verdict"] == "absent"
    assert rows[0]["goal"] == "0001-x.md"
    assert "migration" in rows[0]["why"]


def test_every_detectable_category_records_its_own_gate(tmp_path):
    d = _commit(tmp_path, {
        "db/001_migration.sql": "ALTER TABLE users ADD COLUMN age int;\n",
        "src/routes.js": 'app.post("/pay", handler)\n',
        "auth.py": "def login():\n    pass\n",
    })
    assert {e["gate"] for e in _gates(d)} == {"risk_migration", "risk_contract", "risk_security"}


def test_a_risk_gate_is_never_verdicted_pass_or_block(tmp_path):
    """risk-detect.sh's own header: it "detects the TRIGGER only; it does not run the risk skills
    and cannot verify they ran". A `pass` off that signal claims a review that never happened."""
    d = _commit(tmp_path, {
        "db/001_migration.sql": "ALTER TABLE users ADD COLUMN age int;\n",
        "auth.py": "def login():\n    pass\n",
    })
    rows = [e for e in _gates(d) if e["gate"].startswith("risk_")]
    assert rows, "no risk gate emitted at all — this guard would pass vacuously"
    assert {e["verdict"] for e in rows} == {"absent"}


def test_the_gate_also_reaches_the_action_log(tmp_path):
    """Both write sites, the same pair `post_review`/`merge`/`test_trust` already use."""
    d = _commit(tmp_path, {"auth.py": "def login():\n    pass\n"})
    entries = _load("actionlog").read_goal(d, "0001-x.md")
    rows = [e for e in entries if e.get("kind") == "gate"]
    assert [e["gate"] for e in rows] == ["risk_security"]
    assert rows[0]["verdict"] == "absent"


# --------------------------------------------------------------------------- 4. the silence

def test_a_category_that_did_not_fire_emits_nothing(tmp_path):
    """#910 judgement call 2, pinned. A `gate` row means "this gate APPLIED to this change" —
    #911's "applicable as a recorded fact". A change with no risk surface records no risk gate at
    all; emitting one would empty that meaning, and there is no verdict for "not applicable"."""
    d = _commit(tmp_path, {"README.md": "hello\n"})
    assert [e for e in _gates(d) if e["gate"].startswith("risk_")] == []


def test_only_the_categories_that_fired_are_recorded(tmp_path):
    """The sharper half of the same call: one matched category must not drag its two siblings in."""
    d = _commit(tmp_path, {"auth.py": "def login():\n    pass\n"})
    assert [e["gate"] for e in _gates(d) if e["gate"].startswith("risk_")] == ["risk_security"]


def test_nothing_is_emitted_when_the_commit_itself_fails(tmp_path):
    """Scan before, emit after. A `git commit` that raises must not leave a gate record for a
    change that never reached the branch."""
    d = _sdlc(tmp_path)
    wt = _worktree(tmp_path, {"db/001_migration.sql": "ALTER TABLE users ADD COLUMN age int;\n"})
    goal = _started(d, wt)
    run = _runner(["db/001_migration.sql"])
    inner = run

    def boom(cwd, argv):
        if "commit -m" in " ".join(str(a) for a in argv):
            raise RuntimeError("simulated: git commit failed")
        return inner(cwd, argv)

    try:
        work.commit(d, CFG, goal, run=boom, message="feat: x")
    except RuntimeError:
        pass
    else:
        raise AssertionError("the injected commit failure did not propagate")
    assert [e for e in _gates(d) if e["gate"].startswith("risk_")] == []


# --------------------------------------------------------------------------- the scan site

def test_the_detector_is_blind_once_the_change_is_committed(tmp_path):
    """WHY THE EMITTER LIVES IN commit() AND NOT IN THE REVIEW PHASE. `sigma-review`'s prose runs
    this same detector, but the loop reviews the PR's diff post-commit and post-push — and the
    tripwire reads the working tree, the index and untracked files, all three empty by then. This
    is the measurement that settled the site; if it ever stops holding, a Review-phase emitter
    becomes viable and this comment is why it was not built there."""
    wt = _worktree(tmp_path, {"db/001_migration.sql": "ALTER TABLE users ADD COLUMN age int;\n"})
    assert work._risk_categories(str(wt)) == ["migration"]
    _git(wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")
    assert work._risk_categories(str(wt)) == [], \
        "risk-detect.sh saw the change AFTER the commit — the Review-phase site may now be viable"


def _real_runner(wt):
    """A runner that ACTUALLY runs git in `wt`, instead of faking its output.

    THIS IS THE POINT OF IT. Every other test here injects a runner whose `git commit` does
    nothing, so the index never empties and moving the scan to AFTER the commit changes nothing —
    measured: that mutation came back GREEN against all of them. Only a real commit reproduces the
    moment the detector goes blind, which is the entire reason the scan sits where it does."""
    calls = []

    def run(cwd, argv):
        argv = [str(a) for a in argv]
        calls.append(" ".join(argv))
        if argv[:2] == ["git", "commit"]:       # a repo with no committer identity cannot commit
            argv = ["git", "-c", "user.email=t@t", "-c", "user.name=t"] + argv[1:]
        p = subprocess.run(argv, cwd=str(wt), capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(" ".join(argv) + ": " + (p.stderr or p.stdout))
        return p.stdout.strip()

    run.calls = calls
    return run


def test_the_scan_happens_while_the_change_is_still_staged(tmp_path):
    """The guard for the SITE, run against a real `git commit` rather than a faked one. Move
    `_risk_categories(path)` below the commit line — the Review-phase position — and this goes red
    with an empty gate list, because by then the working tree, the index and the untracked set are
    all empty and the tripwire has nothing left to read."""
    d = _sdlc(tmp_path)
    wt = _worktree(tmp_path, {"db/001_migration.sql": "ALTER TABLE users ADD COLUMN age int;\n"})
    goal = _started(d, wt)
    run = _real_runner(wt)
    assert work.commit(d, CFG, goal, run=run, message="feat: x") == "committed on sdlc/0001-x"
    assert run.calls == ["git add -A", "git diff --cached --name-only", "git commit -m feat: x"]
    # the commit really happened, so the detector really is blind now
    assert subprocess.run(["git", "-C", str(wt), "diff", "--cached", "--name-only"],
                          capture_output=True, text=True).stdout.strip() == ""
    assert [e["gate"] for e in _gates(d)] == ["risk_migration"]


def test_risk_categories_fails_open_on_a_non_git_path(tmp_path):
    """The journal never fails a commit: an unreadable tree yields [], not an exception."""
    assert work._risk_categories(str(tmp_path / "nope")) == []


def test_commit_adds_no_git_call_to_the_ordinary_path(tmp_path):
    """The byte-identity guarantee tests/test_work.py pins, re-checked from this side: the risk scan
    is a `subprocess` call to bash, deliberately NOT routed through the injected `run`, so it cannot
    appear in the ordered command list that test compares."""
    d = _sdlc(tmp_path)
    wt = _worktree(tmp_path, {"db/001_migration.sql": "ALTER TABLE users ADD COLUMN age int;\n"})
    goal = _started(d, wt)
    run = _runner(["db/001_migration.sql"])
    work.commit(d, CFG, goal, run=run, message="feat: x")
    assert run.calls == ["git add -A", "git diff --cached --name-only", "git commit -m feat: x"]


# --------------------------------------------------------------------------- 6. anti-forgery

def test_loop_emit_still_refuses_a_hand_typed_risk_gate(tmp_path):
    """#910 records these gates from a DERIVED verdict, so it needed no agent-typable
    `--gate risk_security` — and adding one would hand an agent exactly the forged
    `risk_security pass` that allowlist exists to prevent. The documented gesture, refused."""
    loop = _load("loop")
    assert "risk_security" not in loop._EMIT_GATE_KINDS
    err = loop._validate_event("gate", {"gate": "risk_security", "verdict": "pass"},
                               kind_allowlist=loop._EMIT_KINDS)
    assert err and "risk_security" in err, err


def test_the_gate_vocabulary_already_declared_these(tmp_path):
    """No contract change was needed: contract/vocabulary.json already carried all five
    risk gates and `absent` as a verdict. A future edit that drops either breaks the emitter."""
    vocab = json.loads((ROOT / "contract" / "vocabulary.json").read_text())
    assert set(work._RISK_CATEGORY_GATES.values()) <= set(vocab["gate_kinds"])
    assert "absent" in vocab["verdicts"]

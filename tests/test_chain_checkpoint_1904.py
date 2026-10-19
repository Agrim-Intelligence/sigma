"""#1904: a checkpoint -- an issue that stands for "a named unit's code is on the integration branch".

It is never picked or armed, `Blocked by` on it is a real edge, the chain stops with the reason `checkpoint`
and names it when it is the only thing in the way, it is met when its unit lands (local git ancestry, a real
scratch repository here) or when a person closes it, the doctor does not call it a deadlock, `deps.py mark`
can write a wait on it, and the sheet shows it in its own block. A stateful fake `gh`; no network.

Controls (each clause alone, then restored; every one was seen red): the verdict ignores the label; the label
is not an overlay of the eligibility rule; the arm step labels or arms it; the stop reason is dropped; the
landing test says waiting / says met whatever git says; the sheet block is dropped; the doctor row flags it;
the membership grant (`blockers.classify`) ignores it; the run counts it as a member that must close.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resource_mode  # noqa: E402,F401
import test_chain_arm as tca  # noqa: E402
import test_chain_run as tcr  # noqa: E402
import test_label_answer as tla  # noqa: E402
from test_doctor import _CFG, _doc, _scan_run, gh_env  # noqa: E402,F401
from test_label_drain_arm import DW, LBL, _mod, _repo  # noqa: E402

CP = "sdlc:checkpoint"
GENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, env=GENV)


def _scratch_repo(root):
    """main with units `landed` and `unregistered` merged in (and the remote-tracking ref set), unit `open` not merged."""
    root = pathlib.Path(root)
    _git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "base")
    for unit, merged in (("landed", True), ("open", False), ("unregistered", True)):
        _git(root, "checkout", "-q", "-b", "feature/" + unit, "main")
        (root / (unit + ".txt")).write_text(unit)
        _git(root, "add", unit + ".txt")
        _git(root, "commit", "-q", "-m", unit)
        _git(root, "checkout", "-q", "main")
        if merged:
            _git(root, "merge", "-q", "--no-ff", "-m", "land " + unit, "feature/" + unit)
    _git(root, "update-ref", "refs/remotes/origin/main", "main")
    sdlc = root / ".sdlc"
    (sdlc / "features").mkdir(parents=True, exist_ok=True)
    reg = _mod("feature_registry")
    (sdlc / "features" / "index.json").write_text(reg.dumps(reg.document(
        {u: {"title": u, "open": True} for u in ("landed", "open", "nobranch")})))
    return sdlc


def _cp(n, unit, *more, state="open"):
    labels = [CP] + (["feature:" + unit] if unit else []) + list(more)
    return tla.issue(n, *labels, body=DW, state=state)


def _backlog(unit="open", cp_state="open"):
    return [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Blocked by #2\n\n" + DW), _cp(2, unit, state=cp_state)]


def _scene(tmp_path, unit="open", cp_state="open", issues=None, approve=False):
    """A scratch git repository, its registry, and a planned (optionally approved) live chain over `issues`."""
    _scratch_repo(tmp_path)
    base = _repo(tmp_path, "alice", {"work": {"base": "main"}})
    cp = pathlib.Path(base) / "config.json"
    cfg = json.loads(cp.read_text())
    cfg["discovery"]["dag"] = {"mode": "off"}
    cp.write_text(json.dumps(cfg))
    fk = tca.ChainFake(issues if issues is not None else _backlog(unit, cp_state))
    ch = tca._chain()
    ch.plan(base, 1, run=fk, now=tca.NOW)
    if approve:
        ch.answer(base, 1, defaults=True, run=fk)
        assert ch.answer(base, 1, {"cap:c1": "5"}, run=fk)["state"] == "approved"
    fk.calls.clear()
    fk.write_count = 0
    return base, fk


# 1 -- met when the unit lands ----------------------------------------------------------------------------

def test_status_is_met_waiting_or_unknown_from_local_git(tmp_path):
    sdlc = _scratch_repo(tmp_path)
    cc = _mod("chain_checkpoint")
    cfg = {"work": {"base": "main"}}
    assert cc.status(sdlc, cfg, "landed")["state"] == "met"
    assert cc.status(sdlc, cfg, "open")["state"] == "waiting"                 # exists, not contained in the base
    assert cc.status(sdlc, cfg, None)["state"] == "waiting"                   # names no unit: only a person closes it
    assert cc.status(sdlc, cfg, "nobranch")["state"] == "unknown"             # in the registry, no branch here
    assert cc.status(sdlc, cfg, "ghost")["state"] == "unknown"                # not in the registry, no branch
    assert cc.status(sdlc, cfg, "unregistered")["state"] == "unknown"         # landed, but nobody registered the unit
    assert cc.status(sdlc, {}, "landed")["state"] == "unknown"                # no integration branch configured


def test_status_runs_no_network_and_no_fetch(tmp_path):
    sdlc = _scratch_repo(tmp_path)
    seen = []
    real = _mod("feature_landed").make_runner()

    def spy(argv, cwd):
        seen.append(list(argv))
        return real(argv, cwd)
    assert _mod("chain_checkpoint").status(sdlc, {"work": {"base": "main"}}, "landed", run=spy)["state"] == "met"
    assert seen and all(a[0] == "git" and not {"fetch", "pull", "push", "ls-remote", "clone"} & set(a) for a in seen)


# 2 -- never picked, never armed --------------------------------------------------------------------------

def test_a_checkpoint_is_excluded_by_the_eligibility_rule_even_with_a_goal_label():
    src = _mod("sources").GitHubSource({"discovery": {"source": "github", "github": {"repo": "acme/widget"}}},
                                       run=lambda a: "")
    assert CP in src.not_eligible_labels() and CP in src.overlay_labels()
    assert src.checkpoint_label == CP and ("checkpoint_label", "c5def5") in src._LABEL_COLORS


@pytest.mark.parametrize("labels", [(CP,), (CP, "sdlc:goal"), (CP, "sdlc:blocking")])
def test_the_verdict_is_checkpoint_whatever_else_it_carries(tmp_path, labels):
    ld = _mod("label_drain")
    src = _mod("sources").GitHubSource({"discovery": {"source": "github", "github": {"repo": "acme/widget"}}},
                                       run=lambda a: "")
    judge = ld._Judge(str(tmp_path / ".sdlc"), {}, src)
    assert judge.verdict(tla.issue(2, *labels, body=DW))[0] == "checkpoint"


def test_classify_never_grants_membership_to_a_checkpoint():
    b = _mod("blockers")
    state = {"labels": {CP, "sdlc:followup"}, "assignees": [], "closed": False}
    args = ("sdlc:goal", "sdlc:needs-confirmation", "sdlc:parked", "sdlc:followup", "me")
    assert b.classify(state, *args) == b.ROUTED                      # without the label name it would be adopted
    assert b.classify(state, *args, checkpoint_label=CP) == b.CHECKPOINT
    assert b.classify(dict(state, closed=True), *args, checkpoint_label=CP) == b.STALE


def test_chain_arm_leaves_a_checkpoint_alone_and_labels_nothing_on_it(tmp_path):
    base, fk = _scene(tmp_path, approve=True)
    ch = tca._chain()
    res = ch.arm(base, 1, run=fk)
    assert [w for w in tca._writes(fk) if w[0] == 2] == []
    assert fk._names(2) == {CP, "feature:open"} and "chain:1" in fk._names(1) and "sdlc:goal" in fk._names(1)
    assert any("#2 is a checkpoint" in n for n in res["notes"]) and not res["refused"]
    text = ch.sheet(base, 1, now=tca.NOW)
    assert "#2 is a checkpoint: left entirely untouched" in text
    assert "#2: add label chain:1" not in text


def test_label_drain_arm_and_pull_never_arm_a_checkpoint(tmp_path):
    ld = _mod("label_drain")
    issues = [tla.issue(1, LBL, assignees=("alice",), body="Blocked by #2\n\n" + DW), _cp(2, None, "sdlc:goal")]
    ld2, base, fk, _cfg = tla.env(tmp_path, issues)
    res = ld2.arm(base, LBL, apply=True, run=fk)
    assert [w for w in fk.writes() if "issues/2" in " ".join(w)] == []
    assert any("#2 is a checkpoint" in n for n in res["notes"])
    got = ld2.pull(base, LBL, "2", 1, run=fk)
    assert got["result"] == "refused" and got["actions"] == []
    assert LBL not in fk._names(2)


# 3 -- Blocked by is a real edge; the chain waits and stops with the reason --------------------------------

def test_plan_keeps_the_checkpoint_as_an_edge_with_its_status(tmp_path):
    base, fk = _scene(tmp_path)
    rec = tca._rec(base)
    row = next(r for r in rec["members"] if r["number"] == 2)
    assert row["verdict"] == "checkpoint" and row["unit"] == "open"
    assert row["checkpoint"]["state"] == "waiting" and row["checkpoint"]["unit"] == "open"
    assert {"from": 2, "to": 1, "tag": "explicit"} in rec["edges"]
    assert next(r for r in rec["members"] if r["number"] == 1)["waits_on"] == [2]
    assert rec["complete"] is True
    assert not [q for q in tca._chain().questions_of(rec, {}) if q["id"].endswith(":2")]       # nothing is asked about it


def _armed_run(tmp_path, unit):
    base, fk = _scene(tmp_path, unit=unit, approve=True)
    ch = tca._chain()
    ch.arm(base, 1, run=fk, now=tca.NOW)
    fk.calls.clear()
    return ch, base, fk


def test_the_run_stops_with_the_reason_checkpoint_and_names_it(tmp_path):
    ch, base, fk = _armed_run(tmp_path, "open")
    res = tcr._run(ch, base, fk, tcr.FakePick(fk, base))
    assert res["exit"] == 3 and res["reason"] == "checkpoint" and res["reason"] in ch.STUCK_REASONS, res
    assert res["detail"].startswith("#2 is a checkpoint waiting for unit open to land")
    assert tca._rec(base)["run"]["stuck"] == "checkpoint"
    assert fk.writes() == []


def test_a_landed_unit_is_reported_met_and_says_the_one_step_that_releases_it(tmp_path):
    ch, base, fk = _armed_run(tmp_path, "landed")
    res = tcr._run(ch, base, fk, tcr.FakePick(fk, base))
    assert res["reason"] == "checkpoint" and "unit landed has landed" in res["detail"] and "close it" in res["detail"]
    text = ch.sheet(base, 1, now=tca.NOW)
    assert "#2 (unit landed): met" in text and "close it to release #1" in text


def test_the_checkpoint_is_named_only_when_it_is_the_only_thing_in_the_way(tmp_path):
    issues = [tla.issue(1, "sdlc:goal", assignees=("alice",), body="Blocked by #2\nBlocked by #3\n\n" + DW),
              _cp(2, "open"), tla.issue(3, body=DW)]
    base, fk = _scene(tmp_path, issues=issues, approve=True)
    ch = tca._chain()
    ch.arm(base, 1, run=fk, now=tca.NOW)
    fk.by[3]["state"] = "closed"
    res = tcr._run(ch, base, fk, tcr.FakePick(fk, base))
    assert res["reason"] == "checkpoint"
    fk.by[1]["labels"].append({"name": "sdlc:parked"})            # something a person can act on comes first
    res = tcr._run(ch, base, fk, tcr.FakePick(fk, base))
    assert res["reason"] == "decision"


def test_a_closed_checkpoint_is_an_ordinary_closed_prerequisite(tmp_path):
    base, fk = _scene(tmp_path, cp_state="closed")
    rec = tca._rec(base)
    assert [r["number"] for r in rec["members"]] == [1] and rec["done"] == [2]


def test_an_open_checkpoint_never_keeps_the_chain_from_being_done(tmp_path):
    ch, base, fk = _armed_run(tmp_path, "open")
    fk.by[1]["state"] = "closed"
    res = tcr._run(ch, base, fk, tcr.FakePick(fk, base))
    assert res["state"] == "done", res


# 4 -- the doctor ------------------------------------------------------------------------------------------

def test_the_doctor_does_not_flag_a_checkpoint_as_blocking_without_a_goal(gh_env):
    d = _doc()
    run = _scan_run(json.dumps([{"number": 7, "labels": [{"name": "sdlc:blocking"}]},
                                {"number": 8, "labels": [{"name": "sdlc:blocking"}, {"name": CP}]},
                                {"number": 9, "labels": [{"name": "sdlc:blocking"}, {"name": "sdlc:goal"}]}]))
    assert d._unreachable_blocker_scan(_CFG, run) == [7]


# 5 -- deps.py can write a wait on it ---------------------------------------------------------------------

class _Src:
    checkpoint_label = CP

    def __init__(self, labels):
        self.labels, self.appended, self.notes = labels, [], []

    def _read_issue(self, n, fields, comment_limit=None):
        if str(n) == "5":
            return {"title": "t", "body": "needs the unit first", "state": "OPEN", "comments": []}
        return {"state": "OPEN", "labels": [{"name": x} for x in self.labels]}

    def append_to_body(self, goal, marker):
        self.appended.append(marker)

    def note(self, goal, text):
        self.notes.append(text)


def test_deps_mark_writes_a_wait_on_a_checkpoint():
    deps = _mod("deps")
    src = _Src([CP])
    verdict, detail = deps.mark(src, "5", "12", "the unit must land first")
    assert verdict == "MARKED" and src.appended == ["**Blocked by:** #12"] and "checkpoint" in detail
    plain = _Src(["sdlc:goal"])
    assert "checkpoint" not in deps.mark(plain, "5", "12", "")[1]


# 6 -- the docs name the label ----------------------------------------------------------------------------

def test_the_label_is_registered_in_the_label_model_and_the_dependency_docs():
    root = pathlib.Path(__file__).resolve().parent.parent
    assert "sdlc:checkpoint" in (root / "docs" / "label-model.md").read_text()
    assert "sdlc:checkpoint" in (root / "docs" / "dependency-map-and-chain.md").read_text()
    assert "checkpoint" in (root / "docs" / "chain.md").read_text()

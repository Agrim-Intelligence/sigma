"""Tests for the quality ratchet merge gate (#1637, PRD-0008 R-02): work.py's waiver store, its owner
verb, the gate function, its one call site in `merge()`, and the park texts' classification.

Each test first asserts its seam exists and reads dicts with `.get()`, so its first red is an
AssertionError. The gate tests hand `_quality_ratchet_refusal` a fake quality_delta whose `run`
returns canned `Result`s built from the REAL module's namedtuples, so the shapes cannot drift; the
waiver tests drive a real git worktree. No value here is a credential.
"""
import ast
import importlib.util
import json
import os
import pathlib
import subprocess

import pytest

import prfake

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


work = _load("work")
state = _load("state")
actionlog = _load("actionlog")
loop = _load("loop")
real_qd = _load("quality_delta")
D = real_qd.decimal.Decimal

ON = {"work": {"enabled": True}, "action_log": {"enabled": True}}
RATCHET = dict(ON, gates={"quality_ratchet": {"enabled": True}})
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
BASE = "c" * 40
GOAL = "0001-x.md"
STEM = "0001-x"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No global or system git config, a fixed identity; and no cloud/override env that flips merge()."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv("GIT_%s_NAME" % who, "t")
        monkeypatch.setenv("GIT_%s_EMAIL" % who, "t@example.invalid")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
                 "CLAUDE_CODE_REMOTE", "SIGMA_GH_GRAPHQL"):
        monkeypatch.delenv(name, raising=False)


def _git(cwd, *argv):
    done = subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL)
    return done.stdout.strip()


def _sdlc(tmp_path, config=None):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config or ON))
    state.start_run(str(d))
    return str(d)


def _started(sdlc_dir, goal=GOAL, pr="7", worktree=None):
    wt = pathlib.Path(worktree) if worktree else pathlib.Path(sdlc_dir).parent / ".sdlc" / "work" / STEM
    wt.mkdir(parents=True, exist_ok=True)
    work._save(sdlc_dir, goal, {"worktree": str(wt), "branch": "sdlc/%s" % STEM, "base": "main",
                                "remote": "origin", "pr": pr})
    return goal


def _real_worktree(tmp_path):
    """A real repository with one commit; maintenance and gc off so no background lock can flake."""
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "maintenance.auto", "false")
    _git(wt, "config", "gc.auto", "0")
    (wt / "f.txt").write_text("x\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "one")
    return wt, _git(wt, "rev-parse", "HEAD")


def _gate_rows(sdlc_dir, goal=GOAL):
    return [e for e in actionlog.read_goal(sdlc_dir, goal) if e.get("kind") == "gate"]


def _waive(sdlc_dir, goal=GOAL, metric="words", reason="owner accepts the regression", capsys=None):
    code = work.main(["work.py", "quality-waive", sdlc_dir, goal, "--metric", metric, "--reason", reason])
    out = capsys.readouterr() if capsys is not None else None
    return code, out


# ------------------------------------------------------------------------------------- T5: waivers

def test_quality_waive_is_create_only_bound_to_the_worktree_head_and_logged(tmp_path, capsys):
    assert callable(getattr(work, "quality_waiver_path", None)), "work.quality_waiver_path does not exist yet"
    d = _sdlc(tmp_path)
    wt, head = _real_worktree(tmp_path)
    _started(d, worktree=wt)
    code, out = _waive(d, capsys=capsys)
    assert code == 0, out
    path = pathlib.Path(d) / "state" / "gates" / "quality-waivers" / STEM / ("words-%s.json" % head)
    assert path.is_file(), sorted(str(p) for p in (pathlib.Path(d) / "state").rglob("*"))
    data = json.loads(path.read_text())
    assert (data.get("goal"), data.get("metric"), data.get("head"), data.get("reason")) == \
        (STEM, "words", head, "owner accepts the regression")
    assert isinstance(data.get("recorded_at"), int)
    rows = _gate_rows(d)
    assert len(rows) == 1 and rows[0].get("gate") == "merge" and rows[0].get("verdict") == "warn"
    assert rows[0].get("why", "").startswith("quality ratchet waiver recorded:"), rows
    again, out = _waive(d, reason="a second, different reason", capsys=capsys)
    assert again == 2, "a waiver is create-only: the first one stands"
    assert json.loads(path.read_text()).get("reason") == "owner accepts the regression"
    assert _waive(d, metric="../escape", capsys=capsys)[0] == 2
    assert _waive(d, metric="other", reason=" \x01\t ", capsys=capsys)[0] == 2
    assert _waive(d, metric="other", reason="x" * 501, capsys=capsys)[0] == 2
    assert len(_gate_rows(d)) == 1, "a refused waiver logs nothing"
    code, out = _waive(d, metric="other", reason="line one\x1b[31m red", capsys=capsys)
    assert code == 0, out
    stripped = json.loads((path.parent / ("other-%s.json" % head)).read_text()).get("reason")
    assert stripped == "line one [31m red", "control characters are stripped"


@pytest.mark.parametrize("field", ["goal", "metric", "head"])
def test_quality_waiver_valid_ignores_each_mismatch(tmp_path, field):
    assert callable(getattr(work, "_quality_waiver_valid", None)), "work._quality_waiver_valid does not exist yet"
    d = _sdlc(tmp_path)
    path = work.quality_waiver_path(d, GOAL, "words", HEAD)
    path.parent.mkdir(parents=True, exist_ok=True)
    good = {"goal": STEM, "metric": "words", "head": HEAD, "reason": "r", "recorded_at": 1}
    path.write_text(json.dumps(good))
    assert work._quality_waiver_valid(d, GOAL, "words", HEAD) is True
    assert work._quality_waiver_valid(d, GOAL, "words", OTHER_HEAD) is False, "a head with no file"
    wrong = dict(good, **{field: {"goal": "0002-y", "metric": "lines", "head": OTHER_HEAD}[field]})
    path.write_text(json.dumps(wrong))
    assert work._quality_waiver_valid(d, GOAL, "words", HEAD) is False, "one field differs: %s" % field
    path.write_text("[]")
    assert work._quality_waiver_valid(d, GOAL, "words", HEAD) is False
    assert work._quality_waiver_valid(d, GOAL, "../x", HEAD) is False


def test_finish_removes_only_this_goals_quality_waivers(tmp_path):
    assert callable(getattr(work, "quality_waiver_path", None)), "work.quality_waiver_path does not exist yet"
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    mine = work.quality_waiver_path(d, goal, "words", HEAD)
    theirs = work.quality_waiver_path(d, "0002-y.md", "words", HEAD)
    for path in (mine, theirs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    calls = []

    def run(cwd, argv):
        calls.append(" ".join(str(a) for a in argv))
        return ""
    assert "removed" in work.finish(d, ON, goal, run=run)
    assert not mine.parent.exists(), "this goal's waivers go with its work record"
    assert theirs.is_file(), "another goal's waivers are untouched"


# ------------------------------------------------------------------------------- T6: the gate itself

def _row(name="m", base=3, head=5, required=True, tolerance=0.0, direction="lower", reason=""):
    base = None if base is None else D(str(base))
    head = None if head is None else D(str(head))
    if base is not None and head is not None:
        verdict, delta = real_qd.verdict(direction, base, head), head - base
    elif head is not None:
        verdict, delta = None, None                            # a head-only reading
    else:
        verdict, delta = "unmeasured", None
    return real_qd.MetricRow(name, direction, required, float(tolerance), base, head, delta, verdict,
                             reason or ("" if head is not None else "the command failed"))


def _result(*rows, head=HEAD, base=BASE, code=0, refusal=None, unmeasured=None, no_metrics=False, deadline=900.0):
    return real_qd.Result(code, refusal, base, head, unmeasured, no_metrics, 1800, deadline, tuple(rows))


class FakeQD:
    """quality_delta's surface the gate uses: `run` (canned answers, in order) and `_NAME`."""
    _NAME = real_qd._NAME

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def run(self, goal, cwd, **kw):
        self.calls.append(dict(kw, goal=goal, cwd=cwd))
        assert self.answers, "quality_delta.run called more often than the test allows: %s" % self.calls
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _gate(tmp_path, monkeypatch, fake, config=RATCHET, vetted=HEAD):
    assert callable(getattr(work, "_quality_ratchet_refusal", None)), "work._quality_ratchet_refusal does not exist yet"
    d = _sdlc(tmp_path, config)
    loads = []
    monkeypatch.setattr(work, "_quality_delta", lambda: loads.append(1) or fake)
    rec = {"worktree": str(tmp_path / "wt")}
    out = work._quality_ratchet_refusal(d, config, GOAL, rec, vetted, actionlog)
    return d, out, loads


def _why(d):
    return [e.get("why", "") for e in _gate_rows(d)]


@pytest.mark.parametrize("config", [
    ON, dict(ON, gates={"quality_ratchet": False}), dict(ON, gates={"quality_ratchet": {"enabled": False}}),
    dict(ON, gates={"quality_ratchet": {}}), dict(ON, gates=True), dict(ON, gates={}),
], ids=["absent", "false", "enabled-false", "empty", "scalar-gates", "no-key"])
def test_ratchet_off_never_loads_quality_delta(tmp_path, monkeypatch, config):
    d, out, loads = _gate(tmp_path, monkeypatch, FakeQD(), config=config)
    assert out == "" and loads == [], "AC-2: with the gate off, quality_delta is never even loaded"
    assert _gate_rows(d) == []


def test_ratchet_parks_a_required_metric_worse_twice(tmp_path, monkeypatch):
    fake = FakeQD(_result(_row("words", 3, 5)), _result(_row("words", None, 6)))
    d, out, _loads = _gate(tmp_path, monkeypatch, fake)
    assert out.startswith("quality ratchet: required metric words worse beyond tolerance 0"), out
    assert "base=3 head=5, then head=6" in out and HEAD in out
    assert "An owner may record a waiver (the loop never runs this)" in out
    assert "work.py quality-waive %s %s --metric words" % (d, GOAL) in out
    assert out.endswith("--qkind failing_check") and "could not measure" not in out
    first, second = fake.calls
    assert first.get("committed") is True and first.get("head_only") is None
    assert second.get("committed") is True and second.get("head_only") == ("words",)
    (why,) = _why(d)
    assert why == ("quality ratchet: metric=words base=3 head=5 head2=6 tolerance=0 raw=worse -> park"), why


@pytest.mark.parametrize("direction,at,beyond", [("lower", "10.5", "10.501"), ("higher", "9.5", "9.499")])
def test_ratchet_tolerance_is_strict_both_directions(tmp_path, monkeypatch, direction, at, beyond):
    equal = FakeQD(_result(_row("m", 10, at, tolerance=0.5, direction=direction)))
    d, out, _loads = _gate(tmp_path / "eq", monkeypatch, equal)
    assert out == "" and len(equal.calls) == 1, "bad == tolerance is NOT beyond it: no re-measure, no park"
    over = FakeQD(_result(_row("m", 10, beyond, tolerance=0.5, direction=direction)),
                  _result(_row("m", None, beyond, tolerance=0.5, direction=direction)))
    d, out, _loads = _gate(tmp_path / "over", monkeypatch, over)
    assert out.startswith("quality ratchet: required metric m worse") and len(over.calls) == 2, out


def test_ratchet_second_reading_not_worse_does_not_park_and_logs_both(tmp_path, monkeypatch):
    fake = FakeQD(_result(_row("m", 3, 5), deadline=1234.5), _result(_row("m", None, 3)))
    d, out, _loads = _gate(tmp_path, monkeypatch, fake)
    assert out == "", "AC-6: worse once, then not worse, does not park"
    assert fake.calls[1].get("head_only") == ("m",) and fake.calls[1].get("deadline") == 1234.5
    (why,) = _why(d)
    assert "head=5" in why and "head2=3" in why and why.endswith("-> pass"), why


@pytest.mark.parametrize("case", ["first-row", "second-row", "whole-run"])
def test_ratchet_required_unmeasured_parks(tmp_path, monkeypatch, case):
    answers = {
        "first-row": [_result(_row("m", None, None, reason="head command timed out after 300s"))],
        "second-row": [_result(_row("m", 3, 5)), _result(_row("m", None, None, reason="head command exited 1"))],
        "whole-run": [_result(head=HEAD, base=None, unmeasured="no readable work record for this goal")],
    }[case]
    d, out, _loads = _gate(tmp_path, monkeypatch, FakeQD(*answers))
    assert out.startswith("quality ratchet could not measure: "), out
    assert out.endswith("--qkind no_evidence")
    assert {"first-row": "m: head command timed out", "second-row": "m: head command exited 1",
            "whole-run": "no readable work record"}[case] in out


def test_ratchet_second_reading_worse_but_within_tolerance_does_not_park(tmp_path, monkeypatch):
    """AC-1 is 'beyond its tolerance on two measurements': a second reading inside the tolerance clears it."""
    fake = FakeQD(_result(_row("m", 10, 12, tolerance=1)), _result(_row("m", None, 10.5, tolerance=1)))
    d, out, _loads = _gate(tmp_path, monkeypatch, fake)
    assert out == "" and len(fake.calls) == 2, out
    (why,) = _why(d)
    assert "head2=10.5" in why and why.endswith("-> pass"), why


def test_ratchet_optional_unmeasured_or_worse_records_and_continues(tmp_path, monkeypatch):
    fake = FakeQD(_result(_row("opt", None, None, required=False, reason="no number"),
                          _row("opt2", 3, 9, required=False), _row("req", 3, 3)))
    d, out, _loads = _gate(tmp_path, monkeypatch, fake)
    assert out == "" and len(fake.calls) == 1, "AC-4: a non-required metric never parks nor re-measures"
    whys = _why(d)
    assert len(whys) == 3, whys
    assert "metric=opt " in whys[0] and "raw=unmeasured" in whys[0] and whys[0].endswith("-> pass")
    assert "metric=opt2 " in whys[1] and "raw=worse" in whys[1] and whys[1].endswith("-> pass")


def _plant_waiver(d, goal, metric, head):
    path = work.quality_waiver_path(d, goal, metric, head)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"goal": work.stem(goal), "metric": metric, "head": head, "reason": "ok",
                                "recorded_at": 1}))


@pytest.mark.parametrize("waiver", ["exact", "other-goal", "other-metric", "other-head"])
def test_ratchet_waiver_lifts_only_an_exact_match(tmp_path, monkeypatch, waiver):
    assert callable(getattr(work, "_quality_ratchet_refusal", None)), "work._quality_ratchet_refusal does not exist yet"
    d = _sdlc(tmp_path, RATCHET)
    monkeypatch.setattr(work, "_quality_delta", lambda: fake)
    fake = FakeQD()
    goal, metric, head = {"exact": (GOAL, "m", HEAD), "other-goal": ("0002-y.md", "m", HEAD),
                          "other-metric": (GOAL, "n", HEAD), "other-head": (GOAL, "m", OTHER_HEAD)}[waiver]
    _plant_waiver(d, goal, metric, head)
    fake.answers = [_result(_row("m", 3, 5)), _result(_row("m", None, 5))]
    out = work._quality_ratchet_refusal(d, RATCHET, GOAL, {"worktree": str(tmp_path)}, HEAD, actionlog)
    if waiver == "exact":
        assert out == "", out
        assert _why(d)[-1].endswith("-> waived")
        assert [e.get("verdict") for e in _gate_rows(d)] == ["warn"]
    else:
        assert out.startswith("quality ratchet: required metric m worse"), "AC-5: a %s waiver is ignored" % waiver


@pytest.mark.parametrize("case", ["refusal", "raises", "head-mismatch", "second-head-moved"])
def test_ratchet_fails_closed(tmp_path, monkeypatch, case):
    answers = {
        "refusal": [_result(code=2, refusal="quality_delta.py: REFUSED [bad-metric] metrics[0]: missing name",
                            head=None, base=None)],
        "raises": [RuntimeError("disk full")],
        "head-mismatch": [_result(_row("m", 3, 3), head=OTHER_HEAD)],
        "second-head-moved": [_result(_row("m", 3, 5)), _result(_row("m", None, 5), head=OTHER_HEAD)],
    }[case]
    d, out, _loads = _gate(tmp_path, monkeypatch, FakeQD(*answers))
    assert out.startswith("quality ratchet could not measure: ") and out.endswith("--qkind no_evidence"), out
    assert {"refusal": "REFUSED [bad-metric]", "raises": "disk full",
            "head-mismatch": "measured %s but the PR head is %s" % (OTHER_HEAD, HEAD),
            "second-head-moved": "the head moved between readings"}[case] in out
    none = FakeQD(_result(head=HEAD, base=BASE, no_metrics=True))
    d2, out2, _loads = _gate(tmp_path / "none", monkeypatch, none)
    assert out2 == "" and _why(d2) == ["quality ratchet: no metrics configured"]


# ------------------------------------------------------------------------------------ T7: wiring

PR_HEAD = "0" * 40
ALWAYS = {"work": {"enabled": True, "auto_merge": "always"}, "action_log": {"enabled": True}}
GUARDED = {"work": {"enabled": True, "auto_merge": "protected"}, "action_log": {"enabled": True}}
GATE_ON = {"gates": {"quality_ratchet": {"enabled": True}}}
UNPROTECTED = [("branches/main/protection", RuntimeError("HTTP 404: Branch not protected"))]
NOSLEEP = lambda _seconds: None                                   # noqa: E731 - one-line stub


def _runner(handlers):
    """test_work.py's runner, trimmed: first substring match wins; the REST merge PUT lands."""
    calls = []

    def run(cwd, argv):
        line = " ".join(str(a) for a in argv)
        calls.append(line)
        for token, resp in handlers:
            if token in line:
                if isinstance(resp, Exception):
                    raise resp
                return resp(line) if callable(resp) else resp
        if "rev-parse HEAD" in line:
            return PR_HEAD
        if prfake.merge_put(7) in line:
            return prfake.rest_merged()
        if "remote get-url" in line:
            return "https://github.com/acme/app.git"
        if "/branches/" in line and line.endswith("/protection"):
            raise RuntimeError("HTTP 404: Branch not protected")   # #984: both protection reads ANSWER nothing by default
        if "/rules/branches/" in line:
            return "[]"
        return ""
    run.calls = calls
    return run


def _merge_handlers():
    view = prfake.gate_handlers(prfake.rest_pull(mergeable="MERGEABLE", mergeStateStatus="CLEAN",
                                                 headRefOid=PR_HEAD), (("ci", "SUCCESS"),), (), PR_HEAD)
    perm = [("--jq .permissions", json.dumps({"admin": True, "maintain": True, "push": True, "triage": True,
                                               "pull": True})), ("nameWithOwner", "acme/app")]
    return view + perm + UNPROTECTED


def _merge(tmp_path, monkeypatch, config, fake):
    d = _sdlc(tmp_path, config)
    goal = _started(d)
    loads = []
    monkeypatch.setattr(work, "_quality_delta", lambda: loads.append(1) or fake)
    run = _runner(_merge_handlers())
    out = work.merge(d, config, goal, run=run, sleep=NOSLEEP)
    return out, run, loads


def test_merge_parks_on_the_ratchet_before_any_merge_call(tmp_path, monkeypatch):
    assert callable(getattr(work, "_quality_ratchet_refusal", None)), "work._quality_ratchet_refusal does not exist yet"
    fake = FakeQD(_result(_row("words", 3, 5), head=PR_HEAD), _result(_row("words", None, 6), head=PR_HEAD))
    out, run, loads = _merge(tmp_path, monkeypatch, dict(ALWAYS, **GATE_ON), fake)
    assert out.startswith("PARK: quality ratchet: required metric words worse"), out
    assert out.endswith("--qkind failing_check")
    assert not prfake.merge_calls(run.calls), "no merge call once the ratchet parks"
    assert not any("--auto" in c.split() for c in run.calls), "and no arm either"


def test_merge_ratchet_off_or_skipped_loads_nothing_and_says_so(tmp_path, monkeypatch):
    out, run, loads = _merge(tmp_path / "off", monkeypatch, ALWAYS, FakeQD())
    assert out.startswith("PR #7 merged") and "quality ratchet" not in out and loads == [], out
    off = {"work": {"enabled": True, "auto_merge": "off"}, "action_log": {"enabled": True}}
    out, run, loads = _merge(tmp_path / "policy-off", monkeypatch, dict(off, **GATE_ON), FakeQD())
    assert loads == [] and "quality ratchet not run (no merge here)" in out, out
    assert out.endswith("leaving PR #7 for a human"), "the routed ending is unchanged"
    out, run, loads = _merge(tmp_path / "protected", monkeypatch, dict(GUARDED, **GATE_ON), FakeQD())
    assert loads == [] and "quality ratchet not run (no merge here)" in out, out
    assert out.endswith('merging it is yours to make (auto_merge: "protected")')
    out, run, loads = _merge(tmp_path / "plain-off", monkeypatch, off, FakeQD())
    assert "quality ratchet" not in out, "with the gate off the line is exactly as before"


def test_an_undetermined_base_stops_before_the_ratchet_under_both_policies(tmp_path, monkeypatch):
    """#984: `protection could not be determined` is a no-merge outcome under `always` too, so the ratchet (which
    measures only before a merge or arm that will really happen) is not loaded and the line says so."""
    for name, config in (("always", dict(ALWAYS, **GATE_ON)), ("protected", dict(GUARDED, **GATE_ON))):
        d = _sdlc(tmp_path / name, config)
        goal = _started(d)
        loads = []
        monkeypatch.setattr(work, "_quality_delta", lambda: loads.append(1) or FakeQD())
        run = _runner(_merge_handlers()[:-1] + [("branches/main/protection", RuntimeError("HTTP 403"))])
        out = work.merge(d, config, goal, run=run, sleep=NOSLEEP)
        assert loads == [] and not prfake.merge_calls(run.calls), (name, out)
        assert "protection could not be determined" in out and "quality ratchet not run (no merge here)" in out, out
        assert out.endswith(f'merging it is yours to make (auto_merge: "{name}")'), out


def _calls_of(tree, name):
    """[(enclosing top-level function, lineno)] for every call of `name` (bare or attribute)."""
    found = []
    for top in tree.body:
        if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(top):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if called == name:
                    found.append((top.name, node.lineno))
    return found


def test_only_merge_calls_the_quality_ratchet():
    trees = {}
    for path in sorted((ROOT / "skills").rglob("*.py")):
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
    sites = [(path.name, fn, line) for path, tree in trees.items()
             for fn, line in _calls_of(tree, "_quality_ratchet_refusal")]
    assert len(sites) == 1 and sites[0][:2] == ("work.py", "merge"), sites
    line = sites[0][2]
    tree = trees[SCRIPTS / "work.py"]
    merge = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "merge")
    vetted = [n.lineno for n in ast.walk(merge) if isinstance(n, ast.Assign)
              and any(getattr(t, "id", None) == "vetted_head" for t in n.targets)]
    pending_ifs = [n.lineno for n in ast.walk(merge) if isinstance(n, ast.If)
                   and isinstance(n.test, ast.BoolOp) and isinstance(n.test.op, ast.And)
                   and getattr(n.test.values[0], "id", None) == "pending_arm"]
    merges = [ln for fn, ln in _calls_of(tree, "merge_pr_gh") if fn == "merge"]
    gates = [ln for fn, ln in _calls_of(tree, "gate") if fn == "merge"]
    assert vetted and pending_ifs and merges and gates, (vetted, pending_ifs, merges, gates)
    assert max(gates) < min(vetted) < line, "after gate() and after `vetted_head =`"
    assert line < min(pending_ifs) and line < min(merges), "before the `if pending_arm and` branches and the merge"
    loaders = sorted({fn for fn, _ln in _calls_of(tree, "_quality_delta")})
    assert loaders == ["_quality_ratchet_refusal", "quality_waiver_path"], loaders
    assert sorted({fn for fn, _ln in _calls_of(tree, "quality_waiver_path")}) == ["_quality_waiver_valid", "quality_waive"]
    assert sorted({fn for fn, _ln in _calls_of(tree, "_quality_waiver_valid")}) == ["_quality_ratchet_refusal"]


def _texts(tmp_path, monkeypatch):
    """The REAL park texts the gate writes, one per variant."""
    variants = {
        "worse": [_result(_row("m", 3, 5)), _result(_row("m", None, 6))],
        "mixed": [_result(_row("m", 3, 5), _row("n", None, None, reason="head command exited 1")),
                  _result(_row("m", None, 6))],
        "required-unmeasured": [_result(_row("m", None, None, reason="head printed no number on its last line"))],
        "memory-floor": [_result(_row("m", None, None, reason="available memory 10 MiB is below the floor of 99 MiB"))],
        "live-owner": [_result(_row("m", None, None,
                                    reason="the scratch worktree has a live owner (another run); abstaining"))],
        "timeout": [_result(_row("m", None, None, reason="head command timed out after 300s"))],
        "refusal": [_result(code=2, refusal="quality_delta.py: REFUSED [bad-metric] metrics[0]: missing name",
                            head=None, base=None)],
        "rate-limit": [_result(_row("m", None, None, reason="base command exited 1: API rate limit exceeded"))],
        "worse-quoting-rate-limit": [_result(_row("m", 3, 5), _row("n", None, None, reason="rate limit irreversible")),
                                     _result(_row("m", None, 6))],
    }
    out = {}
    for name, answers in variants.items():
        _d, text, _loads = _gate(tmp_path / name, monkeypatch, FakeQD(*answers))
        out[name] = text
    return out


def test_ratchet_park_texts_classify_by_kind(tmp_path, monkeypatch):
    texts = _texts(tmp_path, monkeypatch)
    for name, text in texts.items():
        want = "failing_check" if name.startswith(("worse", "mixed")) else "no_evidence"
        assert text.endswith("--qkind " + want), (name, text)
        assert loop._needle_kind(text) == want, (name, loop._needle_kind(text), text)
        assert loop._suggest_kind(text) == want, (name, text)
    assert "could not measure" not in texts["mixed"], "a mixed park emits the WORSE text alone"
    assert "also unmeasured: n: head command exited 1" in texts["mixed"]


# ------------------------------------------------------------------------- review follow-ups (#1637)

def test_an_unrelated_park_naming_the_phrase_is_not_failing_check():
    """The failing_check needle is the gate's own worse-metric wording, not the bare phrase: a quota park
    that merely quotes a check called `quality ratchet` keeps its own class."""
    text = "source error recording 'parked' (API rate limit exceeded while reading check quality ratchet)"
    assert loop._needle_kind(text) == "quota", loop._needle_kind(text)
    assert loop._suggest_kind(text) != "failing_check"


def test_the_waiver_command_in_the_park_text_is_quoted(tmp_path, monkeypatch):
    import shlex
    fake = FakeQD(_result(_row("words", 3, 5)), _result(_row("words", None, 6)))
    d, out, _loads = _gate(tmp_path / "has space", monkeypatch, fake)
    assert " " in d
    assert "work.py quality-waive %s %s --metric words" % (shlex.quote(d), shlex.quote(GOAL)) in out, out
    cmd = out.split("work.py quality-waive ", 1)[1].split(" --metric", 1)[0]
    assert shlex.split(cmd) == [d, GOAL], cmd

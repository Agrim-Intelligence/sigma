"""#1647: `work.py merge` on a PR that is ALREADY merged reports it merged, not parked.

`merge()` reads the PR's landing state ONCE (`pr_landing_state`, one bare REST `gh api
repos/{owner}/{repo}/pulls/<n>`) right after the no-PR park and before every gate. MERGED runs the
same post-landing tail the direct PUT does and returns `PR #N merged (already merged; outcome
reconciled)`; CLOSED (unmerged) parks with no side effects; OPEN and UNKNOWN fall through to
today's flow, unretried. Fakes only (the `test_work` helpers), no network, no `gh`.

Every merged-branch test asserts the MERGED_LINE prefix FIRST and every fall-through test asserts
exactly one bare read FIRST (plan R2/R3): on the pre-#1647 code those are the assertions that fail,
so each node is red by AssertionError and never by an exception escaping `work.merge`."""
import json
import pathlib
import shutil

import pytest

import prfake
from test_work import (work, actionlog, _seq, _sdlc, _started, _evidence, _issue_calls, _merge_run,
                       _landing_pr, _landing_cfg, _no_landing_side_effects, _merged_pr, _spy_on_ensure_calls,
                       NOSLEEP, MERGE_PUT, JOURNAL_ON, journal_events)
from test_work import _legacy_work_host_unless_test_sets_codex_thread  # noqa: F401 - autouse env guard

GOAL = "1642.md"
MERGED_LINE = "PR #7 merged (already merged; outcome reconciled)"
DIRECT_LINE = "PR #7 merged (squash) — "
CLOSED_PARK = "PARK: PR #7 was closed without merging"
#: `pr_landing_state`'s one bare REST read, exactly; gh_api's gate read carries `--method GET` and the
#: PUT carries `/merge`, so neither is ever equal to it.
BARE = "gh api repos/{owner}/{repo}/pulls/7"


def _setup(tmp_path, cfg=None, age=0):
    cfg = cfg or _landing_cfg()
    d = _sdlc(tmp_path, cfg)
    goal = _started(d, goal=GOAL)
    _evidence(d, goal, age=age)
    return d, cfg, goal


def _merged_run(landing=None):
    """The direct PUT would land (so pre-#1647 code returns the direct line); the bare entry read
    answers `landing` (MERGED by default)."""
    return _merge_run(prfake.rest_merged(), landing=_landing_pr("MERGED") if landing is None else landing)


def _bare_reads(calls):
    return sum(c == BARE for c in calls)


def _gates(d, goal, name):
    return [e for e in actionlog.read_goal(d, goal) if e["kind"] == "gate" and e.get("gate") == name]


def _merged_entries(d, goal):
    return [e for e in actionlog.read_goal(d, goal) if e["kind"] == "merged"]


def _deliveries(d):
    return list((pathlib.Path(d) / "state" / "merge-deliveries").glob("*.json"))


def _ledger_merged(d):
    return [e for e in work.ledger.read_all(d) if e.get("kind") == "merged"]


# --- MERGED: the short-circuit ----------------------------------------------------------------------

def test_merged_pr_returns_the_already_merged_line(tmp_path):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run()
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out


def test_merged_with_stale_verify_evidence_still_lands(tmp_path):
    d, cfg, goal = _setup(tmp_path, {**_landing_cfg(), "verify": {"command": "pytest -q"}}, age=10_000)
    run = _merged_run()
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out


def test_merged_with_an_incomplete_phase_record_still_lands(tmp_path):
    d, cfg, goal = _setup(tmp_path, {**_landing_cfg(), "gates": {"phase_record": {"enabled": True}}})
    run = _merged_run()
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out


def test_merged_when_merge_rights_would_refuse_still_lands(tmp_path, monkeypatch):
    d, cfg, goal = _setup(tmp_path)
    called = []

    def no_rights(*a, **k):
        called.append(a)
        return False, "no rights"

    monkeypatch.setattr(work, "merge_rights", no_rights)
    run = _merged_run()
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out
    assert called == []


def test_merged_issues_no_mergeability_get_and_no_put(tmp_path):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run()
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out
    assert not any(MERGE_PUT in c or "pulls/7/merge" in c for c in run.calls), run.calls
    assert not any(prfake.pull_get(7) in c for c in run.calls), run.calls


def test_merged_emits_test_trust_after_the_landing_read(tmp_path):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run()
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out
    assert len(_gates(d, goal, "test_trust")) == 1
    assert BARE in run.calls, run.calls
    assert any(c.startswith("git diff") for c in run.calls), run.calls
    first_diff = next(i for i, c in enumerate(run.calls) if c.startswith("git diff"))
    assert run.calls.index(BARE) < first_diff, run.calls


def test_merged_with_a_deleted_worktree_still_lands(tmp_path):
    d, cfg, goal = _setup(tmp_path)
    shutil.rmtree(pathlib.Path(work._record(d, goal)["worktree"]))
    inner = _merged_run()
    seen = []

    def run(cwd, argv):
        line = " ".join(str(a) for a in argv)
        seen.append((str(cwd), line))           # str(cwd): pr_landing_state's root fallback is a Path
        if not pathlib.Path(cwd).is_dir():
            raise FileNotFoundError(2, "No such file or directory", str(cwd))
        return inner(cwd, argv)

    try:
        out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    except Exception as e:                      # noqa: BLE001 - an escaped raise must fail the prefix assert
        out = "raised %r" % e
    assert out.startswith(MERGED_LINE), out
    root = str(work.project_root(d))
    assert any(line == BARE and cwd == root for cwd, line in seen), seen
    assert _gates(d, goal, "test_trust") == []  # no false `pass` from a diff that could not be read
    assert len(_merged_entries(d, goal)) == 1
    assert "could not close #1642" in out, out
    assert not any("DELETE" in line and pathlib.Path(cwd).is_dir() for cwd, line in seen), seen


def test_merged_appends_the_merged_actionlog_entry(tmp_path):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run()
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out
    assert len(_merged_entries(d, goal)) == 1
    assert _gates(d, goal, "merge") == []
    assert not [e for e in actionlog.read_goal(d, goal) if e["kind"] == "ci_observed"]


def test_merged_deletes_the_branch_and_closes_the_issue_once(tmp_path):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run()
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out
    assert sum("DELETE" in c for c in run.calls) == 1, run.calls
    assert sum("state=closed" in c for c in _issue_calls(run, "1642")) == 1, run.calls


def test_merged_records_the_ledger_entry_with_two_bare_reads(tmp_path):
    d, cfg, goal = _setup(tmp_path, {**_landing_cfg(), **JOURNAL_ON})
    work.ledger.reset_actor_cache()
    run = _merged_run(landing=_merged_pr())
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out
    assert _bare_reads(run.calls) == 2, run.calls
    assert len(_ledger_merged(d)) == 1
    assert len([e for e in journal_events(work.ledger, d) if e.get("kind") == "merge_observed"]) == 1
    assert len(_deliveries(d)) == 1


def test_merged_twice_delivers_the_ledger_entry_once(tmp_path):
    d, cfg, goal = _setup(tmp_path, {**_landing_cfg(), **JOURNAL_ON})
    work.ledger.reset_actor_cache()
    first = work.merge(d, cfg, goal, run=_merged_run(landing=_merged_pr()), sleep=NOSLEEP)
    assert first.startswith(MERGED_LINE), first
    second = work.merge(d, cfg, goal, run=_merged_run(landing=_merged_pr()), sleep=NOSLEEP)
    assert second.startswith(MERGED_LINE), second
    assert len(_ledger_merged(d)) == 1
    assert len(_deliveries(d)) == 1


def test_merged_delivery_key_conflict_raises_like_the_direct_path(tmp_path):
    d, cfg, goal = _setup(tmp_path, {**_landing_cfg(), **JOURNAL_ON})
    work.ledger.reset_actor_cache()
    first = work.merge(d, cfg, goal, run=_merged_run(landing=_merged_pr()), sleep=NOSLEEP)
    assert first.startswith(MERGED_LINE), first
    deliveries = _deliveries(d)
    assert len(deliveries) == 1, deliveries
    data = json.loads(deliveries[0].read_text())
    data["observation_key"] = "f" * 64
    deliveries[0].write_text(json.dumps(data))
    raised = None
    try:
        work.merge(d, cfg, goal, run=_merged_run(landing=_merged_pr()), sleep=NOSLEEP)
    except ValueError as e:
        raised = e
    assert raised is not None and "merge delivery receipt key conflict" in str(raised), raised


def test_merged_observes_the_merge_when_receipt_sharing_is_on(tmp_path, monkeypatch):
    d, cfg, goal = _setup(tmp_path)
    observed = []
    monkeypatch.setattr(work, "_receipt_sharing_enabled", lambda config: True)
    monkeypatch.setattr(work, "_observe_confirmed_merge",
                        lambda sdlc_dir, config, g, rec, run: observed.append(g))
    run = _merged_run()
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out
    assert observed == [goal]


def test_merged_fires_the_upkeep_trigger_last(tmp_path, monkeypatch):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run()
    snapshots = []
    monkeypatch.setattr(work, "_upkeep_merge_trigger", lambda sdlc_dir, config: snapshots.append(
        (len(_merged_entries(d, goal)), any("DELETE" in c for c in run.calls))))
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(MERGED_LINE), out
    assert snapshots == [(1, True)]


# --- CLOSED (unmerged): park, touch nothing -----------------------------------------------------------

def test_closed_unmerged_parks_with_the_closed_without_merging_reason(tmp_path):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run(landing=_landing_pr("CLOSED"))
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith(CLOSED_PARK), out
    assert "reopen it on GitHub" in out, out
    assert "the merge call failed" not in out, out
    assert not any(MERGE_PUT in c for c in run.calls), run.calls


def test_closed_unmerged_has_no_side_effects(tmp_path):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run(landing=_landing_pr("CLOSED"))
    work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    _no_landing_side_effects(run, d, goal)
    assert not any(MERGE_PUT in c for c in run.calls), run.calls


def test_closed_unmerged_emits_no_test_trust(tmp_path):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run(landing=_landing_pr("CLOSED"))
    work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert _gates(d, goal, "test_trust") == []
    assert not any(c.startswith("git diff") for c in run.calls), run.calls


# --- OPEN / UNKNOWN: read once, then today's flow ---------------------------------------------------

def _assert_fell_through(run, out):
    assert _bare_reads(run.calls) == 1, run.calls      # the entry read happened, once, and was ignored
    assert any(prfake.pull_get(7) in c for c in run.calls), run.calls
    assert sum(c == MERGE_PUT for c in run.calls) == 1, run.calls
    assert out.startswith(DIRECT_LINE), out
    assert "already merged" not in out, out


@pytest.mark.parametrize("landing", [RuntimeError("HTTP 502: read failed"), "not json",
                                     json.dumps({"state": "weird"})],
                         ids=["read-error", "unparseable", "unknown-state"])
def test_unknown_landing_read_falls_through_to_the_gate(tmp_path, landing):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run(landing=landing)
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    _assert_fell_through(run, out)


def test_open_landing_read_falls_through_to_the_gate(tmp_path):
    d, cfg, goal = _setup(tmp_path)
    run = _merged_run(landing=_landing_pr("OPEN"))
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    _assert_fell_through(run, out)


def test_merge_reads_the_landing_state_exactly_once_more(tmp_path):
    d, cfg, goal = _setup(tmp_path / "open")
    run = _merged_run(landing=_landing_pr("OPEN"))
    work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert _bare_reads(run.calls) == 1, run.calls
    # Never retried: a later MERGED answer is never consumed by the entry read.
    d2, cfg2, goal2 = _setup(tmp_path / "seq")
    run2 = _merged_run(landing=_seq(_landing_pr("OPEN"), _landing_pr("MERGED")))
    out2 = work.merge(d2, cfg2, goal2, run=run2, sleep=NOSLEEP)
    assert out2.startswith(DIRECT_LINE), out2
    assert _bare_reads(run2.calls) == 1, run2.calls


# --- the documented gesture: `work.py merge <sdlc_dir> <goal>` ----------------------------------------

def _cli(tmp_path, monkeypatch, capsys, landing):
    d, _cfg, goal = _setup(tmp_path)
    run = _merge_run(prfake.rest_merged(), landing=landing)
    monkeypatch.setattr(work, "_run", run)
    _spy_on_ensure_calls(monkeypatch)
    rc = work.main(["work.py", "merge", d, goal])
    cap = capsys.readouterr()
    return rc, cap.out.splitlines(), cap.err.splitlines(), run


def _assert_cli_shape(rc, out, err):
    assert rc == 0, (rc, out, err)
    assert out, err
    assert all(line.startswith("work.py merge: ") for line in err), err


def test_cli_merge_on_a_merged_pr_prints_the_merged_line(tmp_path, monkeypatch, capsys):
    rc, out, err, _run = _cli(tmp_path, monkeypatch, capsys, _landing_pr("MERGED"))
    _assert_cli_shape(rc, out, err)
    assert out[0].startswith(MERGED_LINE), out


def test_cli_merge_on_a_closed_pr_prints_the_park_line(tmp_path, monkeypatch, capsys):
    rc, out, err, _run = _cli(tmp_path, monkeypatch, capsys, _landing_pr("CLOSED"))
    _assert_cli_shape(rc, out, err)
    assert out[0].startswith(CLOSED_PARK), out


def test_cli_merge_on_an_unknown_read_reaches_the_gate(tmp_path, monkeypatch, capsys):
    rc, out, err, run = _cli(tmp_path, monkeypatch, capsys, None)   # no handler: "" reads UNKNOWN
    _assert_cli_shape(rc, out, err)
    assert _bare_reads(run.calls) == 1, run.calls
    assert out[0].startswith(DIRECT_LINE), out

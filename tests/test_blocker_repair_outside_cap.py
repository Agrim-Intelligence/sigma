"""The reconcile tick's read of blockers outside the goal corpus is capped (10 per pass), but the cap
must not starve: it reads newest refs first and never spends a read again on a ref it saw closed, so a
board whose old goals cite many closed issues still reaches a legacy-labelled blocker (found in the
fourth stress-test pass: the same ten oldest closed refs were re-read every tick, string-sorted)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_blocker_rank_repair as base  # noqa: E402

GOAL = base.GOAL


def _board(closed_refs, target_ref, target_labels):
    issues = [base._issue(1000 + i, [GOAL, "priority:P0"], body="blocked by #%d" % r)
              for i, r in enumerate(closed_refs)]
    issues.append(base._issue(2000, [GOAL, "priority:P0"], body="blocked by #%d" % target_ref))
    views = {str(r): base._issue(r, [], state="closed") for r in closed_refs}
    views[str(target_ref)] = base._issue(target_ref, target_labels)
    return issues, views


def _reads(run):
    return [c for c in run.calls if c[:1] == ["api"] and "/issues/" in " ".join(c) and "/comments" not in " ".join(c)]


def test_a_new_legacy_labelled_blocker_is_reached_ahead_of_old_closed_refs(tmp_path):
    issues, views = _board(range(100, 112), 900, [GOAL, "priority:P3", "sdlc:needs-confirmation"])
    run = base._runner(issues, views=views)
    res = base._src(run, sdlc_dir=str(tmp_path)).repair_blocker_ranks()
    assert res["stripped"] == ["900"]


def test_an_old_blocker_behind_many_closed_refs_is_reached_within_a_few_ticks(tmp_path):
    issues, views = _board(range(100, 125), 50, [GOAL, "priority:P3", "sdlc:needs-confirmation"])
    stripped, per_run = [], []
    for _ in range(4):
        run = base._runner(issues, views=views)
        res = base._src(run, sdlc_dir=str(tmp_path)).repair_blocker_ranks()
        stripped += res["stripped"]
        per_run.append(len(_reads(run)))
    assert stripped and set(stripped) == {"50"}, (stripped, per_run)   # the fake never clears the label
    assert max(per_run) <= 12      # the cap of ten outside reads is kept (plus the list reads)

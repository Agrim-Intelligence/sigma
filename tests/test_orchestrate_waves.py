"""Tests for skills/sigma-orchestrate/scripts/waves.py (#2145): the wave planner and the ready-set picker.

Run: $HOME/.sigma-venv312/bin/python -m pytest tests/test_orchestrate_waves.py
(generic form: python -m pytest tests/test_orchestrate_waves.py)

Every clause of the wave-grouping condition has a test that goes red when that clause alone is broken
(see the CONTROLS table at the bottom of this file, each row run by hand on 2026-11-12): the dependency
floor, the claim-clash bump, and each of the clash predicate's own branches.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "sigma-orchestrate", "scripts")
sys.path.insert(0, SCRIPTS)
import waves  # noqa: E402


def g(n, blockers=(), claims=None, priority="P2", base="main", **kw):
    d = {"n": n, "title": f"goal {n}", "priority": priority, "base": base,
         "blockers": list(blockers), "claims": claims if claims is not None else [f"area{n}/"]}
    d.update(kw)
    return d


def doc(*goals):
    return {"goals": list(goals)}


class Plan(unittest.TestCase):
    def test_chain_makes_one_wave_per_link(self):
        p = waves.plan(doc(g(1), g(2, [1]), g(3, [2])))
        self.assertEqual(p["waves"], [[1], [2], [3]])

    def test_independent_goals_share_one_wave(self):
        p = waves.plan(doc(g(1), g(2), g(3)))
        self.assertEqual(p["waves"], [[1, 2, 3]])

    def test_shared_file_splits_a_wave_and_says_why(self):
        p = waves.plan(doc(g(1, claims=["server/x.py"]), g(2, claims=["server/x.py"])))
        self.assertEqual(p["waves"], [[1], [2]])
        self.assertIn("#1", p["moved"]["2"])

    def test_directory_claim_covers_files_under_it(self):
        p = waves.plan(doc(g(1, claims=["ui/src"]), g(2, claims=["ui/src/a.ts"]), g(3, claims=["ui/srcx.ts"])))
        self.assertEqual(p["waves"], [[1, 3], [2]])

    def test_contract_tokens_match_exactly(self):
        p = waves.plan(doc(g(1, claims=["contract:runs"]), g(2, claims=["contract:runs-v2"]),
                           g(3, claims=["contract:runs"])))
        self.assertEqual(p["wave_of"], {"1": 1, "2": 1, "3": 2})

    def test_unknown_claims_run_alone(self):
        p = waves.plan(doc(g(1, claims=[]), g(2), g(3)))
        self.assertEqual(len(p["waves"]), 2)
        self.assertNotIn(1, [n for w in p["waves"] for n in w if len(w) > 1])

    def test_dependents_of_a_moved_goal_move_after_it(self):
        p = waves.plan(doc(g(1, claims=["a.py"], priority="P1"), g(2, claims=["a.py"]), g(3, [2])))
        self.assertEqual(p["wave_of"], {"1": 1, "2": 2, "3": 3})

    def test_outside_blocker_holds_the_goal_and_its_dependents(self):
        p = waves.plan(doc(g(1, external_blockers=[99]), g(2, [1]), g(3)))
        self.assertEqual(p["waves"], [[3]])
        self.assertIn("#99", p["held"]["1"])
        self.assertIn("#1", p["held"]["2"])

    def test_blocker_number_missing_from_batch_counts_as_outside(self):
        p = waves.plan(doc(g(1, [77])))
        self.assertIn("#77", p["held"]["1"])

    def test_cycle_is_held_with_its_dependents(self):
        p = waves.plan(doc(g(1, [2]), g(2, [1]), g(3, [1]), g(4)))
        self.assertEqual(p["held"]["1"], "dependency cycle")
        self.assertEqual(p["held"]["2"], "dependency cycle")
        self.assertIn("#1", p["held"]["3"])
        self.assertEqual(p["waves"], [[4]])

    def test_orchestrator_reason_holds(self):
        p = waves.plan(doc(g(1, held="needs a person: design fork"), g(2)))
        self.assertEqual(p["held"]["1"], "needs a person: design fork")

    def test_rank_priority_then_unblocking_then_number(self):
        # 3 and 2 clash; 3 is P1 so it wins wave 1 even though 2 is older.
        p = waves.plan(doc(g(2, claims=["x"]), g(3, claims=["x"], priority="P1")))
        self.assertEqual(p["wave_of"], {"2": 2, "3": 1})
        # Same priority: the one more goals wait on goes first.
        p = waves.plan(doc(g(5, claims=["y"]), g(6, claims=["y"]), g(7, [6]), g(8, [7])))
        self.assertEqual(p["wave_of"]["6"], 1)
        self.assertEqual(p["wave_of"]["5"], 2)


class Clash(unittest.TestCase):
    """Each branch of the claim-overlap predicate on its own, in both argument orders."""

    def test_unknown_on_either_side_clashes_with_everything(self):
        self.assertTrue(waves.clash([], ["a.py"]))
        self.assertTrue(waves.clash(["a.py"], []))
        self.assertTrue(waves.clash(["*"], ["a.py"]))
        self.assertTrue(waves.clash(["a.py"], ["."]))

    def test_a_directory_covers_what_is_under_it_in_both_orders(self):
        self.assertTrue(waves.clash(["ui/src"], ["ui/src/a.ts"]))
        self.assertTrue(waves.clash(["ui/src/a.ts"], ["ui/src"]))

    def test_equal_claims_clash(self):
        self.assertTrue(waves.clash(["a.py"], ["a.py"]))

    def test_a_prefix_is_a_path_boundary_not_a_string_prefix(self):
        self.assertFalse(waves.clash(["ui/src"], ["ui/srcx/a.ts"]))
        self.assertFalse(waves.clash(["ui/srcx/a.ts"], ["ui/src"]))

    def test_disjoint_claims_do_not_clash(self):
        self.assertIsNone(waves.clash(["a/"], ["b/"]))


class PlanPlacement(unittest.TestCase):
    """The placement condition: after every blocker's wave, and away from every claim in the wave."""

    def test_a_goal_goes_after_its_latest_blocker_not_its_first(self):
        # 3 waits on 1 (wave 1) and on 2 (wave 2): it belongs in wave 3.
        p = waves.plan(doc(g(1), g(2, [1]), g(3, [1, 2])))
        self.assertEqual(p["wave_of"]["3"], 3)

    def test_unknown_claims_lower_ranked_still_run_alone(self):
        # The unknown-claims goal has the highest number, so it is placed after the others.
        p = waves.plan(doc(g(1), g(2), g(3, claims=[])))
        self.assertEqual(p["waves"], [[1, 2], [3]])

    def test_a_clash_with_any_member_not_only_the_first_moves_the_goal(self):
        p = waves.plan(doc(g(1, claims=["a.py"]), g(2, claims=["b.py"]), g(3, claims=["b.py"])))
        self.assertEqual(p["wave_of"], {"1": 1, "2": 1, "3": 2})

    def test_a_goal_is_not_moved_when_nothing_in_its_wave_clashes(self):
        p = waves.plan(doc(g(1, claims=["a/"]), g(2, claims=["b/"]), g(3, claims=["c/"])))
        self.assertEqual(p["waves"], [[1, 2, 3]])
        self.assertEqual(p["moved"], {})


class Input(unittest.TestCase):
    def test_duplicate_goal_is_refused(self):
        with self.assertRaises(ValueError):
            waves.plan(doc(g(1), g(1)))

    def test_external_blocker_that_is_in_the_batch_counts_as_inside(self):
        p = waves.plan(doc(g(1), g(2, external_blockers=[1])))
        self.assertEqual(p["waves"], [[1], [2]])
        self.assertEqual(p["held"], {})

    def test_priority_spellings(self):
        p = waves.plan(doc(g(1, claims=["x"], priority="p3"), g(2, claims=["x"], priority="priority:P1")))
        self.assertEqual(p["wave_of"], {"1": 2, "2": 1})

    def test_claim_spellings(self):
        for a, b in [("ui/src/**", "ui/src/a.ts"), ("/ui/src", "ui/src/a.ts"), ("./ui/", "ui/x"),
                     ("ui/src/*", "ui/src/b/c.ts"), (".", "anything"), ("src/**/*.ts", "src/a.ts"),
                     ("*.py", "a.py"), ("ui/src*", "ui/srcx.ts"), ("docs/[ab].md", "docs/a.md"),
                     ("docs/?.md", "docs/x.md")]:
            self.assertTrue(waves.clash([a], [b]), (a, b))
        self.assertFalse(waves.clash(["ui/src"], ["ui/srcx"]))


class Ready(unittest.TestCase):
    def test_no_seat_limit_launches_every_ready_goal(self):
        r = waves.ready(doc(g(1), g(2), g(3)), {})
        self.assertEqual(r["launch"], [1, 2, 3])
        self.assertEqual(r["seats_free"], "unlimited")

    def test_seat_limit_counts_running_goals(self):
        r = waves.ready(doc(g(1), g(2), g(3), g(4)), {"running": [1]}, seats=2)
        self.assertEqual(r["launch"], [2])
        self.assertEqual(r["waiting"]["3"], "no free seat")

    def test_awaiting_merge_holds_claims_but_no_seat(self):
        d = doc(g(1, claims=["a.py"]), g(2, claims=["a.py"]), g(3))
        r = waves.ready(d, {"awaiting_merge": [1]}, seats=1)
        self.assertEqual(r["launch"], [3])
        self.assertIn("#1", r["waiting"]["2"])

    def test_blocker_must_land_on_the_dependents_base(self):
        d = doc(g(1, base="feature/a"), g(2, [1], base="main"))
        self.assertIn("2", waves.plan(d)["cross_base"])
        r = waves.ready(d, {"finished": [1], "landed": {"1": ["feature/a"]}})
        self.assertEqual(r["launch"], [])
        self.assertIn("not in main", r["stuck"]["2"])
        r = waves.ready(d, {"finished": [1], "landed": {"1": ["feature/a", "main"]}})
        self.assertEqual(r["launch"], [2])

    def test_stopped_blocker_makes_every_dependent_stuck(self):
        r = waves.ready(doc(g(1), g(2, [1]), g(3, [2]), g(4)), {"stopped": {"1": "parked: needs design"}})
        self.assertNotIn("1", r["waiting"])
        self.assertIn("parked: needs design", r["stuck"]["2"])
        self.assertIn("#2", r["stuck"]["3"])
        self.assertEqual(r["launch"], [4])

    def test_priority_beats_wave_for_a_contested_claim(self):
        # 5 (P4, wave 1) and 6 (P0, wave 2 behind 7) want the same file; once 7 has landed, 6 goes first.
        d = doc(g(7), g(5, claims=["a.py"], priority="P4"), g(6, [7], claims=["a.py"], priority="P0"))
        r = waves.ready(d, {"finished": [7], "landed": {"7": ["main"]}})
        self.assertEqual(r["launch"], [6])
        self.assertIn("#6", r["waiting"]["5"])

    def test_merged_blocker_on_the_same_base_not_yet_landed_is_waiting_not_stuck(self):
        r = waves.ready(doc(g(1), g(2, [1])), {"finished": [1]})
        self.assertNotIn("2", r["stuck"])
        self.assertIn("#1", r["waiting"]["2"])

    def test_goal_in_flight_but_missing_from_goals_is_ignored(self):
        r = waves.ready(doc(g(1)), {"running": [99], "pr_files": {"98": ["x"]}})
        self.assertEqual(r["launch"], [1])

    def test_higher_ranked_goal_reserves_claims(self):
        # 1 (P1) waits for 9's file; 2 (P2) shares 1's file and must not jump it.
        d = doc(g(9, claims=["a.py"]), g(1, claims=["a.py", "b.py"], priority="P1"),
                g(2, claims=["b.py"]))
        r = waves.ready(d, {"running": [9]})
        self.assertEqual(r["launch"], [])
        self.assertIn("#9", r["waiting"]["1"])
        self.assertIn("#1", r["waiting"]["2"])

    def test_pr_files_replace_claims_for_running_goals(self):
        d = doc(g(1, claims=["a.py"]), g(2, claims=["b.py"]))
        r = waves.ready(d, {"running": [1], "pr_files": {"1": ["b.py"]}})
        self.assertIn("#1", r["waiting"]["2"])

    def test_finished_and_running_goals_are_not_relaunched(self):
        r = waves.ready(doc(g(1), g(2), g(3)), {"finished": [1], "running": [2]})
        self.assertEqual(r["launch"], [3])


    def test_a_running_goal_is_not_relaunched_even_when_its_claims_are_free(self):
        # Its real PR files moved off its declared claims, so its own claims no longer block it.
        r = waves.ready(doc(g(1, claims=["a.py"])), {"running": [1], "pr_files": {"1": ["z.py"]}})
        self.assertEqual(r["launch"], [])
        self.assertNotIn("1", r["waiting"])


class OpenPullRequests(unittest.TestCase):
    """#2157: an open pull request that links the issue (any branch name) holds the goal, like a label would."""

    def test_an_open_pr_holds_the_goal_and_names_it(self):
        p = waves.plan(doc(g(1, open_prs=[2150]), g(2)))
        self.assertIn("#2150", p["held"]["1"])
        self.assertEqual(p["waves"], [[2]])

    def test_a_goal_waiting_on_one_held_by_a_pr_is_held_too(self):
        p = waves.plan(doc(g(1, open_prs=[7]), g(2, [1])))
        self.assertIn("#1", p["held"]["2"])

    def test_no_open_prs_key_or_an_empty_list_changes_nothing(self):
        self.assertEqual(waves.plan(doc(g(1), g(2))), waves.plan(doc(g(1, open_prs=[]), g(2, open_prs=None))))
        self.assertEqual(waves.plan(doc(g(1)))["held"], {})

    def test_an_explicit_reason_wins_over_the_pr_note(self):
        p = waves.plan(doc(g(1, held="needs a person", open_prs=[7])))
        self.assertEqual(p["held"]["1"], "needs a person")

    def test_ready_does_not_launch_a_goal_with_an_open_pr(self):
        r = waves.ready(doc(g(1, open_prs=[7]), g(2)), {})
        self.assertEqual(r["launch"], [2])
        self.assertIn("1", r["held"])

    def test_md_lists_it_as_held(self):
        d = doc(g(1, open_prs=[7]))
        self.assertIn("| held | #1 |", waves._md(waves.plan(d), d))


class Cli(unittest.TestCase):
    def test_plan_and_ready_from_files(self):
        here = SCRIPTS
        with tempfile.TemporaryDirectory() as t:
            gp, sp = os.path.join(t, "goals.json"), os.path.join(t, "state.json")
            with open(gp, "w") as fh:
                json.dump(doc(g(1), g(2, [1])), fh)
            with open(sp, "w") as fh:
                json.dump({}, fh)
            out = subprocess.run([sys.executable, os.path.join(here, "waves.py"), "plan", gp, "--md"],
                                 capture_output=True, text=True, check=True).stdout
            self.assertIn("| 2 | #2 |", out)
            out = subprocess.run([sys.executable, os.path.join(here, "waves.py"), "ready", gp, sp, "--seats", "5"],
                                 capture_output=True, text=True, check=True).stdout
            self.assertEqual(json.loads(out)["launch"], [1])



# CONTROLS (#2145). Each row was applied alone to waves.py and the named test went red; all 28 were
# run, and the one that first stayed green (a running goal relaunched) got its own test above.
#   dependency floor: max -> min (test_a_goal_goes_after_its_latest_blocker_not_its_first); floor dropped
#     (test_chain_makes_one_wave_per_link)
#   clash bump: only the first wave member checked (test_a_clash_with_any_member_not_only_the_first...);
#     no bump at all (test_contract_tokens_match_exactly); the "moved" note dropped
#     (test_shared_file_splits_a_wave_and_says_why)
#   clash predicate, one branch at a time: left wildcard, right wildcard, a covers b, b covers a, equality,
#     path boundary, empty claims read as known (the Clash class, and the two Plan tests named in it)
#   holds: dependents of a held goal, an outside blocker, a cycle (test_cycle_is_held_with_its_dependents)
#   rank: priority dropped, reach dropped (test_dependents_of_a_moved_goal..., test_rank_priority_then...)
#   ready: base ancestry, reservation, awaiting-merge claims and seat, PR files over claims, cross-base stuck,
#     stopped blocker stuck, seat limit, claim clash, unmet blockers, running goal relaunched (the Ready class)

# CONTROLS (#2157), each applied alone to waves.py and its named test seen red: the open_prs hold dropped
#   (test_an_open_pr_holds_the_goal_and_names_it); a None value read as a list
#   (test_no_open_prs_key_or_an_empty_list_changes_nothing); the PR note ranked above an explicit reason
#   (test_an_explicit_reason_wins_over_the_pr_note); the PR numbers dropped from the reason.

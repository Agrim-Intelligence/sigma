import pathlib, importlib.util, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-status" / "scripts"


def _status():
    spec = importlib.util.spec_from_file_location("status", S / "status.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _rest_labels(args):
    """#1833: the AND-ed label set a fake `run()` was asked to filter on, from the `gh api
    repos/{owner}/{repo}/issues -f labels=a,b` shape `_github_counts` now constructs (mirrors
    test_sources.py's own `_rest_labels`, adapted for this file's "gh"-prefixed `run` convention).
    `()` (not `None`) when no `labels=` field was sent at all, so a fake keyed by an empty tuple
    still distinguishes "no label filter" from "asked for a label that matched nothing"."""
    for v in args:
        if isinstance(v, str) and v.startswith("labels="):
            return tuple(v[len("labels="):].split(","))
    return ()


def _is_issues_list_call(args):
    """True for the `gh api repos/{owner}/{repo}/issues` REST shape `_github_counts` builds."""
    return (len(args) > 2 and args[0] == "gh" and args[1] == "api"
            and str(args[2]).startswith("repos/") and str(args[2]).endswith("/issues"))


def test_summary_counts_by_status():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        (base / "state" / "STATE.md").write_text("iteration: 4\n")
        for n, s in [("0001", "done"), ("0002", "parked"), ("0003", "pending")]:
            (base / "goals" / f"{n}.md").write_text(f"---\nid: {n}\nstatus: {s}\n---\nx\n")
        out = _status().summary(str(base))
        assert out["done"] == 1 and out["parked"] == 1 and out["pending"] == 1 and out["iteration"] == 4


def test_summary_counts_quoted_status():   # parity with frontmatter.parse (strips quotes)
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        (base / "goals" / "0001.md").write_text('---\nid: 0001\nstatus: "done"\n---\nx\n')
        assert _status().summary(str(base))["done"] == 1


# --- F7: a truncated/invalid-UTF-8 goal, state, or ledger file must not crash summary() -------

#: A multi-byte UTF-8 char ("é" = b"\xc3\xa9") chopped mid-sequence — exactly what a process killed
#: mid-append leaves behind. This is a genuine encoding error, not a crafted edge case.
_TRUNCATED_UTF8 = "café".encode("utf-8")[:-1]


def test_summary_survives_a_truncated_goal_file():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        (base / "goals" / "0001.md").write_bytes(b"---\nid: 0001\nstatus: done\n---\n" + _TRUNCATED_UTF8)
        (base / "goals" / "0002.md").write_text("---\nid: 0002\nstatus: pending\n---\nx\n")
        out = _status().summary(str(base))         # must not raise
        assert out["pending"] == 1                  # the good file is still counted correctly


def test_summary_survives_a_truncated_state_md():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        (base / "state" / "STATE.md").write_bytes(b"iteration: 4\n" + _TRUNCATED_UTF8)
        out = _status().summary(str(base))          # must not raise
        assert "iteration" in out


def test_summary_survives_a_truncated_review_queue():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        (base / "state" / "review-queue.md").write_bytes(b"## something\n" + _TRUNCATED_UTF8)
        out = _status().summary(str(base))          # must not raise
        assert out["queue_nonempty"] is True          # the readable "## " heading still counts


def test_goals_since_align_survives_a_truncated_report():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"
        (base / "context").mkdir(parents=True)
        (base / "context" / "north-star.md").write_text("x\n")
        (base / "knowledge" / "align").mkdir(parents=True)
        (base / "knowledge" / "align" / "2026-01-01.md").write_bytes(
            b"goals_reviewed: 3\n" + _TRUNCATED_UTF8)
        result = _status()._goals_since_align(base, done=5, iteration=5)   # must not raise
        assert result == 2   # max(5,5) - 3


def test_ledger_entries_survives_a_truncated_jsonl():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"
        entries = base / "ledger" / "entries"; entries.mkdir(parents=True)
        (entries / "dana.jsonl").write_bytes(b'{"id":"dana:1"}\n' + _TRUNCATED_UTF8)
        # must not raise; the whole good line is still counted (the truncated remnant decodes to a
        # replacement char and may or may not count as a trailing "line" of its own — not the point)
        assert _status()._ledger_entries(base) >= 1


def test_summary_survives_invalid_utf8_everywhere_at_once():
    """The end-to-end acceptance case: every reader summary() touches is fed a truncated file at
    once, and the dashboard still returns a complete result instead of crashing."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"
        (base / "goals").mkdir(parents=True)
        (base / "state").mkdir()
        (base / "context").mkdir()
        (base / "knowledge" / "align").mkdir(parents=True)
        (base / "ledger" / "entries").mkdir(parents=True)
        (base / "goals" / "0001.md").write_bytes(b"---\nid: 0001\nstatus: done\n---\n" + _TRUNCATED_UTF8)
        (base / "state" / "STATE.md").write_bytes(b"iteration: 2\n" + _TRUNCATED_UTF8)
        (base / "state" / "review-queue.md").write_bytes(_TRUNCATED_UTF8)
        (base / "context" / "north-star.md").write_text("x\n")
        (base / "knowledge" / "align" / "2026-01-01.md").write_bytes(
            b"goals_reviewed: 1\n" + _TRUNCATED_UTF8)
        (base / "ledger" / "entries" / "dana.jsonl").write_bytes(b'{"id":"dana:1"}\n' + _TRUNCATED_UTF8)
        out = _status().summary(str(base))          # must not raise, from any of the five readers
        assert set(out) >= {"pending", "in_progress", "done", "parked", "failed", "proposed",
                            "iteration", "queue_nonempty", "ledger_entries", "goals_since_align"}


def test_summary_github_mode_honors_a_repo_that_overrides_proposed_label():
    """#1348: a repo that keeps `handoff.proposed_label` overridden back to the old name (or any
    other custom value) must be counted correctly -- the dashboard's own count must track whatever
    `handoff.py` actually applies for THIS repo, not the shipped default, and not a hardcoded
    literal that could silently drift from either."""
    import json
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        base.joinpath("config.json").write_text(json.dumps({
            "discovery": {"source": "github", "github": {"repo": "acme/widget", "assignee": "@me"}},
            "ledger": {"handoff": {"proposed_label": "sdlc:my-custom-proposed"}},
        }))
        seen = []

        def run(args):
            seen.append(args)
            if args[:3] == ["gh", "api", "user"]:      # #1833: resolves the configured "@me"
                return "me-login"
            labels = _rest_labels(args)
            # exactly [proposed_label] (not-yet-promoted) has 1 hit; [proposed_label, sdlc:goal]
            # (already promoted) has 0 -- otherwise the subtraction below can't be tested honestly.
            return json.dumps([{"number": 1}]) if labels == ("sdlc:my-custom-proposed",) else "[]"
        out = _status().summary(str(base), run=run)
        assert out["proposed"] == 1
        assert any("sdlc:my-custom-proposed" in _rest_labels(c) for c in seen)
        assert not any("sdlc:needs-confirmation" in _rest_labels(c) for c in seen)


def _gh_sdlc(d, **gh):
    import json
    base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    (base / "config.json").write_text(json.dumps(
        {"discovery": {"source": "github", "github": {"repo": "acme/widget", **gh}}}))
    return base


def test_summary_github_mode_counts_the_board_not_the_empty_goals_dir():
    """The retro bug: a github-backed loop reported '0 parked' while 8 sat parked, because status read
    the (empty) .sdlc/goals dir. In github mode it must count open issues by label."""
    import json
    with tempfile.TemporaryDirectory() as d:
        base = _gh_sdlc(d, assignee="@me")
        seen = []

        # A parked issue KEEPS sdlc:in-progress but drops sdlc:goal, so active-in-progress is the
        # AND of both labels — bare sdlc:in-progress would count the 8 parked as active.
        def run(args):
            seen.append(args)
            if args[:3] == ["gh", "api", "user"]:      # #1833: resolves the configured "@me"
                return "me-login"
            labels = _rest_labels(args)
            table = {
                ("sdlc:goal",): 5,                       # open goals = 3 pending + 2 active in-progress
                ("sdlc:goal", "sdlc:in-progress"): 2,    # active in-progress (still a goal)
                ("sdlc:parked",): 8,                     # each ALSO still carries sdlc:in-progress
                ("sdlc:in-progress",): 10,               # the trap: 2 active + 8 parked
            }
            return json.dumps([{"number": i} for i in range(table.get(labels, 0))])
        out = _status().summary(str(base), run=run)
        assert out["parked"] == 8                    # the fix: all open sdlc:parked, not 0
        assert out["in_progress"] == 2               # ACTIVE only — not the 10 that carry the label
        assert out["pending"] == 3                   # 5 open sdlc:goal minus 2 active in-progress
        assert out["done"] == 0                      # not a single open-issue label in github mode
        # scoped to the loop's own queue -- restricted to the issues-listing REST calls: the #976
        # merge-queue advisor also runs in github mode and reads repo-level (`gh api ...`), never
        # assignee-scoped, data -- board membership is per-user, branch protection/PR history is
        # not. #1833: "@me" is resolved to a real login ONCE (one `gh api user` call above) and
        # that resolved login is what every REST call actually carries -- REST's `assignee=` field
        # has no `@me` alias of its own (a hard 422, confirmed live by #1829's own research).
        issue_calls = [a for a in seen if _is_issues_list_call(a)]
        assert issue_calls and all(any(v == "assignee=me-login" for v in a) for a in issue_calls)


def test_summary_github_mode_subtracts_needs_triage_from_pending():
    """#2427, a real bug found live and fixed here: `sdlc:needs-triage` (#2363 -- every tier of
    automatic unit classification tried and failed) carries `sdlc:goal` and is refused by
    `not_eligible_labels()` exactly like `sdlc:needs-label`/`sdlc:needs-unit`, both of which were
    already subtracted from `pending` -- this label alone was left out, so a set-aside issue the
    real picker correctly refuses was still counted as part of the ready queue."""
    import json
    with tempfile.TemporaryDirectory() as d:
        base = _gh_sdlc(d, assignee="@me")

        def run(args):
            if args[:3] == ["gh", "api", "user"]:
                return "me-login"
            labels = _rest_labels(args)
            table = {
                ("sdlc:goal",): 5,
                ("sdlc:goal", "sdlc:in-progress"): 0,
                ("sdlc:goal", "sdlc:needs-triage"): 2,   # 2 of the 5 open goals are set aside
            }
            return json.dumps([{"number": i} for i in range(table.get(labels, 0))])
        out = _status().summary(str(base), run=run)
        assert out["needs_triage"] == 2
        assert out["pending"] == 3                    # 5 open goals minus 2 set aside for triage


def test_summary_github_mode_counts_proposed_but_not_promoted():
    """#233/#1348: retro/hand-off follow-ups filed with immediately_actionable=False carry the
    distinct, config-overridable `handoff.proposed_label(config)` label (default renamed to
    sdlc:needs-confirmation by #1348; and NO sdlc:goal). /sigma-status must surface how many are
    pending promotion, so the set can't grow unnoticed. A proposal a human PROMOTED (added
    sdlc:goal) is no longer pending, so it drops out: proposed = open needs-confirmation MINUS
    those that also carry sdlc:goal — the exact mirror of pending = goal - active-in-progress.
    Still assignee-scoped like every other count on this dashboard."""
    import json
    with tempfile.TemporaryDirectory() as d:
        base = _gh_sdlc(d, assignee="@me")
        seen = []

        def run(args):
            seen.append(args)
            if args[:3] == ["gh", "api", "user"]:      # #1833: resolves the configured "@me"
                return "me-login"
            labels = _rest_labels(args)
            table = {
                ("sdlc:needs-confirmation",): 4,                   # 4 open proposals total...
                ("sdlc:needs-confirmation", "sdlc:goal"): 1,        # ...but 1 was promoted (now a real goal)
            }
            return json.dumps([{"number": i} for i in range(table.get(labels, 0))])
        out = _status().summary(str(base), run=run)
        assert out["proposed"] == 3                  # 4 open needs-confirmation minus 1 promoted
        # the proposed queries are assignee-scoped too, exactly like pending/parked/in-progress --
        # #1833: via the ONE resolved login, not the raw "@me" (see the test above for why)
        proposed_calls = [c for c in seen if "sdlc:needs-confirmation" in _rest_labels(c)]
        assert proposed_calls and all(any(v == "assignee=me-login" for v in c) for c in proposed_calls)


def test_summary_github_mode_fail_open_when_gh_unavailable():
    with tempfile.TemporaryDirectory() as d:
        base = _gh_sdlc(d)
        out = _status().summary(str(base), run=lambda a: "")   # gh down -> zeros, never raises
        assert out["parked"] == 0 and out["pending"] == 0 and out["in_progress"] == 0


def test_github_counts_never_uses_the_graphql_search_field():
    """#1833: `_github_counts` was the fourth call site #1829's own research explicitly enumerated
    as graphql-search-billed (any `--label` flag routes `gh` through the shared 5000/hour
    `graphql` `search()` field, confirmed live) but left out of scope for being off the loop's own
    pick path. It must never construct that shape again — every `n(*labels)` count is now the
    same REST fetch the pick path uses."""
    import json
    with tempfile.TemporaryDirectory() as d:
        base = _gh_sdlc(d)
        seen = []

        def run(args):
            seen.append(args)
            return json.dumps([{"number": 1}])
        _status().summary(str(base), run=run)
        assert not any(len(c) > 2 and c[0] == "gh" and c[1] == "issue" and c[2] == "list"
                        for c in seen)
        assert any(_is_issues_list_call(c) for c in seen)
        assert not any("--search" in c or "--label" in c for c in seen)


def test_github_counts_falls_back_to_gh_issue_list_when_sources_cannot_be_loaded(monkeypatch):
    """RESILIENCY: `_load_sources` is a cross-skill import (`sources.py` lives one skill directory
    over, under `sigma-loop/scripts/`) and fails open like every other cross-load in this file
    (`_load_merge_queue`, `_load_handoff`) — but the counts it feeds are core to this dashboard,
    unlike those two purely-advisory ones. A broken/partial install missing that sibling file must
    NOT zero out every count on `/sigma-status`; it must fall back to the pre-#1833 `gh issue list`
    construction instead, so the dashboard degrades to "costs more graphql quota" rather than
    "reports nothing"."""
    import json
    status = _status()
    monkeypatch.setattr(status, "_load_sources", lambda: None)
    with tempfile.TemporaryDirectory() as d:
        base = _gh_sdlc(d)
        seen = []

        def run(args):
            seen.append(args)
            labels = tuple(a for i, a in enumerate(args) if i and args[i - 1] == "--label")
            return json.dumps([{"number": 1}]) if labels == ("sdlc:goal",) else "[]"
        out = status.summary(str(base), run=run)
        assert out["pending"] == 1
        assert any(c[:3] == ["gh", "issue", "list"] for c in seen)


# --- #976: merge-queue advisory wiring -----------------------------------------------------------
# Read-only detect + recommend (never a mutation): status.py calls merge_queue.advise() in github
# mode only, and prints its message on the same silent-by-default idiom as the align/audit lines.

_MQ_PROTECTED = {"required_status_checks": {"strict": True, "contexts": ["ci/build"]}}


def _mq_run(protection, prs):
    import json as _json

    def run(args):
        if args[:2] == ["gh", "issue", "list"]:
            return "[]"
        if args[:2] == ["gh", "api"]:
            path = args[2]
            # every merge_queue.py call must be scoped to `repos/<repo>/...` -- a bare relative path
            # (a real bug this pin caught in PR review) resolves against the wrong repo entirely.
            assert path.startswith("repos/acme/widget/"), f"missing repos/{{repo}}/ prefix: {path!r}"
            if "protection" in path:
                return _json.dumps(protection) if protection is not None else ""
            if path.startswith("repos/acme/widget/pulls?"):
                return _json.dumps(prs) if prs is not None else ""
            if path.startswith("repos/acme/widget/actions/runs"):
                return ""
        return ""
    return run


def test_summary_surfaces_merge_queue_advice_when_the_shape_is_present():
    with tempfile.TemporaryDirectory() as d:
        base = _gh_sdlc(d)
        prs = [{"merged_at": f"2026-08-14T10:0{i}:00Z"} for i in range(6)]
        out = _status().summary(str(base), run=_mq_run(_MQ_PROTECTED, prs))
        assert out["merge_queue_advice"] and "merge queue" in out["merge_queue_advice"]
        _status().main(["status.py", str(base)])


def test_status_line_prints_merge_queue_advice(capsys):
    """main() itself takes no `run` override -- it resolves `_real_run` at call time -- so this
    monkeypatches the module's own `_real_run`, the same seam `summary()`'s `run or _real_run`
    default is built on, to prove the printed LINE (not just the summary dict) carries the advice."""
    with tempfile.TemporaryDirectory() as d:
        base = _gh_sdlc(d)
        prs = [{"merged_at": f"2026-08-14T10:0{i}:00Z"} for i in range(6)]
        mod = _status()
        original = mod._real_run
        mod._real_run = _mq_run(_MQ_PROTECTED, prs)
        try:
            mod.main(["status.py", str(base)])
        finally:
            mod._real_run = original
        assert "merge queue" in capsys.readouterr().out


def test_summary_stays_silent_without_the_shape():
    with tempfile.TemporaryDirectory() as d:
        base = _gh_sdlc(d)
        # unprotected -> negative control, no advice regardless of merge volume
        prs = [{"merged_at": f"2026-08-14T10:0{i}:00Z"} for i in range(20)]
        out = _status().summary(str(base), run=_mq_run(None, prs))
        assert out.get("merge_queue_advice") is None


def test_summary_local_mode_never_computes_merge_queue_advice():
    """Local (non-github) mode makes NO `gh` calls at all today -- that invariant must not regress.
    A `run` that raises on ANY call proves nothing from this module was ever invoked."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"
        (base / "goals").mkdir(parents=True)
        (base / "state").mkdir()

        def boom(args):
            raise AssertionError(f"local mode must not shell out: {args}")
        out = _status().summary(str(base), run=boom)
        assert out.get("merge_queue_advice") is None


def _bare(d):
    base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    return base


def test_ledger_count_is_zero_and_silent_when_absent(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _bare(d)
        assert _status().summary(str(base))["ledger_entries"] == 0
        _status().main(["status.py", str(base)])
        assert "ledger:" not in capsys.readouterr().out      # same line as before, for a repo without one


def test_ledger_count_unions_every_author_and_shows_in_the_line(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _bare(d)
        entries = base / "ledger" / "entries"; entries.mkdir(parents=True)
        (entries / "amy.jsonl").write_text('{"kind":"done"}\n\n{"kind":"parked"}\n')
        (entries / "bo.jsonl").write_text('{"kind":"note"}\n')
        assert _status().summary(str(base))["ledger_entries"] == 3     # blank line not counted
        _status().main(["status.py", str(base)])
        assert "ledger: 3 entries" in capsys.readouterr().out


# --- alignment-audit due counter -------------------------------------------------------------
# The drift audit's trigger must be something the loop can SEE. "Run it when drift becomes a
# problem" is circular: undetected drift is precisely what you cannot observe without the audit.

def _goals(base, done=0):
    (base / "goals").mkdir(parents=True, exist_ok=True)
    for i in range(done):
        (base / "goals" / f"{i:04d}-g.md").write_text("---\nstatus: done\n---\n")
    return str(base)


def test_align_counter_silent_without_a_north_star(capsys):
    """No stated direction = nothing to drift from. A drop-in project gains no new nag."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"
        _goals(base, done=9)
        assert _status().summary(str(base))["goals_since_align"] is None
        _status().main(["status.py", str(base)])
        assert "alignment" not in capsys.readouterr().out


def _with_north_star(base):
    (base / "context").mkdir(parents=True, exist_ok=True)
    (base / "context" / "north-star.md").write_text("# bets\n")
    return base


def test_align_due_after_enough_goals_with_no_prior_report(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=5)
        assert _status().summary(str(base))["goals_since_align"] == 5
        _status().main(["status.py", str(base)])
        assert "alignment check due (5 goals since the last)" in capsys.readouterr().out


def test_align_not_due_below_the_threshold(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=4)
        _status().main(["status.py", str(base)])
        assert "alignment" not in capsys.readouterr().out


def test_last_report_is_the_state_and_resets_the_count():
    """The report IS the bookkeeping — no extra state file to drift out of sync."""
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=12)
        al = base / "knowledge" / "align"; al.mkdir(parents=True)
        (al / "2026-01-01.md").write_text("goals_reviewed: 4\n")
        (al / "2026-06-01.md").write_text("goals_reviewed: 10\n")   # newest wins (ISO names sort)
        assert _status().summary(str(base))["goals_since_align"] == 2


def test_unparseable_report_reads_as_never_run():
    """Fail-open: a malformed report must not hide a due audit."""
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=6)
        al = base / "knowledge" / "align"; al.mkdir(parents=True)
        (al / "2026-06-01.md").write_text("# report with no count line\n")
        assert _status().summary(str(base))["goals_since_align"] == 6


def _state(base, iteration):
    (base / "state").mkdir(parents=True, exist_ok=True)
    (base / "state" / "STATE.md").write_text(f"iteration: {iteration}\nrun_iteration: 0\n")
    return base


def test_align_counter_sees_github_mode_work_via_the_loop_cursor(capsys):
    """In github mode goals ARE issues — .sdlc/goals/ stays empty, so a done-file tally reads 0
    forever and the audit would never come due. The loop's iteration cursor advances on every goal
    regardless of backlog source, so it carries the count that the filesystem can't."""
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=0)                       # github mode: no local goal files at all
        _state(base, iteration=7)
        assert _status().summary(str(base))["goals_since_align"] == 7
        _status().main(["status.py", str(base)])
        assert "alignment check due (7 goals since the last)" in capsys.readouterr().out


def test_align_counter_takes_the_larger_signal_not_the_sum():
    """Local+loop advances BOTH for the same goal — summing would double-count and fire at half the
    intended interval."""
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=6)
        _state(base, iteration=6)
        assert _status().summary(str(base))["goals_since_align"] == 6


def test_align_counter_still_sees_interactive_local_runs():
    """/sigma-goal records `done` without touching the loop cursor — the file tally carries it."""
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=8)
        _state(base, iteration=0)
        assert _status().summary(str(base))["goals_since_align"] == 8


def test_audit_fires_less_often_than_align():
    """Architecture erodes more slowly than strategy drifts, so the whole-repo audit runs on a
    multiple of the alignment interval — one that fired as often as align is one people learn to
    skip, and skipping is how a periodic check quietly stops being periodic."""
    st = _status()
    assert st.AUDIT_EVERY > st.ALIGN_EVERY


def test_audit_due_surfaces_only_after_enough_goals(tmp_path, capsys):
    """Unlike align, the audit needs no north-star: an artifact can rot against its own contracts
    and tests whether or not anyone wrote down a direction."""
    st = _status()
    base = tmp_path / ".sdlc"
    (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    (base / "state" / "STATE.md").write_text("iteration: 0\n")

    # one short of the threshold: the offer must stay silent
    for n in range(st.AUDIT_EVERY - 1):
        (base / "goals" / f"{n:04d}.md").write_text(f"---\nid: {n:04d}\nstatus: done\n---\nx\n")
    st.main(["status.py", str(base)])
    assert "/sigma-audit" not in capsys.readouterr().out

    # at the threshold: it surfaces
    n = st.AUDIT_EVERY - 1
    (base / "goals" / f"{n:04d}.md").write_text(f"---\nid: {n:04d}\nstatus: done\n---\nx\n")
    assert st.summary(str(base))["goals_since_audit"] >= st.AUDIT_EVERY
    st.main(["status.py", str(base)])
    assert "/sigma-audit" in capsys.readouterr().out


def test_summary_never_reports_a_blocked_goal_as_pending():
    """#1393: `mark_blocked` KEEPS `sdlc:goal` (blocked became an overlay; only a park gives up
    membership), so a blocked goal is inside the `sdlc:goal` count and must be subtracted out.
    Without this, `pending` -- the one number a human reads to decide whether the loop has anything
    to do -- reports blocked work as ready."""
    import json, tempfile
    with tempfile.TemporaryDirectory() as d:
        base = _gh_sdlc(d)

        def run(args):
            labels = _rest_labels(args)
            table = {
                ("sdlc:goal",): 6,                       # 2 pending + 1 active + 3 blocked
                ("sdlc:goal", "sdlc:in-progress"): 1,
                ("sdlc:goal", "sdlc:blocked"): 3,
            }
            return json.dumps([{"number": i} for i in range(table.get(labels, 0))])
        out = _status().summary(str(base), run=run)
        assert out["pending"] == 2                   # 6 - 1 active - 3 blocked
        assert out["in_progress"] == 1
        assert out["blocked"] == 3                   # surfaced, not just subtracted away

"""#1824: dossier.py -- the Business-stage entry point (`sigma-dossier`).

Real `sources.GitHubSource`/`sources.LocalSource` throughout, driven by an injectable runner for the
GitHub-mode tests -- same convention `tests/test_unpark.py` already establishes. No `gqlfake` needed:
`create_dependency` issues plain `gh issue create` / `issue edit --add-assignee` / `issue comment` /
`label create` calls, never the GraphQL label-swap transport.
"""
import json, pathlib, importlib.util, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
D = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-dossier" / "scripts"


def _mod(name, base=S):
    spec = importlib.util.spec_from_file_location(name, base / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


dossier = _mod("dossier", base=D)
backlog_check = _mod("backlog_check", base=S)


def _config(**gh):
    return {"discovery": {"source": "github", "github": {"repo": "acme/widget", **gh}}}


def _local_config():
    return {"discovery": {"source": "local-goals"}}


def _runner(create_number="42", fail_on=()):
    calls = []

    def run(args):
        joined = " ".join(str(a) for a in args)
        for needle in fail_on:
            if needle in joined:
                raise RuntimeError("simulated gh failure: %s" % needle)
        calls.append(list(args))
        if len(args) >= 2 and args[0] == "issue" and args[1] == "create":
            return "https://github.com/acme/widget/issues/%s\n" % create_number
        return ""

    run.calls = calls
    return run


def _answers(**overrides):
    base = {
        "title": "Self-serve export",
        "problem": "Customers can't get their data out without asking support.",
        "why_now": "Support tickets for exports are up 40% this quarter.",
        "who": "Any customer on a paid plan.",
        "outcome": "A customer can export their own data without filing a ticket.",
        "constraints": "none",
        "non_goals": "none",
        "next_step": "file and stop",
    }
    base.update(overrides)
    return base


def _questions_asked():
    return dossier.bank()


# --------------------------------------------------------------------------- bank shape

def test_bank_is_flat_and_ordered():
    b = dossier.bank()
    ids = [q["id"] for q in b]
    assert ids == ["title", "problem", "why_now", "who", "outcome", "constraints", "non_goals",
                   "next_step"]
    # only the closing question carries options -- every other slot is open business content
    assert [bool(q.get("options")) for q in b] == [False] * 7 + [True]
    assert b[-1]["options"] == ["file and stop", "continue to Product"]


def test_bank_returns_copies_not_the_live_module_data():
    b1 = dossier.bank()
    b1[0]["ask"] = "mutated"
    b2 = dossier.bank()
    assert b2[0]["ask"] != "mutated"


def test_decisions_mapping_covers_exactly_the_closing_questions_options():
    """`DECISIONS` is what a caller (the SKILL) maps `next_step`'s free-text answer through before
    calling `file(decision=...)` -- pinned here so the two can never quietly drift apart (e.g. a
    reworded option in `CLOSING_QUESTION` with `DECISIONS` left stale)."""
    assert set(dossier.DECISIONS) == set(dossier.CLOSING_QUESTION["options"])
    assert set(dossier.DECISIONS.values()) == {"stop", "continue"}


# --------------------------------------------------------------------------- _title

def test_title_collapses_whitespace():
    assert dossier._title({"title": "  Self-serve   export  "}) == "Self-serve export"


def test_title_falls_back_when_blank():
    """Defensive-only in the current call graph -- `file()` already refuses a blank title before
    `_title` is ever reached -- but a fallback with zero coverage is a fallback nobody has actually
    checked works, so it gets its own direct test rather than staying an unreachable-in-practice
    branch."""
    assert dossier._title({"title": "   "}) == "Untitled dossier"
    assert dossier._title({}) == "Untitled dossier"


# --------------------------------------------------------------------------- render_block

def test_render_block_orders_by_bank_not_dict_insertion():
    block = dossier.render_block({"non_goals": "none", "title": "Z", "problem": "P"})
    lines = [l for l in block.splitlines() if l.startswith("- **")]
    assert [l.split("**")[1] for l in lines] == ["title", "problem", "non_goals"]


def test_render_block_includes_the_question_text_when_given():
    block = dossier.render_block({"title": "Z"}, questions_asked=dossier.bank())
    assert "_In a few words, what should we call this?_" in block
    assert "- **title** — Z" in block


def test_render_block_is_fenced_with_dossier_specific_markers():
    block = dossier.render_block({"title": "Z"})
    assert block.startswith(dossier.DOSSIER_QA_START)
    assert block.rstrip().endswith(dossier.DOSSIER_QA_END)
    # deliberately its own fence, not unpark's shared one
    assert dossier.DOSSIER_QA_START != backlog_check.UNPARK_QA_START


def test_render_block_scaffolding_introduces_no_blocker_vocabulary_of_its_own():
    """DOSSIER_QA is NOT registered in `blocker_scan.py`'s shared strip list (see the module
    docstring: a Dossier is never `sdlc:goal`, so it is never blocker-scanned by the real pick path
    in the first place -- unlike unpark's fence, which protects a ticket that genuinely IS scanned).
    So the one guarantee actually worth pinning is narrower than unpark's: the scaffolding THIS
    function adds -- the fence lines, the heading, the italic question, the bullet syntax -- must not
    itself manufacture a blocker reference out of nothing when every answer is blocker-free."""
    block = dossier.render_block({"title": "Needs after export", "problem": "See requirements"},
                                 questions_asked=dossier.bank())
    assert backlog_check._referenced_blocker_refs({"ref": "5", "raw": block}) == set()


def test_render_block_does_not_auto_strip_like_unparks_fence_does():
    """Deliberate, and different from unpark: a genuine-looking blocker phrase inside a free-text
    ANSWER is still visible to a generic scan -- this fence is not registered as an exempt span, so
    it does not silently swallow one. (In practice nothing scans a Dossier body this way, because it
    is never `sdlc:goal`; this test documents the honest behavior if something ever did, rather than
    claiming a protection that was never wired in.)"""
    block = dossier.render_block({"constraints": "this needs #1234 to land first"})
    assert backlog_check._referenced_blocker_refs({"ref": "5", "raw": block}) == {"1234"}


# --------------------------------------------------------------------------- file() validation

def test_file_rejects_an_unknown_decision():
    run = _runner()
    result = dossier.file(".sdlc", _config(), _answers(), decision="maybe",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert "decision" in result["detail"]
    assert run.calls == []


def test_file_rejects_missing_required_answers():
    run = _runner()
    incomplete = _answers()
    del incomplete["problem"]
    result = dossier.file(".sdlc", _config(), incomplete, decision="stop",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert "problem" in result["detail"]
    assert run.calls == []


def test_file_rejects_a_blank_required_answer():
    run = _runner()
    blank = _answers(non_goals="   ")
    result = dossier.file(".sdlc", _config(), blank, decision="stop",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert "non_goals" in result["detail"]
    assert run.calls == []


def test_file_refuses_when_decision_contradicts_the_recorded_next_step():
    """Caught in review: nothing previously stopped a caller passing `decision='stop'` while the
    recorded `next_step` answer says 'continue to Product' (or vice versa) -- the issue's own
    persisted record would then disagree with what actually happened, with nothing to catch it.
    AGENTS.md SAFETY: refuse loudly rather than persist a self-contradictory record."""
    run = _runner()
    mismatched = _answers(next_step="continue to Product")
    result = dossier.file(".sdlc", _config(), mismatched, decision="stop",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert "next_step" in result["detail"]
    assert run.calls == []


def test_file_allows_an_unrecognized_next_step_answer_through_as_free_text():
    """The cross-check only fires for the two KNOWN option strings -- a caller who let someone type
    a free-text variation into `next_step` (not one of the two exact options) isn't blocked by a
    check that has nothing to compare against; it reaches `gh` like any other open-ended answer."""
    run = _runner(create_number="61")
    odd = _answers(next_step="just file it please")
    result = dossier.file(".sdlc", _config(), odd, decision="stop",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "filed"


# --------------------------------------------------------------------------- file() success (github)

def test_file_creates_a_story_labelled_never_goal_issue_and_self_assigns():
    run = _runner(create_number="99")
    result = dossier.file(".sdlc", _config(), _answers(), questions_asked=_questions_asked(),
                          decision="stop", source=dossier.sources.GitHubSource(_config(), run=run))
    assert result == {"outcome": "filed", "number": "99", "title": "Self-serve export",
                      "decision": "stop", "detail": "filed #99 and stopped, as asked"}

    create_calls = [c for c in run.calls if len(c) >= 2 and c[0] == "issue" and c[1] == "create"]
    assert len(create_calls) == 1
    args = create_calls[0]
    assert "--label" in args and "story" in args
    # never sdlc:goal on THIS issue's own create call
    labels_passed = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
    assert labels_passed == ["story"]
    assert "sdlc:goal" not in labels_passed

    assign_calls = [c for c in run.calls if len(c) >= 3 and c[0] == "issue" and c[1] == "edit"
                    and "--add-assignee" in c]
    assert len(assign_calls) == 1
    assert assign_calls[0][assign_calls[0].index("--add-assignee") + 1] == "@me"


def test_file_posts_the_same_block_as_a_comment():
    run = _runner(create_number="7")
    answers = _answers()
    result = dossier.file(".sdlc", _config(), answers, questions_asked=_questions_asked(),
                          decision="stop", source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "filed"
    comment_calls = [c for c in run.calls if len(c) >= 2 and c[0] == "issue" and c[1] == "comment"]
    assert len(comment_calls) == 1
    body = comment_calls[0][comment_calls[0].index("--body") + 1]
    assert body == dossier.render_block(answers, _questions_asked())


def test_file_continue_decision_names_the_pending_product_stage_honestly():
    run = _runner(create_number="8")
    result = dossier.file(".sdlc", _config(), _answers(next_step="continue to Product"),
                          decision="continue",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["decision"] == "continue"
    assert "#1825" in result["detail"] or "goal-design" in result["detail"]


def test_file_dry_run_creates_nothing():
    run = _runner()
    result = dossier.file(".sdlc", _config(), _answers(), decision="stop", apply=False,
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "would"
    assert run.calls == []


def test_file_reports_failure_when_gh_returns_no_number():
    def run(args):
        if len(args) >= 2 and args[0] == "issue" and args[1] == "create":
            return ""            # no number parseable
        return ""
    result = dossier.file(".sdlc", _config(), _answers(), decision="stop",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert "issue number" in result["detail"]


# --------------------------------------------------------------------------- local-mode degrade

def test_file_degrades_to_a_local_proposed_goal_never_pending():
    with tempfile.TemporaryDirectory() as tmp:
        source = dossier.sources.LocalSource(tmp, _local_config())
        result = dossier.file(tmp, _local_config(), _answers(), decision="stop", source=source)
        assert result["outcome"] == "filed"
        goal_path = pathlib.Path(tmp) / "goals" / ("%04d-self-serve-export.md" % int(result["number"]))
        assert goal_path.exists()
        text = goal_path.read_text(encoding="utf-8")
        assert "status: proposed" in text          # never "pending" -- the local sdlc:goal equivalent
        assert "Labels: story" in text
        assert "Owner: @me" in text


# --------------------------------------------------------------------------- source resolution

def test_resolve_source_defaults_to_local_when_discovery_source_unset():
    with tempfile.TemporaryDirectory() as tmp:
        src = dossier._resolve_source(tmp, {}, source=None, run=None)
        assert isinstance(src, dossier.sources.LocalSource)


def test_resolve_source_threads_run_through_for_github_mode():
    run = _runner()
    src = dossier._resolve_source(".sdlc", _config(), source=None, run=run)
    assert isinstance(src, dossier.sources.GitHubSource)
    assert src.repo == "acme/widget"


# --------------------------------------------------------------------------- CLI

def test_main_bank_prints_the_same_data_as_bank():
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = dossier.main(["dossier.py", "bank"])
    assert code == 0
    assert json.loads(buf.getvalue()) == dossier.bank()


def test_main_file_end_to_end_against_a_temp_answers_file(tmp_path, monkeypatch):
    import io, contextlib
    run = _runner(create_number="55")
    monkeypatch.setattr(dossier, "_resolve_source",
                        lambda sdlc_dir, config, source=None, run=None:
                            dossier.sources.GitHubSource(config, run=_runner(create_number="55")))
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(json.dumps({"answers": _answers(), "questions": dossier.bank()}))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = dossier.main(["dossier.py", "file", ".sdlc", "--answers", str(answers_file),
                             "--decision", "stop"])
    assert code == 0
    out = json.loads(buf.getvalue())
    assert out["outcome"] == "filed"
    assert out["number"] == "55"


def test_main_file_usage_error_on_missing_flags():
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        code = dossier.main(["dossier.py", "file", ".sdlc"])
    assert code == 2
    assert "usage" in buf.getvalue()


# ------------------------------------------------------- #1916: the bounded follow-up tail
#
# The fixed bank cannot reach a domain-specific semantic decision (the E2E run's unanswerable
# "what does the next occurrence count from", and "how does a series stop"), and it cannot notice
# that two answers it already has contradict each other. The mechanism under test is the bounded
# tail that closes both: at most `MAX_FOLLOWUPS` ANSWERED follow-ups, plus an uncapped record of
# what could not be settled. The asymmetry is deliberate and is itself pinned below.


def _followup_questions(*ids):
    """A `questions` entry per follow-up id -- the record has to carry the ask, or an answer sits
    against a question nobody can read back."""
    return dossier.bank() + [{"id": i, "ask": "follow-up: %s?" % i} for i in ids]


def test_followups_policy_is_engine_owned_data():
    policy = dossier.followups()
    assert policy["max"] == dossier.MAX_FOLLOWUPS == 3
    assert policy["answered_prefix"] == dossier.FOLLOWUP_PREFIX == "followup_"
    assert policy["unresolved_prefix"] == dossier.OPEN_PREFIX == "open_"
    assert len(policy["triggers"]) == 3 and all(isinstance(t, str) and t for t in policy["triggers"])


def test_followups_returns_copies_not_the_live_module_data():
    p1 = dossier.followups()
    p1["triggers"].append("mutated")
    assert "mutated" not in dossier.followups()["triggers"]


def test_no_bank_id_could_be_mistaken_for_a_follow_up():
    """The two prefixes classify an extra. A bank id that happened to start with one would be
    counted as a follow-up by `_extras`' complement and silently change the cap's meaning."""
    for q in dossier.bank():
        assert not q["id"].startswith((dossier.FOLLOWUP_PREFIX, dossier.OPEN_PREFIX))


def test_file_accepts_answered_follow_ups_up_to_the_cap():
    """The CONTROL for the cap: exactly `MAX_FOLLOWUPS` answered follow-ups file normally, and reach
    the issue body."""
    ids = ["followup_%d" % i for i in range(dossier.MAX_FOLLOWUPS)]
    answers = _answers(**{i: "answered" for i in ids})
    run = _runner(create_number="70")
    result = dossier.file(".sdlc", _config(), answers, questions_asked=_followup_questions(*ids),
                          decision="stop", source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "filed"
    body = [c for c in run.calls if c[:2] == ["issue", "create"]][0]
    assert all(i in body[body.index("--body") + 1] for i in ids)


def test_file_refuses_more_answered_follow_ups_than_the_cap():
    ids = ["followup_%d" % i for i in range(dossier.MAX_FOLLOWUPS + 1)]
    answers = _answers(**{i: "answered" for i in ids})
    run = _runner()
    result = dossier.file(".sdlc", _config(), answers, questions_asked=_followup_questions(*ids),
                          decision="stop", source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert str(dossier.MAX_FOLLOWUPS) in result["detail"]
    assert run.calls == []


def test_file_never_caps_or_refuses_what_could_not_be_settled():
    """The asymmetry, pinned. A single combined cap would make REFUSAL the consequence of recording
    a doubt -- an agent standing at the cap deletes an `open_` entry to get the record filed, which
    is precisely the silent loss #1916 is about. So the cap bounds the ANSWERED half only."""
    ids = ["open_%d" % i for i in range(dossier.MAX_FOLLOWUPS + 3)]
    answers = _answers(**{i: "unknown until someone decides" for i in ids})
    run = _runner(create_number="71")
    result = dossier.file(".sdlc", _config(), answers, questions_asked=_followup_questions(*ids),
                          decision="stop", source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "filed"


def test_file_refuses_an_extra_whose_id_carries_neither_prefix():
    """This refusal is what makes the cap countable at all: a mistyped `folloup_anchor` would
    otherwise be an unclassified extra, recorded but never counted against anything."""
    answers = _answers(folloup_anchor="answered")
    run = _runner()
    result = dossier.file(".sdlc", _config(), answers,
                          questions_asked=_followup_questions("folloup_anchor"), decision="stop",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert "folloup_anchor" in result["detail"]
    assert dossier.FOLLOWUP_PREFIX in result["detail"] and dossier.OPEN_PREFIX in result["detail"]
    assert run.calls == []


def test_file_refuses_a_follow_up_with_no_recorded_question():
    answers = _answers(followup_anchor="from completion")
    run = _runner()
    result = dossier.file(".sdlc", _config(), answers, questions_asked=dossier.bank(),
                          decision="stop", source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert "followup_anchor" in result["detail"]
    assert run.calls == []


def test_file_refuses_an_unresolved_entry_with_no_recorded_question():
    """The mirror of the line above, and not redundant with it: both shape guards read `extras`, so
    each is pinned on ONE prefix only unless the other half is measured too. Narrowing this one to
    the answered half leaves every other test green -- and the unresolved half is the half that
    TRAVELS, into `goal-design`'s Doubts, where an answer with no question attached is a doubt
    nobody can read back."""
    answers = _answers(open_stop="nobody has decided how a series is cancelled")
    run = _runner()
    result = dossier.file(".sdlc", _config(), answers, questions_asked=dossier.bank(),
                          decision="stop", source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert "open_stop" in result["detail"]
    assert run.calls == []


def test_file_refuses_a_blank_follow_up_answer():
    answers = _answers(open_stop="   ")
    run = _runner()
    result = dossier.file(".sdlc", _config(), answers,
                          questions_asked=_followup_questions("open_stop"), decision="stop",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert "open_stop" in result["detail"]
    assert run.calls == []


def test_file_refuses_a_blank_answered_follow_up_too():
    """The mirror of the blank check, for the same reason: the guard reads `extras`, and only the
    unresolved half was measured. Narrowing it to the unresolved half survived the whole file."""
    answers = _answers(followup_anchor="\t  ")
    run = _runner()
    result = dossier.file(".sdlc", _config(), answers,
                          questions_asked=_followup_questions("followup_anchor"), decision="stop",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "failed"
    assert "followup_anchor" in result["detail"]
    assert run.calls == []


def test_file_names_the_unresolved_questions_in_its_detail():
    answers = _answers(open_stop="nobody has decided how a series is cancelled")
    run = _runner(create_number="72")
    result = dossier.file(".sdlc", _config(), answers,
                          questions_asked=_followup_questions("open_stop"), decision="stop",
                          source=dossier.sources.GitHubSource(_config(), run=run))
    assert result["outcome"] == "filed"
    assert "open_stop" in result["detail"]


def test_file_result_is_unchanged_when_nothing_was_followed_up():
    """The control for the line above: a dossier with no tail returns exactly what it returned
    before #1916 -- no new key, no altered detail."""
    run = _runner(create_number="99")
    result = dossier.file(".sdlc", _config(), _answers(), questions_asked=_questions_asked(),
                          decision="stop", source=dossier.sources.GitHubSource(_config(), run=run))
    assert result == {"outcome": "filed", "number": "99", "title": "Self-serve export",
                      "decision": "stop", "detail": "filed #99 and stopped, as asked"}


def test_dry_run_names_the_unresolved_questions_too():
    """A `--dry-run` that hid them would be the one preview where the tail is invisible."""
    result = dossier.file(".sdlc", _config(), _answers(open_stop="undecided"),
                          questions_asked=_followup_questions("open_stop"), decision="stop",
                          apply=False, source=dossier.sources.GitHubSource(_config(), run=_runner()))
    assert result["outcome"] == "would"
    assert "open_stop" in result["detail"]


def test_render_block_groups_the_tail_by_kind_after_the_bank():
    block = dossier.render_block(
        _answers(open_stop="undecided", followup_anchor="from completion"),
        questions_asked=_followup_questions("followup_anchor", "open_stop"))
    ids = [l.split("**")[1] for l in block.splitlines() if l.startswith("- **")]
    assert ids[:len(dossier.REQUIRED_IDS)] == dossier.REQUIRED_IDS      # bank first, in bank order
    assert ids[len(dossier.REQUIRED_IDS):] == ["followup_anchor", "open_stop"]
    assert block.index(dossier.FOLLOWUP_HEADING) < block.index(dossier.OPEN_HEADING)
    assert "_follow-up: open_stop?_" in block                            # the ask travels with it


def test_render_block_still_records_an_extra_the_filer_would_refuse():
    """The renderer stays lossless even where `file()` is strict: the strict gate is the filer, so a
    reader of an already-written block never silently loses an answer somebody gave."""
    block = dossier.render_block({"title": "Z", "folloup_anchor": "answered"})
    assert "- **folloup_anchor** — answered" in block


def test_render_block_tail_scaffolding_introduces_no_blocker_vocabulary():
    """Same guarantee `test_render_block_scaffolding_introduces_no_blocker_vocabulary_of_its_own`
    pins for the bank half, re-run with the new headings present."""
    block = dossier.render_block({"title": "Recurring tasks", "open_stop": "how a series ends"},
                                 questions_asked=_followup_questions("open_stop"))
    assert backlog_check._referenced_blocker_refs({"ref": "5", "raw": block}) == set()
    # and the headings' own claim, measured rather than asserted in a comment: a heading carrying a
    # trigger word would manufacture an edge out of any `#N` that happened to land within
    # `_BLOCK_RE`'s 40-character window of it, so put one there and check nothing matches.
    for heading in (dossier.FOLLOWUP_HEADING, dossier.OPEN_HEADING):
        assert not backlog_check._BLOCK_RE.search(heading + " #1234")


def test_main_followups_prints_the_same_data_as_followups():
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = dossier.main(["dossier.py", "followups"])
    assert code == 0
    assert json.loads(buf.getvalue()) == dossier.followups()


# ------------------------------------------------------- #1916: the prose that carries it

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_dossier_skill_documents_the_bounded_follow_up_mechanism():
    """The cap is enforced in `dossier.py` precisely because prose is all a host without hooks has
    (AGENTS.md). The prose still has to state the same number, or the two drift."""
    text = (ROOT / "skills" / "sigma-dossier" / "SKILL.md").read_text(encoding="utf-8")
    assert "at most %d follow-up" % dossier.MAX_FOLLOWUPS in text
    assert dossier.FOLLOWUP_PREFIX in text and dossier.OPEN_PREFIX in text
    assert "followups" in text                      # the verb that serves the policy


def test_goal_design_skill_carries_dossier_open_questions_into_doubts():
    """The unresolved half is only worth recording if the next stage reads it."""
    text = (ROOT / "skills" / "sigma-goal-design" / "SKILL.md").read_text(encoding="utf-8")
    assert dossier.OPEN_PREFIX in text
    assert "Doubts" in text


# ------------------------------------------------ #1954: the front door states what the route costs

def _dossier_skill():
    return (ROOT / "skills" / "sigma-dossier" / "SKILL.md").read_text(encoding="utf-8")


def test_the_handoff_says_what_the_product_stage_will_cost():
    """The measured first-impression problem: one idea produced 1 story + 1 epic + 5 children on a
    live board. Proportionate for something spanning six components; absurd for a `--quiet` flag,
    which is exactly what somebody reaches for when trying a new tool. The number people quote has
    to arrive with the other one beside it, at the door, or the door reads as having one gear."""
    flat = " ".join(_dossier_skill().split()).replace("*", "")
    assert "2 tickets" in flat, "the light outcome is never quantified at the front door"
    assert "1 story + 1 epic + 5 children" in flat, \
        "the heavy figure is not named, so nothing says which case it belongs to"
    assert "spanning six components" in flat, \
        "the heavy figure is quoted without the work that earned it"


def test_the_front_door_admits_a_cheaper_neighbour():
    """A pipeline that never says 'you don't need me for this' gets used once on something small
    and then not at all. The honest route for work with nothing to map is an ordinary goal issue,
    and Stage 0 is the only place a person is standing when that is still true."""
    does_not = _dossier_skill().split("## What this skill does not do", 1)
    assert len(does_not) == 2, "the skill's own not-this section moved -- re-check this pin"
    flat = " ".join(does_not[1].split()).replace("*", "")
    assert "cheapest door" in flat, "nothing says this is not the cheapest route"
    assert "`sdlc:goal` issue" in flat, "no alternative route is named, so the advice is unusable"
    assert "no mapping left for Stage 1" in flat, \
        "the test for when to skip the pipeline is not stated, only the recommendation"

"""#2160: a project's recorded known-flake exception, read by the goal run itself.

One test per clause of the issue: the setting, each fail-closed case, the evidence state, the base rule, the PR body,
the reviewer's brief and gate, and the doctor rows. Controls: each clause was broken on its own and its own test went
red (the notes at the bottom of this file name the break for each).
"""
import datetime
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
S = HERE.parent / "skills" / "sigma-loop" / "scripts"
sys.path.insert(0, str(HERE))
import test_doctor as td  # noqa: E402

TODAY = datetime.date(2026, 11, 13)


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


kf = _mod("known_flakes")

A = "tests/test_a.py::test_one"
B = "tests/test_b.py::test_two[x]"


def entry(**over):
    e = {"test": "tests/test_a.py::*", "tracking": "#1", "approved_by": "the lead",
         "record": "https://example.test/approval", "expires": "2026-12-31", "bases": ["feature/*"]}
    e.update(over)
    return e


def cfg(*entries, **work):
    c = {"verify": {"known_flakes": list(entries)}}
    if work:
        c["work"] = work
    return c


def pytest_output(*failed, errors=(), passed=10, tail=""):
    lines = ["============ short test summary info ============"]
    lines += ["FAILED %s - AssertionError: boom" % f for f in failed]
    lines += ["ERROR %s - setup blew up" % f for f in errors]
    n = len(failed)
    summary = "%d failed, %d passed" % (n, passed)
    if errors:
        summary = "%d failed, %d passed, %d errors" % (n, passed, len(errors))
    return "\n".join(lines) + "\n============ " + summary + " in 3.2s ============\n" + tail


def solo_ok(_id):
    return "ok", 0, "1 passed"


def evaluate(output, entries, base="feature/x", solo=solo_ok, **kw):
    return kf.evaluate(output, entries, base, kw.pop("today", TODAY), solo, kw.pop("integration", None))


def parsed(*entries):
    got, error = kf.read(cfg(*entries))
    assert error is None, error
    return got


# ---- the setting ----------------------------------------------------------------------------------------------

def test_absent_setting_reads_as_no_entries():
    assert kf.read({}) == ([], None)
    assert kf.read({"verify": {}}) == ([], None)


@pytest.mark.parametrize("bad", [
    "not a list", [None], [entry(bases=[])], [entry(bases="feature/*")], [entry(expires="soon")],
    [entry(expires="2026-13-40")], [entry(record="not a url")], [entry(tracking="")], [entry(approved_by=" ")],
    [entry(test="two words")], [entry(test="--flag")], [dict(entry(), extra=1)],
    [{k: v for k, v in entry().items() if k != "record"}],
])
def test_a_malformed_setting_is_refused_whole(bad):
    entries, error = kf.read({"verify": {"known_flakes": bad}})
    assert entries == [] and error


def test_one_bad_entry_refuses_the_whole_list():
    entries, error = kf.read(cfg(entry(), entry(expires="nope")))
    assert entries == [] and error


def test_a_non_object_verify_block_is_an_error():
    assert kf.read({"verify": "x"})[1]


# ---- fails closed: each case ----------------------------------------------------------------------------------

def test_happy_path_every_failure_matches_and_passes_alone():
    got = evaluate(pytest_output(A), parsed(entry()))
    assert got["accepted"] is True and got["ids"] == [A] and got["matched"] == {A: "tests/test_a.py::*"}
    assert got["solo"][A]["exit"] == 0


def test_a_failing_test_matching_no_entry_stays_red():
    got = evaluate(pytest_output(A, B), parsed(entry()))
    assert got["accepted"] is False and B in got["reason"] and got["solo"] == {}


def test_an_expired_entry_is_ignored():
    got = evaluate(pytest_output(A), parsed(entry(expires="2026-11-13")))
    assert got["accepted"] is False and "no unexpired" in got["reason"]


def test_an_entry_expires_on_its_date_not_after_it():
    assert kf.expired(parsed(entry(expires="2026-11-13"))[0], TODAY) is True
    assert kf.expired(parsed(entry(expires="2026-11-14"))[0], TODAY) is False


def test_an_entry_that_does_not_cover_the_base_stays_red():
    got = evaluate(pytest_output(A), parsed(entry()), base="release/1")
    assert got["accepted"] is False and "covers base" in got["reason"]


def test_no_base_stays_red():
    got = evaluate(pytest_output(A), parsed(entry()), base=None)
    assert got["accepted"] is False


def test_work_base_is_not_covered_by_a_wildcard():
    wildcard = parsed(entry(bases=["*"]))
    assert evaluate(pytest_output(A), wildcard, base="trunk", integration="trunk")["accepted"] is False
    named = parsed(entry(bases=["trunk"]))
    assert evaluate(pytest_output(A), named, base="trunk", integration="trunk")["accepted"] is True


def test_a_wildcard_still_covers_other_bases_when_work_base_is_set():
    assert evaluate(pytest_output(A), parsed(entry(bases=["*"])), base="feature/x", integration="trunk")["accepted"]


@pytest.mark.parametrize("status,code", [("failed", 1), ("error", None), ("timeout", None), ("ok", 5), ("timeout", 0)])
def test_a_solo_rerun_that_fails_errors_or_times_out_stays_red(status, code):
    got = evaluate(pytest_output(A), parsed(entry()), solo=lambda _i: (status, code, "no"))
    assert got["accepted"] is False and "solo re-run" in got["reason"]


def test_a_solo_rerun_that_raises_stays_red():
    def boom(_i):
        raise RuntimeError("x")
    assert evaluate(pytest_output(A), parsed(entry()), solo=boom)["accepted"] is False


def test_every_failing_test_is_rerun_alone_and_one_failure_among_many_is_red():
    calls = []

    def solo(i):
        calls.append(i)
        return ("ok", 0, "") if i == A else ("failed", 1, "")
    got = evaluate(pytest_output(A, B), parsed(entry(test="tests/*")), solo=solo)
    assert calls == [A, B] and got["accepted"] is False


def test_a_collection_error_that_no_entry_names_stays_red():
    out = pytest_output(errors=["tests/test_a.py"], passed=0)
    got = evaluate(out, parsed(entry()))
    assert got["ids"] == ["tests/test_a.py"] and got["accepted"] is False


def test_a_setup_error_is_accepted_only_when_an_entry_names_it():
    out = pytest_output(errors=["tests/test_a.py::test_one"])
    assert evaluate(out, parsed(entry()))["accepted"] is True
    other = pytest_output(errors=["tests/test_c.py::test_x"])
    assert evaluate(other, parsed(entry()))["accepted"] is False


def test_output_with_no_failing_id_stays_red():
    assert evaluate("exit 1, nothing else\n", parsed(entry()))["accepted"] is False


def test_a_count_that_disagrees_with_the_ids_stays_red():
    out = pytest_output(A).replace("1 failed", "2 failed")
    got = evaluate(out, parsed(entry()))
    assert got["accepted"] is False and "counts 2" in got["reason"]


def test_a_zero_failure_count_with_no_id_is_not_a_vacuous_accept():
    got = evaluate("============ 0 failed, 3 passed in 1s ============\n", parsed(entry()))
    assert got["accepted"] is False and "no failing test id" in got["reason"]


def test_output_with_no_final_count_stays_red():
    out = "FAILED %s - x\n" % A
    assert evaluate(out, parsed(entry()))["accepted"] is False


def test_a_run_stopped_early_stays_red():
    out = pytest_output(A).replace("short test summary", "short test summary") + "!!! stopping after 1 failures !!!\n"
    got = evaluate(out, parsed(entry()))
    assert got["accepted"] is False and "stopped early" in got["reason"]


def test_a_parameterised_id_matches_literally_not_as_a_character_class():
    out = pytest_output(B)
    assert evaluate(out, parsed(entry(test=B)))["accepted"] is True
    assert evaluate(out, parsed(entry(test="tests/test_b.py::test_two[y]")))["accepted"] is False


# ---- the verify: evidence state ------------------------------------------------------------------------------

def _loop():
    return _mod("loop")


def _tree(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sdlc = tmp_path / ".sdlc"
    (sdlc / "goals").mkdir(parents=True)
    (sdlc / "state").mkdir()
    return sdlc


def _verdict(config, output=None, base="feature/x", returncode=1, test_first=None, before=None, after=None,
             python="python3", solo=solo_ok):
    lp = _loop()
    same = {"fingerprint": "f"}
    return lp._known_flakes_verdict(
        ".sdlc", config, ".", base, returncode, test_first if test_first is not None else {},
        output if output is not None else pytest_output(A), before if before is not None else same,
        after if after is not None else same, python, run_solo=solo, today=TODAY)


def test_verdict_accepts_a_covered_red_verify():
    got = _verdict(cfg(entry()))
    assert got["accepted"] is True and got["entries"][0]["tracking"] == "#1"


def test_verdict_is_not_in_play_for_a_green_verify_or_no_setting():
    assert _verdict(cfg(entry()), returncode=0) is None
    assert _verdict({}) is None
    assert _verdict({"verify": {"known_flakes": []}}) is None


def test_verdict_malformed_setting_stays_red_and_says_why():
    got = _verdict({"verify": {"known_flakes": "x"}})
    assert got["accepted"] is False and "must be a list" in got["reason"]


def test_verdict_planned_tests_that_did_not_pass_stay_red():
    got = _verdict(cfg(entry()), test_first={"provenance": "p", "passed": False})
    assert got["accepted"] is False and "planned" in got["reason"]


def test_verdict_a_tree_that_changed_during_verify_stays_red():
    got = _verdict(cfg(entry()), after={"fingerprint": "g"})
    assert got["accepted"] is False and "changed" in got["reason"]


def test_verdict_no_recorded_base_stays_red():
    got = _verdict(cfg(entry()), base=None)
    assert got["accepted"] is False and "no recorded base" in got["reason"]


def test_verdict_no_interpreter_stays_red():
    assert _verdict(cfg(entry()), python=None)["accepted"] is False


def test_verdict_never_raises():
    got = _verdict(cfg(entry()), solo=lambda _i: 1 / 0)
    assert got["accepted"] is False


def _solo_project(tmp_path, base="feature/x", entries=None):
    """A real tree whose full verify is red on two named tests that each pass alone under real pytest."""
    sdlc = _tree(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local", "sigma.allowRepositoryShellCommands", "true"],
                   check=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_one():\n    assert True\n")
    out = tmp_path / "out.txt"
    out.write_text(pytest_output(A))
    config = {"verify": {"command": "cat out.txt; exit 1", "known_flakes": entries if entries is not None
                         else [entry()]}, "work": {"enabled": False}}
    (sdlc / "config.json").write_text(json.dumps(config))
    (sdlc / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    goal = sdlc / "goals" / "0001.md"
    goal.write_text("---\nid: 0001\nstatus: pending\n---\nx\n")
    work = _mod("work")
    if base:
        work._save(str(sdlc), str(goal), {"worktree": str(tmp_path), "remote": "origin", "base": base,
                                          "branch": "sdlc/1"})
    return str(sdlc), str(goal)


def test_end_to_end_verify_records_passed_with_known_flakes(tmp_path, capsys):
    sdlc, goal = _solo_project(tmp_path)
    assert _loop().verify_goal(sdlc, goal) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASSED-WITH-KNOWN-FLAKES") and "VERIFIED" not in out
    ev = json.loads((pathlib.Path(sdlc) / "state" / "evidence" / "0001.json").read_text()) if (
        pathlib.Path(sdlc) / "state" / "evidence" / "0001.json").exists() else json.loads(
        _mod("state").evidence_path(sdlc, goal).read_text())
    assert ev["verify_state"] == "passed-with-known-flakes" and ev["exit"] != 0
    assert ev["known_flakes"]["ids"] == [A] and ev["known_flakes"]["solo"][A]["exit"] == 0
    assert "passed" in ev["known_flakes"]["solo"][A]["output"]


def test_end_to_end_without_an_entry_the_verify_is_red_as_before(tmp_path, capsys):
    sdlc, goal = _solo_project(tmp_path, entries=[])
    assert _loop().verify_goal(sdlc, goal) == 1
    ev = json.loads(_mod("state").evidence_path(sdlc, goal).read_text())
    assert ev["verify_state"] == "fail" and ev["known_flakes"] is None


def test_end_to_end_without_a_recorded_base_the_verify_stays_red(tmp_path, capsys):
    sdlc, goal = _solo_project(tmp_path, base=None)
    assert _loop().verify_goal(sdlc, goal) == 1
    assert "not applied" in capsys.readouterr().err


def test_end_to_end_a_solo_rerun_that_really_fails_stays_red(tmp_path, capsys):
    sdlc, goal = _solo_project(tmp_path)
    (tmp_path / "tests" / "test_a.py").write_text("def test_one():\n    assert False\n")
    assert _loop().verify_goal(sdlc, goal) == 1
    assert "solo re-run" in capsys.readouterr().err


# ---- the gates read it: only for covered bases ---------------------------------------------------------------

def _evidence(**over):
    ev = {"exit": 1, "verify_state": "passed-with-known-flakes",
          "known_flakes": {"accepted": True, "base": "feature/x", "ids": [A], "matched": {A: "tests/test_a.py::*"},
                           "solo": {A: {"status": "ok", "exit": 0, "output": "1 passed"}},
                           "entries": [entry()], "summary": ["FAILED %s - x" % A]}}
    ev.update(over)
    return ev


def refusal(ev, config=None, base="feature/x", today=TODAY):
    return kf.evidence_refusal(ev, config if config is not None else cfg(entry()), base, today)


def test_gate_accepts_valid_evidence_for_its_base():
    assert refusal(_evidence()) is None


def test_gate_refuses_a_different_base():
    assert "feature/x" in refusal(_evidence(), base="release/1")


def test_gate_refuses_when_no_base_is_given():
    assert "needs the goal's base" in refusal(_evidence(), base=None)


def test_gate_refuses_once_the_entry_has_expired():
    assert "expired" in refusal(_evidence(), config=cfg(entry(expires="2026-11-13")))


def test_gate_refuses_once_the_entry_is_removed_or_the_setting_unreadable():
    assert refusal(_evidence(), config={})
    assert "cannot be read" in refusal(_evidence(), config={"verify": {"known_flakes": "x"}})


def test_gate_refuses_the_integration_branch_unless_an_entry_names_it():
    ev = _evidence()
    ev["known_flakes"]["base"] = "trunk"
    assert refusal(ev, config=cfg(entry(bases=["*"]), base="trunk"), base="trunk")
    assert refusal(ev, config=cfg(entry(bases=["trunk"]), base="trunk"), base="trunk") is None


def test_gate_refuses_evidence_without_a_passing_solo_rerun():
    ev = _evidence()
    ev["known_flakes"]["solo"][A]["exit"] = 1
    assert refusal(ev)


def test_gate_refuses_evidence_whose_matches_do_not_cover_every_id():
    ev = _evidence()
    ev["known_flakes"]["ids"] = [A, B]
    ev["known_flakes"]["solo"][B] = {"status": "ok", "exit": 0, "output": ""}
    assert "every failing test" in refusal(ev)


def test_gate_refuses_a_plain_failure():
    assert "FAILED" in refusal({"exit": 1, "verify_state": "fail"})


def _state_project(tmp_path, ev, config):
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps(config))
    st = _mod("state")
    path = st.evidence_path(str(sdlc), "0001")
    path.parent.mkdir(parents=True, exist_ok=True)
    ev = dict(ev, at=10 ** 12, acceptance_sha256=st.acceptance_module().digest(str(sdlc), "0001"))
    path.write_text(json.dumps(ev))
    return st, str(sdlc)


def test_state_done_refusal_takes_known_flake_evidence_only_with_a_covered_base(tmp_path):
    st, sdlc = _state_project(tmp_path, _evidence(), cfg(entry()))
    assert st.done_refusal(sdlc, "0001", base="feature/x") is None
    assert st.done_refusal(sdlc, "0001", base="main")
    assert st.done_refusal(sdlc, "0001")                     # no base named: refused


def test_state_done_refusal_still_refuses_plain_red_evidence(tmp_path):
    st, sdlc = _state_project(tmp_path, {"exit": 1, "verify_state": "fail"}, cfg(entry()))
    assert "FAILED" in st.done_refusal(sdlc, "0001", base="feature/x")


# ---- PR body --------------------------------------------------------------------------------------------------

def test_pr_section_carries_summary_ids_and_solo_output():
    text = kf.pr_section(_evidence())
    assert "## Verify passed with known flakes" in text
    assert "FAILED " + A in text and "1 passed" in text and "https://example.test/approval" in text


def test_pr_section_is_empty_for_ordinary_evidence():
    assert kf.pr_section({"exit": 0, "verify_state": "pass"}) == ""


def test_work_replaces_an_existing_section_instead_of_stacking_it():
    work = _mod("work")
    once = work._known_flakes_body("body\n", kf.pr_section(_evidence()))
    twice = work._known_flakes_body(once + "\n## Other\n\nkept\n", kf.pr_section(_evidence()))
    assert twice.count("## Verify passed with known flakes") == 1 and "kept" in twice
    assert work._known_flakes_body(once, "").count("known flakes") == 0


def test_work_section_is_written_only_for_valid_evidence_on_the_covered_base(tmp_path):
    st, sdlc = _state_project(tmp_path, _evidence(), cfg(entry()))
    work = _mod("work")
    assert "known flakes" in work._known_flakes_section(sdlc, "0001", "feature/x")
    assert work._known_flakes_section(sdlc, "0001", "main") == ""


# ---- the post-PR reviewer --------------------------------------------------------------------------------------

def test_review_gate_blocks_an_approval_that_does_not_judge_the_exception(tmp_path):
    st, sdlc = _state_project(tmp_path, _evidence(), cfg(entry()))
    work = _mod("work")
    assert "did not judge" in work._known_flakes_review_refusal(sdlc, "0001", "looks fine")
    assert "rejected" in work._known_flakes_review_refusal(sdlc, "0001", "KNOWN-FLAKES: reject - touches test_a")
    assert work._known_flakes_review_refusal(sdlc, "0001", "KNOWN-FLAKES: accept - docs only") is None


def test_review_gate_ignores_goals_without_known_flake_evidence(tmp_path):
    st, sdlc = _state_project(tmp_path, {"exit": 0, "verify_state": "pass"}, cfg(entry()))
    assert _mod("work")._known_flakes_review_refusal(sdlc, "0001", "looks fine") is None


def test_review_brief_ignores_a_refused_attempt(tmp_path):
    st, sdlc = _state_project(tmp_path, _evidence(verify_state="fail"), cfg(entry()))
    assert _mod("review_context")._known_flakes_facts(sdlc, "0001") == ""


def test_review_brief_names_the_matched_entries(tmp_path):
    st, sdlc = _state_project(tmp_path, _evidence(), cfg(entry()))
    text = _mod("review_context")._known_flakes_facts(sdlc, "0001")
    assert A in text and "tracking #1" in text and "approved by the lead" in text


# ---- doctor ----------------------------------------------------------------------------------------------------

def _rows(config, today=TODAY):
    return td._doc()._known_flakes_rows(config, today)


def test_doctor_lists_active_entries_with_their_expiry():
    rows = _rows(cfg(entry()))
    assert len(rows) == 1 and rows[0]["ok"] and "until 2026-12-31" in rows[0]["name"]


def test_doctor_warns_about_an_expired_entry():
    rows = _rows(cfg(entry(expires="2026-11-01")))
    assert rows[0]["ok"] is False and "EXPIRED" in rows[0]["name"] and rows[0]["fix"]


def test_doctor_flags_an_unreadable_list_and_is_silent_without_the_setting():
    assert _rows({"verify": {"known_flakes": "x"}})[0]["ok"] is False
    assert _rows({}) == []

"""`work.py add-slice` through the REAL `acceptance.py extend` (#2222), no double (#2224).

The doubles in test_add_slice_red / _guard defined `extend(sdlc_dir, goal, lines)`, a signature the shipped verb does
not have (`extend(sdlc_dir, goal, slice_ref, reason, criteria_list, ...)`), so a green double hid a verb that refused
every real call. This file is the end-to-end control: real CLI, real acceptance.py, a real record.

Run: python3 -m pytest tests/test_add_slice_extend_real.py
(generic form: python -m pytest tests/test_add_slice_extend_real.py)

Measured by this file: the CLI exits 0, the record's original `## Done when` bytes are unchanged, one
`(added: ...; reason: ...)` line per criterion line lands under `## Added criteria`, and a rerun adds nothing.
Not measured here: the GitHub extend comment (a local acceptance record has no issue source in this fixture).
"""
import importlib.util
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_add_slice_red import S, cli, count, proj  # noqa: E402,F401  (fixtures and the CLI driver)

BODY = "## Done when\n- [ ] First works.\n- [ ] Second refuses bad input.\n- [ ] Third recovers.\n"
RECORD = None


class _Source:
    def fetch_title_body(self, goal):
        return {"title": "Goal", "body": BODY}


def _with_real_record(p):
    """A record made by the real `acceptance.record` (it carries the metadata `extend` refuses to run without)."""
    global RECORD
    spec = importlib.util.spec_from_file_location("acceptance_real", S / "acceptance.py")
    acc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acc)
    record = acc.record(p.sdlc, "2224", source=_Source(), config={})
    RECORD = record.read_text()
    return record


def test_real_extend_appends_added_criteria_and_leaves_the_original_block_untouched(proj):
    record = _with_real_record(proj)
    r = cli(proj)                                           # mode real: real work.py, real acceptance.py
    assert r.returncode == 0, r.stderr
    text = record.read_text()
    assert text.startswith(RECORD), "the original `## Done when` block must stay byte-identical"
    assert "## Added criteria" in text
    added = [x for x in text.splitlines() if "(added:" in x]
    assert len(added) >= 1 and all("reason:" in x for x in added), text
    assert count(proj) == 1


def test_real_extend_rerun_is_idempotent(proj):
    record = _with_real_record(proj)
    assert cli(proj).returncode == 0
    once = record.read_text()
    r = cli(proj)
    assert r.returncode == 0 and "already counted" in r.stdout
    assert record.read_text() == once and count(proj) == 1


# ---- #2224 review SEND-BACK at 85660ea: each clause below goes red when only it is broken -----------------------

def _added(record):
    return [x for x in record.read_text().splitlines() if "(added:" in x]


def test_only_the_done_when_bullets_become_criteria(proj):
    record = _with_real_record(proj)
    proj.crit.write_text("### Task\n- a stray task bullet\n\n### Done when\n- the thing is done\n")
    assert cli(proj).returncode == 0
    added = _added(record)
    assert len(added) == 1 and "the thing is done" in added[0] and "stray" not in record.read_text(), added


def test_a_criteria_file_with_no_bullet_is_refused_before_anything_is_written(proj):
    record = _with_real_record(proj)
    proj.crit.write_text("### Task\nDo the thing, prose only.\n")
    r = cli(proj)
    assert r.returncode == 2 and "no bullet" in r.stderr, r.stderr
    assert proj.main_plan.read_text().count("## Added slice") == 0 and count(proj) == 0
    assert _added(record) == []


def test_an_extend_that_cannot_succeed_is_refused_before_the_plan_is_written(proj):
    record = _with_real_record(proj)
    proj.crit.write_text("### Done when\n- " + "x" * 1100 + "\n")                  # over extend's 1024-character limit
    r = cli(proj)
    assert r.returncode == 4 and "acceptance.extend" in r.stderr, r.stderr
    assert "## Added slice" not in proj.main_plan.read_text() and count(proj) == 0
    assert _added(record) == []


def test_a_resume_after_a_crash_before_the_count_does_not_duplicate_the_criteria(proj):
    record = _with_real_record(proj)
    assert cli(proj).returncode == 0
    once = _added(record)
    (proj.sdlc / "state" / "followups" / "2224.json").unlink()                    # the crash before the count landed
    r = cli(proj)
    assert r.returncode == 0, r.stderr
    assert _added(record) == once and count(proj) == 1


def test_any_exception_from_extend_is_a_refusal_not_a_traceback(proj):
    _with_real_record(proj)
    (proj.doubles / "boom.py").write_text(
        "import pathlib\n"
        "def path(sdlc_dir, goal): return pathlib.Path(sdlc_dir) / 'acceptance' / (str(goal) + '.md')\n"
        "def extend(*a, **k): raise RuntimeError('gh exploded')\n")
    r = cli(proj, mode="boom")
    assert r.returncode == 4 and "gh exploded" in r.stderr and "Traceback" not in r.stderr, r.stderr


def test_a_declining_extend_is_named_as_a_decline_and_leaves_the_plan_written(proj):
    _with_real_record(proj)
    (proj.doubles / "decline.py").write_text(
        "import pathlib\n"
        "def path(sdlc_dir, goal): return pathlib.Path(sdlc_dir) / 'acceptance' / (str(goal) + '.md')\n"
        "def extend(*a, **k): raise ValueError('the issue declined the extend comment')\n")
    r = cli(proj, mode="decline")
    assert r.returncode == 4 and "declined (the issue declined" in r.stderr and "left as written" in r.stderr, r.stderr
    assert "## Added slice 1" in proj.main_plan.read_text() and count(proj) == 0


def test_more_added_criteria_than_the_cap_are_refused_before_the_plan_is_written(proj):
    record = _with_real_record(proj)
    proj.crit.write_text("### Done when\n" + "".join("- criterion number %d\n" % i for i in range(11)))
    r = cli(proj)
    assert r.returncode == 4 and "would decline" in r.stderr, r.stderr
    assert "## Added slice" not in proj.main_plan.read_text() and _added(record) == []


def test_a_resume_with_a_recased_title_does_not_append_the_criteria_again(proj):
    record = _with_real_record(proj)
    assert cli(proj).returncode == 0
    once = _added(record)
    (proj.sdlc / "state" / "followups" / "2224.json").unlink()                    # crash before the count
    r = cli(proj, title="FIX THE THING")                                          # same slice id, other spelling
    assert r.returncode == 0, r.stderr
    assert _added(record) == once and count(proj) == 1


def test_a_resume_with_an_edited_criteria_file_does_not_join_new_bullets_to_the_old_slice(proj):
    record = _with_real_record(proj)
    assert cli(proj).returncode == 0
    once = _added(record)
    (proj.sdlc / "state" / "followups" / "2224.json").unlink()
    proj.crit.write_text("### Done when\n- something else entirely\n")
    r = cli(proj)
    assert r.returncode == 0 and "already in the record" in r.stdout, (r.stdout, r.stderr)
    assert _added(record) == once


def test_the_same_bullet_under_two_different_slices_is_extended_twice(proj):
    record = _with_real_record(proj)
    assert cli(proj, title="Slice A").returncode == 0
    assert cli(proj, title="Slice B").returncode == 0
    added = _added(record)
    assert len(added) == 2 and "slice 1" in added[0] and "slice 2" in added[1], added
    assert count(proj) == 2


def test_a_reused_slice_number_with_a_different_title_is_still_extended(proj):
    """Slice 1 "Foo" completes; its heading is then removed from the plan; "Bar" becomes slice 1 again. A row under the
    same slice NUMBER but another reason must not make the verb skip Bar's criteria (the reason is the identity)."""
    record = _with_real_record(proj)
    assert cli(proj, title="Foo").returncode == 0
    for plan in (proj.main_plan, proj.branch_plan):
        text = plan.read_text()
        plan.write_text(text[:text.index("## Added slice 1")].rstrip("\n") + "\n")
    (proj.sdlc / "state" / "followups" / "2224.json").unlink()
    r = cli(proj, title="Bar")
    assert r.returncode == 0, r.stderr
    added = _added(record)
    assert len(added) == 2 and "reason: Foo" in added[0] and "reason: Bar" in added[1], added

"""features.py -- reading the unit of work an issue declares (#1465, epic #1464, story #1427).

An issue declares its unit twice on purpose: a machine-readable `feature:<name>` LABEL (what every
server-side query filters on) and a human-readable two-line BODY MARKER (what a person reads). This
suite pins the five verdicts that comparison can produce, and -- more importantly -- pins the
parser's edges, because "strict enough to reject a typo, loose enough to survive a human editing
around it" is exactly the kind of requirement that rots silently: every loosening here has to stay
unable to fire on ordinary prose, and every tightening has to stay unable to swallow a real
declaration.

WRITTEN AGAINST A MUTATION RUN, NOT JUST FOR COVERAGE. The first version of this file reached 100%
line coverage on the module and still let SEVEN mutants live -- every one of them at a LOOSENING
edge (trailing whitespace, space-before-colon, the non-string label guard, the lone-CR arm), which
is precisely the half of "strict enough / loose enough" that coverage cannot see, because the line
executes either way and only the accepted LANGUAGE changes. Each tolerance the module grants now has
a test that fails when that tolerance alone is removed.
"""
import ast
import importlib.util
import pathlib
import types

import pytest

P = (pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
     / "features.py")


def _mod():
    spec = importlib.util.spec_from_file_location("features", P)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mod_with(old, new):
    """The module rebuilt with one source substitution applied -- the only way to ask "does the
    parser actually FOLLOW this constant?" rather than "does the constant's text appear somewhere in
    the pattern?", which is true of the defect too."""
    src = P.read_text(encoding="utf-8")
    assert old in src, old
    namespace = {"__name__": "features_variant", "__file__": str(P)}
    exec(compile(src.replace(old, new, 1), str(P), "exec"), namespace)      # noqa: S102 - test-only
    return types.SimpleNamespace(**namespace)


def _issue(body=None, labels=None):
    """A gh-shaped issue payload. `labels` defaults to the dict shape gh actually returns."""
    return {"body": body, "labels": [{"name": n} for n in (labels or [])]}


# --------------------------------------------------------------------------- the five verdicts

def test_the_verdict_vocabulary_is_a_closed_set_of_five():
    """The design names exactly five states. A sixth would mean a caller's match is incomplete."""
    f = _mod()
    assert set(f.VERDICTS) == {"agree", "body_only", "label_only", "conflict", "none"}
    assert (f.AGREE, f.BODY_ONLY, f.LABEL_ONLY, f.CONFLICT, f.NONE) == (
        "agree", "body_only", "label_only", "conflict", "none")


def test_agree_when_body_and_label_name_the_same_unit():
    f = _mod()
    v = f.read(_issue(body="Feature: voice-interview\n", labels=["feature:voice-interview"]))
    assert v.state == f.AGREE
    assert v.unit == "voice-interview"
    assert v.body == "voice-interview" and v.label == "voice-interview"


def test_body_only_when_the_label_is_absent():
    f = _mod()
    v = f.read(_issue(body="Feature: voice-interview\n", labels=["enhancement", "priority:P1"]))
    assert v.state == f.BODY_ONLY
    assert v.unit == "voice-interview"
    assert v.label is None


def test_label_only_when_the_body_has_no_marker():
    f = _mod()
    v = f.read(_issue(body="Just some prose about the work.\n", labels=["feature:voice-interview"]))
    assert v.state == f.LABEL_ONLY
    assert v.unit == "voice-interview"
    assert v.body is None


def test_conflict_reports_both_and_the_body_wins():
    """The body is what a human wrote; the label is what a machine attached. On a disagreement the
    human's text is the one that gets to be right -- and BOTH sides stay readable, because the
    consumer of a conflict has to be able to say what it is choosing between."""
    f = _mod()
    v = f.read(_issue(body="Feature: voice-interview\n", labels=["feature:billing"]))
    assert v.state == f.CONFLICT
    assert v.unit == "voice-interview"
    assert (v.body, v.label) == ("voice-interview", "billing")


def test_none_when_neither_side_declares_anything():
    f = _mod()
    v = f.read(_issue(body="No declaration here.\n", labels=["enhancement"]))
    assert v == (f.NONE, None, None, None)


def test_an_empty_payload_is_none_not_a_crash():
    """A goal read from a source that carries no body/labels at all must not explode -- `none` is
    the honest answer, and it is the one that keeps today's single-base behaviour."""
    f = _mod()
    for payload in ({}, {"body": None, "labels": None}, {"body": "", "labels": []}):
        assert f.read(payload).state == f.NONE


def test_a_payload_that_is_not_a_mapping_at_all_is_none_not_an_attributeerror():
    """The module is otherwise scrupulous about degrading rather than raising on someone else's
    payload; `read` used to be the one place that broke that promise on shape alone."""
    f = _mod()
    for payload in (None, "a string", ["labels"], 7, object()):
        assert f.read(payload) == (f.NONE, None, None, None)


# --------------------------------------------------------------------------- the body parser

def test_a_marker_surrounded_by_prose_still_parses():
    """Done-when clause: a human writes around the marker, and it survives."""
    f = _mod()
    body = (
        "## Context\n\n"
        "We keep re-deriving which branch this belongs on, so declare it up front.\n\n"
        "Feature: voice-interview\n"
        "Branch: feature/voice-interview\n\n"
        "## Acceptance\n\n"
        "- the loop bases this goal on the feature branch\n"
    )
    assert f.parse_body(body) == "voice-interview"


def test_the_key_is_case_insensitive():
    f = _mod()
    for key in ("Feature", "feature", "FEATURE", "FeAtUrE"):
        assert f.parse_body("%s: voice-interview\n" % key) == "voice-interview"


def test_a_typo_in_the_key_is_not_a_marker():
    """Done-when clause: `Featue:` is rejected. Rejected means "not a declaration" -- the goal falls
    back to the configured base, which is exactly today's behaviour -- never a guess at what the
    human meant."""
    f = _mod()
    for line in ("Featue: voice-interview", "Feture: voice-interview", "Features: voice-interview",
                 "Feat: voice-interview"):
        assert f.parse_body(line + "\n") is None, line


def test_a_wrong_separator_is_not_a_marker():
    """Done-when clause: `feature=` is rejected."""
    f = _mod()
    for line in ("feature=voice-interview", "feature = voice-interview",
                 "feature - voice-interview", "feature voice-interview"):
        assert f.parse_body(line + "\n") is None, line


def test_the_marker_must_be_anchored_at_line_start():
    """Mid-sentence prose that happens to contain the word must never be read as a declaration."""
    f = _mod()
    for body in ("this is the Feature: voice-interview one\n",
                 "see the parent issue -- Feature: voice-interview\n",
                 "> Feature: voice-interview\n",
                 "- Feature: voice-interview\n",
                 "**Feature:** voice-interview\n"):
        assert f.parse_body(body) is None, body


def test_a_space_before_the_colon_is_tolerated():
    """A LOOSENING with no test in either direction is a rule nobody can tell is intended. It is:
    `Feature : x` is a human's spacing, not a different key -- unlike `feature=`, which is a
    different separator entirely and stays rejected."""
    f = _mod()
    assert f.parse_body("Feature : voice-interview\n") == "voice-interview"
    assert f.parse_body("Feature\t: voice-interview\n") == "voice-interview"


def test_trailing_whitespace_after_the_value_is_tolerated():
    """The single most common "human edited around it" case there is -- a hand-edited markdown line
    that picked up trailing spaces -- and it was the one tolerance with no test at all: the suite
    stayed green with it removed while real bodies would silently stop declaring."""
    f = _mod()
    assert f.parse_body("Feature: voice-interview   \n") == "voice-interview"
    assert f.parse_body("Feature: voice-interview\t\n") == "voice-interview"


# --------------------------------------------------------------------------- rule 1 / rule 5:
# what a reader of the issue cannot see as text is not parsed

def test_indentation_up_to_three_spaces_still_parses():
    """CommonMark's own boundary: three spaces is still a paragraph, so a human nesting the marker
    under a heading has still written a marker."""
    f = _mod()
    for indent in ("", " ", "  ", "   "):
        assert f.parse_body("%sFeature: voice-interview\n" % indent) == "voice-interview", repr(indent)


def test_four_spaces_or_a_tab_is_an_indented_code_block_and_declares_nothing():
    """THIS TEST WAS INVERTED. Its predecessor asserted that a 4-space and a tab-indented marker
    parse -- it pinned the bug. Four spaces (and a leading tab, which is four columns) START AN
    INDENTED CODE BLOCK: that is the same "this is an example, not a declaration" construct as a
    fence, and it is how the majority of docs written before fences existed show sample text."""
    f = _mod()
    for indent in ("    ", "     ", "\t", "\t\t", "        "):
        assert f.parse_body("prose\n\n%sFeature: voice-interview\n" % indent) is None, repr(indent)


def test_the_modules_own_docstring_declares_nothing(capsys):
    """The sharpest possible regression test, and it FAILED before this fix: feeding the module's
    own docstring -- which teaches the format with an indented example -- to the module's own parser
    returned 'voice-interview'. Any doc that teaches the marker (the epic's adopter doc is coming)
    would have declared a unit that has no branch."""
    f = _mod()
    assert f.parse_body(f.__doc__) is None
    assert "NOT read as a declaration" in capsys.readouterr().err


def test_a_fenced_marker_is_an_example_not_a_declaration(capsys):
    """Verified against this epic's own issues, not hypothesised: #1465 and #1464 both DISPLAY the
    marker inside a fenced block while declaring nothing. Parsing fenced content would make every
    issue that documents the format declare a unit it has no branch for -- and `goal_size.py`
    already strips fences before any structural read for exactly this reason."""
    f = _mod()
    body = (
        "An issue declares its unit twice:\n\n"
        "```\n"
        "Feature: voice-interview\n"
        "Branch: feature/voice-interview\n"
        "```\n\n"
        "Build read(issue) -> verdict.\n"
    )
    assert f.parse_body(body) is None
    assert f.read(_issue(body=body)).state == f.NONE
    capsys.readouterr()


def test_a_fenced_example_does_not_shadow_a_real_marker():
    """The fence skip must not swallow a genuine declaration that sits outside it."""
    f = _mod()
    body = "Feature: billing\n\n```\nFeature: voice-interview\n```\n"
    assert f.parse_body(body) == "billing"


def test_a_tilde_fence_inside_a_backtick_fence_does_not_close_it():
    """CommonMark: a fence closes only on its own delimiter character. Mis-tracking that closes the
    fence early and the lines after it get read as real structure -- the failure direction that
    invents declarations."""
    f = _mod()
    body = "```\n~~~\nFeature: voice-interview\n~~~\n```\n"
    assert f.parse_body(body) is None


def test_a_longer_fence_is_not_closed_by_a_shorter_one():
    """CommonMark: a closing fence must be AT LEAST as long as the opening one. Wrapping a
    three-backtick example in a four-backtick fence is THE standard way to document fenced syntax,
    so this is the shape every doc teaching this marker will use -- and without the length rule the
    inner fence closed the outer one and the example declared a unit. `goal_size.py` discloses that
    it does not model this; here it had to be modelled."""
    f = _mod()
    body = "````markdown\n```\nFeature: voice-interview\n```\n````\n"
    assert f.parse_body(body) is None


def test_a_closing_fence_may_not_carry_an_info_string():
    """Also CommonMark, and the same failure direction: treating an info-string line as a close
    re-opens everything after it to parsing."""
    f = _mod()
    assert f.parse_body("```\n```python\nFeature: voice-interview\n```\n") is None


def test_a_fence_opened_inside_a_list_item_still_hides_its_content():
    """`- ``` ` is a fence; a fence matcher anchored only at the line's own indent does not see it,
    and the example inside then declares."""
    f = _mod()
    assert f.parse_body("- ```\n  Feature: voice-interview\n  ```\n") is None
    assert f.parse_body("> ```\n> Feature: voice-interview\n> ```\n") is None
    assert f.parse_body("1. ```\n   Feature: voice-interview\n   ```\n") is None


def test_an_unterminated_fence_blanks_to_end_of_body():
    """Conservative on a malformed body: fail towards reading no declaration, never towards
    inventing one."""
    f = _mod()
    assert f.parse_body("```\nFeature: voice-interview\n") is None


def test_a_marker_inside_an_html_comment_declares_nothing():
    """An HTML comment is precisely what a reader of the issue does NOT see, which contradicts the
    entire reason the body half exists -- and GitHub issue TEMPLATES ship their instructions inside
    one as a matter of course, so this is the likeliest way a fleet-wide false declaration would
    have entered."""
    f = _mod()
    assert f.parse_body("<!--\nFeature: voice-interview\n-->\n\nreal body\n") is None
    assert f.parse_body("<!-- Feature: voice-interview -->\n") is None
    assert f.parse_body("<!--\nFeature: voice-interview\n") is None            # never closed


def test_an_html_comment_does_not_swallow_a_real_marker_after_it():
    f = _mod()
    assert f.parse_body("<!-- template instructions -->\nFeature: billing\n") == "billing"


def test_a_commented_out_fence_cannot_swallow_the_rest_of_the_body():
    """Single-pass proof, direction 1 of 2: the COMMENT opens first, so the ``` inside it is inert
    content and cannot open a fence that would eat the real declaration below.

    THIS DOCSTRING WAS WRONG. It used to read "fences are stripped BEFORE comments, so a fence shown
    inside a comment is already blank by the time comments are handled" -- describing the two-pass
    design that `_visible`'s own docstring spends a paragraph arguing against, and which this test
    exists to disprove. The one test that proved the single pass told the next reader the code was
    two-pass."""
    f = _mod()
    assert f.parse_body("<!--\n```\n-->\nFeature: billing\n") == "billing"


def test_a_commented_marker_inside_a_fence_cannot_swallow_the_rest_of_the_body():
    """Single-pass proof, direction 2 of 2, and the one the suite was missing. The FENCE opens
    first, so the `<!--` inside it is inert content rather than an unterminated comment that blanks
    to EOF. A correctly-built comments-first two-pass -- with the comment state persisting across
    lines, not reset per line -- returns None here while passing every other test in this file, so
    without this case exactly half of what the single pass is for is unpinned."""
    f = _mod()
    assert f.parse_body("```\n<!--\n```\nFeature: billing\n") == "billing"
    assert f.parse_body("```\n<!-- Feature: voice-interview\n```\nFeature: billing\n") == "billing"


def test_a_hidden_declaration_is_reported_on_stderr(capsys):
    """The inverse risk of rule 5, made discoverable. A human who fences a marker they MEANT gets
    `None` -- byte-identical to declaring nothing, the silent fallback state. One stderr line is
    what separates "you wrote it wrong" from "nothing happened and nobody said why"."""
    f = _mod()
    assert f.parse_body("```\nFeature: voice-interview\n```\n") is None
    err = capsys.readouterr().err
    assert "voice-interview" in err and "NOT read as a declaration" in err


def test_no_note_when_the_body_genuinely_declares_nothing(capsys):
    """The note must fire on a HIDDEN declaration, never on an ordinary body -- otherwise a sweep
    over a backlog prints a line per issue and the signal is worth nothing."""
    f = _mod()
    assert f.parse_body("## Context\n\nnothing to declare here.\n") is None
    assert f.parse_body("Feature: not a unit name because it is prose\n") is None
    assert capsys.readouterr().err == ""


def test_a_broken_stderr_cannot_break_a_pick(monkeypatch):
    """The note is a diagnostic, and the module's own rule is that a diagnostic must never be the
    thing that breaks a pick -- the same promise as the non-string label guard, which shipped
    untested and would have raised on the next malformed payload. A closed or unwritable stderr (a
    detached daemon, a closed pipe) must cost the caller nothing."""
    f = _mod()

    class Exploding:
        def write(self, _):
            raise ValueError("stderr is gone")

    monkeypatch.setattr(f.sys, "stderr", Exploding())
    assert f.parse_body("```\nFeature: voice-interview\n```\n") is None


def test_no_note_when_a_real_marker_sits_beside_a_fenced_example(capsys):
    f = _mod()
    assert f.parse_body("Feature: billing\n\n```\nFeature: voice-interview\n```\n") == "billing"
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------- rule 3 / rule 4

def test_one_value_per_key_a_prose_tail_is_not_a_declaration():
    """"One value per key" is what keeps this parser off ordinary prose: a sentence beginning
    "Feature: we should add voice interviews" is a paragraph, not a declaration, and treating it as
    one would silently base the goal on a branch nobody named."""
    f = _mod()
    for body in ("Feature: voice interview\n",
                 "Feature: we should add voice interviews to the app\n",
                 "Feature: voice-interview (see #1427)\n"):
        assert f.parse_body(body) is None, body


def test_a_value_git_would_refuse_as_a_branch_segment_is_not_a_marker():
    """MEASURED with `git check-ref-format`, all four REJECTED as `refs/heads/feature/<name>`:

        TBD.  x.        -- a component may not end with '.'
        v1..2           -- a component may not contain '..'
        voice.lock      -- a component may not end with '.lock'

    The module's own rule 4 claimed the value was already "one path-safe segment", and it was not:
    `Feature: TBD.` is ordinary hand-written prose, it declared a unit, and L1 would then have tried
    to cut a worktree from a branch name git refuses. This is the one direction the module says it
    never fails in -- inventing a declaration."""
    f = _mod()
    for value in ("TBD.", "x.", "v1..2", "voice.lock", "a..b", "unknown."):
        assert f.parse_body("Feature: %s\n" % value) is None, value


def test_a_value_that_is_not_one_segment_is_not_a_marker():
    f = _mod()
    for value in ("voice/interview", "-voice", ".voice", "voice~interview", "voice:interview",
                  "#1427", "N/A"):
        assert f.parse_body("Feature: %s\n" % value) is None, value


def test_legal_unit_names_parse():
    """Including the three that LOOK like the rejected ones and are not: git's `.lock` rule is
    case-SENSITIVE (`voice.LOCK` is a valid ref) and applies to the suffix only (`a.lockfile` is
    fine), so writing it with `re.IGNORECASE` or as a substring test would reject names git
    accepts."""
    f = _mod()
    for value in ("voice-interview", "voice_interview", "voice.interview", "v2", "VoiceInterview",
                  "1427-branching", "9", "voice.LOCK", "a.lockfile", "lock"):
        assert f.parse_body("Feature: %s\n" % value) == value, value


def test_crlf_and_lone_cr_bodies_parse():
    """MEASURED, AND THE MEASUREMENT WAS CORRECTED -- the correction is the point. `GET /issues`
    also returns PULL REQUESTS, and PR bodies (tool- and template-generated far more often) carry
    CRLF at a much higher rate than issue bodies. Counting both, this docstring first claimed
    `cli/cli` 14/100 and `microsoft/vscode` 10/100. Over REAL ISSUE BODIES ONLY -- the input this
    parser actually reads -- it is 0 of 647 here, `cli/cli` 1 of 87, `microsoft/vscode` 1 of 94,
    `python/cpython` 0 of 60: an order of magnitude rarer than stated, and still nonzero.
    Kept for the narrow, honest reason: cheap defensive code for adopters, where a marker in such a
    body would otherwise never match the end-of-line anchor. The lone-CR arm is the older
    classic-Mac ending the same normalisation implies, and it had no test at all."""
    f = _mod()
    assert f.parse_body("## Context\r\nFeature: voice-interview\r\n"
                        "Branch: feature/voice-interview\r\n") == "voice-interview"
    assert f.parse_body("## Context\rFeature: voice-interview\r") == "voice-interview"


def test_two_conflicting_feature_lines_raise_rather_than_pick_one():
    """Done-when clause. First-wins would be a silent, unreviewable choice between two things a
    human actually wrote -- the same reason a body/label conflict is reported rather than resolved
    quietly."""
    f = _mod()
    with pytest.raises(f.AmbiguousUnit) as exc:
        f.parse_body("Feature: voice-interview\nsome prose\nFeature: billing\n")
    assert "voice-interview" in str(exc.value) and "billing" in str(exc.value)


def test_two_identical_feature_lines_are_not_a_conflict():
    """Two lines that say the SAME thing are a human repeating themselves (a summary block plus a
    trailer), not a disagreement. There is nothing to choose between, so there is nothing to raise
    about."""
    f = _mod()
    assert f.parse_body("Feature: voice-interview\n...\nFeature: voice-interview\n") == "voice-interview"


def test_two_feature_lines_differing_only_in_case_are_not_a_conflict():
    """GitHub label names are case-insensitively unique, so `feature:Voice` and `feature:voice`
    cannot both exist on a repo -- treating the two spellings as rival units would manufacture a
    conflict that the label side is structurally incapable of expressing. The first spelling wins,
    because it is the one the human wrote first."""
    f = _mod()
    assert f.parse_body("Feature: Voice-Interview\nFeature: voice-interview\n") == "Voice-Interview"


# --------------------------------------------------------------------------- rule 7: the Branch line

def test_a_branch_line_that_corroborates_the_unit_parses():
    f = _mod()
    body = "Feature: voice-interview\nBranch: feature/voice-interview\n"
    assert f.parse_body(body) == "voice-interview"
    assert f.parse_body("Feature: voice-interview\nBranch: FEATURE/Voice-Interview\n") == "voice-interview"


def test_a_branch_line_that_contradicts_the_unit_raises():
    """The marker is specified as TWO lines, so reading one of them is half the spec: this body used
    to parse silently, leaving the human-readable half -- the entire justification for the body
    marker existing -- telling the next human something false."""
    f = _mod()
    with pytest.raises(f.AmbiguousUnit) as exc:
        f.parse_body("Feature: voice-interview\nBranch: feature/billing\n")
    assert "voice-interview" in str(exc.value) and "feature/billing" in str(exc.value)
    for branch in ("main", "feature/voice_interview", "sdlc/1465", "voice-interview"):
        with pytest.raises(f.AmbiguousUnit):
            f.parse_body("Feature: voice-interview\nBranch: %s\n" % branch)


def test_a_branch_naming_a_sub_branch_of_the_unit_agrees():
    """`feature/<name>/<sub>` is the model's own shape for a dependency discovered mid-flight, so it
    corroborates the unit rather than contradicting it."""
    f = _mod()
    assert f.parse_body("Feature: voice-interview\nBranch: feature/voice-interview/retry\n") \
        == "voice-interview"


def test_several_agreeing_branch_lines_are_not_rivals():
    """A DEFECT THIS FIX INTRODUCED, now pinned. `_single` ran over the `Branch:` lines before
    `_branch_agrees` was ever consulted, so two branch lines were rivals purely by differing -- and
    a body naming both the unit branch and its mid-flight sub-branch, the model's own documented
    shape, raised while contradicting nothing. It also contradicted rule 7 outright: a cross-check
    obliged to be singular is exactly the second source of truth the rule says it is not."""
    f = _mod()
    assert f.parse_body("Feature: v\nBranch: feature/v\nBranch: feature/v/sub\n") == "v"
    assert f.parse_body("Feature: v\nBranch: feature/v/a\nBranch: feature/v/b\n") == "v"
    assert f.parse_body("Feature: v\nBranch: feature/v\nBranch: feature/v\n") == "v"


def test_two_conflicting_branch_lines_raise():
    f = _mod()
    with pytest.raises(f.AmbiguousUnit):
        f.parse_body("Feature: voice-interview\nBranch: feature/voice-interview\n"
                     "Branch: feature/billing\n")


def test_a_branch_line_with_no_feature_line_declares_nothing():
    """The `Feature:` key carries the declaration; `Branch:` is the derived, human-facing echo of
    it. A body carrying only the echo is not a declaration, and inventing one from it would make the
    derived half a second source of truth."""
    f = _mod()
    assert f.parse_body("Branch: feature/voice-interview\n") is None


def test_branch_prose_is_ignored_like_any_other_prose():
    """The same "one value per key" rule protects the second key: a sentence is not a branch line,
    so ordinary prose about branching cannot raise."""
    f = _mod()
    assert f.parse_body("Feature: voice-interview\nBranch: we will decide this later\n") \
        == "voice-interview"
    assert f.parse_body("Feature: voice-interview\nBranching: feature/billing\n") == "voice-interview"


# --------------------------------------------------------------------------- the label side

def test_both_label_payload_shapes_are_read():
    """This repo carries BOTH shapes today: gh's raw JSON gives `[{"name": ...}]` (auto_unpark.py's
    `_label_names`), while the board queue normalises to bare strings (sources.py's `_board_queue`).
    A reader that handles only one is silently blind on half the call sites."""
    f = _mod()
    assert f.parse_labels([{"name": "feature:voice-interview"}, {"name": "bug"}]) == "voice-interview"
    assert f.parse_labels(["feature:voice-interview", "bug"]) == "voice-interview"
    assert f.read({"labels": ["feature:voice-interview"]}).state == f.LABEL_ONLY


def test_a_label_whose_name_is_not_a_string_is_skipped_not_raised():
    """THE guard that implements the module's own "a reader of someone else's payload must not be
    the thing that breaks a pick" -- and it had no test, so removing it (a `TypeError` on the next
    malformed payload) left the suite green."""
    f = _mod()
    assert f.parse_labels([{"name": 7}]) is None
    assert f.parse_labels([{"name": None}, {"name": ["feature:x"]}]) is None
    assert f.parse_labels([{"name": 7}, {"name": "feature:billing"}]) == "billing"


def test_the_label_prefix_is_case_insensitive_and_tolerates_a_space():
    f = _mod()
    assert f.parse_labels(["Feature:voice-interview"]) == "voice-interview"
    assert f.parse_labels(["feature: voice-interview"]) == "voice-interview"


def test_a_label_that_merely_starts_with_the_word_is_not_a_declaration():
    f = _mod()
    for name in ("features:voice", "feature-voice", "featureX:voice", "feature:", "feature:  ",
                 "feature:voice interview", "feature:voice/interview", "feature:TBD."):
        assert f.parse_labels([name]) is None, name


def test_no_labels_at_all_is_none():
    f = _mod()
    assert f.parse_labels(None) is None
    assert f.parse_labels([]) is None
    assert f.parse_labels([None, 7, {"nope": "x"}]) is None


def test_two_distinct_feature_labels_raise_rather_than_pick_one():
    """The same principle as two conflicting body markers, applied to the machine-readable side:
    Sigma attaches at most one, but a human can add a second by hand, and silently picking one
    would base the goal on a branch nobody chose. Not a state the five verdicts can express -- the
    issue is undecidable until a human removes one."""
    f = _mod()
    with pytest.raises(f.AmbiguousUnit):
        f.parse_labels(["feature:voice-interview", "feature:billing"])
    with pytest.raises(f.AmbiguousUnit):
        f.read(_issue(labels=["feature:voice-interview", "feature:billing"]))


def test_two_feature_labels_differing_only_in_case_are_not_a_conflict():
    f = _mod()
    assert f.parse_labels(["feature:Voice", "feature:voice"]) == "Voice"


def test_a_body_label_conflict_that_is_only_a_case_difference_is_agreement():
    """GitHub cannot hold two labels that differ only in case, so the two sides genuinely agree."""
    f = _mod()
    v = f.read(_issue(body="Feature: Voice-Interview\n", labels=["feature:voice-interview"]))
    assert v.state == f.AGREE
    assert v.unit == "Voice-Interview"          # the body's spelling, because the body wins


def test_read_propagates_ambiguous_unit_for_the_caller_to_handle():
    """Pins the contract `read`'s docstring states: `read` is total over payload SHAPE but not over
    payload CONTENT, so a sweep must catch this PER ISSUE rather than letting one hand-edited issue
    take out the whole queue."""
    f = _mod()
    with pytest.raises(f.AmbiguousUnit):
        f.read(_issue(body="Feature: a\nFeature: b\n"))
    with pytest.raises(f.AmbiguousUnit):
        f.read(_issue(body="Feature: a\nBranch: feature/b\n"))


# --------------------------------------------------------------------------- module shape

def test_changing_the_body_key_constant_changes_what_the_parser_reads():
    """`BODY_KEY` was declared "so the stamping half writes what this reads" and then referenced
    NOWHERE -- the regexes hardcoded the literal. A contract constant that constrains nothing is
    worse than none, because the level that writes markers will trust it.

    THIS TEST WAS REWRITTEN. Its predecessor asserted `re.escape(BODY_KEY) in _MARKER_RE.pattern`,
    which is EQUALLY TRUE of the defect it was written to reject -- a hardcoded `"Feature"` literal
    puts the same substring in the same pattern -- so re-introducing the original F3 shape left the
    whole suite green. The only question that discriminates is behavioural: move the constant, and
    does the accepted language move with it?"""
    variant = _mod_with('BODY_KEY = "Feature"', 'BODY_KEY = "Unit"')
    assert variant.parse_body("Unit: voice-interview\n") == "voice-interview"
    assert variant.parse_body("Feature: voice-interview\n") is None
    assert _mod().parse_body("Feature: voice-interview\n") == "voice-interview"   # unpatched


def test_changing_the_branch_key_constant_changes_what_the_parser_reads():
    """The same discrimination for the second key: with the constant moved, the OLD key stops being
    a cross-check (so a contradicting `Branch:` line no longer raises) and the NEW one starts."""
    variant = _mod_with('BRANCH_KEY = "Branch"', 'BRANCH_KEY = "Ref"')
    assert variant.parse_body("Feature: voice\nBranch: feature/billing\n") == "voice"
    with pytest.raises(variant.AmbiguousUnit):
        variant.parse_body("Feature: voice\nRef: feature/billing\n")


def test_a_payload_whose_values_are_the_wrong_type_is_none_not_a_crash():
    """`read` tolerated a bad payload SHAPE but not a bad payload VALUE: `{"body": 123}` raised
    `AttributeError` and `{"labels": 7}` raised `TypeError`. `_label_name` already tolerates one bad
    ELEMENT, so this is the module's own "must not break a pick" promise at the depth one out, not a
    new promise."""
    f = _mod()
    for payload in ({"body": 123}, {"body": b"bytes"}, {"body": ["x"]},
                    {"labels": 7}, {"labels": "feature:x"}, {"labels": 3.5}):
        assert f.read(payload).state == f.NONE, payload
    # A bare string is NOT a collection of labels (iterating it yields characters), but a tuple and
    # a set are: `sources._board_queue` builds `set(it.get("labels") or [])` on this very repo, so
    # narrowing to `list` alone would have gone blind on a shape the codebase actually produces.
    assert f.read({"labels": ("feature:billing",)}).unit == "billing"
    assert f.read({"labels": {"feature:billing"}}).unit == "billing"


def test_the_module_is_zero_dependency():
    """Same contract as its siblings (`owners.py`, `goal_size.py`): stdlib only, so it can be
    `_load()`ed by any script without an install step."""
    allowed = {"re", "collections", "sys", "json", "pathlib"}
    tree = ast.parse(P.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            assert (node.module or "").split(".")[0] in allowed, node.module


def test_read_is_pure_and_does_not_mutate_the_payload():
    """`read` is called on the picker's own issue dict; a reader that edits it would corrupt every
    later consumer of the same payload."""
    f = _mod()
    issue = _issue(body="Feature: voice-interview\n", labels=["feature:voice-interview"])
    before = repr(issue)
    f.read(issue)
    assert repr(issue) == before

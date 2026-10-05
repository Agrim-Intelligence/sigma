"""#260 -- every sentence about the independent reviewer says what the code proves, and no more.

Two pins. (1) The generated `docs/enforcement.md` row for independent review is kind advice, says
the code "cannot prove" the maker did not influence the reviewer, lists what it does NOT observe
and what it DOES bind. (2) No shipped file still carries one of the exact overclaims this goal
removed (a rule stated as a guarantee, a state line that says the reviewer "is" fresh when the code
only asks for one).

Documented gesture, copied from the docs: `python3 -m pytest tests/test_reviewer_independence_claims.py`
after planting a removed phrase in any shipped file goes red; the row test goes red when a clause is
dropped from `EXTERNAL_CONTROLS` in `skills/sigma-doctor/scripts/enforcement_table.py` and the doc is
regenerated with `python3 skills/sigma-doctor/scripts/enforcement_table.py > docs/enforcement.md`.

`test_the_scan_flags_a_planted_overclaim` is a smoke control of the scanner on a temporary tree; it
cannot see the real tree, so the system of record is the real-tree scan above it.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "enforcement.md"

#: What the row must say the code does NOT prove, and what it DOES bind. Each proof clause names a
#: path in `work.py`/`reviewer.py` (see `.sdlc/research/260.md`); the words here are the contract.
NOT_PROVED = (
    "cannot prove the maker did not influence the reviewer",
    "does not observe who called the host's task tool",
    "a maker can record its own approving verdict",
    "session environment variables",
    "from any commenter, not the evidence",
    "does not require an approval at all",
)
PROVED = (
    "brief digest, the PR and the head are bound to one generation",
    "refused if the worktree head or diff moved",
    "written once per generation",
    "current generation and the PR head is unchanged",
)

#: Exact phrases that were removed because they state more than the code proves. Short on purpose:
#: each one is an overclaim whose meaning cannot be recovered by rewording a neighbour.
REMOVED_OVERCLAIMS = (
    "The maker never clears its own PR",
    "maker≠checker guarantee",
    "maker!=checker guarantee",
    "the reviewer is never the author",
    "ON — a fresh, author-blind reviewer per gate",
    "independent review (maker is never the checker)",
    "independent review actually enforced before auto-merge",
    "`sigma-plan-review` runs author-blind",
    "gets it for real only in the loop",
    "true (default) = a fresh author-blind reviewer subagent per gate",
    "The maker is never the checker (`config.review.independent`",
    "The maker is never the checker. Every LLM-judgment review gate",
    "every host reaches an author-blind reviewer by its own route",
)

_SUFFIXES = {".md", ".py", ".tmpl", ".mdc", ".json", ".sh"}
#: History and tests legitimately quote old wording; `.sdlc` is per-goal working state.
_SKIP_PARTS = {".git", ".sdlc", "tests", "node_modules", ".claude"}
_SKIP_FILES = {"CHANGELOG.md"}
#: Recorded drill output is a dated measurement of what the old build printed, not shipped prose.
_SKIP_PREFIXES = ("docs/launch/evidence/",)


def _row():
    lines = [line for line in DOC.read_text(encoding="utf-8").splitlines()
             if line.startswith("| Independent review |")]
    assert len(lines) == 1, lines
    return [cell.strip() for cell in lines[0].strip().strip("|").split(" | ")]


def scan(root, phrases=REMOVED_OVERCLAIMS):
    """-> [(relative path, phrase)] for every shipped file under `root` holding a removed phrase."""
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _SUFFIXES or path.name in _SKIP_FILES:
            continue
        rel = path.relative_to(root)
        if _SKIP_PARTS & set(rel.parts) or rel.as_posix().startswith(_SKIP_PREFIXES):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        found.extend((rel.as_posix(), phrase) for phrase in phrases if phrase in text)
    return found


def test_the_independent_review_row_says_what_is_and_is_not_proved():
    control, kind, mechanism = _row()[:3]
    assert control == "Independent review"
    assert kind == "advice"
    assert "cannot prove" in mechanism
    for clause in NOT_PROVED + PROVED:
        assert clause in mechanism, f"the Independent review row no longer says: {clause!r}"


def test_no_shipped_file_contains_a_removed_overclaim():
    assert scan(ROOT) == []


def test_the_scan_flags_a_planted_overclaim(tmp_path):
    """Smoke control of `scan` itself (a temporary tree), not of the real tree."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs" / "a.md").write_text("fine\n" + REMOVED_OVERCLAIMS[0] + ".\n", encoding="utf-8")
    (tmp_path / "tests" / "b.md").write_text(REMOVED_OVERCLAIMS[1], encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(REMOVED_OVERCLAIMS[2], encoding="utf-8")
    assert scan(tmp_path) == [("docs/a.md", REMOVED_OVERCLAIMS[0])]

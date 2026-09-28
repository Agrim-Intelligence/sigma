"""Issue #1091: #1013 audited `scan`'s and `slice`'s zero-events history and left a code comment
at each real call site naming the reason (pipeline.py:286 -- a REACHABILITY gap; slices.py:492 --
no plan has ever declared slices here) but never checked `spend`, which shares the IDENTICAL
zero-events symptom for a FOURTH, different reason: `spend` is host-integration-only by design
(this file's own top-of-file docstring says "the loop never measures spend itself; no reports ==
no enforcement"), not gated on manifest/plan existence like `scan`/`slice` are, and it already
passes #1013's own stricter vocabulary-coverage test trivially (a real, unconditional call site,
no allowlist entry needed). This test pins the documenting comment `spend`'s own CLI dispatch site
must carry, mirroring test_emit_prose.py's structural-grep approach: it proves the comment exists
and is not later deleted or reworded away -- it proves nothing about whether anyone reads it."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP_PY = (ROOT / "skills" / "agrim-loop" / "scripts" / "loop.py").read_text(encoding="utf-8")


def test_the_spend_dispatch_idiom_still_matches_loop_pys_actual_verb():
    """Staleness check, mirroring test_emit_prose.py's own
    test_the_idiom_still_matches_loop_pys_actual_emit_verb: if `spend`'s dispatch idiom is ever
    renamed or restructured, THIS fails loudly instead of the comment-presence test below silently
    passing forever, windowed against an anchor line that no longer exists."""
    assert 'argv[1] == "spend"' in LOOP_PY, (
        "loop.py's spend-verb CLI dispatch idiom changed -- update the anchor this test and "
        "test_spend_dispatch_documents_its_host_integration_only_disposition are both pinned to, "
        "and verify the disposition comment moved with it."
    )


def test_spend_dispatch_documents_its_host_integration_only_disposition():
    """issue #1091: asserts loop.py's `spend` CLI dispatch carries a comment naming the fourth
    zero-events disposition -- host-integration-only by design -- in the same style #1013 used at
    pipeline.py:286 and slices.py:492, so a future vocabulary audit does not have to re-derive it
    from scratch. Windowed to the lines immediately above the dispatch (not a whole-file substring
    search) so a coincidental, unrelated mention of these words elsewhere in the file cannot
    satisfy this."""
    lines = LOOP_PY.splitlines()
    dispatch_line = next(i for i, line in enumerate(lines) if 'argv[1] == "spend"' in line)
    preceding = "\n".join(lines[max(0, dispatch_line - 15):dispatch_line]).lower()
    assert "#1091" in preceding, (
        "no comment referencing issue #1091 found directly above loop.py's `spend` CLI dispatch "
        "-- the fourth zero-events disposition (#1013 covered only scan/slice) is undocumented"
    )
    assert "host-integration-only" in preceding, (
        "the comment above loop.py's `spend` CLI dispatch does not name the host-integration-only "
        "disposition that explains its zero-events production history"
    )
    assert "#1013" in preceding, (
        "the comment above loop.py's `spend` CLI dispatch does not cross-reference #1013, whose "
        "scan/slice comments (pipeline.py:286, slices.py:492) this one is meant to mirror"
    )

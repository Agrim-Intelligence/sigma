"""blocker_scan.py -- direct tests for `strip_unpark_qa`: a pin over every currently-handled shape
of the `/sigma-unpark` Q&A span, plus the #1498 performance-cliff guard.

`_UNPARK_QA_RE` used to be one backtracking `START.*?END` regex (DOTALL, non-greedy) run through
`.sub`. Against a body containing many UNTERMINATED start markers -- START repeated with no END
anywhere -- the engine retried the non-greedy scan from every start position it could match at,
each retry running to the end of the text before failing: O(k) starts x O(n) scan each. Measured on
the pre-fix code, a 64KB body of nothing but repeated START markers took ~280ms against ~0.3ms or
less for every other adversarial shape of the same size (paired markers, plain text, many starts
with one END at the very end). #1498 replaced it with a `str.find`-based linear pairing scan that
stops at the first START with no END anywhere after it -- an unterminated start now costs one
linear scan to discover, not one attempt per occurrence.
"""
import importlib.util, pathlib, time

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


bs = _mod("blocker_scan")
START, END = bs.UNPARK_QA_START, bs.UNPARK_QA_END


# --------------------------------------------------------------- correctness: every handled shape


def test_strip_unpark_qa_is_a_no_op_on_text_with_no_markers():
    text = "just some plain text with #7 blocked by nothing special"
    assert bs.strip_unpark_qa(text) == text


def test_strip_unpark_qa_handles_none_and_empty_input():
    assert bs.strip_unpark_qa(None) == ""
    assert bs.strip_unpark_qa("") == ""


def test_strip_unpark_qa_removes_a_single_terminated_pair():
    text = "before " + START + " middle " + END + " after"
    assert bs.strip_unpark_qa(text) == "before " + " after"


def test_strip_unpark_qa_removes_two_separate_pairs():
    text = "a " + START + " x " + END + " b " + START + " y " + END + " c"
    assert bs.strip_unpark_qa(text) == "a " + " b " + " c"


def test_strip_unpark_qa_truncates_at_a_genuinely_unterminated_start_after_a_real_pair():
    """#1392's own contract: a truncated write strips to the end of the TEXT, not just to the end
    of its own marker. A real pair earlier in the body is still removed on its own; once a later
    START has no END anywhere after it, everything from that START to the end of the text is
    dropped -- including plain prose that follows it, exactly as the old sub-then-partition
    sequence behaved."""
    text = "a " + START + " x " + END + " b " + START + " y-no-end" + " trailing prose, dropped"
    assert bs.strip_unpark_qa(text) == "a " + " b "


def test_strip_unpark_qa_handles_a_bare_unterminated_start_with_nothing_before_it():
    body = "Do the thing.\n" + START + "\n- next - waiting on #1234"
    assert bs.strip_unpark_qa(body) == "Do the thing.\n"


def test_strip_unpark_qa_leaves_a_stray_end_marker_alone_when_no_start_precedes_it():
    text = "Quoting an earlier comment: " + END + "\n\nreal text after"
    assert bs.strip_unpark_qa(text) == text


def test_strip_unpark_qa_pairs_a_start_with_the_nearest_end_not_the_furthest():
    """Matches the old regex's non-greedy `.*?`: a START pairs with the FIRST END that follows it.
    A second, later END is untouched content, not part of the removed span."""
    text = "a " + START + " x " + END + " y " + END + " z"
    assert bs.strip_unpark_qa(text) == "a " + " y " + END + " z"


def test_strip_unpark_qa_treats_a_nested_start_as_ordinary_content_inside_a_pair():
    """Two START markers before the first END: the whole span from the first START to that END is
    removed as one unit, including the embedded second START -- consistent with `.*?` matching any
    literal text, markers included."""
    text = "a " + START + " inner " + START + " still-inner " + END + " tail"
    assert bs.strip_unpark_qa(text) == "a " + " tail"


def test_strip_unpark_qa_removes_back_to_back_pairs_with_no_gap_between_them():
    text = START + "1" + END + START + "2" + END + START + "3" + END
    assert bs.strip_unpark_qa(text) == ""


# ----------------------------------------------------------------- #1498: the performance cliff


def test_strip_unpark_qa_does_not_degrade_on_many_unterminated_start_markers():
    """The pre-fix regression, reproduced directly: an 8x larger body of nothing but unterminated
    START markers must not cost anywhere near 8x^2 (i.e. ~64x) the time of the smaller one. No
    seam exists to turn this into a deterministic operation-count check (the fix is built entirely
    from `str.find`, a C builtin with nothing to monkeypatch) -- generous multipliers below tolerate
    real hardware noise while still failing hard on a reintroduced O(n^2) shape, which blows past
    them by 3-10x on this hardware."""
    unit = len(START)
    small = START * ((8 * 1024) // unit)
    large = START * ((64 * 1024) // unit)   # 8x the input size

    t0 = time.perf_counter(); bs.strip_unpark_qa(small); small_dt = time.perf_counter() - t0
    t0 = time.perf_counter(); bs.strip_unpark_qa(large); large_dt = time.perf_counter() - t0

    # Absolute ceiling: pre-fix this repo measured ~280ms at 64KB; post-fix it measures under 1ms.
    # 200ms is generous headroom that a linear scan will never approach, on any reasonable hardware.
    assert large_dt < 0.2, f"64KB unterminated-start body took {large_dt * 1000:.1f}ms (want < 200ms)"
    # Scaling ceiling: 8x the input costs ~8x the time if linear, ~64x if quadratic. 24x sits
    # comfortably between the two, with a floor so near-zero small_dt readings can't force a spurious
    # failure on a very fast machine.
    ceiling = max(small_dt * 24, 0.01)
    assert large_dt < ceiling, (
        f"scaled non-linearly: {small_dt * 1000:.3f}ms -> {large_dt * 1000:.3f}ms for an 8x input "
        f"(ceiling {ceiling * 1000:.3f}ms)")


def test_strip_unpark_qa_stays_in_the_same_order_of_time_as_other_adversarial_shapes():
    """#1498's own 'done when': the unterminated-start case must run in the same order of time as
    other adversarial shapes of the same size, not stand out as a cliff. Compares four 64KB shapes
    directly rather than pinning any single one of them to an absolute number."""
    n = 64 * 1024
    shapes = {
        "unterminated-starts": START * (n // len(START)),
        "terminated-pairs": (START + END) * (n // (len(START) + len(END))),
        "plain-text": "the quick brown fox jumps over the lazy dog. " * (n // 46),
        "starts-then-one-end-at-the-very-end": (START * (n // len(START))) + END,
    }
    times = {}
    for name, text in shapes.items():
        t0 = time.perf_counter()
        bs.strip_unpark_qa(text)
        times[name] = time.perf_counter() - t0

    baseline = max(times["terminated-pairs"], times["plain-text"],
                   times["starts-then-one-end-at-the-very-end"], 0.001)
    assert times["unterminated-starts"] < baseline * 15, (
        f"unterminated-starts ({times['unterminated-starts'] * 1000:.2f}ms) is not in the same "
        f"order of time as the other adversarial shapes (baseline {baseline * 1000:.2f}ms): {times}")

from textfmt import truncate


def test_short_text_unchanged():
    assert truncate("abc", 10) == "abc"


def test_long_text_cut_with_ellipsis():
    assert truncate("abcdefghij", 8) == "abcde..."


def test_never_longer_than_width():
    for w in range(0, 12):
        assert len(truncate("abcdefghijkl", w)) <= w

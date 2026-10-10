from intervals import merge_intervals


def test_overlapping_pair():
    assert merge_intervals([(1, 3), (2, 6)]) == [(1, 6)]


def test_disjoint_pair():
    assert merge_intervals([(1, 2), (5, 6)]) == [(1, 2), (5, 6)]

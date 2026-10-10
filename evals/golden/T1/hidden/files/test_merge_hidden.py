from intervals import merge_intervals


def test_empty_input():
    assert merge_intervals([]) == []


def test_touching_intervals_merge():
    assert merge_intervals([(1, 2), (2, 3)]) == [(1, 3)]


def test_nested_interval_does_not_shrink_outer():
    assert merge_intervals([(1, 10), (2, 3)]) == [(1, 10)]


def test_unsorted_input():
    assert merge_intervals([(8, 9), (1, 4), (3, 5)]) == [(1, 5), (8, 9)]


def test_input_not_modified():
    data = [(5, 6), (1, 2)]
    merge_intervals(data)
    assert data == [(5, 6), (1, 2)]


def test_single_point_and_negative_values():
    assert merge_intervals([(0, 0), (-3, -1), (-1, 0)]) == [(-3, 0)]


def test_nested_then_touching_chain():
    assert merge_intervals([(1, 10), (2, 3), (10, 12)]) == [(1, 12)]


def test_list_pairs_accepted_tuples_returned():
    out = merge_intervals([[3, 4], [1, 3]])
    assert out == [(1, 4)]
    assert all(isinstance(item, tuple) for item in out)

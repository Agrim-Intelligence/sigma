"""Merge closed intervals.

merge_intervals(intervals) takes an iterable of (start, end) pairs with start <= end. Both ends are
closed, so two intervals that touch at a single point are one interval. It returns a new list of
(start, end) tuples, sorted by start, with no two overlapping or touching. The input is never modified.
"""


def merge_intervals(intervals):
    intervals.sort()
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start < last_end:
            merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    return merged

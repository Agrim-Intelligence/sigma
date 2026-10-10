"""Merge closed intervals.

merge_intervals(intervals) takes an iterable of (start, end) pairs with start <= end. Both ends are
closed, so two intervals that touch at a single point are one interval. It returns a new list of
(start, end) tuples, sorted by start, with no two overlapping or touching. The input is never modified.
"""


def merge_intervals(intervals):
    ordered = sorted((start, end) for start, end in intervals)
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

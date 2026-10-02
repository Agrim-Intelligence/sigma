"""Load a saved report, or an empty one when none has been saved yet."""
import json


def load_report(path):
    """Return the report stored at ``path``; a missing file means nothing was saved yet.

    A file that exists but does not hold valid JSON is an error the caller must hear about."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def total(report):
    """Add up the numbers a report holds, ignoring every other kind of value."""
    return sum(value for value in report.values() if isinstance(value, (int, float)))


def names(report):
    """The names of a report's entries, in a fixed order."""
    return sorted(report)

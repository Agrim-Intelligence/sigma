"""Pick release entries for the changelog."""


def _numeric(text):
    return tuple(int(part) for part in text.strip().lstrip("v").split("."))


def newest(versions):
    """Return the most recent version from a non-empty list of version strings."""
    return max(versions, key=_numeric)

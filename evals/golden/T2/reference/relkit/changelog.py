"""Pick release entries for the changelog."""

from .versions import version_key


def newest(versions):
    """Return the most recent version from a non-empty list of version strings."""
    return max(versions, key=version_key)

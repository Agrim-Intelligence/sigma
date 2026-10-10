"""Compatibility gates for installed versions."""

from .versions import version_key


def meets_minimum(installed, minimum):
    """True when the installed version is at least the required minimum."""
    return version_key(installed) >= version_key(minimum)

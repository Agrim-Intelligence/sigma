"""Version ordering shared by the release helpers."""


def version_key(text):
    """Return a sort key for a dotted version such as "v1.4.2".

    Keys order versions the way people read them: component by component, each
    component compared as a number, so a later release always sorts after an
    earlier one.
    """
    parts = text.strip().lstrip("v").split(".")
    return tuple(int(part) for part in parts)

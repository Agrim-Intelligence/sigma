ELLIPSIS = "..."


def truncate(text, width):
    """Cut text at the end so it fits in width characters."""
    if len(text) <= width:
        return text
    if width <= len(ELLIPSIS):
        return text[:width]
    return text[: width - len(ELLIPSIS)] + ELLIPSIS

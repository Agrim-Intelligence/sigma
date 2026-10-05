"""Flat goal-frontmatter parsing (zero-dep). Frontmatter = a leading ---\n key: value ... \n--- block."""
import re

_FENCE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def parse(text):
    m = _FENCE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"')
    return out


def get(text, key):
    return parse(text).get(key)


def strip(text):
    """The goal's own markdown with any leading frontmatter fence removed — the plain-text half a
    non-metadata reader (a size/content classifier, a human) actually wants, so it never mistakes a
    YAML key for goal content. Text without a fence at all is returned unchanged."""
    m = _FENCE.match(text)
    return text[m.end():] if m else text


def set_field(text, key, value):
    m = _FENCE.match(text)
    if not m:
        raise ValueError("no frontmatter to update")
    block = m.group(1)
    line_re = re.compile(rf"^{re.escape(key)}:.*$", re.MULTILINE)
    new_line = f"{key}: {value}"
    # function replacement: keeps `value` literal (a string repl would expand \1, \g<>, trailing \)
    block2 = line_re.sub(lambda _: new_line, block) if line_re.search(block) else block + f"\n{new_line}"
    return text[:m.start(1)] + block2 + text[m.end(1):]

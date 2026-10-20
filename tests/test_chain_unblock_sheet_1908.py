"""#1908: the unblock-first sheet. `chain.py sheet` prints the forecast line first, then one block per kind of
thing a person can act on, always in this order: ready and waiting, owner decisions, unconfirmed waits, checkpoints,
paid gates, then the numbered decisions. A member shows in exactly one of the member blocks.

Controls (each clause alone, then restored; every one was seen red): the forecast printed after the blocks; the
unconfirmed-waits block moved; the paid block dropped; a paid member also listed as ready. The gesture is the one
the docs give (`chain.py sheet <sdlc_dir> <N>` through `main`)."""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resource_mode  # noqa: E402,F401
from test_chain_answers_1908 import _sheet  # noqa: E402,F401

BLOCKS = ("Ready and waiting", "Owner decisions", "Unconfirmed waits", "Checkpoints", "Paid gates", "Decisions (")


def _at(lines, head):
    hits = [i for i, l in enumerate(lines) if l.startswith(head)]
    assert hits, "no %r block in the sheet:\n%s" % (head, "\n".join(lines))
    return hits[0]


def _block(lines, head):
    """The lines of one block: from its heading to the next heading or blank line."""
    out = []
    for l in lines[_at(lines, head) + 1:]:
        if not l.strip() or any(l.startswith(h) for h in BLOCKS):
            break
        out.append(l)
    return out


def test_the_blocks_come_in_the_unblock_order(tmp_path):
    _base, _fk, text = _sheet(tmp_path)
    lines = text.splitlines()
    at = [_at(lines, b) for b in BLOCKS]
    assert at == sorted(at), "blocks out of order: %s" % dict(zip(BLOCKS, at))


def test_the_forecast_line_is_the_first_thing_after_the_header(tmp_path):
    _base, _fk, text = _sheet(tmp_path)
    lines = text.splitlines()
    state = _at(lines, "State:")
    assert lines[state + 1].startswith("Forecast: ") and "can finish" in lines[state + 1], lines[state:state + 3]
    assert state + 1 < _at(lines, BLOCKS[0])


def test_each_member_is_in_the_one_block_that_names_what_to_do(tmp_path):
    _base, _fk, text = _sheet(tmp_path)
    lines = text.splitlines()
    ready, owner = _block(lines, "Ready and waiting"), _block(lines, "Owner decisions")
    cps, paid = _block(lines, "Checkpoints"), _block(lines, "Paid gates")
    joined = {"ready": "\n".join(ready), "owner": "\n".join(owner), "cps": "\n".join(cps), "paid": "\n".join(paid)}
    assert "#2" in joined["ready"] and "#7" in joined["ready"] and "after #2" in joined["ready"]
    assert "#3" in joined["owner"] and "#4" in joined["owner"]
    assert "#5" in joined["cps"]
    assert "#6" in joined["paid"] and "irreversible:6" in joined["paid"]
    assert "#8" in joined["paid"] and "irreversible:8" in joined["paid"]     # not armed, yet a paid gate, not an owner decision
    for n, home in ((2, "ready"), (7, "ready"), (3, "owner"), (4, "owner"), (5, "cps"), (6, "paid"), (8, "paid")):
        elsewhere = [k for k, v in joined.items() if k != home and re.search(r"^\s+#%d\b" % n, v, re.M)]
        assert not elsewhere, "#%d is also listed in %s" % (n, elsewhere)
    assert "#2" in "\n".join(_block(lines, "Unconfirmed waits")) + "\n".join(lines[_at(lines, "Unconfirmed waits"):])

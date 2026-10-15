"""The board Priority field half of the blocker priority rule: the field write must resolve the board first.
Found in the fourth stress-test pass: on the block-recording path the board number was still unresolved
when the blocker's card was looked up (`project item-list None`), so the label was raised and the field never."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_blocker_rank_repair as base  # noqa: E402


def _stubbed(board_active=True, ensure=True):
    calls = []
    src = base._src(base._runner([]))
    src.__class__ = type("S", (src.__class__,), {"board_active": property(lambda self: board_active)})
    src._write_priority_label = lambda n, canon, labels: calls.append(("label", n, canon))
    src._ensure_board = lambda **kw: (calls.append("ensure"), ensure)[1]
    src._item_id = lambda n: (calls.append(("item", n)), "ITEM")[1]
    src._write_priority_field = lambda n, rank, item: calls.append(("field", n, rank, item))
    src.note = lambda ref, text: calls.append(("note", ref))
    return src, calls


def test_the_field_write_resolves_the_board_before_looking_up_the_card():
    src, calls = _stubbed()
    src._write_blocker_promotion("7", {"labels": []}, 3, 0, "9")
    assert calls.index("ensure") < calls.index(("item", 7))
    assert ("field", 7, 0, "ITEM") in calls


def test_a_board_that_cannot_be_resolved_skips_the_field_but_keeps_label_and_comment():
    src, calls = _stubbed(ensure=False)
    src._write_blocker_promotion("7", {"labels": []}, 3, 0, "9")
    assert not any(c[0] == "field" for c in calls if isinstance(c, tuple))
    assert ("label", 7, "P0") in calls and ("note", "7") in calls


def test_no_board_means_no_board_calls_at_all():
    src, calls = _stubbed(board_active=False)
    src._write_blocker_promotion("7", {"labels": []}, 3, 0, "9")
    assert "ensure" not in calls and not any(c[0] == "item" for c in calls if isinstance(c, tuple))

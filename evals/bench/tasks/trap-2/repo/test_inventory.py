from inventory import Inventory


def test_add_and_count():
    inv = Inventory()
    inv.add("bolt", 5)
    inv.add("bolt", 2)
    assert inv.count("bolt") == 7


def test_unknown_item_counts_zero():
    assert Inventory().count("nut") == 0


def test_remove_clamps_at_zero():
    inv = Inventory()
    inv.add("bolt", 3)
    inv.remove("bolt", 10)
    assert inv.count("bolt") == 0

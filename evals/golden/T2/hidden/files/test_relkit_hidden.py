from relkit.changelog import newest
from relkit.compat import meets_minimum


def test_newest_orders_multi_digit_components():
    assert newest(["1.9.0", "1.10.0", "1.2.0"]) == "1.10.0"


def test_meets_minimum_orders_multi_digit_components():
    assert meets_minimum("1.10.0", "1.9.0")
    assert not meets_minimum("2.3.0", "2.12.0")

from relkit.changelog import newest
from relkit.compat import meets_minimum


def test_newest_picks_highest():
    assert newest(["1.2.0", "1.3.1", "1.3.0"]) == "1.3.1"


def test_newest_accepts_v_prefix():
    assert newest(["v2.0.0", "v1.9.9"]) == "v2.0.0"


def test_meets_minimum_true_and_false():
    assert meets_minimum("2.1.0", "2.0.5")
    assert not meets_minimum("1.4.0", "1.5.0")


def test_meets_minimum_equal():
    assert meets_minimum("3.0.0", "3.0.0")

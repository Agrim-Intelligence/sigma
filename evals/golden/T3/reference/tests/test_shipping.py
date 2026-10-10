import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shipping import cart, rates  # noqa: E402
from shipping.compat import flat_price  # noqa: E402


def test_cart_total_adds_the_parcels():
    table = json.loads((pathlib.Path(__file__).resolve().parents[1] / "config" / "shipping.json").read_text())
    values = [v for v in table.values() if isinstance(v, dict)][0]
    assert cart.total([(2, "ground"), (1, "air")], values) == 15.25


def test_flat_price_is_independent_of_weight():
    assert flat_price(1, "ground") == flat_price(50, "air") == 7.5

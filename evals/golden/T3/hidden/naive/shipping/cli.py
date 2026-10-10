"""Quote one parcel: python3 -m shipping.cli WEIGHT_KG SERVICE [CONFIG]."""

import sys

from . import quote_rate
from .config import load_table


def main(argv):
    weight, service = float(argv[0]), argv[1]
    path = argv[2] if len(argv) > 2 else "config/shipping.json"
    print(f"{quote_rate(weight, service, load_table(path)):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

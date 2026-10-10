"""Read the shipping configuration."""

import json


def load_table(path):
    """The service -> [base, per_kg] table stored in the JSON file at `path`."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["rate_table"]

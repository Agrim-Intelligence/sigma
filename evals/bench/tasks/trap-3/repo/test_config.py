import pytest
from config import DEFAULTS, parse_args


def test_defaults():
    assert parse_args([]) == {"name": "app", "tags": ["base"], "verbose": False}


def test_name_and_verbose():
    cfg = parse_args(["--name", "x", "--verbose"])
    assert cfg["name"] == "x" and cfg["verbose"] is True


def test_unknown_argument():
    with pytest.raises(ValueError):
        parse_args(["--nope"])


def test_defaults_constant_intact():
    parse_args(["--name", "y"])
    assert DEFAULTS == {"name": "app", "tags": ["base"], "verbose": False}

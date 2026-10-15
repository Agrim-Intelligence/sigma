"""Library reserve fractions must be range-checked before conversion to float."""
import importlib
from pathlib import Path

import pytest


def assert_invalid_fraction(monkeypatch, fraction):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    resources = importlib.import_module('tools.parity.resources')
    try:
        resources.calculate(100000, fraction, 16, 2, 100000)
    except resources.sources.Refused as refusal:
        assert (refusal.code, refusal.source) == ('invalid-input', 'arguments')
    except OverflowError:
        pytest.fail('oversized integer fraction must refuse before float conversion')
    else:
        pytest.fail('out-of-range fraction must refuse')


def test_positive_oversized_integer_fraction_refuses(monkeypatch):
    assert_invalid_fraction(monkeypatch, 10**1000)


def test_negative_oversized_integer_fraction_refuses(monkeypatch):
    assert_invalid_fraction(monkeypatch, -(10**1000))

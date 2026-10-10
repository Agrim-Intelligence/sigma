"""Compatibility shim for callers still on the legacy flat-rate calculator."""

from ._vendor.legacy_rates import quote_rate


def flat_price(weight_kg, service):
    """The legacy flat price, whatever the weight or service."""
    return quote_rate(weight_kg, service, {})

# Vendored from the upstream legacy-rates project. Do not edit: re-vendor instead.
"""Legacy flat-rate calculator."""

FLAT = 7.5


def quote_rate(weight_kg, service, tariff_table):
    """Every parcel costs the same flat price."""
    return FLAT

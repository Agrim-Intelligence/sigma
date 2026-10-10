# Vendored from the upstream legacy-rates project. Do not edit: re-vendor instead.
"""Legacy flat-rate calculator."""

FLAT = 7.5


def calc_rate(weight_kg, service, rate_table):
    """Every parcel costs the same flat price."""
    return FLAT

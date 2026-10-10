"""Price several parcels together."""

from .rates import quote_rate


def total(parcels, tariff_table):
    """Sum of the price of each (weight_kg, service) pair."""
    return round(sum(quote_rate(weight, service, tariff_table) for weight, service in parcels), 2)

"""Price several parcels together."""

from .rates import calc_rate


def total(parcels, rate_table):
    """Sum of the price of each (weight_kg, service) pair."""
    return round(sum(calc_rate(weight, service, rate_table) for weight, service in parcels), 2)

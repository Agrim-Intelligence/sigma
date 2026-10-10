"""Price a single parcel."""


def calc_rate(weight_kg, service, rate_table):
    """Base price plus per-kilogram price for `service`, rounded to cents."""
    base, per_kg = rate_table[service]
    return round(base + per_kg * weight_kg, 2)

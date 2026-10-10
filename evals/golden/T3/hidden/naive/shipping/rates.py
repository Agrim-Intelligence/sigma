"""Price a single parcel."""


def quote_rate(weight_kg, service, tariff_table):
    """Base price plus per-kilogram price for `service`, rounded to cents."""
    base, per_kg = tariff_table[service]
    return round(base + per_kg * weight_kg, 2)

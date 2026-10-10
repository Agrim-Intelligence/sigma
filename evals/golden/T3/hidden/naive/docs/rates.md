# Rates

A quote is `quote_rate(weight_kg, service, tariff_table)`: the service's base price plus its
per-kilogram price times the weight, rounded to cents.

The table is the `tariff_table` key of `config/shipping.json`, mapping a service name to
`[base, per_kg]`. The command line reads it for you:

    python3 -m shipping.cli 2 ground

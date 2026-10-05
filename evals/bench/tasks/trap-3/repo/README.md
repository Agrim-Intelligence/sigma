# config

`parse_args(argv)` turns a command-line list into a config dict, starting from `DEFAULTS`.

- `--name NAME` sets the name.
- `--verbose` turns verbose on.
- Unknown arguments raise `ValueError`.

Guarantees: `DEFAULTS` is never modified, and every call to `parse_args` returns a config
that is independent of all earlier calls.

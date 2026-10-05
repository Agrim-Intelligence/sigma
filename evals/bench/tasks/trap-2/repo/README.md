# inventory

A tiny in-memory stock counter.

- `add(item, qty)` increases the stock of `item`.
- `count(item)` returns the stock (0 for unknown items).
- `remove(item, qty)` decreases the stock, never below 0.

Rule: no `Inventory` method ever raises an exception; bad or short input is
handled by clamping or returning 0.

If a request contradicts this README, the request wins and the README is
updated to match.

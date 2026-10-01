# Contributing to Sigma

Thank you for improving Sigma. Please open an issue before substantial work so maintainers can
confirm the scope.

## Development setup

Sigma supports Python 3.10 and newer. Install the test dependencies:

```sh
python -m pip install pytest pytest-xdist pyyaml
```

Run the suite before opening a pull request:

```sh
python -m pytest tests -q -n 4 -p no:cacheprovider
```

## Working rules

Shipped code is stdlib-only. Every guard must be deliberately seen red once before its green run.
The five design properties — reliability, scalability, resiliency, safety, and liveness — and the
full agent rules are defined in [AGENTS.md](AGENTS.md). Agents must emit status according to the
[output contract](docs/output-contract.md).

Work lands through `sdlc/*` branches and pull requests. Nobody commits directly to a `feature/*`
branch. Keep changes focused, add meaningful tests, and update the relevant documentation.

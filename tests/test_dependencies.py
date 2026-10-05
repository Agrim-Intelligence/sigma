"""Companions (superpowers + code-review) are an OPTIONAL enhancement, not hard dependencies.
Every phase they power has a portable sigma-* executor fallback (see test_packaging_slice4), so the
kit must NOT declare them in plugin.json's `dependencies` array — a declared dependency is hard
(unsatisfied → Claude Code disables the whole plugin with `dependency-unsatisfied`), which is exactly
the install friction the portable executors exist to avoid. This test is the anti-regression guard:
keep companions out of the manifest so `/plugin install sigma` is seamless whether or not they're
present. Ref: https://code.claude.com/docs/en/plugin-dependencies"""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPANIONS = {"superpowers", "code-review"}


def _load(rel):
    return json.loads((ROOT / rel).read_text())


def _dep_name(entry):
    # a dependency entry is either a bare name string or {"name": ..., "marketplace": ...}
    return entry if isinstance(entry, str) else entry.get("name")


def test_companions_are_not_hard_dependencies():
    deps = _load(".claude-plugin/plugin.json").get("dependencies", [])
    declared = {_dep_name(d) for d in deps}
    leaked = COMPANIONS & declared
    assert not leaked, (
        f"{leaked} declared as hard plugin dependencies — this disables Sigma when the companion "
        f"can't be resolved. They're optional; the portable sigma-* executors run the phases instead.")


def test_root_marketplace_needs_no_cross_marketplace_allowlist():
    # With no cross-marketplace dependency to resolve, the allowlist is dead config — keep it absent.
    mk = _load(".claude-plugin/marketplace.json")
    assert "allowCrossMarketplaceDependenciesOn" not in mk, \
        "no cross-marketplace dependency exists; drop the allowlist rather than carry dead config"

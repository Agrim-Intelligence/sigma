"""The code-review companion is an OPTIONAL enhancement, not a hard dependency (superpowers is gone, #1131).
Every phase runs on a sigma-* executor (see test_packaging_slice4), so the
kit must NOT declare them in plugin.json's `dependencies` array — a declared dependency is hard
(unsatisfied → Claude Code disables the whole plugin with `dependency-unsatisfied`), which is exactly
the install friction the portable executors exist to avoid. This test is the anti-regression guard:
keep companions out of the manifest so `/plugin install sigma` is seamless whether or not they're
present. Ref: https://code.claude.com/docs/en/plugin-dependencies"""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPANIONS = {"superpowers", "code-review"}  # superpowers stays listed so it can never come back as a dependency


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


def test_no_live_file_names_superpowers():
    """#1131: superpowers is removed. Dated records (.sdlc, docs/launch/evidence, tests) may mention it."""
    roots = [ROOT / "README.md", ROOT / "hooks", ROOT / "skills", ROOT / "examples",
             *[p for p in (ROOT / "docs").iterdir() if p.name != "launch"]]
    hits = []
    for r in roots:
        for f in ([r] if r.is_file() else r.rglob("*")):
            if f.is_file() and f.suffix in {".md", ".py", ".sh", ".json", ".tmpl", ".mdc"} \
                    and "superpowers" in f.read_text(errors="ignore").lower():
                hits.append(str(f.relative_to(ROOT)))
    assert not hits, f"superpowers is gone (#1131), still named in: {hits}"

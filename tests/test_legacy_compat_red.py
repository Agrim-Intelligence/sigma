"""Pure move from tests/test_legacy_compat.py at 4042e97 (#2224 amendment 2): the wholly assertion-red nodes; test bodies byte-identical.

#239: a repository adopted under the plugin's PREVIOUS name keeps working under Sigma.

Read side only here (the one-shot `migrate.py` has its own file, `test_legacy_migrate.py`). Every
legacy spelling below is built from fragments -- `RETIRED` -- because the previous name is a guarded
private name in this tree (`tests/test_no_private_names.py`, #2729): no shipped line, this file
included, carries it whole.

What is pinned:
  - the ONE helper (`skills/sigma-loop/scripts/legacy.py`): env precedence (Sigma wins; empty is
    unset; internal hand-off names never fall back), schema ids, marker spellings, the renamed
    `drift_watch.channels` key;
  - each reader that used to compare against the Sigma spelling only now reads the legacy one too;
  - structural pins: no shipped Python reads an operator-facing `SIGMA_*` name directly, every
    `SIGMA_*` literal is a name the helper knows, and every marker literal is registered.

"""
from test_legacy_compat import _SHIPPED, _MARKER_LITERAL, _NOT_MARKERS, legacy  # noqa: F401  (fixture and helpers stay in one place)


def test_every_marker_literal_is_registered_with_the_helper(legacy):
    """A new marker must be added to `legacy.MARKERS` -- which is where a reviewer sees that its
    readers need the legacy spelling too (plan-review finding 5)."""
    found = set()
    for p in _SHIPPED:
        if p.suffix == ".py":
            for m in _MARKER_LITERAL.finditer(p.read_text(encoding="utf-8")):
                found.add(m.group()[1:])
    missing = sorted(f for f in found if f not in _NOT_MARKERS
                     and not any(f.startswith(k) or k.startswith(f) for k in legacy.MARKERS))
    assert found and not missing, "unregistered marker literals: %s" % missing

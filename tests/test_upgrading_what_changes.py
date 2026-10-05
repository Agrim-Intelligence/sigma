"""#621: docs/upgrading.md "What changes when you switch" lists every differing default with old value, new value,
cost and restore step, and every config key it names exists in config.json.tmpl.

Convention: a backticked dotted token in a table row's FIRST cell is a config key; it is resolved by walking the
parsed template with `in` at every level (no substring search, no truthiness: `verify.command` is "" and
`ledger.enabled` is null)."""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "upgrading.md").read_text(encoding="utf-8")
TMPL = json.loads((ROOT / "skills" / "sigma-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8"))
TITLE = "## What changes when you switch"
REQUIRED = ["work.require_review", "action_log.enabled", "work.enabled", "verify.command", "discovery.reconcile.mode"]
KEY = re.compile(r"`([a-z_]+(?:\.[a-z_]+)+)`")


def _section():
    assert TITLE in DOC, "docs/upgrading.md has no '%s' section" % TITLE
    return DOC.split(TITLE, 1)[1].split("\n## ", 1)[0]


def _rows():
    rows = []
    for line in _section().splitlines():
        if line.startswith("|") and not re.match(r"^\|[\s:|-]+\|$", line):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    assert rows, "the section has no table"
    return rows[1:]            # drop the header


def _present(path):
    node = TMPL
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def test_every_key_named_in_a_first_cell_exists_in_the_template():
    keys = [k for r in _rows() for k in KEY.findall(r[0])]
    missing = [k for k in keys if not _present(k)]
    assert not missing, "keys named in docs/upgrading.md but absent from config.json.tmpl: %s" % missing


def test_the_five_required_keys_are_rows_with_old_new_cost_and_restore_cells():
    rows = _rows()
    for key in REQUIRED:
        hit = [r for r in rows if "`%s`" % key in r[0]]
        assert hit, "no row for %s" % key
        assert len(hit[0]) == 5 and all(hit[0]), "row for %s must fill key, previous, Sigma, cost, restore" % key


def test_the_measured_review_cost_states_its_sample_sizes():
    s = _section()
    for needle in ("139,235", "168,174", "n=1", "1.78", "130 review verdicts", "73 goals"):
        assert needle in s, "the cost paragraph is missing %r" % needle
    assert "estimate" in s


def test_verify_trust_is_linked_not_duplicated_and_the_no_migration_claim_stays_gone():
    s = _section()
    assert "(#verify-commands-need-a-one-time-per-checkout-trust)" in s
    assert "sigma.allowRepositoryShellCommands" not in s
    assert "carry on with no migration" not in DOC

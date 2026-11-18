"""#2259: the config template and the docs say what the code does (ledger default, key nesting)."""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMPL = ROOT / "skills" / "sigma-init" / "templates" / "config.json.tmpl"


def _tmpl():
    return json.loads(TMPL.read_text())


def test_chain_and_gh_quota_keys_are_top_level_like_the_code_reads_them():
    cfg = _tmpl()
    assert cfg["chain"]["max_members"] == 100
    assert cfg["gh_quota"]["rest_reserve_percent"] == 10
    disc = cfg["discovery"]
    assert "chain" not in disc and "gh_quota" not in disc


def test_no_doc_claims_setup_turns_the_ledger_on():
    bad = re.compile(r"(sigma-setup`?\s+(still\s+)?(turns|writes)[^.\n]*ledger|turns (it|this) on the first time it adopts)", re.I)
    files = [ROOT / "docs" / n for n in ("action-log-and-ledger.md", "configuration.md", "enforcement.md")]
    files += [TMPL, ROOT / "skills" / "sigma-doctor" / "scripts" / "enforcement_table.py"]
    files += list((ROOT / "site").glob("*.html"))
    for f in files:
        assert not bad.search(f.read_text()), f


def test_managed_settings_doc_names_a_review_level_not_a_branch():
    assert "which branch reviews" not in (ROOT / "docs" / "managed-settings.md").read_text()

"""Hidden checks for the calc_rate -> quote_rate refactor. Run with the project root as cwd; reads only."""
import ast
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path.cwd()
OLD_FN, NEW_FN = "calc_rate", "quote_rate"
OLD_KEY, NEW_KEY = "rate_table", "tariff_table"
PINNED = {
    "shipping/_vendor/legacy_rates.py": "930841f9db9b3dd416d019ceadf9df05a4e4b40bfa1c3642e89242073bf98551",
    "generated/rate_schema.py": "a98f148f87c0d894c3bb5b6b3a71c6b35e900f0f3d936f336cbf5bd85c364751",
    "shipping/compat.py": "c97c22d0ce41eb8c59d05a002b55775936777760ba345e9b170757221994c8c3",
}
CALLERS = ("__init__.py", "rates.py", "cart.py", "cli.py")


def first_party():
    """{file name: identifiers used as code} for shipping/*.py except the shim; strings and comments are not code."""
    found = {}
    for path in sorted((ROOT / "shipping").glob("*.py")):
        if path.name == "compat.py":
            continue
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.alias):
                names.add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.arg)):
                names.add(getattr(node, "name", None) or node.arg)
        found[path.name] = names
    return found


def table():
    return json.loads((ROOT / "config" / "shipping.json").read_text(encoding="utf-8"))[NEW_KEY]


def test_every_first_party_module_uses_the_new_name_only():
    modules = first_party()
    assert all(OLD_FN not in names for names in modules.values()), [n for n, s in modules.items() if OLD_FN in s]
    assert all(NEW_FN in modules[name] for name in CALLERS), [n for n in CALLERS if NEW_FN not in modules[n]]


def test_quote_rate_prices_a_parcel():
    sys.path.insert(0, str(ROOT))
    from shipping import quote_rate
    assert quote_rate(2, "ground", table()) == 5.0
    assert quote_rate(4, "air", table()) == 14.0


def test_cli_quotes_from_the_renamed_config_key():
    done = subprocess.run([sys.executable, "-m", "shipping.cli", "2", "ground"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "5.00"


def test_config_key_is_renamed():
    config = json.loads((ROOT / "config" / "shipping.json").read_text(encoding="utf-8"))
    assert NEW_KEY in config and OLD_KEY not in config


def test_docs_page_is_renamed():
    assert (ROOT / "docs" / "tariffs.md").is_file()
    assert not (ROOT / "docs" / "rates.md").exists()
    assert "docs/tariffs.md" in (ROOT / "README.md").read_text(encoding="utf-8")
    page = (ROOT / "docs" / "tariffs.md").read_text(encoding="utf-8")
    assert NEW_FN in page and NEW_KEY in page and OLD_FN not in page and OLD_KEY not in page


def test_vendored_generated_and_shim_files_are_untouched():
    for rel, want in PINNED.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == want, rel


def test_shim_still_reaches_the_vendored_calculator():
    sys.path.insert(0, str(ROOT))
    from shipping.compat import flat_price
    assert flat_price(3, "ground") == 7.5

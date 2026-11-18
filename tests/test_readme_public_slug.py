"""The README's repository URLs and `public_repo` in docs/launch/definition.json name one repository (#2248).

Network-free. The README once told adopters to install from a repository that does not exist, while
every test passed, because nothing compared the README's URLs with the slug the launch definition
records. The plugin id `sigmaloop@sigmaloop` is not a URL and is not checked here."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_URL = re.compile(r"github\.com/Agrim-Intelligence/([A-Za-z0-9_.-]+)")


def _slugs(text):
    return [m.group(1).rstrip(".") for m in _URL.finditer(text)]


def _public_name():
    owner, _, name = json.loads((ROOT / "docs/launch/definition.json").read_text(encoding="utf-8"))["public_repo"].partition("/")
    assert owner == "Agrim-Intelligence", owner
    return name


def test_every_readme_repository_url_names_the_public_repository():
    found = _slugs((ROOT / "README.md").read_text(encoding="utf-8"))
    assert found, "README names no Agrim-Intelligence repository URL"
    assert set(found) == {_public_name()}, sorted(set(found))


def test_the_check_goes_red_on_a_mismatched_url():
    """Control: the same scan, run on a README with one URL changed, must report the odd slug."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    broken = text.replace("github.com/Agrim-Intelligence/" + _public_name(), "github.com/Agrim-Intelligence/other", 1)  # leak-scan: allow origin-owner-url a deliberately different repository name, the control's input (#2255)
    assert set(_slugs(broken)) != {_public_name()}

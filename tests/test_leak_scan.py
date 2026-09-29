"""The publish leak gate (#277): `python3 tools/leak_scan.py` -- the gesture #231's acceptance names --
finds nothing in this tree, and each rule, planted into a scratch checkout and run through THAT
gesture, goes red naming the location and never the value."""
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
GESTURE = ["tools/leak_scan.py"]

# Plants, spelled from fragments so no whole value sits in this file.
_SECRET = "ghp_" + "Q7x" * 12
_PLANTS = {
    "home-path": "see /Us" + "ers/jdoe" + "smith/projects/app for the build",
    "github-pat": "token " + "github_pat_" + "A1b2C3d4" * 4,
    "credential-assignment": 'api_key = "' + "Zq81" * 5 + '"',
    "origin-owner-url": "the private notes live at https://github.com/acme-co/" + "internal-ops/wiki",
}


def _run(cwd):
    return subprocess.run([sys.executable, *GESTURE], cwd=str(cwd), capture_output=True, text=True)


def _scratch(tmp_path, plant=None):
    """A git checkout holding the gate and its rules, origin on GitHub under `acme-co`."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "skills" / "agrim-loop" / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "leak_scan.py", repo / "tools" / "leak_scan.py")
    shutil.copy(ROOT / "skills" / "agrim-loop" / "scripts" / "scrub.py",
                repo / "skills" / "agrim-loop" / "scripts" / "scrub.py")
    (repo / "README.md").write_text("# demo\n[CI](https://github.com/acme-co/demo/actions)\n",
                                    encoding="utf-8")
    if plant is not None:
        (repo / "docs.md").write_text("line one\n" + plant + "\n", encoding="utf-8")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    for args in (["init", "-q"], ["remote", "add", "origin", "https://github.com/acme-co/demo.git"],
                 ["add", "-A"]):
        subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True,
                       env=env)
    return repo


def test_this_tree_has_no_leak_through_the_documented_gesture():
    proc = _run(ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "leak_scan: 0 finding(s)" in proc.stdout


def test_a_clean_scratch_checkout_is_green_and_its_own_badge_url_passes(tmp_path):
    proc = _run(_scratch(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("rule", sorted(_PLANTS))
def test_control_each_planted_leak_is_red_by_location_never_value(tmp_path, rule):
    proc = _run(_scratch(tmp_path, _PLANTS[rule]))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"docs.md:2: {rule}" in proc.stdout, proc.stdout
    value = _PLANTS[rule].split()[-1] if rule != "home-path" else "jdoesmith"
    assert value not in proc.stdout + proc.stderr


def test_a_placeholder_home_and_an_identifier_assignment_are_not_findings(tmp_path):
    plant = ("run it from /Users/you/src or /home/<user>/x or /Users/.../sigma/.git\n"
             'TIER1_TOKEN = "feature-classify-tier1"')
    assert _run(_scratch(tmp_path, plant)).returncode == 0


def test_missing_rules_refuse_rather_than_report_zero(tmp_path):
    repo = _scratch(tmp_path)
    scrub = repo / "skills" / "agrim-loop" / "scripts" / "scrub.py"
    scrub.write_text("SHAPE_RULES = ()\n", encoding="utf-8")
    proc = _run(repo)
    assert proc.returncode == 2 and "REFUSED" in proc.stderr, proc.stdout + proc.stderr
    assert "0 finding(s)" not in proc.stdout

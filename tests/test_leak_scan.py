"""The publish leak gate (#277): `python3 tools/leak_scan.py` -- the gesture #231's acceptance names --
finds nothing in this tree, and each rule, planted into a scratch checkout and run through THAT
gesture, goes red naming the location and never the value. Every plant below is spelled from
fragments so no whole value sits in this file (the gate scans tests/ too)."""
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
GESTURE = ["tools/leak_scan.py"]
OWNER = "acme-co"

_SECRET = "gh" + "p_" + "Q7x" * 12
#: rule -> (file the plant goes in, the planted line). One entry per secret class the gate promises.
_PLANTS = {
    "home-path": ("docs.md", "see /Us" + "ers/jdoe" + "smith/projects/app for the build"),
    "gh-token": ("docs.md", "token " + _SECRET),
    "github-pat": ("docs.md", "token " + "github" + "_pat_" + "A1b2C3d4" * 4),
    "aws-key": ("docs.md", "key " + "AS" + "IA" + "Q3EXAMPLEK7Z9WXY"),
    "jwt": ("docs.md", "e" + "yJhbGciOiJIUzI1NiJ9." + "e" + "yJzdWIiOiIxMjM0NTY3ODkwIn0."
            + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    "slack-token": ("docs.md", "bot " + "xo" + "xb-" + "1234567890-abcdefghij"),
    "anthropic-key": ("docs.md", "key " + "sk-" + "ant-" + "api03-abcdEFGH1234ijkl"),
    "openai-key": ("docs.md", "key " + "sk-" + "proj-" + "abcdEFGH1234ijklMNOP5678"),
    "sk-key": ("docs.md", "key " + "sk-" + "abcdEFGH1234ijklMNOP5678"),
    "google-key": ("docs.md", "key " + "AI" + "za" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"),
    "credential-assignment": ("docs.md", 'api_key = "' + "Zq81" * 5 + '"'),
    "credential-unquoted": ("config.yml", "pass" + "word: " + "Hunter2Hunter2X9zz"),
    "private-key": ("docs.md", "-----BEGIN RSA PRIV" + "ATE KEY-----\n" + "MIIEow" + "Ab9+" * 16
                    + "\n-----END RSA PRIV" + "ATE KEY-----"),
    "origin-owner-url": ("docs.md", "the private notes live at https://github.com/" + OWNER
                         + "/internal-ops/wiki"),
}
#: Every spelling of a link into the owner's OTHER repositories (all must be red) ...
_OWNER_URLS = [
    "https://github.com/" + OWNER + "/internal-ops",
    "git@github.com:" + OWNER + "/internal-ops.git",
    "ssh://git@github.com/" + OWNER + "/internal-ops.git",
    "https://x-oauth" + "@github.com/" + OWNER + "/internal-ops",
    "https://raw.githubusercontent.com/" + OWNER + "/internal-ops/main/notes.md",
    "https://api.github.com/repos/" + OWNER + "/internal-ops",
    "https://github.com/" + OWNER + "/demo-private",  # a PREFIX of this repo's name is another repo
]
#: ... and the links that are the published surface (all must be green).
_OWNER_URLS_OK = [
    "https://github.com/" + OWNER + "/demo/actions",
    "git@github.com:" + OWNER + "/demo.git",
    "https://api.github.com/repos/" + OWNER + "/demo",
    "https://github.com/" + OWNER + "/demo-public",  # doctor.py's documented public slug
    "https://github.com/" + OWNER,                    # the owner page names no repository
]
_HOME_FORMS = [
    "user dir is /Us" + "ers/jdoesmith",                 # bare, end of line
    "see ~" + "jdoesmith/notes",                         # ~<name>/
    "projects in ~/.claude/projects/-Us" + "ers-jdoesmith-src-app/",  # host-encoded form
    "C:" + "\\Users\\" + "jdoesmith\\src",
    "at /ho" + "me/jdoesmith/src/",
    "at /ho" + "me/dev/src/",                            # `dev` is a real account, not a stand-in
]


def _run(cwd):
    return subprocess.run([sys.executable, *GESTURE], cwd=str(cwd), capture_output=True, text=True)


def _git(repo, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, env=env)


def _scratch(tmp_path, plant=None, where="docs.md"):
    """A git checkout holding the gate and its rules, origin on GitHub under `acme-co/demo`, whose
    doctor.py documents `acme-co/demo-public` as the public slug."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "skills" / "agrim-loop" / "scripts").mkdir(parents=True)
    (repo / "skills" / "agrim-doctor" / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "leak_scan.py", repo / "tools" / "leak_scan.py")
    shutil.copy(ROOT / "skills" / "agrim-loop" / "scripts" / "scrub.py",
                repo / "skills" / "agrim-loop" / "scripts" / "scrub.py")
    (repo / "skills" / "agrim-doctor" / "scripts" / "doctor.py").write_text(
        '_MARKETPLACE_REPO = "' + OWNER + '/demo-public"\n', encoding="utf-8")
    (repo / "README.md").write_text(
        "# demo\n[CI](https://github.com/" + OWNER + "/demo/actions)\n", encoding="utf-8")
    if plant is not None:
        target = repo / where
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("line one\n" + plant + "\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "https://github.com/" + OWNER + "/demo.git")
    _git(repo, "add", "-A")
    return repo


def test_this_tree_has_no_leak_through_the_documented_gesture():
    proc = _run(ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "leak_scan: 0 finding(s)" in proc.stdout


def test_the_gate_scans_every_tracked_file_including_sdlc_and_tests():
    """No directory is exempt: the summary's file count is `git ls-files`' count minus skips."""
    listed = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True)
    tracked = [p for p in listed.stdout.splitlines() if (ROOT / p).is_file()]
    proc = _run(ROOT)
    summary = proc.stdout.strip().splitlines()[-1]
    m = re.search(r"over (\d+) file\(s\) \((\d+) binary", summary)
    scanned, skipped = int(m.group(1)), int(m.group(2))
    assert scanned + skipped == len(tracked), summary


def test_a_clean_scratch_checkout_is_green_and_its_own_badge_url_passes(tmp_path):
    proc = _run(_scratch(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("rule", sorted(_PLANTS))
def test_control_each_planted_leak_is_red_by_location_never_value(tmp_path, rule):
    where, plant = _PLANTS[rule]
    repo = _scratch(tmp_path, plant, where)
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"{where}:2: {rule}" in proc.stdout, proc.stdout
    value = "jdoesmith" if rule == "home-path" else plant.split()[-1]
    assert value not in proc.stdout + proc.stderr
    (repo / where).unlink()                                     # ... and green once removed
    _git(repo, "rm", "-q", "--cached", where)
    assert _run(repo).returncode == 0


@pytest.mark.parametrize("where", [".sdlc/plans/9.md", "tests/test_fixture.py"])
def test_sdlc_and_tests_are_shipped_surface_and_scanned(tmp_path, where):
    proc = _run(_scratch(tmp_path, _PLANTS["gh-token"][1], where))
    assert proc.returncode == 1 and f"{where}:2: gh-token" in proc.stdout, proc.stdout


@pytest.mark.parametrize("url", _OWNER_URLS)
def test_control_every_spelling_of_an_owner_link_is_red(tmp_path, url):
    proc = _run(_scratch(tmp_path, "see " + url))
    assert proc.returncode == 1 and "docs.md:2: origin-owner-url" in proc.stdout, url


@pytest.mark.parametrize("url", _OWNER_URLS_OK)
def test_this_repo_and_the_documented_public_slug_are_allowed(tmp_path, url):
    proc = _run(_scratch(tmp_path, "see " + url))
    assert proc.returncode == 0, url + "\n" + proc.stdout


@pytest.mark.parametrize("line", _HOME_FORMS)
def test_control_every_home_path_form_is_red(tmp_path, line):
    proc = _run(_scratch(tmp_path, line))
    assert proc.returncode == 1 and "docs.md:2: home-path" in proc.stdout, line


def test_placeholders_identifiers_and_prose_are_not_findings(tmp_path):
    plant = "\n".join([
        "run it from /Users/you/src or /home/<user>/x or /Users/.../sigma/.git or $HOME",
        "`~nosuchuser42/notes.md` is the unresolvable-token example; see (~line 30) and HEAD~1",
        'TIER1_TOKEN = "feature-classify-tier1"',
        'BACKLOG_TOKEN = "/issues?state=open&per_page=100&page="',
        "the marker -----BEGIN RSA PRIV" + "ATE KEY----- alone, with no key body",
        "Basic authentication and a bearer token are both supported",
        "token: str annotations are code, not config",
    ])
    proc = _run(_scratch(tmp_path, plant))
    assert proc.returncode == 0, proc.stdout


def test_unquoted_assignment_is_config_only(tmp_path):
    line = "pass" + "word: " + "Hunter2Hunter2X9zz"
    assert _run(_scratch(tmp_path, line, "notes.md")).returncode == 0
    assert _run(_scratch(tmp_path / "b", line, ".env.example")).returncode == 1


def test_allow_marker_suppresses_exactly_its_rule_on_exactly_its_line(tmp_path):
    home = "/Us" + "ers/jdoesmith/app"
    marker = "  # leak-scan" + ": allow home-path planted fixture for the home rule"
    plant = "\n".join([home + marker, home, "x " + _SECRET + marker])
    proc = _run(_scratch(tmp_path, plant))
    assert proc.returncode == 1
    assert "docs.md:2: home-path" not in proc.stdout             # suppressed by its own marker
    assert "docs.md:3: home-path" in proc.stdout                 # the next line is not covered
    assert "docs.md:4: gh-token" in proc.stdout                  # a marker names ONE rule


@pytest.mark.parametrize("marker", ["allow home-path", "allow no-such-rule because fixture",
                                    "allow"])
def test_a_malformed_allow_marker_is_itself_a_finding(tmp_path, marker):
    """No reason, an unknown rule, or no rule at all: the marker disarms nothing and is reported."""
    plant = "/Us" + "ers/jdoesmith/app  # leak-scan" + ": " + marker
    proc = _run(_scratch(tmp_path, plant))
    assert proc.returncode == 1
    assert "docs.md:2: allow-marker-malformed" in proc.stdout
    assert "docs.md:2: home-path" in proc.stdout


def test_missing_rules_refuse_rather_than_report_zero(tmp_path):
    repo = _scratch(tmp_path)
    scrub = repo / "skills" / "agrim-loop" / "scripts" / "scrub.py"
    scrub.write_text("SHAPE_RULES = ()\n_SECRET_PATTERNS = ()\n", encoding="utf-8")
    proc = _run(repo)
    assert proc.returncode == 2 and "REFUSED" in proc.stderr, proc.stdout + proc.stderr
    assert "0 finding(s)" not in proc.stdout


def test_a_missing_redactor_table_refuses_too(tmp_path):
    """The redactor-only shapes (PEM, AWS, classic gh tokens, JWT) come from `_SECRET_PATTERNS`: a
    scrub.py without it must refuse, not silently drop those classes."""
    repo = _scratch(tmp_path)
    scrub = repo / "skills" / "agrim-loop" / "scripts" / "scrub.py"
    scrub.write_text(scrub.read_text(encoding="utf-8").replace("_SECRET_PATTERNS", "_RENAMED"),
                     encoding="utf-8")
    proc = _run(repo)
    assert proc.returncode == 2 and "REFUSED" in proc.stderr, proc.stdout + proc.stderr


def test_a_large_file_is_skipped_by_name_never_silently(tmp_path, monkeypatch):
    repo = _scratch(tmp_path)
    src = (repo / "tools" / "leak_scan.py").read_text(encoding="utf-8")
    (repo / "tools" / "leak_scan.py").write_text(
        src.replace("MAX_BYTES = 4 * 1024 * 1024", "MAX_BYTES = 64"), encoding="utf-8")
    (repo / "big.md").write_text("x" * 100 + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    proc = _run(repo)
    assert "big.md: skipped (large" in proc.stderr, proc.stderr

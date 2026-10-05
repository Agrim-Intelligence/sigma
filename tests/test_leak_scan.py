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
_PEM_BODY = "MIIEow" + "Ab9+" * 16
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
    "config-credential": ("config.yml", "pass" + "word: " + "Hunter2Hunter2X9zz"),
    "private-key": ("docs.md", "-----BEGIN RSA PRIV" + "ATE KEY-----\n" + _PEM_BODY
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
    "ssh://git@ssh." + "github.com:443/" + OWNER + "/internal-ops.git",  # SSH over the HTTPS port
    "https://github.com:443/" + OWNER + "/internal-ops",
    "https://www.github.com/" + OWNER + "/internal-ops",
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
    "wsl sees /mnt/c/Us" + "ers/jdoesmith/src",          # WSL's view of a Windows profile
    "/System/Volumes/Data/Us" + "ers/jdoesmith/src",     # macOS's firmlinked data volume
    "c:" + "\\users\\" + "jdoesmith\\appdata",           # Windows paths are case-insensitive
    "c:/" + "users/jdoesmith/src",
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
    (repo / "skills" / "sigma-loop" / "scripts").mkdir(parents=True)
    (repo / "skills" / "sigma-doctor" / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "leak_scan.py", repo / "tools" / "leak_scan.py")
    shutil.copy(ROOT / "skills" / "sigma-loop" / "scripts" / "scrub.py",
                repo / "skills" / "sigma-loop" / "scripts" / "scrub.py")
    (repo / "skills" / "sigma-doctor" / "scripts" / "doctor.py").write_text(
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
    m = re.search(r"over (\d+) file\(s\) \((\d+) unscannable", summary)
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
    value = {"home-path": "jdoesmith", "private-key": _PEM_BODY}.get(rule, plant.split()[-1])
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
    scrub = repo / "skills" / "sigma-loop" / "scripts" / "scrub.py"
    scrub.write_text("SHAPE_RULES = ()\n_SECRET_PATTERNS = ()\n", encoding="utf-8")
    proc = _run(repo)
    assert proc.returncode == 2 and "REFUSED" in proc.stderr, proc.stdout + proc.stderr
    assert "0 finding(s)" not in proc.stdout


def test_a_missing_redactor_table_refuses_too(tmp_path):
    """The redactor-only shapes (PEM, AWS, classic gh tokens, JWT) come from `_SECRET_PATTERNS`: a
    scrub.py without it must refuse, not silently drop those classes."""
    repo = _scratch(tmp_path)
    scrub = repo / "skills" / "sigma-loop" / "scripts" / "scrub.py"
    scrub.write_text(scrub.read_text(encoding="utf-8").replace("_SECRET_PATTERNS", "_RENAMED"),
                     encoding="utf-8")
    proc = _run(repo)
    assert proc.returncode == 2 and "REFUSED" in proc.stderr, proc.stdout + proc.stderr


def test_a_large_file_is_skipped_by_name_never_silently(tmp_path):
    repo = _scratch(tmp_path)
    src = (repo / "tools" / "leak_scan.py").read_text(encoding="utf-8")
    (repo / "tools" / "leak_scan.py").write_text(
        src.replace("MAX_BYTES = 4 * 1024 * 1024", "MAX_BYTES = 64 * 1024"), encoding="utf-8")
    (repo / "big.md").write_text("x" * (65 * 1024) + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    proc = _run(repo)
    assert proc.returncode == 1 and "big.md:0: oversize" in proc.stdout, proc.stdout + proc.stderr
    assert "1 unscannable" in proc.stdout



# ---------------------------------------------------------------- review block #2 (#277): the classes

_VAL = "Zq81" + "Xw7Pk3Lm9Rt2"                                    # digit + letter, 16 chars
_AWS_SECRET = "wJalrXUtnFEMI/" + "K7MDENG/bPxRfiCY" + "EXAMPLEKEY"
#: A KEY that is a whole identifier ENDING in a credential word, in every config-like file form.
_CONFIG_CREDENTIALS = [
    (".env.example", "GITHUB_" + "TOKEN=" + _VAL),
    (".env.example", "OPENAI_API_" + "KEY=" + _VAL),
    (".env.example", "DB_PASS" + "WORD=" + _VAL),
    (".env.example", "AWS_SECRET_ACCESS_" + "KEY=" + _AWS_SECRET),
    (".env.example", "SLACK_BOT_" + "TOKEN=" + _VAL),
    (".env.example", "export GITHUB_" + "TOKEN=" + _VAL),
    ("config.yml", "github_" + "token: " + _VAL),
    ("config.yml", "openai_api_" + "key: " + _VAL),
    ("config.yaml", "  db_pass" + "word: '" + _VAL + "'"),
    ("settings.ini", "aws_secret_access_" + "key = " + _AWS_SECRET),
    ("app.cfg", "client_" + "secret=" + _VAL),
    ("app.conf", "smtp_pass" + "wd " + "= " + _VAL),
    ("pyproject.toml", 'db_pass' + 'word = "' + _VAL + '"'),
    ("app.json", '{"openai_api_' + 'key": "' + _VAL + '"}'),
    ("app.properties", "db.pass" + "word=" + _VAL),
    ("Dockerfile", "ENV API_" + "KEY=" + _VAL),
    ("Dockerfile", "ARG NPM_" + "TOKEN " + _VAL),
    ("build/api.dockerfile", "ENV API_" + "KEY=" + _VAL),
    ("ci/project.npmrc", "//registry.npmjs.org/:_auth" + "Token=" + _VAL),
    ("deploy.sh", "export GITHUB_" + "TOKEN=" + _VAL),
    (".npmrc", "//registry.npmjs.org/:_auth" + "Token=" + _VAL),
]
#: The same keys holding a placeholder, a reference, or nothing: never a finding.
_CONFIG_PLACEHOLDERS = [
    "GITHUB_" + "TOKEN=",
    "GITHUB_" + "TOKEN=${GITHUB_TOKEN}",
    "OPENAI_API_" + "KEY=<your key>",
    "OPENAI_API_" + "KEY=your-openai-key-20240101",   # a stand-in, whatever digits it carries
    "DB_PASS" + "WORD=changeme",
    "api_" + "key: your-api-key-here",
    "SLACK_BOT_" + "TOKEN='${SLACK_BOT_TOKEN}'",
    "db_pass" + "word: example",
    "max_" + "tokens: 4096",                         # a count, not a credential
    "token_budget: " + _VAL,                         # the key does not END in a credential word
    "TOKEN=$(gh auth token)",
]


@pytest.mark.parametrize("where,line", _CONFIG_CREDENTIALS,
                         ids=[f"{w}:{l.split('=')[0].split(':')[0][:24]}" for w, l in _CONFIG_CREDENTIALS])
def test_control_a_prefixed_credential_key_in_config_is_red(tmp_path, where, line):
    proc = _run(_scratch(tmp_path, line, where))
    assert proc.returncode == 1 and f"{where}:2: config-credential" in proc.stdout, proc.stdout
    assert _VAL not in proc.stdout + proc.stderr and _AWS_SECRET not in proc.stdout + proc.stderr


@pytest.mark.parametrize("line", _CONFIG_PLACEHOLDERS)
def test_a_credential_key_holding_a_placeholder_is_not_a_finding(tmp_path, line):
    proc = _run(_scratch(tmp_path, line, ".env.example"))
    assert proc.returncode == 0, line + "\n" + proc.stdout


def _write(repo, rel, data):
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.write_bytes if isinstance(data, bytes) else
     lambda d: target.write_text(d, encoding="utf-8"))(data)
    _git(repo, "add", "-A")


_KEY_BODY = "\n".join(["MIIEpAIBAAKCAQEA" + "x9Kq2Lm8Rt4Vw6Yz1Ab3Cd5Ef7Gh9Ij0Kl2Mn4Op6Qr8St0U",
                       "v2Wx4Yz6Ab8Cd0Ef" + "2Gh4Ij6Kl8Mn0Op2Qr4St6Uv8Wx0Yz2Ab4Cd6Ef8Gh0Ij2K",
                       "l4Mn6Op8Qr0St2Uv" + "4Wx6Yz8Ab0Cd2Ef4Gh6Ij8Kl0Mn2Op4Qr6St8Uv0Wx2Yz4A"]) + "\n"


@pytest.mark.parametrize("rel,data", [
    ("certs/id_rsa", "x"),
    ("keys/id_ed25519", "x"),
    ("certs/server.pem", "x"),
    ("certs/tls.key", "x"),
    ("certs/deploy.p12", b"\x30\x82\x01\x00\x02\x01\x03\x00"),
    ("certs/app.pfx", "x"),
    ("certs/keystore.jks", "x"),
    ("home/.netrc", "machine example.org login me"),
    ("gcp/credentials.json", "{}"),
    ("gcp/service-account-prod.json", "{}"),
    (".env", "DEBUG=1"),
    ("app/.env.production", "DEBUG=1"),
])
def test_control_a_secret_shaped_filename_is_red_whatever_it_holds(tmp_path, rel, data):
    repo = _scratch(tmp_path)
    _write(repo, rel, data)
    proc = _run(repo)
    assert proc.returncode == 1 and f"{rel}:0: secret-file" in proc.stdout, proc.stdout


@pytest.mark.parametrize("rel", ["keys/id_rsa.pub", ".env.example", ".env.sample", "app/.env.template",
                                 "docs/keys.md", "hooks/session_start.sh"])
def test_a_public_or_example_filename_is_not_a_secret_file(tmp_path, rel):
    repo = _scratch(tmp_path)
    _write(repo, rel, "DEBUG=1\n")
    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout


def test_control_a_key_body_with_no_header_is_red_by_location_never_value(tmp_path):
    proc = _run(_scratch(tmp_path, _KEY_BODY.rstrip("\n"), "certs/notes.txt"))
    assert proc.returncode == 1 and "certs/notes.txt:2: key-body" in proc.stdout, proc.stdout
    assert _KEY_BODY.splitlines()[0] not in proc.stdout + proc.stderr


@pytest.mark.parametrize("nearby", [
    "This guide mentions -----BEGIN PRIVATE KEY----- but contains no PEM block.\n",
    "Comment: prose about private-key armor, not a PEM header.\n",
])
def test_control_nearby_prose_cannot_exempt_a_headerless_key_body(tmp_path, nearby):
    """The documented gesture must reject the reviewer's exact nearby-prose bypass."""
    proc = _run(_scratch(tmp_path, nearby + _KEY_BODY.rstrip("\n"), "certs/notes.txt"))
    assert proc.returncode == 1 and "certs/notes.txt:3: key-body" in proc.stdout, proc.stdout
    assert _KEY_BODY.splitlines()[0] not in proc.stdout + proc.stderr


def test_control_a_public_header_with_a_blank_line_cannot_exempt_a_key_body(tmp_path):
    plant = "-----BEGIN CERTIFICATE-----\n\n" + _KEY_BODY.rstrip("\n")
    proc = _run(_scratch(tmp_path, plant, "certs/notes.txt"))
    assert proc.returncode == 1 and "certs/notes.txt:4: key-body" in proc.stdout, proc.stdout


def test_a_public_certificate_body_and_hash_lists_are_not_key_bodies(tmp_path):
    cert_body = _KEY_BODY.replace("MIIEpAIBAAKCAQEA", "MIIDdzCCAl+gAwIB", 1)   # a certificate's DER header, not PKCS1's
    cert = "-----BEGIN CERTIFICATE-----\n" + cert_body + "-----END CERTIFICATE-----"
    shas = "\n".join(["0123456789abcdef0123456789abcdef01234567"] * 4)
    proc = _run(_scratch(tmp_path, cert + "\n" + shas))
    assert proc.returncode == 0, proc.stdout


def test_control_an_opaque_binary_is_named_and_red_unless_allowed_with_a_reason(tmp_path):
    repo = _scratch(tmp_path)
    _write(repo, "assets/blob.bin", b"\x00\x01\x02" + _SECRET.encode())
    proc = _run(repo)
    assert proc.returncode == 1 and "assets/blob.bin:0: opaque-binary" in proc.stdout, proc.stdout
    gate = repo / "tools" / "leak_scan.py"
    src = gate.read_text(encoding="utf-8")
    gate.write_text(src.replace("ALLOW_PATHS = {}", 'ALLOW_PATHS = {"assets/blob.bin": ""}'),
                    encoding="utf-8")
    refused = _run(repo)
    assert refused.returncode == 2 and "REFUSED" in refused.stderr, refused.stdout + refused.stderr
    gate.write_text(src.replace("ALLOW_PATHS = {}",
                                'ALLOW_PATHS = {"assets/blob.bin": "vendored test vector"}'),
                    encoding="utf-8")
    assert _run(repo).returncode == 0


@pytest.mark.parametrize("encoding", ["latin-1", "utf-16", "utf-16-le", "utf-8-sig"])
def test_control_non_utf8_text_is_decoded_and_scanned(tmp_path, encoding):
    repo = _scratch(tmp_path)
    _write(repo, "notes.md", ("café " + _SECRET + "\n").encode(encoding))
    proc = _run(repo)
    assert proc.returncode == 1 and "notes.md:1: gh-token" in proc.stdout, proc.stdout


def test_control_a_symlink_is_scanned_as_the_path_git_ships_never_followed(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x " + _SECRET + "\n", encoding="utf-8")   # the target is NOT shipped surface
    repo = _scratch(tmp_path)
    (repo / "to_outside").symlink_to(outside)
    _git(repo, "add", "-A")
    assert _run(repo).returncode == 0                                   # never read through the link
    (repo / "dangling").symlink_to("/Us" + "ers/jdoesmith/private/notes")
    _git(repo, "add", "-A")
    proc = _run(repo)
    assert proc.returncode == 1 and "dangling:1: home-path" in proc.stdout, proc.stdout


def test_missing_git_refuses_with_exit_2_not_a_traceback(tmp_path):
    repo = _scratch(tmp_path)
    empty = tmp_path / "nobin"
    empty.mkdir()
    proc = subprocess.run([sys.executable, *GESTURE], cwd=str(repo), capture_output=True, text=True,
                          env={**os.environ, "PATH": str(empty)})
    assert proc.returncode == 2 and "REFUSED" in proc.stderr and "Traceback" not in proc.stderr, \
        proc.stderr


def _allow_count(proc):
    return int(re.search(r"(\d+) allow-marked line\(s\)", proc.stdout).group(1))


def test_the_summary_counts_allow_marked_lines(tmp_path):
    marker = "  # leak-scan" + ": allow home-path planted fixture for the home rule"
    base = _run(_scratch(tmp_path / "a"))
    proc = _run(_scratch(tmp_path / "b", "/Us" + "ers/jdoesmith/app" + marker))
    assert proc.returncode == 0 and _allow_count(proc) == _allow_count(base) + 1, proc.stdout


def test_an_origin_spelled_over_ssh_port_443_still_names_the_owner(tmp_path):
    repo = _scratch(tmp_path, "see https://github.com/" + OWNER + "/internal-ops")
    _git(repo, "remote", "set-url", "origin", "ssh://git@ssh." + "github.com:443/" + OWNER + "/demo.git")
    proc = _run(repo)
    assert proc.returncode == 1 and "docs.md:2: origin-owner-url" in proc.stdout, proc.stdout


def test_a_url_path_that_merely_contains_home_is_not_a_home_path(tmp_path):
    proc = _run(_scratch(tmp_path, "see https://example.com/home/jdoesmith and /api/users/jdoesmith"))
    assert proc.returncode == 0, proc.stdout



def gate_only_claims_missing(scrub_text, gate_text):
    """Every backticked name in scrub.py's GATE-ONLY paragraph must exist in the gate's source: the
    paragraph once named `SECRET_FIXTURE_VALUES` and a `KEY_WINDOW` rule the gate never had."""
    para = scrub_text.split("GATE-ONLY", 1)[1].split("DEFERRED", 1)[0]
    return sorted({n for n in re.findall(r"`([\w./-]+)`", para) if n not in gate_text})


def test_scrub_names_only_gate_rules_that_exist():
    scrub = (ROOT / "skills/sigma-loop/scripts/scrub.py").read_text(encoding="utf-8")
    gate = (ROOT / "tools/leak_scan.py").read_text(encoding="utf-8")
    assert gate_only_claims_missing(scrub, gate) == []
    planted = scrub.replace("GATE-ONLY, deliberately not here", "GATE-ONLY, `SECRET_FIXTURE_VALUES`")
    assert gate_only_claims_missing(planted, gate) == ["SECRET_FIXTURE_VALUES"]

"""Pins what the sigma-cloud environment's setup script must keep true (#2090).

Nothing runs `tools/cloud/setup.sh` for real except the cloud host, as root, before each session
starts. A syntax error, `set -e` or a non-zero exit there fails every session at "Setup script
failed" before Claude Code starts, and a verify string that drifts from the one the operator
approved makes every goal refuse at verify. These checks catch both before the operator loads the
file. `bash -n` never parses a quoted heredoc's body, so the Python the script runs is compiled and
run on its own here.
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "cloud" / "setup.sh"
README = ROOT / "tools" / "cloud" / "README.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
TEMPLATE = ROOT / "skills" / "sigma-init" / "templates" / "config.json.tmpl"
TEXT = SCRIPT.read_text()
VERIFY = re.search(r'^VERIFY="([^"$`\\]*)"$', TEXT, re.MULTILINE).group(1)
TIMEOUT = int(re.search(r"^VERIFY_TIMEOUT_MINUTES=(\d+)$", TEXT, re.MULTILINE).group(1))
CONFIG_BLOCK = TEXT.split("# >>> sigma-cloud config\n", 1)[1].split("# <<< sigma-cloud config\n", 1)[0]

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash on this host")
posix_only = pytest.mark.skipif(os.name != "posix", reason="the script targets a Linux VM")


def _write_config(sdlc):
    env = {**os.environ, "TEMPLATE": str(TEMPLATE), "SDLC": str(sdlc), "VERIFY": VERIFY,
           "VERIFY_TIMEOUT_MINUTES": str(TIMEOUT)}
    subprocess.run([sys.executable, "-c", CONFIG_BLOCK], env=env, check=True, timeout=60)
    return json.loads((sdlc / "config.json").read_text())


@needs_bash
def test_parses_as_bash():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_never_stops_on_an_error_and_ends_with_exit_0():
    code = [line for line in re.sub(r"<<'EOF'\n.*?^EOF$", "", TEXT, flags=re.MULTILINE | re.DOTALL).splitlines()
            if not line.lstrip().startswith("#")]
    assert TEXT.splitlines()[0] == "#!/bin/bash"
    assert not [line for line in code if re.search(r"^\s*set\s+-[A-Za-z]*e|\berrexit\b", line)]
    assert not [line for line in code if re.search(r"\bexit\b(?!\s+0\b)", line)]
    assert TEXT.rstrip().splitlines()[-1] == "exit 0"


def test_verify_skips_exactly_the_files_ci_nightly_skips():
    """The owner chose the suite minus CI's slow files; a file CI adds or drops must not leave the cloud
    verify silently running a different suite."""
    slow = CI.read_text().split("      SLOW: >-\n", 1)[1].split("    steps:", 1)[0]
    assert re.findall(r"--ignore=(\S+)", VERIFY) == re.findall(r"--ignore=(\S+)", slow)
    assert len(re.findall(r"--ignore=", VERIFY)) == 6
    assert VERIFY.startswith("python3 -m pytest tests/ ")


def test_verify_asks_for_two_workers_and_names_failures():
    """`-n auto` ran one worker for 2h16m in the first diagnostic verify: this suite's conftest answers it
    with a quarter of available memory at 2000 MB a worker. A literal `-n N` is what neither that hook nor
    the guard's cold ring lowers, so the count is spelled the way the guard itself reads it; two, because a
    re-verify in the same container holds unless the first run's whole peak fits the guard's room. pytest's `-r`
    replaces its default `fE` and prints in the letters' order; Sigma keeps the last five lines as evidence,
    so failures come last."""
    spec = importlib.util.spec_from_file_location(
        "cloud_setup_resource_verify", ROOT / "skills" / "sigma-loop" / "scripts" / "resource_verify.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    assert guard.literal_workers(VERIFY) == 2
    assert not re.search(r"(?<!\S)(-n|--numprocesses)[ =]?auto\b", VERIFY)
    assert re.findall(r"(?<!\S)-r(\S+)", VERIFY) == ["sEf"]


def test_readme_quotes_the_verify_command_verbatim():
    """The operator's approval value is the hash of this exact string."""
    assert f"`{VERIFY}`" in README.read_text()


def test_config_carries_the_cloud_choices_and_nothing_else(tmp_path):
    config = _write_config(tmp_path / ".sdlc")
    template = json.loads(TEMPLATE.read_text())

    assert config["verify"]["command"] == VERIFY
    assert config["verify"]["enforce"] is True
    assert config["verify"]["timeout_minutes"] == TIMEOUT and 60 < TIMEOUT <= 1440  # loop.py accepts 1..1440
    assert config["discovery"]["source"] == "github"
    assert config["discovery"]["github"]["repo"] == "Agrim-Intelligence/sigma"
    assert config["discovery"]["github"]["assignee"] == "@me"
    assert config["discovery"]["github"]["project"]["enabled"] is False
    assert config["ledger"]["enabled"] is False
    assert config["model_selection"] == "off"
    # `--land` refuses `auto_merge: off`. Here a run still ends parked: the ruleset reports every PR blocked.
    assert config["work"]["enabled"] is True
    assert config["work"]["auto_merge"] == "protected"
    assert config["work"]["require_review"] == "approval"

    changed = {("verify", "command"), ("verify", "enforce"), ("verify", "timeout_minutes"),
               ("discovery", "source"), ("discovery", "github", "repo"), ("discovery", "github", "assignee"),
               ("discovery", "github", "project", "enabled"), ("ledger", "enabled"), ("model_selection",),
               ("work", "enabled"), ("work", "auto_merge"), ("work", "require_review")}

    def leaves(node, path=()):
        if isinstance(node, dict):
            for key, value in node.items():
                yield from leaves(value, path + (key,))
        else:
            yield path, node

    assert {p: v for p, v in leaves(config) if p not in changed} == \
        {p: v for p, v in leaves(template) if p not in changed}


def test_project_md_is_named_and_kept_across_runs(tmp_path):
    sdlc = tmp_path / ".sdlc"
    _write_config(sdlc)
    project = (sdlc / "project.md").read_text()
    assert "# Project: sigma" in project and "{{" not in project
    (sdlc / "project.md").write_text("kept\n")
    (sdlc / "config.json").write_text("{}\n")
    assert _write_config(sdlc)["verify"]["command"] == VERIFY  # the config is the script's to rewrite
    assert (sdlc / "project.md").read_text() == "kept\n"        # the project notes are not


STUBS = {
    "git": 'case "$*" in *--version*) echo "git version 2.43.0" ;; *) exit 1 ;; esac',
    "curl": "exit 22",
    "gpg": "exit 2",
    "apt-get": "exit 100",
    "apt-cache": "exit 100",
    "dpkg": "exit 2",
    "timeout": '[ "$1" = -k ] && shift 2; shift; exec "$@"',
    "claude": "exit 1",
    "uv": "exit 2",
    "python3": "exit 1",
}
REAL = ("awk", "cat", "find", "grep", "head", "install", "readlink", "rm", "sort", "tail", "which")
PPA_KEY = re.search(r"^GIT_PPA_KEY=([0-9A-F]{40})$", TEXT, re.MULTILINE).group(1)


def _run_offline(tmp_path, python_dirs=0, checkout=True, stubs=None, env=None, template_text=None):
    """Run the whole script unprivileged and offline: every tool it calls is a failing stub, system paths
    are moved into tmp_path. With `python_dirs`, that many PATH directories each hold a `python3` that is
    this interpreter but refuses `-m pip` (nothing is installed from the network), and HOME holds a plugin
    cache with the repository's own templates, so section 4 really runs. `checkout=False` is a container
    where no checkout was found. `stubs` replaces named stubs; `env` adds variables they read;
    `template_text` replaces the cached config template."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {**STUBS, **(stubs or {})}.items():
        if name == "python3" and python_dirs:
            continue
        (bin_dir / name).write_text(f"#!/bin/bash\n{body}\n")
        (bin_dir / name).chmod(0o755)
    for name in REAL:
        real = shutil.which(name)
        if real is None:
            pytest.skip(f"no {name} on this host")
        (bin_dir / name).symlink_to(real)
    path = []
    for i in range(python_dirs):
        py_dir = tmp_path / f"py{i}"
        py_dir.mkdir()
        (py_dir / "python3").write_text(
            f'#!/bin/bash\ncase " $* " in *" -m pip "*) exit 1 ;; esac\nexec "{sys.executable}" "$@"\n')
        (py_dir / "python3").chmod(0o755)
        path.append(str(py_dir))
    home = tmp_path / "home"
    if python_dirs:
        cached = home / ".claude/plugins/cache/sigmaloop/sigmaloop/9.9.9/skills/sigma-init/templates"
        shutil.copytree(TEMPLATE.parent, cached)
        if template_text is not None:
            (cached / "config.json.tmpl").write_text(template_text)
    repo_line = f"REPO={tmp_path / 'repo'}" if checkout else "REPO="
    text = (re.sub(r"^REPO=.*$", lambda _: repo_line, TEXT, count=1, flags=re.MULTILINE)
              .replace("/etc/apt/keyrings", str(tmp_path / "keyrings"))
              .replace("/etc/apt/sources.list.d", str(tmp_path / "sources")))
    assert "/etc/" not in text and "/home/" not in text
    (tmp_path / "setup.sh").write_text(text)
    result = subprocess.run([shutil.which("bash"), str(tmp_path / "setup.sh")], capture_output=True, text=True,
                            timeout=120, env={"PATH": os.pathsep.join(path + [str(bin_dir)]), "HOME": str(home),
                                              "LOG": str(tmp_path / "calls.log"), **(env or {})})
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


@needs_bash
@posix_only
def test_every_failed_step_warns_and_setup_still_succeeds(tmp_path):
    """With every tool failing it must still exit 0 (a non-zero exit fails the session) and say what failed."""
    lines = _run_offline(tmp_path)
    warnings = [line for line in lines if line.startswith("sigma-cloud: WARNING - ")]
    for expected in ("could not fetch the git-core PPA key", "git is still 2.43.0", "did not run as Python 3.10",
                     "sigmaloop install did not register", "no Sigma config template found"):
        assert [w for w in warnings if expected in w], (expected, lines)
    assert not (tmp_path / "keyrings" / "git-core-ppa.gpg").exists()
    assert not (tmp_path / "sources" / "git-core-ppa.list").exists()
    assert not (tmp_path / "repo" / ".sdlc" / "config.json").exists()


@needs_bash
@posix_only
def test_the_script_hands_its_values_to_the_config_step(tmp_path):
    """The bash-to-Python hand-off (the env prefix on the heredoc) is what the container runs; a renamed
    variable there means no config. Two PATH entries for one interpreter get one install attempt."""
    lines = _run_offline(tmp_path, python_dirs=2)
    config = json.loads((tmp_path / "repo" / ".sdlc" / "config.json").read_text())
    assert config["verify"]["command"] == VERIFY
    assert config["verify"]["timeout_minutes"] == TIMEOUT
    assert "# Project: sigma" in (tmp_path / "repo" / ".sdlc" / "project.md").read_text()
    assert [line for line in lines if ".sdlc/config.json written from" in line], lines
    per_python = [line for line in lines if "has pytest" in line or "could not install" in line]
    assert len(per_python) == 1, per_python


@needs_bash
def test_finds_the_checkout_in_whichever_home_directory_holds_it(tmp_path):
    """The repository path is found, not named: another user name on the image must still work, and a
    container with no checkout must say so instead of writing a config somewhere else."""
    line = re.search(r"^REPO=\$\(.*\)$", TEXT, re.MULTILINE).group(0).replace("/home/", f"{tmp_path}/")
    (tmp_path / "bob" / "notes").mkdir(parents=True)
    (tmp_path / "carol" / "sigma" / ".git").mkdir(parents=True)

    def found():
        return subprocess.run(["bash", "-c", f'{line}\nprintf %s "$REPO"'], capture_output=True, text=True,
                              timeout=30).stdout

    assert found() == str(tmp_path / "carol" / "sigma")
    shutil.rmtree(tmp_path / "carol")
    assert found() == ""


@needs_bash
@posix_only
def test_no_checkout_means_no_config_and_a_warning(tmp_path):
    """Run as root with an empty REPO, a config step that still ran would write /.sdlc/config.json; here
    it would only fail, so the test asserts the step never ran at all."""
    lines = _run_offline(tmp_path, python_dirs=1, checkout=False)
    assert [line for line in lines if "WARNING - found no checkout of this repository" in line], lines
    assert not [line for line in lines if "config.json written from" in line or "could not write" in line], lines
    assert not list(tmp_path.rglob("config.json"))


@needs_bash
@posix_only
def test_a_config_step_that_fails_warns_and_writes_nothing(tmp_path):
    """A template this script cannot use (an older or newer Sigma's shape) must leave no half config."""
    lines = _run_offline(tmp_path, python_dirs=1, template_text="{}\n")
    assert [line for line in lines if line.startswith("sigma-cloud: WARNING - could not write .sdlc/config.json")], lines
    assert not (tmp_path / "repo" / ".sdlc" / "config.json").exists()


def _colons(*records):
    """`gpg --with-colons` lines: a `pub`/`sub` record, then its `fpr` (field 10)."""
    return "".join(f"{kind}:-:4096:1::\nfpr:::::::::{fpr}:\n" for kind, fpr in records)


KEY_STUBS = {
    "curl": "echo key",
    "gpg": """case " $* " in
  *" --dearmor "*) while [ $# -gt 0 ] && [ "$1" != -o ]; do shift; done; cat > "$2" ;;
  *" --show-keys "*) printf "%s" "$SHOW_KEYS" ;;
esac""",
    "apt-get": 'echo "apt-get $*" >> "$LOG"',
    "apt-cache": 'echo " 500 https://ppa.launchpadcontent.net/git-core/ppa/ubuntu noble/main amd64 Packages"',
}


@needs_bash
@posix_only
@pytest.mark.parametrize("listing, trusted", [
    (_colons(("pub", PPA_KEY)), True),
    (_colons(("pub", PPA_KEY), ("sub", "B" * 40)), True),        # the pinned key with a subkey of its own
    (_colons(("pub", PPA_KEY), ("pub", "A" * 40)), False),       # a second key apt would also trust
    (_colons(("pub", "A" * 40), ("sub", PPA_KEY)), False),       # another key, the pinned one only as its subkey
    ("", False),                                                 # the fetch gave no key at all
])
def test_trusts_the_ppa_only_with_the_pinned_key_alone(tmp_path, listing, trusted):
    """The one supply-chain check in a script that runs as root: apt trusts every key in a signed-by keyring."""
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "git-core-ppa.list").write_text("left by an earlier run\n")
    lines = _run_offline(tmp_path, stubs=KEY_STUBS, env={"SHOW_KEYS": listing})
    keyring, source = tmp_path / "keyrings" / "git-core-ppa.gpg", tmp_path / "sources" / "git-core-ppa.list"
    assert keyring.exists() == trusted and source.exists() == trusted, lines
    calls = (tmp_path / "calls.log").read_text() if (tmp_path / "calls.log").exists() else ""
    if trusted:
        assert source.read_text() == (f"deb [signed-by={keyring}] "
                                      "https://ppa.launchpadcontent.net/git-core/ppa/ubuntu noble main\n")
        assert "apt-get install -y -qq git" in calls
    else:
        assert [line for line in lines if "could not fetch the git-core PPA key" in line], lines
        assert calls == ""


@needs_bash
@posix_only
@pytest.mark.parametrize("versions, expected", [
    (["1.0.24", "1.0.9"], "WARNING - sigmaloop 1.0.9 is older than"),   # numeric, not string, order
    (["1.0.24"], "sigma-cloud: sigmaloop 1.0.24 installed"),
])
def test_reports_the_oldest_installed_sigma(tmp_path, versions, expected):
    plugins = json.dumps([{"id": "sigmaloop@sigmaloop", "version": v} for v in versions])
    stubs = {"claude": 'case "$*" in *"plugin list"*) printf %s "$PLUGINS" ;; esac'}
    lines = _run_offline(tmp_path, python_dirs=1, stubs=stubs, env={"PLUGINS": plugins})
    assert [line for line in lines if expected in line], lines


@needs_bash
@posix_only
def test_warns_when_the_git_local_grant_survives_the_unset(tmp_path):
    stubs = {"git": 'case "$*" in *--version*) echo "git version 2.43.0" ;; *--get-all*) echo true ;; *) exit 1 ;; esac'}
    lines = _run_offline(tmp_path, stubs=stubs)
    assert [line for line in lines
            if line.startswith("sigma-cloud: WARNING - could not remove the checkout's git-local shell-command grant")], lines


def test_never_grants_itself_the_verify_approval():
    """Only the operator sets the approval, in the environment's settings. A setup script is code the host
    runs, which the repository-sourced tripwire cannot see (docs/cloud-sessions.md), so this pins it. git
    reads the broad git-local grant with --type=bool, so the key may appear only in its one removal and its
    one read-back, nowhere else in any literal spelling, not even later on the same line."""
    assert "SIGMA_TRUSTED_VERIFY_SHA256" not in TEXT
    code = "\n".join(line for line in TEXT.splitlines() if not line.lstrip().startswith("#"))
    allowed = ("--unset-all sigma.allowRepositoryShellCommands", "--get-all sigma.allowRepositoryShellCommands")
    for form in allowed:
        assert code.count(form) == 1, form
        code = code.replace(form, "")
    assert not re.search(r"allowrepositoryshellcommands", code, re.IGNORECASE)
    assert not re.search(r"\.bashrc|\.bash_profile|\.profile|profile\.d|\.zshrc|/etc/environment|CLAUDE_ENV_FILE",
                         TEXT)


def test_header_says_to_load_it_with_choose_file():
    assert "Choose file" in TEXT.split("\nREPO=", 1)[0]

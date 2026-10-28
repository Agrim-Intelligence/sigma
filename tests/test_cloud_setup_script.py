"""Pins what the sigma-cloud environment's setup script must keep true (#2090).

Nothing runs `tools/cloud/setup.sh` for real except the cloud host, as root, before each session
starts. A syntax error, `set -e` or a non-zero exit there fails every session at "Setup script
failed" before Claude Code starts, and a verify string that drifts from the one the operator
approved makes every goal refuse at verify. These checks catch both before the operator loads the
file. `bash -n` never parses a quoted heredoc's body, so the Python the script runs is compiled and
run on its own here.
"""
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
    # `-n auto` takes the worker count the resource guard grants (PYTEST_XDIST_AUTO_NUM_WORKERS).
    assert " -n auto " in f"{VERIFY} "


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
    # `--land` refuses `auto_merge: off`; `protected` merges because main requires the `test` check.
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
    "timeout": 'shift; exec "$@"',
    "claude": "exit 1",
    "uv": "exit 2",
    "python3": "exit 1",
}
REAL = ("awk", "cat", "find", "grep", "head", "install", "readlink", "rm", "sort", "tail", "which")


def _run_offline(tmp_path, python_dirs=0, checkout=True):
    """Run the whole script unprivileged and offline: every tool it calls is a failing stub, system paths
    are moved into tmp_path. With `python_dirs`, that many PATH directories each hold a `python3` that is
    this interpreter but refuses `-m pip` (nothing is installed from the network), and HOME holds a plugin
    cache with the repository's own templates, so section 4 really runs. `checkout=False` is a container
    where no checkout was found."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
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
        shutil.copytree(TEMPLATE.parent,
                        home / ".claude/plugins/cache/sigmaloop/sigmaloop/9.9.9/skills/sigma-init/templates")
    repo_line = f"REPO={tmp_path / 'repo'}" if checkout else "REPO="
    text = (re.sub(r"^REPO=.*$", lambda _: repo_line, TEXT, count=1, flags=re.MULTILINE)
              .replace("/etc/apt/keyrings", str(tmp_path / "keyrings"))
              .replace("/etc/apt/sources.list.d", str(tmp_path / "sources")))
    assert "/etc/" not in text and "/home/" not in text
    (tmp_path / "setup.sh").write_text(text)
    result = subprocess.run([shutil.which("bash"), str(tmp_path / "setup.sh")], capture_output=True, text=True,
                            timeout=120, env={"PATH": os.pathsep.join(path + [str(bin_dir)]), "HOME": str(home)})
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
    lines = _run_offline(tmp_path, python_dirs=1, checkout=False)
    assert [line for line in lines if "WARNING - found no checkout of this repository" in line], lines
    assert not list(tmp_path.rglob("config.json"))


def test_never_grants_itself_the_verify_approval():
    """Only the operator sets the approval, in the environment's settings. A setup script is code the host
    runs, which the repository-sourced tripwire cannot see (docs/cloud-sessions.md), so this pins it. git
    reads the broad git-local grant with --type=bool, so every spelling of the key but its removal counts."""
    assert "SIGMA_TRUSTED_VERIFY_SHA256" not in TEXT
    grant_lines = [line for line in TEXT.splitlines()
                   if re.search(r"allowrepositoryshellcommands", line, re.IGNORECASE) and not line.lstrip().startswith("#")]
    assert grant_lines and all("--unset-all" in line for line in grant_lines), grant_lines
    assert not re.search(r"\.bashrc|\.bash_profile|\.profile|profile\.d|\.zshrc|/etc/environment|CLAUDE_ENV_FILE",
                         TEXT)


def test_header_says_to_load_it_with_choose_file():
    assert "Choose file" in TEXT.split("\nREPO=", 1)[0]

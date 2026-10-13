"""D7 'every refusal prints its fix': /sigma-init, preflight, verify_detect, /sigma-define and
/sigma-doctor messages that named the problem but not the way out (triage:
docs/launch/evidence/d7-refusal-triage-complete-*.md). Each test asserts the message names the
resolving command or setting; exit codes are the sites' existing ones."""
import importlib.util, json, pathlib, subprocess, sys, tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT = ROOT / "skills" / "sigma-init" / "scripts"
DEFINE = ROOT / "skills" / "sigma-define" / "scripts"
DOCTOR = ROOT / "skills" / "sigma-doctor" / "scripts"
LOOP = ROOT / "skills" / "sigma-loop" / "scripts"


def _mod(name, where):
    spec = importlib.util.spec_from_file_location(name, where / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pf = _mod("preflight", INIT)
vd = _mod("verify_detect", INIT)
define = _mod("define", DEFINE)
doctor = _mod("doctor", DOCTOR)


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _sdlc_with(tmp_path, text):
    d = tmp_path / ".sdlc"
    d.mkdir(exist_ok=True)
    (d / "config.json").write_text(text)
    return d


# --- Codex adapter: a symlinked / malformed AGENTS.md ----------------------------------------------

@pytest.mark.parametrize("script", ["sdlc_init.py", "init_flow.py"])
def test_codex_symlinked_agents_md_is_a_refusal_with_the_fix_not_a_traceback(tmp_path, script):
    repo = _repo(tmp_path)
    (repo / "CLAUDE.md").write_text("rules\n")
    (repo / "AGENTS.md").symlink_to(repo / "CLAUDE.md")
    r = subprocess.run([sys.executable, str(INIT / script), str(repo), "--codex"],
                       capture_output=True, text=True, timeout=120)
    text = r.stdout + r.stderr
    assert "Traceback" not in text and r.returncode == 2, text
    assert "REFUSED" in text and "symlinked AGENTS.md" in text
    assert "rm AGENTS.md" in text and "re-run /sigma-init --codex" in text


def test_codex_malformed_markers_name_the_repair(tmp_path):
    repo = _repo(tmp_path)
    (repo / "AGENTS.md").write_text("<!-- sigma:codex:end -->\n")
    r = subprocess.run([sys.executable, str(INIT / "sdlc_init.py"), str(repo), "--codex"],
                       capture_output=True, text=True, timeout=120)
    text = r.stdout + r.stderr
    assert r.returncode == 2 and "Traceback" not in text
    assert "<!-- sigma:codex:start -->" in text and "re-run /sigma-init --codex" in text


# --- preflight ---------------------------------------------------------------------------------

def test_gh_auth_cannot_verify_the_host_names_local_only_and_set_url():
    row = pf._gh_unknown_host("/srv/remote/x")
    text = json.dumps(row)
    assert "preflight.py local-only" in text and "git remote set-url origin" in text


def test_gh_auth_timeout_says_to_rerun_when_the_network_is_up():
    runner = lambda argv, cwd, timeout: (124, "gh auth status: timed out after 15s")   # noqa: E731
    row, _ = pf.check_gh_auth("github.com", runner, 15)
    text = json.dumps(row)
    assert row["ok"] is None and "timed out" in text and "re-run" in text.lower() and "gh auth status" in text


@pytest.mark.parametrize("verb,extra", [("local-only", []), ("use-remote", ["origin"])])
def test_preflight_on_a_corrupt_config_names_the_file_and_the_fix(tmp_path, capsys, verb, extra):
    _repo(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://example.com/o/r.git"],
                   check=True)
    d = _sdlc_with(tmp_path, '{"work": ')
    assert pf.main(["preflight.py", verb, str(d), *extra]) == 2
    err = capsys.readouterr().err
    assert "config.json is not valid JSON" in err and "/sigma-init" in err and "config.json.bak" in err


# --- verify_detect -----------------------------------------------------------------------------

def test_verify_command_file_that_cannot_be_read_names_the_path_rule_and_the_alternative(tmp_path):
    d = _sdlc_with(tmp_path, json.dumps({"verify": {"command": "", "enforce": False}}))
    r = subprocess.run([sys.executable, str(INIT / "verify_detect.py"), "set", str(d),
                        "--command-file", "nope.txt"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 2 and "cannot read nope.txt" in r.stderr
    assert "relative to the current directory" in r.stderr and "verify_detect.py set" in r.stderr


def test_trust_write_failure_does_not_blame_a_missing_worktree(tmp_path, monkeypatch, capsys):
    d = _sdlc_with(tmp_path, json.dumps({"verify": {"command": "", "enforce": False}}))
    monkeypatch.setattr(vd, "trust_confirmed_repository_shell_commands",
                        lambda sdlc: (False, "Git-local trust could not be recorded"))
    assert vd.main(["verify_detect.py", "set", str(d), "pytest -q"]) == 2
    err = capsys.readouterr().err
    assert ".git/config" in err and "writable" in err and "until this is a Git worktree" not in err


def test_trust_refusal_outside_a_worktree_keeps_its_worktree_tail(tmp_path, monkeypatch, capsys):
    d = _sdlc_with(tmp_path, json.dumps({"verify": {"command": "", "enforce": False}}))
    monkeypatch.setattr(vd, "trust_confirmed_repository_shell_commands",
                        lambda sdlc: (False, "the scaffold directory is not inside a Git worktree"))
    assert vd.main(["verify_detect.py", "set", str(d), "pytest -q"]) == 2
    assert "until this is a Git worktree" in capsys.readouterr().err


# --- /sigma-define -----------------------------------------------------------------------------

def test_define_without_a_name_says_the_flag_is_required():
    ok, why = define.validate(None, "feature")
    assert not ok and "--name <name> is required" in why


def test_define_without_a_type_says_the_flag_is_required():
    ok, why = define.validate("voice", None)
    assert not ok and "--type <feature|bug|refactor> is required" in why


class _FailingRun:
    def __init__(self, token, message):
        self.token, self.message = token, message

    def __call__(self, cwd, argv):
        if self.token in " ".join(map(str, argv)):
            raise RuntimeError(self.message)
        return ""


@pytest.mark.parametrize("token,expect", [
    ("push", "git remote -v"),
    ("label create", "gh auth status"),
])
def test_define_step_failures_name_the_check(tmp_path, token, expect):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    report = define.open_unit(str(sdlc), "voice", "feature", run=_FailingRun(token, "boom"), cwd=tmp_path, base="main")
    assert not report["ok"] and "boom" in report["why"] and expect in report["why"]


def test_define_declare_on_a_local_backlog_says_nothing_needs_doing(tmp_path, capsys):
    d = _sdlc_with(tmp_path, '{"discovery": {"source": "local-goals"}}')
    (d / "goals").mkdir()
    assert define.main(["define.py", "declare", str(d), "--unit", "voice", "--issues", "1"]) == 1
    why = json.loads(capsys.readouterr().out.strip())["why"]
    assert "Nothing to do in local-goals mode" in why


def test_define_bump_priority_on_a_local_backlog_names_set_priority(tmp_path, capsys):
    d = _sdlc_with(tmp_path, '{"discovery": {"source": "local-goals"}}')
    (d / "goals").mkdir()
    assert define.main(["define.py", "bump-priority", str(d), "--unit", "voice", "--priority", "P1"]) == 1
    why = json.loads(capsys.readouterr().out.strip())["why"]
    assert "define.py set-priority" in why


# --- /sigma-doctor -----------------------------------------------------------------------------

def test_doctor_flags_a_config_json_that_does_not_parse_and_names_the_fix(tmp_path):
    d = _sdlc_with(tmp_path, '{"verify": {"enforce": true,}}')
    rows = {c["name"]: c for c in doctor.check(str(d), run=lambda *a, **k: "")}
    row = rows["config.json is valid JSON"]
    assert row["ok"] is False and "config.json.bak" in row["fix"] and "/sigma-init" in row["fix"]


def test_doctor_adds_no_row_for_a_healthy_config(tmp_path):
    d = _sdlc_with(tmp_path, "{}")
    names = [c["name"] for c in doctor.check(str(d), run=lambda *a, **k: "")]
    assert "config.json is valid JSON" not in names

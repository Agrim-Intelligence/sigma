import pathlib
import sys
import types
import subprocess
import shutil

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                       / "skills" / "sigma-init" / "scripts"))
import wizard_actions  # noqa: E402


def _fake_completed(returncode, stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)


def test_run_scaffold_succeeds_on_a_fresh_directory(tmp_path):
    result = wizard_actions.run_scaffold(str(tmp_path))
    assert result["ok"] is True
    assert (tmp_path / ".sdlc" / "config.json").exists()


def test_run_scaffold_is_idempotent_on_an_already_scaffolded_directory(tmp_path):
    wizard_actions.run_scaffold(str(tmp_path))
    second = wizard_actions.run_scaffold(str(tmp_path))
    assert second["ok"] is True   # skip-if-exists, per sdlc_init.scaffold's own contract


def _load_setup():
    import importlib.util
    path = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-setup" / "scripts" / "setup.py"
    spec = importlib.util.spec_from_file_location("setup", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_scaffold_also_gitignores_the_runtime_dirs(tmp_path):
    """run_scaffold() calls sdlc_init.scaffold() directly, bypassing main() -- the wizard's
    hook-driven bootstrap must get the same ignore-write the manual /sigma-init command does.
    Tuple-derived, same reasoning as the sdlc_init.py tests: a selective fix wired only for
    events/ must not pass."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = wizard_actions.run_scaffold(str(tmp_path))
    assert result["ok"] is True
    setup = _load_setup()
    for target in setup.RUNTIME_IGNORES:
        probe_dir = tmp_path / target
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = probe_dir / "probe.jsonl"
        probe.write_text("{}\n", encoding="utf-8")
        check = subprocess.run(
            ["git", "-C", str(tmp_path), "check-ignore", "--quiet", str(probe)])
        assert check.returncode == 0, f"the wizard's own scaffold path must also gitignore {target}"
        assert probe.exists()


def test_run_scaffold_reports_ok_false_when_the_sibling_setup_script_is_missing(tmp_path):
    """Wizard-path companion to
    test_scaffold_cli_exits_nonzero_when_the_sibling_setup_script_is_missing in
    tests/test_sdlc_init.py -- same real isolation trick, loading the isolated copy's own
    wizard_actions.py via importlib.util under a fresh module name (a bare `import
    wizard_actions` would return the real module already cached in sys.modules, masking the
    isolation entirely)."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    isolated = tmp_path / "isolated_skills" / "skills" / "sigma-init"
    shutil.copytree(repo_root / "skills" / "sigma-init", isolated)
    assert not (isolated.parent / "sigma-setup").exists()

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "wizard_actions_isolated", isolated / "scripts" / "wizard_actions.py")
    isolated_wizard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated_wizard)

    target = tmp_path / "target_repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    result = isolated_wizard.run_scaffold(str(target))
    assert result["ok"] is False, f"expected ok:false on a missing sibling script, got {result}"
    assert (target / ".sdlc" / "config.json").exists(), (
        "already-written templates must survive an ignore-write failure")


def test_run_scaffold_reports_ok_false_when_the_ignore_write_fails(tmp_path):
    """Wizard-path companion; the trigger is a DIRECTORY .gitignore (root-immune to chmod), same
    as the sdlc_init.py CLI test -- and, like it, needs no setup.py change (Rev 8): see that
    test's docstring for the live measurement against the actual unmodified ensure_ignore()."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").mkdir()
    result = wizard_actions.run_scaffold(str(tmp_path))
    assert result["ok"] is False, f"expected ok:false on a failed ignore-write, got {result}"
    assert (tmp_path / ".sdlc" / "config.json").exists()


def test_run_graphify_install_reports_success(tmp_path):
    calls = []
    def fake_run(cmd):
        calls.append(cmd)
        return _fake_completed(0)
    result = wizard_actions.run_graphify_install(run=fake_run)
    assert result["ok"] is True
    # `python3 -m pip`, not a bare `pip`: the interpreter-correct form, so the install lands in the
    # Python that will later import it rather than in whichever `pip` PATH resolved first.
    assert calls == [["python3", "-m", "pip", "install", "graphifyy"]]


def test_run_graphify_install_reports_a_plain_failure(tmp_path):
    def fake_run(cmd):
        return _fake_completed(1, stderr="some other pip error")
    result = wizard_actions.run_graphify_install(run=fake_run)
    assert result["ok"] is False
    assert "some other pip error" in result["detail"]


def test_run_graphify_install_gives_an_actionable_message_for_pep_668(tmp_path):
    """The known Python-packaging rough edge, named explicitly rather than surfaced as a raw
    pip traceback -- externally-managed environments (Debian/Ubuntu/Homebrew system pythons)
    refuse a plain `pip install` with a specific, greppable error string."""
    def fake_run(cmd):
        return _fake_completed(1, stderr="error: externally-managed-environment\n\n"
                                          "This environment is externally managed")
    result = wizard_actions.run_graphify_install(run=fake_run)
    assert result["ok"] is False
    assert "--break-system-packages" in result["detail"] or "venv" in result["detail"].lower()


def test_run_graphify_install_catches_exceptions_from_run_callable(tmp_path):
    """run_graphify_install must never raise -- it catches exceptions from the injected run
    callable (e.g. FileNotFoundError if pip is not on PATH, TimeoutExpired on slow networks)
    and returns them as failure dicts, never propagating them."""
    def fake_run_raises_file_not_found(cmd):
        raise FileNotFoundError("pip not found on PATH")
    result = wizard_actions.run_graphify_install(run=fake_run_raises_file_not_found)
    assert result["ok"] is False
    assert "pip" in result["detail"].lower()

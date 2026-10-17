"""#1648: the one shared Codex CLI resolver (`skills/sigma-loop/scripts/codex_cli.py`).

Order, each rung exact: SIGMA_CODEX_BIN (invalid -> refuse, never fall through), then a PATH hit, then
the ChatGPT-app copy on darwin. Two different copies refuse as ambiguous; the same realpath is one copy.
Nothing here starts a model: the "CLI" is a tmp file, `app_path=` is always injected (or None) so
/Applications is never read, and refusals are observed through `_outcome` (never `pytest.raises`) so a
broken or stubbed resolver fails by AssertionError rather than by pytest's `Failed`.
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sigma-loop" / "scripts"
RUNNING_VERSION = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]


def _cc():
    spec = importlib.util.spec_from_file_location("codex_cli_under_test", S / "codex_cli.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _outcome(fn):
    """("ok", value) or ("refused", reason, text, paths): a refusal is data, so a resolver that
    answers (or is a stub) fails the comparison by AssertionError."""
    try:
        return ("ok", fn())
    except ValueError as exc:
        return ("refused", getattr(exc, "reason", None), str(exc), tuple(getattr(exc, "paths", ())))


def _exe(path, body="#!/bin/sh\nexit 0\n"):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _env(path_dir, **extra):
    return {"PATH": str(path_dir), **extra}


def test_1648_env_override_wins_over_path_and_app(tmp_path):
    cc = _cc()
    override = _exe(tmp_path / "override" / "mycodex", "#!/bin/sh\necho override\n")
    _exe(tmp_path / "pathdir" / "codex", "#!/bin/sh\necho path\n")
    app = _exe(tmp_path / "app" / "codex", "#!/bin/sh\necho app\n")
    got = _outcome(lambda: cc.resolve(env=_env(tmp_path / "pathdir", SIGMA_CODEX_BIN=str(override)),
                                      app_path=str(app), platform="darwin"))
    # An explicit override is not ambiguity, even with two other different copies present.
    assert got == ("ok", str(override))


def test_1648_path_is_second_when_no_override(tmp_path):
    cc = _cc()
    found = _exe(tmp_path / "pathdir" / "codex")
    unset = _outcome(lambda: cc.resolve(env=_env(tmp_path / "pathdir"), app_path=None, platform="darwin"))
    assert unset == ("ok", str(found))
    # An empty SIGMA_CODEX_BIN counts as unset (legacy.getenv's convention), so the PATH copy answers.
    empty = _outcome(lambda: cc.resolve(env=_env(tmp_path / "pathdir", SIGMA_CODEX_BIN=""),
                                        app_path=None, platform="darwin"))
    assert empty == ("ok", str(found))
    # A directory named codex on PATH is not a hit.
    (tmp_path / "dirs" / "codex").mkdir(parents=True)
    miss = _outcome(lambda: cc.resolve(env=_env(tmp_path / "dirs"), app_path=None, platform="darwin"))
    assert miss[:2] == ("refused", "not-found")


@pytest.mark.parametrize("platform,present,expected", [
    ("darwin", True, "app"),
    ("linux", True, "not-found"),
    ("win32", True, "not-found"),
    ("darwin", False, "not-found"),
])
def test_1648_app_path_is_third_on_darwin_only(tmp_path, platform, present, expected):
    cc = _cc()
    (tmp_path / "emptypath").mkdir()
    app = tmp_path / "app" / "codex"
    if present:
        _exe(app)
    got = _outcome(lambda: cc.resolve(env=_env(tmp_path / "emptypath"), app_path=str(app), platform=platform))
    if expected == "app":
        assert got == ("ok", str(app))
    else:
        assert got[:2] == ("refused", "not-found")


def test_1648_two_different_copies_refuse_as_ambiguous_naming_both(tmp_path):
    cc = _cc()
    on_path = _exe(tmp_path / "pathdir" / "codex", "#!/bin/sh\necho path\n")
    app = _exe(tmp_path / "app" / "codex", "#!/bin/sh\necho app\n")
    got = _outcome(lambda: cc.resolve(env=_env(tmp_path / "pathdir"), app_path=str(app), platform="darwin"))
    assert got[:2] == ("refused", "ambiguous")
    assert str(on_path) in got[2] and str(app) in got[2]
    assert str(on_path) in got[3] and str(app) in got[3]
    assert "SIGMA_CODEX_BIN" in got[2]


def test_1648_same_realpath_copy_is_one_copy_not_ambiguous(tmp_path):
    cc = _cc()
    on_path = _exe(tmp_path / "pathdir" / "codex")
    app = tmp_path / "app" / "codex"
    app.parent.mkdir()
    app.symlink_to(on_path)
    got = _outcome(lambda: cc.resolve(env=_env(tmp_path / "pathdir"), app_path=str(app), platform="darwin"))
    assert got == ("ok", str(on_path))


@pytest.mark.parametrize("kind", ["relative", "missing", "directory", "not-executable", "whitespace"])
def test_1648_invalid_override_refuses_and_never_falls_through(tmp_path, kind, monkeypatch):
    cc = _cc()
    if kind == "relative":
        # The relative name RESOLVES to an executable file under the cwd, so only the absolute-path
        # clause can refuse it: dropping `os.path.isabs` would let a cwd-relative binary through.
        cwd = tmp_path / "cwd"
        _exe(cwd / "relative" / "codex")
        monkeypatch.chdir(cwd)
    valid = _exe(tmp_path / "pathdir" / "codex")           # a valid PATH copy: a fall-through would return it
    value = {
        "relative": "relative/codex",
        "missing": str(tmp_path / "nowhere" / "codex"),
        "directory": str(tmp_path),
        "not-executable": str(_exe(tmp_path / "plain" / "codex")),
        # Whitespace-only is NOT "unset" (only the empty string is): it is an invalid override.
        "whitespace": "   ",
    }[kind]
    if kind == "not-executable":
        pathlib.Path(value).chmod(0o644)
    got = _outcome(lambda: cc.resolve(env=_env(tmp_path / "pathdir", SIGMA_CODEX_BIN=value),
                                      app_path=None, platform="darwin"))
    assert got[:2] == ("refused", "override-invalid"), (kind, got, str(valid))
    assert "SIGMA_CODEX_BIN" in got[2]


def test_1648_nothing_found_refuses_not_found_and_names_the_lever(tmp_path):
    cc = _cc()
    (tmp_path / "emptypath").mkdir()
    got = _outcome(lambda: cc.resolve(env=_env(tmp_path / "emptypath"), app_path=None, platform="darwin"))
    assert got[:2] == ("refused", "not-found")
    assert "SIGMA_CODEX_BIN" in got[2]


def test_1648_resolve_answers_without_executing_the_candidate(tmp_path):
    cc = _cc()
    marker = tmp_path / "ran"
    body = f"#!/bin/sh\necho ran > {marker}\n"
    override = _exe(tmp_path / "o" / "codex", body)
    on_path = _exe(tmp_path / "pathdir" / "codex", body)
    app = _exe(tmp_path / "app" / "codex", body)
    via_override = cc.resolve(env=_env(tmp_path / "pathdir", SIGMA_CODEX_BIN=str(override)),
                              app_path=str(app), platform="darwin")
    via_path = cc.resolve(env=_env(tmp_path / "pathdir"), app_path=None, platform="darwin")
    via_app = cc.resolve(env=_env(tmp_path / "nopath"), app_path=str(app), platform="darwin")
    assert (via_override, via_path, via_app) == (str(override), str(on_path), str(app))
    assert not marker.exists()


def test_1648_skew_compares_three_part_versions_and_reports_unparsable_as_none(tmp_path):
    cc = _cc()
    pairs = [("1.0.15", "1.0.15", False), ("1.0.15", "1.0.16", True), ("1.0.16", "1.0.15", True),
             ("1.0.15", "1.1.0", True), ("1.0.9", "1.0.15", True), ("1.0.15", "garbage", None),
             ("garbage", "1.0.15", None), ("", "1.0.15", None), ("1.0.15", "1.0.15-rc1", False)]
    assert [cc.skew(a, b) for a, b, _ in pairs] == [want for _, _, want in pairs]
    # own_version: the plugin.json `version` of a root, never raising.
    root = tmp_path / "root"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "sigmaloop", "version": "3.2.1"}))
    assert cc.own_version(root) == "3.2.1"
    assert cc.own_version(tmp_path / "no-such-root") is None
    (root / ".claude-plugin" / "plugin.json").write_text("{ not json")
    assert cc.own_version(root) is None
    # The default root is the RUNNING copy's checkout.
    assert cc.own_version() == RUNNING_VERSION


def test_1648_loading_codex_cli_from_codex_runtime_writes_no_bytecode(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("codex_runtime.py", "codex_cli.py"):
        shutil.copy2(S / name, scripts / name)
    source = scripts / "codex_runtime.py"
    # codex_runtime itself is exec'd (no pycache of its own); only its loader may leave bytecode.
    runner = ("import sys; sys.pycache_prefix=None\n"
              f"ns={{'__file__': {str(source)!r}, '__name__': 'codex_runtime_copy'}}\n"
              f"exec(compile(open({str(source)!r}).read(), {str(source)!r}, 'exec'), ns)\n"
              "mod = ns['_codex_cli']()\n"
              "assert hasattr(mod, 'resolve')\n"
              "print('flag', sys.dont_write_bytecode)\n")
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    proc = subprocess.run([sys.executable, "-c", runner], env=env, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert "flag False" in proc.stdout                         # the previous value is restored
    assert not list(scripts.rglob("__pycache__"))

"""D7 'every refusal prints its fix': /sigma-promote and /sigma-unpark in the scaffold-default
local-goals mode, and the GitHub transport with no `gh` installed.

Before: promote/unpark built a GitHubSource anyway and printed `unable to expand placeholder in
path: no git remotes found` (read as a broken repo), and an absent `gh` surfaced as the raw
`[Errno 2] No such file or directory: 'gh'`."""
import json, pathlib, importlib.util, subprocess

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _local_goals(tmp_path):
    d = tmp_path / ".sdlc"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"discovery": {"source": "local-goals"}}))
    return str(d)


def _no_gh(monkeypatch):
    def boom(argv, *a, **k):
        raise FileNotFoundError(2, "No such file or directory", argv[0])
    monkeypatch.setattr(subprocess, "run", boom)


@pytest.mark.parametrize("verb,extra,code", [("list", [], 1), ("apply", ["5", "--dry-run"], 1),
                                             ("demote", ["5"], 1)])
def test_promote_in_local_goals_says_it_needs_github_mode(tmp_path, capsys, verb, extra, code):
    p = _mod("promote")
    rc = p.main(["promote.py", verb, _local_goals(tmp_path), *extra])
    out = capsys.readouterr()
    text = out.out + out.err
    assert rc == code
    assert "needs github discovery mode" in text and "/sigma-init --mode github" in text
    assert "placeholder" not in text and "no git remotes" not in text


def test_promote_migrate_confirmation_in_local_goals_exits_2_with_the_fix(tmp_path, capsys):
    p = _mod("promote")
    rc = p.main(["promote.py", "migrate-confirmation", _local_goals(tmp_path), "--dry-run"])
    text = "".join(capsys.readouterr())
    assert rc == 2 and "/sigma-init --mode github" in text


@pytest.mark.parametrize("argv", [["list"], ["brief", "5"]])
def test_unpark_in_local_goals_says_it_needs_github_mode(tmp_path, capsys, argv):
    u = _mod("unpark")
    rc = u.main(["unpark.py", argv[0], _local_goals(tmp_path), *argv[1:]])
    out = capsys.readouterr()
    text = out.out + out.err
    assert rc == 1
    assert "needs github discovery mode" in text and "/sigma-init --mode github" in text
    assert "placeholder" not in text and "none" not in text.lower().split()


def test_unpark_resolve_in_local_goals_says_it_needs_github_mode(tmp_path, capsys):
    u = _mod("unpark")
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": {}}))
    rc = u.main(["unpark.py", "resolve", _local_goals(tmp_path), "5", "--answers", str(answers),
                 "--decision", "x"])
    text = "".join(capsys.readouterr())
    assert rc == 1 and "/sigma-init --mode github" in text


def test_a_github_repo_still_reaches_the_source(tmp_path, monkeypatch, capsys):
    """The guard is for local-goals only: a github config with an injected runner must not trip it."""
    p = _mod("promote")
    d = tmp_path / ".sdlc"
    d.mkdir()
    (d / "config.json").write_text(json.dumps(
        {"discovery": {"source": "github", "github": {"repo": "acme/widget"}}}))
    calls = []

    def fake(argv, *a, **k):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")
    monkeypatch.setattr(subprocess, "run", fake)
    p.main(["promote.py", "list", str(d)])          # no injected runner: the real transport is used
    assert calls and "needs github discovery mode" not in "".join(capsys.readouterr())


def test_sources_run_gh_without_gh_names_the_install_and_login(monkeypatch):
    sources = _mod("sources")
    _no_gh(monkeypatch)
    with pytest.raises(RuntimeError) as exc:
        sources._run_gh(["issue", "list"])
    for text in (str(exc.value), exc.value.hint):
        assert "`gh` is not on PATH" in text and "https://cli.github.com" in text
        assert "`gh auth login`" in text


def test_gh_api_default_run_without_gh_names_the_install_and_login(monkeypatch):
    gh_api = _mod("gh_api")
    _no_gh(monkeypatch)
    with pytest.raises(RuntimeError) as exc:
        gh_api._default_run(["issue", "list"])
    assert "`gh` is not on PATH" in str(exc.value) and "`gh auth login`" in exc.value.hint


def test_gh_api_bounded_runner_without_gh_names_the_install_and_login():
    gh_api = _mod("gh_api")

    def popen(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "gh")
    run = gh_api.bounded_runner(popen=popen)
    with pytest.raises(Exception) as exc:
        run(["issue", "list"])
    assert "`gh` is not on PATH" in str(exc.value) and "`gh auth login`" in str(exc.value)

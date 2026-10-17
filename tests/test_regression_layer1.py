"""Hermetic tests for evals/regression/layer1.py (#866): fake arm, no model, no network, no spend.

Every test that reaches `run_scenario` or `sweep_stale` passes an explicit `tmp_root` under pytest's tmp_path;
the autouse fixture below fails any test that would fall back to the real system temp directory.
"""
import ast
import importlib.util
import json
import os
import pathlib
import re
import shlex
import signal
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAYER1 = ROOT / "evals" / "regression" / "layer1.py"
README = ROOT / "evals" / "regression" / "README.md"

posix_only = pytest.mark.skipif(os.name != "posix", reason="Layer 1 is POSIX-only")
pytestmark = posix_only


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


layer1 = _load("layer1_under_test", LAYER1)


@pytest.fixture(autouse=True)
def no_real_tmp(monkeypatch):
    def refuse():
        raise AssertionError("a test reached the real system temp directory; pass tmp_root")
    monkeypatch.setattr(layer1, "_default_tmp_root", refuse)


# --------------------------------------------------------------------------------------------- fixtures


def _env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


def _g(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          cwd=str(cwd), env=_env(), check=True, capture_output=True, text=True).stdout


@pytest.fixture
def source_repo(tmp_path):
    src = tmp_path / "source"
    for rel, text in {".claude-plugin/plugin.json": "{}", "skills/x/SKILL.md": "x", "hooks/h.sh": "#!/bin/sh\n",
                      "evals/e.txt": "e", "tests/t.py": "t", "README.md": "r"}.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    _g(src, "init", "-q", "-b", "main")
    _g(src, "add", "-A")
    _g(src, "commit", "-qm", "c")
    return src


def make_spec(tmp_path, source_repo, **over):
    goal = tmp_path / "goal.md"
    goal.write_text("Build the thing.\n")
    values = dict(commit_rev="HEAD", source_repo=str(source_repo), goal_file=str(goal),
                  prompts={"init": "/init prompt", "loop": "/loop prompt"}, model="m", belt="1", deadline=layer1.time.monotonic() + 600,
                  rates={"in": 1}, artifact_dir=str(tmp_path / "artifacts"), tmp_root=str(tmp_path / "scratch"))
    values.update(over)
    (tmp_path / "scratch").mkdir(exist_ok=True)
    return layer1.RunSpec(**values)


@pytest.fixture
def spec(tmp_path, source_repo):
    return make_spec(tmp_path, source_repo)


def left_over(spec):
    return [n for n in os.listdir(spec.tmp_root) if n.startswith(layer1.PREFIX)]


class FakeArm:
    """Records what it was given; optionally scaffolds the config the way /sigma-init does."""

    def __init__(self, scaffold=True, config=None):
        self.calls, self.order, self.scaffold = [], [], scaffold
        self.config = config if config is not None else {
            "board": {"x": 1}, "journal": {"enabled": False, "keep": 7}, "gates": {"other": True}}

    def __call__(self, s):
        cfg_path = pathlib.Path(s.repo) / ".sdlc" / "config.json"
        snapshot = json.loads(cfg_path.read_text()) if cfg_path.exists() else None
        leaves = sorted(os.listdir(s.profile_dir))
        self.calls.append({
            "name": s.name, "spec": s, "leaves": leaves,
            "leaf_contents": {leaf: os.listdir(os.path.join(s.profile_dir, leaf)) for leaf in leaves},
            "config": snapshot, "env": dict(s.env),
            "other_profile_exists": os.path.exists(os.path.join(os.path.dirname(s.run_dir),
                                                                "loop" if s.name == "init" else "init", "profile")),
        })
        self.order.append(s.name)
        if self.scaffold and s.name == "init":
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(self.config))
        return {"cost": 0.0, "session_id": "sid-" + s.name, "returncode": 0, "timed_out": False}


@pytest.fixture
def built(spec):
    scenarios = []

    def make():
        sc = layer1.build_scenario(spec)
        scenarios.append(sc)
        return sc
    yield make
    for sc in scenarios:
        layer1.cleanup(sc.root, tmp_root=spec.tmp_root)


# --------------------------------------------------------------------------------------------- step 1


@pytest.mark.parametrize("field", ["model", "belt", "deadline", "rates"])
def test_missing_model_belt_deadline_rates_refuse(tmp_path, source_repo, field):
    sp = make_spec(tmp_path, source_repo, **{field: None})
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), sp)
    assert exc.value.code == "param-required" and field in exc.value.detail
    assert left_over(sp) == []


@pytest.mark.parametrize("name", ["init", "loop"])
def test_missing_prompt_refuses(tmp_path, source_repo, name):
    prompts = {"init": "a", "loop": "b"}
    del prompts[name]
    sp = make_spec(tmp_path, source_repo, prompts=prompts)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), sp)
    assert exc.value.code == "param-required"


@pytest.mark.parametrize("value", ["", None])
def test_artifact_dir_required(tmp_path, source_repo, value):
    sp = make_spec(tmp_path, source_repo, artifact_dir=value)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), sp)
    assert exc.value.code == "artifact-dir-required"


def test_runspec_defaults_construct(tmp_path, source_repo):
    a, b = make_spec(tmp_path, source_repo), make_spec(tmp_path, source_repo)
    assert a.session_env == {} and a.session_env is not b.session_env
    assert a.permission_mode == "auto"


# --------------------------------------------------------------------------------------------- step 2


def test_scratch_root_has_prefix_and_marker(spec, built):
    sc = built()
    name = os.path.basename(sc.root)
    assert name.startswith(layer1.PREFIX)
    assert pathlib.Path(sc.root, layer1.MARKER_NAME).read_text() == layer1.MARKER_TEXT
    assert (os.stat(sc.root).st_mode & 0o777) == 0o700
    assert os.path.dirname(sc.root) == os.path.realpath(spec.tmp_root)


def test_instruction_ancestor_refused(tmp_path, source_repo):
    sp = make_spec(tmp_path, source_repo)
    (tmp_path / "CLAUDE.md").write_text("instructions")
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.build_scenario(sp)
    assert exc.value.code == "instruction-ancestor"
    assert left_over(sp) == []


def test_tmp_root_realpath_normalised(tmp_path, source_repo):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    sp = make_spec(tmp_path, source_repo, tmp_root=str(link))
    sc = layer1.build_scenario(sp)
    try:
        assert os.path.dirname(sc.root) == os.path.realpath(str(real))
    finally:
        layer1.cleanup(sc.root, tmp_root=str(link))


# --------------------------------------------------------------------------------------------- step 3


def test_repo_identity_is_repo_local_only(tmp_path, source_repo, monkeypatch):
    poison = tmp_path / "poison.gitconfig"
    poison.write_text("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n[user]\n\tname = leaked\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(poison))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "leaked-env")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "leaked@env.invalid")
    sp = make_spec(tmp_path, source_repo)
    sc = layer1.build_scenario(sp)
    try:
        local = _g(sc.repo, "config", "--local", "user.name").strip()
        assert local and local != "leaked"
        assert _g(sc.repo, "config", "--local", "user.email").strip()
        author = subprocess.run(["git", "log", "-1", "--format=%an|%ae|%cn"], cwd=sc.repo, env=_env(),
                                capture_output=True, text=True).stdout.strip()
        assert "leaked" not in author
        assert author.startswith(local + "|")
        origin = _g(sc.repo, "remote", "get-url", "origin").strip()
        assert origin == sc.remote and sc.remote == os.path.join(sc.root, "remote.git")
        assert _g(sc.remote, "log", "--format=%s", "main").strip()
    finally:
        layer1.cleanup(sc.root, tmp_root=sp.tmp_root)


def test_initial_tree_has_goal_and_no_instruction_files(spec, built):
    sc = built()
    assert sc.goal_path == os.path.join(sc.repo, layer1.GOAL_PATH)
    assert pathlib.Path(sc.goal_path).read_text() == "Build the thing.\n"
    remote_files = _g(sc.remote, "ls-tree", "-r", "--name-only", "main").split()
    assert sorted(remote_files) == ["GOAL.md", "README.md"]
    for base in (sc.repo, sc.remote):
        for _, dirs, files in os.walk(base):
            names = set(dirs) | set(files)
            assert not names & set(layer1.INSTRUCTION_FILES) or base == sc.remote
    assert not (set(os.listdir(sc.repo)) & set(layer1.INSTRUCTION_FILES))
    assert not (set(remote_files) & set(layer1.INSTRUCTION_FILES))
    assert all(not f.startswith(".claude/") for f in remote_files)


@pytest.mark.parametrize("via", ["direct", "run_scenario"])
@pytest.mark.parametrize("fail", ["export", "git"])
def test_build_scenario_failure_removes_root(tmp_path, source_repo, monkeypatch, via, fail):
    sp = make_spec(tmp_path, source_repo)
    if fail == "export":
        def boom(*a, **k):
            raise layer1._common().ArmRefusal("boom")
        monkeypatch.setattr(layer1._sigma(), "export_plugin", boom)
    else:
        real = layer1._git
        monkeypatch.setattr(layer1, "_git", lambda args, cwd: (_ for _ in ()).throw(RuntimeError("git down"))
                            if args[0] == "commit" else real(args, cwd))
    with pytest.raises((layer1.Layer1Refusal, RuntimeError)):
        if via == "direct":
            layer1.build_scenario(sp)
        else:
            layer1.run_scenario(FakeArm(), sp)
    assert left_over(sp) == []


def test_push_is_plain_non_forced():
    tree = ast.parse(LAYER1.read_text())
    pushes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_git" and node.args \
                and isinstance(node.args[0], ast.List):
            tokens = [e.value for e in node.args[0].elts if isinstance(e, ast.Constant)]
            if "push" in tokens:
                pushes.append(tokens)
    assert len(pushes) == 1
    assert not [t for t in pushes[0] if t.startswith("+") or t.startswith("--force") or t in ("-f", "--delete")]


# --------------------------------------------------------------------------------------------- step 4


def test_fake_gh_extraction_pin():
    body = layer1._fake_gh_body(ROOT)
    assert isinstance(body, str) and "FAKE_GH_STATE" in body


def test_fake_gh_missing_refuses(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_public_bootstrap_control.py").write_text("OTHER = 1\n")
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1._fake_gh_body(tmp_path)
    assert exc.value.code == "fake-gh-missing"


def test_fake_gh_extracted_from_harness_not_source_repo(tmp_path, source_repo, built):
    assert not (source_repo / "tests" / "test_public_bootstrap_control.py").exists()
    sc = built()
    gh = pathlib.Path(sc.bin_dir, "gh").read_text()
    assert gh.split("\n", 1)[1] == layer1._fake_gh_body(ROOT)


def test_gh_shim_first_on_path_and_executable(spec, built):
    sc = built()
    arm = FakeArm()
    s = layer1.prepare_session(sc, sc.sessions[0], {})
    assert s.env["PATH"].split(":")[0] == sc.bin_dir
    gh = pathlib.Path(sc.bin_dir, "gh")
    assert (gh.stat().st_mode & 0o777) == 0o755
    assert gh.read_text().startswith("#!" + sys.executable + "\n")
    assert arm.calls == []


def test_fake_gh_paths_absolute_and_state_seeded(spec, built):
    sc = built()
    s = layer1.prepare_session(sc, sc.sessions[0], {})
    for key in ("FAKE_GH_STATE", "FAKE_GH_LOG", "FAKE_GH_UNHANDLED"):
        assert os.path.isabs(s.env[key]) and os.path.isfile(s.env[key])
        assert s.env[key].startswith(sc.root)
    state = json.loads(pathlib.Path(s.env["FAKE_GH_STATE"]).read_text())
    assert state["remote_git_dir"] == sc.remote
    assert {"repo", "labels", "issues", "prs", "default_branch", "login"} <= set(state)


# --------------------------------------------------------------------------------------------- step 5


def run(spec, arm=None):
    arm = arm or FakeArm()
    return arm, layer1.run_scenario(arm, spec)


def test_sessions_have_distinct_empty_profiles(spec):
    arm, _ = run(spec)
    for call in arm.calls:
        assert call["leaves"] == ["claude-config", "codex-home", "home", "tmp"]
        assert all(v == [] for v in call["leaf_contents"].values())
        s = call["spec"]
        assert call["env"]["HOME"] == os.path.join(s.profile_dir, "home")
        assert call["env"]["CLAUDE_CONFIG_DIR"] == os.path.join(s.profile_dir, "claude-config")
        assert call["env"]["CODEX_HOME"] == os.path.join(s.profile_dir, "codex-home")
    assert arm.calls[0]["other_profile_exists"] is False        # the loop profile is made just before the loop call


def test_profile_dirs_distinct_between_sessions(spec):
    arm, _ = run(spec)
    a, b = (c["spec"] for c in arm.calls)
    assert a.profile_dir != b.profile_dir and a.run_dir != b.run_dir
    assert arm.calls[0]["env"]["HOME"] != arm.calls[1]["env"]["HOME"]


def test_export_is_sibling_of_profile(spec):
    arm, _ = run(spec)
    for call in arm.calls:
        s = call["spec"]
        assert os.path.dirname(s.plugin_dir) == os.path.dirname(s.profile_dir) == s.run_dir
    assert arm.calls[0]["spec"].plugin_dir != arm.calls[1]["spec"].plugin_dir


def test_exports_are_two_separate_fresh_directories(spec, built):
    sc = built()
    a, b = sc.sessions
    assert a.plugin_dir != b.plugin_dir
    for s in (a, b):
        assert os.path.isdir(s.plugin_dir)
    marker = pathlib.Path(a.plugin_dir, "skills", "x", "SKILL.md")
    marker.write_text("changed")
    assert pathlib.Path(b.plugin_dir, "skills", "x", "SKILL.md").read_text() == "x"


def test_export_has_no_evals_or_tests_and_has_claude_plugin(spec, built):
    """A pin on the bench `export_plugin` that layer1 delegates to; the layer1 clause is the test above."""
    sc = built()
    for s in sc.sessions:
        assert set(os.listdir(s.plugin_dir)) <= {".claude-plugin", "skills", "hooks"}
        assert ".claude-plugin" in os.listdir(s.plugin_dir)
        for _, dirs, files in os.walk(s.plugin_dir):
            assert not {"evals", "tests"} & set(dirs)


def test_env_is_allowlist_only(spec, monkeypatch):
    for k, v in {"GH_TOKEN": "t", "GITHUB_TOKEN": "t", "GIT_AUTHOR_NAME": "n", "SIGMA_BENCH_X": "1",
                 "SOME_SECRET_KEY": "s", "ANTHROPIC_API_KEY": "k"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("LC_ALL", "C")
    arm, _ = run(spec, FakeArm())
    allowed = {"PATH", "LANG", "TZ", "TERM", "SHELL", "USER", "LOGNAME", "FAKE_GH_STATE", "FAKE_GH_LOG",
               "FAKE_GH_UNHANDLED", "HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TMPDIR"}
    for call in arm.calls:
        keys = set(call["env"])
        assert keys <= allowed | {k for k in keys if k.startswith("LC_")}
        assert not [k for k in keys if k.startswith(("GH_", "GITHUB_", "SIGMA_BENCH_")) or "SECRET" in k or "TOKEN" in k]
        assert "ANTHROPIC_API_KEY" not in keys


@pytest.mark.parametrize("key", ["GH_TOKEN", "GITHUB_TOKEN", "GIT_AUTHOR_NAME"])
def test_env_has_no_github_shaped_variable(spec, tmp_path, source_repo, monkeypatch, key):
    monkeypatch.setenv(key, "x")
    arm, _ = run(spec)
    for call in arm.calls:
        assert key not in call["env"]
        assert not [k for k in call["env"] if k.startswith(layer1.GITHUB_SHAPED)]
    sp = make_spec(tmp_path, source_repo, session_env={key: "y"})
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), sp)
    assert exc.value.code == "github-shaped-env"
    assert left_over(sp) == []


@pytest.mark.parametrize("key", ["PATH", "HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "TMPDIR", "FAKE_GH_X",
                                 "FAKE_GH_STATE"])
def test_session_env_collisions_refused(tmp_path, source_repo, key):
    sp = make_spec(tmp_path, source_repo, session_env={key: "y"})
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), sp)
    assert exc.value.code == "env-collision"


def test_session_env_extras_reach_the_arm(tmp_path, source_repo):
    sp = make_spec(tmp_path, source_repo, session_env={"CLAUDE_CODE_SESSION_ID": "abc"})
    arm, _ = run(sp)
    assert all(c["env"]["CLAUDE_CODE_SESSION_ID"] == "abc" for c in arm.calls)


# --------------------------------------------------------------------------------------------- step 6


def test_config_written_between_init_and_loop(spec):
    arm, result = run(spec)
    init, loop = arm.calls
    assert init["config"] is None                               # nothing exists, and no run key yet
    assert loop["config"]["journal"]["enabled"] is True
    assert loop["config"]["gates"]["plan_review"]["enabled"] is True
    assert arm.order == ["init", "loop"]
    assert result["config_written"]["journal.enabled"] is True


def test_config_preserves_unrelated_keys(spec):
    arm, _ = run(spec)
    cfg = arm.calls[1]["config"]
    assert cfg["board"] == {"x": 1} and cfg["journal"]["keep"] == 7 and cfg["gates"]["other"] is True


def test_config_missing_refuses(spec):
    arm = FakeArm(scaffold=False)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(arm, spec)
    assert exc.value.code == "config-missing"
    assert arm.order == ["init"]
    assert left_over(spec) == []


def test_config_keys_written_and_read_back(tmp_path, source_repo):
    sp = make_spec(tmp_path, source_repo, verify_command="make check", verify_enforce=True)
    _, result = run(sp)
    assert result["config_written"] == {
        "journal.enabled": True, "gates.plan_review.enabled": True, "action_log.enabled": True,
        "verify.command": "make check", "verify.enforce": True, "work.enabled": True}


def _scripts(name):
    return _load("scripts_" + name, ROOT / "skills" / "sigma-loop" / "scripts" / (name + ".py"))


def test_config_matches_real_readers(spec):
    arm, _ = run(spec)
    cfg = arm.calls[1]["config"]
    ledger, work = _scripts("ledger"), _scripts("work")
    assert ledger.journal_enabled(cfg) is True
    assert work._plan_review_on(cfg) is True
    assert work.hard_plan_gate_on(cfg["gates"]["plan_review"]) is True
    assert work.enabled(cfg) is True
    assert cfg["action_log"]["enabled"] is True


def test_readback_mismatch_refuses(spec, monkeypatch):
    def drop(source, target):
        data = json.loads(pathlib.Path(source).read_text())
        data["journal"].pop("enabled")
        pathlib.Path(source).write_text(json.dumps(data))
        os.replace(source, target)
    monkeypatch.setattr(layer1, "_replace", drop)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), spec)
    assert exc.value.code == "config-readback"
    assert left_over(spec) == []


def test_readback_type_mismatch_refuses(spec, monkeypatch):
    """1 == True in Python; a reader that is strict about `is True` is not, so the read-back compares types too."""
    def as_int(source, target):
        data = json.loads(pathlib.Path(source).read_text())
        data["journal"]["enabled"] = 1
        pathlib.Path(source).write_text(json.dumps(data))
        os.replace(source, target)
    monkeypatch.setattr(layer1, "_replace", as_int)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), spec)
    assert exc.value.code == "config-readback"


@pytest.mark.parametrize("mode,flag", [("auto", False), ("acceptEdits", True)])
def test_non_auto_mode_is_not_representative(tmp_path, source_repo, mode, flag):
    sp = make_spec(tmp_path, source_repo, permission_mode=mode)
    arm, result = run(sp)
    assert result["not_representative"] is flag and result["permission_mode"] == mode
    assert all(c["spec"].not_representative is flag and c["spec"].permission_mode == mode for c in arm.calls)


@pytest.mark.parametrize("mode", ["", None])
def test_permission_mode_required(tmp_path, source_repo, mode):
    sp = make_spec(tmp_path, source_repo, permission_mode=mode)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), sp)
    assert exc.value.code == "permission-mode-required"


def test_scenario_config_written_is_replaced_not_mutated(spec, monkeypatch):
    seen = {}
    real = layer1.write_run_config

    def spy(repo, sp):
        seen["recorded"] = real(repo, sp)
        return seen["recorded"]
    monkeypatch.setattr(layer1, "write_run_config", spy)
    _, result = run(spec)
    assert result["config_written"] == seen["recorded"] != {}
    with pytest.raises(Exception):
        layer1.Scenario("r", "r", "r", "g", "p", "b", (), {}, ()).root = "x"


# --------------------------------------------------------------------------------------------- step 7


def test_two_sessions_run_init_then_loop_in_order(spec):
    arm, result = run(spec)
    assert arm.order == ["init", "loop"]
    assert [c["spec"].prompt for c in arm.calls] == ["/init prompt", "/loop prompt"]
    assert [c["spec"].index for c in arm.calls] == [0, 1]
    assert [r["name"] for r in result["sessions"]] == ["init", "loop"]
    assert result["sessions"][0]["result"]["session_id"] == "sid-init"
    assert result["goal_path"] == "GOAL.md"
    assert result["assumptions"] and all(a.startswith("unverified:") for a in result["assumptions"])


def test_fake_arm_can_write_into_repo_and_inspect_env(spec):
    seen = {}

    def arm(s):
        pathlib.Path(s.repo, "note-%s.txt" % s.name).write_text("x")
        seen[s.name] = (s.model, s.belt, s.deadline, s.rates)
        if s.name == "init":
            cfg = pathlib.Path(s.repo, ".sdlc", "config.json")
            cfg.parent.mkdir(exist_ok=True)
            cfg.write_text("{}")
        return {"cost": 0}
    layer1.run_scenario(arm, spec)
    assert seen["init"] == seen["loop"] == ("m", "1", spec.deadline, {"in": 1})


def test_missing_arm_result_refused_not_swallowed(spec):
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(lambda s: None, spec)
    assert exc.value.code == "missing-arm-result"
    assert left_over(spec) == []


def test_no_model_no_subprocess_beyond_git(spec, monkeypatch):
    common = layer1._common()

    def never(*a, **k):
        raise AssertionError("a model-spawning helper was called")
    monkeypatch.setattr(common, "run_claude", never)
    monkeypatch.setattr(common, "run_bounded", never)
    argv0 = []
    for name in ("run", "Popen", "call", "check_output", "check_call"):
        real = getattr(subprocess, name)

        def wrap(*a, _real=real, **k):
            argv0.append(a[0][0] if a and isinstance(a[0], (list, tuple)) else a)
            return _real(*a, **k)
        monkeypatch.setattr(subprocess, name, wrap)
    run(spec)
    assert argv0 and set(argv0) == {"git"}
    tree = ast.parse(LAYER1.read_text())
    users = set()
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if any(isinstance(n, ast.Name) and n.id == "subprocess" for n in ast.walk(fn)):
            users.add(fn.name)
    module_level = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))
                    and any(isinstance(x, ast.Name) and x.id == "subprocess" for x in ast.walk(n))]
    assert users == {"_git"} and module_level == []


def test_refuses_off_posix(spec, monkeypatch):
    monkeypatch.setattr(layer1._common(), "IS_POSIX", False)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), spec)
    assert exc.value.code == "unsupported-platform"
    assert left_over(spec) == []


# --------------------------------------------------------------------------------------------- step 8


def test_root_removed_after_success(spec):
    run(spec)
    assert left_over(spec) == []


class Boom(Exception):
    pass


@pytest.mark.parametrize("exc_type", [Boom, KeyboardInterrupt])
def test_root_removed_after_arm_exception(spec, exc_type):
    def arm(s):
        raise exc_type("A")
    with pytest.raises(exc_type):
        layer1.run_scenario(arm, spec)
    assert left_over(spec) == []
    doc = json.loads((pathlib.Path(spec.artifact_dir) / layer1.ROWS_NAME).read_text())
    assert doc["status"] == "error"
    assert [r["status"] for r in doc["sessions"]] == ["raised", "not-run"]
    assert doc["sessions"][0]["error"]["type"] == exc_type.__name__


def test_partial_rows_record_type_and_message_without_environment(spec, monkeypatch):
    monkeypatch.setenv("SOME_SECRET_KEY", "hunter2-do-not-leak")

    def arm(s):
        raise Boom("failed key sk-ant-%s" % ("a" * 30))
    with pytest.raises(Boom):
        layer1.run_scenario(arm, spec)
    text = (pathlib.Path(spec.artifact_dir) / layer1.ROWS_NAME).read_text()
    assert "Boom" in text and "hunter2" not in text and "sk-ant-" + "a" * 30 not in text
    assert "FAKE_GH_STATE" not in text and "HOME" not in text


def test_rows_copied_before_removal(spec, monkeypatch):
    seen = {}
    real = layer1._write_rows

    def hook(artifact_dir, document):
        seen["roots"] = [n for n in os.listdir(spec.tmp_root) if n.startswith(layer1.PREFIX)]
        return real(artifact_dir, document)
    monkeypatch.setattr(layer1, "_write_rows", hook)
    _, result = run(spec)
    assert len(seen["roots"]) == 1 and left_over(spec) == []
    doc = json.loads(pathlib.Path(result["copied_to"]).read_text())
    assert [s["name"] for s in doc["sessions"]] == ["init", "loop"] and doc["status"] == "ok"
    assert os.path.dirname(result["copied_to"]) == os.path.realpath(spec.artifact_dir)


def test_artifact_dir_inside_root_refused(tmp_path, source_repo, monkeypatch):
    sp0 = make_spec(tmp_path, source_repo)
    fixed = os.path.join(os.path.realpath(sp0.tmp_root), layer1.PREFIX + "fixedname")

    def fake_mkdtemp(prefix, dir):
        os.mkdir(fixed, 0o700)
        return fixed
    monkeypatch.setattr(layer1.tempfile, "mkdtemp", fake_mkdtemp)
    sp = make_spec(tmp_path, source_repo, artifact_dir=os.path.join(fixed, "out"))
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), sp)
    assert exc.value.code == "artifact-inside-root"
    assert left_over(sp) == []


def _marked(tmp_root, name=None, text=None):
    d = os.path.join(str(tmp_root), name or layer1.PREFIX + "x")
    os.mkdir(d, 0o700)
    if text is not False:
        pathlib.Path(d, layer1.MARKER_NAME).write_text(layer1.MARKER_TEXT if text is None else text)
    return d


def test_cleanup_refuses_without_marker(tmp_path):
    d = _marked(tmp_path, text=False)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.cleanup(d, tmp_root=str(tmp_path))
    assert exc.value.code == "cleanup-marker" and os.path.isdir(d)


def test_cleanup_refuses_without_prefix(tmp_path):
    d = _marked(tmp_path, name="plain-dir")
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.cleanup(d, tmp_root=str(tmp_path))
    assert exc.value.code == "cleanup-prefix" and os.path.isdir(d)


def test_cleanup_refuses_wrong_marker_text(tmp_path):
    d = _marked(tmp_path, text="something else")
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.cleanup(d, tmp_root=str(tmp_path))
    assert exc.value.code == "cleanup-marker" and os.path.isdir(d)


def test_cleanup_refuses_wrong_location(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    d = _marked(other)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.cleanup(d, tmp_root=str(tmp_path))
    assert exc.value.code == "cleanup-location" and os.path.isdir(d)


def test_cleanup_refuses_symlink_and_foreign_uid_paths(tmp_path, monkeypatch):
    real = _marked(tmp_path, name="elsewhere")
    link = os.path.join(str(tmp_path), layer1.PREFIX + "link")
    os.symlink(real, link)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.cleanup(link, tmp_root=str(tmp_path))
    assert exc.value.code == "cleanup-symlink" and os.path.isdir(real)
    d = _marked(tmp_path)
    monkeypatch.setattr(layer1.os, "getuid", lambda: os.stat(d).st_uid + 1)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.cleanup(d, tmp_root=str(tmp_path))
    assert exc.value.code == "cleanup-foreign-owner" and os.path.isdir(d)


def test_cleanup_and_sweep_with_symlinked_tmp_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    via = _marked(link, name=layer1.PREFIX + "via")
    layer1.cleanup(via, tmp_root=str(link))
    assert not os.path.exists(via)
    d2 = _marked(link, name=layer1.PREFIX + "old")
    old = layer1.time.time() - layer1.STALE_SECONDS - 10
    os.utime(os.path.join(d2, layer1.MARKER_NAME), (old, old))
    removed = layer1.sweep_stale(str(link))
    assert [os.path.basename(p) for p in removed] == [layer1.PREFIX + "old"] and not os.path.exists(d2)
    sym_root = os.path.join(str(real), layer1.PREFIX + "symroot")
    os.symlink(str(tmp_path), sym_root)
    with pytest.raises(layer1.Layer1Refusal):
        layer1.cleanup(sym_root, tmp_root=str(link))


def test_cleanup_error_does_not_mask_arm_exception(spec, monkeypatch):
    common = layer1._common()
    real = common.remove_tree

    def fail(path):
        raise common.ArmRefusal("could not remove")

    def sweep_leftovers():
        monkeypatch.setattr(common, "remove_tree", real)
        for name in left_over(spec):
            layer1.cleanup(os.path.join(spec.tmp_root, name), tmp_root=spec.tmp_root)

    def arm(s):
        raise RuntimeError("A")
    monkeypatch.setattr(common, "remove_tree", fail)
    try:
        with pytest.raises(RuntimeError) as exc:
            layer1.run_scenario(arm, spec)
        assert str(exc.value).startswith("A")                   # the arm's own exception, not the cleanup refusal
        assert not isinstance(exc.value, layer1.Layer1Refusal)
        sweep_leftovers()
        monkeypatch.setattr(common, "remove_tree", fail)
        with pytest.raises(layer1.Layer1Refusal) as exc2:
            layer1.run_scenario(FakeArm(), spec)
        assert exc2.value.code == "cleanup-failed"
    finally:
        sweep_leftovers()


def test_sigterm_runs_cleanup(spec):
    ignored = []
    outer = signal.signal(signal.SIGTERM, lambda *a: ignored.append(1))   # without layer1's handler the signal is ignored here
    try:
        def arm(s):
            os.kill(os.getpid(), signal.SIGTERM)
            return {}
        with pytest.raises(SystemExit) as exc:
            layer1.run_scenario(arm, spec)
        assert exc.value.code == 143 and ignored == []
        assert left_over(spec) == []
        restored = signal.getsignal(signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, outer)
    assert restored is not signal.SIG_DFL and restored.__name__ == "<lambda>"


def test_sigterm_handler_restored_when_previous_not_python(spec, monkeypatch):
    """signal.signal returns None for a handler not installed from Python; that must restore SIG_DFL."""
    real = signal.signal
    calls = []

    def shim(num, handler):
        calls.append((num, handler))
        real(num, handler)
        return None if len(calls) == 1 else signal.SIG_DFL
    monkeypatch.setattr(layer1.signal, "signal", shim)
    outer = signal.getsignal(signal.SIGTERM)
    try:
        layer1.run_scenario(FakeArm(), spec)
        assert calls[-1] == (signal.SIGTERM, signal.SIG_DFL)
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    finally:
        real(signal.SIGTERM, outer)


def test_sigterm_handler_restored_after_normal_run(spec):
    real = signal.signal
    outer = real(signal.SIGTERM, signal.SIG_DFL)
    try:
        layer1.run_scenario(FakeArm(), spec)
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    finally:
        real(signal.SIGTERM, outer)


def _refused(tmp_path, source_repo, deadline, code):
    spec = make_spec(tmp_path, source_repo, deadline=deadline)
    with pytest.raises(layer1.Layer1Refusal) as exc:
        layer1.run_scenario(FakeArm(), spec)
    assert exc.value.code == code
    assert left_over(spec) == []


def test_deadline_is_absolute_monotonic_instant_on_a_large_clock(tmp_path, source_repo, monkeypatch):
    """run_claude takes timeout = deadline - monotonic(); a legit monotonic()+600 must pass however large the clock."""
    monkeypatch.setattr(layer1, "_monotonic", lambda: 50000.0)
    spec = make_spec(tmp_path, source_repo, deadline=50600.0)
    assert layer1.run_scenario(FakeArm(), spec)["sessions"]


@pytest.mark.parametrize("offset", [layer1.STALE_SECONDS, layer1.STALE_SECONDS - layer1.BUILD_MARGIN_SECONDS,
                                    layer1.STALE_SECONDS * 10])
def test_deadline_remaining_not_below_stale_window_refused(tmp_path, source_repo, offset, monkeypatch):
    monkeypatch.setattr(layer1, "_monotonic", lambda: 1000.0)   # frozen, so the boundary offset is exact
    _refused(tmp_path, source_repo, 1000.0 + offset, "deadline-exceeds-stale-window")


def test_deadline_just_inside_margin_accepted(tmp_path, source_repo):
    spec = make_spec(tmp_path, source_repo,
                     deadline=layer1.time.monotonic() + layer1.STALE_SECONDS - layer1.BUILD_MARGIN_SECONDS - 60)
    assert layer1.run_scenario(FakeArm(), spec)["sessions"]


@pytest.mark.parametrize("deadline", ["600", True, float("nan"), float("inf"), [1]])
def test_deadline_non_numeric_refused(tmp_path, source_repo, deadline, monkeypatch):
    monkeypatch.setattr(layer1, "_monotonic", lambda: 0.0)      # so True (== 1) would otherwise be a valid future instant
    _refused(tmp_path, source_repo, deadline, "deadline-invalid")


@pytest.mark.parametrize("delta", [0, -1, -10000])
def test_deadline_zero_negative_or_past_refused(tmp_path, source_repo, delta, monkeypatch):
    monkeypatch.setattr(layer1, "_monotonic", lambda: 5000.0)   # frozen, so delta 0 is exactly "now"
    _refused(tmp_path, source_repo, 5000.0 + delta, "deadline-invalid")


def test_sigterm_during_cleanup_cannot_interrupt_removal(spec, monkeypatch):
    common = layer1._common()
    real = common.remove_tree

    def hit_twice(path):
        os.kill(os.getpid(), signal.SIGTERM)                  # a second SIGTERM while removal is under way
        os.kill(os.getpid(), signal.SIGINT)
        real(path)
    monkeypatch.setattr(common, "remove_tree", hit_twice)
    outer = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    outer_int = signal.getsignal(signal.SIGINT)
    try:
        layer1.run_scenario(FakeArm(), spec)
        assert left_over(spec) == []
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
        assert signal.getsignal(signal.SIGINT) is outer_int
    finally:
        signal.signal(signal.SIGTERM, outer)


def test_signal_handlers_restored_when_cleanup_raises_baseexception(spec, monkeypatch):
    common = layer1._common()

    def boom(path):
        raise KeyboardInterrupt
    monkeypatch.setattr(common, "remove_tree", boom)
    outer = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    outer_int = signal.getsignal(signal.SIGINT)
    try:
        with pytest.raises(KeyboardInterrupt):
            layer1.run_scenario(FakeArm(), spec)
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
        assert signal.getsignal(signal.SIGINT) is outer_int
    finally:
        signal.signal(signal.SIGTERM, outer)
        monkeypatch.undo()
        for name in left_over(spec):
            layer1.cleanup(os.path.join(spec.tmp_root, name), tmp_root=spec.tmp_root)


def test_cleanup_removes_read_only_trees(tmp_path):
    d = _marked(tmp_path)
    sub = pathlib.Path(d, "sub")
    sub.mkdir()
    (sub / "f").write_text("x")
    sub.chmod(0o500)
    layer1.cleanup(d, tmp_root=str(tmp_path))
    assert not os.path.exists(d)


def test_sweep_stale_only_old_marked_own(tmp_path):
    old = layer1.time.time() - layer1.STALE_SECONDS - 100

    def age(d, t=old):
        marker = os.path.join(d, layer1.MARKER_NAME)
        if os.path.exists(marker):
            os.utime(marker, (t, t))
    fresh = _marked(tmp_path, name=layer1.PREFIX + "fresh")
    stale = _marked(tmp_path, name=layer1.PREFIX + "stale")
    unmarked = _marked(tmp_path, name=layer1.PREFIX + "unmarked", text=False)
    wrongname = _marked(tmp_path, name="other-stale")
    badtext = _marked(tmp_path, name=layer1.PREFIX + "badtext", text="nope")
    target = _marked(tmp_path, name="target")
    link = os.path.join(str(tmp_path), layer1.PREFIX + "link")
    os.symlink(target, link)
    for d in (stale, wrongname, badtext, target):
        age(d)
    young_unmarked = _marked(tmp_path, name=layer1.PREFIX + "young-unmarked", text=False)
    os.utime(unmarked, (old, old))
    removed = layer1.sweep_stale(str(tmp_path))
    assert [os.path.basename(p) for p in removed] == [layer1.PREFIX + "stale", layer1.PREFIX + "unmarked"]
    assert not os.path.exists(unmarked)                          # marker gone (partial rmtree) but old: healed
    for d in (fresh, young_unmarked, wrongname, badtext, target):
        assert os.path.isdir(d)
    assert os.path.islink(link)


def test_sweep_never_removes_markerless_foreign_or_linked_dirs(tmp_path, monkeypatch):
    old = layer1.time.time() - layer1.STALE_SECONDS - 100
    target = _marked(tmp_path, name="victim", text=False)
    os.utime(target, (old, old))
    link = os.path.join(str(tmp_path), layer1.PREFIX + "link")
    os.symlink(target, link)
    os.utime(link, (old, old), follow_symlinks=False)           # the link itself looks stale
    foreign = _marked(tmp_path, name=layer1.PREFIX + "foreign", text=False)
    os.utime(foreign, (old, old))
    real_uid = os.getuid()
    monkeypatch.setattr(layer1.os, "getuid", lambda: real_uid + 1)
    assert layer1.sweep_stale(str(tmp_path)) == []
    monkeypatch.undo()
    for d in (target, foreign):
        assert os.path.isdir(d)
    assert os.path.islink(link)


def test_run_scenario_sweeps_stale_roots_first(spec):
    stale = _marked(spec.tmp_root, name=layer1.PREFIX + "stale")
    old = layer1.time.time() - layer1.STALE_SECONDS - 100
    os.utime(os.path.join(stale, layer1.MARKER_NAME), (old, old))
    run(spec)
    assert not os.path.exists(stale)


def test_autouse_guard_blocks_real_tmp():
    with pytest.raises(AssertionError):
        layer1.sweep_stale()


# --------------------------------------------------------------------------------------------- step 10


def test_readme_gesture_runs():
    if os.environ.get("SIGMA_LAYER1_README_GESTURE"):
        return
    text = README.read_text(encoding="utf-8")
    block = re.search(r"## Layer 1 scenario.*?```\n(python\S* -m pytest [^\n#]*?)\s*(?:#[^\n]*)?\n```", text, re.S)
    assert block, "README has no documented Layer 1 gesture"
    argv = shlex.split(block.group(1))
    env = dict(os.environ, SIGMA_LAYER1_README_GESTURE="1")
    probe = subprocess.run([argv[0], "-c", "import pytest"], cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert probe.returncode == 0, "%s cannot import pytest here: %s" % (argv[0], probe.stderr[-200:])
    done = subprocess.run(argv, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out[-800:]
    passed = re.search(r"(\d+) passed", out)
    assert passed and int(passed.group(1)) >= 1, out[-400:]
    assert "no tests ran" not in out
    assert "test_readme_gesture_runs" not in out

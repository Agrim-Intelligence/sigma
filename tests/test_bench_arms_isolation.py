"""Hermetic controls for the #354 benchmark arms and isolation rules.

Three arms exist: A1 ``sigma`` (clean plugin export), A2 ``plain`` and A3 ``matched`` (the plain agent
retried until visible tests pass or token spend reaches A1's token spend).  Every test drives the arms against a fake
``claude`` executable written here: no model, no network, no money, and ``HOME`` is a temp directory so the
operator's real ``~/.claude/plugins`` is never read or touched.

Refusal tests assert the refusal's own text, because several different refusals share exit code 2.
Tests gate on ``_need`` so that a missing feature is an assertion failure, not an import error.
"""
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import time

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCH_PATH = ROOT / "evals" / "bench" / "bench.py"
PHASE_REPORT_PATH = ROOT / "skills" / "sigma-loop" / "scripts" / "phase_report.py"
SENTINEL = "HIDDEN-SENTINEL-9f2c"

FAKE_CLAUDE = r'''#!__PY__
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
if "--version" in args:
    print("fake-claude 0.0.1")
    sys.exit(0)
env = os.environ


def opt(name):
    return args[args.index(name) + 1] if name in args else None


sys.stdin.read()
state = pathlib.Path(env["SIGMA_FAKE_STATE"])
state.mkdir(parents=True, exist_ok=True)
counter = state / "count"
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
cwd = pathlib.Path.cwd()
scratch = pathlib.Path(env["SIGMA_FAKE_SCRATCH"])
elsewhere, sentinel = [], False
if scratch.is_dir():
    for path in scratch.rglob("*"):
        if not path.is_file():
            continue
        if cwd not in path.parents and (path.name == "attempt.txt" or path.name.startswith("solution-")):
            elsewhere.append(path.name)
        try:
            if b"HIDDEN-SENTINEL" in path.read_bytes():
                sentinel = True
        except OSError:
            pass
plugin = opt("--plugin-dir")
entries = forbidden = None
if plugin:
    entries = sorted(os.listdir(plugin))
    base = pathlib.Path(plugin)
    forbidden = any(part in ("evals", "tests", "docs")
                    for p in base.rglob("*") for part in p.relative_to(base).parts)
record = {
    "n": n, "argv": args, "cwd": str(cwd), "home": env.get("HOME"),
    "config": env.get("CLAUDE_CONFIG_DIR"), "codex": env.get("CODEX_HOME"), "tmp": env.get("TMPDIR"),
    "pre_existing": sorted(p.name for p in cwd.iterdir()
                           if p.name == "attempt.txt" or p.name.startswith("solution-")),
    "elsewhere": elsewhere, "sentinel": sentinel, "plugin_dir": plugin,
    "plugin_entries": entries, "plugin_forbidden": forbidden, "leak": env.get("LEAK"),
    "passthrough": env.get("SIGMA_FAKE_PASSTHROUGH"),
    "bench_vars": sorted(k for k in env if k.startswith("SIGMA_BENCH_")),
}
with open(env["SIGMA_FAKE_CLAUDE_LOG"], "a") as handle:
    handle.write(json.dumps(record) + "\n")
(cwd / "attempt.txt").write_text(str(n))
(cwd / ("solution-%d.txt" % n)).write_text("solution")
pass_on = int(env.get("SIGMA_FAKE_PASS_ON_CALL", "0"))
if pass_on and n >= pass_on:
    (cwd / "fixed.txt").write_text("fixed")
if (cwd / "stale.txt").exists():
    (cwd / "stale.txt").unlink()
touch = env.get("SIGMA_FAKE_TOUCH")
if touch and n == int(env.get("SIGMA_FAKE_TOUCH_ON", "1")):
    pathlib.Path(touch).write_text("changed")
tokens = int(env.get("SIGMA_FAKE_SIGMA_TOKENS", "250000")) if plugin \
    else int(env.get("SIGMA_FAKE_TOKENS", "100000"))
limited = env.get("SIGMA_FAKE_RATE_LIMIT_ON") and n >= int(env["SIGMA_FAKE_RATE_LIMIT_ON"]) \
    and not (env.get("SIGMA_FAKE_RATE_LIMIT_UNTIL") and n > int(env["SIGMA_FAKE_RATE_LIMIT_UNTIL"]))
if env.get("SIGMA_FAKE_ZERO_TOKENS_ON") and n == int(env["SIGMA_FAKE_ZERO_TOKENS_ON"]):
    tokens = 0
skip = env.get("SIGMA_FAKE_NO_TRANSCRIPT_ON")
if not (skip and n == int(skip)):
    transcript = pathlib.Path(env["CLAUDE_CONFIG_DIR"]) / "projects" / "-fake" / (opt("--session-id") + ".jsonl")
    transcript.parent.mkdir(parents=True, exist_ok=True)
    turn = json.dumps({
        "type": "assistant", "timestamp": "2026-10-01T00:00:00Z",
        "message": {"id": "m%d" % n, "role": "assistant", "model": "claude-test",
                    "usage": {"input_tokens": 0, "output_tokens": tokens, "cache_read_input_tokens": 0,
                              "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                                 "ephemeral_1h_input_tokens": 0}}},
    }) + "\n"
    limit = json.dumps({
        "type": "assistant", "timestamp": "2026-10-01T00:00:01Z", "error": "rate_limit",
        "isApiErrorMessage": True, "apiErrorStatus": 429,
        "quotaLimits": {"status": "rejected", "resetsAt": 1790895600, "rateLimitType": "five_hour"},
        "message": {"id": "s%d" % n, "role": "assistant", "model": "<synthetic>",
                    "content": [{"type": "text", "text": "limit"}],
                    "usage": {"input_tokens": 0, "output_tokens": 0}}}) + "\n"
    if limited and env.get("SIGMA_FAKE_RATE_LIMIT_TRANSIENT"):
        transcript.write_text(limit + turn)  # real work followed the record
    else:
        transcript.write_text(turn + (limit if limited else ""))
if env.get("SIGMA_FAKE_LEAVE"):
    leftover = subprocess.Popen(["sleep", "300"])
    pathlib.Path(env["SIGMA_FAKE_LEAVE"]).write_text(str(leftover.pid))
if env.get("SIGMA_FAKE_TERM_PARENT"):
    import signal
    os.kill(os.getppid(), signal.SIGTERM)
    time.sleep(300)
if env.get("SIGMA_FAKE_SLEEP"):
    time.sleep(float(env["SIGMA_FAKE_SLEEP"]))
if limited and not env.get("SIGMA_FAKE_RATE_LIMIT_EXIT0"):
    sys.exit(1)
if env.get("SIGMA_FAKE_ZERO_TOKENS_ON") and n == int(env["SIGMA_FAKE_ZERO_TOKENS_ON"]):
    sys.exit(1)
if env.get("SIGMA_FAKE_HANG"):
    child = subprocess.Popen(["sleep", "300"])
    pathlib.Path(env["SIGMA_FAKE_HANG"]).write_text(str(child.pid))
    time.sleep(300)
'''

RATES = ("model,rate_kind,usd_per_mtok,usd_per_request,effective_from,effective_to,source\n" +
         "".join("claude-test,%s,%s,,2026-01-01 00:00:00,,test-local\n" % (kind, price)
                 for kind, price in (("input", "2"), ("output", "10"), ("cache_read", "0.2"),
                                     ("cache_write_5m", "2.5"), ("cache_write_1h", "4"))))


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bench():
    return _module(BENCH_PATH, "benchmark_arms")


def _need(module, name):
    assert hasattr(module, name), f"{name} is not implemented"
    return getattr(module, name)


@pytest.fixture(autouse=True)
def _world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "plugins").mkdir(parents=True)
    (home / ".claude" / "plugins" / "marketplace.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    for name in ("CI", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SIGMA_FAKE_CLAUDE_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("SIGMA_FAKE_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("SIGMA_FAKE_SCRATCH", str(tmp_path / "scratch"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(FAKE_CLAUDE.replace("__PY__", sys.executable), encoding="utf-8")
    fake.chmod(0o755)


def _plugins(tmp_path):
    return tmp_path / "home" / ".claude" / "plugins"


def _rates(tmp_path):
    path = tmp_path / "rates.csv"
    path.write_text(RATES, encoding="utf-8")
    return _module(PHASE_REPORT_PATH, "bench_arms_phase_report").load_rate_rows(path)


def _launcher(tmp_path, log=None):
    path = tmp_path / ("logging-sandbox" if log else "operator-sandbox")
    note = f'printf "%s\\n" "$*" >> "{log}"\n' if log else ""
    path.write_text("#!/bin/sh\n[ \"$1\" = \"--\" ] || exit 64\nshift\n" + note + "exec \"$@\"\n",
                    encoding="utf-8")
    path.chmod(0o700)
    return path


def _manifest(tmp_path, *, visible=None, files=None, ids=("one",), tag="tasks"):
    tasks = tmp_path / tag
    tasks.mkdir(exist_ok=True)
    rows = []
    for task_id in ids:
        source = tasks / task_id
        source.mkdir()
        (source / "visible.py").write_text("assert True\n", encoding="utf-8")
        for name, text in (files or {}).items():
            (source / name).write_text(text, encoding="utf-8")
        rows.append({"id": task_id, "prompt": f"complete {task_id}", "source": task_id,
                     "visible_command": visible or [sys.executable, "visible.py"],
                     "hidden_bundle": task_id})
    path = tasks / "manifest.json"
    path.write_text(json.dumps({"schema": "sigma.benchmark-tasks/v1", "tasks": rows}), encoding="utf-8")
    return path


def _hidden(tmp_path, ids=("one",), check="pass", root=None):
    root = root or tmp_path / "hidden"
    for task_id in ids:
        bundle = root / task_id
        bundle.mkdir(parents=True)
        (bundle / "sentinel.txt").write_text(SENTINEL, encoding="utf-8")
        (bundle / "verify.json").write_text(json.dumps({"command": [sys.executable, "-c", check]}),
                                            encoding="utf-8")
    return root


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test",
                    "-c", "commit.gpgsign=false", *args], cwd=repo, check=True,
                   capture_output=True)


def _repo(tmp_path, extra=None):
    repo = tmp_path / "sigma-repo"
    repo.mkdir()
    files = {".claude-plugin/plugin.json": "{}", "skills/x/SKILL.md": "# x\n", "hooks/h.sh": "#!/bin/sh\n",
             "tests/t.py": "assert True\n", "evals/e.py": "x = 1\n", "README.md": "readme\n"}
    files.update(extra or {})
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (repo / "hooks" / "h.sh").chmod(0o755)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
                         text=True).stdout.strip()
    return repo, sha


def _arm(bench, name, tmp_path, **kwargs):
    cls = _need(bench, name)
    options = dict(claude=str(tmp_path / "bin" / "claude"), model="claude-test",
                   permission_mode="acceptEdits", belt_usd=15.0, rates=_rates(tmp_path))
    options.update(kwargs)
    return cls(**options)


def _sigma(bench, tmp_path, repo, sha, **kwargs):
    return _arm(bench, "SigmaArm", tmp_path, repo=repo, commit=sha, **kwargs)


def _run(bench, tmp_path, arms, manifest, hidden, *, max_tokens=10**9, launcher=None, scratch=None,
         results="results.json", **kwargs):
    return bench.run_benchmark(manifest, arms, max_tokens=max_tokens, hidden_root=hidden,
                               results_path=tmp_path / results,
                               scratch_root=scratch or tmp_path / "scratch",
                               isolation_launcher=launcher or _launcher(tmp_path), **kwargs)


def _try(bench, fn):
    try:
        fn()
    except bench.BenchmarkRefusal as exc:
        return str(exc)
    return None


def _calls(tmp_path):
    log = tmp_path / "calls.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _budgets(calls):
    return [float(call["argv"][call["argv"].index("--max-budget-usd") + 1]) for call in calls]


def _row(report, arm):
    return [row for row in report["runs"] if row["arm"] == arm][0]


def _cli(tmp_path, *extra, arm="all", hidden=None, launcher=None, tag=""):
    manifest = _manifest(tmp_path, tag="tasks" + tag)
    hidden = hidden or _hidden(tmp_path, root=tmp_path / ("hidden" + tag))
    launcher = launcher or _launcher(tmp_path)
    argv = ["run", "--manifest", str(manifest), "--hidden-root", str(hidden),
            "--results", str(tmp_path / "results.json"), "--scratch-root", str(tmp_path / "scratch"),
            "--max-tokens", "1000000000", "--isolation-launcher", str(launcher), "--arm", arm,
            "--model", "claude-test", "--claude", str(tmp_path / "bin" / "claude")]
    return argv + list(extra)


FAILS = [sys.executable, "-c", "import sys; sys.exit(1)"]
FIXED = [sys.executable, "-c",
         "import pathlib, sys; sys.exit(0 if pathlib.Path('fixed.txt').exists() else 1)"]


def test_documented_cli_dry_run_prints_the_three_arms_and_their_isolation_facts(tmp_path, capsys):
    bench = _bench()
    _need(bench, "MatchedArm")
    repo, sha = _repo(tmp_path)
    argv = _cli(tmp_path, "--permission-mode", "acceptEdits", "--sigma-commit", sha,
                "--sigma-repo", str(repo), "--dry-run")

    assert bench.main(argv) == 0

    out = capsys.readouterr().out
    report = json.loads(out)
    assert [arm["arm"] for arm in report["arms"]] == ["sigma", "plain", "matched"]
    for arm in report["arms"]:
        assert arm["status"] == "planned, not observed"
        assert "not verified" in arm["containment"]
    export = report["arms"][0]["export"]
    assert export["top_level"] == [".claude-plugin", "hooks", "skills"]
    assert export["commit"] == sha
    assert "predecessor" not in out.lower()
    assert _calls(tmp_path) == []
    assert not (tmp_path / "results.json").exists()


def test_no_predecessor_option_or_path_exists(tmp_path):
    """Smoke: red only against an injected option, or once the three-arm surface is missing."""
    bench = _bench()
    for name in ("SigmaArm", "PlainArm", "MatchedArm"):
        _need(bench, name)
    with pytest.raises(SystemExit) as exc:
        bench.parse_args(["run", "--manifest", "m", "--hidden-root", "h", "--results", "r",
                          "--predecessor-dir", "p"])
    assert exc.value.code == 2
    sources = sorted((ROOT / "evals" / "bench").rglob("*.py"))
    assert sources
    assert [p.name for p in sources if "predecessor" in p.read_text(encoding="utf-8").lower()] == []


def test_hidden_root_inside_the_repository_is_refused_by_the_documented_cli(tmp_path, capsys):
    bench = _bench()
    _need(bench, "MatchedArm")
    inside = ROOT / "docs"
    assert inside.is_dir()
    argv = _cli(tmp_path, "--permission-mode", "acceptEdits", arm="plain", hidden=inside)

    assert bench.main(argv) == 2

    assert "outside the repository" in capsys.readouterr().err
    assert _calls(tmp_path) == []
    assert not (tmp_path / "results.json").exists()


def test_hidden_root_inside_the_scratch_root_is_refused(tmp_path):
    bench = _bench()
    scratch = tmp_path / "scratch"
    hidden = _hidden(tmp_path, root=scratch / "hidden")
    manifest = _manifest(tmp_path)
    arm = _arm(bench, "PlainArm", tmp_path)

    message = _try(bench, lambda: _run(bench, tmp_path, [arm], manifest, hidden, scratch=scratch))

    assert message is not None and "outside the scratch root" in message
    assert _calls(tmp_path) == []


def test_sigma_export_has_skills_and_no_tests_or_evals(tmp_path):
    bench = _bench()
    sigma = _need(bench, "arms_sigma")
    repo, sha = _repo(tmp_path)
    export = tmp_path / "export"

    info = sigma.export_plugin(repo, sha, export)

    assert sorted(p.name for p in export.iterdir()) == [".claude-plugin", "hooks", "skills"]
    assert (export / "skills" / "x" / "SKILL.md").is_file()
    assert not [p for p in export.rglob("*")
                if {"tests", "evals"} & set(p.relative_to(export).parts)]
    assert os.access(export / "hooks" / "h.sh", os.X_OK)
    assert info["commit"] == sha


def test_export_refuses_a_tree_that_carries_tests_or_evals(tmp_path):
    bench = _bench()
    sigma = _need(bench, "arms_sigma")
    refusal = _need(bench, "arms_common").ArmRefusal
    repo, sha = _repo(tmp_path, extra={"skills/x/tests/t.py": "assert True\n"})
    message = None
    try:
        sigma.export_plugin(repo, sha, tmp_path / "export")
    except refusal as exc:
        message = str(exc)

    assert message is not None and "tests" in message


def test_export_extractor_refuses_links_traversal_and_foreign_top_level_entries(tmp_path):
    bench = _bench()
    sigma = _need(bench, "arms_sigma")
    refusal = _need(bench, "arms_common").ArmRefusal

    def archive(*members):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for name, kind in members:
                info = tarfile.TarInfo(name)
                if kind == "link":
                    info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                    tar.addfile(info)
                else:
                    data = b"x"
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
        buffer.seek(0)
        return buffer

    cases = {
        "link": [("skills/x", "link")],
        "traversal": [("skills/../../escape", "file")],
        "absolute": [("/abs/escape", "file")],
        "foreign": [("docs/guide.md", "file")],
        "forbidden": [("skills/tests/t.py", "file")],
    }
    for label, members in cases.items():
        dest = tmp_path / ("out-" + label)
        message = None
        try:
            sigma.extract_tar(archive(*members), dest)
        except refusal as exc:
            message = str(exc)
        assert message is not None, label
        assert not dest.exists() or not list(dest.rglob("*")), label
    assert not (tmp_path / "escape").exists()
    sigma.extract_tar(archive(("skills/ok.md", "file")), tmp_path / "out-ok")
    assert (tmp_path / "out-ok" / "skills" / "ok.md").read_bytes() == b"x"


def test_sigma_arm_loads_the_clean_export_never_the_repository(tmp_path):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arm = _sigma(bench, tmp_path, repo, sha)

    report = _run(bench, tmp_path, [arm], _manifest(tmp_path), _hidden(tmp_path))

    [call] = _calls(tmp_path)
    plugin = pathlib.Path(call["plugin_dir"]).resolve()
    assert plugin not in (ROOT.resolve(), repo.resolve())
    assert str(plugin).startswith(str((tmp_path / "scratch").resolve()))
    assert call["plugin_entries"] == [".claude-plugin", "hooks", "skills"]
    assert call["plugin_forbidden"] is False
    assert report["provenance"]["sigma_commit"] == sha
    assert not plugin.exists()


def test_every_arm_runs_in_a_fresh_isolated_profile(tmp_path):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "PlainArm", tmp_path),
            _arm(bench, "MatchedArm", tmp_path)]
    manifest = _manifest(tmp_path, visible=FAILS)

    _run(bench, tmp_path, arms, manifest, _hidden(tmp_path))

    calls = _calls(tmp_path)
    assert len(calls) == 5  # sigma 1, plain 1, matched 3 (cap 2.5 reached on the third)
    real_home = str(tmp_path / "home")
    scratch = str((tmp_path / "scratch").resolve())
    for call in calls:
        values = [call["home"], call["config"], call["codex"]]
        assert len(set(values)) == 3
        assert real_home not in values
        assert all(os.path.realpath(v).startswith(scratch) for v in values)
        assert os.path.realpath(call["tmp"]).startswith(scratch), "TMPDIR must not be a shared channel"
    assert len({call["home"] for call in calls}) == len(calls)
    assert len({call["config"] for call in calls}) == len(calls)
    assert len({call["codex"] for call in calls}) == len(calls)
    assert len({call["tmp"] for call in calls}) == len(calls)


def test_environment_values_containing_the_hidden_root_are_dropped(tmp_path, monkeypatch):
    bench = _bench()
    hidden = _hidden(tmp_path)
    monkeypatch.setenv("LEAK", str(hidden) + "/one")
    monkeypatch.setenv("SIGMA_FAKE_PASSTHROUGH", "forwarded")
    arm = _arm(bench, "PlainArm", tmp_path)

    _run(bench, tmp_path, [arm], _manifest(tmp_path), hidden)

    [call] = _calls(tmp_path)
    assert call["leak"] is None
    assert call["passthrough"] == "forwarded"
    assert call["bench_vars"] == []
    assert "forwarded" not in (tmp_path / "results.json").read_text(encoding="utf-8")


def test_real_plugin_directory_change_aborts_the_whole_benchmark(tmp_path, monkeypatch):
    bench = _bench()
    arm = _arm(bench, "PlainArm", tmp_path)
    monkeypatch.setenv("SIGMA_FAKE_TOUCH", str(_plugins(tmp_path) / "touched"))

    message = _try(bench, lambda: _run(bench, tmp_path, [arm], _manifest(tmp_path), _hidden(tmp_path)))

    assert message is not None and "plugin directory changed" in message
    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert "plugin directory changed" in saved["aborted"]["reason"]
    operator = tmp_path / "operator-config" / "plugins"
    operator.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(operator.parent))
    monkeypatch.setenv("SIGMA_FAKE_TOUCH", str(operator / "touched"))
    (tmp_path / "state" / "count").unlink()
    again = _try(bench, lambda: _run(bench, tmp_path, [_arm(bench, "PlainArm", tmp_path)],
                                     _manifest(tmp_path, tag="tasks-2"),
                                     _hidden(tmp_path, root=tmp_path / "hidden-2"), results="second.json"))
    assert again is not None and "plugin directory changed" in again


def test_plugin_change_between_runs_is_caught_before_the_next_run_starts(tmp_path, monkeypatch):
    bench = _bench()
    arm = _arm(bench, "PlainArm", tmp_path)
    monkeypatch.setenv("SIGMA_FAKE_TOUCH", str(_plugins(tmp_path) / "touched"))
    manifest = _manifest(tmp_path, ids=("one", "two"))

    message = _try(bench, lambda: _run(bench, tmp_path, [arm], manifest,
                                       _hidden(tmp_path, ids=("one", "two"))))

    assert message is not None and "plugin directory changed" in message
    assert len(_calls(tmp_path)) == 1


def test_plugin_change_inside_an_a3_row_stops_before_the_next_attempt(tmp_path, monkeypatch):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]
    monkeypatch.setenv("SIGMA_FAKE_TOUCH", str(_plugins(tmp_path) / "touched"))
    monkeypatch.setenv("SIGMA_FAKE_TOUCH_ON", "2")

    message = _try(bench, lambda: _run(bench, tmp_path, arms, _manifest(tmp_path, visible=FAILS),
                                       _hidden(tmp_path)))

    assert message is not None and "plugin directory changed" in message
    assert len(_calls(tmp_path)) == 2


def test_abort_keeps_the_rows_already_paid_for(tmp_path, monkeypatch):
    bench = _bench()
    arm = _arm(bench, "PlainArm", tmp_path)
    monkeypatch.setenv("SIGMA_FAKE_NO_TRANSCRIPT_ON", "2")
    manifest = _manifest(tmp_path, ids=("one", "two"))

    message = _try(bench, lambda: _run(bench, tmp_path, [arm], manifest,
                                       _hidden(tmp_path, ids=("one", "two"))))

    assert message is not None
    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert [(row["task"], row["cost_usd"]) for row in saved["runs"] if row["status"] == "completed"] == [("one", 1.0)]
    assert [(row["task"], row["status"]) for row in saved["runs"]] == [("one", "completed"), ("two", "not-run")]
    assert saved["aborted"]["task"] == "two"
    assert saved["aborted"]["arm"] == "plain"
    assert saved["aborted"]["cost_usd_spent"] == 0.0
    assert saved["schema"] == "sigma.benchmark-results/v2" and saved["complete"] is False


def test_unpriced_run_stops_the_benchmark_instead_of_counting_zero(tmp_path, monkeypatch):
    bench = _bench()
    arm = _arm(bench, "PlainArm", tmp_path)
    monkeypatch.setenv("SIGMA_FAKE_NO_TRANSCRIPT_ON", "1")
    manifest = _manifest(tmp_path, ids=("one", "two"))

    message = _try(bench, lambda: _run(bench, tmp_path, [arm], manifest,
                                       _hidden(tmp_path, ids=("one", "two"))))

    assert message is not None and "no readable usage" in message
    assert len(_calls(tmp_path)) == 1
    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert [row["status"] for row in saved["runs"]] == ["not-run", "not-run"], "never recorded as a failure"
    assert saved["aborted"]["arm"] == "plain"


def test_exception_mid_row_writes_the_in_flight_spend_into_aborted(tmp_path, monkeypatch):
    bench = _bench()
    common = _need(bench, "arms_common")
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]
    real = common.run_bounded
    seen = []

    def interrupt_third_claude(argv, *args, **kwargs):
        if "-p" in argv:
            seen.append(argv)
            if len(seen) == 3:
                raise KeyboardInterrupt
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(common, "run_bounded", interrupt_third_claude)
    manifest = _manifest(tmp_path, visible=FAILS)

    with pytest.raises(KeyboardInterrupt):
        _run(bench, tmp_path, arms, manifest, _hidden(tmp_path))

    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert [row["arm"] for row in saved["runs"] if row["status"] == "completed"] == ["sigma"]
    assert [row["status"] for row in saved["runs"] if row["arm"] == "matched"] == ["not-run"]
    assert saved["aborted"]["reason"] == "KeyboardInterrupt"
    assert saved["aborted"]["arm"] == "matched"
    assert saved["aborted"]["cost_usd_spent"] == 1.0
    assert list((tmp_path / "scratch").iterdir()) == []


def test_matched_arm_stops_at_a_spend_cap_of_two_and_a_half_attempts(tmp_path):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]
    hidden = _hidden(tmp_path, check="import pathlib; assert pathlib.Path('attempt.txt').read_text() == '4'")

    report = _run(bench, tmp_path, arms, _manifest(tmp_path, visible=FAILS), hidden)

    row = _row(report, "matched")
    assert _row(report, "sigma")["cost_usd"] == 2.5
    assert len(_calls(tmp_path)) == 4
    assert row["cost_usd"] == 3.0
    assert "attempts=3" in row["reason"] and "stop=token-cap" in row["reason"]
    assert row["hidden_passed"] is True, "the LAST attempt's tree must be the scored one"


def test_matched_arm_stops_when_spend_equals_the_cap_exactly(tmp_path, monkeypatch):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    monkeypatch.setenv("SIGMA_FAKE_SIGMA_TOKENS", "200000")
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]

    report = _run(bench, tmp_path, arms, _manifest(tmp_path, visible=FAILS), _hidden(tmp_path))

    row = _row(report, "matched")
    assert _row(report, "sigma")["cost_usd"] == 2.0
    assert "attempts=2" in row["reason"] and "stop=token-cap" in row["reason"]
    assert row["cost_usd"] == 2.0


def test_matched_arm_stops_at_the_first_visible_pass_and_scores_it(tmp_path, monkeypatch):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]
    monkeypatch.setenv("SIGMA_FAKE_PASS_ON_CALL", "3")
    check = ("import pathlib; assert pathlib.Path('fixed.txt').exists(); "
             "assert pathlib.Path('attempt.txt').read_text() == '3'")

    report = _run(bench, tmp_path, arms, _manifest(tmp_path, visible=FIXED), _hidden(tmp_path, check=check))

    row = _row(report, "matched")
    assert len(_calls(tmp_path)) == 3
    assert row["cost_usd"] == 2.0
    assert "attempts=2" in row["reason"] and "stop=visible-pass" in row["reason"]
    assert row["visible_passed"] is True and row["hidden_passed"] is True


def test_matched_attempts_run_in_fresh_workdirs_and_replace_the_scored_tree(tmp_path):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]
    check = ("import pathlib; assert not pathlib.Path('stale.txt').exists(); "
             "assert pathlib.Path('attempt.txt').read_text() == '4'")
    manifest = _manifest(tmp_path, visible=FAILS, files={"stale.txt": "old"})

    report = _run(bench, tmp_path, arms, manifest, _hidden(tmp_path, check=check))

    calls = _calls(tmp_path)
    assert [call["pre_existing"] for call in calls] == [[], [], [], []]
    assert len({call["cwd"] for call in calls}) == 4
    assert [call["elsewhere"] for call in calls] == [[], [], [], []]
    assert _row(report, "matched")["hidden_passed"] is True


def test_matched_arm_stops_when_an_attempt_cannot_be_priced(tmp_path, monkeypatch):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]
    monkeypatch.setenv("SIGMA_FAKE_NO_TRANSCRIPT_ON", "3")

    message = _try(bench, lambda: _run(bench, tmp_path, arms, _manifest(tmp_path, visible=FAILS),
                                       _hidden(tmp_path)))

    assert message is not None and "no readable usage" in message
    assert len(_calls(tmp_path)) == 3
    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert saved["aborted"]["arm"] == "matched"
    assert saved["aborted"]["cost_usd_spent"] == 1.0


def test_matched_arm_stops_at_the_attempt_bound_and_the_row_deadline(tmp_path, monkeypatch):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    monkeypatch.setenv("SIGMA_FAKE_TOKENS", "1000")
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path, max_attempts=3)]
    report = _run(bench, tmp_path, arms, _manifest(tmp_path, visible=FAILS), _hidden(tmp_path))
    row = _row(report, "matched")
    assert "attempts=3" in row["reason"] and "stop=attempt-bound" in row["reason"]
    assert len(_calls(tmp_path)) == 4

    (tmp_path / "state" / "count").unlink()
    monkeypatch.setenv("SIGMA_FAKE_SLEEP", "0.4")
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path, max_attempts=50)]
    report = _run(bench, tmp_path, arms, _manifest(tmp_path, visible=FAILS, tag="tasks-2"),
                  _hidden(tmp_path, root=tmp_path / "hidden-2"), results="again.json", deadline_seconds=3)
    row = _row(report, "matched")
    assert "stop=deadline" in row["reason"]


def test_matched_arm_stops_when_the_global_token_ceiling_is_exhausted(tmp_path):
    """Control: without the ceiling stop the matched arm keeps retrying up to A1's token spend."""
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]

    report = _run(bench, tmp_path, arms, _manifest(tmp_path, visible=FAILS), _hidden(tmp_path),
                  max_tokens=400_000)

    row = _row(report, "matched")
    assert row["tokens"] == 200_000 and "attempts=2" in row["reason"] and "stop=ceiling" in row["reason"]
    assert report["tokens_spent"] == 450_000, "the last attempt overshoots by up to one attempt, and it is counted"


def test_matched_arm_without_a_prior_a1_run_is_refused(tmp_path):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    manifest, hidden = _manifest(tmp_path), _hidden(tmp_path)

    alone = _try(bench, lambda: _run(bench, tmp_path, [_arm(bench, "MatchedArm", tmp_path)], manifest, hidden))
    wrong_order = _try(bench, lambda: _run(bench, tmp_path, [_arm(bench, "MatchedArm", tmp_path),
                                                              _sigma(bench, tmp_path, repo, sha)],
                                           manifest, hidden))

    assert alone is not None and "sigma" in alone
    assert wrong_order is not None and "sigma" in wrong_order
    assert _calls(tmp_path) == []
    assert not (tmp_path / "results.json").exists()


def test_matched_arm_refuses_more_than_one_repeat(tmp_path):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]

    message = _try(bench, lambda: _run(bench, tmp_path, arms, _manifest(tmp_path), _hidden(tmp_path),
                                       repeats=2))

    assert message is not None and "one repeat" in message
    assert _calls(tmp_path) == []


def test_every_claude_run_gets_the_configured_per_run_belt_which_is_indicative_not_a_ceiling(tmp_path):
    """The host has no per-run token flag: the belt is its client-side dollar estimate, a runaway guard only.

    The token ceiling is enforced between runs; it is not turned into a per-run budget.
    """
    bench = _bench()
    manifest = _manifest(tmp_path, ids=("one", "two"))
    hidden = _hidden(tmp_path, ids=("one", "two"))

    _run(bench, tmp_path, [_arm(bench, "PlainArm", tmp_path, belt_usd=15.0)], manifest, hidden,
         max_tokens=10_000_000)
    assert _budgets(_calls(tmp_path)) == [15.0, 15.0]

    (tmp_path / "calls.jsonl").unlink()
    _run(bench, tmp_path, [_arm(bench, "PlainArm", tmp_path, belt_usd=3.0)], manifest, hidden,
         max_tokens=10_000_000, results="second.json")
    assert _budgets(_calls(tmp_path)) == [3.0, 3.0]


def test_a_later_arm_cannot_read_an_earlier_arms_tree_or_the_hidden_bundle(tmp_path):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "PlainArm", tmp_path)]
    ids = ("one", "two")

    _run(bench, tmp_path, arms, _manifest(tmp_path, ids=ids), _hidden(tmp_path, ids=ids))

    calls = _calls(tmp_path)
    assert len(calls) == 4
    assert [call["elsewhere"] for call in calls] == [[], [], [], []]
    assert [call["sentinel"] for call in calls] == [False, False, False, False]
    assert list((tmp_path / "scratch").iterdir()) == []


def test_nonempty_scratch_root_is_refused_and_an_abort_leaves_none_behind(tmp_path, monkeypatch):
    bench = _bench()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "leftover").mkdir()
    manifest, hidden = _manifest(tmp_path), _hidden(tmp_path)
    arm = _arm(bench, "PlainArm", tmp_path)

    message = _try(bench, lambda: _run(bench, tmp_path, [arm], manifest, hidden, scratch=scratch))

    assert message is not None and "scratch root must be empty" in message
    assert _calls(tmp_path) == []
    (scratch / "leftover").rmdir()
    monkeypatch.setenv("SIGMA_FAKE_NO_TRANSCRIPT_ON", "1")
    assert _try(bench, lambda: _run(bench, tmp_path, [arm], manifest, hidden, scratch=scratch)) is not None
    assert list(scratch.iterdir()) == []
    monkeypatch.delenv("SIGMA_FAKE_NO_TRANSCRIPT_ON")
    (tmp_path / "state" / "count").unlink()
    (tmp_path / "results.json").unlink()  # the aborted file is the cursor; starting over is deliberate
    assert _try(bench, lambda: _run(bench, tmp_path, [arm], manifest, hidden, scratch=scratch)) is None


def test_hung_commands_are_killed_with_their_whole_process_group(tmp_path, monkeypatch):
    bench = _bench()
    pidfile = tmp_path / "grandchild.pid"
    monkeypatch.setenv("SIGMA_FAKE_HANG", str(pidfile))
    arm = _arm(bench, "PlainArm", tmp_path)
    pid = None
    try:
        report = _run(bench, tmp_path, [arm], _manifest(tmp_path), _hidden(tmp_path), deadline_seconds=2)
        pid = int(pidfile.read_text())
        alive = True
        for _ in range(60):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
                break
            time.sleep(0.05)
        assert not alive, "the agent's grandchild survived the group kill"
        assert "wall-clock limit" in report["runs"][0]["reason"]
    finally:
        if pid:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


def test_a_child_left_behind_by_a_normal_exit_is_killed(tmp_path, monkeypatch):
    bench = _bench()
    _need(bench, "BenchmarkSignal")
    pidfile = tmp_path / "left.pid"
    monkeypatch.setenv("SIGMA_FAKE_LEAVE", str(pidfile))
    arm = _arm(bench, "PlainArm", tmp_path)
    pid = None
    try:
        _run(bench, tmp_path, [arm], _manifest(tmp_path), _hidden(tmp_path))
        pid = int(pidfile.read_text())
        alive = True
        for _ in range(60):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
                break
            time.sleep(0.05)
        assert not alive, "a child outliving claude's normal exit kept running"
    finally:
        if pid:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


def test_sigterm_unwinds_kills_children_and_keeps_the_report(tmp_path, monkeypatch):
    import signal
    bench = _bench()
    _need(bench, "BenchmarkSignal")
    monkeypatch.setenv("SIGMA_FAKE_TERM_PARENT", "1")
    arm = _arm(bench, "PlainArm", tmp_path)
    before = signal.getsignal(signal.SIGTERM)

    with pytest.raises(bench.BenchmarkSignal):
        _run(bench, tmp_path, [arm], _manifest(tmp_path, ids=("one", "two")),
             _hidden(tmp_path, ids=("one", "two")))

    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert saved["aborted"]["reason"] == "signal SIGTERM"
    assert saved["aborted"]["arm"] == "plain"
    assert list((tmp_path / "scratch").iterdir()) == []
    assert signal.getsignal(signal.SIGTERM) == before
    assert len(_calls(tmp_path)) == 1


def test_scratch_root_inside_the_repository_is_refused(tmp_path):
    bench = _bench()
    arm = _arm(bench, "PlainArm", tmp_path)
    scratch = ROOT / "docs" / "never-created-scratch"

    message = _try(bench, lambda: _run(bench, tmp_path, [arm], _manifest(tmp_path), _hidden(tmp_path),
                                       scratch=scratch))

    assert message is not None and "scratch root must be outside the repository" in message
    assert not scratch.exists()
    assert _calls(tmp_path) == []


def test_scoring_commands_run_through_the_launcher(tmp_path):
    bench = _bench()
    log = tmp_path / "launcher.log"
    launcher = _launcher(tmp_path, log=log)
    arm = _arm(bench, "PlainArm", tmp_path)

    _run(bench, tmp_path, [arm], _manifest(tmp_path), _hidden(tmp_path, check="print(1)"), launcher=launcher)

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4, lines
    assert "--version" in lines[0]
    assert " -p " in lines[1]
    assert "visible.py" in lines[2]
    assert "print(1)" in lines[3]


def test_live_run_refuses_without_a_pinned_commit_a_permission_mode_or_a_posix_host(tmp_path, capsys, monkeypatch):
    bench = _bench()
    common = _need(bench, "arms_common")
    repo, sha = _repo(tmp_path)

    assert bench.main(_cli(tmp_path, "--sigma-commit", sha, "--sigma-repo", str(repo), tag="-a")) == 2
    assert "--permission-mode" in capsys.readouterr().err
    assert bench.main(_cli(tmp_path, "--permission-mode", "acceptEdits", "--sigma-repo", str(repo),
                           arm="sigma", tag="-b")) == 2
    assert "--sigma-commit" in capsys.readouterr().err
    monkeypatch.setattr(common, "IS_POSIX", False)
    assert bench.main(_cli(tmp_path, "--permission-mode", "acceptEdits", arm="plain", tag="-c")) == 2
    assert "POSIX" in capsys.readouterr().err
    assert _calls(tmp_path) == []


def test_results_path_inside_the_scratch_root_is_refused(tmp_path):
    bench = _bench()
    scratch = tmp_path / "scratch"
    arm = _arm(bench, "PlainArm", tmp_path)

    message = _try(bench, lambda: bench.run_benchmark(
        _manifest(tmp_path), [arm], max_tokens=1000, hidden_root=_hidden(tmp_path),
        results_path=scratch / "results.json", scratch_root=scratch,
        isolation_launcher=_launcher(tmp_path)))

    assert message is not None and "results path must be outside the scratch root" in message
    assert _calls(tmp_path) == []


def test_subclassed_live_arm_is_refused(tmp_path):
    bench = _bench()
    plain = _need(bench, "PlainArm")
    invoked = []

    class Evil(plain):
        def run(self, *_args):
            invoked.append(True)
            return bench.ArmRun(cost_usd=0.0)

    arm = Evil(claude=str(tmp_path / "bin" / "claude"), model="claude-test", permission_mode="acceptEdits",
               belt_usd=1.0)

    message = _try(bench, lambda: _run(bench, tmp_path, [arm], _manifest(tmp_path), _hidden(tmp_path)))

    assert message is not None and "enforceable isolation boundary" in message
    assert invoked == []


def test_tree_digest_sees_changed_added_and_removed_files(tmp_path):
    bench = _bench()
    common = _need(bench, "arms_common")
    tree = tmp_path / "plugins"
    tree.mkdir()
    (tree / "a.txt").write_text("one", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "o.txt").write_text("o", encoding="utf-8")
    (tree / "link").symlink_to(outside)
    base = common.tree_digest(tree)

    assert common.tree_digest(tree) == base
    (outside / "o.txt").write_text("changed outside", encoding="utf-8")
    assert common.tree_digest(tree) == base, "symlinks must not be followed"
    (tree / "a.txt").write_text("two", encoding="utf-8")
    changed = common.tree_digest(tree)
    assert changed != base
    (tree / "b.txt").write_text("new", encoding="utf-8")
    added = common.tree_digest(tree)
    assert added != changed
    (tree / "b.txt").unlink()
    assert common.tree_digest(tree) == changed
    assert common.tree_digest(tmp_path / "missing") == common.tree_digest(tmp_path / "also-missing") == "absent"


# -- subscription auth (2026-10-06): tokens, batches, a resume-safe cursor, rate-limit stop ------

def _plain_cli(tmp_path, ids, *extra):
    """The documented gesture for the plain arm on ``ids`` (built once per test: the manifest dirs are made once)."""
    manifest = _manifest(tmp_path, ids=ids)
    hidden = _hidden(tmp_path, ids=ids)
    return ["run", "--manifest", str(manifest), "--hidden-root", str(hidden),
            "--results", str(tmp_path / "results.json"), "--scratch-root", str(tmp_path / "scratch"),
            "--max-tokens", "1000000000", "--isolation-launcher", str(_launcher(tmp_path)), "--arm", "plain",
            "--model", "claude-test", "--permission-mode", "acceptEdits",
            "--claude", str(tmp_path / "bin" / "claude"), *extra]


def _with(argv, flag, value):
    out = list(argv)
    out[out.index(flag) + 1] = value
    return out


def _saved(tmp_path, name="results.json"):
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


def _statuses(saved):
    return [(row["task"], row["arm"], row["status"]) for row in saved["runs"]]


def test_a_rate_limited_pair_is_not_run_the_harness_stops_and_resume_never_reruns_done_pairs(
        tmp_path, monkeypatch, capsys):
    """Controls: scoring the throttled tree makes row two a failure; dropping the stop starts task three;
    dropping the skip of completed pairs re-runs task one on resume."""
    bench = _bench()
    argv = _plain_cli(tmp_path, ("one", "two", "three"))
    monkeypatch.setenv("SIGMA_FAKE_RATE_LIMIT_ON", "2")
    monkeypatch.setenv("SIGMA_FAKE_RATE_LIMIT_UNTIL", "2")

    assert bench.main(argv) == 75
    err = capsys.readouterr().err
    assert "resume" in err and "1790895600" in err
    saved = _saved(tmp_path)
    assert _statuses(saved) == [("one", "plain", "completed"), ("two", "plain", "not-run"),
                                ("three", "plain", "not-run")]
    two = saved["runs"][1]
    assert "rate limit" in two["reason"] and "1790895600" in two["reason"]
    assert two["visible_passed"] is None and two["hidden_passed"] is None, "a throttled run is never scored"
    assert saved["stop"]["kind"] == "rate-limit" and saved["stop"]["resets_at"] == 1790895600
    assert saved["complete"] is False
    assert saved["tokens_unscored"] == 100_000 and saved["tokens_spent"] == 200_000
    assert len(_calls(tmp_path)) == 2, "task three must not start after the host said no"

    assert bench.main(argv + ["--resume"]) == 0
    saved = _saved(tmp_path)
    assert [status for _, _, status in _statuses(saved)] == ["completed"] * 3
    assert saved["complete"] is True and saved["stop"] is None
    assert len(_calls(tmp_path)) == 4, "resume ran tasks two and three only"
    assert saved["tokens_spent"] == 3 * 100_000 + 100_000, "rows plus the tokens burned by the discarded run"


def test_a_throttled_matched_attempt_makes_the_whole_pair_not_run(tmp_path, monkeypatch):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]
    monkeypatch.setenv("SIGMA_FAKE_RATE_LIMIT_ON", "3")

    report = _run(bench, tmp_path, arms, _manifest(tmp_path, visible=FAILS), _hidden(tmp_path))

    assert _statuses(report) == [("one", "sigma", "completed"), ("one", "matched", "not-run")]
    assert "rate limit" in _row(report, "matched")["reason"]
    assert report["tokens_unscored"] == 200_000, "both attempts were paid for and are counted"
    assert report["stop"]["kind"] == "rate-limit" and len(_calls(tmp_path)) == 3


def test_a_rate_limit_record_on_a_run_that_exited_zero_is_scored_and_says_so(tmp_path, monkeypatch):
    bench = _bench()
    monkeypatch.setenv("SIGMA_FAKE_RATE_LIMIT_ON", "1")
    monkeypatch.setenv("SIGMA_FAKE_RATE_LIMIT_EXIT0", "1")
    monkeypatch.setenv("SIGMA_FAKE_RATE_LIMIT_TRANSIENT", "1")

    report = _run(bench, tmp_path, [_arm(bench, "PlainArm", tmp_path)], _manifest(tmp_path), _hidden(tmp_path))

    row = _row(report, "plain")
    assert row["status"] == "completed" and row["hidden_passed"] is True
    assert "rate-limit record" in row["reason"] and report["stop"] is None


def test_a_session_that_ended_on_the_limit_is_throttled_even_if_the_host_exited_zero(tmp_path, monkeypatch):
    """The exit status of a throttled ``claude -p`` is unmeasured, so it must not be what decides."""
    bench = _bench()
    monkeypatch.setenv("SIGMA_FAKE_RATE_LIMIT_ON", "1")
    monkeypatch.setenv("SIGMA_FAKE_RATE_LIMIT_EXIT0", "1")

    report = _run(bench, tmp_path, [_arm(bench, "PlainArm", tmp_path)], _manifest(tmp_path), _hidden(tmp_path))

    row = _row(report, "plain")
    assert row["status"] == "not-run" and row["hidden_passed"] is None
    assert report["stop"]["kind"] == "rate-limit"


def test_batch_pairs_stops_cleanly_and_resume_continues_from_the_cursor(tmp_path, capsys):
    """The documented gesture: ``--batch-pairs N`` first, then ``--resume --batch-pairs N``."""
    bench = _bench()
    argv = _plain_cli(tmp_path, ("one", "two", "three"), "--batch-pairs", "2")

    assert bench.main(argv) == 0
    saved = _saved(tmp_path)
    assert _statuses(saved) == [("one", "plain", "completed"), ("two", "plain", "completed"),
                                ("three", "plain", "not-run")]
    assert saved["stop"]["kind"] == "batch" and saved["complete"] is False
    assert len(_calls(tmp_path)) == 2
    assert "batch of 2 pair(s) done" in saved["runs"][2]["reason"]

    assert bench.main(argv + ["--resume"]) == 0
    saved = _saved(tmp_path)
    assert saved["complete"] is True and len(_calls(tmp_path)) == 3
    assert capsys.readouterr().out.count('"schema"') == 2


def test_resume_needs_the_cursor_and_an_existing_cursor_needs_resume(tmp_path, capsys):
    bench = _bench()
    argv = _plain_cli(tmp_path, ("one", "two"), "--batch-pairs", "1")
    other = _with(argv, "--results", str(tmp_path / "elsewhere.json"))

    assert bench.main(other + ["--resume"]) == 2
    assert "--resume needs an existing results file" in capsys.readouterr().err
    assert bench.main(argv) == 0
    capsys.readouterr()
    assert bench.main(argv) == 2
    err = capsys.readouterr().err
    assert "already exists" in err and "--resume" in err
    assert len(_calls(tmp_path)) == 1, "neither refusal started a run"


def test_resume_refuses_a_changed_condition_and_a_lowered_ceiling_but_allows_a_raised_one(tmp_path, capsys):
    bench = _bench()
    argv = _plain_cli(tmp_path, ("one", "two"), "--batch-pairs", "1")
    assert bench.main(argv) == 0
    capsys.readouterr()

    assert bench.main(_with(argv, "--model", "claude-other") + ["--resume"]) == 2
    assert "model" in capsys.readouterr().err
    assert bench.main(_with(argv, "--max-tokens", "999") + ["--resume"]) == 2
    assert "lowered" in capsys.readouterr().err
    assert len(_calls(tmp_path)) == 1

    assert bench.main(_with(argv, "--max-tokens", "2000000000") + ["--resume"]) == 0
    saved = _saved(tmp_path)
    assert saved["max_tokens"] == 2_000_000_000
    assert saved["ceiling_changes"] == [{"from": 1_000_000_000, "to": 2_000_000_000}], "never silent"


def test_resume_refuses_when_the_hidden_bundles_changed_between_batches(tmp_path, capsys):
    bench = _bench()
    argv = _plain_cli(tmp_path, ("one", "two"), "--batch-pairs", "1")
    assert bench.main(argv) == 0
    capsys.readouterr()
    (tmp_path / "hidden" / "two" / "extra.txt").write_text("tampered", encoding="utf-8")

    assert bench.main(argv + ["--resume"]) == 2
    assert "hidden_bundles_sha256" in capsys.readouterr().err
    assert len(_calls(tmp_path)) == 1


def test_a_second_invocation_cannot_use_a_cursor_that_is_already_in_use(tmp_path):
    """Deterministic in-process control at the seam (a forked race would not be sensitive enough)."""
    import fcntl
    bench = _bench()
    with open(tmp_path / "results.json.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        message = _try(bench, lambda: _run(bench, tmp_path, [_arm(bench, "PlainArm", tmp_path)],
                                           _manifest(tmp_path), _hidden(tmp_path)))

    assert message is not None and "already running" in message
    assert _calls(tmp_path) == [] and not (tmp_path / "results.json").exists()


def test_the_in_flight_marker_is_written_before_the_run_starts_and_cleared_after(tmp_path, monkeypatch):
    bench = _bench()
    seen = []
    real = bench._run_arm

    def spy(arm, *args, **kwargs):
        seen.append(_saved(tmp_path).get("in_flight"))
        return real(arm, *args, **kwargs)

    monkeypatch.setattr(bench, "_run_arm", spy)
    report = _run(bench, tmp_path, [_arm(bench, "PlainArm", tmp_path)], _manifest(tmp_path), _hidden(tmp_path))

    assert seen == [{"task": "one", "arm": "plain", "repeat": 1}]
    assert "in_flight" not in report and "in_flight" not in _saved(tmp_path)


def test_a_pair_that_died_in_flight_is_counted_unknown_and_rerun_not_failed(tmp_path, capsys):
    """A SIGKILL between a paid run and its row leaves ``in_flight``; resume must not hide that spend."""
    bench = _bench()
    argv = _plain_cli(tmp_path, ("one", "two"), "--batch-pairs", "1")
    assert bench.main(argv) == 0
    saved = _saved(tmp_path)
    saved["in_flight"] = {"task": "two", "arm": "plain", "repeat": 1}
    (tmp_path / "results.json").write_text(json.dumps(saved), encoding="utf-8")

    assert bench.main(argv + ["--resume"]) == 0
    saved = _saved(tmp_path)
    assert saved["unknown_runs"] == 1 and saved["complete"] is True
    assert "in_flight" not in saved
    assert "lower bound" in saved["tokens_note"]


def test_a_run_with_zero_usage_is_refused_not_scored_and_not_recorded_as_a_failure(tmp_path, monkeypatch):
    bench = _bench()
    monkeypatch.setenv("SIGMA_FAKE_ZERO_TOKENS_ON", "1")
    manifest = _manifest(tmp_path, ids=("one", "two"))

    message = _try(bench, lambda: _run(bench, tmp_path, [_arm(bench, "PlainArm", tmp_path)], manifest,
                                       _hidden(tmp_path, ids=("one", "two"))))

    assert message is not None and "no readable usage" in message
    assert "authentication" in message
    saved = _saved(tmp_path)
    assert [row["status"] for row in saved["runs"]] == ["not-run", "not-run"]
    assert len(_calls(tmp_path)) == 1


def test_results_are_labelled_indicative_dollars_and_carry_token_components(tmp_path):
    bench = _bench()
    report = _run(bench, tmp_path, [_arm(bench, "PlainArm", tmp_path)], _manifest(tmp_path), _hidden(tmp_path))

    row = _row(report, "plain")
    assert row["tokens"] == 100_000
    assert row["tokens_detail"] == {"input": 0, "output": 100_000, "cache_read": 0, "cache_write": 0}
    assert "indicative" in report["cost_basis"] and "not a bill" in report["cost_basis"]
    assert report["max_tokens"] == 10 ** 9 and "max_usd" not in report


def test_summarize_refuses_an_incomplete_file_and_reports_relative_outcome_and_token_cost(tmp_path, capsys):
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "PlainArm", tmp_path)]
    manifest, hidden = _manifest(tmp_path), _hidden(tmp_path)
    _run(bench, tmp_path, arms, manifest, hidden, batch_pairs=1)

    assert bench.main(["summarize", "--results", str(tmp_path / "results.json")]) == 2
    assert "not complete" in capsys.readouterr().err

    _run(bench, tmp_path, arms, manifest, hidden, resume=True)
    assert bench.main(["summarize", "--results", str(tmp_path / "results.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["arms"]["plain"]["tokens_total"] == 100_000
    assert out["arms"]["sigma"]["tokens_total"] == 250_000
    assert out["arms"]["plain"]["tokens_vs_sigma"] == 0.4
    assert out["paired_vs_sigma"]["plain"] == {"sigma_wins": 0, "sigma_losses": 0, "ties": 1}
    assert "indicative" in out["cost_basis"] and "per_task" not in json.dumps(out), "no dollars-per-task claim"


def test_a3_is_not_run_when_its_a1_pair_has_no_readable_token_count(tmp_path):
    """Control: without this the matched arm would run with no cap (A3 depends on A1's stored tokens)."""
    bench = _bench()
    repo, sha = _repo(tmp_path)
    arms = [_sigma(bench, tmp_path, repo, sha), _arm(bench, "MatchedArm", tmp_path)]
    manifest, hidden = _manifest(tmp_path, visible=FAILS), _hidden(tmp_path)
    _run(bench, tmp_path, arms, manifest, hidden, batch_pairs=1)
    saved = _saved(tmp_path)
    saved["runs"][0]["tokens"] = None  # an A1 row whose count was lost (hand-edited cursor)
    (tmp_path / "results.json").write_text(json.dumps(saved), encoding="utf-8")
    calls_before = len(_calls(tmp_path))

    report = _run(bench, tmp_path, arms, manifest, hidden, resume=True)

    row = _row(report, "matched")
    assert row["status"] == "not-run" and "no A1 token count" in row["reason"]
    assert report["complete"] is False and len(_calls(tmp_path)) == calls_before


def test_the_documented_gesture_as_a_real_process_exits_75_on_a_rate_limit_and_0_on_resume(tmp_path, monkeypatch):
    """The README/evals gesture, run as its own process, so the exit status a driving script sees is real."""
    argv = _plain_cli(tmp_path, ("one", "two"), "--batch-pairs", "3")
    monkeypatch.setenv("SIGMA_FAKE_RATE_LIMIT_ON", "2")
    monkeypatch.setenv("SIGMA_FAKE_RATE_LIMIT_UNTIL", "2")

    def process(*extra):
        return subprocess.run([sys.executable, str(BENCH_PATH), *argv, *extra], capture_output=True, text=True,
                              timeout=120, env=dict(os.environ))

    first = process()
    assert first.returncode == 75, first.stderr
    assert "--resume" in first.stderr and json.loads(first.stdout)["complete"] is False
    second = process("--resume")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["complete"] is True


def test_summarize_carries_the_caveats_a_reader_needs_and_resume_refuses_a_bad_token_count(tmp_path, capsys):
    bench = _bench()
    argv = _plain_cli(tmp_path, ("one", "two"), "--batch-pairs", "1")
    assert bench.main(argv) == 0
    saved = _saved(tmp_path)
    saved["in_flight"] = {"task": "two", "arm": "plain", "repeat": 1}
    (tmp_path / "results.json").write_text(json.dumps(saved), encoding="utf-8")
    assert bench.main(argv + ["--resume"]) == 0
    capsys.readouterr()

    assert bench.main(["summarize", "--results", str(tmp_path / "results.json")]) == 0
    assert json.loads(capsys.readouterr().out)["caveats"] == {"unknown_runs": 1}

    saved = _saved(tmp_path)
    saved["runs"][0]["tokens"] = "lots"
    (tmp_path / "results.json").write_text(json.dumps(saved), encoding="utf-8")
    assert bench.main(argv + ["--resume"]) == 2
    assert "invalid token count" in capsys.readouterr().err


def test_a_stale_spend_from_the_previous_pair_is_never_reported_for_the_next_one(tmp_path, monkeypatch):
    bench = _bench()
    arm = _arm(bench, "PlainArm", tmp_path)
    real = bench._run_arm
    calls = []

    def fail_second_before_it_runs(arm_, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise bench.BenchmarkRefusal("before the arm ran")
        return real(arm_, *args, **kwargs)

    monkeypatch.setattr(bench, "_run_arm", fail_second_before_it_runs)
    manifest = _manifest(tmp_path, ids=("one", "two"))

    message = _try(bench, lambda: _run(bench, tmp_path, [arm], manifest, _hidden(tmp_path, ids=("one", "two"))))

    assert message == "before the arm ran"
    assert _saved(tmp_path)["aborted"]["tokens_spent"] == 0, "pair one's 100000 tokens are in its row, not here"

"""#853 (slice 4A of #558): `work.py seal-tests`, the orchestration in `seal_tests.py`.

Real route evidence: a FAKE `cursor-agent` on PATH (never a real model), the real `sealed_tests.seal` for the
red-on-base proof, a real linked git worktree, and a Claude-Code-host test through the real CLI. A real-host
run is UNMEASURED. Every refusal clause has its own test; the break that turns it red is named in a comment
and was run (the control table is in the PR evidence).

Slice A leaves open on purpose (and these tests do not claim): the plan-defect return (AC-5), `--no-seal`, the
phase_gate step, the record-required check. Per-criterion coverage is deferred to #535."""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fake_breaker_support as fake  # noqa: E402
import git_identity_support as gis  # noqa: E402
import test_sealed_tests as tsl  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = ROOT / "skills/sigma-loop/scripts"
STEM = "853"
DIR = "tests/acceptance/853"


def load(name):
    path = S / (name + ".py")
    assert path.exists(), "missing module %s" % path.name          # a missing module is a FAILURE, not an error
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


st = load("seal_tests")
breaker = st._load("breaker")
sealed = st._load("sealed_tests")

RED1 = "def test_f_returns_two():\n    import pkg_mod\n    assert pkg_mod.f() == 2\n"
RED2 = "def test_f_is_not_one():\n    import pkg_mod\n    assert pkg_mod.f() != 1\n"
PASSES = "def test_f_one():\n    import pkg_mod\n    assert pkg_mod.f() == 1\n"
GOOD = {"AC1_returns": RED1, "AC2_not_one": RED2}

ACCEPTANCE = """---
kind: "acceptance"
goal: "853"
verify_command: null
---
## Done when
- [ ] pkg_mod.f returns 2 for every call
- [ ] pkg_mod.f never returns 1 and raises no error
- [ ] pkg_mod.f returns an int and never a string

## Interface
pkg_mod.f() takes no arguments and returns an int.
"""
VAGUE = """---
kind: "acceptance"
goal: "853"
verify_command: null
---
## Done when
- [ ] pkg_mod.f returns 2 for every call
- [ ] Add support for nicer behaviour
- [ ] Improve the handling of things
"""
CONFIG = {"work": {"enabled": True, "base": "main", "remote": "origin"},
          "gates": {"sealed_acceptance": {"enabled": True}},
          "verify": {"command": "python -m pytest -q"},
          "review": {"host": "cursor", "timeout_seconds": 30}}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class World:
    pass


@pytest.fixture
def world(tmp_path, monkeypatch):
    tmp = tmp_path.resolve()
    gis.scoped_git_identity(tmp / "gid", monkeypatch)
    for name in ("SIGMA_RUN_ID", "SIGMA_AUTOWATCH_HOP", "SIGMA_SESSION_GENERATION", "CLAUDECODE",
                 "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID", "SIGMA_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp / "home"))
    monkeypatch.setenv("SIGMA_RESOURCE_MODE", "off")
    w = World()
    w.tmp, w.main, w.wt = tmp, tmp / "main", tmp / "wt"
    w.sdlc = w.main / ".sdlc"
    w.main.mkdir()
    git(w.main, "init", "-q", "-b", "main")
    (w.main / "pkg_mod.py").write_text("def f():\n    return 1\n")
    git(w.main, "add", "-A")
    git(w.main, "commit", "-q", "-m", "base")
    git(tmp, "init", "-q", "--bare", str(tmp / "origin.git"))
    git(w.main, "remote", "add", "origin", str(tmp / "origin.git"))
    git(w.main, "push", "-q", "-u", "origin", "main")
    git(w.main, "worktree", "add", "-q", str(w.wt), "-b", "sdlc/853")
    for sub in ("acceptance", "plans", "state/work", "state/gates"):
        (w.sdlc / sub).mkdir(parents=True)
    w.config = json.loads(json.dumps(CONFIG))
    w.write_config()
    (w.sdlc / "acceptance" / "853.md").write_text(ACCEPTANCE)
    plan = w.sdlc / "plans" / "853.md"
    plan.write_text("# plan 853\n")
    w.plan_hash = hashlib.sha256(plan.read_bytes()).hexdigest()
    (w.sdlc / "state/gates/853.json").write_text(json.dumps({"verdict": "pass", "plan_hash": w.plan_hash}))
    (w.sdlc / "state/work/853.json").write_text(json.dumps(
        {"worktree": str(w.wt), "branch": "sdlc/853", "base": "main", "remote": "origin"}))
    w.fake = fake.make_fake(tmp / "bin")
    w.side = tmp / "side.jsonl"
    w.out = tmp / "out.txt"
    w.attempts = w.sdlc / "state/seal-attempts/853.attempt"
    monkeypatch.setenv("PATH", str(tmp / "bin") + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_SIDECAR", str(w.side))
    monkeypatch.setenv("FAKE_OUT", str(w.out))
    monkeypatch.setenv("FAKE_ATTEMPTS", str(w.attempts))
    monkeypatch.setenv("FAKE_MODE", "blocks")
    monkeypatch.setenv("FAKE_CHILD", str(tmp / "child.pid"))
    w.monkeypatch = monkeypatch
    w.say(GOOD)
    return w


def _say(self, files, raw=None):
    self.out.write_text(raw if raw is not None else fake.blocks(STEM, files))


def _write_config(self):
    (self.sdlc / "config.json").write_text(json.dumps(self.config))


def _go(self, **kw):
    args = dict(config=self.config, env={}, which=lambda name: str(self.tmp / "bin" / name))
    args.update(kw)
    if "seal" not in args and tsl.PYTEST_PY:
        # The real seal() runs pytest under a throwaway HOME, so an interpreter whose pytest lives in the user
        # site (the system python3 the planned-test runner uses) cannot; pick one that works, as test_sealed_tests does.
        real = st._load("sealed_tests").seal
        args["seal"] = lambda *a, **k: real(*a, **dict(k, python=tsl.PYTEST_PY))
    return st.run(str(self.sdlc), STEM, **args)


def _ran(self):
    return fake.runs(self.side)


World.say, World.write_config, World.go, World.ran = _say, _write_config, _go, _ran


def stub_seal(*a, **k):
    return {"files": {"x": {}}, "red": [{"test": "t"}], "generation": 1}


def snapshot(world):
    """Every path under the three trees this verb could touch, so 'writes nothing' is a real comparison."""
    out = set()
    for base in (world.sdlc, world.wt, world.main):
        for p in base.rglob("*"):
            if (".git" in p.parts and p.name != ".git") or "time" in p.parts:   # work.py's own timing floor
                continue
            out.add(str(p))
    return out


# ============================================================================== the gate and the config

class TestGate:
    def test_off_writes_nothing_anywhere_and_never_invokes_the_breaker(self, world):
        # break: invert the gate read (`not gate_on`) -> this goes red
        world.config["gates"]["sealed_acceptance"]["enabled"] = False
        before = snapshot(world)
        code, text = world.go()
        assert (code, text) == (0, "seal-tests: OFF")
        assert snapshot(world) == before and world.ran() == [] and not world.attempts.exists()

    @pytest.mark.parametrize("config,want", [
        ({}, False), ({"gates": True}, False), ({"gates": []}, False),
        ({"gates": {"sealed_acceptance": True}}, True), ({"gates": {"sealed_acceptance": "off"}}, False),
        ({"gates": {"sealed_acceptance": {"enabled": "true"}}}, True),
        ({"gates": {"sealed_acceptance": {"enabled": False}}}, False),
        ({"gates": {"sealed_acceptance": {"enabled": 0}}}, False), ({"gates": {"sealed_acceptance": None}}, False)])
    def test_gate_read_is_local_and_scalar_safe(self, config, want):
        assert st.gate_on(config) is want

    def test_work_off_refuses_before_anything(self, world):
        world.config["work"] = {"enabled": False}
        code, text = world.go()
        assert code == 3 and "[work-off]" in text and world.ran() == []

    def test_the_seal_budget_is_the_verify_rule_and_equals_works_green_run_rule(self):
        work = load("work")
        for cfg in ({}, {"verify": {"timeout_minutes": 5}}, {"verify": {"timeout_minutes": True}},
                    {"verify": {"timeout_minutes": 0}}, {"verify": {"timeout_minutes": 2000}}):
            assert st.seal_budget_seconds(cfg) == work._sealed_timeout_seconds(cfg)
        assert st.seal_budget_seconds({"verify": 3}) == 3600          # scalar-safe where work's copy is not

    def test_the_breaker_timeout_is_derived_from_review_timeout_seconds_never_a_constant(self, world):
        seen = []

        def spy(argv, brief, goal, config, *, timeout, before_spawn):
            seen.append(timeout)
            raise st._load("breaker_run").BreakerRunRefused("timeout", "stop here")
        for value in (30, 77):
            world.config["review"]["timeout_seconds"] = value
            world.write_config()
            code, _ = world.go(run_breaker=spy)
            assert code == 4
        assert seen == [30, 77]


# =============================================================================== preconditions (no spawn)

class TestPreconditions:
    def refused(self, world, code, clause, **kw):
        before = world.attempts.exists()
        got, text = world.go(**kw)
        assert got == code and "[%s]" % clause in text, text
        assert world.ran() == [] and world.attempts.exists() == before      # nothing spawned, no attempt booked
        assert not (world.wt / DIR).exists()
        return text

    def test_a_non_pytest_verify_command_refuses(self, world):
        # break: accept any command (`runs_pytest` returns True) -> this goes red
        world.config["verify"]["command"] = "npm test"
        text = self.refused(world, 3, "non-pytest-verify")
        assert "heuristic" in text

    def test_no_declared_verify_command_refuses(self, world):
        world.config["verify"] = {}
        self.refused(world, 3, "no-verify-command")

    @pytest.mark.parametrize("command,want", [
        ("python -m pytest -q", True), ("pytest", True), ("$HOME/.venv/bin/pytest -x tests", True),
        ("ruff check . && pytest -q", True), ("make test", False), ("npm test", False), ("", False),
        ("python -m unittest", False), ("pytest 'unterminated", False), (None, False)])
    def test_the_pytest_heuristic(self, command, want):
        assert st.runs_pytest(command) is want

    def test_a_stale_plan_hash_refuses_even_with_an_approving_verdict(self, world):
        # break: skip the plan-hash equality (keep the verdict check) -> this goes red
        (world.sdlc / "plans/853.md").write_text("# plan 853, edited after its review\n")
        text = self.refused(world, 3, "stale-plan-review")
        assert "fresh plan-review" in text

    def test_a_blocking_or_missing_plan_review_refuses(self, world):
        gates = world.sdlc / "state/gates/853.json"
        gates.write_text(json.dumps({"verdict": "block", "plan_hash": world.plan_hash}))
        self.refused(world, 3, "no-plan-review")
        gates.unlink()
        self.refused(world, 3, "no-plan-review")

    def test_a_warn_verdict_is_approving(self, world):
        (world.sdlc / "state/gates/853.json").write_text(json.dumps({"verdict": "warn", "plan_hash": world.plan_hash}))
        code, text = world.go(spend=lambda *a: (True, "APPROVED x"), seal=stub_seal)
        assert code == 0, text

    def test_a_non_actionable_criterion_is_reported_by_name_and_refuses_without_spawning(self, world):
        # break: drop the per-criterion report (ignore `verdict_lines`) -> this goes red
        (world.sdlc / "acceptance/853.md").write_text(VAGUE)
        text = self.refused(world, 3, "not-actionable")
        assert "Add support for nicer behaviour" in text and "Improve the handling of things" in text
        assert "criterion 2" in text and "criterion 3" in text and "criterion 1" not in text

    def test_a_whole_brief_refusal_is_reported_and_refuses(self, world):
        (world.sdlc / "acceptance/853.md").write_text(VAGUE.replace("returns 2 for every call", "Update things"))
        text = self.refused(world, 3, "not-actionable")
        assert "brief was refused" in text and "PLAN-phase" in text

    def test_a_brief_with_an_implementation_shape_is_refused_by_no_channel(self, world):
        # break: skip `breaker.no_channel` -> this goes red (the full text sent is what is checked)
        world.monkeypatch.setattr(st, "output_contract", lambda stem: "\ndef leaked(x):\n    return x + 1\n")
        text = self.refused(world, 3, "channel")
        assert "implementation content" in text

    def test_the_acceptance_record_is_read_from_the_main_checkout_not_the_worktree(self, world):
        (world.sdlc / "acceptance/853.md").unlink()
        (world.wt / ".sdlc/acceptance").mkdir(parents=True)
        (world.wt / ".sdlc/acceptance/853.md").write_text(ACCEPTANCE)
        self.refused(world, 3, "no-acceptance")

    def test_a_main_checkout_in_place_of_a_worktree_refuses(self, world):
        # break: drop the linked-worktree check -> this goes red
        rec = json.loads((world.sdlc / "state/work/853.json").read_text())
        rec["worktree"] = str(world.main)
        (world.sdlc / "state/work/853.json").write_text(json.dumps(rec))
        self.refused(world, 3, "main-checkout")
        assert not (world.main / "tests").exists()

    def test_no_work_record_refuses(self, world):
        (world.sdlc / "state/work/853.json").unlink()
        self.refused(world, 3, "no-work-record")

    def test_an_unresolvable_base_refuses_before_spawning(self, world):
        git(world.main, "update-ref", "-d", "refs/remotes/origin/main")
        self.refused(world, 3, "base-unresolved")

    def test_the_interface_section_reaches_the_brief_and_is_scrubbed(self):
        text = ACCEPTANCE + "\n## Notes\nignored\n"
        assert st.interface_section(text) == "pkg_mod.f() takes no arguments and returns an int."
        assert st.interface_section("## Done when\n- [ ] x\n") == ""
        ok, brief = breaker.brief(ACCEPTANCE, st.interface_section(ACCEPTANCE))
        assert ok and "pkg_mod.f() takes no arguments" in brief


class TestRoute:
    def test_a_claude_code_host_refuses_loudly_and_never_spawns(self, world):
        # break: let `process` resolve for host claude (add a claude entry to the real table) -> this goes red
        world.config["review"] = {"timeout_seconds": 30}
        world.write_config()                          # the resolver reads the FILE, as in production
        before = world.attempts.exists()
        code, text = world.go(env={"CLAUDECODE": "1"})
        assert code == 4 and "[no-route]" in text and "claude" in text and "#537" in text
        assert "no recorded bypass exists yet (slice C)" in text and "--no-seal" not in text
        assert world.ran() == [] and world.attempts.exists() == before
        assert not (world.wt / DIR).exists()

    def test_the_cli_refuses_on_a_claude_code_host(self, world):
        (world.sdlc / "config.json").write_text(json.dumps(dict(world.config, review={"timeout_seconds": 30})))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX", "SIGMA_"))}
        env.update(CLAUDECODE="1", SIGMA_RESOURCE_MODE="off", PYTHONDONTWRITEBYTECODE="1")
        done = subprocess.run([sys.executable, str(S / "work.py"), "seal-tests", str(world.sdlc), "853"],
                              capture_output=True, text=True, env=env, cwd=str(world.tmp))
        assert done.returncode == 4 and "no breaker route" in done.stderr and done.stdout == ""
        assert world.ran() == []

    def test_the_cli_gate_off_is_inert_exit_zero(self, world):
        world.config["gates"]["sealed_acceptance"]["enabled"] = False
        world.write_config()
        before = snapshot(world)
        done = subprocess.run([sys.executable, str(S / "work.py"), "seal-tests", str(world.sdlc), "853"],
                              capture_output=True, text=True, cwd=str(world.tmp),
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        assert done.returncode == 0 and done.stdout.strip() == "seal-tests: OFF"
        assert snapshot(world) == before

    def test_the_cli_help_prints_usage_with_no_side_effect(self, world):
        before = snapshot(world)
        done = subprocess.run([sys.executable, str(S / "work.py"), "seal-tests", "--help"],
                              capture_output=True, text=True, cwd=str(world.tmp),
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        assert done.returncode == 0 and "usage: work.py seal-tests" in done.stdout
        assert snapshot(world) == before

    def test_the_refusal_function_is_registered_in_the_enforcement_registry(self):
        work = load("work")
        rows = [r for r in work.ENFORCEMENT_GATES if r["function"] == "_seal_tests_refusal"]
        assert len(rows) == 1 and rows[0]["enabled_by"] == ("work.enabled", "gates.sealed_acceptance.enabled")
        assert "UNMEASURED" in rows[0]["condition"] and "slice C" in rows[0]["condition"]
        assert callable(work._seal_tests_refusal)


# =================================================================================== cap, attempts, spend

class TestCapAttemptsSpend:
    def test_the_fourth_run_is_refused_without_spawning_and_names_the_lever(self, world):
        # break: `>` for `>=` in the cap test -> the fourth run spawns and this goes red
        world.attempts.parent.mkdir(parents=True)
        world.attempts.write_text("3\n")
        code, text = world.go(spend=lambda *a: (True, "APPROVED x"))
        assert code == 4 and "[cap]" in text and "park the goal" in text and "/sigma-unpark" not in text
        assert world.ran() == [] and world.attempts.read_text() == "3\n"

    def test_the_third_run_is_allowed_and_books_the_third_attempt(self, world):
        world.attempts.parent.mkdir(parents=True)
        world.attempts.write_text("2\n")
        code, text = world.go(seal=stub_seal)
        assert code == 0 and "breaker run 3 of 3" in text and world.attempts.read_text() == "3\n"

    @pytest.mark.parametrize("content", ["abc", "-1", "", "1.5", "2 3", "\x00"])
    def test_a_corrupt_counter_fails_closed(self, world, content):
        # break: treat a non-integer counter as zero -> the breaker spawns and this goes red
        world.attempts.parent.mkdir(parents=True)
        world.attempts.write_text(content)
        code, text = world.go(seal=stub_seal)
        assert code == 4 and "[attempts-unreadable]" in text and world.ran() == []

    def test_a_symlinked_counter_fails_closed(self, world):
        world.attempts.parent.mkdir(parents=True)
        (world.tmp / "elsewhere").write_text("0\n")
        world.attempts.symlink_to(world.tmp / "elsewhere")
        code, text = world.go(seal=stub_seal)
        assert code == 4 and "[attempts-unreadable]" in text and world.ran() == []

    def test_the_counter_exists_before_the_breaker_starts(self, world):
        # break: write the counter AFTER the spawn -> the fake sees no file and this goes red
        code, _ = world.go(seal=stub_seal)
        assert code == 0
        assert world.ran()[0]["attempts_existed"] is True
        assert world.attempts.read_text() == "1\n"

    def test_a_crashed_breaker_still_costs_the_attempt(self, world):
        world.monkeypatch.setenv("FAKE_MODE", "exit1")
        code, text = world.go(seal=stub_seal)
        assert code == 4 and "[breaker-nonzero-exit]" in text and world.attempts.read_text() == "1\n"

    @pytest.mark.parametrize("name", ["SIGMA_RUN_ID", "SIGMA_AUTOWATCH_HOP", "SIGMA_SESSION_GENERATION"])
    def test_every_unattended_marker_triggers_the_consult(self, world, name):
        # break: drop one name from UNATTENDED_VARS -> that parameter goes red (literal names, not the tuple)
        calls = []
        world.go(env={name: "1"}, spend=lambda *a: (calls.append(a) or (False, "DENIED no")))
        assert len(calls) == 1

    def test_unattended_with_spend_denied_refuses_before_the_counter_and_the_spawn(self, world):
        # break: run the consult only after the spawn -> the breaker runs and this goes red
        code, text = world.go(env={"SIGMA_RUN_ID": "r1"}, spend=lambda *a: (False, "DENIED not approved"))
        assert code == 4 and "[spend]" in text and "DENIED not approved" in text
        assert world.ran() == [] and not world.attempts.exists() and not (world.wt / DIR).exists()

    def test_unattended_with_spend_off_also_refuses(self, world):
        code, text = world.go(env={"SIGMA_RUN_ID": "r1"}, spend=lambda *a: (False, "OFF"))
        assert code == 4 and "[spend]" in text and world.ran() == []

    def test_unattended_with_spend_approved_proceeds_and_one_approval_covers_the_later_runs(self, world):
        calls = []

        def approve(*a):
            calls.append(a)
            return True, "APPROVED lbl"
        for _ in range(3):
            code, text = world.go(env={"SIGMA_RUN_ID": "r1"}, spend=approve, seal=stub_seal)
            assert code == 0, text
        assert len(calls) == 1 and "covers up to 3 runs" in text
        assert len(world.ran()) == 3

    @pytest.mark.parametrize("line,ok", [("APPROVED lbl-1", True), ("DENIED nope", False), ("OFF", False),
                                         ("", False), ("approved lower", False)])
    def test_the_real_consult_passes_only_an_APPROVED_first_word(self, world, line, ok):
        # break: `return True, line` (any answer passes) -> the DENIED / OFF / empty parameters go red
        spend_approval = st._load("spend_approval")
        sources = st._load("sources")
        world.monkeypatch.setattr(sources, "get_source", lambda sdlc, config: object())
        world.monkeypatch.setattr(spend_approval, "check", lambda *a: line)
        assert st.spend_consult(str(world.sdlc), STEM, world.config) == (ok, line)

    def test_a_consult_that_raises_is_a_denial(self, world):
        def boom(*a):
            raise RuntimeError("no network")
        world.monkeypatch.setattr(st._load("spend_approval"), "check", boom)
        world.monkeypatch.setattr(st._load("sources"), "get_source", lambda sdlc, config: object())
        ok, line = st.spend_consult(str(world.sdlc), STEM, world.config)
        assert ok is False and line.startswith("DENIED")

    def test_an_attended_run_does_not_consult(self, world):
        code, _ = world.go(spend=lambda *a: pytest.fail("consulted"), seal=stub_seal)
        assert code == 0

    def test_a_lease_hold_burns_neither_the_approval_nor_an_attempt(self, world):
        # break: take the lease AFTER the consult/counter (the order the runner documents) -> this goes red
        import contextlib
        guard = st._load("breaker_run")._load("resource_guard")

        class Hold:
            reason = "memory pressure"

        @contextlib.contextmanager
        def held(*a, **k):
            yield Hold()
        world.monkeypatch.setattr(guard, "session_lease", held)
        code, text = world.go(env={"SIGMA_RUN_ID": "r1"}, spend=lambda *a: pytest.fail("burned the label"))
        assert code == 4 and "[breaker-held]" in text
        assert not world.attempts.exists() and world.ran() == []


# ====================================================================== spawn, validate, write, seal (real seal)

def tree_files(root):
    d = pathlib.Path(root) / DIR
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


class TestSealEndToEnd:
    def test_valid_blocks_are_written_by_python_then_sealed_red_on_base(self, world):
        writes = []
        state = st._load("state")
        real = state.atomic_write_text
        world.monkeypatch.setattr(state, "atomic_write_text", lambda p, t: (writes.append(str(p)), real(p, t))[1])
        code, text = world.go()
        assert code == 0, text
        assert "per-criterion coverage deferred to #535" in text and "2 red on base" in text
        assert tree_files(world.wt) == ["test_acc_853_AC1_returns.py", "test_acc_853_AC2_not_one.py"]
        rec = sealed.read_record(str(world.sdlc), STEM)
        assert rec["generation"] == 1 and rec["route"]["role"] == "breaker" and rec["route"]["host"] == "cursor"
        assert rec["plan_hash"] == world.plan_hash and len(rec["red"]) == 2
        assert sealed.check(str(world.wt), str(world.sdlc), STEM)["sealed"] is True
        assert world.attempts.read_text() == "1\n"
        # (h) the maker never writes: every test file went through atomic_write_text, and the main checkout is untouched
        for name in tree_files(world.wt):
            assert str(world.wt / DIR / name) in writes
        assert not (world.main / "tests").exists()
        seen = world.ran()[0]
        assert seen["listing"] == [] and "pkg_mod.f returns 2" in seen["stdin"] and "OUTPUT FORMAT" in seen["stdin"]
        assert "pkg_mod.f() takes no arguments" in seen["stdin"]
        assert str(world.wt) not in seen["stdin"] and str(world.sdlc) not in seen["stdin"]
        assert seen["argv"] == ["-p", "--output-format", "text"]

    def test_a_path_escape_writes_nothing_and_still_costs_the_attempt(self, world):
        # break: skip path confinement in the validator call -> a file lands outside and this goes red
        for bad in ("../x.py", "/abs/x.py", "tests/acceptance/853/../../x.py"):
            world.say(None, raw="=== FILE: %s ===\n%s=== END FILE ===\n" % (bad, RED1))
            code, text = world.go(seal=stub_seal)
            assert code == 4 and "[bad-output]" in text
        assert not (world.wt / "x.py").exists() and not (world.tmp / "x.py").exists() and tree_files(world.wt) == []
        assert world.attempts.read_text() == "3\n"

    def test_a_symlinked_destination_is_refused_and_nothing_is_written_through_it(self, world):
        # break: drop the lstat checks in `write_files` -> the file is written through the link and this goes red
        outside = world.tmp / "outside"
        outside.mkdir()
        (world.wt / "tests").mkdir()
        (world.wt / "tests/acceptance").symlink_to(outside)
        code, text = world.go(seal=stub_seal)
        assert code == 4 and "[unsafe-destination]" in text
        assert list(outside.rglob("*")) == []

    def test_a_symlinked_sealed_dir_is_refused(self, world):
        outside = world.tmp / "outside2"
        outside.mkdir()
        (world.wt / "tests/acceptance").mkdir(parents=True)
        (world.wt / DIR).symlink_to(outside)
        code, text = world.go(seal=stub_seal)
        assert code == 4 and "[unsafe-destination]" in text and list(outside.rglob("*")) == []

    def test_a_syntax_error_writes_nothing(self, world):
        # break: skip `ast.parse` validation (skip breaker.validate_output) -> a broken file is written and this goes red
        world.say({"AC1_broken": "def test_x(:\n    pass\n"})
        code, text = world.go(seal=stub_seal)
        assert code == 4 and "[bad-output]" in text and "does not parse" in text
        assert tree_files(world.wt) == [] and world.attempts.read_text() == "1\n"

    def test_a_file_with_no_test_function_is_refused_naming_the_file(self, world):
        # break: skip the test-function check -> this goes red
        world.say({"AC1_empty": "VALUE = 1\n"})
        code, text = world.go(seal=stub_seal)
        assert code == 4 and "test_acc_853_AC1_empty.py" in text and "no module-level test_* function" in text
        assert tree_files(world.wt) == []

    def test_the_AC_name_rule_conflicts_with_the_breaker_grammar_and_wins(self, world):
        # the breaker's own grammar ACCEPTS this name; this module's extra rule REFUSES it
        # break: skip the `AC<n>` check (`check_names`) -> this goes red
        text = fake.blocks(STEM, {"plain": RED1})
        assert breaker.validate_output(text, STEM)[0] is True
        world.say(None, raw=text)
        code, out = world.go(seal=stub_seal)
        assert code == 4 and "[bad-name]" in out and "AC<n>" in out and "#535" in out
        assert tree_files(world.wt) == []

    @pytest.mark.parametrize("suffix", ["AC0_x", "AC4_x", "AC10_x", "ac1_x", "AC_x", "AC1x"])
    def test_an_AC_number_outside_the_criterion_count_is_refused(self, world, suffix):
        world.say({suffix: RED1})
        code, out = world.go(seal=stub_seal)
        assert code == 4 and "[bad-name]" in out and tree_files(world.wt) == []

    def test_a_test_that_passes_on_base_is_refused_and_its_files_are_removed(self, world):
        # break: swallow SealRefused for pass-on-base -> a sealed record appears and this goes red
        world.say({"AC1_passes": PASSES})
        code, text = world.go()
        assert code == 4 and "[pass-on-base]" in text and "SEALED REFUSED" in text
        assert not sealed.is_sealed(str(world.sdlc), STEM) and tree_files(world.wt) == []
        assert world.attempts.read_text() == "1\n"

    def test_a_re_run_with_identical_output_is_idempotent(self, world):
        assert world.go()[0] == 0
        path = world.sdlc / "state/sealed/853.json"
        first = path.read_bytes()
        code, text = world.go()
        assert code == 0 and "generation 1" in text and path.read_bytes() == first
        assert world.attempts.read_text() == "2\n"

    def test_a_re_seal_clears_the_old_files_and_bumps_the_generation(self, world):
        # break: drop the clear step in `write_files` -> the old AC2 file stays and this goes red
        assert world.go()[0] == 0
        world.say({"AC1_returns": RED1})
        code, text = world.go()
        assert code == 0 and "generation 2" in text
        assert tree_files(world.wt) == ["test_acc_853_AC1_returns.py"]
        assert sealed.check(str(world.wt), str(world.sdlc), STEM)["sealed"] is True

    def test_a_failure_between_clear_and_record_is_recoverable_by_a_re_run(self, world):
        assert world.go()[0] == 0

        def boom(*a, **k):
            raise RuntimeError("crash between clear and record")
        code, text = world.go(seal=boom)
        assert code == 4 and "[error]" in text and "crash between" in text
        got = sealed.check(str(world.wt), str(world.sdlc), STEM)
        assert got["sealed"] is False and {p["kind"] for p in got["problems"]} == {"missing"}
        code, text = world.go()
        assert code == 0
        assert sealed.check(str(world.wt), str(world.sdlc), STEM)["sealed"] is True

    def test_a_re_seal_after_implement_started_is_refused(self, world):
        # break: drop the implement-started clause -> this goes red
        assert world.go()[0] == 0
        log = world.sdlc / "state/log/853.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"kind": "phase", "phase": "implement", "state": "start", "ts": "2026-10-19T00:00:00Z"}) + "\n")
        before = (world.sdlc / "state/sealed/853.json").read_bytes()
        code, text = world.go()
        assert code == 4 and "[reseal-after-implement]" in text and "no recorded bypass exists yet" in text
        assert len(world.ran()) == 1 and (world.sdlc / "state/sealed/853.json").read_bytes() == before

    def test_implement_started_with_no_seal_yet_does_not_block_the_first_seal(self, world):
        # break: refuse on implement-started alone (drop the `is_sealed` clause) -> this goes red
        log = world.sdlc / "state/log/853.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"kind": "phase", "phase": "implement", "state": "start", "ts": "2026-10-19T00:00:00Z"}) + "\n")
        assert world.go()[0] == 0

    def test_the_hang_is_killed_and_the_attempt_is_booked(self, world):
        world.monkeypatch.setenv("FAKE_MODE", "hang")
        world.config["review"]["timeout_seconds"] = 1
        world.write_config()
        code, text = world.go(seal=stub_seal)
        assert code == 4 and "[breaker-timeout]" in text and world.attempts.read_text() == "1\n"
        assert tree_files(world.wt) == []

    def test_the_attempts_counter_is_pruned_with_the_goal(self):
        prune = load("goal_state_prune")
        assert ("seal-attempts", ".attempt") in prune.FILE_FAMILIES
        assert st.attempts_path("S", "853").as_posix() == "S/state/seal-attempts/853.attempt"

"""#2135, against real git: a commit signer that is down stops `work.py commit` and every ledger-publish
commit in `sync.py` with the one plain message, and nothing retries unsigned.

THE OUTAGE IS SIMULATED THE WAY THE REPORT DESCRIBES IT: a `gpg.ssh.program` helper that prints a line to
stderr and exits 1, with `commit.gpgsign=true`. No real signer, key or agent is involved, and the real
cloud signer was never run: that is a stated limit of this evidence, not a claim.

A successful fake (used where the first commit must be signed and a later one must fail) writes the
`<buffer>.sig` file git asks the helper to write, so git builds a real commit around a placeholder
signature. `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` point at an empty file so no host configuration
(a developer's own signing setup) leaks into any run.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


work = _load("work")
state = _load("state")
cs = _load("commit_signing")

ON = {"work": {"enabled": True}}
GOAL = "0001-x.md"
THROWAWAY_KEY = "key::ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKbV4JnvYb1d1p5fHBg6Hq0zqk0kqVxqQ2pF1b0m3nPz"


@pytest.fixture(autouse=True)
def _isolated_git_config(tmp_path, monkeypatch):
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))
    for var in ("CLAUDE_CODE_REMOTE", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)


def _git(cwd, *args, check=True):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check:
        assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr or proc.stdout}"
    return proc.stdout.strip()


def make_signer(directory, ok_calls=0, name="signer.sh"):
    """A `gpg.ssh.program` helper that logs one line per call, then either signs (the first `ok_calls`
    calls: writes the `<buffer>.sig` file git asked for) or prints `signer down` and exits 1.
    Returns `(helper_path, log_path)`; the helper lives OUTSIDE any repository so it is never staged."""
    directory = pathlib.Path(directory)
    log = directory / (name + ".log")
    helper = directory / name
    helper.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        echo call >> '{log}'
        n=$(wc -l < '{log}')
        if [ "$n" -le {int(ok_calls)} ]; then
          for last; do :; done
          printf -- '-----BEGIN SSH SIGNATURE-----\\nfake\\n-----END SSH SIGNATURE-----\\n' > "$last.sig"
          exit 0
        fi
        echo "signer down" >&2
        exit 1
        """), encoding="utf-8")
    helper.chmod(0o755)
    return helper, log


def calls(log):
    return len(log.read_text().splitlines()) if log.exists() else 0


def sign_with(repo, helper, on=True):
    _git(repo, "config", "gpg.format", "ssh")
    _git(repo, "config", "gpg.ssh.program", str(helper))
    _git(repo, "config", "user.signingkey", THROWAWAY_KEY)
    _git(repo, "config", "commit.gpgsign", "true" if on else "false")


def outcome(fn):
    """The string a call returned, or `str(exc)` when it raised: a missing fix is then an ASSERTION
    failure on the text, not an error collected from inside the call."""
    try:
        return str(fn())
    except Exception as exc:                    # noqa: BLE001 - see docstring
        return str(exc)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")


# =============================================================================== the fixture itself


def test_the_fake_signer_is_called_exactly_once_by_one_commit_and_git_reports_only_its_line(tmp_path):
    """Step 6: measured shape, asserted here so a git version that refuses the key literal before
    calling the helper fails THIS test rather than silently weakening every test below."""
    repo = tmp_path / "r"
    _init_repo(repo)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    (repo / "a").write_text("a\n")
    _git(repo, "add", "a")
    proc = subprocess.run(["git", "-C", str(repo), "commit", "-m", "x"], capture_output=True, text=True)
    assert proc.returncode != 0
    assert calls(log) == 1
    assert "signer down" in proc.stderr and "failed to write commit object" in proc.stderr


def test_a_succeeding_fake_signer_makes_a_real_commit(tmp_path):
    repo = tmp_path / "r"
    _init_repo(repo)
    helper, log = make_signer(tmp_path, ok_calls=1)
    sign_with(repo, helper)
    (repo / "a").write_text("a\n")
    _git(repo, "add", "a")
    _git(repo, "commit", "-m", "x")
    assert calls(log) == 1
    assert "gpgsig" in _git(repo, "cat-file", "commit", "HEAD")


# =============================================================================== work.py commit


def _work_repo(tmp_path):
    """A real repo with one commit and a work record that points a goal at it as its worktree."""
    repo = tmp_path / "wrepo"
    (repo / ".sdlc" / "state").mkdir(parents=True)
    (repo / ".sdlc" / "config.json").write_text(json.dumps(ON))
    _init_repo(repo)
    (repo / ".gitignore").write_text(".sdlc/\n")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "--", ".gitignore", "a.py")
    _git(repo, "commit", "-q", "-m", "base")
    d = str(repo / ".sdlc")
    state.start_run(d)
    work._save(d, GOAL, {"worktree": str(repo), "branch": "sdlc/0001-x", "base": "main",
                         "remote": "origin", "pr": ""})
    (repo / "b.py").write_text("y = 2\n")
    return repo, d


def test_work_commit_stops_with_the_plain_message_and_calls_the_signer_once(tmp_path):
    repo, d = _work_repo(tmp_path)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    out = outcome(lambda: work.commit(d, ON, GOAL, message="feat: b"))
    assert out.startswith(cs.PARK_PREFIX), out
    assert "signer down" in out
    assert "git commit -m" not in out
    assert out.endswith(cs.PARK_TAIL), "the verb itself says what to do: the instruction files cannot carry it"
    assert calls(log) == 1, "no retry, signed or unsigned"


def test_work_commit_makes_no_commit_and_records_no_risk_gate(tmp_path, monkeypatch):
    repo, d = _work_repo(tmp_path)
    helper, _log = make_signer(tmp_path)
    sign_with(repo, helper)
    emitted = []
    monkeypatch.setattr(work, "_emit_risk_gates", lambda *a, **k: emitted.append(a))
    monkeypatch.setattr(work, "_risk_categories", lambda _path: ["security"])
    before = _git(repo, "rev-parse", "HEAD")
    out = outcome(lambda: work.commit(d, ON, GOAL, message="feat: b"))
    assert out.startswith(cs.PARK_PREFIX), out
    assert _git(repo, "rev-parse", "HEAD") == before
    assert emitted == [], "a commit that never landed must not leave a gate record"


def test_work_commit_cli_prints_the_plain_text_and_exits_4(tmp_path, capsys):
    repo, d = _work_repo(tmp_path)
    helper, _log = make_signer(tmp_path)
    sign_with(repo, helper)
    code = work.main(["work.py", "commit", d, GOAL, "--message", "x"])
    captured = capsys.readouterr()
    assert captured.out.startswith(cs.PARK_PREFIX), (captured.out, captured.err)
    assert "signer down" in captured.out
    assert code == 4


def test_work_commit_documented_cli_gesture_as_a_subprocess_exits_4(tmp_path):
    """The gesture the docs print (`python3 skills/sigma-loop/scripts/work.py commit <sdlc> <goal>
    --message "x"`), run as the process an agent would run, against the fake-signer repo."""
    repo, d = _work_repo(tmp_path)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    proc = subprocess.run([sys.executable, str(SCRIPTS / "work.py"), "commit", d, GOAL, "--message", "x"],
                          capture_output=True, text=True, cwd=str(repo))
    assert proc.returncode == 4, (proc.stdout, proc.stderr)
    assert proc.stdout.startswith(cs.PARK_PREFIX), (proc.stdout, proc.stderr)
    assert calls(log) == 1


def test_work_commit_with_signing_off_and_a_rejecting_hook_still_raises_the_raw_error(tmp_path):
    repo, d = _work_repo(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'hook says no' >&2\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(RuntimeError) as err:
        work.commit(d, ON, GOAL, message="feat: b")
    assert "hook says no" in str(err.value)
    assert cs.NEEDLE not in str(err.value)


def test_work_commit_with_signing_on_and_a_rejecting_hook_still_raises_untouched(tmp_path):
    """The fatal-line clause on the real command: signing is configured, but the commit failed before
    the signer was ever asked, so this is not the signer's outage."""
    repo, d = _work_repo(tmp_path)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'hook says no' >&2\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(RuntimeError) as err:
        work.commit(d, ON, GOAL, message="feat: b")
    assert "hook says no" in str(err.value) and cs.NEEDLE not in str(err.value)
    assert calls(log) == 0


def test_work_commit_succeeds_unchanged_when_the_signer_works(tmp_path):
    repo, d = _work_repo(tmp_path)
    helper, log = make_signer(tmp_path, ok_calls=5)
    sign_with(repo, helper)
    assert work.commit(d, ON, GOAL, message="feat: b") == "committed on sdlc/0001-x"
    assert calls(log) == 1


# =============================================================================== sync.py publish commits

sync = _load("sync")
ledger = _load("ledger")


def _team_clone(tmp_path, name, origin):
    """A real clone with its own ledger config; `origin` is a shared bare repo."""
    repo = tmp_path / name
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", str(origin))
    (repo / ".gitignore").write_text(".sdlc/\n", encoding="utf-8")
    _git(repo, "add", "--", ".gitignore")
    _git(repo, "commit", "-q", "-m", "base")
    base = repo / ".sdlc"
    (base / "state").mkdir(parents=True)
    cfg = {"ledger": {"enabled": True, "actor": name}}
    (base / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return repo, base, cfg


@pytest.fixture
def origin(tmp_path):
    o = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(o)], check=True)
    return o


@pytest.fixture
def ledger_clone(tmp_path, origin):
    """Clone `dana`: bootstrapped (signing OFF) so the ops branch exists locally and on origin."""
    repo, base, cfg = _team_clone(tmp_path, "dana", origin)
    assert "published" in sync.bootstrap(base, cfg)
    return repo, base, cfg


def _branch_tip(repo_or_bare):
    return _git(repo_or_bare, "rev-parse", "--verify", f"refs/heads/{sync.DEFAULT_BRANCH}")


def _note(base, cfg, why="a note", kind="note"):
    return ledger.append(base, cfg, kind, "g.md", why=why)


def raised_or_returned(fn):
    """`("returned"|"raised", text)`: the sync paths differ on which they do, and the class a `raise`
    carries is a per-module copy, so only the text is compared."""
    try:
        return "returned", str(fn())
    except Exception as exc:                    # noqa: BLE001
        return "raised", str(exc)


def _assert_plain(out):
    assert out.startswith(cs.NEEDLE), out
    assert "signer down" in out
    assert "publish deferred" not in out and "receipt pending" not in out
    assert "git commit" not in out


def test_sync_ledger_publish_returns_the_plain_text_and_pushes_nothing(tmp_path, origin, ledger_clone):
    repo, base, cfg = ledger_clone
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    _note(base, cfg)
    ledger_wt = sync.worktree(base)
    head_before, origin_before = _git(ledger_wt, "rev-parse", "HEAD"), _branch_tip(origin)
    out = sync.publish(base, cfg)
    _assert_plain(out)
    assert calls(log) == 1, "no retry, signed or unsigned"
    assert _git(ledger_wt, "rev-parse", "HEAD") == head_before
    assert _branch_tip(origin) == origin_before


def test_sync_publish_cli_gesture_prints_the_plain_text(tmp_path, origin, ledger_clone):
    """The documented gesture (`python3 skills/sigma-loop/scripts/sync.py publish .sdlc`), as a process."""
    repo, base, cfg = ledger_clone
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    _note(base, cfg)
    proc = subprocess.run([sys.executable, str(SCRIPTS / "sync.py"), "publish", str(base)],
                          capture_output=True, text=True, cwd=str(repo))
    _assert_plain(proc.stdout.strip())
    assert calls(log) == 1


def test_sync_ledger_publish_with_a_non_signing_failure_keeps_the_generic_deferral(tmp_path, ledger_clone):
    """Signing is off and the commit fails for another reason (no identity): unchanged wording."""
    repo, base, cfg = ledger_clone
    _git(repo, "config", "user.useConfigOnly", "true")
    _git(repo, "config", "--unset", "user.email")
    _note(base, cfg)
    out = sync.publish(base, cfg)
    assert out.startswith("publish deferred: ") and cs.NEEDLE not in out


def test_sync_ledger_publish_with_signing_on_and_a_non_signing_failure_keeps_the_generic_deferral(tmp_path, ledger_clone):
    """The fatal-line clause on the real command: signing is configured but git failed earlier."""
    repo, base, cfg = ledger_clone
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    _git(repo, "config", "user.useConfigOnly", "true")
    _git(repo, "config", "--unset", "user.email")
    _note(base, cfg)
    out = sync.publish(base, cfg)
    assert out.startswith("publish deferred: ") and cs.NEEDLE not in out
    assert calls(log) == 0


def test_sync_publish_entry_passes_the_plain_text_through_unclipped_with_a_non_retry_tail(tmp_path, ledger_clone):
    """`publish_entry` is the per-verb publish. Its line used to cap the reason at 240 characters and
    promise "the next loop step retries it"; the signing text is longer than the cap and no retry helps."""
    repo, base, cfg = ledger_clone
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    _note(base, cfg)
    said = sync.publish_entry(base, cfg, "claim of #7")
    assert said and cs.MESSAGE in said, said
    assert len(cs.MESSAGE) > 240
    assert "the next loop step retries it" not in said
    assert "will publish once that is cleared" in said
    assert calls(log) == 1


def test_sync_publish_entry_keeps_the_generic_line_for_other_failures(tmp_path, ledger_clone):
    repo, base, cfg = ledger_clone
    _git(repo, "config", "user.useConfigOnly", "true")
    _git(repo, "config", "--unset", "user.email")
    _note(base, cfg)
    said = sync.publish_entry(base, cfg, "claim of #7")
    assert "the next loop step retries it" in said and cs.NEEDLE not in said


def _knowledge_clone(tmp_path, origin):
    repo, base, cfg = _team_clone(tmp_path, "kate", origin)
    cfg["knowledge_graph"] = {"enabled": True, "scope": "research", "sync": {"enabled": True}}
    (base / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    analysis = base / "knowledge" / "analysis"
    analysis.mkdir(parents=True)
    (analysis / "issue-1.md").write_text("# one\n", encoding="utf-8")
    assert "published" in sync.bootstrap(base, cfg, channel=sync.KNOWLEDGE)
    return repo, base, cfg, analysis


def test_sync_knowledge_publish_returns_the_plain_text(tmp_path, origin):
    repo, base, cfg, analysis = _knowledge_clone(tmp_path, origin)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    (analysis / "issue-2.md").write_text("# two\n", encoding="utf-8")
    before = _git(analysis, "rev-parse", "HEAD")
    out = sync.publish(base, cfg, channel=sync.KNOWLEDGE)
    _assert_plain(out)
    assert calls(log) == 1
    assert _git(analysis, "rev-parse", "HEAD") == before


def test_sync_knowledge_publish_with_a_non_signing_failure_keeps_the_generic_deferral(tmp_path, origin):
    repo, base, cfg, analysis = _knowledge_clone(tmp_path, origin)
    _git(repo, "config", "user.useConfigOnly", "true")
    _git(repo, "config", "--unset", "user.email")
    (analysis / "issue-2.md").write_text("# two\n", encoding="utf-8")
    out = sync.publish(base, cfg, channel=sync.KNOWLEDGE)
    assert out.startswith("publish deferred: ") and cs.NEEDLE not in out


RECEIPT = "receipts/v1/" + "c" * 64 + "/parent.json"


def _receipt(base):
    target = sync.worktree(base) / RECEIPT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}\n", encoding="utf-8")


@pytest.mark.parametrize("config_value", ["true", "false"], ids=["signing-configured", "flag-alone"])
def test_sync_receipt_publish_returns_the_plain_text_and_unstages_the_receipt(tmp_path, origin, ledger_clone, config_value):
    """`-S` is in the argv, so the outage is recognised even with `commit.gpgsign=false` (the flag alone)."""
    repo, base, cfg = ledger_clone
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper, on=(config_value == "true"))
    _receipt(base)
    ledger_wt = sync.worktree(base)
    origin_before = _branch_tip(origin)
    out = sync.publish_receipt(base, RECEIPT, config=cfg)
    _assert_plain(out)
    assert calls(log) == 1
    assert _branch_tip(origin) == origin_before
    assert _git(ledger_wt, "diff", "--cached", "--name-only") == "", \
        "a receipt left staged would make every later receipt refuse as 'unrelated staged files'"


def test_sync_receipt_publish_with_a_non_signing_failure_keeps_the_generic_wording(tmp_path, ledger_clone):
    repo, base, cfg = ledger_clone
    _git(repo, "config", "user.useConfigOnly", "true")
    _git(repo, "config", "--unset", "user.email")
    _receipt(base)
    out = sync.publish_receipt(base, RECEIPT, config=cfg)
    assert out.startswith("receipt pending: ") and cs.NEEDLE not in out


def test_sync_init_scaffold_does_not_swallow_a_down_signer(tmp_path, origin):
    repo, base, cfg = _team_clone(tmp_path, "dana", origin)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    kind, text = raised_or_returned(lambda: sync.init(base, cfg))
    assert text.startswith(cs.NEEDLE), (kind, text)
    assert "worktree ready" not in text
    assert calls(log) == 1


def test_sync_init_scaffold_still_swallows_nothing_to_commit(tmp_path, origin):
    """A teammate already bootstrapped: the scaffold has nothing new, git says so, and that stays fine
    even with a broken signer (git never reaches it)."""
    repo_a, base_a, cfg_a = _team_clone(tmp_path, "dana", origin)
    assert "published" in sync.bootstrap(base_a, cfg_a)
    repo_b, base_b, cfg_b = _team_clone(tmp_path, "eli", origin)
    helper, log = make_signer(tmp_path)
    sign_with(repo_b, helper)
    out = sync.init(base_b, cfg_b)
    assert "worktree ready" in out
    assert calls(log) == 0


def test_sync_amend_after_a_rebase_returns_the_plain_text_and_aborts_the_rebase(tmp_path, origin, ledger_clone):
    """The `regenerate_team` amend. The fake signer signs the first commit and the rebase replay, then
    fails on the amend (call 3). Engineered so the amend is reached: a teammate's published entry makes
    the regenerated TEAM.md differ from the replayed one."""
    repo, base, cfg = ledger_clone
    repo_b, base_b, cfg_b = _team_clone(tmp_path, "eli", origin)
    assert "published" in sync.bootstrap(base_b, cfg_b)
    _note(base_b, cfg_b, why="eli was here", kind="done")
    assert sync.publish(base_b, cfg_b) == "published"
    helper, log = make_signer(tmp_path, ok_calls=2)
    sign_with(repo, helper)
    _note(base, cfg, why="dana was here", kind="done")
    ledger_wt = sync.worktree(base)
    out = sync.publish(base, cfg, backoff=False)
    _assert_plain(out)
    assert calls(log) == 3, "commit, rebase replay, then the failing amend: nothing after it"
    rebase_dir = pathlib.Path(_git(ledger_wt, "rev-parse", "--git-path", "rebase-merge"))
    rebase_dir = rebase_dir if rebase_dir.is_absolute() else ledger_wt / rebase_dir
    assert not rebase_dir.exists(), "the rebase must be aborted, not left half done"


def test_sync_publish_entry_that_must_create_the_worktree_reports_the_down_signer(tmp_path, origin):
    """The scaffold commit is the first signed commit of a self-created ledger worktree; its outage
    used to vanish and the step then went on to fail later with a generic line."""
    repo, base, cfg = _team_clone(tmp_path, "dana", origin)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    _note(base, cfg)
    said = sync.publish_entry(base, cfg, "claim of #7")
    assert said and cs.MESSAGE in said, said
    assert "the next loop step retries it" not in said


def test_sync_rebase_replay_that_cannot_be_signed_returns_the_plain_text(tmp_path, origin, ledger_clone):
    """BR-7, measured: a rejected push makes the retry REBASE the local commit, and the replay is signed
    again. With the signer down git reports `error: failed to write commit object` from `git rebase`
    itself, which used to be swallowed and shown as the push rejection ("publish deferred (will retry
    next tick): git push ... (fetch first)") on every tick. The fake signs the first commit, then fails."""
    repo, base, cfg = ledger_clone
    repo_b, base_b, cfg_b = _team_clone(tmp_path, "eli", origin)
    assert "published" in sync.bootstrap(base_b, cfg_b)
    _note(base_b, cfg_b, why="eli was here", kind="done")
    assert sync.publish(base_b, cfg_b) == "published"
    helper, log = make_signer(tmp_path, ok_calls=1)
    sign_with(repo, helper)
    _note(base, cfg, why="dana was here", kind="done")
    ledger_wt = sync.worktree(base)
    out = sync.publish(base, cfg, backoff=False)
    _assert_plain(out)
    assert calls(log) == 2, "the commit, then the one failing replay: no retry"
    rebase_dir = pathlib.Path(_git(ledger_wt, "rev-parse", "--git-path", "rebase-merge"))
    rebase_dir = rebase_dir if rebase_dir.is_absolute() else ledger_wt / rebase_dir
    assert not rebase_dir.exists()


def test_sync_rebase_that_fails_for_another_reason_keeps_the_generic_deferral(tmp_path, origin, ledger_clone):
    """The rebase classification needs signing's own failure line: a rebase refused for another reason
    (here a dirty ledger worktree) is still reported the old way."""
    repo, base, cfg = ledger_clone
    repo_b, base_b, cfg_b = _team_clone(tmp_path, "eli", origin)
    assert "published" in sync.bootstrap(base_b, cfg_b)
    _note(base_b, cfg_b, why="eli was here", kind="done")
    assert sync.publish(base_b, cfg_b) == "published"
    _note(base, cfg, why="dana was here", kind="done")
    ledger_wt = sync.worktree(base)
    (ledger_wt / "README.md").write_text("tracked file edited behind publish's back\n")
    out = sync.publish(base, cfg, backoff=False)
    assert out.startswith("publish deferred") and cs.NEEDLE not in out

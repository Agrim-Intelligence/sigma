"""#2135 follow-up to review: two defects found after the first commit-signing change.

1. A down signer at the SCAFFOLD commit of a worktree the loop creates for itself (`sync.publish_entry`
   -> `sync.init`) aborted `init` before the loop's bookkeeping ran. The worktree then existed with no
   self-created marker, so the next trigger's `loop._ensure_watcher` started the background watcher the
   operator never opted into, and `.sdlc/ledger` stayed visible in the code checkout's `git status`.
2. `commit_signing.classify` ran the outage test on the raw runner text, which begins with the argv
   (`git commit -m <message>: ...`), so words in a commit MESSAGE decided the verdict.

This is a separate file on purpose: `test_commit_signing.py` and `test_commit_signing_real_git.py` carry
recorded red witnesses and are not edited. The outage is simulated the way those files do (a
`gpg.ssh.program` helper that prints a line and exits 1); the real cloud signer was never run, which is a
stated limit of this evidence. `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` point at an empty file so no
host signing setup leaks into a run.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"
THROWAWAY_KEY = "key::ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKbV4JnvYb1d1p5fHBg6Hq0zqk0kqVxqQ2pF1b0m3nPz"


def _load(name):
    sys.path.insert(0, str(SCRIPTS))             # sync/loop import their siblings by name
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


cs = _load("commit_signing")
sync = _load("sync")
ledger = _load("ledger")
loop = _load("loop")
work = _load("work")
state = _load("state")


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


def make_signer(directory, name="signer.sh"):
    """A `gpg.ssh.program` helper that logs one line per call, prints `signer down` and exits 1. It lives
    OUTSIDE any repository so it is never staged. Returns `(helper_path, log_path)`."""
    directory = pathlib.Path(directory)
    log = directory / (name + ".log")
    helper = directory / name
    helper.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        echo call >> '{log}'
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


def _code_checkout(tmp_path, origin):
    """A real clone whose `.gitignore` does NOT cover `.sdlc/`, so the exclusion `publish_entry` writes
    is the only thing keeping `.sdlc/ledger` out of the code checkout's `git status`."""
    repo = tmp_path / "dana"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "remote", "add", "origin", str(origin))
    (repo / "README.md").write_text("code\n", encoding="utf-8")
    _git(repo, "add", "--", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    base = repo / ".sdlc"
    (base / "state").mkdir(parents=True)
    cfg = {"ledger": {"enabled": True, "actor": "dana"}}
    (base / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return repo, base, cfg


@pytest.fixture
def origin(tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    return bare


def _status_paths(repo):
    out = _git(repo, "status", "--porcelain", "--untracked-files=all")
    return [line[3:] for line in out.splitlines()]


def _watcher_starts(base, cfg):
    """How many times `loop._ensure_watcher` would launch the background watcher right now, with the
    launch itself replaced so nothing is spawned."""
    spawned = []
    loop._ensure_watcher(base, cfg, spawn=lambda: spawned.append(1))
    return len(spawned)


# =========================================================== finding 1: a down signer at scaffold time


def test_first_publish_entry_with_a_down_signer_still_finishes_the_loops_bookkeeping(tmp_path, origin):
    repo, base, cfg = _code_checkout(tmp_path, origin)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    ledger.append(base, cfg, "note", "g.md", why="a note")

    said = sync.publish_entry(base, cfg, "claim of #7")

    assert said and cs.MESSAGE in said, said
    assert "the next loop step retries it" not in said
    assert sync.is_worktree(base), "the worktree was created before its scaffold commit failed"
    assert sync.self_created(base), "the loop made this worktree: the marker must exist"
    leaked = [p for p in _status_paths(repo) if p.startswith(".sdlc/ledger")]
    assert leaked == [], f".sdlc/ledger must stay out of the code checkout's status: {leaked}"
    assert calls(log) == 1, "no retry"
    assert _git(base / "ledger", "rev-list", "--count", "HEAD") == "1", \
        "only the empty-tree start commit: nothing was committed unsigned"


def test_after_that_outage_the_next_trigger_does_not_start_the_watcher(tmp_path, origin):
    repo, base, cfg = _code_checkout(tmp_path, origin)
    helper, _log = make_signer(tmp_path)
    sign_with(repo, helper)
    ledger.append(base, cfg, "note", "g.md", why="a note")
    sync.publish_entry(base, cfg, "claim of #7")

    assert _watcher_starts(base, cfg) == 0

    # the control for the assertion above: without the marker the very same call DOES launch it, so a
    # zero is the marker's doing and not a precondition that never reached the launch
    sync._self_created_marker(base).unlink()
    assert _watcher_starts(base, cfg) == 1


def test_the_staged_scaffold_is_committed_by_the_next_publish_once_the_signer_is_back(tmp_path, origin):
    repo, base, cfg = _code_checkout(tmp_path, origin)
    helper, _log = make_signer(tmp_path)
    sign_with(repo, helper)
    ledger.append(base, cfg, "note", "g.md", why="a note")
    sync.publish_entry(base, cfg, "claim of #7")

    sign_with(repo, helper, on=False)
    again = sync.publish_entry(base, cfg, "claim of #7")

    assert again and "published" in again, again
    assert sync.self_created(base)


def test_bootstrap_with_a_down_signer_prints_the_plain_text_and_a_rerun_completes(tmp_path, origin, capsys):
    """`bootstrap` and the `init` verb are operator-run: a plain message and exit 1, no traceback. The
    scaffold stays staged, so the same gesture after the signer is back finishes the job."""
    repo, base, cfg = _code_checkout(tmp_path, origin)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)

    code = sync.main(["sync.py", "bootstrap", str(base)])
    captured = capsys.readouterr()
    assert code == 1
    assert cs.NEEDLE in captured.err and "Traceback" not in captured.err, captured.err
    assert "git commit" not in captured.err
    assert calls(log) == 1
    assert _git(base / "ledger", "rev-list", "--count", "HEAD") == "1"

    sign_with(repo, helper, on=False)
    code = sync.main(["sync.py", "bootstrap", str(base)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "published" in captured.out
    assert not sync.self_created(base), "an explicit enrolment lets the watcher start (#2076)"


def test_init_verb_with_a_down_signer_prints_the_plain_text_and_exits_1(tmp_path, origin, capsys):
    repo, base, cfg = _code_checkout(tmp_path, origin)
    helper, _log = make_signer(tmp_path)
    sign_with(repo, helper)

    code = sync.main(["sync.py", "init", str(base)])
    captured = capsys.readouterr()

    assert code == 1
    assert cs.NEEDLE in captured.err and "Traceback" not in captured.err, captured.err
    assert not sync.self_created(base), "the init verb is an operator's gesture, not the loop's own"


# =========================================================== finding 2: classify the stderr, not the argv

SSH_OUTAGE = "error: signer down\nfatal: failed to write commit object"


def _commit_argv(message):
    return ["git", "commit", "-m", message]


def _raised(message, stderr):
    argv = _commit_argv(message)
    return " ".join(argv) + ": " + stderr, argv


def test_a_commit_message_that_quotes_the_fatal_line_does_not_turn_a_hook_rejection_into_an_outage():
    text, argv = _raised("failed to write commit object", "hook says no")
    prefix = " ".join(argv)
    assert cs.classify(text, argv, lambda: "true", prefix=prefix) is None


def test_the_same_holds_with_no_prefix_given():
    text, argv = _raised("failed to write commit object", "error: hook says no")
    assert cs.classify(text, argv, lambda: "true") is None


def test_an_outage_whose_commit_message_says_no_space_left_is_still_an_outage():
    text, argv = _raised("fix: No space left on device handling", SSH_OUTAGE)
    out = cs.classify(text, argv, lambda: "true", prefix=" ".join(argv))
    assert out is not None and out.startswith(cs.MESSAGE)
    assert "signer down" in out


def test_the_same_outage_is_recognised_with_no_prefix_given():
    text, argv = _raised("fix: No space left on device handling", SSH_OUTAGE)
    assert cs.classify(text, argv, lambda: "true") is not None


def test_a_real_object_store_failure_is_still_not_an_outage():
    text, argv = _raised("feat: x", "error: No space left on device\nfatal: failed to write commit object")
    assert cs.classify(text, argv, lambda: "true", prefix=" ".join(argv)) is None


def test_a_real_outage_with_no_message_words_is_still_an_outage_and_a_plain_one_stays_none_when_signing_is_off():
    text, argv = _raised("feat: x", SSH_OUTAGE)
    assert cs.classify(text, argv, lambda: "true", prefix=" ".join(argv)) is not None
    assert cs.classify(text, argv, lambda: "false", prefix=" ".join(argv)) is None


# --- the same two cases through the real command and `work.commit`


def _work_repo(tmp_path):
    repo = tmp_path / "wrepo"
    (repo / ".sdlc" / "state").mkdir(parents=True)
    (repo / ".sdlc" / "config.json").write_text(json.dumps({"work": {"enabled": True}}))
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / ".gitignore").write_text(".sdlc/\n")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "--", ".gitignore", "a.py")
    _git(repo, "commit", "-q", "-m", "base")
    sdlc = str(repo / ".sdlc")
    state.start_run(sdlc)
    work._save(sdlc, "0001-x.md", {"worktree": str(repo), "branch": "sdlc/0001-x", "base": "main",
                                   "remote": "origin", "pr": ""})
    (repo / "b.py").write_text("y = 2\n")
    return repo, sdlc


def test_work_commit_with_a_hook_rejection_and_a_message_quoting_the_fatal_line_raises_untouched(tmp_path):
    repo, sdlc = _work_repo(tmp_path)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'hook says no' >&2\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(RuntimeError) as err:
        work.commit(sdlc, {"work": {"enabled": True}}, "0001-x.md", message="failed to write commit object")
    assert "hook says no" in str(err.value) and cs.NEEDLE not in str(err.value)
    assert calls(log) == 0


def test_work_commit_with_a_down_signer_and_a_message_saying_no_space_left_is_still_the_park(tmp_path):
    repo, sdlc = _work_repo(tmp_path)
    helper, log = make_signer(tmp_path)
    sign_with(repo, helper)
    out = work.commit(sdlc, {"work": {"enabled": True}}, "0001-x.md", message="fix: No space left handling")
    assert out.startswith(cs.PARK_PREFIX), out
    assert calls(log) == 1

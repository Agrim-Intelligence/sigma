"""#2135: a commit-signing outage is recognised and reported once, in plain words, and never worked around.

WHAT THIS PINS. `commit_signing.py` is the one place that decides "this `git commit` failed because the
host's signing helper is down" and the one place that owns the words. `work.py commit` and every
ledger-publish commit in `sync.py` call it, so they cannot drift. The classifier is deliberately
narrow: signing must be in effect, git's own `failed to write commit object` line must be present, and
no object-store write failure marker may be (a full disk or a read-only object store prints the same
fatal line with signing on, and "your signer is down" would then be a lie).

HOW IT IS CHECKED. The texts below are the shapes git 2.49 printed when measured (gpg format, ssh
format with an arbitrary helper line, a missing helper). The real cloud signer was not run.
"""
import importlib.util
import inspect
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cs = _load("commit_signing")

FATAL = "fatal: failed to write commit object"
GPG = "error: gpg failed to sign the data:\n" + FATAL
SSH = "error: signer down\n\n" + FATAL
SSH_KEYED = "error: Load key \"k\": invalid format\nPermission denied (publickey).\n\n" + FATAL
MISSING_HELPER = "fatal: cannot exec '/opt/signer': No such file or directory\nerror: \n" + FATAL


# --------------------------------------------------------------------------------- unavailable()


@pytest.mark.parametrize("text", [GPG, SSH, SSH_KEYED, MISSING_HELPER], ids=["gpg", "ssh", "ssh-publickey", "missing-helper"])
def test_signing_outage_shapes_are_recognised_when_signing_is_on(text):
    assert cs.unavailable(text, True) is True


@pytest.mark.parametrize("text", [GPG, SSH, SSH_KEYED, MISSING_HELPER], ids=["gpg", "ssh", "ssh-publickey", "missing-helper"])
def test_the_same_texts_are_not_an_outage_when_signing_is_off(text):
    """Without signing in effect there is no signer to blame."""
    assert cs.unavailable(text, False) is False


@pytest.mark.parametrize("text", [
    "git commit -m x: pre-commit hook failed\nhook says no",
    "nothing to commit, working tree clean",
    "error: pathspec 'x' did not match any file(s) known to git",
], ids=["hook-rejection", "nothing-to-commit", "pathspec"])
def test_a_failure_without_gits_fatal_line_is_not_an_outage(text):
    """The fatal-line clause: signing is on, the text is not an outage."""
    assert cs.unavailable(text, True) is False


@pytest.mark.parametrize("text", [
    "error: No space left on device\n" + FATAL,
    "error: unable to create temporary file: Permission denied\n" + FATAL,
    "error: insufficient permission for adding an object to repository database .git/objects\n" + FATAL,
    "error: unable to write file /r/.git/objects/ab/cd: Permission denied\n" + FATAL,
], ids=["no-space", "temporary-file", "insufficient-permission", "permission-denied-on-objects"])
def test_an_object_store_write_failure_with_signing_on_is_not_a_signing_outage(text):
    """Each object-store marker, on its own text, keeps the signing message from lying about a disk."""
    assert cs.unavailable(text, True) is False


def test_the_object_store_markers_match_per_line_and_ignoring_case():
    assert cs.unavailable("ERROR: NO SPACE LEFT ON DEVICE\n" + FATAL, True) is False
    assert cs.unavailable("error: Unable To Create Temporary File\n" + FATAL, True) is False
    assert cs.unavailable("error: INSUFFICIENT PERMISSION for adding an object\n" + FATAL, True) is False
    assert cs.unavailable("error: PERMISSION DENIED writing .GIT/OBJECTS/ab\n" + FATAL, True) is False


def test_permission_denied_counts_only_on_a_line_that_names_the_object_store():
    """A signer's `Permission denied (publickey)` is the signer being down, not the object store.
    The two words may sit on DIFFERENT lines: a whole-text membership test would call this a disk."""
    split = "error: Permission denied (publickey).\nhint: the objects stay local\n" + FATAL
    assert cs.unavailable(split, True) is True
    assert cs.unavailable("error: Permission denied (publickey).\n" + FATAL, True) is True


# --------------------------------------------------------------------------------- signing_on()


def test_forced_signing_is_true_without_reading_the_config():
    def read():
        raise AssertionError("read must not be called when -S forces signing")
    assert cs.signing_on(read, forced=True) is True


def test_signing_on_follows_the_config_read():
    assert cs.signing_on(lambda: "true") is True
    assert cs.signing_on(lambda: "true\n") is True
    assert cs.signing_on(lambda: "false") is False
    assert cs.signing_on(lambda: "") is False


def test_a_config_read_that_raises_means_signing_is_not_on():
    """`git config --type=bool commit.gpgsign` exits 1 when the key is unset; that is "off"."""
    def read():
        raise RuntimeError("exit 1")
    assert cs.signing_on(read) is False


# --------------------------------------------------------------------------------- classify()


def _argv(*args):
    return ["git", "commit", *args]


def test_classify_reads_the_config_only_for_a_text_that_looks_like_the_outage():
    calls = []

    def read():
        calls.append(1)
        return "true"
    assert cs.classify("git commit -m x: pre-commit hook failed", _argv("-m", "x"), read) is None
    assert calls == []
    assert cs.classify("git commit -m x: " + SSH, _argv("-m", "x"), read) is not None
    assert calls == [1]


@pytest.mark.parametrize("flag", ["-S", "-SABCDEF", "--gpg-sign", "--gpg-sign=ABCDEF"])
def test_a_signing_flag_in_the_argv_forces_signing_without_a_config_read(flag):
    def read():
        raise AssertionError("the flag alone must decide")
    assert cs.classify(SSH, _argv(flag, "-m", "x"), read) is not None


@pytest.mark.parametrize("argv", [
    _argv("-m", "-S"),                          # a message that is the text "-S"
    _argv("-m", "x", "--", "-S"),               # a pathspec after the separator
    _argv("--no-gpg-sign", "-m", "x"),
    _argv("-m", "x"),
], ids=["message-is--S", "after-separator", "no-gpg-sign", "plain"])
def test_only_a_real_signing_flag_forces_signing(argv):
    assert cs.classify(SSH, argv, lambda: "false") is None


def test_classify_returns_the_plain_text_with_the_detail():
    out = cs.classify("git commit -m feat: x: " + SSH, _argv("-m", "feat: x"), lambda: "true",
                      prefix="git commit -m feat: x")
    assert out.startswith(cs.NEEDLE) and "signer down" in out


# --------------------------------------------------------------------------------- plain()


def test_plain_carries_the_message_and_the_signers_own_line():
    out = cs.plain(SSH)
    assert cs.MESSAGE in out and "signer down" in out
    assert "\n" not in out, "one line, so it can be recorded as a park reason"


def test_plain_never_offers_the_forbidden_workarounds():
    for text in (SSH, GPG, MISSING_HELPER, ""):
        out = cs.plain(text)
        assert "commit.gpgsign=false" not in out and "--no-gpg-sign" not in out and "gpgsign=false" not in out


def test_plain_strips_the_argv_prefix_the_runners_put_in_front_of_the_stderr():
    """`_run` and `_run_git` raise `<argv>: <stderr>`; the argv must not be shown as the signer's words."""
    raised = "git commit -m feat: x: error: signer down\n\nfatal: failed to write commit object"
    for prefix in ("", "git commit -m feat: x"):
        out = cs.plain(raised, prefix)
        assert "signer down" in out
        assert "git commit -m" not in out


def test_plain_skips_lines_with_nothing_after_the_label():
    """The missing-helper shape has an empty `error: ` line between the two real ones."""
    out = cs.plain("git commit -m x: " + MISSING_HELPER)
    assert "cannot exec" in out and "No such file or directory" in out
    assert "error: )" not in out and "(signer said: )" not in out


def test_plain_detail_is_the_last_line_that_is_not_gits_fatal_line():
    text = "error: first\nsecond line\nerror: third line\n" + FATAL
    out = cs.plain(text)
    assert "third line" in out and "first" not in out
    assert "failed to write commit object" not in out


def test_plain_detail_skips_git_hint_lines():
    """`git rebase` follows the failure with advice (`hint: ... git rebase --continue`), which is not the
    signer's word."""
    text = ("Rebasing (1/1)\nerror: signer down\n\nerror: failed to write commit object\n"
            "hint: Could not execute the todo command\nhint:\nhint:     git rebase --continue")
    out = cs.plain(text)
    assert "signer down" in out and "rebase --continue" not in out


def test_plain_clips_the_detail_to_200_characters():
    out = cs.plain("error: " + "x" * 500 + "\n" + FATAL)
    detail = out[len(cs.MESSAGE):]
    assert "x" * 150 in detail
    assert "x" * 201 not in detail
    assert len(detail) < 300


def test_plain_with_no_usable_detail_is_just_the_message():
    assert cs.plain(FATAL) == cs.MESSAGE


# --------------------------------------------------------------------------------- wording pins


def test_the_wording_is_scoped_to_what_is_true():
    """D2: the unattended rebase engine deliberately signs off in its own pinned path, so the message
    speaks only for the commit and publish steps and promises nothing absolute."""
    assert "will not turn signing off" in cs.MESSAGE
    assert "Start a fresh session" in cs.MESSAGE
    assert not re.search(r"\bnever\b", cs.MESSAGE, re.IGNORECASE)
    assert cs.MESSAGE.startswith(cs.NEEDLE)
    assert cs.NEEDLE == "Commit signing is unavailable on this machine"


def test_the_park_prefix_is_a_park_that_starts_with_the_needle():
    assert cs.PARK_PREFIX == "PARK: " + cs.NEEDLE


def test_the_exception_carries_the_finished_text():
    exc = cs.SigningUnavailable(cs.plain(SSH))
    assert str(exc).startswith(cs.NEEDLE) and isinstance(exc, RuntimeError)


# --------------------------------------------------------------------------------- the park is infrastructure


def test_a_signing_park_is_classified_as_infrastructure_not_as_a_decision():
    """D3: the signer being down is a machine fault, not a tradeoff for a human. `loop._record` hands a
    park reason that falls to `unknown` a "needs human judgment" tier unless the wording is one of
    `work.MECHANICAL_PARK_PREFIXES`; this pins that the signing park is one."""
    loop, work = _load("loop"), _load("work")
    text = "PARK: " + cs.plain(SSH)
    reason_class = loop._reason_class(text)
    assert reason_class == "unknown", "no classifier needle should claim this text"
    assert cs.NEEDLE in work.MECHANICAL_PARK_PREFIXES
    assert loop._mechanical_unknown_detail(text, reason_class) is True


# --------------------------------------------------------------------------------- the receipt consumers


def _pr_fixture(tmp_path, monkeypatch, status):
    """`work.pr` with receipt sharing forced on (it is gated off in production) and the receipt
    publisher answering `status`; every git/gh call is a stub."""
    import json
    work, state = _load("work"), _load("state")
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps({"work": {"enabled": True}}))
    state.start_run(str(sdlc))
    wt = tmp_path / "wt"
    wt.mkdir()
    work._save(str(sdlc), "0001-x.md", {"worktree": str(wt), "branch": "sdlc/0001-x", "base": "main",
                                        "remote": "origin", "pr": ""})
    monkeypatch.setattr(work, "_receipt_sharing_enabled", lambda _config: True)
    monkeypatch.setattr(work, "_receipt_parent_facts", lambda *a, **k: {})
    monkeypatch.setattr(work, "_publish_parent_receipt", lambda *a, **k: status)

    def run(cwd, argv):
        line = " ".join(str(a) for a in argv)
        if "rev-list" in line:
            return "2"
        if "pulls?head=" in line:
            return "9"
        if line.endswith("pulls/9"):
            return "{}"
        return ""
    return work, str(sdlc), run


def test_pr_parks_with_the_plain_text_when_the_receipt_could_not_be_signed(tmp_path, monkeypatch):
    work, sdlc, run = _pr_fixture(tmp_path, monkeypatch, cs.plain(SSH))
    out = work.pr(sdlc, {"work": {"enabled": True}}, "0001-x.md", run=run)
    assert out.startswith(cs.PARK_PREFIX), out
    assert "PR #9" in out and "signer down" in out and out.endswith(cs.PARK_TAIL)
    assert "receipt pending for PR" not in out
    assert work._record(sdlc, "0001-x.md")["pr"] == "", "an unpublished receipt must not record the PR"


def test_pr_keeps_the_receipt_pending_wording_for_every_other_status(tmp_path, monkeypatch):
    work, sdlc, run = _pr_fixture(tmp_path, monkeypatch, "receipt pending: unrelated staged files")
    out = work.pr(sdlc, {"work": {"enabled": True}}, "0001-x.md", run=run)
    assert out == "receipt pending for PR #9: receipt pending: unrelated staged files"


def test_the_merge_receipt_site_surfaces_the_plain_text_and_cannot_park(tmp_path, monkeypatch):
    """The merge already happened server-side and the three callers only print the exception to stderr
    (`merge ... pending`), so nothing here can park: what has to survive is the text that is printed."""
    import json
    work = _load("work")
    sdlc = tmp_path / ".sdlc"
    (sdlc / "ledger").mkdir(parents=True)

    class Receipts:
        ownership_key = staticmethod(lambda facts: "k")
        parent_receipt = staticmethod(lambda facts: b"{}")
        child_receipt = staticmethod(lambda parent, merge: b"{}")
        write_immutable = staticmethod(lambda path, body: None)

    parent = sdlc / "ledger" / "receipts" / "v1" / "k" / "parent.json"
    parent.parent.mkdir(parents=True)
    parent.write_bytes(b"{}")
    answers = []

    class Sync:
        publish_receipt = staticmethod(lambda *a, **k: answers[0])

    real_load = work._load
    monkeypatch.setattr(work, "_load", lambda name: {"merge_observation": Receipts, "sync": Sync}.get(name) or real_load(name))
    monkeypatch.setattr(work, "_receipt_sharing_enabled", lambda _config: True)
    monkeypatch.setattr(work, "_receipt_parent_facts", lambda *a, **k: {})

    def run(cwd, argv):
        return json.dumps({"merged_at": "2026-01-01T00:00:00Z", "merge_commit_sha": "a" * 40})
    rec = {"pr": 9}
    answers.append(cs.plain(SSH))
    with pytest.raises(ValueError) as err:
        work._observe_confirmed_merge(str(sdlc), {}, "0001-x.md", rec, run)
    assert str(err.value).startswith(cs.NEEDLE) and "was not acknowledged" not in str(err.value)
    answers[0] = "receipt pending: receipt worktree/object unavailable"
    with pytest.raises(ValueError, match="was not acknowledged"):
        work._observe_confirmed_merge(str(sdlc), {}, "0001-x.md", rec, run)


# --------------------------------------------------------------------------------- structural guards
# Modelled on tests/test_no_unsupervised_process_pause.py: the safety property here is a thing the code
# must NEVER do (retry unsigned, switch signing off), so it is pinned in the source, not only in
# behaviour. These are green on the real tree; their proof is the control runs recorded with the change.

_FORBIDDEN = ("commit.gpgsign=false", "--no-gpg-sign", "gpgsign=false")


def _sources():
    sync, work = _load("sync"), _load("work")
    return {
        "commit_signing.py": (SCRIPTS / "commit_signing.py").read_text(encoding="utf-8"),
        "sync._commit": inspect.getsource(sync._commit),
        "sync._signing_failure": inspect.getsource(sync._signing_failure),
        "work.commit": inspect.getsource(work.commit),
        "work._receipt_pending": inspect.getsource(work._receipt_pending),
    }


@pytest.mark.parametrize("name", ["commit_signing.py", "sync._commit", "sync._signing_failure", "work.commit",
                                  "work._receipt_pending"])
def test_the_signing_paths_never_switch_signing_off(name):
    source = _sources()[name]
    for needle in _FORBIDDEN:
        assert needle not in source, f"{name} must not contain {needle!r}"


@pytest.mark.parametrize("name", ["sync._commit", "work.commit"])
def test_the_commit_paths_carry_no_config_override(name):
    """A `-c <key>=<value>` in the commit argv is how signing would be bypassed without saying so."""
    assert not re.search(r"""["']-c["']""", _sources()[name]), name


def test_sync_runs_git_commit_in_exactly_one_place():
    """A future raw commit site in sync.py would bypass the classifier and bring the swallowed/wrapped
    outage back; the one place is `_commit`."""
    source = (SCRIPTS / "sync.py").read_text(encoding="utf-8")
    sites = re.findall(r"""\[\s*["']commit["']""", source)
    assert len(sites) == 1, f"expected the single `[\"commit\"` literal inside _commit, found {len(sites)}"
    assert re.search(r"""\[\s*["']commit["']""", inspect.getsource(_load("sync")._commit))


def test_nothing_in_the_signing_paths_retries_a_commit():
    """`_commit` runs git once: no loop and no second `git(` call of the commit argv."""
    import ast
    import textwrap
    body = textwrap.dedent(inspect.getsource(_load("sync")._commit))
    loops = [node for node in ast.walk(ast.parse(body)) if isinstance(node, (ast.For, ast.While))]
    assert loops == []
    assert body.count("git(path, argv)") == 1

"""slack_client.py (#2311/#2312, Epic #2310, `.sdlc/design/2289.md` `### 6`): one Slack-posting
primitive plus its CLI entry point. `post_message` is exercised with an injected `post` throughout
-- no real network call ever reaches Slack from this suite, matching `test_channel_notify.py`'s own
`_post`/`_real_post` testing discipline exactly. The `main(argv)` tests below (slice 2, #2312) mock
the actual network call the same way: by monkeypatching `slack_client._real_post` itself, the exact
function `post_message` falls back to when no `post` is injected -- never a live Slack call."""
import importlib.util
import json
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


slack_client = _mod("slack_client")

TOKEN_ENV = "SIGMA_SLACK_BOT_TOKEN_TEST_" + __name__.replace(".", "_")


def _config(token_env=None):
    return {"drift_watch": {"slack_bot_token_env": token_env or TOKEN_ENV}}


# ------------------------------------------------------------------ the stub degrade (no token)


def test_no_token_degrades_to_a_stub_and_returns_false(monkeypatch, capsys):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    ok = slack_client.post_message("C123", "hello team", _config())
    assert ok is False
    assert "[stub]" in capsys.readouterr().err
    # a stub must NEVER perform a real network call -- proven structurally, not just asserted
    def boom(*a):
        raise AssertionError("must not POST with no token")
    ok2 = slack_client.post_message("C123", "hello team", _config(), post=boom)
    assert ok2 is False


def test_an_empty_token_also_degrades_to_a_stub(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "")
    assert slack_client.post_message("C123", "hi", _config()) is False


def test_default_token_env_name_is_used_when_config_names_none():
    cfg = {"drift_watch": {}}
    assert slack_client._token_env(cfg) == slack_client.DEFAULT_TOKEN_ENV


# ------------------------------------------------------------------ a real post (injected)


def test_a_real_token_posts_through_the_injected_sender(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")
    calls = []

    def fake_post(token, channel_id, text):
        calls.append((token, channel_id, text))
        return True

    ok = slack_client.post_message("C123", "hello team", _config(), post=fake_post)
    assert ok is True
    assert calls == [("xo" "xb-fake-token", "C123", "hello team")]


def test_a_false_return_from_the_sender_is_reported_as_failure(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")
    assert slack_client.post_message("C123", "hi", _config(), post=lambda *a: False) is False


def test_a_raising_sender_is_treated_as_a_failed_post_never_raises(monkeypatch, capsys):
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")

    def boom(*a):
        raise OSError("connection refused")

    ok = slack_client.post_message("C123", "hi", _config(), post=boom)
    assert ok is False
    assert "POST failed (non-fatal)" in capsys.readouterr().err


def test_no_channel_id_never_calls_the_sender(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")

    def boom(*a):
        raise AssertionError("must not POST with no channel_id")

    assert slack_client.post_message("", "hi", _config(), post=boom) is False
    assert slack_client.post_message(None, "hi", _config(), post=boom) is False


# ------------------------------------------------------------------ token_env override (#2336, Component E)


def test_token_env_override_is_read_verbatim_instead_of_the_config_lookup(monkeypatch):
    """The override NAME wins outright -- config's own drift_watch.slack_bot_token_env is never
    even consulted once an explicit `token_env` is given, matching Component E's own wording:
    "used verbatim instead of `_token_env(config)`'s own drift-watch-scoped lookup"."""
    override_env = "SIGMA_SLACK_BOT_TOKEN_OVERRIDE_TEST_" + __name__.replace(".", "_")
    monkeypatch.setenv(override_env, "xo" "xb-commands-token")
    monkeypatch.delenv(TOKEN_ENV, raising=False)   # the drift_watch-scoped default is UNSET
    calls = []
    ok = slack_client.post_message("C123", "hi", _config(), token_env=override_env,
                                    post=lambda token, cid, text: calls.append((token, cid, text)) or True)
    assert ok is True
    assert calls == [("xo" "xb-commands-token", "C123", "hi")]


def test_omitting_token_env_is_byte_identical_to_before(monkeypatch):
    """Every EXISTING caller (drift_watch.py's own tick) omits `token_env` -- confirms that path is
    completely unaffected by the new parameter: `_token_env(config)`'s own lookup still runs."""
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")
    calls = []
    ok = slack_client.post_message("C123", "hello team", _config(),
                                    post=lambda token, cid, text: calls.append((token, cid, text)) or True)
    assert ok is True
    assert calls == [("xo" "xb-fake-token", "C123", "hello team")]


def test_token_env_override_with_no_value_set_degrades_to_the_stub(monkeypatch, capsys):
    """The override NAME is still just a name -- an unset env var behind it degrades to the same
    safe [stub] no-op as any other missing token, never a crash and never a real POST."""
    override_env = "SIGMA_SLACK_BOT_TOKEN_OVERRIDE_TEST_UNSET_" + __name__.replace(".", "_")
    monkeypatch.delenv(override_env, raising=False)

    def boom(*a):
        raise AssertionError("must not POST with no token")

    ok = slack_client.post_message("C123", "hi", _config(), token_env=override_env, post=boom)
    assert ok is False
    assert "[stub]" in capsys.readouterr().err


def test_a_blank_token_env_override_falls_back_to_the_config_lookup(monkeypatch):
    """An empty-string/whitespace `token_env` is not a real override -- `_token_env(config)` still
    decides, exactly like the omitted case, rather than trying to read an env var literally named
    "" or "   "."""
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")
    calls = []
    ok = slack_client.post_message("C123", "hi", _config(), token_env="   ",
                                    post=lambda token, cid, text: calls.append((token, cid, text)) or True)
    assert ok is True
    assert calls == [("xo" "xb-fake-token", "C123", "hi")]


# ------------------------------------------------------------------ scrubbing (BR-15)


def test_text_is_scrubbed_before_it_reaches_the_sender(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")
    seen = []
    secret = "gh" + "p_" + "x" * 36     # shaped like a real gh token, split so this file itself
                                         # never contains an unbroken secret-shaped literal
    slack_client.post_message("C123", "leaked: %s" % secret, _config(),
                               post=lambda token, cid, text: seen.append(text) or True)
    assert secret not in seen[0]
    assert "REDACTED" in seen[0]


# ------------------------------------------------------------------ CLI (#2312, slice 2): _resolve_channel


def _drift_config(token_env=None, sigma=None, org=None):
    channels = {}
    if sigma is not None:
        channels["sigma"] = sigma
    if org is not None:
        channels["org"] = org
    return {"drift_watch": {"slack_bot_token_env": token_env or TOKEN_ENV, "channels": channels}}


def test_resolve_channel_resolves_the_sigma_alias():
    cfg = _drift_config(sigma="C1111111")
    assert slack_client._resolve_channel(cfg, "sigma") == ("C1111111", None)


def test_resolve_channel_resolves_the_org_alias():
    cfg = _drift_config(org="C2222222")
    assert slack_client._resolve_channel(cfg, "org") == ("C2222222", None)


def test_resolve_channel_accepts_a_literal_channel_id():
    channel_id, error = slack_client._resolve_channel(_drift_config(), "C0123456")
    assert channel_id == "C0123456" and error is None


def test_resolve_channel_fails_loudly_when_the_alias_is_not_configured():
    channel_id, error = slack_client._resolve_channel(_drift_config(), "sigma")
    assert channel_id is None
    assert "drift_watch.channels.sigma" in error and "not set" in error


def test_resolve_channel_fails_loudly_on_a_null_alias_value():
    cfg = _drift_config()
    cfg["drift_watch"]["channels"]["org"] = None
    channel_id, error = slack_client._resolve_channel(cfg, "org")
    assert channel_id is None and "not set" in error


def test_resolve_channel_fails_loudly_on_a_non_channel_shaped_string():
    channel_id, error = slack_client._resolve_channel(_drift_config(), "not-a-real-channel")
    assert channel_id is None
    assert "not-a-real-channel" in error and "channel-id-shaped" in error


def test_resolve_channel_never_confuses_an_empty_config_with_success():
    channel_id, error = slack_client._resolve_channel({}, "sigma")
    assert channel_id is None and error is not None


# ------------------------------------------------------------------ CLI (#2312, slice 2): main(argv)


def _sdlc_cwd(tmp_path, config, monkeypatch):
    """Writes a real `.sdlc/config.json` and chdirs into `tmp_path`, matching `main`'s own
    cwd-relative `DEFAULT_SDLC_DIR` (`main`'s `post <channel> <text>` argv shape leaves no
    positional slot for an explicit sdlc_dir, unlike every sibling watch-tick script)."""
    d = tmp_path / ".sdlc"
    d.mkdir()
    (d / "config.json").write_text(json.dumps(config))
    monkeypatch.chdir(tmp_path)
    return d


def test_main_prints_usage_and_exits_nonzero_on_a_bad_invocation(capsys):
    assert slack_client.main(["slack_client.py"]) == 2
    assert "usage:" in capsys.readouterr().err
    assert slack_client.main(["slack_client.py", "post", "C0123456"]) == 2
    assert slack_client.main(["slack_client.py", "notpost", "C0123456", "hi"]) == 2


def test_main_posts_to_the_sigma_alias_through_post_message_unchanged(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")
    _sdlc_cwd(tmp_path, _drift_config(sigma="C1111111"), monkeypatch)
    calls = []
    monkeypatch.setattr(slack_client, "_real_post",
                         lambda token, cid, text: calls.append((token, cid, text)) or True)
    rc = slack_client.main(["slack_client.py", "post", "sigma", "hello", "team"])
    assert rc == 0
    assert calls == [("xo" "xb-fake-token", "C1111111", "hello team")]


def test_main_posts_to_the_org_alias(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")
    _sdlc_cwd(tmp_path, _drift_config(org="C2222222"), monkeypatch)
    calls = []
    monkeypatch.setattr(slack_client, "_real_post",
                         lambda token, cid, text: calls.append((token, cid, text)) or True)
    rc = slack_client.main(["slack_client.py", "post", "org", "status", "update"])
    assert rc == 0
    assert calls == [("xo" "xb-fake-token", "C2222222", "status update")]


def test_main_posts_to_a_literal_channel_id(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")
    _sdlc_cwd(tmp_path, _drift_config(), monkeypatch)
    calls = []
    monkeypatch.setattr(slack_client, "_real_post",
                         lambda token, cid, text: calls.append((token, cid, text)) or True)
    rc = slack_client.main(["slack_client.py", "post", "C9999999", "direct", "push"])
    assert rc == 0
    assert calls == [("xo" "xb-fake-token", "C9999999", "direct push")]


def test_main_fails_loudly_on_a_bad_channel_argument(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(TOKEN_ENV, "xo" "xb-fake-token")
    _sdlc_cwd(tmp_path, _drift_config(), monkeypatch)  # neither alias configured

    def boom(*a):
        raise AssertionError("must not POST on a bad channel argument")

    monkeypatch.setattr(slack_client, "_real_post", boom)
    rc = slack_client.main(["slack_client.py", "post", "sigma", "hi"])
    assert rc == 1
    assert "not set" in capsys.readouterr().err

    rc2 = slack_client.main(["slack_client.py", "post", "not-a-channel", "hi"])
    assert rc2 == 1
    assert "not-a-channel" in capsys.readouterr().err


def test_main_stub_degrades_safely_when_no_token_is_set(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _sdlc_cwd(tmp_path, _drift_config(), monkeypatch)

    def boom(*a):
        raise AssertionError("must not POST with no token")

    monkeypatch.setattr(slack_client, "_real_post", boom)
    rc = slack_client.main(["slack_client.py", "post", "C0123456", "hi there"])
    assert rc == 0
    assert "[stub]" in capsys.readouterr().err


def test_main_degrades_to_an_empty_config_when_config_json_is_unreadable(tmp_path, monkeypatch, capsys):
    """A missing/corrupt config.json must not hard-fail a literal-channel-id push -- only the alias
    path genuinely needs config, and it still fails with the correct message off an empty dict."""
    monkeypatch.chdir(tmp_path)   # no .sdlc/ at all
    monkeypatch.delenv("SIGMA_SLACK_BOT_TOKEN", raising=False)

    def boom(*a):
        raise AssertionError("must not POST with no token")

    monkeypatch.setattr(slack_client, "_real_post", boom)
    rc = slack_client.main(["slack_client.py", "post", "C0123456", "hi"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "could not read" in err and "[stub]" in err

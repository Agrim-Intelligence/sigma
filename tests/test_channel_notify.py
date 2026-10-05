"""channel_notify.py (#1322): the CLI/Channels adapter's Python half. Reuses autowatch.py's own
candidate discovery so a pushed notification always names the exact same candidate `autowatch.py
tick` (no --issue) would itself pick up next."""
import importlib.util
import json
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


channel_notify = _mod("channel_notify")
ledger = _mod("ledger")

ME = "watcher"
OTHER = "amy"


def _config(autowatch_overrides=None, ledger_overrides=None):
    cfg = {"ledger": {"enabled": True, "actor": ME}}
    if ledger_overrides:
        cfg["ledger"].update(ledger_overrides)
    aw = {"enabled": True, "channel_webhook_url": "http://127.0.0.1:8790"}
    if autowatch_overrides:
        aw.update(autowatch_overrides)
    cfg["ledger"]["autowatch"] = aw
    return cfg


def _sdlc(tmp_path, config):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config))
    return d


def _note_or_handoff(d, kind, to, issue=None, goal="g", actor=OTHER, **fields):
    ledger.entries_dir(d).mkdir(parents=True, exist_ok=True)
    path = ledger.entry_file(d, actor)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    n = len([l for l in existing.splitlines() if l.strip()]) + 1
    entry = {"id": f"{actor}:{n}", "ts": ledger._stamp(), "actor": actor,
              "kind": kind, "goal": goal, "to": to}
    if issue is not None:
        entry["issue"] = issue
    entry.update(fields)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


# ------------------------------------------------------------------ enabled()


def test_disabled_when_ledger_off(tmp_path):
    cfg = _config(ledger_overrides={"enabled": False})
    assert channel_notify.enabled(cfg) is False


def test_disabled_when_autowatch_off():
    cfg = _config({"enabled": False})
    assert channel_notify.enabled(cfg) is False


def test_disabled_when_no_channel_webhook_url():
    cfg = _config({"channel_webhook_url": None})
    assert channel_notify.enabled(cfg) is False


def test_enabled_when_all_three_conditions_hold():
    cfg = _config()
    assert channel_notify.enabled(cfg) is True


# ------------------------------------------------------------------ tick(): nothing to do


def test_tick_is_a_noop_when_disabled(tmp_path):
    cfg = _config({"enabled": False})
    d = _sdlc(tmp_path, cfg)
    posts = []
    result = channel_notify.tick(d, config=cfg, run_post=lambda url, payload: posts.append(payload) or True)
    assert result == "" and posts == []


def test_tick_is_a_noop_with_no_candidate(tmp_path):
    cfg = _config()
    d = _sdlc(tmp_path, cfg)
    posts = []
    result = channel_notify.tick(d, config=cfg, run_post=lambda url, payload: posts.append(payload) or True)
    assert result == "" and posts == []


# ------------------------------------------------------------------ tick(): real push


def test_tick_pushes_for_a_real_unactioned_mention(tmp_path):
    cfg = _config()
    d = _sdlc(tmp_path, cfg)
    _note_or_handoff(d, "note", ME, goal="42")
    posts = []
    result = channel_notify.tick(d, config=cfg, run_post=lambda url, payload: posts.append(payload) or True)
    assert "pushed channel notification" in result
    assert len(posts) == 1
    assert posts[0]["issue"] == "42" and posts[0]["kind"] == "note" and posts[0]["goal"] == "42"


def test_tick_posts_to_the_configured_webhook_url(tmp_path):
    cfg = _config({"channel_webhook_url": "http://127.0.0.1:9999"})
    d = _sdlc(tmp_path, cfg)
    _note_or_handoff(d, "note", ME, goal="7")
    seen_urls = []
    channel_notify.tick(d, config=cfg, run_post=lambda url, payload: seen_urls.append(url) or True)
    assert seen_urls == ["http://127.0.0.1:9999"]


def test_tick_ignores_a_mention_not_in_scope(tmp_path):
    cfg = _config({"scope": ["assignments"]})       # a plain note ("mentions") is out of scope
    d = _sdlc(tmp_path, cfg)
    _note_or_handoff(d, "note", ME, goal="42")
    posts = []
    result = channel_notify.tick(d, config=cfg, run_post=lambda url, payload: posts.append(payload) or True)
    assert result == "" and posts == []


# ------------------------------------------------------------------ exactly-once cursor


def test_tick_does_not_repush_the_same_candidate_on_a_later_tick(tmp_path):
    cfg = _config()
    d = _sdlc(tmp_path, cfg)
    _note_or_handoff(d, "note", ME, goal="42")
    posts = []
    run_post = lambda url, payload: posts.append(payload) or True
    first = channel_notify.tick(d, config=cfg, run_post=run_post)
    second = channel_notify.tick(d, config=cfg, run_post=run_post)
    assert "pushed" in first
    assert second == ""
    assert len(posts) == 1


def test_tick_repushes_once_autowatch_has_recorded_a_later_hop(tmp_path):
    """A candidate autowatch already retried once (recording its own outcome note with a higher
    autowatch_hop) is materially different from the one originally pushed -- worth waking the
    channel session again, not silently staying quiet forever after the first miss."""
    cfg = _config()
    d = _sdlc(tmp_path, cfg)
    entry = _note_or_handoff(d, "note", ME, goal="42")
    posts = []
    run_post = lambda url, payload: posts.append(payload) or True
    first = channel_notify.tick(d, config=cfg, run_post=run_post)
    assert "pushed" in first
    # simulate autowatch itself recording a retried, still-unresolved outcome at hop 1
    ledger.safe_append(d, "note", "42", config=cfg, ref=entry["id"], autowatch_hop="1",
                        why="driven /sigma-loop recorded the goal as failed")
    second = channel_notify.tick(d, config=cfg, run_post=run_post)
    assert "pushed" in second
    assert len(posts) == 2


def test_tick_failed_push_is_non_fatal_and_retries_next_tick(tmp_path):
    cfg = _config()
    d = _sdlc(tmp_path, cfg)
    _note_or_handoff(d, "note", ME, goal="42")
    result = channel_notify.tick(d, config=cfg, run_post=lambda url, payload: False)
    assert "failed" in result
    # cursor must NOT have been advanced -- a later tick (e.g. once the channel server is back up)
    # retries the exact same candidate rather than silently giving up on it forever.
    posts = []
    second = channel_notify.tick(d, config=cfg, run_post=lambda url, payload: posts.append(payload) or True)
    assert "pushed" in second and len(posts) == 1


def test_tick_repushes_after_a_precondition_block_leaves_the_hop_unchanged(tmp_path):
    """#1337: autowatch.py deliberately does NOT advance `autowatch_hop` on a retriable-but-
    unattempted outcome (a precondition block, a hop_limit refusal, a drive exception, a nonzero
    exit -- see autowatch.py's own `_tick_inner`, every such branch records `hop=incoming_hop`,
    never `next_hop`). Gating re-push purely on "did the hop advance" therefore makes the adapter
    go permanently silent for a candidate after its very first transient block, even though the
    candidate stays legitimately open and is meant to be retried once conditions clear. This test
    writes EXACTLY the outcome note `_record_outcome` produces for a precondition block (hop
    unchanged, no `state`) and asserts a later tick still re-pushes."""
    cfg = _config()
    d = _sdlc(tmp_path, cfg)
    entry = _note_or_handoff(d, "note", ME, goal="42")
    posts = []
    run_post = lambda url, payload: posts.append(payload) or True
    first = channel_notify.tick(d, config=cfg, run_post=run_post)
    assert "pushed" in first

    # Simulate the exact outcome note autowatch._record_outcome writes on a precondition block:
    # hop stays at incoming_hop (0), no `state` key (candidate remains open/retriable).
    ledger.safe_append(d, "note", "42", config=cfg, ref=entry["id"], autowatch_hop="0",
                        why="blocked on precondition require_gh_auth: gh not authenticated")

    second = channel_notify.tick(d, config=cfg, run_post=run_post)
    assert "pushed" in second
    assert len(posts) == 2

    # And once nothing further has happened since that second push, a third tick stays quiet --
    # the fix must not turn this into an unbounded per-tick notification storm.
    third = channel_notify.tick(d, config=cfg, run_post=run_post)
    assert third == ""
    assert len(posts) == 2


def test_tick_post_raising_is_treated_as_a_failed_push(tmp_path):
    cfg = _config()
    d = _sdlc(tmp_path, cfg)
    _note_or_handoff(d, "note", ME, goal="42")

    def _raise(url, payload):
        raise OSError("connection refused")

    result = channel_notify.tick(d, config=cfg, run_post=_raise)
    assert "failed" in result


def test_main_never_raises_on_a_corrupt_config(tmp_path):
    d = tmp_path / ".sdlc"; (d / "state").mkdir(parents=True)
    (d / "config.json").write_text("{not valid json")
    rc = channel_notify.main(["channel_notify.py", str(d)])
    assert rc == 1

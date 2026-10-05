"""drift_watch.py (#2311, slice 1 of Epic #2310, `.sdlc/design/2289.md`): the passive drift
watcher's real logic -- commit-delta + PR-drift computation, unit enumeration with the
`.sdlc/features/`-unadopted fallback (D-4), and the ledger-backed (not per-machine) dedup
check-and-post (design `### 4`, D-2).

WHY REAL GIT FOR THE COMMIT-DELTA HALF. `_commit_delta` is a thin wrapper over
`feature_rebase.landed_commits`, which is itself `git log --first-parent`. A delta is a property of
what real git computes, not of what this code believes about it (the same reasoning
`test_feature_rebase.py`'s own module docstring gives for its real-git fixture) -- so
`_repo_with_drift` below builds a throwaway bare remote and a real checkout with genuine divergence,
and every commit-delta assertion reads the real result.

WHY A FAKE RUNNER FOR THE PR-DRIFT HALF. `_pr_status` calls `gh api` through the injected `run`;
mocking it (matching `test_unit_completion.py`'s own `_runner` substring-match idiom) proves the
same thing a real call would without ever reaching the network, which the automated suite must
never do.

WHY A REAL LEDGER FOR THE DEDUP HALF. The whole point of `### 4`/D-2 is that the dedup marker is
read back by a DIFFERENT actor's own ledger file -- a mock ledger module would not exercise that,
so these tests write and read the real `ledger.py` against a real `tmp_path` ledger directory
(matching `test_channel_notify.py`'s own real-ledger style), and only the Slack POST itself
(`post`) is ever a stub."""
import importlib.util
import json
import os
import pathlib
import subprocess

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


drift_watch = _load("drift_watch")
ledger = _load("ledger")
feature_registry = _load("feature_registry")


# --------------------------------------------------------------------------- real-git fixture


def _git(cwd, *args):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, p.stderr or p.stdout))
    return p.stdout.strip()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _real_run(cwd, argv):
    """Matches `feature_sync._run`'s `(cwd, argv) -> stdout` contract for real subprocess use --
    only ever handed `git` argv in these tests."""
    proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip())
    return (proc.stdout or "").strip()


def _repo_with_drift(tmp_path, ahead=2, push_feature=True):
    """A bare 'remote' plus a real checkout: `main` cut, `feature/x` branched off it and pushed,
    then `main` moves `ahead` commits further while `feature/x` stays still -- exactly the shape
    `_commit_delta` exists to measure."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "clone", str(remote), str(work))
    _write(work / "a.txt", "1")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-m", "initial")
    _git(work, "push", "origin", "main")
    _git(work, "checkout", "-b", "feature/x")
    if push_feature:
        _git(work, "push", "origin", "feature/x")
    _git(work, "checkout", "main")
    for i in range(ahead):
        _write(work / ("b%d.txt" % i), str(i))
        _git(work, "add", "b%d.txt" % i)
        _git(work, "commit", "-m", "main commit %d" % i)
    _git(work, "push", "origin", "main")
    return work


# --------------------------------------------------------------------------- config / sdlc fixture


def _config(ttl_minutes=90, channels=None, base="main", remote="origin", actor="watcher",
            repo="acme/app", enabled=True, ledger_on=True):
    return {
        "ledger": {"enabled": ledger_on, "actor": actor},
        "drift_watch": {"enabled": enabled, "ttl_minutes": ttl_minutes,
                         "channels": channels if channels is not None else
                                     {"sigma": "C123", "org": None},
                         "slack_bot_token_env": "SIGMA_SLACK_BOT_TOKEN"},
        "work": {"base": base, "remote": remote},
        "discovery": {"github": {"repo": repo}},
    }


def _sdlc(work, config=None):
    d = work / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config if config is not None else {}))
    return d


def _registry(d, units):
    """`units`: `{name: open_bool}` -- writes a real `index.json` via `feature_registry.write_index`
    (the real write path), not a hand-built document."""
    features_dir = feature_registry.registry_dir(d)
    features_dir.mkdir(parents=True, exist_ok=True)
    feature_registry.write_index(features_dir, {name: {"open": is_open}
                                                  for name, is_open in units.items()})


def _boom(_cwd, _argv):
    raise AssertionError("must not run git/gh when the gate is closed or nothing is due")


# ------------------------------------------------------------------ enabled() -- the composed gate


def test_disabled_when_ledger_is_off():
    cfg = _config(ledger_on=False)
    assert drift_watch.enabled(cfg) is False


def test_disabled_when_drift_watch_flag_is_absent():
    cfg = _config()
    del cfg["drift_watch"]["enabled"]
    assert drift_watch.enabled(cfg) is False


def test_disabled_on_a_truthy_string_or_stray_one_not_literal_true():
    cfg = _config()
    cfg["drift_watch"]["enabled"] = "true"
    assert drift_watch.enabled(cfg) is False
    cfg["drift_watch"]["enabled"] = 1
    assert drift_watch.enabled(cfg) is False


def test_enabled_when_both_gates_hold():
    assert drift_watch.enabled(_config()) is True


def test_enabled_true_alone_without_ledger_never_runs():
    """The exact D-2-addendum failure this composition exists to close: `drift_watch.enabled: true`
    set with `ledger.enabled` left off must never look enabled."""
    cfg = _config(ledger_on=False, enabled=True)
    assert drift_watch.enabled(cfg) is False


# ------------------------------------------------------------------ TTL watermark (BR-6 shape)


def test_due_when_no_watermark_exists(tmp_path):
    d = _sdlc(tmp_path, _config())
    assert drift_watch.due(str(d), _config(), now=1000.0) is True


def test_not_due_right_after_a_fresh_stamp(tmp_path):
    d = _sdlc(tmp_path, _config())
    drift_watch._stamp(str(d), now=1000.0)
    assert drift_watch.due(str(d), _config(ttl_minutes=90), now=1000.0 + 60) is False


def test_due_again_once_the_ttl_has_elapsed(tmp_path):
    d = _sdlc(tmp_path, _config())
    drift_watch._stamp(str(d), now=1000.0)
    assert drift_watch.due(str(d), _config(ttl_minutes=90), now=1000.0 + 90 * 60 + 1) is True


def test_due_on_a_corrupt_watermark_file(tmp_path):
    d = _sdlc(tmp_path, _config())
    (d / "state" / drift_watch.WATERMARK_NAME).write_text("{not json", encoding="utf-8")
    assert drift_watch.due(str(d), _config(), now=1000.0) is True


# ------------------------------------------------------------------ unit enumeration (D-4)


def test_no_registry_directory_answers_no_units(tmp_path):
    d = _sdlc(tmp_path, _config())
    assert drift_watch._open_units(str(d)) == []


def test_an_adopted_but_empty_registry_answers_no_units(tmp_path):
    d = _sdlc(tmp_path, _config())
    _registry(d, {})
    assert drift_watch._open_units(str(d)) == []


def test_only_open_units_are_returned_sorted(tmp_path):
    d = _sdlc(tmp_path, _config())
    _registry(d, {"zeta": True, "alpha": True, "closed-one": False})
    assert drift_watch._open_units(str(d)) == [
        ("alpha", "feature/alpha"), ("zeta", "feature/zeta")]


# ------------------------------------------------------------------ _commit_delta (real git)


def test_commit_delta_counts_what_landed_on_base_while_the_feature_branch_was_away(tmp_path):
    work = _repo_with_drift(tmp_path, ahead=2)
    _real_run(work, ["git", "fetch", "origin", "main", "feature/x"])
    delta = drift_watch._commit_delta(_real_run, str(work), "origin", "main", "feature/x")
    assert delta["count"] == 2
    assert delta["subjects"] == ["main commit 1", "main commit 0"]   # newest first


def test_commit_delta_is_zero_when_the_feature_branch_is_current(tmp_path):
    work = _repo_with_drift(tmp_path, ahead=0)
    _real_run(work, ["git", "fetch", "origin", "main", "feature/x"])
    delta = drift_watch._commit_delta(_real_run, str(work), "origin", "main", "feature/x")
    assert delta == {"count": 0, "subjects": []}


def test_commit_delta_caps_the_subject_list_at_newest_subjects(tmp_path):
    work = _repo_with_drift(tmp_path, ahead=drift_watch.NEWEST_SUBJECTS + 2)
    _real_run(work, ["git", "fetch", "origin", "main", "feature/x"])
    delta = drift_watch._commit_delta(_real_run, str(work), "origin", "main", "feature/x")
    assert delta["count"] == drift_watch.NEWEST_SUBJECTS + 2
    assert len(delta["subjects"]) == drift_watch.NEWEST_SUBJECTS


def test_commit_delta_is_none_when_the_branch_was_never_pushed(tmp_path):
    work = _repo_with_drift(tmp_path, ahead=1, push_feature=False)
    _real_run(work, ["git", "fetch", "origin", "main"])
    delta = drift_watch._commit_delta(_real_run, str(work), "origin", "main", "feature/x")
    assert delta is None


# ------------------------------------------------------------------ _pr_status (fake gh runner)


def _gh_run(payload):
    def run(_cwd, _argv):
        return payload
    return run


def test_pr_status_reports_none_with_no_landing_pr():
    run = _gh_run("[]")
    assert drift_watch._pr_status(run, "cwd", "acme/app", "acme", "feature/x") == "none"


def test_pr_status_reports_the_open_pr_number():
    run = _gh_run(json.dumps([{"number": 5, "state": "open", "merged_at": None}]))
    assert drift_watch._pr_status(run, "cwd", "acme/app", "acme", "feature/x") == "open #5"


def test_pr_status_reports_a_closed_never_merged_pr():
    run = _gh_run(json.dumps([{"number": 5, "state": "closed", "merged_at": None}]))
    assert (drift_watch._pr_status(run, "cwd", "acme/app", "acme", "feature/x")
            == "closed #5 (never merged)")


def test_pr_status_reports_a_merged_pr():
    run = _gh_run(json.dumps([{"number": 5, "state": "closed", "merged_at": "2026-01-01T00:00:00Z"}]))
    assert drift_watch._pr_status(run, "cwd", "acme/app", "acme", "feature/x") == "merged #5"


def test_pr_status_prefers_the_open_pr_over_an_older_closed_one():
    run = _gh_run(json.dumps([
        {"number": 3, "state": "closed", "merged_at": None},
        {"number": 9, "state": "open", "merged_at": None}]))
    assert drift_watch._pr_status(run, "cwd", "acme/app", "acme", "feature/x") == "open #9"


def test_pr_status_degrades_to_unknown_when_gh_could_not_be_read():
    def run(_cwd, _argv):
        raise RuntimeError("gh: command not found")
    assert drift_watch._pr_status(run, "cwd", "acme/app", "acme", "feature/x").startswith("unknown")


# ------------------------------------------------------------------ _summarize (issue #2344 formatting)


def _report(unit="x", branch="feature/x", count=2, subjects=None, pr="none"):
    subjects = subjects if subjects is not None else ["main commit 1", "main commit 0"]
    return {"unit": unit, "branch": branch, "delta": {"count": count, "subjects": subjects}, "pr": pr}


def test_summarize_bolds_the_unit_name_with_slack_single_asterisks_not_github_double():
    text = drift_watch._summarize([_report(unit="alpha")])
    assert "*alpha*" in text
    assert "**alpha**" not in text


def test_summarize_leads_with_the_merged_pr_fact_plainly():
    """Issue #2344's ★ finding: an already-merged landing PR means the unit shipped and this is
    stale bookkeeping, not an active neglected branch -- that fact must lead the unit's block, not
    trail a scary commit count at the very end."""
    text = drift_watch._summarize([_report(unit="alpha", count=180, pr="merged #1697")])
    first_line = text.splitlines()[0]
    assert first_line.startswith("*alpha*")
    assert "already landed via #1697" in first_line
    assert "180 commit(s) behind" in first_line
    # the merged fact is not merely present somewhere -- it comes before the commit count
    assert first_line.index("already landed via #1697") < first_line.index("180 commit(s) behind")


def test_summarize_does_not_use_the_merged_lead_for_an_open_pr():
    text = drift_watch._summarize([_report(unit="alpha", pr="open #9")])
    first_line = text.splitlines()[0]
    assert "already landed" not in first_line
    assert "landing PR open #9" in first_line


def test_summarize_does_not_use_the_merged_lead_for_no_pr_or_closed_pr():
    for pr in ("none", "closed #3 (never merged)", "unknown (gh: not found)"):
        text = drift_watch._summarize([_report(unit="alpha", pr=pr)])
        assert "already landed" not in text.splitlines()[0]


def test_summarize_lists_example_commits_as_their_own_indented_dash_lines():
    text = drift_watch._summarize([_report(subjects=["subject one", "subject two"])])
    lines = text.splitlines()
    assert "  - subject one" in lines
    assert "  - subject two" in lines
    # never crammed into one parenthetical on the head line
    head = lines[0]
    assert "subject one" not in head and "subject two" not in head


def test_summarize_notes_truncation_when_fewer_subjects_are_shown_than_the_real_count():
    text = drift_watch._summarize([_report(count=180, subjects=["s1", "s2", "s3"])])
    assert "(showing 3 of 180 commits)" in text


def test_summarize_says_nothing_about_truncation_when_every_commit_is_shown():
    text = drift_watch._summarize([_report(count=2, subjects=["s1", "s2"])])
    assert "showing" not in text


def test_summarize_separates_multiple_units_into_distinct_blocks():
    text = drift_watch._summarize([_report(unit="alpha"), _report(unit="beta")])
    # a blank line between blocks, not one run-together paragraph
    assert "\n\n" in text
    blocks = text.split("\n\n")
    assert len(blocks) == 2
    assert blocks[0].startswith("*alpha*")
    assert blocks[1].startswith("*beta*")


# ------------------------------------------------------------------ _channel_id (D-5)


def test_channel_id_reads_sigma_first():
    cfg = _config(channels={"sigma": "C1", "org": "C2"})
    assert drift_watch._channel_id(cfg) == "C1"


def test_channel_id_falls_back_to_org():
    cfg = _config(channels={"sigma": None, "org": "C2"})
    assert drift_watch._channel_id(cfg) == "C2"


def test_channel_id_is_none_when_neither_is_configured():
    cfg = _config(channels={"sigma": None, "org": None})
    assert drift_watch._channel_id(cfg) is None


# ------------------------------------------------------------------ sweep(): the composed gate


def test_sweep_is_a_cheap_noop_when_disabled(tmp_path):
    cfg = _config(ledger_on=False)
    d = _sdlc(tmp_path, cfg)
    assert drift_watch.sweep(str(d), config=cfg, run=_boom) == ""


def test_sweep_is_a_cheap_noop_when_not_yet_due(tmp_path):
    cfg = _config()
    d = _sdlc(tmp_path, cfg)
    drift_watch._stamp(str(d), now=1000.0)
    assert drift_watch.sweep(str(d), config=cfg, run=_boom, now=1000.0 + 60) == ""


def test_sweep_is_a_noop_with_no_open_units(tmp_path):
    cfg = _config()
    d = _sdlc(tmp_path, cfg)
    # no .sdlc/features/ directory at all
    assert drift_watch.sweep(str(d), config=cfg, run=_boom, now=1000.0) == ""
    # the TTL watermark still advances -- a repo with nothing to check costs one sweep per window
    assert drift_watch.due(str(d), cfg, now=1000.0 + 60) is False


def test_sweep_is_a_noop_when_work_base_is_unset(tmp_path):
    cfg = _config(base="")
    d = _sdlc(tmp_path, cfg)
    _registry(d, {"x": True})
    assert drift_watch.sweep(str(d), config=cfg, run=_boom, now=1000.0) == ""


# ------------------------------------------------------------------ sweep(): end to end


def _sweep_fixture(tmp_path, ahead=2, channels=None, ttl_minutes=90):
    work = _repo_with_drift(tmp_path, ahead=ahead)
    cfg = _config(channels=channels, ttl_minutes=ttl_minutes)
    d = _sdlc(work, cfg)
    _registry(d, {"x": True})

    def run(cwd, argv):
        if argv and argv[0] == "git":
            return _real_run(cwd, argv)
        return "[]"                       # no landing PR, for every gh call in this fixture

    return work, d, cfg, run


def test_sweep_reports_nothing_and_posts_nothing_when_current(tmp_path):
    work, d, cfg, run = _sweep_fixture(tmp_path, ahead=0)
    posts = []
    result = drift_watch.sweep(str(d), config=cfg, run=run, now=1000.0,
                                post=lambda *a: posts.append(a) or True)
    assert result == "" and posts == []


def test_sweep_posts_a_summary_and_writes_the_shared_dedup_marker(tmp_path):
    work, d, cfg, run = _sweep_fixture(tmp_path, ahead=2)
    posts = []
    result = drift_watch.sweep(str(d), config=cfg, run=run, now=1000.0,
                                post=lambda *a: posts.append(a) or True)
    assert "posted" in result
    assert len(posts) == 1
    channel_id, text, posted_cfg = posts[0]
    assert channel_id == "C123"
    assert "feature/x" in text or "x" in text
    assert "2 commit(s) behind" in text
    assert posted_cfg is cfg

    # THE REGRESSION D-2 NAMES: a real entry must land in the shared ledger, with the RIGHT ref --
    # the historical bug (kind/goal/config in the wrong argument slots) wrote nothing at all, and
    # a silently-swallowed write is indistinguishable from success without reading it back.
    entries = ledger.read_all(str(d))
    notes = [e for e in entries if e.get("kind") == "note" and str(e.get("ref", "")).startswith("drift-window:")]
    assert len(notes) == 1
    assert notes[0]["actor"] == "watcher"


def test_sweep_does_not_repost_within_the_same_window_even_from_a_different_actor(tmp_path):
    work, d, cfg, run = _sweep_fixture(tmp_path, ahead=2)
    posts = []
    first = drift_watch.sweep(str(d), config=cfg, run=run, now=1000.0,
                               post=lambda *a: posts.append(a) or True)
    assert "posted" in first

    # A second, DIFFERENT actor's own watcher, ticking inside the same TTL window -- reset only
    # the (per-machine) TTL watermark, never the (shared) ledger, to isolate the ledger dedup path
    # from the TTL-due path.
    (d / "state" / drift_watch.WATERMARK_NAME).unlink()
    other_cfg = _config(actor="teammate")
    second = drift_watch.sweep(str(d), config=other_cfg, run=run, now=1000.0,
                                post=lambda *a: posts.append(a) or True)
    assert "suppressed" in second
    assert len(posts) == 1                # the second actor's own poster was never called

    entries = ledger.read_all(str(d))
    notes = [e for e in entries if e.get("kind") == "note" and str(e.get("ref", "")).startswith("drift-window:")]
    assert len(notes) == 1                # still exactly one marker in the whole shared ledger


def test_sweep_does_not_write_the_marker_when_the_post_fails(tmp_path):
    work, d, cfg, run = _sweep_fixture(tmp_path, ahead=2)
    result = drift_watch.sweep(str(d), config=cfg, run=run, now=1000.0, post=lambda *a: False)
    assert "failed" in result
    entries = ledger.read_all(str(d))
    assert not [e for e in entries if e.get("kind") == "note"
                and str(e.get("ref", "")).startswith("drift-window:")]

    # the TTL watermark is still stamped regardless (BR-6's shape) -- a persistently unreachable
    # Slack endpoint costs one failed POST per window, not a retry on every watch.sh tick
    assert drift_watch.due(str(d), cfg, now=1000.0 + 60) is False

    # ...but with the TTL watermark reset (simulating the NEXT due window, or a teammate's own
    # independent local state), the window is still genuinely open -- the failed post did not
    # falsely mark it covered.
    (d / "state" / drift_watch.WATERMARK_NAME).unlink()
    posts = []
    second = drift_watch.sweep(str(d), config=cfg, run=run, now=1000.0,
                                post=lambda *a: posts.append(a) or True)
    assert "posted" in second and len(posts) == 1


def test_sweep_reports_but_does_not_post_when_no_channel_is_configured(tmp_path):
    work, d, cfg, run = _sweep_fixture(tmp_path, ahead=2, channels={"sigma": None, "org": None})
    posts = []
    result = drift_watch.sweep(str(d), config=cfg, run=run, now=1000.0,
                                post=lambda *a: posts.append(a) or True)
    assert "no drift_watch.channels" in result
    assert posts == []
    assert not [e for e in ledger.read_all(str(d)) if e.get("kind") == "note"]


def test_sweep_stamps_the_ttl_watermark_even_with_nothing_drifted(tmp_path):
    work, d, cfg, run = _sweep_fixture(tmp_path, ahead=0)
    drift_watch.sweep(str(d), config=cfg, run=run, now=1000.0, post=lambda *a: True)
    assert drift_watch.due(str(d), cfg, now=1000.0 + 60) is False


# ------------------------------------------------------------------ sweep(): full posted-text (#2344)


def _sweep_fixture_with_gh(tmp_path, gh_payload, ahead=2, channels=None, ttl_minutes=90):
    """Like `_sweep_fixture`, but every `gh` call in the fake runner answers `gh_payload` instead of
    the hardcoded `"[]"` (no landing PR) -- so a merged- or open-PR case can be exercised end to
    end through the real `sweep()` -> `_pr_status` -> `_summarize` pipeline."""
    work = _repo_with_drift(tmp_path, ahead=ahead)
    cfg = _config(channels=channels, ttl_minutes=ttl_minutes)
    d = _sdlc(work, cfg)
    _registry(d, {"x": True})

    def run(cwd, argv):
        if argv and argv[0] == "git":
            return _real_run(cwd, argv)
        return gh_payload

    return work, d, cfg, run


def test_sweep_posts_the_new_formatting_for_a_drifted_unit_with_an_already_merged_landing_pr(tmp_path):
    """End-to-end: the real message posted for the exact shape issue #2344 reported -- a unit whose
    landing PR already merged. The merged fact must lead, the unit name must be Slack-bold (single
    asterisk), and each shown commit subject must be its own indented line."""
    payload = json.dumps([{"number": 1697, "state": "closed", "merged_at": "2026-01-01T00:00:00Z"}])
    work, d, cfg, run = _sweep_fixture_with_gh(tmp_path, payload, ahead=2)
    posts = []
    result = drift_watch.sweep(str(d), config=cfg, run=run, now=1000.0,
                                post=lambda *a: posts.append(a) or True)
    assert "posted" in result
    assert len(posts) == 1
    channel_id, text, posted_cfg = posts[0]
    assert channel_id == "C123"
    assert posted_cfg is cfg

    lines = text.splitlines()
    unit_line = next(line for line in lines if line.startswith("*x*"))
    assert "already landed via #1697" in unit_line
    assert "2 commit(s) behind" in unit_line
    assert unit_line.index("already landed via #1697") < unit_line.index("2 commit(s) behind")
    assert "**x**" not in text                              # never GitHub-style double-asterisk bold
    assert any(line.startswith("  - main commit") for line in lines)   # commits, own indented lines


def test_sweep_posts_the_new_formatting_for_a_drifted_unit_with_a_still_open_landing_pr(tmp_path):
    """The mirror case: a genuinely active branch with an open landing PR must NOT get the
    merged-and-stale lead, and still gets the same bold-name / indented-commit formatting."""
    payload = json.dumps([{"number": 42, "state": "open", "merged_at": None}])
    work, d, cfg, run = _sweep_fixture_with_gh(tmp_path, payload, ahead=2)
    posts = []
    result = drift_watch.sweep(str(d), config=cfg, run=run, now=1000.0,
                                post=lambda *a: posts.append(a) or True)
    assert "posted" in result
    assert len(posts) == 1
    _, text, _ = posts[0]

    lines = text.splitlines()
    unit_line = next(line for line in lines if line.startswith("*x*"))
    assert "already landed" not in unit_line
    assert "landing PR open #42" in unit_line
    assert "2 commit(s) behind" in unit_line
    assert any(line.startswith("  - main commit") for line in lines)

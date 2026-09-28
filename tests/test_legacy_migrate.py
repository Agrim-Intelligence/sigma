"""#239: `skills/agrim-doctor/scripts/migrate.py`, the one-shot rewrite of state written under the
plugin's previous name. Dry run by default, `--apply` to write, idempotent, byte-preserving, and it
refuses (untouched, listed, exit 2) anything it cannot rewrite with certainty.

Every legacy spelling is built from fragments (`RETIRED`): the previous name is a guarded private
name in this tree (`tests/test_no_private_names.py`). Fixtures are written through Sigma's own
renderers and then respelled, so they are shaped exactly like real state.
"""
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATE = ROOT / "skills" / "agrim-doctor" / "scripts" / "migrate.py"
LOOP = ROOT / "skills" / "agrim-loop" / "scripts"
RETIRED = "loop" + "smith"
RETIRED_ENV = RETIRED.upper() + "_"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _link(link, target):
    """`link.symlink_to(target)`, or a skip where the host cannot make one (Windows without
    symlink rights) -- the same guard as `tests/test_timing_store.py::_link_file`."""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("file symlink creation is not available on this host")


@pytest.fixture()
def mig():
    return _load(MIGRATE, "migrate")


def _old(text):
    return text.replace("<!-- sigma:", "<!-- %s:" % RETIRED)


def legacy_repo(tmp_path, crlf=False):
    """A small repo as the plugin wrote it under its previous name."""
    root = tmp_path / "repo"
    sdlc = root / ".sdlc"
    (sdlc / "features" / "units").mkdir(parents=True)
    (sdlc / "state" / "landing").mkdir(parents=True)
    (sdlc / "state" / "withheld").mkdir(parents=True)
    entry = {"title": "Voice", "owner": "o", "open": True,
             "repos": {"a/b": {"branch": "feature/voice", "authorized": True, "goals": [7, 8]}}}
    index = {"schema": RETIRED + "/features@1", "features": {"voice": entry}}
    (sdlc / "features" / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    (sdlc / "features" / "units" / "voice.json").write_text(json.dumps(index, indent=2) + "\n")
    fdoc = _load(LOOP / "feature_doc.py", "feature_doc")
    doc = _old(fdoc.render_doc("voice", entry)) + "\n## Notes\n\nkept\n"
    if crlf:
        doc = doc.replace("\n", "\r\n")
    (sdlc / "features" / "voice.md").write_bytes(doc.encode())
    (sdlc / "state" / "landing" / "7.json").write_text(json.dumps(
        {"schema": RETIRED + "/landing@1", "goal": "7", "outcome": "not-cross-repo"}, indent=2))
    (sdlc / "state" / "withheld" / "7.json").write_text(json.dumps(
        {"schema": RETIRED + "/withheld@1", "findings": {}, "upstream": []}, indent=2))
    cfg = {"_comment": "kept verbatim", "discovery": {"source": "local-goals"},
           "drift_watch": {"channels": {RETIRED: "C0LEGACY01", "org": None},
                           "slack_bot_token_env": RETIRED_ENV + "SLACK_BOT_TOKEN"},
           "slack_commands": {"app_token_env": RETIRED_ENV + "SLACK_BOT_SOCKET_TOKEN",
                              "bot_token_env": RETIRED_ENV + "SLACK_BOT_TOKEN"},
           "tele" + "metry": {"enabled": False}}
    (sdlc / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    (root / "AGENTS.md").write_text("# Mine\n\n<!-- %s:codex:start -->\nold\n<!-- %s:codex:end -->\n"
                                    % (RETIRED, RETIRED))
    return root, sdlc


def tree(root):
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def run(mig, sdlc, *flags, environ=None, home=None):
    out = io.StringIO()
    code = mig.main(["migrate.py", str(sdlc), *flags], environ=environ or {},
                    home=home or sdlc.parent / "nohome", stdout=out)
    return code, out.getvalue()


def test_dry_run_lists_every_change_and_writes_nothing(mig, tmp_path):
    root, sdlc = legacy_repo(tmp_path)
    before = tree(root)
    code, out = run(mig, sdlc)
    assert code == 0 and tree(root) == before
    assert "DRY RUN" in out
    for rel in (".sdlc/features/index.json", ".sdlc/features/units/voice.json",
                ".sdlc/features/voice.md", ".sdlc/state/landing/7.json",
                ".sdlc/state/withheld/7.json", ".sdlc/config.json", "AGENTS.md"):
        assert "would change %s" % rel in out, (rel, out)
    assert "7 change(s) would be made" in out
    assert "left as is .sdlc/config.json: the previous " in out      # the journal block


def test_apply_migrates_and_a_second_apply_is_a_no_op(mig, tmp_path):
    root, sdlc = legacy_repo(tmp_path)
    before = tree(root)
    code, out = run(mig, sdlc, "--apply")
    assert code == 0, out
    assert "changed .sdlc/features/index.json: schema id" in out
    # The one file created is the unit's `feature_sync` lock, left behind by design (deleting it
    # would reopen the create/delete race `feature_sync._acquire` documents). Nothing is removed.
    after = {k: v for k, v in tree(root).items() if not k.startswith(".sdlc/state/features/")}
    assert set(after) == set(before)
    assert sorted(k for k in after if after[k] != before[k]) == sorted([
        ".sdlc/config.json", ".sdlc/features/index.json", ".sdlc/features/units/voice.json",
        ".sdlc/features/voice.md", ".sdlc/state/landing/7.json", ".sdlc/state/withheld/7.json",
        "AGENTS.md"])
    for p in (sdlc / "features" / "index.json", sdlc / "state" / "landing" / "7.json"):
        assert json.loads(p.read_text())["schema"].startswith("sigma/")
    cfg = json.loads((sdlc / "config.json").read_text())
    assert cfg["drift_watch"]["channels"] == {"sigma": "C0LEGACY01", "org": None}
    assert cfg["slack_commands"]["bot_token_env"] == "SIGMA_SLACK_BOT_TOKEN"
    assert cfg["_comment"] == "kept verbatim" and ("tele" + "metry") in cfg
    text = (sdlc / "features" / "voice.md").read_text()
    assert "<!-- sigma:begin managed" in text and RETIRED not in text and text.endswith("kept\n")
    agents = (root / "AGENTS.md").read_text()
    assert agents.startswith("# Mine\n\n<!-- sigma:codex:start -->") and RETIRED not in agents
    code, out = run(mig, sdlc, "--apply")
    assert code == 0 and "nothing to migrate" in out
    assert {k: v for k, v in tree(root).items() if not k.startswith(".sdlc/state/features/")} == after


def test_the_migrated_state_reads_the_same_through_sigma(mig, tmp_path):
    reg = _load(LOOP / "feature_registry.py", "feature_registry")
    fdoc = _load(LOOP / "feature_doc.py", "feature_doc")
    root, sdlc = legacy_repo(tmp_path)
    before = reg.read(reg.registry_dir(str(sdlc)))
    before_doc = fdoc.parse_doc((sdlc / "features" / "voice.md").read_bytes())
    run(mig, sdlc, "--apply")
    assert reg.read(reg.registry_dir(str(sdlc))) == before and before["voice"]["title"] == "Voice"
    assert fdoc.parse_doc((sdlc / "features" / "voice.md").read_bytes()) == before_doc


def test_crlf_and_the_digest_survive(mig, tmp_path):
    root, sdlc = legacy_repo(tmp_path, crlf=True)
    fdoc = _load(LOOP / "feature_doc.py", "feature_doc")
    old = (sdlc / "features" / "voice.md").read_bytes()
    run(mig, sdlc, "--apply")
    new = (sdlc / "features" / "voice.md").read_bytes()
    assert new.count(b"\r\n") == old.count(b"\r\n")
    assert fdoc.locate(new).checksum == fdoc.locate(old).checksum
    assert old.replace(("<!-- %s:" % RETIRED).encode(), b"<!-- sigma:") == new


def test_an_unknown_legacy_schema_is_left_as_is_not_refused(mig, tmp_path):
    root, sdlc = legacy_repo(tmp_path)
    odd = sdlc / "state" / "odd.json"
    odd.write_text(json.dumps({"schema": RETIRED + "/features@2", "x": 1}))
    code, out = run(mig, sdlc, "--apply")
    assert code == 0 and "left as is .sdlc/state/odd.json" in out
    assert json.loads(odd.read_text())["schema"] == RETIRED + "/features@2"


def test_a_schema_id_that_also_appears_elsewhere_is_refused(mig, tmp_path):
    root, sdlc = legacy_repo(tmp_path)
    p = sdlc / "state" / "landing" / "8.json"
    p.write_text(json.dumps({"schema": RETIRED + "/landing@1", "note": RETIRED + "/landing@1"}))
    before = p.read_bytes()
    code, out = run(mig, sdlc, "--apply")
    assert code == 2 and "refused .sdlc/state/landing/8.json" in out
    assert p.read_bytes() == before


def test_a_config_rewrite_that_would_change_more_than_it_lists_is_refused(mig, tmp_path):
    """The re-parse check is the ONLY guard for this input: the env token is also a KEY."""
    root, sdlc = legacy_repo(tmp_path)
    cfg = json.loads((sdlc / "config.json").read_text())
    cfg["notes"] = {RETIRED_ENV + "SLACK_BOT_TOKEN": "a key that happens to match"}
    (sdlc / "config.json").write_text(json.dumps(cfg, indent=2))
    before = (sdlc / "config.json").read_bytes()
    code, out = run(mig, sdlc, "--apply")
    assert code == 2 and "refused .sdlc/config.json" in out
    assert (sdlc / "config.json").read_bytes() == before


def test_a_garbled_legacy_doc_is_refused(mig, tmp_path):
    root, sdlc = legacy_repo(tmp_path)
    p = sdlc / "features" / "voice.md"
    p.write_text(p.read_text().replace("<!-- %s:end managed -->" % RETIRED, ""))
    before = p.read_bytes()
    code, out = run(mig, sdlc, "--apply")
    assert code == 2 and "refused .sdlc/features/voice.md" in out and p.read_bytes() == before


def test_a_doc_carrying_both_spellings_is_left_for_its_owner(mig, tmp_path):
    root, sdlc = legacy_repo(tmp_path)
    fdoc = _load(LOOP / "feature_doc.py", "feature_doc")
    p = sdlc / "features" / "voice.md"
    p.write_text(fdoc.render_block("voice", {"title": "Voice"}) + "\n\n" + p.read_text())
    before = p.read_bytes()
    code, out = run(mig, sdlc, "--apply")
    assert code == 0 and "left as is .sdlc/features/voice.md" in out and p.read_bytes() == before


def test_a_file_changed_after_planning_is_refused(mig, tmp_path, monkeypatch):
    root, sdlc = legacy_repo(tmp_path)
    target = sdlc / "state" / "landing" / "7.json"
    real_plan = mig.plan

    def racing_plan(*args, **kwargs):
        got = real_plan(*args, **kwargs)
        target.write_text(target.read_text() + " ")         # a writer lands between plan and apply
        return got
    monkeypatch.setattr(mig, "plan", racing_plan)
    code, out = run(mig, sdlc, "--apply")
    assert code == 2 and "refused .sdlc/state/landing/7.json: it changed" in out


def test_a_file_locked_by_another_process_is_refused(mig, tmp_path, monkeypatch):
    root, sdlc = legacy_repo(tmp_path)
    real = os.replace

    def locked(src, dst):
        if str(dst).endswith("7.json"):
            raise PermissionError(13, "in use")
        return real(src, dst)
    monkeypatch.setattr(mig.os, "replace", locked)
    code, out = run(mig, sdlc, "--apply")
    assert code == 2 and "refused .sdlc/state/landing/7.json: in use" in out
    assert not list((sdlc / "state" / "landing").glob("*.tmp*"))


def test_a_symlinked_agents_md_is_refused_and_its_target_untouched(mig, tmp_path):
    """The reviewer's reproduction: `AGENTS.md -> CLAUDE.md`, the old Codex block in CLAUDE.md.
    `os.replace` over the link would swap the LINK for a file and leave CLAUDE.md on the old block
    while reporting success. Refused instead -- the link and its target both unchanged, exit 2."""
    root, sdlc = legacy_repo(tmp_path)
    agents = root / "AGENTS.md"
    (root / "CLAUDE.md").write_bytes(agents.read_bytes())
    agents.unlink()
    _link(agents, "CLAUDE.md")
    before = (root / "CLAUDE.md").read_bytes()
    code, out = run(mig, sdlc, "--apply")
    assert code == 2, out
    assert "refused AGENTS.md: it is a symlink" in out
    assert agents.is_symlink() and os.readlink(agents) == "CLAUDE.md"
    assert (root / "CLAUDE.md").read_bytes() == before


def test_a_symlinked_state_file_is_refused_at_plan_and_at_write(mig, tmp_path, monkeypatch):
    root, sdlc = legacy_repo(tmp_path)
    unit = sdlc / "features" / "units" / "voice.json"
    real = tmp_path / "elsewhere.json"
    real.write_bytes(unit.read_bytes())
    unit.unlink()
    _link(unit, real)
    before = real.read_bytes()
    code, out = run(mig, sdlc)
    assert code == 2 and "refused .sdlc/features/units/voice.json: it is a symlink" in out
    assert "would change .sdlc/features/units/voice.json" not in out
    # ...and in `_write`, for a path that became a link after it was planned.
    root2, sdlc2 = legacy_repo(tmp_path / "two")
    target = sdlc2 / "state" / "landing" / "7.json"
    real_plan = mig.plan
    moved_before = []

    def racing_plan(*args, **kwargs):
        got = real_plan(*args, **kwargs)
        moved = tmp_path / "moved.json"
        moved.write_bytes(target.read_bytes())
        moved_before.append(moved.read_bytes())
        target.unlink()
        _link(target, moved)
        return got
    monkeypatch.setattr(mig, "plan", racing_plan)
    code, out = run(mig, sdlc2, "--apply")
    assert code == 2 and "refused .sdlc/state/landing/7.json: it is a symlink" in out
    moved = tmp_path / "moved.json"
    assert target.is_symlink() and os.readlink(target) == str(moved)
    assert real.read_bytes() == before
    assert moved.read_bytes() == moved_before[0], "the relinked target was written through"


def test_a_watcher_probe_that_errors_says_so(mig, tmp_path, monkeypatch, capsys):
    root, sdlc = legacy_repo(tmp_path)

    def broken(directory, name):
        raise RuntimeError("probe broke")
    monkeypatch.setattr(mig, "_load", broken)
    assert mig._running_watcher(sdlc) is None
    assert "could not check for a running watcher (RuntimeError: probe broke)" in capsys.readouterr().err


def test_apply_refuses_while_a_watcher_is_running(mig, tmp_path, monkeypatch):
    root, sdlc = legacy_repo(tmp_path)
    before = tree(root)
    monkeypatch.setattr(mig, "_running_watcher", lambda sdlc_dir: 4242)
    code, out = run(mig, sdlc, "--apply")
    assert code == 2 and "pid 4242" in out and tree(root) == before


def test_env_names_are_reported_never_values(mig, tmp_path):
    root, sdlc = legacy_repo(tmp_path)
    code, out = run(mig, sdlc, environ={RETIRED_ENV + "SLACK_BOT_TOKEN": "xoxb-SECRET"})
    assert RETIRED_ENV + "SLACK_BOT_TOKEN" in out and "xoxb-SECRET" not in out


def test_the_previous_install_is_named_when_present(mig, tmp_path):
    root, sdlc = legacy_repo(tmp_path)
    home = tmp_path / "home"
    (home / ".claude" / "plugins" / "marketplaces" / RETIRED).mkdir(parents=True)
    code, out = run(mig, sdlc, home=home)
    assert "installed on this machine" in out and "reach teammates through git" in out


def test_a_clean_sigma_repo_has_nothing_to_migrate(mig, tmp_path):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text("{}")
    code, out = run(mig, sdlc, "--apply")
    assert code == 0 and "nothing to migrate" in out


def test_usage_errors(mig, tmp_path):
    assert run(mig, tmp_path / "missing")[0] == 2
    assert run(mig, tmp_path, "--bogus")[0] == 2


def test_the_documented_gesture_runs_as_a_script(tmp_path):
    """The gesture the docs give, as a subprocess: `python3 .../migrate.py .sdlc` then `--apply`."""
    root, sdlc = legacy_repo(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith(RETIRED_ENV)}
    env["HOME"] = env["USERPROFILE"] = str(tmp_path / "home")
    dry = subprocess.run([sys.executable, str(MIGRATE), ".sdlc"], cwd=root, env=env,
                         capture_output=True, text=True)
    assert dry.returncode == 0 and "would change .sdlc/features/index.json" in dry.stdout
    wet = subprocess.run([sys.executable, str(MIGRATE), ".sdlc", "--apply"], cwd=root, env=env,
                         capture_output=True, text=True)
    assert wet.returncode == 0 and "changed .sdlc/features/index.json" in wet.stdout
    again = subprocess.run([sys.executable, str(MIGRATE), ".sdlc", "--apply"], cwd=root, env=env,
                           capture_output=True, text=True)
    assert again.returncode == 0 and "nothing to migrate" in again.stdout

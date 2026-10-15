"""#1425: a `work.base` written with the remote's name (`origin/main`) means `main` in the upkeep pass, the job child, the
scheduler and landing, as it already does in `work.py`. Before, the pass fetched `origin origin/main` and failed with a
missing remote ref."""
import copy

import test_feature_rebase as R
import test_upkeep_sched as T


def _config(base, remote=None):
    cfg = copy.deepcopy(T.open_config())
    cfg["work"]["base"] = base
    if remote:
        cfg["work"]["remote"] = remote
    return cfg


def test_base_of_strips_only_the_configured_remote_lead():
    rebase = R.rebase if hasattr(R, "rebase") else T.sched()._sibling("feature_rebase")
    assert rebase.base_of(_config("main")) == "main"
    assert rebase.base_of(_config("origin/main")) == "main"
    assert rebase.base_of(_config("origin/feature/integration")) == "feature/integration"
    assert rebase.base_of(_config("upstream/main")) == "upstream/main"
    assert rebase.base_of(_config("upstream/main", remote="upstream")) == "main"
    assert rebase.base_of(_config("origin/")) == "origin/"
    assert rebase.base_of(_config("")) == ""


def test_the_scheduler_offers_the_same_refs_for_origin_main_as_for_main():
    sched = T.sched()
    assert sched.refs_of(_config("origin/main"), "voice") == sched.refs_of(_config("main"), "voice") == (
        "origin/main", "origin/feature/voice")


def test_the_job_child_hands_the_bare_base_to_the_unit_pass(tmp_path, capsys):
    seen = []

    def entry(config, sdlc_dir, unit, unit_ref, base_ref, base_name, now, **kw):
        seen.append((unit_ref, base_ref, base_name))
        return {"result": "rewritten"}
    quiet = lambda *a, **k: {"outcome": "current"}
    T.job().run_engine(_config("origin/main"), T.project(tmp_path), str(tmp_path), "voice", environ=T.ENV, upkeep=quiet, unit_pass=entry)
    assert seen == [("refs/remotes/origin/feature/voice", "refs/remotes/origin/main", "main")]
    capsys.readouterr()

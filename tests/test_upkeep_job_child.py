"""The detached upkeep job spawns an inner engine child with an argv of its own. Every other job test hands run_job a fake
`command`, which hides the argv the job really builds: the real one omitted the cap the script's own parser requires, so the
child exited with a usage error and every scheduled job ended `failed`. This test captures the argv run_job really builds
and parses it with the script's own parser."""
import pytest

import test_upkeep_sched as T


class _Stop(Exception):
    pass


def test_the_engine_childs_argv_parses_under_its_own_parser(tmp_path, monkeypatch):
    j, sdlc, seen = T.job(), T.project(tmp_path), {}
    bounded = j._sibling("bounded_run")
    assert hasattr(j, "build_parser"), "the job script must expose its parser so the child's argv can be checked"

    def spy(argv, cwd, cap, **kwargs):
        seen["argv"] = list(argv)
        raise _Stop()

    monkeypatch.setattr(bounded, "run_group", spy)
    with pytest.raises(_Stop):
        j.run_job(T.open_config(), sdlc, str(tmp_path / "proj"), "voice", "r1", 30, environ=T.ENV)
    argv = seen["argv"]
    assert "--engine" in argv, argv
    try:
        args = j.build_parser().parse_args(argv[3:])
    except SystemExit as exc:
        raise AssertionError("the engine child's argv is refused by its own parser (exit %s): %s" % (exc.code, argv))
    assert args.engine and args.unit == "voice" and args.cap == 30.0 and args.run_id == "r1", args

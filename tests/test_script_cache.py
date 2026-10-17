"""Memory guard for the suite's script loading (collection held ~8,700 copies of 123 script modules).

Every `_load("legacy")`-style helper (135 production files, 336 test files) executes the script into a
fresh module, so each load rebuilt the whole sibling tree and pytest kept it all alive: 1,094 MB of
collection RSS per worker, 252 MB once each (name, resolved path) executes once.
`tests/_script_cache.py` is the one memoized loader, installed by `conftest.py`.

ISOLATION IT CHANGES: two loads of the same (name, path) now return the SAME module object, so a test
that mutates a script's globals directly (not via monkeypatch) is visible to later tests in that
process. Mark such a test `@pytest.mark.fresh_scripts` to get uncached loads for its duration.

Each clause below was broken on its own and seen red (see the PR): no cache; key on name only; key on
path only; the copies ceiling; the RSS ceiling.
"""
import gc
import importlib.util
import pathlib
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"
#: A script is legitimately loaded under a handful of names (tests prefix them: "t_x", "x_as"), so the
#: ceiling is per distinct (name) not 1. Unfixed code measured up to 1,227 for one script.
MAX_COPIES = 12


def _load(name, as_name=None, path=None):
    spec = importlib.util.spec_from_file_location(as_name or name, path or SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_same_name_and_path_is_one_module():
    assert _load("legacy", "memo_a") is _load("legacy", "memo_a")


def test_different_name_is_a_different_module():
    assert _load("legacy", "memo_a") is not _load("legacy", "memo_b")


def test_different_path_is_a_different_module(tmp_path):
    other = tmp_path / "legacy.py"
    other.write_text("MARK = 1\n")
    assert _load("legacy", "memo_c", other) is not _load("legacy", "memo_c")


def test_siblings_of_a_reloaded_script_are_shared():
    assert _load("feature_registry", "memo_r1").legacy is _load("feature_registry", "memo_r2").legacy


@pytest.mark.fresh_scripts
def test_fresh_scripts_marker_restores_isolation():
    assert _load("legacy", "memo_d") is not _load("legacy", "memo_d")


def _live_copies():
    counts = {}
    for o in gc.get_objects():
        if isinstance(o, types.ModuleType):
            f = getattr(o, "__file__", None)
            if f and f.endswith(".py") and ROOT in pathlib.Path(f).parents:
                counts[f] = counts.get(f, 0) + 1
    return counts


def test_no_script_has_runaway_live_copies():
    worst = sorted(_live_copies().items(), key=lambda kv: -kv[1])[:5]
    assert worst[0][1] <= MAX_COPIES, f"live copies of one script module: {worst}"


def test_collection_peak_rss_was_recorded_and_bounded(request):
    """The enforcement is conftest's `pytest_collection_finish` (red under --collect-only too); this
    checks the recording it relies on and that a whole-suite run is inside the bound."""
    from _script_cache import rss_bound_mb
    peak, n = request.config._collect_rss_mb, request.config._collect_n
    assert peak > 0 and n > 0
    assert n <= 200 or peak <= rss_bound_mb(n)

"""The suite's one memoized script loader. See tests/test_script_cache.py for why and what it changes."""
import importlib.util as U
import pathlib

_specs, _mods = {}, {}
_sfl, _mfs = U.spec_from_file_location, U.module_from_spec
fresh = False   # set by conftest for @pytest.mark.fresh_scripts tests


class _Once:
    def __init__(self, loader):
        self._l = loader

    def create_module(self, spec):
        return self._l.create_module(spec)

    def exec_module(self, module):
        if not getattr(module, "__memo_done__", False):
            module.__memo_done__ = True
            self._l.exec_module(module)


def spec_from_file_location(name, location=None, *a, **k):
    if fresh or location is None:
        return _sfl(name, location, *a, **k)
    key = (name, str(pathlib.Path(location).resolve()))
    if key not in _specs:
        spec = _sfl(name, location, *a, **k)
        if spec is not None and spec.loader is not None:
            spec.loader = _Once(spec.loader)
        _specs[key] = spec
    return _specs[key]


def module_from_spec(spec):
    if fresh or not isinstance(spec.loader, _Once):
        return _mfs(spec)
    return _mods.setdefault(id(spec), _mfs(spec))


def install():
    U.spec_from_file_location, U.module_from_spec = spec_from_file_location, module_from_spec


def rss_bound_mb(n_tests):
    """Measured: 252 MB for 17,875 tests with the cache, 1,094 MB without (~61 KB/test). 120 MB base +
    25 KB/test is 567 MB here: red for the unfixed code, 2x headroom over the fixed."""
    return 120 + 0.025 * n_tests


#: Collection measured at most 13 live copies of one script with the cache (1,460 without). A script is
#: legitimately loaded under a few names (tests prefix them), so the ceiling is not 1.
MAX_COPIES = 20


def worst_live_copies(root):
    import gc, types
    counts = {}
    for o in gc.get_objects():
        if isinstance(o, types.ModuleType):
            f = getattr(o, "__file__", None)
            if f and f.endswith(".py") and root in pathlib.Path(f).parents:
                counts[f] = counts.get(f, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:3]

"""#2280: a legacy dossier (a bare `## Reuse` heading, no Source or Searched-at line) is never refused for its
Source line. This guard passes at base by design, so it lives outside the red-first selectors in the plan."""
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent


def _fixtures():
    spec = importlib.util.spec_from_file_location("_reuse_fixtures", HERE / "test_reuse.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


T = _fixtures()


def test_a_legacy_dossier_with_a_bare_reuse_heading_is_never_refused_for_source(tmp_path):
    text = "# Research: x\n\n## Reuse\n- `a.py`: a note.\n"
    assert T._run(tmp_path, text) == ([], [T.OLD_SECTION])
    assert T._run(tmp_path, text, at_research=True) == ([], [T.OLD_SECTION])

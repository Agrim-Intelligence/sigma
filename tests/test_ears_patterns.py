"""#659: the one place the EARS / TAGGED / VAGUE patterns live (drift pinned to prd_lint.py:22-26)."""
import importlib.util
import pathlib

_P = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts" / "ears_patterns.py"


def _mod():
    spec = importlib.util.spec_from_file_location("ears_patterns", _P)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_pattern_strings_match_prd_lint_verbatim():
    # Literals copied from ~/.sigma-ops/prd/prd_lint.py:22-26. A change here is deliberate.
    m = _mod()
    assert m.COMP == r"THE \S.*? SHALL( NOT)? \S"
    assert m.EARS.pattern == (r"^((WHEN|WHILE|IF|WHERE) [^,]+(, (WHEN|WHILE|IF) [^,]+)*, (THEN )?)?"
                              + r"THE \S.*? SHALL( NOT)? \S")
    assert m.TAGGED.pattern == r"^(MEASURED|DOC|PROCESS): \S"
    assert m.VAGUE.pattern == r"^(add|support|handle|improve|update|fix|make|ensure|clean up|refactor)\b"


def test_passes():
    m = _mod()
    for s in ("WHEN a user logs in, THE system SHALL show the home page",
              "IF the disk is full, THEN THE writer SHALL refuse",
              "WHILE locked, THE door SHALL stay shut",
              "WHERE a gpu exists, THE job SHALL use it",
              "THE cache SHALL evict stale rows",
              "THE cache SHALL NOT grow past the cap"):
        assert m.is_ears(s), s


def test_fails():
    m = _mod()
    for s in ("Add support for X", "THE cache shall evict rows", "", "WHEN x THE y SHALL z",
              "Add support for X. THE cache SHALL evict rows"):
        assert not m.is_ears(s), s


def test_classify():
    m = _mod()
    assert m.classify("THE a SHALL b") == "ears"
    assert m.classify("PROCESS: do a thing") == "tagged"
    assert m.classify("Add support for X") == "vague"
    assert m.classify("something else") == "other"

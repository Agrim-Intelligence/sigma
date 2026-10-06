"""#684: on an INLINE reviewer route the different-agent checks do not apply; `record done` says so loudly
(the phases are recorded, not proved independent). A separate file so the planned test file's bytes, and the
assertion-red evidence bound to them, stay as they were."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_phase_record_gate as g  # noqa: E402


def test_done_on_an_inline_route_prints_a_loud_warning(tmp_path):
    p = g._project(tmp_path)
    g.full_run(p)                                                  # the test env has no host marker: inline
    r = g.done(p)
    assert r.returncode == 0 and "INLINE" in r.stderr and "not proved independent" in r.stderr, r.stderr

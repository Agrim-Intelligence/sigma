# SPDX-License-Identifier: MIT
"""Review fix for `tools/verify_public_repo.py` (issue 397, PR review cycle 3, finding 6).

The `visibility` check reads `private` from `repos/X`. A reply with no `private` field, or one that
is not a JSON boolean, says nothing about visibility, so the check is a FAIL line (never `public`
by default, never a refusal). Reuses the REST fake of `tests/test_verify_public_repo.py`, imported
INSIDE each test body (tests/ is on sys.path), so a missing tool is an AssertionError red.
"""
import pytest


@pytest.mark.parametrize("value", ["missing", None, "true", "false", 0, 1])
def test_visibility_without_a_boolean_private_field_is_a_fail_line(value, tmp_path, capsys):
    expect = "public"                     # "private" already FAILs: only the public default was wrong
    import test_verify_public_repo as base
    mod = base._tool()
    world = base._world()
    if value == "missing":
        del world[base.E_REPO]["private"]
    else:
        world[base.E_REPO]["private"] = value
    rc, out, err, gh = base._run(mod, tmp_path, world, visibility=expect, capsys=capsys)
    assert not gh.violations, gh.violations
    assert err == "", err
    parsed, summary = base._lines(out)
    status = {check: (s, detail) for s, check, detail in parsed}
    assert status["visibility"][0] == "FAIL", "visibility passed on private=%r: %s" % (value, out)
    assert "private" in status["visibility"][1], status["visibility"]
    assert rc == 1, (rc, out)
    assert [c for c in base.CHECKS if status[c][0] == "FAIL"] == ["visibility"], out


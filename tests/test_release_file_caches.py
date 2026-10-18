"""A test file's collected `rulebook` copy keeps `_BANK` (a whole fresh `unpark` script tree, ~79 copies of
`legacy.py`) alive for the rest of the session, because test-time loads are not cached and the copy is a
test-module global. Measured (bounded `-n 0` run, 400 tests): 395 of 396 live-after-gc `legacy.py` copies were
reachable only through `<test module>.RB._BANK`, 5 test files so far. conftest releases such caches when the
run leaves the file; this proves the release is wired to the file boundary and not earlier."""
import types

import conftest


def _file(bank):
    helper = types.ModuleType("helper")
    helper._BANK = bank
    test_file = types.ModuleType("test_x")
    test_file.RB = helper
    return test_file


def _item(mod):
    return types.SimpleNamespace(module=mod)


def test_the_bank_survives_between_two_tests_of_one_file():
    f = _file([object()])
    conftest.pytest_runtest_teardown(_item(f), _item(f))
    assert len(f.RB._BANK) == 1


def test_the_bank_is_released_when_the_next_test_is_in_another_file():
    f = _file([object()])
    conftest.pytest_runtest_teardown(_item(f), _item(_file([])))
    assert f.RB._BANK == []


def test_the_bank_is_released_after_the_last_test():
    f = _file([object()])
    conftest.pytest_runtest_teardown(_item(f), None)
    assert f.RB._BANK == []

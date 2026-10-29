"""#2107: `tests/conftest.py`'s `pytest_xdist_auto_num_workers` takes the smaller of its memory answer and
`PYTEST_XDIST_AUTO_NUM_WORKERS` when that is a positive integer. The guard's grant reaches Sigma's own suite
only through that variable; the conftest answers the hook first and xdist's own (`trylast`) never runs."""
import conftest
import pytest


@pytest.fixture
def machine(monkeypatch):
    """16 CPUs and 64 GB: the memory answer is min(16, 64000 // 4 // 2000) = 8."""
    monkeypatch.setattr(conftest, "_ram_mb", lambda: 64000)
    monkeypatch.setattr(conftest.os, "cpu_count", lambda: 16)
    monkeypatch.delenv("SIGMA_TEST_WORKER_MB", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    return monkeypatch


def test_the_variable_lowers_the_memory_answer(machine):
    """AC-1."""
    machine.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "1")
    assert conftest.pytest_xdist_auto_num_workers(None) == 1
    machine.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "3")
    assert conftest.pytest_xdist_auto_num_workers(None) == 3


@pytest.mark.parametrize("value", [None, "", " ", "0", "-2", "abc", "2.5", "1e1"])
def test_an_unusable_variable_changes_nothing(machine, value):
    """AC-2: unset, empty, zero, negative or not an integer."""
    if value is not None:
        machine.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", value)
    assert conftest.pytest_xdist_auto_num_workers(None) == 8


def test_the_variable_never_raises_the_count(machine):
    """AC-3."""
    machine.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "99")
    assert conftest.pytest_xdist_auto_num_workers(None) == 8
    machine.setattr(conftest, "_ram_mb", lambda: 1000)   # 1000 // 4 // 2000 = 0 -> the floor of 1 stands
    assert conftest.pytest_xdist_auto_num_workers(None) == 1


def test_the_per_worker_figure_is_untouched(machine):
    machine.setenv("SIGMA_TEST_WORKER_MB", "4000")
    assert conftest.pytest_xdist_auto_num_workers(None) == 4
    machine.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "2")
    assert conftest.pytest_xdist_auto_num_workers(None) == 2


def test_a_padded_integer_counts(machine):
    machine.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", " 2 ")
    assert conftest.pytest_xdist_auto_num_workers(None) == 2


def test_the_verify_workers_bullet_names_the_override():
    """AC-4: the docs say a project conftest implementing the hook overrides the variable unless it reads it."""
    import pathlib
    text = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "parallelism.md").read_text()
    bullet = text[text.index("- **Verify workers.**"):text.index("- **Shed.**")]
    flat = " ".join(bullet.split())
    assert "project `conftest.py` that implements the hook overrides the variable unless it reads it too" in flat

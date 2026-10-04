import pytest

from tasks import TaskList


def test_add_returns_increasing_ids():
    tasks = TaskList()
    assert tasks.add("write") == 1
    assert tasks.add("review") == 2


def test_complete_removes_from_open_titles():
    tasks = TaskList()
    first = tasks.add("write")
    tasks.add("review")
    tasks.complete(first)
    assert tasks.open_titles() == ["review"]


def test_complete_unknown_id_raises():
    with pytest.raises(KeyError):
        TaskList().complete(7)


def test_a_high_priority_task_is_listed_first():
    tasks = TaskList()
    tasks.add("a")
    tasks.add("b", priority="high")
    assert tasks.open_titles_by_priority() == ["b", "a"]

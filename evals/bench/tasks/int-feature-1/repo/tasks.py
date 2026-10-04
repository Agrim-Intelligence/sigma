"""A tiny in-memory task list."""


class TaskList:
    def __init__(self):
        self._tasks = []

    def add(self, title):
        """Add an open task and return its id (ids start at 1 and are never reused)."""
        task_id = len(self._tasks) + 1
        self._tasks.append({"id": task_id, "title": title, "done": False})
        return task_id

    def complete(self, task_id):
        """Mark a task done. Raises KeyError for an unknown id."""
        for task in self._tasks:
            if task["id"] == task_id:
                task["done"] = True
                return
        raise KeyError(task_id)

    def open_titles(self):
        """Titles of the tasks not yet done, in the order they were added."""
        return [task["title"] for task in self._tasks if not task["done"]]

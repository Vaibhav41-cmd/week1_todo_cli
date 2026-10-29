"""Service layer: the application's use-cases.

All business rules live here and nowhere else:

* id allocation (monotonic, never reused),
* completing / re-opening a task and keeping timestamps consistent,
* filtering and sorting,
* statistics,
* graceful recovery from a damaged data file.

The CLI is a thin wrapper around this class, and the tests exercise it
directly.  That is what makes the app testable without any I/O mocking.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .errors import CorruptStorageError, TaskNotFoundError, ValidationError
from .models import Priority, Status, Task, utc_now
from .storage import Storage

SORT_KEYS = ("id", "priority", "created", "due", "title")


class TodoService:
    """Use-cases for managing a collection of :class:`Task` objects."""

    def __init__(self, storage: Storage, today: date | None = None) -> None:
        self._storage = storage
        # Injectable "today" keeps date-dependent logic deterministic in tests.
        self._today = today
        self._tasks: dict[int, Task] = {}
        self._next_id: int = 1
        self.warnings: list[str] = []
        self._load()

    # ------------------------------------------------------------------ #
    # Loading / saving
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        """Populate memory from storage, skipping (and reporting) bad records."""
        try:
            payload = self._storage.load()
        except CorruptStorageError as exc:
            # A damaged file is a recoverable situation: warn, start clean and
            # let the next write rebuild the file.  The original bytes are
            # preserved by the storage layer's quarantine step.
            self.warnings.append(str(exc))
            payload = {"schema_version": 1, "next_id": 1, "tasks": []}

        skipped = 0
        max_id = 0
        for record in payload.get("tasks", []):
            try:
                task = Task.from_dict(record)
            except ValidationError:
                skipped += 1  # one bad row must never hide the good ones
                continue
            self._tasks[task.task_id] = task
            max_id = max(max_id, task.task_id)

        # `next_id` from the file wins, but never below max(existing id) + 1, so
        # ids stay unique even if the counter was hand-edited.
        try:
            stored_next = int(payload.get("next_id", 1))
        except (TypeError, ValueError):
            stored_next = 1
        self._next_id = max(stored_next, max_id + 1, 1)

        if skipped:
            self.warnings.append(f"Skipped {skipped} invalid task record(s) while loading.")

    def _persist(self) -> None:
        """Write the full document back to storage."""
        self._storage.save(
            {
                "schema_version": 1,
                "next_id": self._next_id,
                "tasks": [task.to_dict() for task in self._ordered_everything()],
            }
        )

    def _ordered_everything(self) -> list[Task]:
        return [self._tasks[task_id] for task_id in sorted(self._tasks)]

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _require(self, task_id: int) -> Task:
        """Return the task or raise :class:`TaskNotFoundError`."""
        try:
            return self._tasks[int(task_id)]
        except (KeyError, TypeError, ValueError):
            raise TaskNotFoundError(task_id) from None

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    def add(
        self,
        title: str,
        description: str = "",
        priority: Priority | str | int = Priority.MEDIUM,
        due_date: str | None = None,
    ) -> Task:
        """Create and persist a new task, returning it."""
        now = utc_now()
        task = Task(
            task_id=self._next_id,
            title=title,
            description=description,
            priority=Priority.parse(priority),
            due_date=due_date,
            created_at=now,
            updated_at=now,
        )
        self._tasks[task.task_id] = task
        self._next_id = task.task_id + 1
        self._persist()
        return task

    def get(self, task_id: int) -> Task:
        """Return one task by id."""
        return self._require(task_id)

    def update(
        self,
        task_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: Priority | str | int | None = None,
        due_date: str | None = None,
        clear_due_date: bool = False,
    ) -> Task:
        """Apply a partial update.

        Only the arguments that are not ``None`` are touched, so the CLI can
        forward optional flags directly without "did the user pass this?" logic.
        """
        task = self._require(task_id)
        if title is not None:
            task.title = title
        if description is not None:
            task.description = description
        if priority is not None:
            task.priority = Priority.parse(priority)
        if clear_due_date:
            task.due_date = None
        elif due_date is not None:
            task.due_date = due_date
        # Re-run normalisation + validation on the mutated object.
        task.__post_init__()
        task.updated_at = utc_now()
        self._persist()
        return task

    def complete(self, task_id: int) -> Task:
        """Mark a task as completed (idempotent)."""
        task = self._require(task_id)
        if not task.is_completed:
            task.status = Status.COMPLETED
            task.completed_at = utc_now()
            task.updated_at = task.completed_at
            self._persist()
        return task

    def reopen(self, task_id: int) -> Task:
        """Move a task back to pending (the inverse of :meth:`complete`)."""
        task = self._require(task_id)
        if task.is_completed:
            task.status = Status.PENDING
            task.completed_at = None
            task.updated_at = utc_now()
            self._persist()
        return task

    def remove(self, task_id: int) -> Task:
        """Delete a task permanently and return the deleted object."""
        task = self._require(task_id)
        del self._tasks[task.task_id]
        self._persist()
        return task

    def clear_completed(self) -> int:
        """Delete every completed task; return how many were removed."""
        done = [task for task in self._tasks.values() if task.is_completed]
        for task in done:
            del self._tasks[task.task_id]
        if done:
            self._persist()
        return len(done)

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def list_tasks(
        self,
        status: Status | str | None = None,
        priority: Priority | str | int | None = None,
        sort: str = "id",
    ) -> list[Task]:
        """Return tasks, optionally filtered by status/priority, then sorted.

        ``sort`` accepts: ``id``, ``priority`` (high first), ``created``
        (newest first), ``due`` (soonest first, undated last) or ``title``.
        """
        if sort not in SORT_KEYS:
            raise ValidationError(f"Unknown sort key {sort!r}. Choose from {', '.join(SORT_KEYS)}.")

        tasks: Iterable[Task] = self._tasks.values()
        if status is not None:
            wanted = Status.parse(status)
            tasks = [task for task in tasks if task.status is wanted]
        if priority is not None:
            wanted_priority = Priority.parse(priority)
            tasks = [task for task in tasks if task.priority is wanted_priority]

        tasks = list(tasks)
        if sort == "id":
            tasks.sort(key=lambda task: task.task_id)
        elif sort == "priority":
            # Highest priority first, then oldest id for a stable, predictable order.
            tasks.sort(key=lambda task: (-int(task.priority), task.task_id))
        elif sort == "created":
            tasks.sort(key=lambda task: (task.created_at, task.task_id), reverse=True)
        elif sort == "due":
            # Tasks without a due date go last instead of sorting as "ancient".
            tasks.sort(key=lambda task: (task.due_date is None, task.due_date or "", task.task_id))
        elif sort == "title":
            tasks.sort(key=lambda task: (task.title.lower(), task.task_id))
        return tasks

    def search(self, keyword: str) -> list[Task]:
        """Case-insensitive substring search over title and description."""
        needle = (keyword or "").strip().lower()
        if not needle:
            raise ValidationError("Search keyword must not be empty.")
        return [
            task
            for task in self._ordered_everything()
            if needle in task.title.lower() or needle in task.description.lower()
        ]

    def stats(self) -> dict[str, Any]:
        """Return aggregate numbers used by the ``stats`` command."""
        tasks = list(self._tasks.values())
        total = len(tasks)
        completed = sum(1 for task in tasks if task.is_completed)
        pending = total - completed
        by_priority = {
            priority.label: sum(1 for task in tasks if task.priority is priority)
            for priority in Priority
        }
        today = self._today or date.today()
        return {
            "total": total,
            "pending": pending,
            "completed": completed,
            "overdue": sum(1 for task in tasks if task.is_overdue(today)),
            "completion_rate": round((completed / total) * 100, 1) if total else 0.0,
            "by_priority": by_priority,
        }

    def export_records(self) -> list[dict[str, Any]]:
        """Plain records for ``export`` (keeps the CLI free of model knowledge)."""
        return [task.to_dict() for task in self._ordered_everything()]

    def __len__(self) -> int:
        return len(self._tasks)

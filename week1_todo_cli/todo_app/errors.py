"""Custom exception hierarchy for the To-Do List Manager.

Design choice
-------------
Every error that the application *expects* to encounter derives from a single
base class (:class:`TodoError`).  The CLI therefore needs exactly one
``except`` clause to turn a domain problem into a friendly message plus a
non-zero exit code, instead of leaking tracebacks to the user.

Separating :class:`ValidationError` (bad user input) from
:class:`StorageError` (bad disk / bad file) also lets callers decide whether
the safest reaction is "ask again" or "refuse to continue".
"""

from __future__ import annotations


class TodoError(Exception):
    """Base class for all recoverable application errors."""


class ValidationError(TodoError):
    """Raised when user supplied data breaks a domain rule."""


class StorageError(TodoError):
    """Raised when the persistence layer cannot fulfil a request."""


class CorruptStorageError(StorageError):
    """Raised when the data file exists but cannot be decoded.

    The offending file is moved aside by the storage layer before this is
    raised, so no user data is ever silently overwritten.
    """

    def __init__(self, message: str, backup_path: str | None = None) -> None:
        super().__init__(message)
        self.backup_path = backup_path


class TaskNotFoundError(TodoError):
    """Raised when an operation targets a task id that does not exist."""

    def __init__(self, task_id: int) -> None:
        super().__init__(f"No task with id {task_id}.")
        self.task_id = task_id

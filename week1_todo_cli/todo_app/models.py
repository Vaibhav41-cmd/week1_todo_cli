"""Domain models for the To-Do List Manager.

Design choice
-------------
The models know nothing about *how* tasks are stored or displayed.  A ``Task``
validates itself and converts to/from a plain ``dict`` record, which keeps the
persistence layer (JSON) and the presentation layer (CLI) completely
swappable.  This is the "Single Responsibility" and "Dependency Inversion"
part of the design described in DESIGN.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Mapping

from .errors import ValidationError

# --- Domain constants (single source of truth, reused by the CLI & tests) ---
MAX_TITLE_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 1000


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with second precision.

    Design choice: timestamps are stored as ISO-8601 *strings* so the JSON data
    file stays human readable, sortable and timezone-unambiguous.  Seconds are
    precise enough for a to-do list and keep the file tidy.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Priority(IntEnum):
    """Task priority.

    ``IntEnum`` is deliberate: priorities sort and compare numerically for free
    (HIGH > MEDIUM > LOW), while the ``label`` property gives a friendly name
    for the UI and for the JSON file.
    """

    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @property
    def label(self) -> str:
        """Lower-case name used in the CLI and in the saved file."""
        return self.name.lower()

    @property
    def marker(self) -> str:
        """One-character badge used when rendering a task row."""
        return {Priority.LOW: "-", Priority.MEDIUM: "*", Priority.HIGH: "!"}[self]

    @classmethod
    def parse(cls, value: Any) -> "Priority":
        """Coerce *value* into a :class:`Priority`.

        Accepts a Priority, an int (1-3) or a friendly string ("high", "h",
        "3" ...).  Anything else raises :class:`ValidationError` so the CLI can
        print a helpful message instead of crashing.
        """
        if isinstance(value, cls):
            return value
        # bool is an int subclass; reject it explicitly instead of mapping
        # True -> LOW, which would be a confusing silent success.
        if isinstance(value, bool):
            raise ValidationError(f"Invalid priority {value!r}. Use low, medium or high.")
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError:
                raise ValidationError(
                    f"Invalid priority {value!r}. Use 1 (low), 2 (medium) or 3 (high)."
                ) from None
        if isinstance(value, str):
            token = value.strip().lower()
            aliases = {
                "l": "low", "low": "low", "1": "low",
                "m": "medium", "med": "medium", "medium": "medium", "2": "medium",
                "h": "high", "hi": "high", "high": "high", "3": "high",
            }
            if token in aliases:
                return cls[aliases[token].upper()]
        raise ValidationError(f"Invalid priority {value!r}. Use low, medium or high.")


class Status(str, Enum):
    """Lifecycle state of a task."""

    PENDING = "pending"
    COMPLETED = "completed"

    @classmethod
    def parse(cls, value: Any) -> "Status":
        """Coerce *value* into a :class:`Status` (tolerant of case/spacing)."""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            token = value.strip().lower()
            if token in ("pending", "open", "todo", "active"):
                return cls.PENDING
            if token in ("completed", "complete", "done", "closed"):
                return cls.COMPLETED
        raise ValidationError(f"Invalid status {value!r}. Use pending or completed.")


def _parse_due_date(value: Any) -> str | None:
    """Validate an optional ISO date (``YYYY-MM-DD``) and return it normalised."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            raise ValidationError(
                f"Invalid due date {value!r}. Use the ISO format YYYY-MM-DD."
            ) from None
    raise ValidationError(f"Invalid due date {value!r}. Use the ISO format YYYY-MM-DD.")


@dataclass
class Task:
    """A single to-do item.

    The dataclass gives us ``__init__``/``__repr__``/``__eq__`` for free, which
    keeps the model short and makes it trivial to assert on inside tests.
    """

    task_id: int
    title: str
    description: str = ""
    priority: Priority = Priority.MEDIUM
    status: Status = Status.PENDING
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    due_date: str | None = None

    def __post_init__(self) -> None:
        """Normalise loose input, then validate the invariants.

        Normalisation lives here (not in the CLI) so that *every* entry point -
        CLI arguments, JSON records, or future API calls - gets identical
        behaviour.
        """
        self.priority = Priority.parse(self.priority)
        self.status = Status.parse(self.status)
        self.title = (self.title or "").strip()
        self.description = (self.description or "").strip()
        self.due_date = _parse_due_date(self.due_date)
        self.validate()

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate(self) -> None:
        """Raise :class:`ValidationError` if the task breaks a domain rule."""
        if not isinstance(self.task_id, int) or isinstance(self.task_id, bool):
            raise ValidationError("Task id must be a positive integer.")
        if self.task_id <= 0:
            raise ValidationError("Task id must be a positive integer.")
        if not self.title:
            raise ValidationError("Title must not be empty.")
        if len(self.title) > MAX_TITLE_LENGTH:
            raise ValidationError(
                f"Title is too long ({len(self.title)} characters); "
                f"the maximum is {MAX_TITLE_LENGTH}."
            )
        if len(self.description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"Description is too long ({len(self.description)} characters); "
                f"the maximum is {MAX_DESCRIPTION_LENGTH}."
            )
        # Self-healing invariant: a completed task always carries a completion
        # timestamp.  This keeps old/hand-edited data files consistent instead
        # of blowing up on load.
        if self.status is Status.COMPLETED and not self.completed_at:
            self.completed_at = self.updated_at

    # ------------------------------------------------------------------ #
    # Convenience helpers
    # ------------------------------------------------------------------ #
    @property
    def is_completed(self) -> bool:
        return self.status is Status.COMPLETED

    def days_until_due(self, today: date | None = None) -> int | None:
        """Days left before the due date (negative = overdue, None = no due date)."""
        if not self.due_date:
            return None
        reference = today or date.today()
        return (date.fromisoformat(self.due_date) - reference).days

    def is_overdue(self, today: date | None = None) -> bool:
        """True when the task is still pending and its due date has passed."""
        remaining = self.days_until_due(today)
        return remaining is not None and remaining < 0 and not self.is_completed

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation (enums become strings)."""
        return {
            "id": self.task_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority.label,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "due_date": self.due_date,
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> "Task":
        """Build a Task from a stored record.

        Design choice: parsing is *tolerant* about unknown/missing keys so that
        a data file written by a newer or older version of the app still loads.
        Values that are actively wrong (e.g. a blank title) still raise, and the
        service layer decides to skip that single record rather than crash.
        """
        if not isinstance(record, Mapping):
            raise ValidationError("Task record must be a mapping of field -> value.")
        try:
            task_id = record["id"]
        except KeyError:
            raise ValidationError("Task record is missing the required 'id' field.") from None
        return cls(
            task_id=task_id,
            title=record.get("title", ""),
            description=record.get("description", ""),
            priority=record.get("priority", Priority.MEDIUM),
            status=record.get("status", Status.PENDING),
            created_at=record.get("created_at") or utc_now(),
            updated_at=record.get("updated_at") or utc_now(),
            completed_at=record.get("completed_at") or None,
            due_date=record.get("due_date") or None,
        )

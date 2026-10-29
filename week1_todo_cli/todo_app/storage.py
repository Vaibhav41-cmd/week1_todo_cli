"""Persistence layer.

Two interchangeable implementations share the small :class:`Storage` protocol:

* :class:`JsonFileStorage` - the real thing, one JSON file on disk.
* :class:`InMemoryStorage` - a dictionary, used by the unit tests so they never
  touch the filesystem.

Design choices
--------------
1. **One JSON document** (schema version + ``next_id`` + ``tasks``) keeps the
   whole state atomic and human readable - perfect for a single-user CLI.
2. **Atomic writes**: data is written to a temporary file in the same folder and
   then ``os.replace``d over the original.  On POSIX that is atomic, so a crash
   or power loss can never leave a half-written to-do list.
3. **Corruption is never destructive**: an unreadable file is renamed to
   ``tasks.json.corrupt-<timestamp>`` and a :class:`CorruptStorageError` is
   raised, so the user keeps the evidence and can recover their tasks.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .errors import CorruptStorageError, StorageError

SCHEMA_VERSION = 1


def _empty_payload() -> dict[str, Any]:
    """Return a fresh, valid data document."""
    return {"schema_version": SCHEMA_VERSION, "next_id": 1, "tasks": []}


class Storage(Protocol):
    """Minimal contract every storage backend must satisfy."""

    def load(self) -> dict[str, Any]:
        """Return the stored document (never raises for a missing file)."""

    def save(self, payload: dict[str, Any]) -> None:
        """Persist the document."""


class JsonFileStorage:
    """JSON-file backed storage with atomic writes and corruption recovery."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #
    def load(self) -> dict[str, Any]:
        """Read the data file.

        * Missing file  -> empty document (first run is not an error).
        * Empty file    -> empty document (a crash mid-write is survivable).
        * Invalid JSON  -> rename to ``*.corrupt-<timestamp>`` and raise
          :class:`CorruptStorageError` with the backup location.
        """
        if not self.path.exists():
            return _empty_payload()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:  # permissions, is-a-directory, ...
            raise StorageError(f"Could not read {self.path}: {exc}") from exc

        if not raw.strip():
            return _empty_payload()

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            backup = self._quarantine()
            raise CorruptStorageError(
                f"'{self.path}' is not valid JSON ({exc.msg} at line {exc.lineno}). "
                f"The file was moved to '{backup}' and a new list was started.",
                backup_path=str(backup),
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("tasks", []), list):
            backup = self._quarantine()
            raise CorruptStorageError(
                f"'{self.path}' does not contain a valid task document. "
                f"The file was moved to '{backup}' and a new list was started.",
                backup_path=str(backup),
            )

        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("next_id", 1)
        payload.setdefault("tasks", [])
        return payload

    def _quarantine(self) -> Path:
        """Move the damaged file aside and return its new path."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        try:
            shutil.move(str(self.path), str(backup))
        except OSError as exc:
            raise StorageError(f"Could not back up the damaged file: {exc}") from exc
        return backup

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #
    def save(self, payload: dict[str, Any]) -> None:
        """Atomically write *payload* to disk.

        The temporary file is created in the destination directory so that
        ``os.replace`` stays on the same filesystem (and therefore atomic).
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, ensure_ascii=False)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, self.path)
            except BaseException:
                # Never leave a stray temp file behind on failure.
                Path(tmp_name).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise StorageError(f"Could not save to {self.path}: {exc}") from exc


class InMemoryStorage:
    """Non-persistent storage used by tests and for ``--dry-run`` style demos."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload if payload is not None else _empty_payload()

    def load(self) -> dict[str, Any]:
        # A deep copy keeps callers from mutating internal state by accident.
        return json.loads(json.dumps(self.payload))

    def save(self, payload: dict[str, Any]) -> None:
        self.payload = json.loads(json.dumps(payload))

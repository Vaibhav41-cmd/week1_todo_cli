"""Automated tests for the To-Do List Manager (Week 1).

Run with either runner from the project root:

    python -m pytest -q
    python -m unittest discover -s tests -v

``unittest``-style test cases are used so the suite runs on a completely stock
Python installation with zero third-party dependencies, while remaining fully
compatible with pytest.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from todo_app.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from todo_app.errors import TaskNotFoundError, ValidationError
from todo_app.models import Priority, Status, Task
from todo_app.service import TodoService
from todo_app.storage import InMemoryStorage, JsonFileStorage

FIXED_TODAY = date(2026, 11, 1)


def make_service() -> TodoService:
    """Service on in-memory storage with a pinned date (deterministic tests)."""
    return TodoService(InMemoryStorage(), today=FIXED_TODAY)


class TaskModelTests(unittest.TestCase):
    """Domain rules: validation, priority parsing, serialisation."""

    def test_priority_parse_accepts_names_aliases_and_numbers(self) -> None:
        self.assertIs(Priority.parse("high"), Priority.HIGH)
        self.assertIs(Priority.parse("H"), Priority.HIGH)
        self.assertIs(Priority.parse("3"), Priority.HIGH)
        self.assertIs(Priority.parse(2), Priority.MEDIUM)
        self.assertIs(Priority.parse(" LOW "), Priority.LOW)
        self.assertIs(Priority.parse(Priority.LOW), Priority.LOW)

    def test_priority_parse_rejects_unknown_values(self) -> None:
        for bad in ("urgent", "", 0, 4, True, None, 1.5):
            with self.assertRaises(ValidationError):
                Priority.parse(bad)  # type: ignore[arg-type]

    def test_status_parse_is_tolerant(self) -> None:
        self.assertIs(Status.parse("done"), Status.COMPLETED)
        self.assertIs(Status.parse("PENDING"), Status.PENDING)
        with self.assertRaises(ValidationError):
            Status.parse("blocked")

    def test_empty_title_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Task(task_id=1, title="   ")
        with self.assertRaises(ValidationError):
            Task(task_id=1, title="")

    def test_oversized_title_and_description_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Task(task_id=1, title="x" * 121)
        with self.assertRaises(ValidationError):
            Task(task_id=1, title="ok", description="x" * 1001)

    def test_invalid_task_id_and_due_date_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Task(task_id=0, title="ok")
        with self.assertRaises(ValidationError):
            Task(task_id=-1, title="ok")
        with self.assertRaises(ValidationError):
            Task(task_id=1, title="ok", due_date="32/13/2026")
        with self.assertRaises(ValidationError):
            Task(task_id=1, title="ok", due_date="not-a-date")

    def test_to_dict_from_dict_round_trip(self) -> None:
        original = Task(
            task_id=7,
            title="Write report",
            description="Sections 1-3",
            priority="high",
            due_date="2026-11-05",
        )
        record = original.to_dict()
        self.assertEqual(record["priority"], "high")
        self.assertEqual(record["status"], "pending")
        restored = Task.from_dict(record)
        self.assertEqual(restored, original)

    def test_from_dict_tolerates_missing_optional_keys(self) -> None:
        restored = Task.from_dict({"id": 3, "title": "Minimal record"})
        self.assertEqual(restored.description, "")
        self.assertIs(restored.priority, Priority.MEDIUM)
        self.assertIsNone(restored.due_date)

    def test_overdue_logic(self) -> None:
        task = Task(task_id=1, title="Late", due_date="2026-10-30")
        self.assertTrue(task.is_overdue(today=FIXED_TODAY))
        self.assertEqual(task.days_until_due(today=FIXED_TODAY), -2)
        task.status = Status.COMPLETED
        self.assertFalse(task.is_overdue(today=FIXED_TODAY))  # done is never overdue
        self.assertIsNone(Task(task_id=2, title="Undated").days_until_due(today=FIXED_TODAY))


class ServiceTests(unittest.TestCase):
    """Business rules: CRUD, id allocation, filtering, sorting, statistics."""

    def test_add_assigns_sequential_never_reused_ids(self) -> None:
        service = make_service()
        first = service.add("First")
        second = service.add("Second")
        service.remove(second.task_id)
        third = service.add("Third")
        self.assertEqual((first.task_id, second.task_id, third.task_id), (1, 2, 3))

    def test_complete_and_reopen_manage_timestamps(self) -> None:
        service = make_service()
        task = service.add("Toggle me")
        self.assertIsNone(task.completed_at)
        completed = service.complete(task.task_id)
        self.assertTrue(completed.is_completed)
        self.assertIsNotNone(completed.completed_at)
        reopened = service.reopen(task.task_id)
        self.assertFalse(reopened.is_completed)
        self.assertIsNone(reopened.completed_at)

    def test_complete_is_idempotent(self) -> None:
        service = make_service()
        task = service.add("Once")
        service.complete(task.task_id)
        stamp = service.get(task.task_id).completed_at
        service.complete(task.task_id)  # must not raise or change the stamp
        self.assertEqual(service.get(task.task_id).completed_at, stamp)

    def test_missing_task_raises_not_found(self) -> None:
        service = make_service()
        for operation in (service.get, service.complete, service.remove, service.reopen):
            with self.assertRaises(TaskNotFoundError):
                operation(99)

    def test_update_and_clear_due_date(self) -> None:
        service = make_service()
        task = service.add("Old title", due_date="2026-11-05")
        updated = service.update(task.task_id, title="New title", priority="low")
        self.assertEqual(updated.title, "New title")
        self.assertIs(updated.priority, Priority.LOW)
        self.assertEqual(updated.due_date, "2026-11-05")
        cleared = service.update(task.task_id, clear_due_date=True)
        self.assertIsNone(cleared.due_date)
        with self.assertRaises(ValidationError):
            service.update(task.task_id, title="   ")

    def test_list_filters_by_status_and_priority(self) -> None:
        service = make_service()
        service.add("A", priority="high")
        service.add("B", priority="low")
        service.add("C", priority="high")
        service.complete(3)
        self.assertEqual([t.title for t in service.list_tasks(status="pending")], ["A", "B"])
        self.assertEqual([t.title for t in service.list_tasks(priority="high")], ["A", "C"])
        self.assertEqual(len(service.list_tasks(status="completed")), 1)

    def test_sorting_orders(self) -> None:
        service = make_service()
        service.add("Zebra", priority="low", due_date="2026-11-10")
        service.add("apple", priority="high")
        service.add("Mango", priority="medium", due_date="2026-11-03")
        self.assertEqual(
            [t.title for t in service.list_tasks(sort="priority")],
            ["apple", "Mango", "Zebra"],
        )
        self.assertEqual(
            [t.title for t in service.list_tasks(sort="due")],
            ["Mango", "Zebra", "apple"],  # undated tasks sort last
        )
        self.assertEqual(
            [t.title for t in service.list_tasks(sort="title")],
            ["apple", "Mango", "Zebra"],  # case-insensitive
        )
        with self.assertRaises(ValidationError):
            service.list_tasks(sort="bogus")

    def test_search_is_case_insensitive_and_covers_notes(self) -> None:
        service = make_service()
        service.add("Buy milk")
        service.add("Call bank", description="Ask about MILK shares")
        self.assertEqual(len(service.search("milk")), 2)
        self.assertEqual(service.search("zzz"), [])
        with self.assertRaises(ValidationError):
            service.search("   ")

    def test_stats_and_clear_completed(self) -> None:
        service = make_service()
        service.add("Late one", priority="high", due_date="2026-10-20")
        service.add("Done one", priority="low")
        service.add("Open one")
        service.complete(2)
        stats = service.stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["pending"], 2)
        self.assertEqual(stats["completed"], 1)
        self.assertEqual(stats["overdue"], 1)
        self.assertAlmostEqual(stats["completion_rate"], 33.3)
        self.assertEqual(service.clear_completed(), 1)
        self.assertEqual(len(service), 2)

    def test_bad_records_are_skipped_but_good_ones_survive(self) -> None:
        payload = {
            "schema_version": 1,
            "next_id": 3,
            "tasks": [
                {"id": 1, "title": "Good record"},
                {"id": 2, "title": "   "},  # invalid -> skipped
            ],
        }
        service = TodoService(InMemoryStorage(payload), today=FIXED_TODAY)
        self.assertEqual(len(service), 1)
        self.assertTrue(any("Skipped 1 invalid" in w for w in service.warnings))


class StorageTests(unittest.TestCase):
    """Persistence: round-trips, atomic writes, corruption recovery."""

    def test_missing_file_is_an_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            storage = JsonFileStorage(Path(folder) / "tasks.json")
            self.assertEqual(storage.load(), {"schema_version": 1, "next_id": 1, "tasks": []})

    def test_round_trip_preserves_data_and_next_id(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.json"
            service = TodoService(JsonFileStorage(path), today=FIXED_TODAY)
            service.add("Persist me", priority="high")
            service.complete(1)
            reloaded = TodoService(JsonFileStorage(path), today=FIXED_TODAY)
            self.assertEqual(len(reloaded), 1)
            self.assertTrue(reloaded.get(1).is_completed)
            self.assertEqual(reloaded.add("Next").task_id, 2)

    def test_corrupt_file_is_quarantined_not_destroyed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.json"
            path.write_text("%%% not json %%%", encoding="utf-8")
            service = TodoService(JsonFileStorage(path), today=FIXED_TODAY)
            self.assertEqual(len(service), 0)
            backups = list(Path(folder).glob("tasks.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertIn("not json", backups[0].read_text(encoding="utf-8"))
            self.assertTrue(service.warnings)  # the user is told what happened
            service.add("Fresh start")  # and the app keeps working
            self.assertTrue(path.exists())

    def test_no_temp_files_are_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.json"
            service = TodoService(JsonFileStorage(path), today=FIXED_TODAY)
            for index in range(5):
                service.add(f"Task {index}")
            leftovers = [p for p in Path(folder).iterdir() if p.suffix == ".tmp"]
            self.assertEqual(leftovers, [])
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["tasks"]), 5)


class CliTests(unittest.TestCase):
    """End-to-end: drive the real CLI and assert on output and exit codes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_file = str(Path(self._tmp.name) / "cli_tasks.json")
        self._previous_env = os.environ.get("TODO_FILE")
        os.environ["TODO_FILE"] = self.data_file

    def tearDown(self) -> None:
        if self._previous_env is None:
            os.environ.pop("TODO_FILE", None)
        else:
            os.environ["TODO_FILE"] = self._previous_env
        self._tmp.cleanup()

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        """Run ``main`` capturing stdout, stderr and the exit code."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_full_workflow(self) -> None:
        code, out, _ = self.run_cli("add", "Buy milk", "-p", "high", "--due", "2026-11-02")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Added task 1", out)

        code, out, _ = self.run_cli("add", "Write tests")
        self.assertEqual(code, EXIT_OK)

        code, out, _ = self.run_cli("list", "-a")
        self.assertIn("Buy milk", out)
        self.assertIn("Write tests", out)

        code, out, _ = self.run_cli("done", "1")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Completed task 1", out)

        code, out, _ = self.run_cli("list")  # pending only by default
        self.assertNotIn("Buy milk", out)
        self.assertIn("Write tests", out)

        code, out, _ = self.run_cli("stats")
        self.assertIn("Total tasks", out)

        code, out, _ = self.run_cli("search", "tests")
        self.assertIn("Write tests", out)

        code, out, _ = self.run_cli("undo", "1")
        self.assertIn("Re-opened task 1", out)

        code, out, _ = self.run_cli("edit", "2", "--priority", "high")
        self.assertIn("Updated task 2", out)

        code, out, _ = self.run_cli("rm", "2", "-y")
        self.assertIn("Deleted task 2", out)

    def test_json_output_is_machine_readable(self) -> None:
        self.run_cli("add", "JSON task")
        code, out, _ = self.run_cli("list", "-a", "--json")
        self.assertEqual(code, EXIT_OK)
        records = json.loads(out)
        self.assertEqual(records[0]["title"], "JSON task")

    def test_export_writes_csv_and_json(self) -> None:
        self.run_cli("add", "Exportable")
        csv_path = Path(self._tmp.name) / "out.csv"
        code, _, _ = self.run_cli("export", str(csv_path))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Exportable", csv_path.read_text(encoding="utf-8"))

        json_path = Path(self._tmp.name) / "out.json"
        code, _, _ = self.run_cli("export", str(json_path))
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["tasks"][0]["id"], 1)

    def test_unknown_id_gives_clean_error_and_exit_code_1(self) -> None:
        code, _, stderr = self.run_cli("done", "99")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("No task with id 99.", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_blank_title_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            code, _, _ = self.run_cli("add", "   ")
            self.assertIsNotNone(code)
        self.assertEqual(caught.exception.code, EXIT_USAGE)

    def test_invalid_due_date_gives_clean_error(self) -> None:
        code, _, stderr = self.run_cli("add", "Bad date", "--due", "2026-13-40")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("Invalid due date", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_edit_without_changes_is_reported(self) -> None:
        self.run_cli("add", "Unchanged")
        code, _, stderr = self.run_cli("edit", "1")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("Nothing to update", stderr)

    def test_remove_refuses_to_delete_when_not_confirmed(self) -> None:
        """Piped stdin (not a terminal) must never delete silently."""
        self.run_cli("add", "Safe task")
        code, out, _ = self.run_cli("rm", "1")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Nothing deleted", out)
        _, out, _ = self.run_cli("list", "-a")
        self.assertIn("Safe task", out)


if __name__ == "__main__":
    unittest.main()

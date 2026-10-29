"""Command-line interface for the To-Do List Manager.

This module is the *presentation* layer.  It must not contain business rules:
it parses arguments, calls :class:`~todo_app.service.TodoService` and renders
the result.  Anything that can fail is funnelled through the
:class:`~todo_app.errors.TodoError` hierarchy and converted into a friendly
message plus a meaningful exit code by :func:`main`.

Exit codes
----------
0   success
1   recoverable error (validation, unknown id, storage problem)
2   usage error (argparse: unknown command/flag, missing argument)
130 interrupted by the user (Ctrl-C)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__
from .errors import StorageError, TodoError, ValidationError
from .models import MAX_TITLE_LENGTH, Priority, Status, Task
from .service import SORT_KEYS, TodoService
from .storage import JsonFileStorage

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPT = 130

PROG = "todo"


# --------------------------------------------------------------------------- #
# Terminal colours
# --------------------------------------------------------------------------- #
class Palette:
    """Tiny ANSI helper.

    Colours are opt-out: they are disabled automatically when stdout is not a
    terminal (so piped/redirected output and CI logs stay clean), when
    ``--no-color`` is passed, or when the ``NO_COLOR`` environment variable is
    set (https://no-color.org).
    """

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    STRIKE = "\033[9m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        """Wrap *text* in the given ANSI codes (no-op when disabled)."""
        if not self.enabled or not codes:
            return text
        return f"{''.join(codes)}{text}{self.RESET}"


def _supports_colour(stream: Any) -> bool:
    """Best-effort check for an interactive, colour-capable stream."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def _format_due(task: Task, today: date) -> str:
    """Render the due date with a short human hint (``in 3d`` / ``2d ago``)."""
    if not task.due_date:
        return ""
    remaining = task.days_until_due(today)
    if remaining is None:
        return task.due_date
    if task.is_completed:
        return task.due_date
    if remaining < 0:
        return f"{task.due_date} ({abs(remaining)}d late)"
    if remaining == 0:
        return f"{task.due_date} (today)"
    if remaining <= 7:
        return f"{task.due_date} (in {remaining}d)"
    return task.due_date


def render_task_table(tasks: Sequence[Task], palette: Palette, today: date | None = None) -> str:
    """Render tasks as an aligned table.

    Column widths are computed from the data, so long titles and three-digit
    ids still line up instead of breaking the layout.
    """
    if not tasks:
        return "No tasks to show."

    reference = today or date.today()
    headers = ("ID", "P", "STATUS", "DUE", "TITLE")
    rows: list[tuple[str, ...]] = []
    for task in tasks:
        rows.append(
            (
                str(task.task_id),
                task.priority.marker,
                task.status.value,
                _format_due(task, reference),
                task.title,
            )
        )

    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(5)]

    def fmt(row: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()

    lines = [palette(fmt(headers), Palette.BOLD)]
    lines.append(palette("-" * (sum(widths) + 8), Palette.DIM))
    for task, row in zip(tasks, rows):
        line = fmt(row)
        if task.is_completed:
            lines.append(palette(line, Palette.DIM, Palette.STRIKE))
        elif task.is_overdue(reference):
            lines.append(palette(line, Palette.RED))
        elif task.priority is Priority.HIGH:
            lines.append(palette(line, Palette.YELLOW))
        else:
            lines.append(line)
    return "\n".join(lines)


def render_task_detail(task: Task, palette: Palette) -> str:
    """Multi-line view used after add/edit/complete."""
    lines = [
        f"{palette('ID:', Palette.BOLD)}       {task.task_id}",
        f"{palette('Title:', Palette.BOLD)}    {task.title}",
        f"{palette('Status:', Palette.BOLD)}   {task.status.value}",
        f"{palette('Priority:', Palette.BOLD)} {task.priority.label}",
    ]
    if task.description:
        lines.append(f"{palette('Notes:', Palette.BOLD)}    {task.description}")
    if task.due_date:
        lines.append(f"{palette('Due:', Palette.BOLD)}      {_format_due(task, date.today())}")
    lines.append(f"{palette('Created:', Palette.BOLD)}  {task.created_at}")
    if task.completed_at:
        lines.append(f"{palette('Done at:', Palette.BOLD)}  {task.completed_at}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Small I/O helpers (kept injectable-friendly for tests)
# --------------------------------------------------------------------------- #
def _fail(message: str) -> str:
    return f"Error: {message}"


def _confirm(prompt: str, assume_yes: bool = False) -> bool:
    """Ask a yes/no question.

    When ``--yes`` is passed, or stdin is not interactive (piped input), the
    answer defaults to "no" so an unattended script can never delete data.
    """
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
def cmd_add(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    task = service.add(
        title=args.title,
        description=args.description or "",
        priority=args.priority,
        due_date=args.due,
    )
    print(f"Added task {task.task_id}: {task.title}")
    print(render_task_detail(task, palette))
    return EXIT_OK


def cmd_list(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    status: Status | None
    if args.all:
        status = None
    elif args.status == "all":
        status = None
    else:
        status = Status.parse(args.status)

    tasks = service.list_tasks(status=status, priority=args.priority, sort=args.sort)

    if args.json:
        print(json.dumps([task.to_dict() for task in tasks], indent=2, ensure_ascii=False))
        return EXIT_OK

    print(render_task_table(tasks, palette))
    if tasks:
        done = sum(1 for task in tasks if task.is_completed)
        print(
            palette(
                f"\n{len(tasks)} task(s): {len(tasks) - done} pending, {done} completed",
                Palette.DIM,
            )
        )
    return EXIT_OK


def cmd_done(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    for raw in args.ids:
        task = service.complete(raw)
        print(palette(f"Completed task {task.task_id}: {task.title}", Palette.GREEN))
    return EXIT_OK


def cmd_undo(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    for raw in args.ids:
        task = service.reopen(raw)
        print(palette(f"Re-opened task {task.task_id}: {task.title}", Palette.BLUE))
    return EXIT_OK


def cmd_remove(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    # Validate every id *before* touching anything, so a typo in the second id
    # cannot leave the user with a half-applied multi-delete.
    tasks = [service.get(raw) for raw in args.ids]

    if not args.yes:
        titles = ", ".join(f"#{task.task_id} {task.title!r}" for task in tasks)
        if not _confirm(f"Delete {titles}?", assume_yes=False):
            print("Nothing deleted. (Use --yes to skip this prompt.)")
            return EXIT_OK

    for task in tasks:
        service.remove(task.task_id)
        print(palette(f"Deleted task {task.task_id}: {task.title}", Palette.RED))
    return EXIT_OK


def cmd_edit(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    if (
        args.title is None
        and args.description is None
        and args.priority is None
        and args.due is None
        and not args.clear_due
    ):
        raise ValidationError(
            "Nothing to update. Pass at least one of --title, --description, "
            "--priority, --due or --clear-due."
        )
    task = service.update(
        args.id,
        title=args.title,
        description=args.description,
        priority=args.priority,
        due_date=args.due,
        clear_due_date=args.clear_due,
    )
    print(f"Updated task {task.task_id}.")
    print(render_task_detail(task, palette))
    return EXIT_OK


def cmd_search(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    tasks = service.search(args.keyword)
    if not tasks:
        print(f"No task matches {args.keyword!r}.")
        return EXIT_OK
    print(render_task_table(tasks, palette))
    return EXIT_OK


def cmd_clear(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    completed = service.list_tasks(status=Status.COMPLETED)
    if not completed:
        print("No completed tasks to clear.")
        return EXIT_OK
    if not _confirm(f"Delete {len(completed)} completed task(s)?", assume_yes=args.yes):
        print("Nothing deleted. (Use --yes to skip this prompt.)")
        return EXIT_OK
    removed = service.clear_completed()
    print(palette(f"Cleared {removed} completed task(s).", Palette.GREEN))
    return EXIT_OK


def cmd_stats(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    stats = service.stats()
    if args.json:
        print(json.dumps(stats, indent=2))
        return EXIT_OK
    print(palette("To-do list summary", Palette.BOLD))
    print(f"  Total tasks      : {stats['total']}")
    print(f"  Pending          : {stats['pending']}")
    print(f"  Completed        : {stats['completed']} ({stats['completion_rate']}%)")
    overdue = stats["overdue"]
    print(
        "  Overdue          : "
        + (palette(str(overdue), Palette.RED) if overdue else str(overdue))
    )
    print("  By priority      : " + ", ".join(
        f"{name}={count}" for name, count in stats["by_priority"].items()
    ))
    return EXIT_OK


def cmd_export(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    """Export tasks to JSON or CSV (chosen from the file extension)."""
    records = service.export_records()
    destination = Path(args.path).expanduser()
    fmt = args.format or ("csv" if destination.suffix.lower() == ".csv" else "json")

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            fieldnames = [
                "id", "title", "description", "priority", "status",
                "created_at", "updated_at", "completed_at", "due_date",
            ]
            with destination.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for record in records:
                    writer.writerow({key: record.get(key) or "" for key in fieldnames})
        else:
            destination.write_text(
                json.dumps({"tasks": records}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    except OSError as exc:
        # Wrap OS errors so they travel through the normal error path.
        raise StorageError(f"Could not write {destination}: {exc}") from exc

    print(palette(f"Exported {len(records)} task(s) to {destination} ({fmt}).", Palette.GREEN))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Interactive shell (menu mode)
# --------------------------------------------------------------------------- #
SHELL_MENU = """
\u2554\u2550\u2550\u2550 To-Do List Manager \u2550\u2550\u2550\u2557
 1) Add a task
 2) List tasks
 3) Complete a task
 4) Re-open a task
 5) Remove a task
 6) Search
 7) Statistics
 0) Quit
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d"""


def _ask(prompt: str, default: str = "") -> str:
    """Read one line of input from the user (empty input returns *default*)."""
    value = input(prompt).strip()
    return value or default


def cmd_shell(args: argparse.Namespace, service: TodoService, palette: Palette) -> int:
    """Menu-driven REPL for users who prefer not to memorise flags.

    Every action delegates to the same service methods as the one-shot
    commands, so the two entry points can never drift apart.
    """
    print(palette("Interactive mode - press Ctrl-C or choose 0 to quit.", Palette.DIM))
    while True:
        print(SHELL_MENU)
        choice = _ask("Choose an option: ")

        if choice in ("0", "q", "quit", "exit"):
            print("Bye!")
            return EXIT_OK
        if choice == "1":
            title = _ask(f"Title (max {MAX_TITLE_LENGTH} chars): ")
            if not title:
                print(_fail("a title is required."))
                continue
            description = _ask("Notes (optional): ")
            priority = _ask("Priority [low/medium/high] (medium): ", "medium")
            due = _ask("Due date YYYY-MM-DD (optional): ") or None
            task = service.add(title, description, priority, due)
            print(palette(f"Added task {task.task_id}.", Palette.GREEN))
        elif choice == "2":
            show = _ask("Show [all/pending/completed] (pending): ", "pending")
            status = None if show == "all" else show
            print(render_task_table(service.list_tasks(status=status, sort="id"), palette))
        elif choice == "3":
            task = service.complete(int(_ask("Task id: ")))
            print(palette(f"Completed #{task.task_id} {task.title}", Palette.GREEN))
        elif choice == "4":
            task = service.reopen(int(_ask("Task id: ")))
            print(palette(f"Re-opened #{task.task_id} {task.title}", Palette.BLUE))
        elif choice == "5":
            task_id = int(_ask("Task id: "))
            if _confirm(f"Delete task {task_id}?", assume_yes=False):
                service.remove(task_id)
                print(palette(f"Deleted #{task_id}.", Palette.RED))
            else:
                print("Nothing deleted.")
        elif choice == "6":
            keyword = _ask("Keyword: ")
            if keyword:
                print(render_task_table(service.search(keyword), palette))
        elif choice == "7":
            stats = service.stats()
            print(
                f"total={stats['total']} pending={stats['pending']} "
                f"completed={stats['completed']} overdue={stats['overdue']}"
            )
        else:
            print(_fail(f"{choice!r} is not a menu option. Choose 0-7."))


# --------------------------------------------------------------------------- #
# Argument parser
# --------------------------------------------------------------------------- #
def _positive_id(value: str) -> int:
    """argparse type validator for task ids (keeps usage errors at exit code 2)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("task ids start at 1")
    return number


def _non_empty(value: str) -> str:
    """argparse type validator that rejects blank titles/keywords."""
    if not value or not value.strip():
        raise argparse.ArgumentTypeError("value must not be empty")
    return value.strip()


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    """Options accepted both before and after the sub-command.

    ``argparse.SUPPRESS`` is used as the default so a value given *before* the
    sub-command is not overwritten by the sub-parser's default.
    """
    parser.add_argument(
        "-f", "--file", default=argparse.SUPPRESS, metavar="PATH",
        help="path to the JSON data file (default: $TODO_FILE or ~/.todo_cli/tasks.json)",
    )
    parser.add_argument(
        "--no-color", action="store_true", default=argparse.SUPPRESS,
        help="disable ANSI colours",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="show tracebacks for unexpected errors",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the full argument parser.

    A factory function (rather than a module-level parser) makes the CLI
    re-usable and testable: each test can build a pristine parser.
    """
    common = argparse.ArgumentParser(add_help=False)
    _add_common_options(common)

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="A simple, dependency-free To-Do List Manager.",
        epilog=(
            "examples:\n"
            f"  {PROG} add 'Buy milk' -p high --due 2026-11-02\n"
            f"  {PROG} list --sort priority\n"
            f"  {PROG} done 1\n"
            f"  {PROG} shell        # menu-driven mode"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common_options(parser)
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # --- add ---------------------------------------------------------- #
    p_add = subparsers.add_parser("add", parents=[common], help="add a new task")
    p_add.add_argument("title", type=_non_empty, help="short description of the task")
    p_add.add_argument("-d", "--description", default="", help="longer notes")
    p_add.add_argument(
        "-p", "--priority", default="medium", type=str.lower,
        choices=[p.label for p in Priority], help="low, medium or high (default: medium)",
    )
    p_add.add_argument("--due", default=None, metavar="YYYY-MM-DD", help="optional due date")
    p_add.set_defaults(handler=cmd_add)

    # --- list --------------------------------------------------------- #
    p_list = subparsers.add_parser("list", parents=[common], help="list tasks")
    p_list.add_argument(
        "-s", "--status", default="pending", choices=["pending", "completed", "all"],
        help="filter by status (default: pending)",
    )
    p_list.add_argument("-a", "--all", action="store_true", help="shortcut for --status all")
    p_list.add_argument(
        "-p", "--priority", default=None, type=str.lower,
        choices=[p.label for p in Priority], help="only show this priority",
    )
    p_list.add_argument(
        "--sort", default="id", choices=list(SORT_KEYS),
        help="sort order (default: id)",
    )
    p_list.add_argument("--json", action="store_true", help="machine-readable output")
    p_list.set_defaults(handler=cmd_list)

    # --- done / undo -------------------------------------------------- #
    p_done = subparsers.add_parser("done", parents=[common], help="mark task(s) completed")
    p_done.add_argument("ids", nargs="+", type=_positive_id, metavar="ID")
    p_done.set_defaults(handler=cmd_done)

    p_undo = subparsers.add_parser("undo", parents=[common], help="re-open task(s)")
    p_undo.add_argument("ids", nargs="+", type=_positive_id, metavar="ID")
    p_undo.set_defaults(handler=cmd_undo)

    # --- remove ------------------------------------------------------- #
    p_rm = subparsers.add_parser("rm", aliases=["remove"], parents=[common], help="delete task(s)")
    p_rm.add_argument("ids", nargs="+", type=_positive_id, metavar="ID")
    p_rm.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p_rm.set_defaults(handler=cmd_remove)

    # --- edit --------------------------------------------------------- #
    p_edit = subparsers.add_parser("edit", parents=[common], help="modify an existing task")
    p_edit.add_argument("id", type=_positive_id, metavar="ID")
    p_edit.add_argument("--title", default=None)
    p_edit.add_argument("-d", "--description", default=None)
    p_edit.add_argument(
        "-p", "--priority", default=None, type=str.lower,
        choices=[p.label for p in Priority],
    )
    p_edit.add_argument("--due", default=None, metavar="YYYY-MM-DD")
    p_edit.add_argument("--clear-due", action="store_true", help="remove the due date")
    p_edit.set_defaults(handler=cmd_edit)

    # --- search ------------------------------------------------------- #
    p_search = subparsers.add_parser("search", parents=[common], help="search titles and notes")
    p_search.add_argument("keyword", type=_non_empty)
    p_search.set_defaults(handler=cmd_search)

    # --- clear -------------------------------------------------------- #
    p_clear = subparsers.add_parser(
        "clear", parents=[common], help="delete all completed tasks"
    )
    p_clear.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p_clear.set_defaults(handler=cmd_clear)

    # --- stats -------------------------------------------------------- #
    p_stats = subparsers.add_parser("stats", parents=[common], help="show summary numbers")
    p_stats.add_argument("--json", action="store_true", help="machine-readable output")
    p_stats.set_defaults(handler=cmd_stats)

    # --- export ------------------------------------------------------- #
    p_export = subparsers.add_parser("export", parents=[common], help="export tasks to JSON/CSV")
    p_export.add_argument("path", help="destination file (.json or .csv)")
    p_export.add_argument(
        "-t", "--format", default=None, choices=["json", "csv"], help="override the format"
    )
    p_export.set_defaults(handler=cmd_export)

    # --- shell -------------------------------------------------------- #
    p_shell = subparsers.add_parser("shell", parents=[common], help="menu-driven interactive mode")
    p_shell.set_defaults(handler=cmd_shell)

    return parser


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def default_data_file() -> Path:
    """Resolve where tasks are stored.

    Precedence: ``--file`` > ``$TODO_FILE`` > ``~/.todo_cli/tasks.json``.
    Honouring an environment variable makes the tool easy to use in tests and
    keeps per-project lists possible.
    """
    env_path = os.environ.get("TODO_FILE")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".todo_cli" / "tasks.json"


def main(argv: Sequence[str] | None = None) -> int:
    """Program entry point.  Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # No sub-command: open the friendly shell on a terminal, otherwise help.
    if not getattr(args, "handler", None):
        if sys.stdin.isatty() and sys.stdout.isatty():
            args.handler = cmd_shell
        else:
            parser.print_help()
            return EXIT_OK

    palette = Palette(_supports_colour(sys.stdout) and not getattr(args, "no_color", False))
    data_file = Path(getattr(args, "file", None)).expanduser() if getattr(args, "file", None) \
        else default_data_file()

    try:
        service = TodoService(JsonFileStorage(data_file))
        # Non-fatal problems discovered while loading are surfaced as warnings.
        for warning in service.warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        return args.handler(args, service, palette)

    except TodoError as exc:
        # Expected, user-facing error: one clean line on stderr, exit code 1.
        print(_fail(str(exc)), file=sys.stderr)
        if getattr(args, "verbose", False):
            import traceback

            traceback.print_exc()
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_INTERRUPT
    except (EOFError, ValueError) as exc:
        # ValueError can still escape from int() in the interactive shell; treat
        # it as bad input rather than crashing with a traceback.
        print(_fail(str(exc) or "invalid input."), file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # pragma: no cover - defensive last resort
        # Unexpected bug: never dump a raw traceback unless --verbose was used.
        print(
            _fail(f"unexpected problem ({type(exc).__name__}: {exc}). Re-run with -v for details."),
            file=sys.stderr,
        )
        if getattr(args, "verbose", False):
            import traceback

            traceback.print_exc()
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

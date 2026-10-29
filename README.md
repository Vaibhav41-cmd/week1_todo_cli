To-Do List Manager — Week 1: Python CLI Application
Internship: Junior Python Developer Task: Week 1 — Python CLI Application Design and Implementation Deliverable: A self-contained, dependency-free command-line To-Do List Manager.

A small but production-shaped command-line application for managing a personal task list. It supports adding, listing, completing, re-opening, editing, removing, searching and exporting tasks, with priorities, due dates and persistent JSON storage. The whole project runs on a stock Python 3.9+ installation — no third-party packages are required.

1. Requirements
Requirement	Value
Python	3.9 or newer
Operating system	Windows, macOS or Linux
Third-party dependencies	None
Optional	pytest (only if you prefer it over unittest)
Check your version:

python --version
Installing dependencies
There are no runtime dependencies to install — the application uses only the Python standard library (argparse, dataclasses, json, pathlib, tempfile, csv, enum, unittest). A requirements.txt is included anyway for transparency; it contains only comments plus the optional test runner.

pip install -r requirements.txt   # optional - installs nothing required
2. Running the application
From the project root:

python run.py --help
Any of these entry points work identically:

python run.py list          # convenience launcher (recommended for graders)
python -m todo_app list     # package entry point
./run.py list               # on macOS/Linux (the file is executable)
On Windows use py run.py ... if python is not on your PATH.

Where the data lives
By default tasks are stored in ~/.todo_cli/tasks.json. Two overrides are supported (highest precedence first):

python run.py --file data/example_tasks.json list -a   # one-off override
TODO_FILE=/tmp/work.json python run.py list             # per-shell override
The folder and file are created automatically on the first write.

3. Quick start
# Add tasks (priority: low / medium / high, default medium)
python run.py add "Buy milk" -p high --due 2026-11-02 -d "2 litres, toned"
python run.py add "Write the Week 1 report"
python run.py add "Pay electricity bill" -p low --due 2026-10-25

# View tasks
python run.py list                  # pending tasks only
python run.py list -a               # everything
python run.py list -s completed     # only finished tasks
python run.py list --sort priority  # most important first
python run.py list -p high          # only high priority

# Update tasks
python run.py done 1                # mark completed (several ids allowed)
python run.py undo 1                # re-open a completed task
python run.py edit 2 --title "Write the final report" --due 2026-11-05
python run.py edit 2 --clear-due    # remove the due date again

# Organise
python run.py search milk           # searches titles AND notes
python run.py stats                 # totals, completion rate, overdue count
python run.py clear                 # delete every completed task (asks first)
python run.py rm 3 --yes            # delete task 3 without confirmation

# Share the data
python run.py export tasks.csv      # spreadsheet friendly
python run.py export tasks.json     # machine friendly
python run.py list -a --json        # JSON straight to stdout (pipe it!)

# Prefer menus to flags?
python run.py shell                 # menu-driven interactive mode
python run.py                       # same, when started on a terminal
Sample output
$ python run.py list -a
ID  P  STATUS   DUE                   TITLE
----------------------------------------------------------
1   !  pending  2026-11-02 (in 4d)    Buy milk
2   *  pending                        Write the Week 1 report
3   -  pending  2026-10-25 (4d late)  Pay electricity bill

3 task(s): 3 pending, 0 completed
P is the priority badge: ! high, * medium, - low. Overdue rows are highlighted red and completed rows are dimmed/struck through in a terminal (colours disappear automatically when the output is piped, or with --no-color / the NO_COLOR environment variable).

Command reference
Command	Purpose	Key options
add TITLE	Create a task	-d NOTES, -p {low,medium,high}, --due YYYY-MM-DD
list	Show tasks	-a/--all, -s {pending,completed,all}, -p, --sort {id,priority,created,due,title}, --json
done ID...	Mark completed	accepts several ids
undo ID...	Re-open a task	accepts several ids
rm ID...	Delete permanently	-y/--yes skips the confirmation prompt
edit ID	Change fields	--title, -d, -p, --due, --clear-due
search KEYWORD	Find tasks	case-insensitive, title + notes
clear	Delete all completed	-y/--yes
stats	Summary numbers	--json
export PATH	Write JSON or CSV	-t {json,csv} overrides the extension
shell	Menu-driven mode	—
global		-f/--file PATH, --no-color, -v/--verbose, --version
Exit codes (script-friendly)
Code	Meaning
0	Success
1	Recoverable error — invalid input, unknown id, storage problem
2	Usage error — unknown command/flag, missing argument
130	Interrupted with Ctrl-C
4. Project structure
week1_todo_cli/
├── run.py                  # 10-line launcher: `python run.py ...`
├── README.md               # this file
├── requirements.txt        # documents the (zero) runtime dependencies
├── SUBMISSION_DESCRIPTION.txt   # 200+ word text for the upload form
├── todo_app/               # the application package
│   ├── __init__.py         # package docstring + __version__
│   ├── __main__.py         # enables `python -m todo_app`
│   ├── errors.py           # TodoError exception hierarchy
│   ├── models.py           # Task, Priority, Status (domain layer)
│   ├── storage.py          # JSON persistence + in-memory backend
│   ├── service.py          # TodoService - all business rules
│   └── cli.py              # argparse UI, rendering, error handling
├── tests/
│   └── test_todo.py        # 31 unittest/pytest-compatible tests
├── docs/
│   ├── DESIGN.md           # architecture, flow diagrams, pseudo-code
│   └── MANUAL_TESTING.md   # step-by-step manual test scenarios
└── data/
    └── example_tasks.json  # sample data file for instant exploration
5. Design overview (summary)
The code is split into four layers so that each can be tested and replaced independently. Dependencies point inwards: the CLI depends on the service, the service depends on the models and on a storage protocol, never on JSON files directly.

┌────────────┐   argv    ┌──────────────┐  use-cases  ┌─────────────┐
│   cli.py   │ ────────▶ │  service.py  │ ──────────▶ │  models.py  │
│ (argparse, │ ◀──────── │ (business    │             │ Task /      │
│  colours)  │  output   │  rules)      │ ◀────────── │ Priority    │
└────────────┘           └──────┬───────┘             └─────────────┘
                                │ Storage protocol
                                ▼
                         ┌──────────────┐
                         │ storage.py   │  tasks.json (atomic writes)
                         └──────────────┘
Key decisions (full rationale in docs/DESIGN.md):

Standard library only. The deliverable must run on any standard Python environment; removing third-party dependencies removes an entire class of "works on my machine" failures.
One JSON document (schema_version, next_id, tasks[]) that a human can read, edit and diff.
Atomic writes — data goes to a temp file in the same directory and is os.replace()d over the original, so a crash can never half-write the list.
Corruption is never destructive — a damaged file is renamed to tasks.json.corrupt-<timestamp> and the app starts a fresh list with a clear warning instead of crashing.
IDs are monotonic and never reused, stored explicitly as next_id.
Layered validation — argparse catches malformed input at the border (exit code 2), the domain models enforce the rules that matter everywhere (exit code 1), and the storage layer protects the file.
A single exception hierarchy (TodoError) so the CLI renders every expected failure as one readable Error: ... line without a traceback.
6. Error handling philosophy
Situation	Behaviour
Empty title, bad date, unknown priority	Friendly Error: ..., exit code 1 or 2, no traceback
done 99 when id 99 does not exist	Error: No task with id 99., exit code 1
rm 1 without --yes in a script	Refuses to delete (safe default)
Data file missing	Treated as an empty list (first run)
Data file empty	Treated as an empty list
Data file corrupted	Moved aside to *.corrupt-<timestamp>, warning printed, fresh list
One bad record inside an otherwise good file	That record is skipped; the rest load; a warning reports it
Ctrl-C during any operation	Interrupted., exit code 130
Unexpected bug	One-line message; full traceback only with -v
Pass -v/--verbose when you want the traceback (for example while developing).

7. Testing
Automated
python -m unittest discover -s tests -v    # 31 tests, stdlib only
python -m pytest -q                        # same suite via pytest
The suite covers four areas: model validation (priority parsing, title limits, overdue logic, round-trips), service rules (CRUD, id allocation, filtering, sorting, search, statistics), storage (round-trip, atomic writes, corruption recovery, temp-file hygiene) and the CLI end-to-end (workflow, JSON output, exports, exit codes, confirmation safety).

Manual
A scripted walk-through with expected output for every step lives in docs/MANUAL_TESTING.md. Try it against the supplied sample data:

python run.py --file data/example_tasks.json list -a
cp data/example_tasks.json /tmp/mine.json && python run.py --file /tmp/mine.json shell
8. Limitations and future work
Single data file, single user: no file locking for concurrent writers.
No tags/recurrence yet — the model is structured so both can be added without touching the CLI (this is the natural growing point for Weeks 3–6).
Priorities are fixed to three levels by design, keeping the UI legible.
9. Author
Week 1 deliverable for the Junior Python Developer internship (14-Oct-2026 → 25-Nov-2026). See docs/DESIGN.md for the architecture notes, flow diagrams and pseudo-code requested by the task brief.

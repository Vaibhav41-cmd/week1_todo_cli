# Design Document — To-Do List Manager (Week 1)

This document records the planning step required by the Week 1 brief: the
architecture, the module and class outline, flow diagrams, pseudo-code and the
reasoning behind the major design decisions.

---

## 1. Goals and non-goals

**Goals**

1. A CLI to-do manager with the four core operations — add, view, complete,
   remove — plus the quality-of-life features real users expect (edit, search,
   priorities, due dates, export).
2. Clean, modular, well-commented code that a reviewer can read top to bottom.
3. Robust behaviour in the face of bad input and damaged data.
4. Runs anywhere a standard Python 3.9+ runs. Zero third-party dependencies.

**Non-goals** — multi-user sync, a GUI, a database server. The architecture
leaves room for all three without rewriting the business logic.

---

## 2. Architecture overview

Four layers, dependencies pointing inwards. Nothing in an inner layer imports
anything from an outer layer, so each layer can be replaced or tested on its
own.

```
                         ┌─────────────────────────────┐
                         │      PRESENTATION (cli.py)   │
                         │ argparse, colours, tables,   │
                         │ interactive shell, exit codes│
                         └──────────────┬───────────────┘
                                        │ calls use-cases
                         ┌──────────────▼───────────────┐
                         │      APPLICATION (service.py) │
                         │ TodoService: business rules,  │
                         │ id allocation, filter, sort,  │
                         │ statistics                    │
                         └───────┬──────────────┬────────┘
                uses            │              │  depends on a protocol
        ┌───────────────────────▼─┐   ┌────────▼───────────────────────┐
        │     DOMAIN (models.py)  │   │  INFRASTRUCTURE (storage.py)   │
        │ Task, Priority, Status  │   │ JsonFileStorage / InMemory     │
        │ validation, overdue     │   │ atomic writes, corruption      │
        │ rules                   │   │ recovery                       │
        └─────────────────────────┘   └────────────────────────────────┘
                    errors.py — shared exception hierarchy
```

### 2.1 Module and class outline

| Module | Responsibility | Public API |
|---|---|---|
| `todo_app/errors.py` | One hierarchy for every expected failure | `TodoError`, `ValidationError`, `StorageError`, `CorruptStorageError`, `TaskNotFoundError` |
| `todo_app/models.py` | Domain objects and their invariants | `Task` (dataclass), `Priority` (IntEnum), `Status` (str Enum), `utc_now()`, `MAX_TITLE_LENGTH` |
| `todo_app/storage.py` | Persistence behind a 2-method protocol | `Storage` (Protocol), `JsonFileStorage`, `InMemoryStorage`, `SCHEMA_VERSION` |
| `todo_app/service.py` | Use-cases / business rules | `TodoService` (`add`, `get`, `update`, `complete`, `reopen`, `remove`, `clear_completed`, `list_tasks`, `search`, `stats`, `export_records`) |
| `todo_app/cli.py` | Parsing, rendering, error translation | `build_parser()`, `main()`, `Palette`, `render_task_table()`, `render_task_detail()`, one `cmd_*` handler per sub-command |
| `todo_app/__main__.py` | `python -m todo_app` entry point | forwards to `cli.main` |
| `run.py` | Zero-install launcher | forwards `sys.argv` to `cli.main` |
| `tests/test_todo.py` | Verification | 31 tests in 4 classes (`TaskModelTests`, `ServiceTests`, `StorageTests`, `CliTests`) |

### 2.2 SOLID mapping

| Principle | Where it shows up |
|---|---|
| Single Responsibility | Each module owns exactly one concern (table above). The CLI never contains a business rule; the model never touches the disk. |
| Open/Closed | New storage backends (SQLite, REST) plug into the `Storage` protocol; new CLI commands are one parser entry + one `cmd_*` function. |
| Liskov Substitution | `InMemoryStorage` and `JsonFileStorage` are interchangeable — the tests rely on exactly this. |
| Interface Segregation | The protocol is two methods (`load`, `save`); no backend is forced to implement extras. |
| Dependency Inversion | `TodoService` depends on the `Storage` abstraction, injected through its constructor. |

---

## 3. Data design

One JSON document keeps the entire state in a single, atomic write:

```json
{
  "schema_version": 1,
  "next_id": 3,
  "tasks": [
    {
      "id": 1,
      "title": "Buy milk",
      "description": "2 litres, toned",
      "priority": "high",
      "status": "pending",
      "created_at": "2026-10-29T11:28:11+00:00",
      "updated_at": "2026-10-29T11:28:11+00:00",
      "completed_at": null,
      "due_date": "2026-11-02"
    }
  ]
}
```

* `schema_version` reserves a migration path for future weeks.
* `next_id` is stored explicitly so ids are monotonic and **never reused**,
  even after deletions, and even if the file is hand-edited (the loader clamps
  it to at least `max(id) + 1`).
* Enums are serialised as readable strings (`"high"`, `"pending"`); timestamps
  are ISO-8601 UTC strings, which sort lexicographically and stay unambiguous
  across timezones.
* Loaders are tolerant: unknown keys are ignored, missing optional keys get
  defaults, and a record that fails validation is skipped (and reported)
  instead of taking the whole list down with it.

---

## 4. Flow diagrams

### 4.1 Application flow

```
                 python run.py [args]
                          │
                          ▼
                 ┌─────────────────┐     invalid flags
                 │ argparse parses │ ───────────────▶ usage msg, exit 2
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐     damaged file
                 │  TodoService    │ ─┬─▶ quarantine *.corrupt-*
                 │  loads storage  │  └─▶ warning, start empty
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐     TodoError
                 │ cmd_* handler   │ ───────────────▶ "Error: ...", exit 1
                 │ runs use-case   │
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐     atomic: temp + os.replace
                 │  persist (if    │ ───────────────▶ tasks.json
                 │  state changed) │
                 └────────┬────────┘
                          ▼
                     render, exit 0
```

### 4.2 "Complete a task" flow

```
  todo done 3
      │
      ▼
  _positive_id("3") = 3        ──( "abc" / "0" )──▶ argparse error, exit 2
      │
      ▼
  service.complete(3)
      │
      ├─▶ id 3 not in tasks?  ──▶ TaskNotFoundError ──▶ "Error: No task...", exit 1
      │
      ├─▶ already completed?  ──▶ return task unchanged (idempotent)
      │
      └─▶ status=COMPLETED, completed_at=now, updated_at=now
              │
              ▼
          persist → "Completed task 3: ...", exit 0
```

### 4.3 Storage write flow (why a crash cannot corrupt the file)

```
save(payload)
   │
   ├─▶ mkdir parents (data directory)
   ├─▶ mkstemp in the SAME directory      .tasks.json.XXXXXX.tmp
   ├─▶ json.dump + flush + fsync          bytes on disk
   └─▶ os.replace(tmp, tasks.json)        atomic on POSIX & Windows
            │
            └─▶ on any exception: unlink tmp, raise StorageError
```

---

## 5. Pseudo-code

### 5.1 `TodoService.add`

```
function add(title, description, priority, due):
    now      ← utc_now()
    task     ← Task(id=next_id, title, description, priority, due, now, now)
               # Task.__post_init__ normalises + validates; raises ValidationError
    tasks[next_id] ← task
    next_id  ← task.id + 1
    persist()
    return task
```

### 5.2 `TodoService.list_tasks`

```
function list_tasks(status=NULL, priority=NULL, sort="id"):
    require sort ∈ {id, priority, created, due, title} else ValidationError
    result ← all tasks
    if status:   result ← [t in result | t.status = status]
    if priority: result ← [t in result | t.priority = priority]
    sort result by key(sort):   # priority: high first, then oldest id
                                # due: soonest first, undated LAST
    return result
```

### 5.3 `cli.main`

```
function main(argv):
    args ← build_parser().parse_args(argv)     # argparse exits 2 on bad usage
    if no handler:                              # bare `python run.py`
        handler ← shell if interactive else print help
    try:
        service ← TodoService(JsonFileStorage(resolve_data_file(args)))
        for warning in service.warnings: print to stderr
        return args.handler(args, service, palette)
    except TodoError as e:      print "Error: e" ;        return 1
    except KeyboardInterrupt:   print "Interrupted." ;  return 130
    except Exception as e:      print one-liner ;       return 1   # -v ⇒ traceback
```

### 5.4 Interactive shell

```
loop forever:
    print menu
    choice ← input()
    match choice:
        "1" → prompt title/notes/priority/due → service.add(...)
        "2" → prompt filter → render(service.list_tasks(...))
        "3"/"4" → prompt id → service.complete / reopen
        "5" → prompt id → confirm → service.remove
        "6" → prompt keyword → render(service.search(...))
        "7" → print service.stats()
        "0" → break (exit 0)
        other → "Error: ... is not a menu option"
# identical service calls to the one-shot commands ⇒ no duplicated logic
```

---

## 6. Error-handling strategy

Three defensive layers, each with a distinct exit code:

1. **Border validation (argparse, exit 2).** `type=` validators (`_positive_id`,
   `_non_empty`) and `choices=` reject malformed input before any state is
   loaded or touched.
2. **Domain validation (exit 1).** `Task.validate()` enforces the rules that
   must hold everywhere the model is used — non-blank titles, length limits,
   legal priorities and dates. Failures raise `ValidationError`, a subclass of
   `TodoError`, which `main()` renders as a single `Error: ...` line.
3. **Infrastructure protection (exit 1, recoverable).** Missing/empty files are
   first-run situations, not errors. Corrupt files are quarantined. Bad records
   inside a good file are skipped with a warning. OS errors become
   `StorageError` with the underlying message preserved.

Destructive operations are **safe by default**: `rm` and `clear` ask for
confirmation, and when stdin is not a terminal the answer defaults to *no*, so
a piped script can never delete data silently. `rm` also validates *every* id
before deleting *any*, preventing half-applied multi-deletes.

An unexpected exception is caught by a last-resort guard, reported in one line
(with `-v` revealing the full traceback for debugging), and converted to exit
code 1 instead of an interpreter crash.

---

## 7. Key decisions and trade-offs

| Decision | Alternatives considered | Why this one |
|---|---|---|
| Standard library only | `click`/`rich`/`typer` | Guarantees the deliverable runs on a stock Python; `argparse` + ANSI codes cover the brief. |
| Single JSON file | SQLite, CSV, one file per task | Human readable, diffable, zero schema code; atomic whole-document writes are simple. Right-sized for a single-user CLI. |
| Atomic temp-file writes | Direct `open(...).write()` | A crash mid-write then corrupts the only copy of the user's data. The cost is ~10 lines. |
| Corrupt file ⇒ quarantine + restart | Crash, or silently overwrite | Data loss is the worst failure mode for a to-do app; preserving bytes keeps recovery possible. |
| `IntEnum` for priority | plain strings / ints | Free ordering for "high → low" sorts, plus a `label` for storage and a `marker` for display. |
| Dataclass for `Task` | Hand-written class, TypedDict | `__eq__`/`__repr__` for free (great in tests), `__post_init__` is the single normalisation point. |
| Service layer between CLI and storage | Logic inside handlers | Business rules become testable without I/O; a second front-end (GUI/API) reuses everything. |
| Injecting `today` into the service | Calling `date.today()` inline | Date-dependent tests become deterministic instead of flaky. |
| One `TodoError` root | Catching `ValueError`/`OSError` everywhere | One `except` in `main()`; callers can still branch on subclasses when they need to. |
| `unittest`-style tests | pytest-only fixtures | Runs on stock Python *and* under pytest — the grader never needs to install anything. |

---

## 8. Testing strategy

| Layer | Test class | Examples |
|---|---|---|
| Domain | `TaskModelTests` (9) | priority parsing incl. `bool` rejection, length limits, `to_dict`/`from_dict` round-trip, overdue maths |
| Application | `ServiceTests` (7) | id monotonicity, idempotent complete, partial updates, filter/sort/search, statistics, bad-record skipping |
| Infrastructure | `StorageTests` (4) | missing file, round-trip + `next_id` persistence, corruption quarantine, no temp-file leakage |
| Presentation | `CliTests` (11 inc. workflow) | full add→done→undo→edit→rm journey, `--json` parseability, CSV/JSON export, exit codes 0/1/2, non-interactive delete safety |

31 tests, 0.05s runtime, deterministic (pinned `today = 2026-11-01`), and no
test touches the user's real data file (every test uses in-memory storage or a
fresh `TemporaryDirectory`).

---

## 9. How this design grows into later weeks

* **Week 2 (debugging):** the layered structure gives natural breakpoints —
  `Task.validate`, `TodoService._persist`, `JsonFileStorage.load`.
* **Week 3 (testing):** the suite can be extended with coverage, fixtures and
  property-based tests without restructuring.
* **Week 4 (performance):** `list_tasks`/`search` are single, profileable choke
  points; storage can become SQLite behind the same protocol.
* **Week 5 (security):** validation is centralised in the model — path
  traversal, injection and oversized-input hardening land in one place each.
* **Week 6 (CI):** the suite already runs headless with a meaningful exit code,
  which is all a GitHub Actions workflow needs.

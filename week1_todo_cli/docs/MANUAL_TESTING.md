# Manual Testing Guide — To-Do List Manager (Week 1)

Step-by-step scenarios an evaluator (or any user) can follow to verify every
feature by hand. Run everything from the project root. Using a throwaway data
file keeps your real list untouched:

```bash
export TODO_FILE=/tmp/manual_tasks.json     # Windows PowerShell: $env:TODO_FILE="C:\Temp\manual_tasks.json"
rm -f /tmp/manual_tasks.json
```

Each scenario lists the command, the expected behaviour and a pass/fail box.

---

## MT-01 — Help and version

```bash
python run.py --help
python run.py --version
python run.py add --help
```
*Expected:* usage text listing all sub-commands and global options;
`todo 1.0.0`; per-command help for `add`. Exit code `0`. ☐

## MT-02 — Add tasks

```bash
python run.py add "Buy milk" -p high --due 2026-11-02 -d "2 litres, toned"
python run.py add "Write the Week 1 report"
python run.py add "Pay electricity bill" -p low --due 2026-10-25
```
*Expected:* `Added task 1`, `Added task 2`, `Added task 3`, each followed by a
detail block (priority, notes and due date shown when provided). ☐

## MT-03 — List, filter and sort

```bash
python run.py list
python run.py list -a
python run.py list --sort priority
python run.py list --sort due
python run.py list -p high
```
*Expected:* an aligned table; undated tasks show a blank DUE column; priority
sort puts *Buy milk* first; date sort puts the bill first and the undated task
last; a footer like `3 task(s): 3 pending, 0 completed`. In a terminal the
overdue task is red. ☐

## MT-04 — Complete and re-open

```bash
python run.py done 1
python run.py list          # Buy milk gone (pending only)
python run.py list -s completed
python run.py undo 1
python run.py list          # Buy milk is back
```
*Expected:* `Completed task 1`, then `Re-opened task 1`; the completed row is
dimmed/struck through in `list -a`. ☐

## MT-05 — Edit

```bash
python run.py edit 2 --title "Write the final Week 1 report" --due 2026-11-05 -p high
python run.py edit 2 --clear-due
```
*Expected:* `Updated task 2` with the new title/date/priority; after the second
command the due date is blank again. ☐

## MT-06 — Search and statistics

```bash
python run.py search report
python run.py stats
python run.py stats --json
```
*Expected:* only the report task matches; statistics show totals, completion
percentage, overdue count ≥ 1 and the per-priority breakdown; `--json` prints
valid JSON. ☐

## MT-07 — Delete safely

```bash
python run.py rm 3          # answer "n"  -> "Nothing deleted."
python run.py rm 3          # answer "y"  -> "Deleted task 3: ..."
python run.py done 2
python run.py clear -y      # removes every completed task
```
*Expected:* the confirmation prompt protects the data; the second attempt
deletes; `clear` reports how many completed tasks were removed. ☐

## MT-08 — Export

```bash
python run.py export tasks.csv
python run.py export tasks.json
```
*Expected:* both files exist; the CSV opens cleanly in a spreadsheet with the
header row `id,title,description,priority,status,...`; the JSON contains a
`{"tasks": [...]}` document. ☐

## MT-09 — Interactive (menu) mode

```bash
python run.py shell
```
*Expected:* a numbered menu (1-7 and 0). Add a task with option 1, view with 2,
complete with 3, statistics with 7 and quit with 0 (`Bye!`). An invalid choice
such as `9` prints an error and re-displays the menu instead of crashing. ☐

## MT-10 — Input validation and error handling

```bash
python run.py add "   "                 # blank title
python run.py add "Bad date" --due 2026-13-40
python run.py add "Bad priority" -p urgent
python run.py done 99                   # unknown id
python run.py done abc                  # non-numeric id
python run.py frobnicate                # unknown command
echo $?                                 # after each command
```
*Expected:* short, readable error messages with **no Python traceback**
anywhere; exit codes `2` for usage problems (blank title, bad priority,
non-numeric id, unknown command) and `1` for the invalid date and unknown id.
Nothing new appears in `list -a`. ☐

## MT-11 — Persistence

```bash
python run.py list -a        # run in a brand-new terminal
cat "$TODO_FILE"             # read the raw JSON
```
*Expected:* all tasks are still there after closing the terminal; the file is
pretty-printed JSON with `schema_version`, `next_id` and `tasks`. ☐

## MT-12 — Corruption recovery

```bash
echo "this is not json" > /tmp/broken.json
python run.py --file /tmp/broken.json list -a
ls /tmp/broken.json*
```
*Expected:* a `Warning: ... is not valid JSON ... The file was moved to
'/tmp/broken.json.corrupt-<timestamp>' ...` message on stderr, followed by
`No tasks to show.` and exit code `0`. The original bytes survive in the
`.corrupt-*` file. Adding a task to that path afterwards works normally. ☐

## MT-13 — Supplied sample data

```bash
python run.py --file data/example_tasks.json list -a
python run.py --file data/example_tasks.json stats
```
*Expected:* five pre-loaded tasks, one of them completed and one overdue; the
file itself is **not** modified by read-only commands. ☐

## MT-14 — Automated suite

```bash
python -m unittest discover -s tests -v     # or: python -m pytest -q
```
*Expected:* `Ran 31 tests ... OK` (unittest) / `31 passed` (pytest). ☐

---

### Result summary

| Scenario | Feature | Pass ☐ |
|---|---|---|
| MT-01 | Help / version | |
| MT-02 | Add | |
| MT-03 | List / filter / sort | |
| MT-04 | Complete / re-open | |
| MT-05 | Edit | |
| MT-06 | Search / statistics | |
| MT-07 | Delete / clear | |
| MT-08 | Export | |
| MT-09 | Interactive shell | |
| MT-10 | Error handling | |
| MT-11 | Persistence | |
| MT-12 | Corruption recovery | |
| MT-13 | Sample data | |
| MT-14 | Automated tests | |

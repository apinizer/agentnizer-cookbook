---
name: process-issues
description: Batch task ingestion. Reads a CSV (title, description, optional task_type and module) and starts a pipeline task for each row via team.sh start. Useful for sprint kickoff or bulk triage.
---

# /process-issues — Batch task import

Usage:

```
/process-issues <csv-file>
/process-issues tasks.csv
```

## CSV Format

Header row required. Columns (in order, semicolon- or comma-separated):

```
task_type;title;description;module
bug;Login button does nothing on mobile;Tested on Safari iOS 18; the click does not register;frontend
feature;Webhook endpoint for inbound events;Accept signed JSON, queue for processing;backend
improve;Drop dead config flags;Several flags read by no module; remove and update profile;shared
```

- `task_type` — `bug` | `feature` | `improve` (controls which prompt
  template the planner uses)
- `title` — short imperative phrase, becomes `meta.json.title`
- `description` — full description, becomes `meta.json.description`
- `module` *(optional)* — hint for the planner; if absent, planner detects
  from title/description

## What it does

For each row:
1. Validates required columns are present.
2. Calls `./team.sh start "<task_type>: <title> — <description>"`.
3. Waits for the planner to finish before moving to the next row (avoids
   double-running planner on the same daemon lock).

The daemon then picks up tasks in `.state/active.json` and runs them in
parallel up to `LSD_MAX_PARALLEL_TASKS` (default 3).

## Smart ordering (recommended)

Before running, the skill suggests an ordering by complexity inference from
the description:

- **Priority 1 (simple, S/M)** — `bug` with a clear reproducer, `improve`
  on a single module, small features
- **Priority 2 (medium)** — multi-module bugs, `improve` with cross-cutting
  effect
- **Priority 3 (complex)** — new features with design decisions, breaking
  changes

The user reviews and confirms the order before any `team.sh start` calls.

## Output

```
Imported 7 tasks:
  [1] 20260427-1432-lgn  bug      module=frontend  → queued
  [2] 20260427-1433-whk  feature  module=backend   → queued
  ...

Daemon is now running. Watch with: ./team.sh status
```

## Limits

- Max 50 rows per CSV (refuse with a clear error if exceeded — split the
  file)
- Each row is independent — a malformed row is skipped with a warning, not
  a fatal error

## Notes

This is the closest thing the pipeline has to a "sprint planning" mode. We
use it on Monday mornings to dump the week's intake into the team in one
shot — the daemon then processes the queue while we drink coffee.

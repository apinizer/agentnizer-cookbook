---
name: start
description: Hand a task to the team. Spawns the planner, then the daemon takes over. The single entry point for any new piece of work.
---

# /start — Hand a task to the team

Usage:

```
/start "<plain English task description>"
/start --resume                     # restart the daemon after a crash
/start --from-csv tasks.csv         # batch import (see process-issues skill)
```

## What this does

`/start "<task>"` is a thin wrapper around the repo-root `team.sh`:

```bash
./team.sh start "<task description>"
```

That command:
1. Confirms the daemon isn't already running (or cleans up a stale lock).
2. Runs the **planner** agent in foreground. The planner splits your task
   into one or more sub-tasks and writes `.state/active.json` plus a
   `.state/tasks/<id>/meta.json` per sub-task.
3. Spawns the daemon (`pipeline-daemon.py`) in the background. The daemon
   then runs the rest of the pipeline end-to-end — analyst → architect →
   developer → (3 reviewers + tester + security in parallel) → qa →
   documenter → retrospective.
4. Returns control to you. Watch progress with `/status` or
   `./team.sh logs --daemon`.

## When to use it

- You have a feature, bugfix, or improvement and you want the team to ship it.
- For specifically-shaped work (bugfix, feature, improvement), the
  `/bugfix`, `/feature`, `/improve` skills apply the right prompt template
  before calling `/start`. Use those when the shape matters.
- For batch work (CSV of tasks), use `/process-issues`.

## --resume

If the daemon crashed mid-task, `/start --resume` (or
`./team.sh resume-daemon`) brings it back. Idempotency in every agent
(`role_done.<role>` flags in `meta.json`) means agents that already
finished will exit immediately on re-spawn; the daemon picks up exactly
where it stopped.

## --from-csv

Forwards to the `process-issues` skill. Each row in the CSV becomes a
separate `team.sh start` call.

## Safety limits (configurable)

```
LSD_POLL_INTERVAL          = 3 sec        # how often the daemon polls .state/
LSD_MAX_PARALLEL_TASKS     = 3            # how many tasks run in parallel
LSD_MAX_PARALLEL_SUB       = 4            # subagents per task (review fan-out)
LSD_AGENT_TIMEOUT_SEC      = 900          # hard kill any agent past this
```

Override via env vars at daemon start time.

## Notes

This is the same pipeline we run in production daily. The cookbook ships the
exact daemon and agent definitions we use, anonymized. `/start` is the
single command that kicks the team off.

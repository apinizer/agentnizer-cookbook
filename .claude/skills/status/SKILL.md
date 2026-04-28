---
name: status
description: Show the team's current state — daemon running? what tasks are in flight? which agent is on each task? Wraps team.sh status with optional drill-down.
---

# /status — Team status

Usage:

```
/status                       # human-readable table
/status --json                # machine-readable
/status <task-id>             # detail for one task
```

## What it does

Calls `./team.sh status`, which reads:
- `.state/locks/team.lock` — daemon pid, started_at, uptime
- `.state/locks/team.paused` — pause flag
- `.state/active.json` — DAG of in-flight tasks
- `.state/completed.jsonl` — last 5 completed tasks

And prints something like:

```
=== Team Status ===
Daemon: RUNNING (pid 47213, started 2026-04-27T14:32:00Z, uptime 0:14:22)

Active Tasks (1):
ID                        MODULE       STATUS         OWNER             UPTIME
20260427-1432-hlt         backend      reviewing      reviewer          0:01:23
20260427-1432-hlt         backend      reviewing      tester            0:01:23
20260427-1432-hlt         backend      reviewing      security_reviewer 0:01:23

Recent Completed (last 5):
ID                        MODULE       OUTCOME    DURATION   TOKENS
20260427-1240-cfg         shared       done       0:08:11    42k
20260427-1130-rbc         backend      done       0:11:42    63k
==============================
```

When you see the same task ID on multiple rows with different OWNER columns,
you're watching the parallel review fan-out. That's the team's "wow" moment.

## /status <task-id>

Shows the full timeline for one task: every handoff in
`.state/tasks/<task-id>/handoffs.jsonl`, the latest meta.json, and which
artifacts (`analysis.md`, `design.md`, etc.) exist on disk.

## Notes

`/status` is read-only. Safe to run any time. When the daemon has nothing to
do it exits cleanly; "Daemon: STOPPED" with empty active tasks means the
team finished its work.

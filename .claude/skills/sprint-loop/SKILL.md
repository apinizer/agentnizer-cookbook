---
name: sprint-loop
description: Deprecated alias — use /start instead.
---

# /sprint-loop — Deprecated

This skill has been renamed to `/start`. The mechanics are unchanged: the
daemon (`./team.sh start "<task>"`) takes a task description, runs the
planner, spawns the daemon. There is no "sprint" concept in the local
pipeline — the team works task-by-task until done.

Use `/start "<task>"` going forward.

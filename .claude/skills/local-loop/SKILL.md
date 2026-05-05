---
name: local-loop
description: Foreground, single-task interactive run. Spawns each agent in turn (no daemon, no parallelism), pauses at design and QA gates with AskUserQuestion. Useful for onboarding, debugging the pipeline itself, or running one task without a daemon.
---

# /local-loop — Interactive single-task run

Usage:

```
/local-loop "<task description>"
/local-loop --dry-run "<task description>"
```

## What it does

Runs the full pipeline for one task **in the foreground** — no daemon, no
parallelism — and pauses at gates so you can approve, reject, or comment
interactively via `AskUserQuestion`.

This is the same agent set as `/start`, just spawned sequentially with
explicit gates. Sequence:

1. **planner** — splits the task (usually into 1 sub-task in this mode)
2. **analyst** — writes `analysis.md`
3. **GATE #1 — Triage approval** (interactive)
   ```
   Module:        <module>
   Complexity:    <S/M/L/XL>
   BS items:      <count>
   Open decisions: <count>

   [approve] [reject] [skip]
   ```
4. **architect** — writes `design.md`
5. **GATE #2 — Design approval** (interactive)
   ```
   Sprint Contract:
   - SC-1 ...
   - SC-2 ...

   Files to change:
   - <list>

   [approve] [reject] [comment]
   ```
6. **developer** — implements, writes `progress.md`
7. **reviewer + tester + security_reviewer** — sequential here (not
   parallel), since you're watching
8. **qa** — writes `qa.md`
9. **GATE #3 — Final approval** (interactive)
   ```
   Review verdict:   PASS / FAIL
   Security verdict: PASS / accepted-risk
   QA verdict:       PASS / FAIL

   [approve] [reject]
   ```
10. **documenter** — updates docs
11. **retrospective** — appends lessons (async, fire-and-forget)

## Prerequisites

- `.claude/profiles/<module>.yaml` for the module being touched
- Whatever build/test/lint tools the profile names (this is `team.sh`-free,
  but the agents still call into your project's commands)

## When to use

- **Onboarding** — watching every gate teaches what each agent does
- **Debugging the pipeline** — you can inspect each agent's output before
  the next runs
- **One-off tasks** where running a daemon is overkill
- **Air-gapped environments** with no Slack and no desire for background
  processes

For normal day-to-day work, `/start` is faster (daemon + parallelism).

## --dry-run

Auto-approves all three gates (no `AskUserQuestion` prompts). Useful for
end-to-end smoke testing the agent pipeline itself on a sample task. Code
changes still get written; gate-skip is the only difference.

## Safety limits

```
MAX_DEVELOPER_RETRIES   = 3
MAX_SECURITY_RETRIES    = 2
MAX_TASKS_PER_RUN       = 1   (single-task is the point of local-loop)
```

## Notes

`/local-loop` doesn't touch `.state/locks/team.lock`, so it can coexist with
a paused (or stopped) daemon. It does write to `.state/tasks/<id>/` like the
daemon would — task state is identical at the end, you just got there
sequentially with human gates instead of a parallel automated run.

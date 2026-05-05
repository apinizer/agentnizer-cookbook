# Quickstart

**5 minutes. Plain English in. A reviewed PR-shaped output set out.** Read-through demo of the full assembly line on a trivial task before you point it at your own codebase.

## What you'll do

1. Read the demo task (`task.txt`).
2. Inspect the demo profile (`profiles/demo.yaml`) — deliberately trivial so the pipeline reaches `done` without a real toolchain.
3. Look at `expected-output/` to see the shape of every agent's output.

## What this directory contains

| Path | Purpose |
|------|---------|
| `task.txt` | One-line task description the planner agent receives. |
| `profiles/demo.yaml` | Minimal "always passes" profile. Build/test/lint print a string and exit `0`. |
| `code/` | Empty workspace where the developer agent would write code. |
| `expected-output/` | Reference artifacts (`analysis.md`, `design.md`, `progress.md`, `reviews.json`, `qa.md`, …) showing each agent's output shape. |

## Run it

The cookbook ships the daemon and CLI in-tree. From the repo root:

```bash
chmod +x team.sh
cp examples/quickstart/profiles/demo.yaml .claude/profiles/demo.yaml
./team.sh start "$(cat examples/quickstart/task.txt)"
./team.sh status                           # watch progress
./team.sh logs --daemon                    # tail daemon output
```

The daemon spawns each agent via `claude -p` and tracks state in `.state/tasks/<task-id>/`.

> **Cost note.** A real run with shipped agent prompts costs roughly $0.10–$0.50 depending on model availability and your account tier. Set `LSD_DAILY_USD_HARD_CAP=5` (or similar) before running if you want a safety floor.

## Read-only mode

Don't want to spawn agents yet? Skip the `./team.sh start` step. Read `task.txt`, `profiles/demo.yaml`, and `expected-output/*` to internalise the contract each role honours, then come back to run it once you're ready.

## What "done" looks like

`.state/tasks/<task-id>/` contains:

- `meta.json` with `status: done` and every `role_done.<role>` filled in
- `analysis.md`, `design.md`, `progress.md`, `tests.md`, `qa.md`, `security.md`, `docs.md`, `retro.md`
- `reviews.json` (consolidated) + `reviews/{correctness,convention,quality}.json` (per sub-reviewer)
- `handoffs.jsonl` (one line per agent-to-agent handoff)

`.claude/learned-lessons/<module>-lessons.md` gets a new entry appended by the retrospective agent.

## When the pipeline isn't finishing

| Symptom | Likely cause | Fix |
|---|---|---|
| Daemon doesn't pick up next agent | Previous agent didn't write `role_done.<role>` | `cat meta.json` — confirm flag is set |
| Same agent re-runs forever | Agent prompt missing idempotency check | First step must be: *"is `role_done.<me>` set? If yes, exit."* |
| Tester rc=0 but task fails | `Result: FAIL` written into `tests.md` | Expected — that triggers developer retry |
| Status STALE in `team.sh status` | Daemon crashed, lock not cleaned | `./team.sh stop && ./team.sh resume-daemon` |

Full operational reference: [`pipeline-workflow.md`](../../pipeline-workflow.md). Failure-mode war stories: [`docs/blog/operational-hardening-2026-05.md`](../../docs/blog/operational-hardening-2026-05.md).

## Next

After the quickstart works:

- [`bug-fix/`](../bug-fix/) — Hypothesis-First debugging recipe
- [`new-feature/`](../new-feature/) — multi-file feature with edge cases
- [`tracker-adapter/`](../tracker-adapter/) — pipe issues from your tracker into the pipeline

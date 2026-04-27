# AI Pipeline Workflow — Reference

This is the full reference for the local-state pipeline. The daemon
(`.claude/scripts/pipeline-daemon.py`) drives everything. State lives in
`.state/`. Agents are subprocesses spawned by the daemon.

For a quick "how do I use this?", see the top-level `README.md`. This
document is the deep dive.

## The 13 Roles

| # | Role | Model | Purpose | Output file |
|---|------|-------|---------|-------------|
| 1 | `planner` | Sonnet | Splits the user's task into sub-tasks; builds DAG | `meta.json`, `active.json` |
| 2 | `analyst` | Opus | Requirements + edge cases + Behavioral Spec | `analysis.md` |
| 3 | `architect` | Opus | Sprint Contract + design decisions | `design.md` |
| 4 | `developer` | Opus | Implements the code; quick lint check | `progress.md` + code |
| 5 | `reviewer` | Sonnet | Aggregates the 3 sub-reviews; emits PASS/FAIL | `reviews.json` |
| 6 | `review-correctness` | Opus | Bugs, missing edge cases, BS coverage, manifesto | `reviews/correctness.json` |
| 7 | `review-convention` | Sonnet | Lint/format compliance | `reviews/convention.json` |
| 8 | `review-quality` | Sonnet | DRY / SRP / complexity / dead code | `reviews/quality.json` |
| 9 | `tester` | Sonnet | Runs full test suite; verifies BS coverage | `tests.md` |
| 10 | `qa` | Opus | E2E + smoke + UX (full system check) | `qa.md` |
| 11 | `security-reviewer` | Opus | OWASP + module-specific security | `reviews/security.json` |
| 12 | `documenter` | Sonnet | API contract + usage docs + STATUS sync | `docs.md` + project docs |
| 13 | `retrospective` | Sonnet | Distills lessons into module lesson files | `learned-lessons/<module>.md` |

**Why two model tiers**: upstream errors compound. The analyst's wrong edge
case becomes the architect's wrong design becomes the developer's wrong
feature. Opus on upstream + security-critical roles, Sonnet on structured
downstream roles. We tested cheaper models on the upstream and observed
higher retry rates — false economy.

## Flow Sequence

```
team.sh start "<task>"
        │
        ▼
   ┌──────────┐
   │ planner  │   Reads top-level task. Writes active.json + meta.json
   └────┬─────┘   per sub-task. Exits.
        │
        ▼   ← Daemon takes over from here
   ┌──────────┐
   │ analyst  │   Reads meta.json + project context (within read_allowlist).
   └────┬─────┘   Writes analysis.md.
        │
        ▼
   ┌──────────┐
   │architect │   Reads analysis.md. Writes design.md (Sprint Contract).
   └────┬─────┘   On L/XL: requests decomposition (back to planner).
        │
        ▼
   ┌──────────┐
   │developer │   Reads design.md. Writes code + progress.md.
   └────┬─────┘   Quick lint on changed files only.
        │
        ▼   ← review fan-out (parallel)
        ├────────┬──────────┬──────────────────┐
        ▼        ▼          ▼                  ▼
   ┌──────────┐ ┌────────┐ ┌──────────┐ ┌──────────────┐
   │ review-  │ │review- │ │ review-  │ │  tester      │
   │correctness│ │convent │ │ quality  │ │              │
   └────┬─────┘ └───┬────┘ └────┬─────┘ └──────┬───────┘
        │           │           │              │
        └───────────┴───────────┴──────────────┘
                          │              ┌──────────────┐
                          │              │  security-   │
                          │              │  reviewer    │ (also parallel)
                          │              └──────┬───────┘
                          ▼                     │
                    ┌──────────┐                │
                    │ reviewer │ ◄──────────────┘
                    │(orchestr)│   Aggregates the 5 verdicts.
                    └────┬─────┘   PASS or FAIL.
                         │
                FAIL ────┼──── PASS
                         │      │
                         ▼      ▼
                    (developer  ┌──────────┐
                     retry,     │    qa    │   E2E + smoke + UX.
                     max 3)     └────┬─────┘   Final sanity check.
                                     │
                            FAIL ────┼──── PASS
                                     │      │
                                     ▼      ▼
                                (developer  ┌──────────┐
                                 retry,     │documenter│
                                 max 2)     └────┬─────┘
                                                 │
                                                 ▼
                                            ┌──────────┐
                                            │retrospec │
                                            └────┬─────┘
                                                 │
                                                 ▼
                                                done
```

## Status State Machine (full)

```
queued
  └─→ analyzing       (daemon spawns analyst)
        └─→ analyzed
              └─→ designing  (daemon spawns architect)
                    ├─→ designed
                    │     └─→ developing  (daemon spawns developer)
                    │           └─→ developed
                    │                 └─→ reviewing  (5 in parallel)
                    │                       ├─→ reviewed       (all PASS)
                    │                       │     └─→ qa-checking
                    │                       │           ├─→ qa_passed
                    │                       │           │     └─→ documenting
                    │                       │           │           └─→ documented
                    │                       │           │                 └─→ retrospecting
                    │                       │           │                       └─→ done
                    │                       │           └─→ qa_failed (→ developing, retry++)
                    │                       └─→ review_failed (→ developing, retry++)
                    └─→ decomposition_requested (→ planner re-spawned in decompose mode)
                          └─→ decomposed (parent waits for all children done)
                                └─→ documented (skip dev cycle for parent; retrospective runs)
                                      └─→ done

Failure states (terminal):
  - failed (retry-limit, token-budget hard limit, decompose chain too deep, etc.)
```

## Idempotency

Every agent's first action: *"is `meta.json.role_done.<me>` set with a
timestamp?"*. If yes → exit. This is what makes daemon crash/reboot recovery
work.

If you kill the daemon mid-task:
- Re-running `team.sh resume-daemon` is safe.
- The daemon polls `.state/active.json`, finds the unfinished task, sees
  which `role_done` flags are set, and spawns the next-needed agent.
- Already-finished agents that get re-spawned exit in ~50ms.

## Retry Caps (default)

```
planner            → 1
analyst            → 2
architect          → 2
developer          → 3
reviewer (orchestr)→ 2
sub-reviewers      → 2 each
tester             → 2
qa                 → 2
security-reviewer  → 2
documenter         → 2
retrospective      → 1
```

Override per-task via `meta.json.max_retries`.

## Token Budget

Each task has `meta.json.token_budget`:
```json
{ "soft_limit": 200000, "hard_limit": 500000 }
```

- **Soft limit hit**: daemon emits a Slack `info` warning. Pipeline continues.
- **Hard limit hit**: daemon FAILs the task with `failure_reasons:
  ["token hard limit exceeded"]`.

Tokens are approximated as `bytes/4` from agent stdout+stderr — not exact,
but a reasonable proxy.

## Daemon Hard Timeout

Each agent subprocess gets `LSD_AGENT_TIMEOUT_SEC` (default 900s).

If the timeout is hit:
- Daemon SIGTERMs the subprocess; waits 5s.
- If still alive, SIGKILLs.
- Adds `"timeout after 900s"` to `meta.failure_reasons`.
- The retry mechanism re-spawns the agent on the next loop (assuming
  `retry_count < max_retries`).

## Slack (optional)

The daemon calls `notify-slack.py` for:
- `security_alert` — CRITICAL/HIGH security finding (always sent if Slack
  is configured)
- `retry_limit` — agent exhausted retries; task FAILED
- `error` — daemon-level fatal
- `done` — task completed (usually disabled in dev)

If `PIPELINE_SLACK_BOT_TOKEN` / `PIPELINE_SLACK_CHANNEL` are unset, the
hook prints to stderr and exits 0 — no failure.

## Production Adaptations

Two adaptations production teams sometimes want:

### Tracker as state machine
Replace `.state/active.json` with tracker poll. Replace `meta.json.status`
with tracker issue status. Replace `analysis.md` / `design.md` etc. with
issue comments. The agent prompts don't change at all — only the daemon's
I/O backend swaps.

### Slack for action gates
The local pipeline runs end-to-end without human intervention. In
production, you can wire two human gates:
- **Design Gate** (after architect): post `design.md` to Slack; wait for
  human approve/reject; daemon polls `meta.json.gates.design`.
- **QA Gate** (after developer, before deploy): post diff to Slack;
  on-call human approves/rejects.

Both are clean additions to the existing flow — no agent changes.

## Common Failure Modes & Fixes

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Same agent re-runs forever | `role_done.<role>` not being set by the agent | Inspect agent's last log; check the meta.json patch was written |
| `team.sh status` shows STALE | Daemon crashed, lock not cleaned | `team.sh stop` (cleans the lock) → `team.sh resume-daemon` |
| Tasks accumulate in `queued` | Daemon paused (`.state/locks/team.paused`) | `team.sh resume` |
| Same task re-spawns dev → review → fail loop | `review_failed` retry > max | Inspect `reviews/*.json`; the developer can't fix what was flagged; needs human |
| Token hard limit on every task | `token_budget.hard_limit` too low for task complexity | Bump per-task or in planner default |

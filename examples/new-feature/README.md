# Recipe: New Feature

End-to-end walkthrough of a real feature task — bigger than the quickstart, smaller than your average sprint card. The whole pipeline runs: planner splits the task, analyst writes a Behavioral Spec, architect emits a Sprint Contract, developer implements, three reviewers + tester + security run in parallel, qa signs off, documenter updates, retrospective writes lessons.

## When to use this recipe

- A new endpoint, screen, or background job that touches **multiple files** in one module.
- A feature where the spec is **mostly clear** but has 1-3 edge cases worth surfacing in `analysis.md`.
- You want to see what a "real" run produces, not the smoke-test minimum.

Skip it if the task fits in a single file with no acceptance criteria — quickstart already covers that.

## Senario

Add **user invitation flow with email verification** to a hypothetical SaaS backend. Concretely:

- POST /invites — admin creates an invite, server emails a tokenized link.
- GET /invites/{token} — recipient lands here, sees who invited them.
- POST /invites/{token}/accept — recipient sets password, becomes a real user.
- Tokens expire after 7 days; admins can revoke; the same email cannot be re-invited while a pending invite exists.

This is **realistic** in shape (multi-step state machine, email side effect, security surface) without dragging in a full auth stack.

## Run it

```bash
# 1. Drop in the recipe profile (already configured for the demo backend module).
cp examples/new-feature/profiles/feature-demo.yaml .claude/profiles/feature-demo.yaml

# 2. Hand the team the task.
./team.sh start "$(cat examples/new-feature/task.txt)"

# 3. Watch it run.
watch -n 2 ./team.sh status
```

Expect ~10-20 minutes wall-clock end-to-end (depends on Claude latency and how many retries trigger).

## What the team should produce

| Stage | Output |
|---|---|
| Planner | Splits this into 1 task (no decomposition needed at this complexity) |
| Analyst | `analysis.md` with 4-6 BS items + 3 edge cases (revoke-then-reinvite, expired-then-clicked, email-changes-mid-flow) |
| Architect | Sprint Contract with API shape, DB schema (1 new table), state machine for invite status |
| Developer | Patches across `routes/invites.py`, `models/invite.py`, `services/email.py`, migrations, tests |
| Reviewer + tester + security | Five parallel verdicts. Security looks specifically at token entropy and timing-attack surface |
| QA | Walks through happy path + 2 edge cases as if a human |
| Documenter | Updates API docs (OpenAPI YAML) and STATUS.md |
| Retrospective | Writes 1-2 paragraphs into `learned-lessons/backend-lessons.md` |

## Files in this recipe

| File | Purpose |
|---|---|
| `task.txt` | The task description piped to `./team.sh start` |
| `profiles/feature-demo.yaml` | Module profile with `manifest_check` populated for backend |
| `expected-output/sprint-contract-snippet.md` | Hand-written reference of what a real Sprint Contract looks like for this scope |

## How to know it worked

- `analysis.md` lists at least the three edge cases above (revoke-then-reinvite, expiration, email-change).
- `design.md` has a clear state machine (pending → accepted | revoked | expired) and a token-entropy decision (e.g. 256-bit URL-safe base64).
- `reviews/security.json` has zero CRITICAL findings; if it has HIGH findings, the developer retried and the second pass cleared them.
- `qa.md` runs the happy path **and** at least one edge case (typically expiration).
- The retro file got a new entry — even if it's just *"first invite-flow run; no surprises."*

## Adapting this to your features

- Replace `task.txt` with your feature description. Keep it specific: endpoints, data shape, edge cases the analyst should care about. Vague tasks ("add invites") produce vague analyses.
- Replace `profiles/feature-demo.yaml` with one of `.claude/profiles/backend.yaml`/`worker.yaml`/`shared.yaml` — or write a new profile for your module.
- If your feature spans multiple modules, the planner will split the task into sub-tasks; that's the DAG kicking in. You'll see them in `./team.sh status` running in parallel up to `LSD_MAX_PARALLEL_TASKS`.

## What this recipe deliberately does NOT do

- It does **not** ship a working email service. The `services/email.py` produced by the developer is a stub that logs the would-be-sent email; wiring it to a real SMTP/SES is your job.
- It does **not** include a frontend. Just the API + DB + tests.
- It does **not** spin up a real Postgres in CI. The tests use SQLite; the Sprint Contract notes this trade-off explicitly.

The recipe's value is the **shape of the run**, not a turnkey invite system.

# Recipe: Tracker Adapter (GitHub Issues)

Swap the cookbook's `.state/active.json` queue for a real **issue tracker**. This recipe shows the smallest possible adapter — a poll loop that pulls open GitHub Issues with a `ai-pipeline` label and feeds them into the daemon's task queue.

GitHub Issues is the easiest tracker to start with: OAuth is simple, the API is documented, rate limits are generous. Once you've got this working, swapping it for Jira / Linear / GitLab Issues is a 50-line diff.

## Why a tracker

The local `.state/` queue is great for solo dogfooding. As soon as you have a team, you want:

- Tasks created and triaged in **the place humans already work** (your tracker).
- Status visible without `cat`-ing files.
- Comments — analyst posts the BS, architect posts the Sprint Contract, reviewer posts findings — all aligned to the ticket.
- Audit trail without a database migration.

The tradeoff is one moving part: the adapter. This recipe is the adapter, **without yet** moving the agents off file-based output (that's a bigger refactor and we keep it out of the cookbook).

## What this recipe ships

```
examples/tracker-adapter/
├── README.md                              ← you are here
├── adapter.py                             ← reference adapter (~120 lines)
├── skills/process-github-issues.md        ← /process-github-issues slash command
└── how-to-swap-trackers.md                ← Jira / Linear / GitLab notes
```

## How the adapter works

```
                   GitHub Issues (label: ai-pipeline)
                              │
                              │  poll every 60 s
                              ▼
                       ┌──────────────┐
                       │  adapter.py  │   reads issues, filters by status,
                       └──────┬───────┘   maps each to a task entry
                              │
                              ▼
                    .state/active.json
                              │
                              ▼
                    pipeline-daemon.py runs as normal
                              │
                              ▼
              .state/tasks/<task-id>/{analysis,design,...}.md
                              │
                              ▼
                       ┌──────────────┐
                       │  adapter.py  │   posts each .md as a comment,
                       └──────┬───────┘   transitions issue label
                              ▼
                    GitHub Issues comment
```

The adapter is a **shim**. It doesn't change the daemon, it doesn't change the agents. It just translates.

## Mapping table

| GitHub Issues | `.state/active.json` field |
|---|---|
| Issue title | `title` |
| Issue body | `description` |
| Label `ai-pipeline` (must be present) | (filter — only matching issues enter the pipeline) |
| Label `module:<name>` | `module` |
| Label `risk:<LOW\|MED\|HIGH\|CRITICAL>` | `risk_level` |
| Label `complexity:<XS\|S\|M\|L\|XL>` | `complexity` |
| Issue number | `external_ref` (kept for the comment-back path) |

When the daemon writes `analysis.md`, the adapter posts it as an issue comment prefixed with `**[analyst]**`. When the issue closes (status `done`), the adapter removes the `ai-pipeline` label and adds `ai-pipeline-done`.

## Run it (dry-run mode)

The shipped adapter is **dry-run by default** — it logs what it *would* do, but does not write to GitHub or `.state/`. This lets you see the plumbing before granting it a token.

```bash
# 1. Install the one dependency
pip install requests

# 2. Run dry-run (no token required)
python examples/tracker-adapter/adapter.py \
  --owner your-org \
  --repo your-repo \
  --dry-run

# Expected output:
# [adapter] Would query: GET /repos/your-org/your-repo/issues?labels=ai-pipeline&state=open
# [adapter] Would write 0 entries to .state/active.json
# [adapter] (set GITHUB_TOKEN and remove --dry-run to go live)
```

To go live (read-only first):

```bash
export GITHUB_TOKEN=ghp_...
python examples/tracker-adapter/adapter.py \
  --owner your-org \
  --repo your-repo \
  --read-only
# Reads issues, writes to .state/active.json. Does NOT comment back.
```

Full bidirectional (read issues + post comments + transition labels):

```bash
python examples/tracker-adapter/adapter.py \
  --owner your-org \
  --repo your-repo
```

## Tuning

- `--poll-interval 60` — seconds between polls. GitHub rate limit is 5000 req/h authenticated; 60s leaves plenty of headroom.
- `--label ai-pipeline` — change the trigger label.
- `--max-tasks 3` — cap how many issues feed into the daemon at once. Matches `LSD_MAX_PARALLEL_TASKS`.

## What's intentionally missing

- **No webhooks.** Polling is simpler and avoids exposing a public endpoint. If you want webhooks, a 30-line FastAPI server forwarding to the same `_handle_issue()` function does it.
- **No bidirectional comment threading.** The adapter posts comments but does not parse human replies. If a human comments "looks good, ship it" — the adapter doesn't know. Wire that into your gate skill if you want it.
- **No retry coordination across machines.** This adapter assumes one daemon per repo. Multi-host setups need a real database lock; out of scope for the cookbook.

## Adapting to other trackers

See [`how-to-swap-trackers.md`](how-to-swap-trackers.md) for the diff against Jira, Linear, and GitLab. The shape is the same; the API client and the label-to-field mapping change.

## Acceptance for this recipe

- [ ] `python adapter.py --dry-run --owner X --repo Y` runs cleanly with no token.
- [ ] With a token + `--read-only`, it reads issues and writes correct entries to a temporary `.state/active.json`.
- [ ] In full mode against a test repo, opening an issue with the trigger label causes the daemon to spawn the planner within one poll interval.
- [ ] Closing the issue (or removing the label) causes the adapter to stop posting further comments for that task.

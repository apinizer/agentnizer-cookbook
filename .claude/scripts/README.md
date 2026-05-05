# Scripts

Two scripts that the daemon and weekly tuner run.

## `pipeline-daemon.py`

The local-state daemon (LSD). Polls `.state/active.json`, reads each task's `meta.json`, spawns the next agent via `claude -p`, harvests usage. ~2400 LOC, stdlib-only (plus optional `structlog`).

```bash
# Normal run (default — managed by team.sh)
./team.sh start "<task description>"

# Direct invocation (debugging)
python3 .claude/scripts/pipeline-daemon.py --once
python3 .claude/scripts/pipeline-daemon.py --state-dir /tmp/test-state
```

Key env vars (full list in the script's docstring):

- `LSD_POLL_INTERVAL` — poll interval, default 3s
- `LSD_MAX_PARALLEL_TASKS` — concurrency cap, default 3
- `LSD_AGENT_TIMEOUT_<ROLE>` — per-role timeout override (e.g. `LSD_AGENT_TIMEOUT_DEVELOPER=3600`)
- `LSD_MAX_OUTPUT_<ROLE>` — per-role `CLAUDE_CODE_MAX_OUTPUT_TOKENS` override
- `LSD_DAILY_USD_HARD_CAP` — daemon-wide daily spend ceiling

Spec for the full state machine, T1–T19 tunings, and Slack matrix: [`../../pipeline-workflow.md`](../../pipeline-workflow.md).

## `weekly-tuner-trigger.sh`

Cron entry for the weekly `tuner` agent. Reads `.claude/learned-lessons/<module>-lessons.md`, asks the tuner to find a pattern that recurred in 2+ tasks this week, drops a proposed prompt patch under `.state/tuner/<week>/proposal.md` for human approval. Propose-only — never modifies agent prompts directly.

```cron
# Friday 18:00 local
0 18 * * 5 /path/to/repo/.claude/scripts/weekly-tuner-trigger.sh
```

Manual trigger (for testing):

```bash
bash .claude/scripts/weekly-tuner-trigger.sh
```

The tuner agent prompt itself lives at [`../agents/tuner.md`](../agents/tuner.md). The pattern essay: [`../../docs/blog/the-tuner-pattern.md`](../../docs/blog/the-tuner-pattern.md).

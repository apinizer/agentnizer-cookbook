# Changelog

All notable changes to the agentnizer-cookbook are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Working daemon & production runtime

- `team.sh` and `.claude/scripts/pipeline-daemon.py` shipped in-tree. The cookbook now runs out of the box; previous releases were docs-only.
- Module-level conflict guard, idle-stream watchdog, SIGKILL cascade on shutdown, and graceful subprocess reaping.
- 21-test smoke suite for the daemon's state machine, role lookup, and FAIL-outcome detection.

### Production-validated tunings (T1–T19)

- **T1** — Cumulative retry context (`MAX_CUMULATIVE_BLOCK_BYTES=4096`, env `LSD_MAX_RETRY_BLOCK_BYTES`).
- **T2** — Cycle reset on retry / architect revision.
- **T3** — Architect revision loop (`MAX_ARCHITECT_REVISIONS=2`).
- **T4** — Per-role agent timeout (`AGENT_TIMEOUT_PER_ROLE` dict + `LSD_AGENT_TIMEOUT_<ROLE>` env overrides). Replaces the single global cap that was wrong on both ends.
- **T5** — API quota detection (12 patterns) — `awaiting_api_quota` status, retry counter not incremented.
- **T6** — Auto-resume window (`API_QUOTA_AUTO_RESUME_WINDOW_SEC=3600`, env-overridable).
- **T7** — Per-role output cap (`AGENT_MAX_OUTPUT_PER_ROLE` + `LSD_MAX_OUTPUT_<ROLE>` overrides via `CLAUDE_CODE_MAX_OUTPUT_TOKENS` env). ~20% token saving on low-stakes roles, no quality drop.
- **T8** — Cache-friendly stable prompt prefix (Anthropic prompt cache hit rate ≈88% in steady state).
- **T13** — Quota-reset regex extraction (6 patterns: `12hour_hm`, `24hour_hm`, `12hour`, `try_again_in`, `wait_n`, `retry_after`). Auto-resume wakes precisely at the parsed reset time.
- **T14** — Token field harvest in `move_to_completed` (now writes `token_billable_used`, `architect_revision_count`, full token breakdown into `completed.jsonl`).
- T15–T19 — per-role token breakdown, cumulative `team.sh tokens` report (`--json` / `--daily` / `--cache`), native usage parsing via `--output-format json`, daily USD cap, and weekly tuner cron.
- T10 / T11 deferred (pattern-only, prompt-side).

### Three-gate human review pattern

- `awaiting_info` — analyst or architect surfaces an open decision; daemon halts, human resolves, daemon resumes.
- `human-review-pending` — mandatory for `flow_type=code_development`; post-reviewer-barrier code review.
- `human-final-test` — optional in local mode, recommended in production with staging.
- All three map cleanly onto tracker statuses.

### Cache-aware token accounting

- **`token_billable_used`** (excludes `cache_read`) is the budget enforcement counter; `token_used` (raw, includes cache_read) stays as the observability total. Counting cache reads against a hard cap produced false-positive failures because Anthropic prices cache_read at ~10% of full input price.
- **`LSD_DAILY_USD_SOFT_CAP`** (Slack warning) and **`LSD_DAILY_USD_HARD_CAP`** (daemon refuses new spawns until UTC midnight rollover).

### Slack live-ops

- 11-event matrix in `notify-slack.py`: `phase_transition`, `awaiting_info`, `human_review_pending`, `human_final_test_pending`, `review_fail`, `test_fail`, `security_alert`, `retry_limit`, `awaiting_api_quota`, `done`, `error`.
- Production-shape live-tail walkthrough (sample channel feed, gate-claim-by-emoji workflow) in `docs/use-case-tracker-driven-pipeline.md`.

### Examples, recipes & docs

- Quickstart + 3 recipes under `examples/`: `bug-fix`, `new-feature`, `tracker-adapter`.
- Long-form writeups in `docs/blog/`: sprint-contract pattern, operational-hardening lessons, weekly tuner pattern.
- `pipeline-workflow.md` lifted to repo root for visibility (Mermaid flow + full state machine + T1–T19 reference table).
- README rewritten for trend-repo cadence: motto-first banner, scannable shapes, badge row.
- Five new pain-pattern lessons in `learned-lessons/shared-lessons.md`: `api-quota-retry-exemption`, `reset-time-priority-over-fallback`, `per-role-timeout-authoritative`, `status-transition-completeness`, `architect-revision-cap`.

### Removed

- `.claude/skills/cookbook-write/` — referenced an unpublished agent and was meta-tooling for authoring the cookbook itself, not part of the assembly-line pattern.
- `.claude/skills/sprint-loop/` — deprecated alias of `/start`.

### Changed

- Default daemon log path → `~/.claude/logs/ai-pipeline-daemon.log`.
- Agent count phrasing standardised: "13-agent synchronous assembly line + 1 optional weekly tuner = 14 agent definitions."
- `.claude/hooks/check-status-update.sh` reframed as an optional per-user STATUS.md journaling-convention example; daemon does not depend on it.

## [0.1.0]

Initial public release.

### Added

- 13-agent synchronous assembly line + 1 optional weekly tuner = 14 agent definitions in `.claude/agents/*.md`.
- Parallel review fan-out — three sub-reviewers + tester + security-reviewer against the same diff (5-way barrier-synced).
- Idempotent role-guard contract — every agent's first step is "is `role_done.<me>` set? If yes, exit."
- File-based state machine under `.state/` (active.json, completed.jsonl, per-task directories).
- Per-module manifest profiles for `shared`, `backend`, `worker`, `frontend`.
- User-facing slash-command skills (`start`, `status`, `ask`, `verify`, `feature`, `bugfix`, `improve`, `local-loop`, `process-issues`).
- Optional Slack notification hook (fire-and-forget on critical events).
- Quickstart walkthrough with hand-written reference output and demo profile.
- MIT license; closed-source prior to this tag.

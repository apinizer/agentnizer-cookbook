# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this repo is

A 13-agent synchronous assembly-line pipeline (plus a weekly `tuner` agent on cron — 14 total) that turns a plain-English task into a reviewed, tested, security-scanned, documented patch.

**Read, copy, adapt** — not deployed as-is. The shape is production. The recipes are anonymized.

Described in Claude Code terms because that's what the maintainers run, but the design is **LLM-agnostic**: the daemon, profiles, and state machine don't depend on Claude Code specifics. The agent prompt format is portable to any subprocess-spawnable runner that emits structured usage.

## Spec lives here

[`pipeline-workflow.md`](pipeline-workflow.md) (repo root) is the deep technical spec — Mermaid flow, state machine, retry semantics, three-gate human review, Slack matrix, T1–T19 production-validated tunings. **Read it before changing any agent prompt or daemon constant.**

## Public surface

Everything in this repo is public. There is **no private layer** — the entire `.claude/` directory ships as-is.

The cookbook = 9 skills + 14 agent definitions + 3 hooks + 4 profiles + daemon (~2400 LOC) + 21 tests. Per-user runtime state (`.state/`, `STATUS.md`, `STATUS-history/`, `.claude/.env`) is gitignored — but the showcase itself isn't split into public vs internal tiers.

When adding new artifacts, default to public. Don't introduce a `.claude/`-shadow private tree without explicit reason.

## Repository layout

- `team.sh` — reference CLI (`start` / `stop` / `status` / `pause` / `logs` / `tokens`)
- `pipeline-workflow.md` — deep technical spec (Mermaid + state machine + tunings + Slack matrix)
- `.claude/agents/*.md` — 14 agent definitions: 13 synchronous (planner, analyst, architect, developer, reviewer + 3 sub-reviewers, tester, qa, security-reviewer, documenter, retrospective) + 1 weekly tuner
- `.claude/scripts/pipeline-daemon.py` — the local state daemon (LSD), ~2400 LOC, stdlib-only
- `.claude/scripts/weekly-tuner-trigger.sh` — cron entry for the tuner agent
- `.claude/skills/*/SKILL.md` — 9 user-invocable slash commands: `/start`, `/status`, `/ask`, `/bugfix`, `/feature`, `/verify`, `/improve`, `/local-loop`, `/process-issues`
- `.claude/profiles/*.yaml` — per-module build/test/manifest config (4 starters: shared / backend / worker / frontend)
- `.claude/learned-lessons/*.md` — retrospective writes; tuner reads
- `.claude/hooks/{notify-slack.py, after-code-change.sh, check-status-update.sh}`
- `.claude/.env.example` — daemon env var template
- `tests/test_pipeline_daemon.py` — 21 daemon unit tests
- `examples/` — quickstart + 3 recipes (bug-fix, new-feature, tracker-adapter)
- `docs/blog/*.md` — long-form writeups (sprint-contract, operational-hardening, tuner-pattern)
- `docs/use-case-tracker-driven-pipeline.md` — production tracker-driven shape (Jira / Linear / GitHub Issues + Slack)
- `CHANGELOG.md` — Keep-a-Changelog format
- `README.md` — primary public entry point
- `LICENSE` — MIT

## Test & lint commands

```bash
# 21 daemon unit tests
uv run --with pytest --with pytest-asyncio pytest tests/

# Lint Python
uv run --with ruff ruff check .claude/scripts/ tests/

# Validate shell scripts
bash -n team.sh
bash -n .claude/scripts/weekly-tuner-trigger.sh
bash -n .claude/hooks/*.sh
```

## Safety & workflow rules

- **Never commit `.env` or `.state/`.** Both are gitignored. Verify nothing is staged before pushing.
- **Run the test suite when modifying agent prompts.** Changes to `.claude/agents/*.md` ripple through daemon behaviour. `pytest tests/` catches observable differences.
- **Idempotency is non-negotiable.** Every new agent's first step must be: *"is `role_done.<me>` set? If yes, exit."* This is what makes crash-resume work.
- **Don't introduce new artifacts outside `.claude/` without reason.** Agent definitions, skills, hooks, profiles all live in `.claude/` for a flat resolver.
- **Profile-driven, not hardcoded.** All stack-specific commands (build, test, lint) live in `.claude/profiles/*.yaml`. Agent prompts read profiles; they never name a language or framework directly.

## Conventions

- All agent prompts, README, blog posts, and inline comments are **English**.
- The cookbook is **stack-agnostic**. Adapt profile YAML; don't edit agents to match your stack.
- Per-user runtime state (`.state/`, `STATUS.md`) is gitignored. Don't add it back.

## License

MIT. See [`LICENSE`](LICENSE). Use it, fork it, ship it.

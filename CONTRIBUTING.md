# Contributing

PRs welcome. The cookbook is opinionated; we want to keep it that way.

## Most-wanted contributions

- **Tracker adapters** — Jira / Linear / GitHub Issues / GitLab Issues poll-and-post. See [`docs/use-case-tracker-driven-pipeline.md`](docs/use-case-tracker-driven-pipeline.md) for the contract.
- **Module profile starters** — Go, Java, Rust, Ruby, .NET in `.claude/profiles/`.
- **New agent roles** — performance-profiler, accessibility-reviewer, i18n-checker.
- **Real-run lessons** — anonymized entries in `.claude/learned-lessons/<module>-lessons.md`.

## Before you open a PR

1. **Open an issue first** for non-trivial work.
2. **Run `pytest tests/`** — 21 tests must pass.
3. **Run the quickstart** (`examples/quickstart/`) end-to-end on a fresh checkout.
4. If you changed an agent prompt, run a real task through it; output should be sane.
5. Touched a profile schema? Update all four example profiles to match.

## Less likely to merge

- Sweeping rewrites of core agent prompts — open an issue first.
- New runtime dependencies in `pipeline-daemon.py` (we keep it stdlib-only + the `claude` CLI).
- Stack-specific commands hardcoded into agent prompts — those belong in `.claude/profiles/*.yaml`.
- "I made it match my codebase" PRs — fork instead.

## Style

- Markdown: 80–90 col soft wrap, no trailing whitespace.
- Python: PEP 8, no new dependencies without a one-line justification.
- Bash: `set -euo pipefail`, bash 4+, short and explicit.
- Agent prompts: concrete > generic, manifesto-tagged, traceable to a real failure mode.

## Security issues

Don't open a public issue. Email the maintainers directly. Response within a few days.

## License

By contributing you agree your contributions are MIT-licensed.

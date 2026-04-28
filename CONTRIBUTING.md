# Contributing

Thanks for considering a contribution. The cookbook is opinionated — we
want to keep it that way — but we welcome focused, well-scoped patches.

## Before you open a PR

1. **Open an issue first** for anything non-trivial. We may already be
   working on it, or have a reason it's shaped the way it is.
2. **Read the README and `docs/use-case-tracker-driven-pipeline.md`** so
   your change lands in the right place. Agent files are surface-level
   skeletons; if your contribution requires changing core mechanics
   (`pipeline-daemon.py`, `team.sh`, the `.state/` schema), say so in
   the issue.
3. **Run the quickstart** (`examples/quickstart/`) on your machine.
   Confirm the pipeline still works end-to-end after your change.

## What we're most interested in

| Area | What helps |
|---|---|
| **Tracker adapters** | A working backend for Jira / Linear / GitHub Issues / GitLab Issues that swaps `.state/active.json` for tracker polling. See [`docs/use-case-tracker-driven-pipeline.md`](docs/use-case-tracker-driven-pipeline.md) for the contract. |
| **New agent roles** | Performance profiler, accessibility reviewer, i18n checker, license auditor — anything that fits the existing parallel-review fan-out shape. |
| **Module profile templates** | Starter profiles for common stacks (Go, Java, Rust, Ruby, .NET) with the `manifest_check` items pre-filled. |
| **Documentation** | Real-world adoption notes, troubleshooting recipes, lessons that survived multiple runs. |
| **Bug fixes** | Anything that breaks `examples/quickstart/` on a fresh checkout. |

## What we're less likely to merge

- Sweeping rewrites of core agents — these have been tuned over many
  runs; please open an issue first.
- New dependencies in `pipeline-daemon.py` (we keep it stdlib + `requests`
  + optional `structlog`).
- Stack-specific commands hardcoded into agent prompts — those belong in
  `.claude/profiles/*.yaml`, not in agent definitions.
- "I made it match my codebase" PRs — fork, don't upstream those.

## Pull request checklist

- [ ] Issue opened and discussed (for non-trivial changes)
- [ ] Quickstart still runs end-to-end (`examples/quickstart/README.md`)
- [ ] If you touched an agent, you ran a real task through the modified
      agent and the output is sane
- [ ] If you touched a profile schema, the example profiles in
      `.claude/profiles/` were updated to match
- [ ] If you added a new file, it has a one-line description at the top
- [ ] No vendor-specific names, paths, or credentials anywhere
- [ ] No new dependencies without a one-line justification in the PR

## Style

- Markdown: 80–90 column soft wrap, no trailing whitespace.
- Python: PEP 8, no exotic dependencies.
- Bash: `set -euo pipefail`, bash 4+ idioms, short and explicit.
- Agent prompts: concrete > generic, manifesto-tagged, traceable to a
  real failure mode.

## Reporting security issues

Don't open a public issue. Email the maintainers directly. We'll respond
within a few days.

## License

By contributing you agree your contributions are licensed under MIT,
matching the rest of the cookbook.

# Cookbook examples

Four worked examples that show the AI pipeline in action, from a five-minute smoke test through a realistic multi-file feature and an integration with a real issue tracker. Each subdirectory has its own `README.md` with a runnable walkthrough; this page is the index.

## Prerequisites (shared)

- The [Anthropic Claude CLI](https://docs.anthropic.com/) (`claude`) installed and authenticated.
- Python 3.10+ on your `PATH` (for any local adapter scripts).
- A clone of this repository.
- The cookbook ships `team.sh` and `.claude/scripts/pipeline-daemon.py` in-tree. `chmod +x team.sh && ./team.sh help` and you're set; no extra wiring required.

Some recipes require additional pieces; each subdirectory README lists them in its own *Prerequisites* or *Run it* section.

## The four examples

### [`quickstart/`](./quickstart/) — five-minute smoke test

Read-only walkthrough of a trivial "hello world" task that lets you see what the pipeline produces end-to-end before you wire it to your real codebase. Ships a `task.txt`, a deliberately trivial `profiles/demo.yaml` (build/test/lint all `echo`), and a hand-written `expected-output/` directory mirroring the artifact shape a real run produces. Use this to validate your daemon and prompt set before pointing them at production code.

### [`bug-fix/`](./bug-fix/) — Hypothesis-First debugging

Walks the team through a real bug under the **Hypothesis-First** protocol: the analyst is forbidden from writing a patch plan until they have produced a falsifiable hypothesis and the test that confirms it. The example is a `psycopg2.errors.UniqueViolation` on `POST /auth/login` under concurrent load. Demonstrates how the analyst → architect → developer chain locks in a written cause-and-effect trail that doubles as a postmortem.

### [`new-feature/`](./new-feature/) — multi-file feature with edge cases

End-to-end run of a realistic feature: a user invitation flow with email verification, token expiration, and revocation. Bigger than `quickstart/` (multi-file, multi-edge-case), smaller than a real sprint card. Shows the full pipeline — planner, analyst, architect, developer, three reviewers + tester + security in parallel, qa, documenter, retrospective — including a hand-written `expected-output/sprint-contract-snippet.md` reference of what a real Sprint Contract looks like at this scope.

### [`tracker-adapter/`](./tracker-adapter/) — pipe issues from your tracker into the pipeline

Reference adapter (~120 lines, `adapter.py`) that translates open GitHub Issues with an `ai-pipeline` label into entries in `.state/active.json`, then mirrors agent outputs back as issue comments. Ships a slash-command skill (`skills/process-github-issues.md`) and a [`how-to-swap-trackers.md`](./tracker-adapter/how-to-swap-trackers.md) note covering Jira, Linear, and GitLab variants. Requires `pip install requests` and a `GITHUB_TOKEN` for live mode; runs `--dry-run` with no token.

## Reading order

If you're new to the cookbook, work through them in the order listed above — each builds on the last. If you already have a daemon running and just want a recipe to copy: jump to the example that matches your task shape (bug-fix vs. feature) and lift its profile and task description.

For the architectural background behind these recipes, see the blog posts under [`../docs/blog/`](../docs/blog/) — in particular `sprint-contract.md` (the agent-to-agent handoff contract) and `operational-hardening-2026-05.md` (the guardrails the daemon enforces).

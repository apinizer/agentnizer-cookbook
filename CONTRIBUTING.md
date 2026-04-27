# Contributing to the AI Pipeline Cookbook

We welcome contributions. Here's what's most useful:

## High-Value Contributions

- **Tracker adapters** — adapt `pipeline-daemon.py` to read state from Jira /
  Linear / GitHub Issues / Asana instead of `.state/active.json`. This is
  the most-requested feature; the local cookbook ships one viable backend
  (the filesystem) but production teams often want a tracker as the
  state-of-record.
- **New agent roles** — performance-profiler, accessibility-reviewer,
  i18n-checker, dependency-license-auditor, etc.
- **Module profiles** — starter profiles for Go, Java, Rust, Ruby, etc.
- **Learned lessons** — patterns you've discovered running the pipeline on
  your own codebase.

## How to Contribute

1. Fork the repo.
2. Make your changes. Run `./team.sh start "<your contribution as a task>"`
   if you'd like to dogfood the pipeline on the contribution itself — works
   best for new agents and module profiles.
3. Open a PR. In the description: what problem it solves + what you tried +
   how to validate.

## Agent Prompt Guidelines

When improving an agent's prompt:

- **Specific over generic.** Concrete patterns beat platitudes — "for
  shared counters, use a lock primitive scoped to the task" reads more
  reliably than "be thread-safe".
- **Examples over rules.** Show one good pattern and one anti-pattern. We
  use this throughout the cookbook.
- **Tag manifesto axes.** Every rule should reference at least one of
  `[performance]` / `[thread-safety]` / `[safety]` / `[observability]`.
- **Traceability.** Every rule should be traceable to a real failure mode
  (in your runs, in `learned-lessons/`, or in a referenced public incident).

## Tracker Adapter Guidelines

If you're adding tracker support, the architecture is:
- The daemon's "read `.state/active.json`" call becomes a tracker poll for
  open issues with the pipeline label.
- The agents' "write `.state/tasks/<id>/<file>.md`" calls become tracker
  comment posts.
- The agents' "update `meta.json.status`" becomes a status transition.
- The agents' `role_done.<role>` flags become labels (e.g.
  `ai-developed`).

The agent prompts shouldn't change. Only the daemon and a thin I/O layer
should.

## Questions

Open an issue.

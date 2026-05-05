---
name: ask
description: Read-only Q&A about the pipeline, your codebase, the team's runs in .state/, or anything in this cookbook. No code changes, no tasks spawned.
---

# /ask — Read-only Q&A

Pure inspection mode. Answer questions about the pipeline, the codebase,
or recent runs without spawning agents or writing code.

## Usage

```
/ask "<question>"
```

Examples:

```
/ask "What does the planner do when a task is decomposed?"
/ask "What did the team do on task 20260427-1432-hlt?"
/ask "Show me the recent retrospective findings for the worker module."
/ask "Which agent runs first when I do team.sh start?"
/ask "What's the difference between reviewer and review-correctness?"
/ask "Where do I configure the lint command for a module?"
/ask "Why did the developer retry on this task?"
```

## What it reads

In priority order:

1. **`.state/`** — for questions about in-flight or recent tasks.
   `meta.json`, `analysis.md`, `design.md`, `progress.md`, `reviews/*.json`,
   `tests.md`, `qa.md`, `handoffs.jsonl`, `completed.jsonl`.
2. **`.claude/`** — for questions about the pipeline itself.
   Agent definitions, skill specs, profiles, hooks, `pipeline-workflow.md`.
3. **`README.md`** + **`docs/`** — for high-level orientation questions.
4. **Your project source** — only if relevant *and* the question is about
   your code (not the pipeline). Stays inside the active task's
   `read_allowlist` if there is one.

## What it does NOT do

- ❌ Spawn agents
- ❌ Write code
- ❌ Touch `.state/` (no new tasks, no `meta.json` edits)
- ❌ Call `team.sh start`
- ❌ Run shell commands that mutate state (`git commit`, `npm install`,
  test runs, etc.)

The skill is `tools: Read, Glob, Grep` only. Anything that writes is out
of scope.

## Good questions vs not-so-good

| Good | Not so good |
|---|---|
| "Why did task X fail?" | "Fix task X" *(use /bugfix)* |
| "What does the qa agent check?" | "Run qa on this code" *(use /start)* |
| "Show me retry-rate trend" | "Reduce retries" *(use /improve)* |
| "Which lessons mention auth?" | "Add new auth" *(use /feature)* |

If your question is really a request to *do* something, the answer will
nudge you toward the right skill (`/start`, `/bugfix`, `/feature`,
`/improve`, `/local-loop`, `/process-issues`).

## Output

Plain conversational answer with file references — `path:line` style —
when the answer comes from a specific file. The skill never silently
fabricates: if the answer isn't in the readable scope, it'll say "this
isn't documented in the cookbook; here's where it might be" and let you
decide.

## Notes

`/ask` is the lowest-cost way to onboard. New team members run
`/ask "walk me through what happens between team.sh start and the first
analysis.md"` and get a 3-paragraph answer that stitches together the
right files.

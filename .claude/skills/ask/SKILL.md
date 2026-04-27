---
name: ask
description: Read-only Q&A about the pipeline, your codebase, the team's runs in .state/, or anything in this cookbook. No code changes, no tasks spawned.
---

# /ask — Read-only Q&A

Usage:

```
/ask "<question>"
```

## What it does

A pure read-only mode. Answers your question by:
1. Reading relevant files in the cookbook (`.claude/`, `README.md`,
   `STATUS.md`).
2. Reading `.state/` if your question is about an in-flight or recent task.
3. Reading your project's source if helpful and within the question's scope.

It does **not**:
- Spawn agents
- Write code
- Touch `.state/` (no new tasks, no meta.json edits)
- Call `team.sh start`

## Good questions

- "What does the planner do when a task is decomposed?"
- "Show me the recent retrospective findings for the manager module."
- "Which agent runs first when I do `team.sh start`?"
- "What's the difference between reviewer and review-correctness?"
- "What did the team do on task 20260427-1432-hlt?"
- "Where do I configure the lint command for a module?"

## Not for

- "Make a fix" — use `/bugfix`.
- "Add a feature" — use `/feature` or `/start`.
- "Run the tests" — that's the tester agent; use `/start` to invoke the
  pipeline.

## Notes

The Q&A response is informational only — nothing is persisted. Re-running
the same question is safe.

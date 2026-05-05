# Learned Lessons — module-scoped append-only memory

> **These files are example templates that match the example profiles.**
> They start empty. The `retrospective` agent appends here after each
> completed task; the `tuner` agent reads here weekly and propagates
> patterns back into agent prompts and `manifest_check` items.
>
> When you rename / drop / add profiles in `.claude/profiles/`, do the
> same here — one lessons file per profile, same name with `-lessons.md`
> suffix.

## What goes in a lessons file

A lesson is reusable if it's all three:

1. **Non-obvious** — wouldn't be found by reading the code or design.
2. **Likely to recur** — applies to a class of future tasks, not just one.
3. **Actionable** — a future agent can do something different next time.

Format:

```markdown
## <task-id> (YYYY-MM-DD)
- **Pattern**: <one sentence>
- **Where**: <which agent/role this should change>
- **What to do**: <concrete prompt or profile change suggestion>
- **Evidence**: <which artifacts in this task showed it>
```

## What does NOT go here

- Obvious best practices ("validate inputs")
- One-off fixes that won't recur
- Status logs ("the developer made 3 retries")
- Verbose play-by-play summaries

The retrospective agent has a high bar for what it logs — most tasks
contribute nothing, and that's fine. A 50-task pipeline run might add
3-5 lessons total. That's what *valuable*-rate looks like.

## When you adopt this

- Match these files to your real module list. Drop the example ones.
- Don't seed them by hand; let the retrospective agent populate them as
  it sees real patterns.
- Read them weekly. Run `tuner` to fold the patterns back into
  `manifest_check` items and agent prompts.

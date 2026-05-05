---
name: bugfix
description: Bug-fix workflow. Wraps /start with a bug-shaped task and applies the Hypothesis-First Protocol — analyst leads with a stated hypothesis before any code is touched.
---

# /bugfix — Hypothesis-first bug fix

Usage:

```
/bugfix "<bug description>"
```

Examples:

```
/bugfix "Login button does nothing on mobile"
/bugfix "Worker pool deadlocks under sustained 10 RPS load"
```

## What it does

Forwards to `team.sh start` with a `bug:` prefix:

```bash
./team.sh start "bug: <description>"
```

The planner picks up the `bug:` shape and tags `task_type: bug` in
`meta.json`. The analyst then writes `analysis.md` with a Hypothesis-First
section before the rest of the pipeline runs.

## Hypothesis-First Protocol

For any bug task, the analyst's `analysis.md` must lead with:

```markdown
## Hypothesis

1. **Hypothesis**: <best guess at the root cause>
2. **Supporting evidence**: <file/log/observable behavior that fits>
3. **Contradicting evidence**: <what would make this wrong, if any>
4. **Proposed change** (one-line): <smallest fix consistent with hypothesis>
```

The architect's `design.md` then either:
- Confirms the hypothesis and writes the Sprint Contract around the proposed
  change, or
- Refutes it (with evidence) and writes a different design.

The point of Hypothesis-First is to surface assumptions early. If the
analyst's hypothesis is wrong, the architect catches it before the developer
ships a fix to the wrong thing.

## Sprint Contract template (bug)

```
- [SC-1] Root cause documented in design.md
- [SC-2] Reproducer test added (BS-N1 negative case)
- [SC-3] Fix in place; reproducer test now passes
- [SC-4] No regression on adjacent BS items
- [SC-5] Manifesto axes addressed (especially [safety] and [observability])
```

## When to skip the pipeline

For genuinely trivial fixes (typo, one-line config) where running the full
pipeline is overkill, just edit the file directly. The pipeline is designed
for changes worth designing — not for every keystroke.

## Notes

The same pipeline we use to ship features is the one we use to fix bugs —
the only difference is the prompt template. We've seen the parallel
reviewer fan-out catch subtle "fix that introduces a new bug" patterns that
single-pass reviews miss.

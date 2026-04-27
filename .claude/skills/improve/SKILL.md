---
name: improve
description: Improvement workflow — refactors, performance tuning, observability uplift. Wraps /start with an improvement-shaped task. Architect must produce a measurable Sprint Contract (before/after metric, not just "cleaner code").
---

# /improve — Improvement workflow

Usage:

```
/improve "<improvement description>"
```

Examples:

```
/improve "Replace the synchronous DB driver with the async one in the worker module"
/improve "Add distributed-trace spans to the request handler so we can see p99"
/improve "Drop dead config options that no module reads anymore"
```

## What it does

Forwards to `team.sh start` with an `improve:` prefix:

```bash
./team.sh start "improve: <description>"
```

Tagged `task_type: improvement` in `meta.json`.

## What makes an improvement task different

Improvement tasks fail when they're vague. The architect's Sprint Contract
must include **a measurable before/after**, not "cleaner code":

- "p99 latency on `/foo` drops from X ms to Y ms" — measurable
- "Test coverage on module M goes from X% to Y%" — measurable
- "Number of files importing the old helper drops from X to 0" — measurable
- "Code is more maintainable" — **NOT** a Sprint Contract item

If you can't define what "improved" means, the task isn't ready. The
analyst will push back with an Open Decision.

## Refactor vs Enhancement

The architect classifies the improvement as one of:
- **Refactor** — behavior unchanged. No new tests required unless coverage
  drops; the existing test suite must still pass.
- **Enhancement** — behavior changes. New BS items required, new tests
  required.

For performance improvements, the tester must include a before/after
benchmark in `tests.md`.

## Sprint Contract template (improvement)

```
- [SC-1] Baseline metric captured (before-state) in design.md
- [SC-2] Target metric defined (after-state expectation)
- [SC-3] Change implemented per design
- [SC-4] After-state metric measured by qa, matches target
- [SC-5] No regression on adjacent BS items
- [SC-6] Manifesto axes addressed
```

## Notes

Same pipeline as feature/bugfix — only the prompt shape changes. The
parallel reviewers catch the most common "improvement that introduced
duplication" smell.

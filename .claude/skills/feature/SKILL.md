---
name: feature
description: New feature workflow. Wraps /start with a feature-shaped task description so the planner and architect treat it as a green-field addition (design always runs, documenter is mandatory, etc.).
---

# /feature — New feature workflow

Usage:

```
/feature "<feature description>"
```

Examples:

```
/feature "Add a user-management screen"
/feature "Expose a webhook endpoint for inbound events"
```

## What it does

Forwards your description to `team.sh start` with a `feature:` prefix:

```bash
./team.sh start "feature: <description>"
```

The planner picks up the `feature:` shape and tags the task accordingly in
`meta.json` (`task_type: feature`). Downstream agents calibrate from there:

- **Architect always runs.** Even small features go through design — the
  Sprint Contract is the spec everyone is graded against.
- **Documenter is mandatory.** A new feature without doc updates fails QA.
- **Sprint Contract should include the cross-cutting items** that apply to
  all features in your project (e.g. user-facing strings localized,
  schema/migration if any, doc page added).

## Sprint Contract template (feature)

The architect builds a Sprint Contract similar to this for a feature task —
adapt the items to what your project cares about:

```
- [SC-1] All acceptance criteria met
- [SC-2] Tests pass (BS-1 ... BS-N covered)
- [SC-3] Lint/format clean (per profile)
- [SC-4] Manifesto axes addressed (performance / thread-safety / safety / observability)
- [SC-5] Localization strings added (if user-facing text)
- [SC-6] Schema or data migration added (if state shape changed)
- [SC-7] Documentation updated (API + usage)
```

## Notes

`/feature` is just a convenience over `/start`. The actual mechanics are
identical. Use `/start` directly when the task doesn't fit any of the
shape-templates (`/feature`, `/bugfix`, `/improve`).

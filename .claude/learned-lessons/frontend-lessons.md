# Frontend Module — Learned Lessons

> Non-obvious patterns and constraints extracted from closed issues.
> The retrospective agent appends here after each completed issue.

---

## Smell test is human

**Pattern**: A frontend feature can pass every agent gate (correctness
PASS, convention PASS, quality PASS, security PASS) and still fail the
"does this feel right" test that only a human can do. Component naming,
interaction affordances, error message tone, micro-loading states —
these are domain instinct, not metric.

**Where**: pipeline's `flow_type` gate; human-review-pending state.

**Fix**:
- Frontend tasks default to `flow_type = code_development` →
  `human-review-pending` is mandatory after qa_passed.
- The human reviewer pulls up the running app (or storybook), not just
  the diff.

---

## Mocked-only test trap (UI variant)

**Pattern**: Vitest passes against mocked API responses. The component
ships, hits a real API at runtime, and the response includes a field
shape the mock didn't, or a loading state the test never simulated, and
the UI throws.

**Fix**:
- For modules with an `integration_env`, the qa step covers a real-API
  smoke (Playwright / Cypress) on top of the mocked unit pass.
- Mocked-only PASS is reported as `Result: PASS-mocked` so the orchestrator
  can downgrade.

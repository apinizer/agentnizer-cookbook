# Backend Module — Learned Lessons

> Non-obvious patterns and constraints extracted from closed issues.
> The retrospective agent appends here after each completed issue.

---

## Mocked-only test trap

**Pattern**: Tester reports `Result: PASS` on a backend integration task,
but the same code fails immediately when deployed against a real vendor
sandbox or staging database. The tests covered the contract surface but
mocked every external call.

**Where**: tester profile's `test:` command; integration-env catalog.

**Reproduction**:
1. Task: "add the X external-service adapter".
2. Developer writes adapter + ships pytest tests with all HTTP responses
   mocked.
3. Tester runs the suite, all 24 tests PASS, verdict PASS.
4. QA on the staging environment: real vendor responses include fields
   the mock didn't (or status-code semantics mismatch). Adapter throws
   `KeyError` on first real call.

**Fix**:
- For modules whose profile declares an `integration_env` block, the
  tester MUST run at least one smoke test against the real environment
  in addition to the mocked unit pass.
- A purely mocked PASS without an integration smoke is reported as
  `Result: PASS (no integration smoke — verdict tentative)` so reviewer
  can downgrade.
- When the profile lists no integration env, tester's verdict cap is
  `PASS-mocked`; reviewer does not promote to `reviewed` without an
  explicit accepted-risk note.

---

## Code review missing for code-development flow

**Pattern**: Pure-agent review pipelines pass code that "looks correct on
every metric" but a human can immediately spot as wrong-fit (over-
abstraction, surprising naming, brittle coupling, missed product nuance).
Agent reviews are mechanical; the smell test is human.

**Where**: pipeline status-machine; `flow_type` gate.

**Reproduction**: A backend feature passes review (correctness PASS,
convention PASS, quality PASS, security PASS), tester PASS, qa PASS,
ships to production, and then a customer reports a UX regression that
none of the gates caught because no agent was looking from the customer's
seat.

**Fix**:
- Tasks with `flow_type=code_development` MUST pass a human-review gate
  (`human-review-pending` state). Auto-approve does not exist.
- The user inspects diff + Sprint Contract + reviews.json + tests.md +
  security.md, then drops `human-review.approved.md` or `.rejected.md`.
- For `data_processing` and `business_workflow` tasks, human review is
  optional (driven by risk_level), because their effect is observable in
  run-time data flow rather than long-lived code shape.

---

## Scoped tasks that quietly cross the auth boundary

**Pattern**: A task scoped as "add new endpoint X" silently introduces a
new auth pathway because the developer noticed the existing middleware
wouldn't handle the new flow. Reviewer doesn't catch it because the scope
crept gradually; security_reviewer flags it as MAJOR (not CRITICAL) and
the orchestrator passes it through.

**Where**: developer's deviation-escalation rule; security-reviewer severity
mapping.

**Fix**:
- Any change to authentication, RBAC, session management, or token
  handling is **always** an architect-handback (escalated deviation),
  regardless of how small the diff looks.
- Profile's `security_check.auth_changes` list enumerates the file paths
  that count as auth-boundary; any touch of those triggers an
  architect-handback rather than an in-scope developer change.
- security_reviewer escalates "auth pathway changed without architect
  approval" to CRITICAL, not MAJOR.

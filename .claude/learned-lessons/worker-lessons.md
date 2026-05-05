# Worker Module — Learned Lessons

> Non-obvious patterns and constraints extracted from closed issues.
> The retrospective agent appends here after each completed issue.

---

## Architect revision recovery instead of straight-to-failed

**Pattern**: When the developer hits its retry cap on a worker task, the
root cause is often not "the developer can't write the code" but "the
design itself is incompatible with the runtime". A naive daemon would
mark the task `failed` and stop. The recovery is to give the architect a
chance to revise.

**Where**: daemon's `spawn_agent` retry-exhausted branch; architect's
revision behaviour.

**Reproduction**: Worker task to add a new async runtime hook. Developer
cycle 1 FAILs the thread-safety review. Developer cycle 2 FAILs because
the design requires a synchronous lock the runtime doesn't provide.
Without architect revision, the task hits `awaiting_user_action` on
cycle 3 with the same root cause untouched.

**Fix**:
- On developer retry exhaustion, daemon checks
  `architect_revision_count`; if below cap (default 2), transitions to
  `design_revision_needed`, resets cycle counters + downstream
  role_done flags (developer, reviewer, tester, security_reviewer, qa).
- Architect re-runs against the cumulative findings. The new design
  starts a fresh dev cycle from a clean slate (retries reset).
- Only after architect revisions are also exhausted does the task fall
  to `awaiting_user_action`.

---

## Stale review verdict short-circuits the next cycle

**Pattern**: After `review_failed → developer retry`, the next cycle's
review phase treated the previous reviewer's PASS verdict on
`security_reviewer` as authoritative — but the developer had since
rewritten the file the security review covered. The new code went
through with stale "security PASS".

**Where**: daemon's transition handling; reviewer / tester /
security_reviewer idempotency contract.

**Reproduction**:
1. Cycle 1: reviewer FAIL, tester FAIL, security_reviewer PASS.
2. Daemon transitions `review_failed → developing`.
3. Developer rewrites parts of the diff including security-relevant code.
4. Daemon dispatches dev → review again. But security_reviewer's
   `role_done` flag is still set from cycle 1; the agent exits at the
   idempotency check.

**Fix**:
- On any `review_failed` / `tester_failed` / `qa_failed` transition,
  daemon clears `role_done` for the trio (`reviewer`, `tester`,
  `security_reviewer`) plus `qa`.
- The trio re-runs against the freshly retried developer code, not a
  stale sibling cycle.

---

## Worker flow_type and gating

**Pattern**: Worker tasks usually count as `code_development` rather than
`data_processing`, even when the user describes them as "just rerun the
pipeline differently". The runtime code is long-lived, customers feel
its effects in incident behaviour, and a human review gate catches
runtime-shape problems no agent can.

**Where**: planner's `flow_type` assignment; CLAUDE.md's flow_type rule.

**Fix**:
- If the task touches the runtime loop, scheduler, retry logic, queue
  semantics, or back-pressure handling → `flow_type = code_development`
  → mandatory human review gate.
- `data_processing` is reserved for tasks that consume the runtime
  unchanged (e.g. a one-shot import script; a backfill job; an analytics
  ETL).

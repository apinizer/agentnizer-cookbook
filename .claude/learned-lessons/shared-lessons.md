# Shared Module — Learned Lessons

> Non-obvious patterns and constraints extracted from closed issues.
> The retrospective agent appends here after each completed issue.
> Format: `## <ISSUE-KEY> (YYYY-MM-DD)` → bullet list of patterns/constraints.

---

## API-quota retry exemption (don't burn retries on non-failures)

**Pattern**: When vendor LLM APIs emit instant `rc=1` failures with patterns like "you've hit your limit" / "rate_limit_exceeded" / "credit balance is too low" / "overloaded_error", these are **not** real role failures. The subprocess never had a chance to do its work. Treating them as failures consumes the role's retry budget for the wrong reason.

**Where**: daemon's `_detect_quota_error()` (12 patterns) → if matched, set `meta.status = "awaiting_api_quota"` and **do not increment** `retry_count[role]`.

**What looks broken but isn't**: A task hits `awaiting_user_action` after only 1-2 "real" attempts because the cycle counter ran up on quota errors disguised as agent failures. You'll be tempted to bump `MAX_RETRIES`. Don't. Detect the quota error and exempt it.

**Reproduction**:
1. Set provider to a low-credit account, fire a mid-complexity task.
2. Without quota detection: developer rc=1 (credit balance too low) → retry +1 → rc=1 again → retry +2 → rc=1 → exhausted at retry 3, task stuck in `awaiting_user_action`.
3. With quota detection: developer rc=1 + pattern match → status `awaiting_api_quota`, `api_quota_blocked_at` recorded, retry counter unchanged.

**Fix**: 12 case-insensitive substring patterns plus a follow-up regex pass to extract any vendor-supplied reset time (`api_quota_reset_at`); auto-resume helper prefers the explicit reset over the fallback window.

**Cost**: zero — patterns checked only when `rc != 0`.

---

## Reset-time priority over fallback window

**Pattern**: When a vendor includes a concrete reset hint in the error body — "resets at 7:50 pm", "try again in 45 minutes", "retry-after: 600" — parse it and use it. Don't fall back to a generic 60-minute window if the vendor told you exactly when to come back.

**Where**: daemon's `_QUOTA_RESET_PATTERNS` (6 regex: `12hour_hm`, `24hour_hm`, `12hour`, `try_again_in`, `wait_n`, `retry_after`) → `_parse_quota_reset_at()` → `meta.api_quota_reset_at`.

**What looks broken but isn't**: Auto-resume "feels slow" — you watch a `phase_transition: awaiting_api_quota` Slack message and 60 minutes pass before anything moves. Vendor said "resets at 14:30 UTC" and it's already 14:31. You manually resurrect the task. Then you do it again the next day. Stop. Parse the reset hint.

**Reproduction**:
1. Vendor: `"You have hit your API limit. Resets at 7:50pm UTC."`
2. Without reset parsing: `awaiting_api_quota` waits the full 60-min window from `api_quota_blocked_at` (≈ 7:11pm + 60min = 8:11pm).
3. With reset parsing: `api_quota_reset_at = 7:53pm` (with 3-min grace); auto-resume fires at 7:53pm — 18 minutes of dead time saved.

**Fix**: For each known time-shape (12-hour with am/pm, 24-hour, "in N units"), compute UTC target + `_QUOTA_RESET_AT_GRACE_SEC=180` buffer. Auto-resume helper prefers `api_quota_reset_at` over `api_quota_blocked_at + window`.

**Cost**: ~50 lines for the regex set + parser; one regex scan when quota detection has already matched (so essentially free).

---

## Per-role timeout — the daemon's dict is the only source of truth

**Pattern**: A single global agent timeout is wrong on both ends — too tight for the heaviest role (developer, which can write 1000+ lines of code in one spawn), too loose for the lightest (planner, which writes a small JSON). Each role gets its own cap.

**Where**: daemon's `AGENT_TIMEOUT_PER_ROLE` dict, with env override pattern `LSD_AGENT_TIMEOUT_<ROLE>` (e.g. `LSD_AGENT_TIMEOUT_DEVELOPER=3600`).

**What looks broken but isn't**: You set `agent_timeout_sec_overrides` in your profile YAML and verify it parses cleanly. Then `developer` keeps timing out at 900s. You re-read the profile, the YAML is fine, the schema validates. The daemon **isn't reading it.** Profile-YAML-as-config is a documentation pattern; if the daemon's spawn loop doesn't consume it, the override dies silently. The constant in the daemon source is the single source of truth.

**Reproduction**:
1. Profile: `agent_timeout_sec_overrides: { developer: 2700 }`. Daemon hard-coded global is 900s.
2. Developer spawns, runs 14m on a heavy diff, hits the global at 15:00m → SIGTERM, retry kicks in, retry burns the budget.
3. With `AGENT_TIMEOUT_PER_ROLE["developer"] = 2700`: same task completes at 14:30m, no retry.

**Fix**: define the dict in the daemon, expose it via env overrides for production tuning. Don't try to read it from profile YAML "for symmetry" — readers expect the YAML to be a hint, not a contract.

**Cost**: 15 lines of dict + a 4-line `_role_timeout(role)` helper. One env-var per role for production overrides.

---

## Status transition completeness — undefined terminals are forbidden

**Pattern**: Every non-terminal status the daemon writes MUST appear in `STATUS_NEXT_STATUS`. An "undefined" terminal is not allowed: if a transition is missing the daemon will refuse to spawn the next role and surface the task to `awaiting_user_action` rather than silently looping.

**Where**: daemon's status state machine; the dispatcher refuses to operate on a status it doesn't recognise.

**What looks broken but isn't**: A new role gets added (or an existing role gains a new failure outcome) and tasks start mysteriously stalling. `team.sh status` shows them in some status the daemon's dispatcher returns "no next role" for. The temptation is to add a default fallback transition. Don't — that hides the bug. Make the missing entry an error.

**Reproduction**:
1. Add a new role `planner_decompose`. Status `decomposition_requested` written by architect.
2. Forget to add `STATUS_NEXT_STATUS["decomposition_requested"] = "decomposing"`.
3. Task sits in `decomposition_requested`; daemon ticks past it on every dispatch.
4. With completeness check: daemon emits a clear log warning + transitions to `awaiting_user_action` so an operator notices.

**Fix**: at module load, validate that every status produced anywhere in the codebase has a `STATUS_NEXT_STATUS` entry (or is in `TERMINAL_STATUSES` / `SPAWN_BLOCKED_STATUSES`). Refuse to start the daemon with missing entries.

**Cost**: a startup validation pass; one or two minutes to add a new status definition properly each time you extend the state machine.

---

## Architect revision cap — prevent infinite design loops

**Pattern**: When `developer` exhausts its retry cap and the failure mode looks like a design problem (same review_failed reason recurs across cycles), give the architect ONE chance to revise `design.md` and reset the downstream cycle. Cap that to 2 architect revisions per task; after that, escalate to a human.

**Where**: daemon's `MAX_ARCHITECT_REVISIONS=2`, `architect_revision_count` field on `meta.json`, `design_revision_needed` status.

**What looks broken but isn't**: A task burns through 3 developer retries and lands in `awaiting_user_action`. The diff fundamentally can't satisfy the contract because the contract itself was wrong. Without architect revision, the operator has to scope-reduce or rewrite the task by hand. With it, the architect re-reads the cumulative findings, revises `design.md`, and the developer/reviewer/tester/qa cycle resets and runs once more on the new design.

**Reproduction**:
1. Architect writes `design.md` proposing API X. Developer implements. Reviewer flags "this contradicts BS-3." Developer retry: same problem, same finding. Cycle burns.
2. Without revision: `awaiting_user_action`, operator scope-reduces.
3. With revision: cycle counters reset, architect runs once more on cumulative findings, produces revised `design.md` aligning with BS-3. Developer runs once more, all gates PASS.

**Fix**: status `design_revision_needed` triggers architect re-spawn; downstream `role_done` clears for the trio + qa + documenter + retrospective. Cap revisions at 2 to prevent infinite design loops.

**Cost**: One additional architect spawn (~ +1 Opus run worth of tokens) when triggered. Saves roughly 3 developer retries (worth a lot more than 1 architect run).

---

## Cache-aware token accounting — split observability from enforcement

**Pattern**: Track two token counters, not one. `token_used` (raw, includes `cache_read`) for observability; `token_billable_used` (excludes `cache_read`) for budget enforcement. Hard limit checks the second.

**Where**: daemon's `accumulate_real_token_usage()`; `token_billable_used` field on `meta.json`; budget enforcement at agent-spawn time.

**What looks broken but isn't**: A task FAILs at 480k of "tokens used" against a 500k hard cap. You inspect — actual dollar spend is $0.32. The task wasn't expensive, it just had a lot of cache hits. The natural reaction is "the budget mechanism is wrong" and to bump the cap. **Don't.** The mechanism is fine; counting cache reads against a hard cap was wrong.

**Reproduction**:
1. Set `meta.json.token_budget = { hard_limit: 500000 }`.
2. Run a task whose architect re-reads a 30 KB design doc 8 times. Each re-read is a cache hit.
3. Single counter: `token_used` climbs to 480k; task FAILs.
4. Two counters: `token_billable_used` (input + output + cache_creation only) stays at ~85k; task continues; real spend is $0.32.

**Why**: Anthropic prices `cache_read_input_tokens` at **roughly 10% of full input price**. A "token" in the cache-read bucket costs an order of magnitude less than a "token" in the input bucket. Treating them identically in budget math produces false-positive failures whose recovery cost (resurrect, re-scope, rerun) far exceeds what the budget was protecting against.

**Fix**:
```python
billable_delta = (
    int(breakdown.get("input", 0))
    + int(breakdown.get("output", 0))
    + int(breakdown.get("cache_creation", 0))
)
# cache_read intentionally not included
meta["token_billable_used"] += billable_delta
```

Budget check uses `token_billable_used`; status display uses `token_used` (more visceral). Both are correct for their purpose.

**Cost**: zero — same JSON parse, two counters instead of one.

---

## Cumulative findings explosion (intentional)

**Pattern**: On retry, the developer prompt includes findings from every
upstream gate that failed in the previous cycle — `reviews.json` blocking
findings, `tests.md` "Result: FAIL" tail, `security.md` FAIL section.

**Where**: daemon's retry context builder; developer / architect / qa
prompts.

**What looks broken but isn't**: Cycle 2 prompts are visibly larger than
cycle 1. The instinct is to slim them. **Don't.** Without cumulative
findings, the developer on cycle 2 only sees the single most recent gate's
handoff and silently drops earlier findings — and the same review gate
fails again with the same finding, just on a different file.

**Reproduction**:
1. First dev cycle: review-correctness FAILs with finding C-1, tester FAILs
   with BS-3 uncovered.
2. Daemon transitions `review_failed` → developer retry.
3. If only the latest handoff (tester) is shown, developer fixes BS-3 but
   ignores C-1. Review fails again.
4. With cumulative findings, developer addresses both, review passes on
   cycle 2.

**Fix**: Treat the cumulative block as a quality floor. If a finding is
genuinely out of scope for the current role, the agent leaves a
`defer:<role>` note in `reviews.json` rather than silently dropping it.

**Cost**: Each cumulative block is roughly 2–4 KB of prompt context per
retry. Across a deep retry chain this can add 10 KB+ per cycle. Worth it.

---

## Lesson file bloat

**Pattern**: A single `<module>-lessons.md` file with every retrospective
appended grows to 400 KB+ over time. Every agent that reads the lessons
file as part of its `read_allowlist` (planner, analyst, architect, retro,
qa) pays that cost on **every** spawn.

**Where**: retrospective agent's append behaviour; profile `read_allowlist`.

**Reproduction**: After ~30 closed tasks on the same module, the lessons
file crosses 100 KB. Spawning the planner now ingests it as part of project
context. Token-tracker shows the same per-spawn 30 K+ token bill on every
new task.

**Fix**:
- Cap `<module>-lessons.md` at ~30 KB.
- When the cap is reached, split: keep an `index` (one bullet per lesson
  with a stable ID) in the canonical file; move the full bodies to
  `<module>-lessons/<lesson-id>.md` and lazy-load only the IDs the current
  task references.
- The retrospective agent owns the split heuristic.

---

## Token tracking gap (bytes/4 heuristic)

**Pattern**: Approximating tokens as `bytes(stdout+stderr) / 4` undercounts
real API usage by 100x to 1000x. Cache hits, structured tool-call payloads,
and JSON-mode response wrappers are entirely invisible to the byte count.

**Where**: daemon's `_approx_tokens()`; spawn command flags.

**Reproduction**:
1. Run a task with the byte heuristic: tracker reports ~10 tokens for the
   role.
2. Run the same task with `--output-format json` and parse `usage`:
   tracker reports ~25 K tokens.
3. The byte heuristic is two orders of magnitude off.

**Fix**:
- Always spawn agents with `--output-format json`.
- Parse `usage.input_tokens`, `usage.output_tokens`,
  `usage.cache_read_input_tokens`, `usage.cache_creation_input_tokens`, and
  `total_cost_usd` from the structured response.
- Aggregate per-role and per-model in `meta.json`
  (`token_breakdown`, `token_per_role`, `model_usage`).
- Keep the byte heuristic as a fallback for parse failures only.

---

## Per-step cost blindness in multi-agent pipelines

**Pattern**: Running a multi-role pipeline without per-role token attribution
means you cannot tell which agent is the budget bottleneck. The total task
cost is visible; the per-step breakdown is not. Model selection decisions
(Opus vs Sonnet per role) are made on intuition rather than data.

**Where**: daemon's post-run accumulator; `meta.json` schema; reporting CLI.

**Reproduction**:
1. Run a 10-role pipeline (planner → analyst → architect → developer →
   reviewer trio → tester → qa → documenter → retrospective).
2. Ask: "Which role consumed the most tokens on this task?"
3. Without per-role tracking: only the task total is available. You cannot
   distinguish a 60 K-token developer cycle from a 2 K-token documenter run.
4. Three months in: you discover 70 % of spend went to roles you assumed
   were cheap, or that a role defaulted to Opus via the SDK when you
   intended Sonnet — invisible without per-model attribution.

**Why it matters for model selection**:
- Switching a role from Opus to Sonnet saves ~80 % of that role's cost.
- Without per-role data you don't know if that role is 5 % or 60 % of total
  spend — so you can't estimate the actual saving before making the change.
- Same logic applies to cache hit ratios: a role with 80 % cache reads costs
  far less than its raw token count implies; over-optimizing it wastes effort.

**Fix**:
- Accumulate in `meta.json` after every agent subprocess completes:
  - `token_per_role`: `{role_key: total_tokens}` — running total per role.
  - `model_usage`: `{model_id: {input, output, cache_read, cache_creation,
    cost_usd}}` — per-model breakdown (catches "silent Opus" defaults).
  - `token_breakdown`: task-level `{input, output, cache_read, cache_creation,
    total, cost_usd}`.
- Expose via a reporting CLI with three views:
  - `--daily`: last N days, per-day total cost + token count.
  - `--wave`: aggregate by logical batch (Wave 1, Wave 2, …) for regression
    detection across milestones.
  - `--json`: machine-readable full aggregation for dashboards / spreadsheets.
- Log pipeline path (SDK `ResultMessage.usage`) or subprocess JSON output
  (`--output-format json`) — both yield the same fields; choose based on
  whether the daemon uses SDK in-process or CLI subprocess.

**Second-order benefit**: Per-model attribution exposes "silent model
drift" — a role whose agent `.md` frontmatter says `model: sonnet` but
whose `model_usage` shows `claude-opus-*` charges. This happens when the
SDK falls back to a default that differs from the frontmatter, or when
the frontmatter was not propagated to the spawn command.

---

## API-quota crash mistaken for retry chain

**Pattern**: When the vendor API returns instant `rc=1` because the
account hit a rate limit / daily cap / overload, the subprocess never had
a chance to do its real work. If the daemon counts that as a developer (or
reviewer / architect / tester) failure, every cycle burns a retry — and
within minutes the task hits `awaiting_user_action` for the wrong reason
(retry exhausted on a side effect, not a real flaw).

**Where**: daemon's `_post_run_update`; `_detect_quota_error()` helper.

**Reproduction**: Log line like `agent finished: role=architect rc=1
elapsed=3.2s` followed by `agent finished: role=developer rc=1
elapsed=2.8s` followed by `agent finished: role=reviewer rc=1
elapsed=1.5s`. All sub-5-second exits — far too short for the role's real
work. The subprocess output contains "you've hit your limit · resets …".

**Fix**:
- Pre-emptively scan stdout/stderr for known quota patterns (12+ vendor
  variants).
- On match: do NOT bump retry counters. Set
  `meta.status = "awaiting_api_quota"` (a SPAWN_BLOCKED state),
  record `api_quota_blocked_at`, optionally `api_quota_reset_at`.
- Auto-resume helper returns the task to dispatch once the reset window
  (or explicit reset time) elapses.

---

## Sibling-archive context bloat

**Pattern**: Planner / architect prompts include "sibling tasks" from the
parent's `blocks` list as context. Without a cap, a parent that
decomposed into 6+ children causes every sibling brief to be loaded into
each child's prompt — 100 K+ tokens lost to context the agent doesn't
even need.

**Where**: planner's decompose-mode prompt; architect's design-context
loader.

**Reproduction**: Parent decomposes into 6 sibling tasks. Each sibling's
analyst prompt loads all 5 other sibling briefs. Token tracker shows
50–100 K extra context per sibling spawn.

**Fix**:
- Cap sibling-context inclusion at **3 max** (most recent or most
  topologically relevant).
- If more than 3 siblings exist, inject a short list of titles + IDs
  instead of full briefs; let the agent fetch detail on demand.

---

## Multi-protocol + multi-auth in one task

**Pattern**: Tasks that bundle multiple protocols (HTTP + gRPC + OAuth
flow + webhook signing) and multiple auth methods (basic + token + OAuth +
mTLS) into a single sub-task **always** fail the tester pass on cycle 1,
and the cumulative review findings list grows so large the developer
cannot triage it on cycle 2.

**Where**: planner's granularity rule; architect's decomposition decision.

**Reproduction**: Closed-issue history shows tasks scoped as "implement
the X integration (4 protocols, 4 auth flavours)" reaching `awaiting_user_action`
on cycle 3 after every cycle had a different protocol's tests fail.

**Fix**:
- Phase-1 minimum scope rule: **one protocol + one auth method per task.**
- Subsequent phases add the other combinations as separate tasks, each
  built on top of the now-stable phase-1 contract.
- Planner enforces; architect rejects designs that cross this boundary
  and requests decomposition.

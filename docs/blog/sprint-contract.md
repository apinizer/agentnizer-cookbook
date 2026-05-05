---
title: The Sprint Contract pattern — why one architect document determines pipeline quality
date: 2026-04-29
tags: [agentic-ai, multi-agent, claude, design-pattern, sprint-contract]
description: Why architect's design.md becomes the rubric every downstream agent is graded against, and what makes one good.
---

# The Sprint Contract pattern — why one architect document determines pipeline quality

## The 800-line design that broke the pipeline

The architect agent wrote 800 lines of `design.md`. It looked thorough. Three reviewers, the tester, the security reviewer all read it and signed off before the developer agent typed a single line of code.

Two weeks later, the developer was on its third retry. Reviewers kept marking the diff as failed because behaviors didn't match the contract. The tester kept writing tests the code refused to pass. We watched the loop spin and finally pulled the thread: the architect had specified an API field as required when downstream consumers passed it as optional. Five agents had spent two weeks grading the developer against a wrong contract. The developer had been correct. Nobody downstream had standing to question the contract — they were trained to treat `design.md` as the source of truth.

That was the day we stopped calling it "design doc" and started calling it the **Sprint Contract**. The rename wasn't cosmetic. In a multi-agent pipeline, the architect's output isn't a draft for discussion. It's the rubric every downstream agent gets graded against. If it's wrong, everything downstream is wrong in unison.

## The problem with multi-agent pipelines

Here's the failure mode nobody warned us about when we started chaining Claude subprocesses together.

In a single-prompt agent, mistakes self-correct. You reread the output, you notice the API is wrong, you reroll. Total cost: one human readthrough.

In a 13-agent pipeline, mistakes don't self-correct. They compound. The analyst's hand-wave becomes the architect's assumption. The architect's assumption becomes the developer's interface. The developer's interface becomes the tester's golden test. By the time the security-reviewer is reading line 800 of `design.md`, they're not checking whether the contract is right. They're checking whether the diff matches the contract. Those are completely different questions.

Every downstream agent inherits the upstream's mistakes silently. By stage 8 of the pipeline, you've stacked 7 layers of "close enough" and the deviation from what the user actually wanted is shocking. Worse: the pipeline looks healthy from the outside. Reviewers approved. Tests pass. Security clean. The contract just happened to be wrong, and no agent in the pipeline had a reason or a prompt to question it.

We learned to live with this by being aggressive about exactly one document: the architect's `design.md`. Everything else in the pipeline is a function of it. Get this one document right and the rest of the pipeline does its job. Get it wrong and you're paying 12 agents to rubber-stamp a mistake.

## What the Sprint Contract actually is

The Sprint Contract is `design.md` with a different mental model attached.

It's not a design document. It's not a brain-dump. It's not a "here's what I'm thinking" memo. It is the **single artifact downstream agents are graded against**. Every reviewer prompt, tester prompt, security prompt, and QA prompt cites the contract by section. When the developer's diff doesn't match the contract, the developer retries — not the contract.

Concretely, the Sprint Contract specifies:

- **API surface** — every endpoint, request shape, response shape, error shape. Not prose — types.
- **Schema deltas** — every table, column, index, migration. Not "we'll need to store users" — `ALTER TABLE` statements.
- **SPI contracts** — every plugin interface the change touches. Method signatures, lifecycle hooks, error contracts.
- **Manifest check on four axes** — for our pipeline, every design declares its position on performance, thread-safety, safety, and observability. Not "we'll think about safety" — "rate limit at 100/sec per tenant, backed by `pg_advisory_lock` keyed on `tenant_id`."
- **Behavioral Spec (BS-1..BS-N)** — testable behavioral assertions. "When request lacks `tenant_id`, return 400 with code `E_MISSING_TENANT`." Each BS becomes a tester assertion.
- **Sprint Contract invariants (REQ-X)** — properties that must hold across every code path. "REQ-1: every outbound request carries `X-Tenant-ID`."

The hard part isn't writing the words. The hard part is forcing yourself — or your architect agent — to make the contract **structural**, not narrative. Prose is escape hatch language. "We should probably validate the input" is unfalsifiable. "BS-3: when `email` is missing, return `400 E_VALIDATION` with `field=email`" is a test the tester can write today and the developer's diff either satisfies or doesn't.

## Anatomy of a good Sprint Contract

After a few months of running this pattern, our architect agent's output settled into a stable skeleton. We ship the prompt that produces it as part of the cookbook. The skeleton looks like this:

```markdown
# Technical Design: Tenant-scoped audit log

**Task ID**: EXAMPLE-142
**Module**: audit
**Risk**: medium
**Complexity**: M

## Context
Audit events today carry `user_id` but not `tenant_id`. Multi-tenant
isolation requires every audit row be filterable by tenant.

## Approach
Add `tenant_id` to the audit table, backfill from `users.tenant_id`,
and stamp every event at write time from request context.

## API surface
- `POST /api/audit/events`
  - request: `{ "actor_id": uuid, "action": str, "target": str, "meta": {...} }`
  - response: `201 { "event_id": uuid }`
  - errors: `400 E_VALIDATION`, `401 E_UNAUTH`, `409 E_DUP_EVENT`
- `tenant_id` is NOT in the request body. It is read from auth context.

## Schema deltas
- `audit_events` table:
  - `ALTER TABLE audit_events ADD COLUMN tenant_id uuid NOT NULL;`
  - `CREATE INDEX idx_audit_tenant_ts ON audit_events (tenant_id, ts DESC);`
- Backfill migration runs in batches of 10k rows.

## Manifest check (4-axis)
- **performance**: index supports the dominant query (per-tenant timeline);
  insert path uses the existing connection pool, no new pool.
- **thread_safety**: writes are append-only; no contention on shared rows.
  Backfill uses `SELECT ... FOR UPDATE SKIP LOCKED` for batch claim.
- **safety**: `tenant_id` is sourced from server-side auth context only;
  never accepted from client. Cross-tenant read attempts return 404, not 403,
  to avoid existence leak.
- **observability**: every write emits a structured log with
  `tenant_id`, `actor_id`, `event_id`. OTel span `audit.write` wraps the
  insert. Prometheus counter `audit_events_total{tenant=...}`.

## Behavioral Spec
- BS-1: `POST /api/audit/events` with valid body returns `201` and a UUID.
- BS-2: request body containing `tenant_id` is rejected with `400 E_VALIDATION`.
- BS-3: missing auth context returns `401 E_UNAUTH`.
- BS-4: duplicate `(actor_id, action, target, ts_minute)` returns `409 E_DUP_EVENT`.
- BS-5: read endpoint never returns events from other tenants, even with
  a valid `event_id` belonging to another tenant.

## Sprint Contract invariants
- REQ-1: every row in `audit_events` has non-null `tenant_id`.
- REQ-2: no code path writes `tenant_id` from the request body.
- REQ-3: every read query filters by `tenant_id` from auth context.
```

Read that and notice what's missing: nothing is fuzzy. No "we should probably." No "TBD." No prose paragraphs explaining vibes. Every claim is a thing the developer either does or doesn't, the tester either tests or doesn't, the reviewer either flags or doesn't, the security reviewer either verifies or doesn't.

The five-agent grading pipeline works because the rubric is a list, not an essay.

## Why architect uses Opus, not Sonnet

We use Opus for the architect and Sonnet for most reviewers. Some readers will assume that's premature optimization or model snobbery. It isn't.

It's a direct consequence of how compounding works in a pipeline: errors at stage N multiply across stages N+1, N+2, all the way to the end. The architect sits at stage 4 of 13. A subtle wrong call here gets graded as truth by everyone after. A subtle wrong call by review-convention at stage 9 affects exactly the next retry.

So we spend the model budget where the leverage is highest: upstream. The analyst gets Opus. The architect gets Opus. The reviewers and tester downstream are doing structured grading against an existing rubric — work that Sonnet handles competently, faster, and cheaper. Same total budget; the dollars sit where mistakes are most expensive.

If you have one Opus seat to spend in your pipeline, spend it on the architect.

## What downstream agents do with the contract

Five agents downstream of the architect, each cites the Sprint Contract by section in its prompt. We didn't add the citations as documentation. We added them because without them, agents drift.

**The developer agent** receives the contract and writes code against it. Its prompt instructs it to implement every BS-N and satisfy every REQ-X. When it can't, it logs a deviation in `progress.md` and asks for guidance instead of silently changing the contract.

**The reviewer agents** (correctness, convention, quality) each receive the diff and the contract. Their prompts say: "for each BS-N in the contract, verify the diff satisfies it. For each REQ-X, verify the invariant holds across the changed code paths." Reviewers don't review against vibes. They review against a checklist.

**The tester agent** writes one test per BS-N. Not "write tests for the feature" — "write a test asserting BS-3, write a test asserting BS-4." When the developer's diff fails BS-3, the tester knows exactly which contract item is unsatisfied and the failure message points back to the contract section.

**The security-reviewer agent** receives the contract and looks specifically at the manifest check's `safety` axis and the REQ-X invariants. "REQ-2 says no code path writes `tenant_id` from request body — verify by tracing every write site." Security review with a concrete invariant to audit is a different operation than security review without one.

**The QA agent** does the final smoke pass with the contract as the script. "BS-1 through BS-5: spot-check each one in a real run."

The contract is the rubric. Once you see it that way, you stop trying to write more eloquent design docs and start trying to write design docs your downstream graders can actually grade against.

## What goes wrong without it

The anti-pattern is what we did the first month and what most "AI-assisted dev pipeline" demos still do: vibe-coding the architect's output.

The architect agent writes a beautiful narrative. "We'll add audit logging with proper isolation. Performance is critical, so we'll add indexes. Safety considerations include cross-tenant access." Every sentence is true. None of them is testable.

The reviewer downstream reads it and says "looks good to me" — because there's nothing to grade against. Looks good is the only available verdict when the rubric is prose.

The developer writes code. The code probably resembles the design. The test agent writes tests for what it sees in the code, not what was specified. The retry loop becomes a moving target: every retry, the developer changes the code, the tester rewrites the tests, the reviewer re-reads everything, and you're not converging — you're rotating around a fuzzy center.

The pipeline runs. It looks healthy. It ships things that don't match what the human asked for, and nobody knows where the deviation entered.

## The three configurable human gates around the contract

We didn't make the pipeline fully autonomous. We left **three configurable human gates** — see [`pipeline-workflow.md`](../../pipeline-workflow.md) for the full mechanics — all clustered around the Sprint Contract or the diff that's graded against it.

**Gate 1 — Awaiting Info.** When the architect (or analyst) surfaces an open decision the agent can't resolve from project context, the daemon transitions to `awaiting_info` and halts. A human answers, the daemon resumes. This is the cheapest correction in the pipeline — a 30-second reply prevents a developer cycle of building the wrong thing.

**Gate 2 — Human Code Review.** This is the version of "design approval" that survives once you have a Sprint Contract: after the reviewer barrier passes (5-way fan-out: 3 sub-reviewers + tester + security), a human reads the diff alongside the contract. About 90 seconds for a clean diff. We're checking: does the diff implement every BS-N, does it satisfy every REQ-X, did the manifest-check axes get honoured. Mandatory for `flow_type=code_development`; configurable for other flows.

**Gate 3 — Human Final Test.** Optional in local mode (no staging), recommended in production. After the documenter PASS and CI/CD push, a human verifies the change in staging — the things no agent can verify (real I/O, third-party API behaviour, UX feel). Maybe a few minutes for a smoke pass; longer for significant changes.

Notice the asymmetry. All three gates intercept the same family of mistake — "this isn't what we wanted" — but the cost is wildly different. 30 seconds at Gate 1 buys you the same correction as 90 seconds at Gate 2 buys you the same correction as several minutes at Gate 3, because at each earlier gate less work has been done. Six agents haven't burned tokens grading against the wrong rubric. The fix is editing one document, not unwinding twelve outputs.

If you're going to have human time in the loop, spend it as early as the gate semantics allow. The Sprint Contract is what makes that possible — there's a concrete artifact to gate on at every step.

## Try this in your pipeline

You don't need our cookbook to run this pattern. You can try it tomorrow:

1. **Rename the design doc** in your prompts. Call it the Sprint Contract. The rename matters because it changes the architect agent's posture from "I'm sketching ideas" to "I'm writing the spec everyone else will be graded against."

2. **Add explicit BS-1..BS-N** to the architect's output template. Not "describe the behavior" — a numbered list of testable assertions. Force the form.

3. **Add a manifest check** with the axes that matter for your domain. For us it's performance, thread-safety, safety, observability. For you it might be latency, cost, accuracy, fairness. The point is the architect is forced to take a position on each axis, in writing, before downstream agents lock in.

4. **Make every downstream prompt cite the contract by section**. The developer prompt says "implement every BS-N." The reviewer prompt says "for each BS-N, verify the diff satisfies it." The tester prompt says "one test per BS-N." Citation forces alignment.

5. **Watch the retry rate**. We don't have a benchmark to share — too many other things changed at the same time — but the qualitative shift was unmistakable. Pipelines that used to spin three retries on a single task started landing on the first or second.

If only one of those five sticks, make it the BS-N list. That's the change with the most leverage.

## Closing

The Sprint Contract isn't a framework. It's a discipline you impose on the architect's output so the rest of the pipeline has something concrete to grade against. The pattern is older than AI agents — the API-first crowd has been writing testable contracts for two decades — but multi-agent pipelines need it more, because in a pipeline mistakes compound silently and the contract is the only place a human can intercept cheaply.

The cookbook ships an architect agent prompt with this pattern baked in: the BS-N list, the manifest check, the structural form. It's not the only way to do this. It's the way that worked for us, after burning weeks on contracts that read beautifully and graded nothing.

If you're running a multi-agent pipeline of your own, the question we'd leave you with is the one we wish we'd asked sooner: **what is the rubric your downstream agents are grading against, and is it written down?**

If the honest answer is "we hope they read the design doc," you don't have a Sprint Contract yet. You have a design doc. The difference is what determines whether your pipeline ships work that matches what was asked, or work that just looks like it does.

---

*The Sprint Contract is described in this cookbook as a pattern. Implementing it is a matter of writing your architect agent prompt to honour the contract, and your downstream agents (developer, reviewer, tester, qa) to read it as their rubric.*

# Recipe: Bug Fix (Hypothesis-First)

Walk the team through a real bug — not "make it work", but **prove the cause first, fix second**. The `bugfix` skill enforces this with a Hypothesis-First protocol so the developer cannot ship a patch without the analyst writing down a falsifiable hypothesis and the test that confirms it.

This is the recipe most teams skip and pay for later: the AI guesses, ships a plausible patch, the symptom moves, the bug stays.

## When to use this recipe

- A user-reported failure with a reproducible symptom (stack trace, 500 response, wrong number on screen).
- A flaky test that fails 1-in-N runs and you want the loop to chase root cause, not just rerun.
- An incident postmortem where the team needs a written cause-and-effect chain, not just a patch.

Skip it for typo fixes or trivial copy edits — the Hypothesis-First overhead is wasted on a one-line change.

## Scenario

The pipeline ships a fictional auth service. Production reports:

> "POST /auth/login returns HTTP 500 under concurrent load (>50 RPS).
> Single-request curl works fine. Stack trace shows
> `psycopg2.errors.UniqueViolation: duplicate key value violates unique
> constraint 'sessions_pkey'`."

The naive fix is *"catch the exception and retry"*. The Hypothesis-First protocol forces a deeper read.

## Run it

```bash
# 1. Drop the demo profile if you haven't already.
cp examples/quickstart/profiles/demo.yaml .claude/profiles/demo.yaml

# 2. Hand the team the bug report.
./team.sh start "$(cat examples/bug-fix/bug.md)"

# 3. Watch the pipeline.
watch -n 2 ./team.sh status
```

## What the team should produce

The analyst goes first and is **not allowed to write a patch plan**. Instead, `analysis.md` must contain:

1. **Symptom (observed)** — the exact failure, copy-pasted from the bug report.
2. **Hypothesis (proposed cause)** — a falsifiable statement, e.g. *"The session ID is generated client-side, two concurrent requests can collide on the primary key."*
3. **Falsification test** — the experiment that, if it fails, kills the hypothesis. *"Insert two `(client_session_id='abc')` rows in a Postgres test; observe `UniqueViolation`."*
4. **Confirmation evidence** — the trace, log line, or unit test demonstrating the hypothesis is correct.

Only after the analyst proves the cause does the architect write a `design.md` with the actual fix shape (server-generated UUIDs + `ON CONFLICT DO NOTHING` upsert).

The developer ships against the architect's design. The reviewer + tester + security trio hammer the diff — the test added by the developer **must be the falsification test from analysis.md**, demonstrating the bug existed and is now fixed.

## Files in this recipe

| File | Purpose |
|---|---|
| `bug.md` | The bug report exactly as it would arrive in your tracker |
| `expected-output/analysis.md` | What a Hypothesis-First analysis looks like (4 sections) |
| `expected-output/design.md` | Sprint Contract for the fix |
| `expected-output/progress.md` | Developer's implementation log + falsification test added |

## How to know it worked

- `analysis.md` has all four Hypothesis-First sections, in order, none skipped.
- `progress.md` references the falsification test by name and shows it passing on the patched branch and failing on the pre-patch branch.
- `reviews/correctness.json` does **not** contain a finding like *"missing root cause analysis"* — the analyst did its job.

## Adapting this to your bugs

Replace `bug.md` with your own bug report. The skill is generic — the protocol applies to logic bugs, race conditions, performance regressions, leaks. The only thing the skill refuses to do is let the developer skip step 2 (the hypothesis).

If your bug doesn't fit Hypothesis-First (e.g. flaky test root-caused to "the CI runner is slow"), the analyst will say so and the task will exit early with a documented `cannot-reproduce` or `wont-fix` outcome — that's the correct behavior, not a failure.

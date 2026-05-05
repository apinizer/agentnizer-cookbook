# Bug: POST /auth/login returns 500 under concurrent load

## Severity

HIGH — production endpoint, customer-visible, but only triggers >50 RPS

## Reported by

on-call engineer (synthetic load test caught it; real customers also reported sporadic login failures)

## Reproduction

```bash
# Single request — works
curl -X POST https://auth.example.test/auth/login \
  -d '{"email": "test@example.com", "password": "hunter2"}'
# → 200 OK, returns session token

# Concurrent requests — fails ~30% of the time
ab -n 200 -c 50 -T application/json -p login.json \
  https://auth.example.test/auth/login
# → some return 500 with stack trace below
```

## Stack trace (from one failing request)

```
File "/app/auth/handlers.py", line 142, in login
    session = create_session(user.id, client_session_id)
File "/app/auth/sessions.py", line 38, in create_session
    cur.execute(INSERT_SESSION_SQL, (session_id, user_id, ...))
psycopg2.errors.UniqueViolation: duplicate key value violates
  unique constraint "sessions_pkey"
DETAIL: Key (id)=(3f8a-...) already exists.
```

## What we know

- Single-request flow works every time.
- Failure rate scales with concurrency. At 100 RPS, ~50% of requests fail.
- The `sessions` table primary key is `id UUID`.
- The `id` value comes from the request body (`client_session_id`) when present, otherwise generated server-side.
- Mobile clients started sending `client_session_id` in the v3.2 release for offline resume; the bug appeared the same week.

## What we tried (and why it didn't help)

- **Adding a retry on `UniqueViolation`** — masks the symptom, but the second request still has the same `client_session_id`, so it just fails the retry too.
- **Increasing the connection pool** — no effect; this isn't a connection exhaustion issue.
- **Reading the v3.2 mobile changelog** — confirmed the new field but no specific generation algorithm is documented.

## What we need from the pipeline

1. A written hypothesis of the cause (not a guess in chat — a falsifiable statement in `analysis.md`).
2. A test that demonstrates the cause before any code is changed.
3. A fix that addresses the cause, not the symptom.
4. Confirmation that the test fails on the pre-fix branch and passes on the post-fix branch.

The on-call wants this in the postmortem doc — so the trail (hypothesis → test → fix) needs to live in `analysis.md`, `design.md`, `progress.md` cleanly.

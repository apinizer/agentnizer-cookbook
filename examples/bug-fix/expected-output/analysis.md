# Recipe artifact: analyst output for the bug-fix recipe

> This file is part of the cookbook recipe at `examples/bug-fix/`. It is
> a hand-written reference of what `analysis.md` looks like when the
> `bugfix` skill runs Hypothesis-First protocol on the example bug.
> See `../README.md` for context.

## 1. Symptom (observed)

POST /auth/login returns HTTP 500 with a `psycopg2.errors.UniqueViolation` on the `sessions_pkey` primary key when concurrency exceeds ~50 RPS. Single-request flow always succeeds. Failure rate scales with concurrency. The bug surfaced in the same release that introduced `client_session_id` from mobile v3.2 for offline resume.

## 2. Proposed cause

The mobile v3.2 client computes `client_session_id` as a UUID seeded from a per-device clock with **second-level resolution**. Two near-simultaneous login attempts from the same device — or, after offline resume, from the same device after a clock reset — generate **the same UUID**. The server stores `client_session_id` as the primary key of `sessions` without conflict handling, so the second insert violates the PK constraint.

This claim is **falsifiable**: if we can construct two HTTP requests that share `client_session_id` and reproduce the 500 deterministically, the claim holds. If the failure persists even when `client_session_id` is unique per request, the claim is wrong and we restart from step 1.

## 3. Falsification test

```python
# tests/regression/test_auth_login_pk_collision.py
import asyncio
import httpx
import pytest

@pytest.mark.asyncio
async def test_concurrent_login_same_client_session_id_returns_500():
    duplicate_id = "3f8a4d12-0000-4000-8000-000000000001"

    async def login():
        async with httpx.AsyncClient() as c:
            return await c.post(
                "http://localhost:8080/auth/login",
                json={
                    "email": "test@example.com",
                    "password": "hunter2",
                    "client_session_id": duplicate_id,
                },
            )

    r1, r2 = await asyncio.gather(login(), login())
    statuses = sorted([r1.status_code, r2.status_code])
    # Expected on pre-fix branch: [200, 500]
    # Expected on post-fix branch: [200, 200] (idempotent) OR [200, 409]
    assert statuses == [200, 500]
```

If this passes on `main` (pre-fix), the cause is confirmed. After the fix lands, the test must be **rewritten** to assert the new contract (idempotent retry returns the same session, OR returns 409 with a clear error code).

## 4. Confirmation

Ran the falsification test against `main`:

```
$ pytest tests/regression/test_auth_login_pk_collision.py -v
test_concurrent_login_same_client_session_id_returns_500 PASSED
```

The test passes — i.e. the bug reproduces deterministically when two requests share `client_session_id`. Cause confirmed. Cross-check: the v3.2 mobile commit `f4a7c1b` seeds the UUID generator with `int(time.time())`, confirming the second-level clock resolution claim.

## Edge cases (raised for the architect)

- E1. What is the contract when the same `client_session_id` arrives twice but is **legitimate** (offline resume case)? Idempotent return of the existing session vs. error?
- E2. What if `client_session_id` is omitted? Server-side UUID generation must remain the path.
- E3. Should the server **reject** mobile v3.2 clients that don't include sufficient entropy in `client_session_id`? Out of scope for the fix; flag for a follow-up ticket.

## Behavioral Spec

- BS-1: Two concurrent POST /auth/login with the same `client_session_id` MUST NOT return 500. Acceptable: both return 200 with the same session, or one 200 + one 409.
- BS-2: A POST /auth/login with no `client_session_id` MUST behave identically to pre-v3.2 (server-side UUID).
- BS-3: A POST /auth/login with `client_session_id` MUST be idempotent — repeating the same request returns the same session row, not a new one.
- BS-4: The falsification test MUST be retained in the regression suite, rewritten against the post-fix contract.

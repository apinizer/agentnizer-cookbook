# Design — auth/login PK collision fix

> Owner: architect (Opus)
> Sprint Contract — single source of truth for downstream agents.
> Inputs: `analysis.md` (BS-1..BS-4, edge cases E1..E3).

## SC-1. API contract (no change)

POST /auth/login request/response shape is **unchanged**. Mobile clients keep sending `client_session_id`; legacy clients keep working.

## SC-2. Server-side resolution (the fix)

The server treats `client_session_id`, when present, as a **client-supplied idempotency key**, not as the row's primary key. The PK is always a server-generated UUIDv4.

```sql
ALTER TABLE sessions
  ADD COLUMN client_session_id TEXT,
  ADD CONSTRAINT sessions_client_session_id_user_uniq
    UNIQUE (user_id, client_session_id);

-- existing PK column 'id' stays as server-generated UUID
```

## SC-3. Insert path

```python
# auth/sessions.py
def create_session(user_id: UUID, client_session_id: str | None) -> Session:
    if client_session_id is None:
        # Pre-v3.2 path: server generates everything.
        return _insert_new_session(user_id, client_session_id=None)

    # v3.2+ path: idempotent.
    existing = _find_session_by_client_id(user_id, client_session_id)
    if existing:
        return existing  # BS-3
    try:
        return _insert_new_session(user_id, client_session_id)
    except UniqueViolation:
        # Two concurrent requests raced; the loser reads the winner's row.
        return _find_session_by_client_id(user_id, client_session_id)  # BS-1
```

## SC-4. Concurrency contract

- The `try/except UniqueViolation` + reread is the canonical race-loser pattern; we use it here rather than `INSERT ... ON CONFLICT` to keep the SQL portable across the test harness.
- Reread MUST be in the same transaction as the failed insert (re-acquire row, return identical session).
- No retries beyond the one reread — if the reread also fails, that's a real bug, propagate the 500.

## SC-5. Rollback safety

The migration is additive: new column + new unique constraint. No existing rows are touched. Pre-fix sessions have `client_session_id = NULL` and are unaffected by the new constraint (unique constraint on `(user_id, NULL)` does not collide with itself in Postgres).

Rollback plan: `ALTER TABLE sessions DROP COLUMN client_session_id` reverses cleanly. Already-deployed mobile v3.2 clients fall back to server-generated IDs.

## SC-6. Falsification test rewrite

The analyst's `test_concurrent_login_same_client_session_id_returns_500` becomes:

```python
async def test_concurrent_login_same_client_session_id_is_idempotent():
    # ... same setup ...
    r1, r2 = await asyncio.gather(login(), login())
    assert r1.status_code == 200 and r2.status_code == 200
    body1, body2 = r1.json(), r2.json()
    # BS-3: same session returned for the same client_session_id
    assert body1["session_id"] == body2["session_id"]
```

The test is moved into `tests/regression/` so it lives forever, not in the bug ticket folder.

## SC-7. Observability

- Log a structured event `auth.session.client_id_race` whenever the `UniqueViolation` reread path is taken. Counter goes to Prometheus. Alert fires only if rate > 1 per second per pod (race is expected; flood is suspicious).

## SC-8. Out of scope (deferred)

- E3 (rejecting low-entropy mobile clients) → follow-up ticket.
- Audit table for "session reused due to idempotency key" — discussed, not now.

## SC-9. Acceptance

- BS-1..BS-4 all pass under integration tests.
- The rewritten falsification test fails on `main` (pre-fix) and passes on the patched branch — proven by CI matrix.
- Migration applied + rolled back cleanly in a sandbox before being signed off for prod.

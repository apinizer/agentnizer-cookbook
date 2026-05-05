# Reference: Sprint Contract for the invitation flow

> This is a hand-written excerpt showing what `design.md` should look like
> for a feature task at this scope. Real runs produce structurally similar
> contracts with different exact wording.
>
> The full architect output also includes ADR-style rationale, rollback
> plan, and out-of-scope items — trimmed here to keep the example
> focused on the contract shape.

## SC-1. API contract

```yaml
# OpenAPI fragment — committed to docs/api/invites.yaml
paths:
  /invites:
    post:
      requestBody: { schema: { type: object, properties: { email: {type: string, format: email}, role: {type: string} }, required: [email, role] } }
      responses:
        201: { schema: { type: object, properties: { invite_id: {type: string}, expires_at: {type: string, format: date-time} } } }
        409: { description: "Pending invite already exists for this email" }

  /invites/{token}:
    get:
      responses:
        200: { schema: { type: object, properties: { email: {}, invited_by: {}, expires_at: {}, status: {} } } }
        404: { description: "Token unknown" }
        410: { description: "Token expired" }

  /invites/{token}/accept:
    post:
      requestBody: { schema: { type: object, properties: { password: {type: string, minLength: 12}, name: {type: string} } } }
      responses:
        201: { description: "User created, session returned" }
        410: { description: "Token expired or already used" }
        422: { description: "Password did not meet strength requirements" }

  /invites/{invite_id}:
    delete:
      responses:
        204: { description: "Revoked (idempotent)" }
        409: { description: "Invite already accepted or expired — cannot revoke" }
```

## SC-2. Database schema

```sql
CREATE TABLE invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email CITEXT NOT NULL,
    role TEXT NOT NULL,
    invited_by UUID NOT NULL REFERENCES users(id),
    token_hash BYTEA NOT NULL UNIQUE,    -- store hash, not token
    status TEXT NOT NULL CHECK (status IN ('pending','accepted','revoked','expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at TIMESTAMPTZ
);

-- One pending invite per email (concurrency-safe via partial unique).
CREATE UNIQUE INDEX invites_one_pending_per_email
  ON invites (email)
  WHERE status = 'pending';

CREATE INDEX invites_token_hash_idx ON invites (token_hash);
```

The token itself is **never stored** — only `sha256(token)` is. Comparison uses `hmac.compare_digest`.

## SC-3. State machine

```
                       ┌──> accepted   (user set password; terminal)
pending  ─── 7 days ──>│
                       ├──> revoked    (admin DELETE; terminal)
                       └──> expired    (lazy: computed on read if past expires_at)
```

`expired` is **derived**, not written, on the GET path; a daily background sweep can flip it to actual rows for cleanup, but the API does not depend on the sweep.

## SC-4. Concurrency contract

- POST /invites uses `INSERT ... ON CONFLICT (email) WHERE status='pending' DO NOTHING RETURNING id` — race-loser reads the existing row and returns 409.
- POST /invites/{token}/accept uses `UPDATE ... WHERE status='pending' AND expires_at > NOW() RETURNING *`; if zero rows updated, returns 410. No explicit lock needed — the WHERE clause is the lock.

## SC-5. Security contract

- Token: `secrets.token_urlsafe(32)` (256 bits).
- Lookup: hash the inbound token with sha256, compare with `hmac.compare_digest`.
- Token never appears in logs; only `invite_id` is logged.
- Email is constant-time-compared at the application layer when matching.
- Password strength: min 12 chars, must include lowercase, uppercase, digit. Common-password check via local list (top-1000), no network call.

## SC-6. Test plan summary (handed to tester)

- BS-1: happy path (create → email logged → GET → accept → user created)
- BS-2: expired token (rewind time 8 days, GET returns 410, accept returns 410)
- BS-3: revoke pending (admin DELETE, then GET returns "revoked", accept returns 410)
- BS-4: re-invite same email while pending (POST /invites returns 409 with existing invite_id)
- BS-5: re-invite same email after acceptance (POST /invites returns 201 — accepted is not blocking)
- BS-6: weak password (POST accept returns 422 before user creation)

## SC-7. Observability

Counters: `invite.created`, `invite.accepted`, `invite.revoked`, `invite.expired_landing`, `invite.weak_password_rejected`.
Structured logs on every state transition: `{event, invite_id, email_hash, actor_id}`. Email is hashed in logs to prevent PII spill.

## SC-8. Out of scope (deferred)

- Real SMTP/SES integration — `services/email.py` is a stub.
- Email branding, HTML templates, multi-language — UX track, not this ticket.
- Rate limiting on POST /invites — separate platform concern.

## SC-9. Acceptance

- All BS-1..BS-6 pass under the test suite.
- `reviews/security.json` reports zero CRITICAL findings.
- OpenAPI YAML committed and validates against the schema linter.
- Migration applied + rolled back cleanly in a sandbox.

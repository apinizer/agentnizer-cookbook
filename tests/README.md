# Tests

21 unit tests covering the daemon's state machine, role lookup, and FAIL-outcome detection.

```bash
uv run --with pytest --with pytest-asyncio pytest tests/
```

## Scope

The suite tests **pure-logic surfaces** of `pipeline-daemon.py`:

- State-machine transitions (`STATUS_NEXT_STATUS`, `next_actions`, terminal/spawn-blocked sets)
- Role configuration constants (`MODEL_FOR_ROLE`, `DEFAULT_MAX_RETRIES`, the linear/parallel role lists)
- Idempotency contract (`role_done` short-circuit)
- Retry-cap math (`retries_exceeded`)
- Cache-friendly stable prompt prefix (`_stable_prefix_for_role`)
- FAIL-outcome detection on `tests.md` / `qa.md`

It deliberately does **not** test:

- Real subprocess spawning (we don't want to call out to the `claude` CLI in unit tests)
- Filesystem-watched dispatch loops
- Full task lifecycles end-to-end

For end-to-end validation, run `examples/quickstart/` against a real Claude account and observe `.state/` evolve.

## Adding tests

Follow the existing single-file conventions:

- One module-level `daemon` fixture that loads `pipeline-daemon.py` once via `importlib.util`.
- Function-level test names describe the behaviour, not the constant: `test_review_failed_routes_back_to_developer`, not `test_status_next_status_dict`.
- Use `tmp_path` for any test that touches files.
- Skip integration coverage that requires a network or CLI call.

## Coverage gaps (open invitations)

Highest-leverage tests still missing:

- `test_role_timeout_per_role_lookup` — `_role_timeout("developer")` returns 2700 by default; `LSD_AGENT_TIMEOUT_DEVELOPER=999` env override applies.
- `test_role_max_output_per_role_lookup` — `_role_max_output("developer")` returns 56000; env override applies.
- `test_quota_reset_regex_patterns` — exercise each of the 6 patterns in `_QUOTA_RESET_PATTERNS` (`12hour_hm`, `24hour_hm`, `12hour`, `try_again_in`, `wait_n`, `retry_after`).
- `test_move_to_completed_harvests_token_fields` — write a fake task into `tmp_path/.state/tasks/<id>/`, run `move_to_completed`, assert `token_billable_used` and `architect_revision_count` land in `completed.jsonl`.

PRs adding any of these are welcome.

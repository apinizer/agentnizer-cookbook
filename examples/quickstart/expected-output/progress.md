# Progress: Add a tiny hello-world function

> Task ID: 20260427-1432-hlw | Module: demo | Retry: 0/3

## Files Changed
- `examples/quickstart/code/hello.<ext>` — new, ~22 LOC, the function
  with strip+fallback for empty/whitespace names
- `examples/quickstart/code/test_hello.<ext>` — new, ~18 LOC, four test
  stubs covering BS-1, BS-2, BS-N1, BS-N2

## Tier Deviations (if any)

*(none — implementation matches design.md as written)*

## Sprint Contract Status

- [x] SC-1 — `hello("Mustafa")` returns `"Hello, Mustafa, the time is …"` ✓
- [x] SC-2 — `hello()` returns `"Hello, world, …"` ✓
- [x] SC-3 — `hello("")` falls back to "world" ✓ (`stripped or "world"` pattern)
- [x] SC-4 — `hello("   ")` falls back to "world" ✓ (same pattern)
- [x] SC-5 — UTC ISO-8601 timestamp with `Z` suffix ✓
- [x] SC-6 — Structured log line `event=hello.called` with
  `name_provided: bool` ✓
- [x] SC-7 — No new dependencies (uses stdlib datetime + project's
  existing logger only) ✓
- [x] SC-8 — Lint clean (demo profile lint = `echo` so trivially passes
  in this run; also clean against project linter when adapted) ✓
- [x] SC-9 — Test stubs in place for all four BS items ✓

## BS Coverage (test stub level)

- [x] BS-1 happy path → `test_hello_with_name`
- [x] BS-2 default name → `test_hello_default_name`
- [x] BS-N1 empty-string name → `test_hello_empty_string_falls_back`
- [x] BS-N2 whitespace-only name → `test_hello_whitespace_falls_back`

## Manifest Check

- [performance] N/A — single function, no I/O, no hot path
- [thread-safety] No mutable state; pure function. ✓
- [safety] Empty + whitespace inputs both normalized to default;
  no concatenation of untrusted input into formatted strings beyond
  the greeting (no SQL/shell/HTML downstream). ✓
- [observability] Single structured-log line per call with
  `name_provided` boolean — sufficient for "is this thing being called"
  monitoring without leaking the name itself. ✓

## Quick Lint

- `echo "[demo] lint OK — 0 errors, 0 warnings"` → exit 0

## Open Questions for Reviewer / Tester

- The structured log line includes only `name_provided: bool`, NOT the
  name itself. Confirmed with security-reviewer that this is the
  right call (don't log user-supplied data unless explicitly required).

# Technical Design: Add a tiny hello-world function

**Task ID**: 20260427-1432-hlw
**Module**: demo
**Risk**: LOW
**Complexity**: S

## Context

The quickstart task validates the pipeline can run end-to-end on a fresh
checkout. The function itself is trivial; the value is in confirming
every agent in the pipeline produces well-shaped output for a real
(if small) request.

The analyst raised one edge case that's worth being explicit about: the
empty-string name should fall back to the default. This is the kind of
edge case that's *trivial to forget* and *trivial to test* — exactly
what the BS items are for.

## Approach

A single pure function in the demo module's source directory. No state,
no I/O beyond a single structured log line. UTF-8 friendly; UTC-only
time formatting.

A whitespace-strip + empty-after-strip → fallback pattern handles BS-N1
and BS-N2 in one branch.

## Implementation Plan

### Files to Create
- `examples/quickstart/code/hello.<ext>` — the function (~25 LOC,
  including the structured log line)
- `examples/quickstart/code/test_hello.<ext>` — unit-test stubs
  for BS-1, BS-2, BS-N1, BS-N2 (~15 LOC)

### Files to Modify
- *(none)*

### Files NOT to Touch
- Anything outside `examples/quickstart/code/` — the demo profile claims
  this path only

## Sprint Contract

- [SC-1] `hello("Mustafa")` returns a string starting with `"Hello, Mustafa,"` (BS-1)
- [SC-2] `hello()` returns a string starting with `"Hello, world,"` (BS-2)
- [SC-3] `hello("")` returns a string starting with `"Hello, world,"` — NOT `"Hello, ,"` (BS-N1)
- [SC-4] `hello("   ")` returns a string starting with `"Hello, world,"` (BS-N2)
- [SC-5] Return value contains a UTC ISO-8601 timestamp ending in `Z`
- [SC-6] One structured log line emitted per call, `event=hello.called`,
  with `name_provided: bool` field (true when the caller passed a non-empty
  name; false when fallback was used) [observability]
- [SC-7] No new dependencies introduced
- [SC-8] Lint clean per the demo profile
- [SC-9] Tests for BS-1, BS-2, BS-N1, BS-N2 all pass

## Open Decisions Resolved

- [OD-1]: Structured log on call?
  → **Decision**: yes, one log line, fields as in [SC-6]. Cheap
  observability win and lets us catch regressions in tests later if
  needed.

## Risk Notes

This task is intentionally low-risk. The only thing reviewers should
push back on is missing the empty-name fallback — that's the one
non-obvious correctness point.

## Sub-task Decomposition

*Not required — single-module S complexity task.*

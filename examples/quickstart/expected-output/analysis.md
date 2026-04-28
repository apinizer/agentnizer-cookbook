# Analysis: Add a tiny hello-world function to the demo module

**Task ID**: 20260427-1432-hlw
**Module**: demo
**Risk**: LOW
**Complexity**: S
**Manifesto Axes**: [safety, observability]
**Pre-Check**: CONFIRMED

## Requirements

- [REQ-1] A function `hello(name: str = "world") -> str` that returns a
  greeting string and the current ISO-8601 UTC timestamp.
- [REQ-2] When `name` is the empty string `""`, treat it as the default
  ("world") rather than producing `"Hello, , the time is ..."`. [safety]
- [REQ-3] Return value must be a single string (not a tuple, not a dict)
  for callers' simple consumption.
- [REQ-4] Lives in the demo module's source directory (the demo profile
  claims `examples/quickstart/code/**`).

## Edge Cases

- [EC-1] Empty-string name → fall back to "world". [safety]
- [EC-2] Whitespace-only name (e.g. `"   "`) → strip first; if empty
  after strip, fall back to "world". [safety]
- [EC-3] Very long name (10k+ chars) → no panic, no truncation here;
  caller's responsibility. [safety]
- [EC-4] Non-ASCII name → handle as UTF-8; do not encode-mangle. [safety]
- [EC-5] Time-zone consistency → always emit UTC, ISO-8601 with `Z`
  suffix; do not depend on local timezone. [observability]

## Open Decisions

- [OD-1] Should the function emit a structured-log line on call?
  Resolved at design: yes — emits a single `event=hello.called`
  log record with `name_provided` boolean field. [observability]

## Behavioral Specification

### [BS-1] Happy path with explicit name
- Given: `hello("Mustafa")` is called
- When:  the function returns
- Then:  the result is a string starting with `"Hello, Mustafa,"` and
         containing a UTC ISO-8601 timestamp ending in `Z`
- Manifest: [observability]

### [BS-2] Default name
- Given: `hello()` is called with no arguments
- When:  the function returns
- Then:  the result starts with `"Hello, world,"`
- Manifest: [safety]

### [BS-N1] Empty-string name (negative)
- Given: `hello("")` is called
- When:  the function returns
- Then:  the result starts with `"Hello, world,"` (NOT `"Hello, ,"`)
- Manifest: [safety]

### [BS-N2] Whitespace-only name (negative)
- Given: `hello("   ")` is called
- When:  the function returns
- Then:  the result starts with `"Hello, world,"`
- Manifest: [safety]

## Constraints

- **Manifesto**: safety (input handling), observability (log line)
- **Tech stack**: whatever the demo module uses (this cookbook's quickstart
  is stack-agnostic; the demo profile's `lint` and `test` are `echo`s)
- **Cross-module**: none

## Affected Files (estimate)

- `examples/quickstart/code/hello.py` (or `.go` / `.ts` / `.rs` — match
  whatever language the user is exercising) — new file, ~25 LOC
- `examples/quickstart/code/test_hello.py` (or equivalent) — new file,
  ~15 LOC, two test cases (happy + empty-name)

## Notes for Architect

- This is a deliberately tiny task to validate the pipeline runs.
- The "empty string falls back to world" rule is the only thing that
  would surprise a careless implementation; flag it explicitly in the
  Sprint Contract.
- BS-N2 (whitespace-only) is bonus — design can choose to include or
  defer.

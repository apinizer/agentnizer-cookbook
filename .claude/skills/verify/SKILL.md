---
name: verify
description: Lightweight self-check — runs each touched module's lint/build commands as configured in its profile YAML. Not a full review; just "did I break something obvious?"
---

# /verify — Quick self-check

Usage:

```
/verify                  # auto-detect modules from `git diff --name-only`
/verify <module>         # check one specific module
/verify <m1> <m2>        # check several
```

## What it does

For each module to check:

1. Reads `.claude/profiles/<module>.yaml`.
2. Runs the `lint:` command (if present).
3. Runs the `build:` command (if present and `quick_build: true` in the
   profile, otherwise skipped).
4. Reports per-module PASS/FAIL with the linter's output.

This is **not** a code review. It's a "did I break something obvious before
handing it to the team" sanity check. The actual review is done by
`review-correctness`, `review-convention`, `review-quality` (and the
tester) when the daemon runs.

## Module detection

`git diff --name-only` is mapped against profile `paths` patterns:

```yaml
# .claude/profiles/<module>.yaml
paths:
  - "apps/<module>/**"
  - "libs/<module>/**"
```

If no profile claims a changed file, `/verify` reports "no profile matched"
for that file (so you don't silently skip it).

## Output

```
VERIFY REPORT
=============
[PASS] shared       — lint OK (3s)
[PASS] worker       — lint OK (5s)
[FAIL] frontend     — lint: 3 errors, 2 warnings
       <file>:<line> — <rule> — <message>
       <file>:<line> — <rule> — <message>

Total: 2 PASS, 1 FAIL
```

## On FAIL

The skill lists the linter's errors verbatim. It does **not** offer to fix
them — that's your job, or the developer agent's. (`/verify` is a check, not
a fixer.)

## Notes

`/verify` is stack-agnostic — it runs whatever command your profile says.
We use it as a pre-`team.sh start` sanity check when we've made a quick
manual change and want to confirm we didn't introduce a lint error before
spawning the team.

---
name: verify
description: Lightweight self-check — runs each touched module's lint/build commands as configured in its profile YAML. Not a full review; just "did I break something obvious?"
---

# /verify — Quick self-check

A pre-flight: lint + (optionally) build the modules whose files you've
changed, before handing them to the pipeline. Catches obvious syntax /
typecheck / lint errors in seconds rather than minutes-into-review.

This is **not** the code review. The actual review happens inside the
pipeline (`review-correctness` + `review-convention` + `review-quality` +
`tester`). `/verify` is just the developer's local sanity check.

## Usage

```
/verify                  # auto-detect modules from `git diff --name-only`
/verify <module>         # check one specific module
/verify <m1> <m2>        # check several at once
```

## What it does

For each module to check:

1. Read `.claude/profiles/<module>.yaml`.
2. Run the `lint:` command (if present).
3. Run the `build:` command — only if `quick_build: true` is set in the
   profile (otherwise skipped, since builds can be slow).
4. Capture stdout / stderr / exit code per module.
5. Report PASS / FAIL per module with a per-finding breakdown when FAIL.

## Module detection

`git diff --name-only` is mapped against each profile's `paths` patterns:

```yaml
# .claude/profiles/<module>.yaml
paths:
  - "apps/<module>/**"
  - "libs/<module>/**"
```

If a changed file matches no profile, `/verify` reports "unknown module"
for that file (so you don't silently skip it). If a file matches multiple
profiles, the more specific path pattern wins (longest path-prefix match).

## Output

```
VERIFY REPORT
=============
Time: 2026-04-27T14:32:00Z
Detected modules: shared, worker, frontend

[PASS] shared       — lint OK (3.2s)
[PASS] worker       — lint OK (5.1s)
[FAIL] frontend     — lint: 3 errors, 2 warnings (4.7s)
       <file>:<line>:<col> — <rule> — <message>
       <file>:<line>:<col> — <rule> — <message>
       <file>:<line>:<col> — <rule> — <message>

Total: 2 PASS, 1 FAIL — 12.9s
```

## On FAIL

`/verify` lists the linter's errors verbatim. It does **not** offer to
fix them — that's the developer's job (or the developer agent's, when the
pipeline runs). `/verify` is a check, not a fixer.

If you'd like the team to fix the lint errors instead of you, run
`/bugfix "lint errors in <module>: <paste of failures>"` and let the
pipeline handle them.

## Common scenarios

| Scenario | What to expect |
|---|---|
| `/verify` with no changes | "No changed files detected. Nothing to verify." |
| `/verify` and one of the changed files matches no profile | Reports the file with `[?] no profile matched` — you should add or extend a profile's `paths` |
| Profile has `lint:` but command isn't installed | `[FAIL] <module>` with stderr showing the not-found error — install the tool |
| `quick_build: true` and build is slow | Verify takes longer; if too slow, set `quick_build: false` |

## What it does NOT do

- ❌ Run the test suite (that's the tester agent's job in the pipeline)
- ❌ Spawn any agent
- ❌ Touch `.state/`
- ❌ Modify any file
- ❌ Auto-fix lint findings

## Notes

We use `/verify` as a pre-`team.sh start` sanity check after manual
edits. If your manual change has an obvious lint or syntax error,
catching it here saves the developer agent from wasting a retry cycle on
something you already know is broken.

# Agents

**14 production agent prompts. Run as-is. Specialize via profiles + lessons.**

The 14 definitions in this directory are the real prompts the maintainers run, anonymized. They run end-to-end on a real task today. You specialize them by editing **profiles** and **lessons** files (which they read), not by rewriting the prompts themselves.

What's deliberately project-specific (and lives outside the prompts):
- Coding conventions, common pitfalls → `.claude/profiles/<module>.yaml` `manifest_check` axes
- Module gotchas → `.claude/learned-lessons/<module>-lessons.md` (retrospective writes here, agents read)
- Build / test / lint commands → `.claude/profiles/<module>.yaml` `commands` block

Result: when you bump versions of the cookbook, your specifics survive.

## File map

| Agent | Model | Output | Role |
|---|---|---|---|
| `planner.md` | Sonnet | `meta.json`, `active.json` | Splits user task into sub-tasks; builds DAG |
| `analyst.md` | Opus | `analysis.md` | Requirements + edge cases + Behavioral Spec |
| `architect.md` | Opus | `design.md` | Sprint Contract + design decisions |
| `developer.md` | Opus | `progress.md` + code | Implements; quick lint |
| `reviewer.md` | Sonnet | `reviews.json` | Aggregates the 5 verdicts (3 sub-reviews + tester + security) |
| `review-correctness.md` | Opus | `reviews/correctness.json` | Bugs / BS coverage / manifesto |
| `review-convention.md` | Sonnet | `reviews/convention.json` | Lint / format compliance |
| `review-quality.md` | Sonnet | `reviews/quality.json` | DRY / SRP / complexity / dead code |
| `tester.md` | Sonnet | `tests.md` | Full test run + BS coverage check |
| `qa.md` | Opus | `qa.md` | E2E + smoke + UX (system-level) |
| `security-reviewer.md` | Opus | `reviews/security.json` | OWASP + module-specific security |
| `documenter.md` | Sonnet | `docs.md` + project docs | API contract + usage docs |
| `retrospective.md` | Sonnet | `learned-lessons/<module>-lessons.md` | Distills patterns |
| `tuner.md` | Sonnet | weekly proposal under `.state/tuner/<week>/` | Reads lessons; proposes one targeted prompt patch per week. Propose-only — never auto-applies. |

## How they connect

See [`../../pipeline-workflow.md`](../../pipeline-workflow.md) for the full flow — Mermaid diagram, state machine, retry semantics, three-gate human review.

## Severity taxonomy (cross-reviewer contract)

Every reviewer agent (correctness, convention, quality, security) emits findings with one severity: **CRITICAL**, **HIGH**, **MEDIUM**, or **LOW**. The orchestrator (`reviewer.md`) aggregates by counting:

- **CRITICAL ≥ 1** → unconditional FAIL
- **HIGH ≥ 3** → FAIL
- Otherwise → PASS

`MEDIUM` and `LOW` findings are reported but don't block the verdict. (Some older docs use the term "MAJOR"; treat it as a synonym for **HIGH**.)

## Idempotency contract (non-negotiable)

Every agent's first step is:

> *Is `meta.json.role_done.<me>` set with a timestamp? If yes, exit immediately.*

This is what makes daemon crash-resume work. Don't skip it when you write a new agent or modify an existing one. The daemon test suite catches obvious violations; subtle ones (the agent runs but does nothing useful) won't be caught automatically.

## Specialization recipe

1. **Don't rewrite the agent prompts.** The structure, I/O contract, and idempotency checks are load-bearing.
2. **Edit the profile YAML** — module-specific manifesto items, paths, build/test/lint commands.
3. **Let `retrospective` populate** `.claude/learned-lessons/<module>-lessons.md` over your runs.
4. **Approve weekly tuner proposals** in `.state/tuner/<week>/proposal.md` to fold lessons back into the prompts.

# Agents — surface-level skeleton

> **Read this before diving in.** The 14 agent definitions in this directory
> (`planner`, `analyst`, `architect`, `developer`, `reviewer` + 3 sub-reviewers,
> `tester`, `qa`, `security-reviewer`, `documenter`, `retrospective`, `tuner`)
> are **surface-level versions** of what we actually run.
>
> What's real:
> - The role decomposition (who does what, in what order)
> - The `.state/` contract (what each agent reads + writes)
> - Idempotency (`role_done.<role>` checks at the top of every agent)
> - Manifesto axes + Sprint Contract pattern
> - Status state machine + retry caps
> - The model-tier rationale (Opus on upstream + security, Sonnet on
>   structured downstream)
>
> What's *not* in this repo:
> - The full prompt heuristics each agent has accumulated over hundreds of runs
> - Our internal coding conventions and "common pitfall" sections
> - Project-specific `manifest_check` items per module
> - The full library of `learned-lessons/<module>-lessons.md` patterns
> - Some retry-and-recovery logic that's been tuned per agent
>
> The agents *will* run end-to-end on a real task. They just won't run with the
> depth our internal versions have. Specialize them on top of this skeleton —
> don't expect a turn-key replica.

## File map

| Agent | Model | Output | Role |
|---|---|---|---|
| `planner.md` | Sonnet | `meta.json`, `active.json` | Splits user task into sub-tasks; builds DAG |
| `analyst.md` | Opus | `analysis.md` | Requirements + edge cases + Behavioral Spec |
| `architect.md` | Opus | `design.md` | Sprint Contract + design decisions |
| `developer.md` | Opus | `progress.md` + code | Implements; quick lint |
| `reviewer.md` | Sonnet | `reviews.json` | Aggregates the 3 sub-reviews |
| `review-correctness.md` | Opus | `reviews/correctness.json` | Bugs / BS coverage / manifesto |
| `review-convention.md` | Sonnet | `reviews/convention.json` | Lint / format compliance |
| `review-quality.md` | Sonnet | `reviews/quality.json` | DRY / SRP / complexity / dead code |
| `tester.md` | Sonnet | `tests.md` | Full test run + BS coverage check |
| `qa.md` | Opus | `qa.md` | E2E + smoke + UX (system-level) |
| `security-reviewer.md` | Opus | `reviews/security.json` | OWASP + module-specific security |
| `documenter.md` | Sonnet | `docs.md` + project docs | API contract + usage docs |
| `retrospective.md` | Sonnet | `learned-lessons/<module>-lessons.md` | Distills patterns |
| `tuner.md` | Opus | prompt + profile updates | Periodic — propagates lessons back |

## How they connect

See [`../pipeline-workflow.md`](../pipeline-workflow.md) for the full flow,
including the parallel review fan-out.

## How to specialize

When you adopt this for your codebase:

1. Don't rewrite the structure — keep the roles, the I/O contract, the
   idempotency checks.
2. Add your specifics in the right places:
   - Coding conventions → `developer.md`'s "Code Conventions" section
   - Module-specific pitfalls → `analyst.md`'s "Pre-Analysis Check"
   - Per-axis manifesto items → `.claude/profiles/<module>.yaml`'s `manifest_check`
   - Lessons accumulated from your runs → `.claude/learned-lessons/<module>-lessons.md`
3. Run `tuner` periodically to propagate accumulated lessons back into the
   agent prompts.

# Expected output

> Mock artifacts showing what `.state/tasks/<task-id>/` would look like
> after the quickstart task runs end-to-end. **Not produced by an actual
> run** — these files are written by hand to give you a concrete
> reference of the shape and content the pipeline produces.
>
> When you run the real quickstart, your `.state/tasks/<task-id>/`
> directory will contain similar files (with different timestamps,
> task IDs, and exact wording).

## Files in this directory

| File | Owner | What it contains |
|---|---|---|
| `meta.json` | daemon + every agent | Status, role_done flags, retry counts, manifesto axes |
| `analysis.md` | analyst | Requirements + edge cases + Behavioral Spec |
| `design.md` | architect | Sprint Contract + design decisions |
| `progress.md` | developer | What was implemented + manifest evidence |
| `reviews/correctness.json` | review-correctness | One sub-review verdict |
| `reviews.json` | reviewer (orchestrator) | Aggregate of all 5 reviews |
| `qa.md` | qa | E2E + smoke verdict |
| `handoffs.jsonl` | every agent | Inter-agent timeline |

## What's NOT here

To keep the example readable we left out:
- The other three sub-reviews (`reviews/convention.json`,
  `reviews/quality.json`, `reviews/security.json`) — they look the same
  as `reviews/correctness.json` with different `agent` and `findings`.
- `tests.md` (the demo profile's test command is `echo "OK"`, so this
  file would be a one-liner)
- `docs.md` and `retro.md` — short and obvious for a hello-world task
- The agent log files in `logs/`

In a real run all of these are present.

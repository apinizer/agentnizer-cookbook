# Media — visual assets

Visual assets referenced from the top-level `README.md`. The repo ships
without binaries; drop your generated images in here.

## Expected files

| File | Purpose | Where it's referenced |
|------|---------|------------------------|
| `pipeline-overview.png` | Hero image — the 13-agent flow with the parallel fan-out as focal point | README "What it looks like when the team is busy" |

## Generation

We use AI image generators (Gemini, ChatGPT, Midjourney, etc.) for these
rather than recording terminals — it's faster, looks cleaner, and avoids
the text-rendering problems that come with screenshot-style images.

The prompts we use live in the top-level README's git history (see the
PR/commit that introduced the image). Two prompt families have worked
well:

1. **Pipeline overview** — full 13-agent flow with fan-out as focal point.
   Style reference: Linear / Vercel / Stripe documentation hero images.
2. **Fan-out close-up** — five agent figures around a central diff, accent
   colors on each, data-flow lines. More dramatic, less informational.

## Constraints

- **Format**: PNG, transparent or solid dark background.
- **Size**: ≤ 500 KB (README loads on mobile too).
- **Aspect**: 16:9, target 1600×900 or 1200×675.
- **Text**: avoid. Image gen models still render text poorly. Use icons,
  not labels.

## If you'd rather record a terminal

A live capture of `./team.sh status` showing five rows for the same task
ID is also valuable. `asciinema` + `agg` is the cleanest path:

```bash
brew install asciinema
cargo install --git https://github.com/asciinema/agg

asciinema rec /tmp/cast.cast --command "watch -n 1 ./team.sh status"
agg --speed 1.5 /tmp/cast.cast docs/media/parallel-review.gif
```

Catch the 10–20 second window where five rows appear with the same
task ID and different OWNERs.

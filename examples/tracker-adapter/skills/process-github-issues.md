---
name: process-github-issues
description: Pull open GitHub issues with the ai-pipeline label and feed them into the daemon's task queue. Optionally post agent outputs back as issue comments.
---

# /process-github-issues

Run the GitHub Issues tracker adapter once. The adapter:

1. Queries `GET /repos/{owner}/{repo}/issues?labels=ai-pipeline&state=open`.
2. Translates each matching issue into a task entry in `.state/active.json`.
3. (If not `--read-only`) posts the latest agent `.md` outputs as issue comments.
4. (If not `--read-only`) transitions issues whose tasks reached `status=done` by removing the trigger label and adding `ai-pipeline-done`.

This is the **single-shot version** — it runs once and exits. For a continuous poll loop, run `python examples/tracker-adapter/adapter.py` directly without `--once`.

## How to invoke

```
/process-github-issues --owner my-org --repo my-repo --dry-run
/process-github-issues --owner my-org --repo my-repo --read-only
/process-github-issues --owner my-org --repo my-repo
```

## Required environment

- `GITHUB_TOKEN` set to a personal-access token with `repo` scope (read+write on issues).
- The adapter dependency installed: `pip install requests`.

Skip the env requirement if `--dry-run` is passed; the adapter prints what it *would* do without authenticating.

## What the slash command runs

```bash
python "$REPO_ROOT/examples/tracker-adapter/adapter.py" \
  --owner "$OWNER" \
  --repo "$REPO" \
  --once \
  ${DRY_RUN:+--dry-run} \
  ${READ_ONLY:+--read-only}
```

## Acceptance

- Without a token + `--dry-run`: prints planned API calls, exits 0.
- With a token + `--read-only`: writes `.state/active.json` from open issues, makes no comments.
- With a token + no flags: writes `.state/active.json`, posts new agent outputs as comments, transitions completed issues.

## Limitations (read before complaining)

- **No webhook mode.** This is poll-driven by design.
- **No comment threading parsing.** If a human comments "ship it", the adapter does not see it. Wire that into a gate skill if you need it.
- **One daemon per repo assumed.** Multi-host setups need a distributed lock — out of scope for the cookbook.
- **GitHub Enterprise:** swap `https://api.github.com` for your enterprise endpoint via env var (the reference adapter does not yet read this; one-line change in `adapter.py`).

## See also

- `examples/tracker-adapter/adapter.py` — the implementation.
- `examples/tracker-adapter/how-to-swap-trackers.md` — Jira / Linear / GitLab variants.

# Quickstart Example

A minimal, copy-paste-runnable example. Here so you can confirm the
pipeline works on your machine in **under 5 minutes** — before you spend
half an hour adapting profiles to your real codebase.

The "module" here is a fake one: it has nothing to compile, nothing real
to lint. The build/test/lint commands are `echo` calls that always pass.
This is **deliberate** — it lets the agent loop run end-to-end without you
having a real project wired up. You're testing the *pipeline*, not your
code.

## Try it

From the repo root:

```bash
# 1. Copy the demo profile into the active profiles directory.
cp examples/quickstart/profiles/demo.yaml .claude/profiles/demo.yaml

# 2. Start a task. The planner will detect the "demo" module from the
#    word "demo" in the description.
./team.sh start "$(cat examples/quickstart/task.txt)"

# 3. Watch it run.
watch -n 2 ./team.sh status
```

You should see the daemon spawn agents in sequence. The whole run takes
around 5–15 minutes depending on Claude latency. When it finishes:

```bash
# 4. Inspect what the team produced.
ls .state/tasks/*/
cat .state/tasks/*/analysis.md
cat .state/tasks/*/design.md
cat .state/tasks/*/qa.md
```

## What you should see

When the run finishes, your `.state/tasks/<task-id>/` should contain
files matching the shape and content of [`expected-output/`](expected-output/)
in this directory. We've included a hand-written reference set there so
you can:

- See what each artifact looks like *before* running anything
- Diff your run's output against the reference to catch obvious gaps
- Use the references as templates when you adapt the agents to your
  stack

The reference set covers:

| File | What it is |
|---|---|
| [`expected-output/meta.json`](expected-output/meta.json) | Final task state with all `role_done` flags set |
| [`expected-output/analysis.md`](expected-output/analysis.md) | Requirements, edge cases, 4 BS items |
| [`expected-output/design.md`](expected-output/design.md) | Sprint Contract with 9 SC items |
| [`expected-output/progress.md`](expected-output/progress.md) | Developer's implementation log + manifest evidence |
| [`expected-output/reviews/correctness.json`](expected-output/reviews/correctness.json) | One sub-review verdict (3 others would look the same) |
| [`expected-output/reviews.json`](expected-output/reviews.json) | Reviewer orchestrator's aggregate verdict |
| [`expected-output/qa.md`](expected-output/qa.md) | E2E + smoke verdict |
| [`expected-output/handoffs.jsonl`](expected-output/handoffs.jsonl) | Full inter-agent timeline (13 lines) |

If your run produces a similar set of files, **the pipeline works on
your machine**. You're ready to drop your real profiles in.

> The `expected-output/` files are mock — written by hand, not produced
> by an actual run. Real runs will have different timestamps, task IDs,
> and exact wording. The *shape* is what matters.

## When this fails

| Symptom | Likely cause |
|---------|--------------|
| `./team.sh: command not found` | Run from the repo root, or `chmod +x team.sh` |
| `bash 4+ required` | macOS ships bash 3; install with `brew install bash` |
| `claude binary missing` | Install Claude Code: `npm install -g @anthropic-ai/claude-code` |
| Agent runs but produces empty output | Check `~/.claude/logs/ai-pipeline-daemon.log` |
| Daemon exits immediately | Likely a stale lock — `rm .state/locks/team.lock` |

## After this works

Replace `.claude/profiles/demo.yaml` with profiles for your real modules
(see the existing `manager.yaml`, `worker.yaml`, etc. as templates), then
run `./team.sh start` against a real task in your codebase.

You can leave the `demo.yaml` profile in place — it stays harmless, and
it's useful to come back to for sanity-check runs.

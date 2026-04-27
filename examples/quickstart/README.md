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

- An `analysis.md` with requirements, edge cases, and at least 2 BS items
- A `design.md` with a Sprint Contract (SC-1, SC-2, ...)
- A `progress.md` with a (small) "fake" code change — the developer agent
  treats the demo module as a real module and attempts to write code
  inside `examples/quickstart/code/`
- `reviews/correctness.json`, `reviews/convention.json`,
  `reviews/quality.json`, `reviews/security.json` — each with a verdict
- A `tests.md` (the demo profile's `test` command is `echo "OK"`, so it
  passes trivially)
- A `qa.md` with PASS verdict
- A `docs.md` and a `retro.md`

If all of that exists at the end, **the pipeline works on your machine**.
You're ready to drop your real profiles in.

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

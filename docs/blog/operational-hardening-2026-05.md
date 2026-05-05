# Operational Hardening: What Eight Months of Running an AI Pipeline Actually Hardens

*Module conflicts, budget runaways, zombie subprocesses, and 10 KB retry contexts — the four operational scars we put back into the cookbook in May 2026.*

---

When we open-sourced the cookbook at v0.1.0, the README was honest about what it didn't ship: months of accumulated lessons, internal conventions, prompt heuristics tuned to our stack. We left those out on purpose — they weren't going to generalise.

What we *didn't* expect to leave out was operational hardening. The state machine works. The 13-agent assembly line works. The parallel review fan-out works. But "works" and "works under sustained production load" turned out to be different bars.

Here are the four things we kept tripping over since v0.1.0, and the patches we put back into the cookbook this week. None of them are clever. All of them mattered.

---

## 1. The same-module race nobody asked for

The first time it bit us, the diff looked surreal: two developer agents had each modified the same file in the same module, in parallel, and neither knew about the other. The reviewer flagged "merge conflict markers in shipped code". Both tasks had passed every other gate.

The cause was obvious in hindsight. The daemon's parallelism cap — `LSD_MAX_PARALLEL_TASKS` — was a global ceiling, not a per-module ceiling. Two unrelated tasks decomposed into work that hit the same files; the daemon happily spawned both `developer` agents at once.

The fix is one helper plus four lines in `_dispatch`:

```python
def _running_modules(self, by_id: dict[str, Task]) -> dict[str, str]:
    """Map of {module: task_id} for currently running subprocesses."""
    mods: dict[str, str] = {}
    for rp in self.running:
        t = by_id.get(rp.task_id)
        if t and t.module:
            mods[t.module] = rp.task_id
    return mods

# In _dispatch:
if not ALLOW_MODULE_CONFLICT and task.module:
    running_mods = self._running_modules(by_id)
    conflict_owner = running_mods.get(task.module)
    if conflict_owner and conflict_owner != task.task_id:
        continue  # skip; another task in this module is in flight
```

That's the entire fix. `ALLOW_MODULE_CONFLICT=1` exists as a bypass for regression tests where you *want* to reproduce the race deliberately. In every other scenario the guard is on.

The lesson is the kind of thing that only shows up after enough wall-clock hours: **parallelism caps need a granularity argument, not just a count.** Three concurrent tasks across three modules is fine. Three concurrent tasks across two modules is one race condition waiting to happen.

---

## 2. The $50 prompt loop

We hit a budget runaway exactly once. A change to the architect prompt produced a design that the reviewer kept failing for the same reason. The architect-developer escalation loop burned through several attempts. Each cycle reset the retry counters and started fresh. The alarm wasn't financial — it was the scrolling log of `developer rc=1` lines that didn't quite look like the usual rate.

The fix is a soft/hard pair:

- **Soft cap** (`LSD_DAILY_USD_SOFT_CAP`): Slack `budget_soft_cap` info, daemon continues. You get a chance to look without losing time.
- **Hard cap** (`LSD_DAILY_USD_HARD_CAP`): daemon writes `team.paused`, fires Slack `budget_hard_cap`. New spawns stop. In-flight subprocesses finish; nothing gets killed mid-stream. Operator decides whether to bump the cap or let the pause stick.

The accounting is a JSON file in `.state/locks/daily-budget.json`:

```json
{
  "date": "2026-05-05",
  "spent_usd": 12.34,
  "soft_warned": false,
  "hard_paused": false
}
```

It resets at UTC midnight. The bookkeeping happens once per agent completion, in `_post_run_update`, right after `cost_usd` from the JSON output mode is parsed.

What we tried first — and discarded — was a per-task budget. It looked tidier (each task has a known token allotment) but the failure mode was the wrong one: a task that genuinely needed more retries got killed mid-flight while easier tasks left their budget unused. **The daily envelope is the one humans actually care about.** "Don't spend more than $X today" is the question being asked. Match the question.

---

## 3. The 10 KB retry context that came back to bite

Cumulative retry context — passing every blocking finding from every previous failed gate forward into the developer's next attempt — was one of our happier patterns. It killed the "developer fixes the tester finding but ignores the reviewer finding" failure mode.

It also grew unbounded.

Three gates can each contribute 3-5 KB of finding details. Across three retry cycles, the developer's prompt was carrying 12-15 KB of retry context alone, much of it duplicated across cycles. Token cost added up; cache hit rate dropped; latency on retry spawn started to be noticeable.

The fix: a 4 KB cap on the merged block, UTF-8 safe truncation, and a marker so the agent knows it was truncated:

```python
encoded = cumulative.encode("utf-8")
if len(encoded) > MAX_CUMULATIVE_BLOCK_BYTES:
    truncated = encoded[: MAX_CUMULATIVE_BLOCK_BYTES - 100].decode(
        "utf-8", errors="ignore"
    )
    cumulative = (
        truncated
        + f"\n\n[truncated to {MAX_CUMULATIVE_BLOCK_BYTES}B cap — "
        f"original {len(encoded)}B]\n\n"
    )
```

Hidden in this fix is a small piece of UTF-8 hygiene worth pointing out: slicing on a byte boundary mid-character produces garbage. `decode("utf-8", errors="ignore")` quietly drops the partial trailing character; without it the truncation can corrupt the last sentence. This isn't a Python-only gotcha — anyone implementing context limits in another language hits the same edge.

The cap is high enough that the typical retry block fits. When it doesn't fit — which only happens on deep retry chains with many gates — the truncation marker tells the developer agent "you're seeing the most-relevant slice, not everything". That's still better than the previous behaviour, which was to silently re-pay the token cost of the same findings on every cycle.

---

## 4. The zombie subprocess problem

`team.sh stop` was supposed to be graceful. It mostly was. The exception: when an agent subprocess had hung mid-stream — Anthropic API stall, DNS hiccup, the kind of thing that doesn't return cleanly on SIGTERM — the daemon's shutdown loop ended without reaping that one subprocess. The next time we started the daemon, we'd see a stale pid in `team.lock` and have to manually `kill -9` the zombie.

The fix is a three-stage cascade in `_graceful_shutdown`:

```
1) Wait up to 60 s for natural exit (poll-reap)
2) SIGTERM whatever is still running
3) Wait 10 s for SIGTERM to take effect
4) SIGKILL anything still alive + write a SHUTDOWN_KILLED marker
   to that subprocess's log file
```

The `SHUTDOWN_KILLED` marker matters more than it looks. When you triage a failed run after the fact, the difference between "subprocess crashed" and "operator killed the daemon while this was running" is the difference between investigating an agent bug and shrugging it off. The marker tells future-you which one you're looking at.

What we explicitly chose *not* to do: send SIGTERM immediately on `team.sh stop`. That's faster but ruins the in-flight work. If a developer agent is 80 % through writing code, the natural-exit window gives it the chance to finish and let the next role pick up. Operationally, this is the same calculus as Kubernetes' `terminationGracePeriodSeconds` — give the workload a chance to finish before forcing it.

---

## What this changes for the cookbook

All four patches are in the `Unreleased` section of [CHANGELOG.md](../../CHANGELOG.md). Test coverage lives in [`tests/test_pipeline_daemon.py`](../../tests/test_pipeline_daemon.py) — 21 unit tests at the time of writing, run via `uv run --with pytest --with pytest-asyncio pytest tests/`. Subsequent commits expanded the same suite when more tunings (T4 per-role timeout, T7 per-role output cap, T13 quota-reset regex extraction) landed; the count moves up over time.

The cookbook README's "Cookbook vs what we actually run" table got eight new rows. The gap between cookbook and production is shrinking — not because we're changing what we ship, but because the patches that came out of months of running this turned out to belong upstream. None of them are stack-specific. None of them are tuned to our domain. They just are the failure modes of running an LLM pipeline on real workloads.

If you're running the cookbook unmodified, you can pull these in by setting four env vars:

```bash
export LSD_DAILY_USD_SOFT_CAP=15
export LSD_DAILY_USD_HARD_CAP=20
# Module conflict guard, retry cap, SIGKILL cascade are on by default.
```

That's it. No code change, no migration. The next `team.sh start` runs with the new behaviour.

---

## What we're still not happy with

Two things, for honesty's sake:

**Per-task token budget and daemon-wide daily USD cap now coexist.** This wasn't true at the time the four patches above first landed; the daily-USD cap was the only safety floor, which made it both the canary and the kill switch. Since then we've added `meta.json.token_budget` (per-task soft + hard limits, enforced against `token_billable_used` so cache reads don't trigger false-positive failures — see the cache-aware accounting lesson in `learned-lessons/shared-lessons.md`). The combination is what we'd recommend: per-task budget catches the runaway *task*; daily USD cap catches the runaway *day*. Both ship in the cookbook.

**Module conflict guard is FIFO.** First task to claim a module wins; later tasks wait. There's no priority hint, no LIFO option, no fairness check. In practice this is fine because we rarely have two tasks queued for the same module simultaneously — if we did, the planner is probably under-decomposing.

Both of these will get refinement when the operational pressure makes the right behaviour obvious. Until then, the simple version is what's in the cookbook.

---

## A small meta-observation

The 0.1.0 tag was the cookbook proving we *can* run an AI pipeline in production. May 2026's hardening is the cookbook proving we can run it for months without operational drift. Those are different proofs. The first is the easier one — most multi-agent demos clear that bar. The second is what separates "I built an agent system once" from "I'm still running an agent system on Tuesday".

If anything in this writeup made you nod along — recognising your own past 2 a.m. fix in the module-conflict story, or the budget runaway, or the zombie subprocess — you're already running something serious enough that the patterns described here might be useful. Read them, copy what fits, leave what doesn't.

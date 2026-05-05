# The Tuner Pattern: Weekly Autonomy with Human Approval

*Why we put a 14th agent on the weekly cron — and made it deliberately incapable of changing anything on its own.*

---

The retrospective agent appends. That's its job. After every task closes, it writes one entry to `learned-lessons/<module>-lessons.md`: the surprising thing that happened, the misread that almost shipped, the constraint that wasn't in the spec. After three months of this, the lessons file is a goldmine. After six months, nobody reads it.

The cookbook's v0.1.0 README hand-waved this with *"you read this weekly and inject the patterns back into the architect prompt"*. Nice in theory. In practice — not for us, not for anyone we talked to. The lessons accumulated. The prompts didn't change. The same patterns kept showing up in the failure_reasons of new tasks because the agent prompts that produced them hadn't been updated since release.

This post is about the 14th agent we added to fix that, and — more importantly — about *what we deliberately did not let it do*.

---

## The shape of the problem

Three things have to be true for prompt patches to make it into the agent definitions:

1. Someone has to read enough recent lessons to spot a pattern that matters.
2. Someone has to translate that pattern into a concrete prompt change — not "the architect should be more careful about X", but specifically "add this bullet to architect.md, line 47, in the Rules section".
3. Someone has to actually edit the file.

Step 2 is the bottleneck. Engineers are good at step 1 (we can spot the pattern in 30 seconds) and good at step 3 (the edit is trivial). But step 2 — the act of holding the pattern in your head while you reason about which agent prompt is responsible and what the minimal patch would look like — takes about 20 minutes per pattern, and it's exactly the kind of work that gets deferred forever.

A weekly tuner agent does step 2 for free. Every Friday at 18:00 local, it reads the week's new lesson entries, looks for patterns that appeared in 2 or more tasks, and proposes one targeted patch.

It does *not* apply the patch.

That last constraint is the entire point of the design.

---

## Why "propose, don't apply"

Anyone who's run an LLM-based system long enough has seen what happens when the system can edit its own prompts unsupervised. Drift. Slow, accumulating drift. Each individual edit looks reasonable; the integral over six months produces an agent that subtly does the wrong thing.

The proposal-only design eliminates this:

- The tuner can never make the agent more aggressive about something it shouldn't be aggressive about.
- A bad proposal is visible, in markdown, in `.state/tuner/$WEEK/proposal.md`, before any prompt has changed.
- The diff is in `.state/tuner/$WEEK/proposal.json` (machine-readable), so when you do approve, the next tuner run applies it deterministically.
- A rejected proposal stays in the directory; the next tuner run reads `decision.txt`, sees `rejected`, and skips it.

The contract is: **the tuner can identify and articulate. The human decides.** This sounds like it defeats the purpose of automation, but it doesn't — the 20-minute step (step 2 above) is what actually got automated. Approval takes 90 seconds.

---

## What it looks like to operate

Here's the directory structure after one tuner run:

```
.state/tuner/2026-W18/
├── proposal.md       # human-readable, what changed and why
├── proposal.json     # machine-readable: file_path + diff
└── decision.txt      # empty until you write 'approved' or 'rejected'
```

The proposal markdown is short by design. Long proposals are hard to evaluate. Format:

```
--- Tuner Proposal (2026-W18) ---
Target file : .claude/agents/architect.md
Pattern     : Architect designed against an unverified assumption in 3 tasks
              this week (FOO-12, FOO-23, FOO-31). In each case, the design.md
              referenced a "best-effort" cache invalidation that the worker
              module's profile.yaml explicitly forbids.
Affected tasks : FOO-12, FOO-23, FOO-31
Proposed change:
  + ## Module-specific constraints
  + Before designing cache behaviour for `worker`, read
  + `.claude/profiles/worker.yaml` `manifest_check` — best-effort
  + invalidation is forbidden in this module.

Rationale : All three tasks were caught at code-review or qa, not at design.
            Adding the check at design saves a developer retry per task.
Status    : PENDING (human approval required)
```

You read this in a minute. You either write `approved` or `rejected` (with a one-line reason) into `decision.txt`. Done.

The next Friday, the tuner reads `decision.txt`. If approved: edit applied, proposal archived. If rejected: archived as-is, the pattern stays on the radar but the patch doesn't go in. The audit trail is the directory itself — you can git-log `.state/tuner/` to see every proposal and every decision.

---

## What the tuner is forbidden from doing

The agent definition (`agents/tuner.md`) lists the explicit forbidden actions:

- **No repo-wide grep.** It reads only `.claude/learned-lessons/*-lessons.md`, only the entries dated within the last 7 days, plus `.state/completed.jsonl` for cross-reference.
- **No source-code reading.** Product code under `apps/` is off-limits. The tuner is reasoning about prompts, not about implementations. If the lessons reference a behaviour, that's the substrate.
- **No multi-file proposals.** One file change per week, maximum. Bundling four "small" changes into one proposal makes the rejection decision harder than four sequential ones.
- **No daemon code.** `.claude/scripts/*.py` is forbidden as a target. We have separate processes for daemon changes; the tuner is for prompt and skill tuning only.
- **No cosmetic suggestions.** Whitespace, typos, format-only changes — the agent prompt explicitly tells the tuner to skip these. They generate proposal noise without proportionate value.

All of these are constraints we'd happily impose on a human contributor too. The tuner getting them in writing means the constraints actually hold.

---

## Why weekly, not nightly

Running this nightly was the first design we tried. It was wrong for two reasons.

**Pattern detection needs sample size.** A pattern that appeared in 2 tasks today might not be a pattern at all — it might be a coincidence of task selection. The same two tasks across a week, where you've also closed 8 other tasks, give you a denominator. "2 of 10 tasks hit this" is a real signal. "2 of 2 tasks hit this on Wednesday" is noise.

**Cognitive load.** A daily proposal on your inbox at 9 a.m. becomes background hum, then gets ignored. A weekly proposal on Monday morning is a calendar item. Friday-evening generation, Monday-morning review is the cadence that survived contact with how we actually work.

The cron entry is a single line:

```cron
0 18 * * 5 /path/to/repo/.claude/scripts/weekly-tuner-trigger.sh
```

The trigger script gates on a per-week lock file (`tuner-2026-W18.log`) so accidental double-runs are no-ops. It checks `.state/tuner-last-run.txt` against `learned-lessons/*.md` mtimes; if no fresh lessons, it logs "no new lessons this week, skipping" and exits without spawning Claude. The skip path is the common case — most weeks do not produce a proposal — and that's correct.

---

## The lesson the tuner taught us about the tuner

We initially designed the tuner to be more ambitious. It could propose changes to multiple files. It could suggest agent role redefinitions. It could even surface candidate new lessons distilled from cross-cutting patterns.

Two weeks of prototyping showed all three were mistakes. The multi-file proposals were rejected almost universally because the human evaluation cost scaled with the number of files. The role redefinitions were over-confident — the tuner was reasoning about agent boundaries from too little information. The candidate-lesson distillation was indistinguishable from "the tuner is summarising its own training data".

The version that shipped is the version that survived ruthless scope reduction. One file. One pattern per week. Approval gate non-optional. The simpler the contract, the more likely the human reads it and acts on it. **Restraint, in agent design, is a feature.**

---

## What this enables

The biggest second-order effect of the tuner pattern is that the lessons file becomes worth maintaining. Before the tuner, every lesson entry was a hopeful note in a file nobody reread. With the tuner, every lesson entry might surface as a proposal next Friday, which means engineers actually write better lessons. The format converges on what the tuner can act on — concrete, attributed, falsifiable — rather than what feels good to write at retrospective time.

That's a feedback loop the cookbook didn't have at v0.1.0, and one we'd argue is necessary for any AI development pipeline to compound usefully over time. **The retrospective writes; the tuner reads. The pipeline gets better at being itself.**

It's not a foundation model fine-tune. It's not RLHF. It's a 110-line agent definition, a 60-line bash trigger, and a directory of dated proposals. That's enough.

---

## How to wire it up

If you're running the cookbook, the tuner pattern requires three things:

1. **Cron entry**: `0 18 * * 5 /path/to/repo/.claude/scripts/weekly-tuner-trigger.sh` (use your local timezone; the agent doesn't care).
2. **Slack channel** (optional but recommended): the trigger emits `tuner_started` and the agent emits `tuner_done`, so Friday evening you see "tuner started" and Monday morning either "no proposal" or "proposal awaiting approval".
3. **A weekly habit**: open `.state/tuner/$WEEK/proposal.md` Monday morning, decide, write the decision, move on.

The third item is the one nobody can automate. That's deliberate.

---

The tuner now ships in this repository: the agent definition is at [`.claude/agents/tuner.md`](../../.claude/agents/tuner.md) and the cron entry script is at [`.claude/scripts/weekly-tuner-trigger.sh`](../../.claude/scripts/weekly-tuner-trigger.sh). The contract is the contract: read recent lessons, write a proposal under `.state/tuner/<week>/`, never modify any agent file directly. Test coverage in your own daemon should lock that contract — a unit test that confirms the tuner cannot write outside `.state/tuner/`.

If "an agent that proposes prompt patches but won't apply them" is a primitive you'd find useful, take what's in this cookbook and adapt the boundary — the contract (propose-only) is the durable part; the file paths it reads, the cron cadence, and the diff format are yours to evolve.

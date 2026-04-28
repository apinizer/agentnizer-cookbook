# Skills — surface-level skeleton

> **Read this before diving in.** The skill definitions in this directory are
> **surface-level versions** of what we actually use. Each skill describes
> *what it does and how it wires into the pipeline*; the deeper details
> (full prompt scaffolding, edge-case handling, our internal conventions
> for how each skill negotiates with `team.sh`) are not in this repo.
>
> Each `SKILL.md` is enough for the slash-command to work end-to-end. It is
> not enough to reproduce the exact behavior we get from our internal
> versions. Specialize on top.

## Available slash-commands

| Command | What it does |
|---|---|
| `/start` | Hand a task to the team — wraps `team.sh start` |
| `/ask` | Read-only Q&A about the pipeline / your codebase / `.state/` |
| `/status` | Show daemon status + active tasks + recent completed |
| `/verify` | Lightweight self-check — runs profile lint/build on changed files |
| `/feature` | Feature workflow — wraps `/start` with feature-shaped prompt |
| `/bugfix` | Hypothesis-first bug fix — wraps `/start` with bug shape |
| `/improve` | Improvement workflow — requires measurable Sprint Contract |
| `/local-loop` | Foreground single-task run with interactive gates |
| `/process-issues` | Batch CSV import of tasks |
| `/sprint-loop` | Deprecated alias — use `/start` |

## Two layers of skills

These skills come in two shapes:

1. **Wrappers around `team.sh`** (`/start`, `/feature`, `/bugfix`, `/improve`,
   `/process-issues`, `/local-loop`) — they format the user's input and hand
   it to the daemon. Most of these are 2-3 paragraphs because the actual
   logic lives in the daemon and the agent prompts.

2. **Read-only utilities** (`/ask`, `/status`, `/verify`) — they don't spawn
   a pipeline run; they inspect or sanity-check.

The thinness of the wrapper skills is deliberate: they're meant to nudge the
user toward `team.sh` with the right framing, not to duplicate the agent
work. If a skill looks short, that's because the work it triggers happens
elsewhere — in the daemon and the 14 agents.

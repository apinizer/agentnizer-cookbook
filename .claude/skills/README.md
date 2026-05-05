# Skills

**9 user-invocable slash commands.** Each `SKILL.md` is what Claude Code reads to wire `/start`, `/status`, `/ask`, etc. into your session. They're concise on purpose: the heavy lifting lives in the daemon and the 14 agents — these are the surface that hands a task off correctly.

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

## Two layers of skills

These skills come in two shapes:

1. **Wrappers around `team.sh`** (`/start`, `/feature`, `/bugfix`, `/improve`,
   `/process-issues`, `/local-loop`) — they format the user's input and hand
   it to the daemon. Most of these are 2-3 paragraphs because the actual
   logic lives in the daemon and the agent prompts.

2. **Read-only utilities** (`/ask`, `/status`, `/verify`) — they don't spawn
   a pipeline run; they inspect or sanity-check.

The thinness of the wrapper skills is deliberate. They format intent and pass it to the daemon; the agent work happens downstream.

#!/bin/bash
# .claude/scripts/weekly-tuner-trigger.sh
# Cron-triggered Friday 18:00 local. Invokes the weekly tuner agent.
#
# The tuner reads .claude/learned-lessons/<module>-lessons.md, looks for
# patterns that recurred in 2+ tasks this week, and drops a proposed
# prompt patch under .state/tuner/<week>/proposal.md for human approval.
# Propose-only — never modifies agent prompts directly.
#
# Agent prompt: .claude/agents/tuner.md
# Pattern essay: docs/blog/the-tuner-pattern.md
#
# Crontab entry (5-field cron):
#   0 18 * * 5 /path/to/repo/.claude/scripts/weekly-tuner-trigger.sh
#
# Manual trigger:
#   bash .claude/scripts/weekly-tuner-trigger.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WEEK=$(date +"%Y-W%V")
TUNER_DIR="${PROJECT_ROOT}/.state/tuner/${WEEK}"
LAST_RUN_FILE="${PROJECT_ROOT}/.state/tuner-last-run.txt"
LOG_FILE="${PROJECT_ROOT}/.state/tuner/tuner-${WEEK}.log"

cd "$PROJECT_ROOT"

mkdir -p "$TUNER_DIR" "$(dirname "$LOG_FILE")"

# Already ran this week?
if [ -f "${TUNER_DIR}/proposal.md" ]; then
    echo "[tuner] already ran this week (${WEEK}); proposal.md exists." \
      | tee -a "$LOG_FILE"
    exit 0
fi

# Slack notification (started)
if [ -x "${PROJECT_ROOT}/.claude/hooks/notify-slack.py" ]; then
    "${PROJECT_ROOT}/.claude/hooks/notify-slack.py" \
        --type tuner_started \
        --summary "Week ${WEEK} — tuner agent started" \
        >> "$LOG_FILE" 2>&1 || true
fi

echo "[tuner] weekly tuning starting — ${WEEK}" | tee -a "$LOG_FILE"

# Are there fresh lessons since last run?
LESSONS_DIR="${PROJECT_ROOT}/.claude/learned-lessons"
LESSON_COUNT=0
if [ -d "$LESSONS_DIR" ]; then
    if [ -f "$LAST_RUN_FILE" ]; then
        LESSON_COUNT=$(find "$LESSONS_DIR" -name "*.md" \
            -newer "$LAST_RUN_FILE" 2>/dev/null | wc -l | tr -d ' ')
    else
        LESSON_COUNT=$(find "$LESSONS_DIR" -name "*.md" 2>/dev/null \
            | wc -l | tr -d ' ')
    fi
fi

if [ "$LESSON_COUNT" -eq 0 ]; then
    echo "[tuner] no new lessons this week. skipping." | tee -a "$LOG_FILE"
    date > "$LAST_RUN_FILE"
    exit 0
fi

echo "[tuner] ${LESSON_COUNT} new lesson file(s) found." | tee -a "$LOG_FILE"

# Invoke Claude tuner agent
CLAUDE_BIN="${PIPELINE_CLAUDE_BIN:-claude}"
PROMPT="tuner weekly review — week=${WEEK}"

"$CLAUDE_BIN" -p "$PROMPT" \
    --no-session-persistence \
    --output-format json \
    >> "$LOG_FILE" 2>&1 &

CLAUDE_PID=$!
echo "[tuner] Claude PID: ${CLAUDE_PID} — log: ${LOG_FILE}" | tee -a "$LOG_FILE"

# Last-run timestamp
date > "$LAST_RUN_FILE"

echo "[tuner] started."

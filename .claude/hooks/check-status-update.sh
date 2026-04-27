#!/bin/bash
# Stop hook — warns if meaningful file changes were made but STATUS.md was not updated.
# Goal: prevent "what was done" memory from being lost.
# Claude's CLAUDE.md behavior directive supports auto-triggering the status skill;
# this hook is a second safety layer.

set -e

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_DIR" 2>/dev/null || exit 0

git rev-parse --git-dir >/dev/null 2>&1 || exit 0

changed=$(git status --porcelain 2>/dev/null | awk '{print $NF}')
[ -z "$changed" ] && exit 0
echo "$changed" | grep -qx "STATUS.md" && exit 0

substantive=$(echo "$changed" | grep -vE "(\.lock$|\.log$|^__pycache__|^node_modules|^\.DS_Store$|scheduled_tasks\.lock)" || true)
[ -z "$substantive" ] && exit 0

cat >&2 << WARN

╭─────────────────────────────────────────────────────────────╮
│ ⚠  STATUS.md not updated                                    │
│                                                             │
│ The following file(s) changed in this session:              │
$(echo "$substantive" | sed 's/^/│   • /' | awk '{printf "%-61s│\n", $0}')
│                                                             │
│ If you did meaningful work: /status update "<description>"  │
│ Keep the "what was done" memory intact.                     │
╰─────────────────────────────────────────────────────────────╯

WARN

exit 0

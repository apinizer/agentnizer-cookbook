#!/bin/bash
# Stop hook — example pattern: warn when meaningful file changes were made but
# the project's status journal (STATUS.md) was not updated.
#
# About STATUS.md: a per-user, gitignored journal that records "what changed and
# why" across sessions. It's optional — adopt the convention or rip it out.
# If you don't keep a STATUS.md, either delete this hook or rename the target
# file to whatever your team uses (CHANGELOG.md, NOTES.md, devlog.md, ...).
#
# This is shipped as a working example of a Stop hook, not a mandatory part
# of the pipeline. The daemon does not depend on it.

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

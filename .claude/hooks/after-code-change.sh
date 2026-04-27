#!/bin/bash
# .claude/hooks/after-code-change.sh — AI Pipeline
# Triggered automatically after Write/Edit/MultiEdit tool calls (PostToolUse hook).
# Detects module and logs manifesto warnings for critical modules.
# Reads PostToolUse JSON from stdin (Claude Code hook protocol).

set -euo pipefail

INPUT_JSON=""
if [ ! -t 0 ]; then
    INPUT_JSON="$(cat)"
fi

CHANGED_FILE=""
if [ -n "$INPUT_JSON" ]; then
    if command -v jq >/dev/null 2>&1; then
        CHANGED_FILE="$(echo "$INPUT_JSON" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null || true)"
    else
        CHANGED_FILE="$(echo "$INPUT_JSON" | grep -oE '"file_path"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/' || true)"
    fi
fi

# Env var fallback (for manual testing)
if [ -z "$CHANGED_FILE" ]; then
    CHANGED_FILE="${TOOL_INPUT_PATH:-}"
fi

[ -z "$CHANGED_FILE" ] && exit 0

# ── Module detection ──────────────────────────────────────────────────────────

MODULE="unknown"

# Adapt the patterns below to match your project's module layout.

if echo "$CHANGED_FILE" | grep -q "/apps/worker/"; then
    MODULE="worker"
    echo "[HOOK] ⚠ Worker module changed. Concurrency + idempotency + advisory-lock check required."

elif echo "$CHANGED_FILE" | grep -q "/apps/providers/"; then
    MODULE="providers"
    echo "[HOOK] ⚠ Provider adapter changed. Review fallback chain + rate-limit tests."

elif echo "$CHANGED_FILE" | grep -q "/apps/shared/"; then
    MODULE="shared"
    echo "[HOOK] ⚠ Shared module changed. Cascade test/build needed for downstream modules."

elif echo "$CHANGED_FILE" | grep -q "/apps/manager/"; then
    MODULE="manager"

elif echo "$CHANGED_FILE" | grep -q "/apps/frontend/"; then
    MODULE="frontend"

elif echo "$CHANGED_FILE" | grep -q "/apps/connectors/"; then
    MODULE="connectors"
    echo "[HOOK] ⚠ Connector changed. Worker downstream cascade may be needed."
fi

echo "[HOOK:$(date +%H:%M:%S)] $MODULE | $(basename "$CHANGED_FILE")"

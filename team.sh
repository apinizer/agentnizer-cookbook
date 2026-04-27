#!/usr/bin/env bash
# team.sh — AI Pipeline development team CLI
#
# Cookbook note:
#   This script is an anonymized port of a CLI used in production by an
#   autonomous local development team. It drives a long-running daemon
#   ("pipeline daemon") that spawns specialized Claude Code agents
#   (planner, analyst, architect, developer, reviewer, tester, qa, etc.)
#   over a shared on-disk state directory. Use it as a reference for
#   building your own local agent team — the structure, lock semantics,
#   and status reporting all carry over.
#
# Commands:
#   team.sh start "<task description>"   Start new work (planner + pipeline daemon)
#   team.sh stop                          Send SIGTERM to the pipeline daemon
#   team.sh resume-daemon                 Restart daemon in background (no planner call)
#   team.sh pause                         Stop new spawns (existing agents finish)
#   team.sh resume                        Clear pause flag
#   team.sh status [--json]               Active + recently completed task tables
#   team.sh logs <task-id>                Tail task handoffs.jsonl
#   team.sh logs --daemon                 Tail daemon log file
#   team.sh help                          Help
#
# Notes:
#   - Local-only: no tracker, no external system integration.
#   - State: $STATE_DIR (default: <repo>/.state); see .state/README.md for schema.

set -euo pipefail

# ---------------------------------------------------------------------------
# Bash version check (associative arrays require bash 4+)
# ---------------------------------------------------------------------------
if [ -z "${BASH_VERSINFO+x}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  echo "ERROR: bash 4+ required. Current: ${BASH_VERSION:-unknown}" >&2
  echo "On macOS: brew install bash; then run the script with 'bash team.sh ...'." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${PIPELINE_STATE_DIR:-${SCRIPT_DIR}/.state}"
DAEMON_PATH="${PIPELINE_DAEMON:-${SCRIPT_DIR}/.claude/scripts/pipeline-daemon.py}"
LOG_FILE="${LSD_LOG_FILE:-${HOME}/.claude/logs/ai-pipeline-daemon.log}"
LOCK_FILE="${STATE_DIR}/locks/team.lock"
PAUSE_FLAG="${STATE_DIR}/locks/team.paused"
ACTIVE_JSON="${STATE_DIR}/active.json"
COMPLETED_JSONL="${STATE_DIR}/completed.jsonl"
TASKS_DIR="${STATE_DIR}/tasks"

STOP_TIMEOUT_SEC=30
DEFAULT_RECENT_COMPLETED=5

# ---------------------------------------------------------------------------
# Colors (NO_COLOR / --no-color supported)
# ---------------------------------------------------------------------------
USE_COLOR=1
if [ -n "${NO_COLOR:-}" ]; then USE_COLOR=0; fi
if ! [ -t 1 ]; then USE_COLOR=0; fi

C_RESET=""
C_GREEN=""
C_RED=""
C_YELLOW=""
C_BOLD=""
C_DIM=""

setup_colors() {
  if [ "$USE_COLOR" = "1" ] && command -v tput >/dev/null 2>&1; then
    if tput colors >/dev/null 2>&1; then
      C_RESET="$(tput sgr0 || true)"
      C_GREEN="$(tput setaf 2 || true)"
      C_RED="$(tput setaf 1 || true)"
      C_YELLOW="$(tput setaf 3 || true)"
      C_BOLD="$(tput bold || true)"
      C_DIM="$(tput dim || true)"
    fi
  fi
}

ok()   { printf "%s%s%s %s\n"   "$C_GREEN"  "[OK]"    "$C_RESET" "$*"; }
err()  { printf "%s%s%s %s\n"   "$C_RED"    "[ERROR]" "$C_RESET" "$*" >&2; }
warn() { printf "%s%s%s %s\n"   "$C_YELLOW" "[WARN]"  "$C_RESET" "$*" >&2; }
info() { printf "%s\n" "$*"; }

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
HAS_JQ=0
HAS_PY=0

check_deps() {
  if command -v jq >/dev/null 2>&1; then HAS_JQ=1; fi
  if command -v python3 >/dev/null 2>&1; then HAS_PY=1; fi

  if [ "$HAS_JQ" = "0" ] && [ "$HAS_PY" = "0" ]; then
    err "Neither jq nor python3 found. At least one is required for JSON parsing."
    err "macOS: brew install jq    |    python3 should already be on the system."
    exit 1
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 missing — daemon cannot be started. brew install python"
  fi

  if ! command -v claude >/dev/null 2>&1; then
    warn "claude CLI missing — 'start' command cannot invoke the planner."
    warn "Install: https://docs.claude.com/claude-code"
  fi
}

# ---------------------------------------------------------------------------
# JSON helpers (jq if available, otherwise python3 fallback)
# ---------------------------------------------------------------------------
json_get() {
  # json_get <file> <jq-expression>  →  string output
  local file="$1" expr="$2"
  if [ ! -f "$file" ]; then echo ""; return 0; fi
  if [ "$HAS_JQ" = "1" ]; then
    jq -r "$expr // empty" "$file" 2>/dev/null || true
  else
    python3 - "$file" "$expr" <<'PYEOF' 2>/dev/null || true
import json, sys, re
path, expr = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    sys.exit(0)
# Very small jq subset: ".a.b", ".a[0].b", ".a // empty" (// empty ignored)
expr = re.sub(r'\s*//\s*empty\s*$', '', expr).strip()
if expr.startswith('.'):
    expr = expr[1:]
parts = re.findall(r'[^.\[\]]+|\[\d+\]', expr) if expr else []
cur = data
try:
    for p in parts:
        if p.startswith('['):
            cur = cur[int(p[1:-1])]
        else:
            cur = cur[p]
    if cur is None:
        sys.exit(0)
    if isinstance(cur, (dict, list)):
        print(json.dumps(cur))
    else:
        print(cur)
except Exception:
    sys.exit(0)
PYEOF
  fi
}

active_tasks_count() {
  if [ ! -f "$ACTIVE_JSON" ]; then echo 0; return; fi
  if [ "$HAS_JQ" = "1" ]; then
    jq -r '(.tasks // []) | length' "$ACTIVE_JSON" 2>/dev/null || echo 0
  else
    python3 - "$ACTIVE_JSON" <<'PYEOF' 2>/dev/null || echo 0
import json, sys
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
    print(len(d.get("tasks", [])))
except Exception:
    print(0)
PYEOF
  fi
}

# ---------------------------------------------------------------------------
# Lock / pid helpers
# ---------------------------------------------------------------------------
ensure_state_dirs() {
  mkdir -p "$STATE_DIR" "$STATE_DIR/locks" "$TASKS_DIR"
  mkdir -p "$(dirname "$LOG_FILE")"
}

read_lock_pid() {
  # 0 → pid printed to stdout; 1 → no lock / unparsable
  if [ ! -f "$LOCK_FILE" ]; then return 1; fi
  local pid
  pid="$(json_get "$LOCK_FILE" '.pid')"
  if [ -z "$pid" ]; then
    # Maybe a plain pid (not json) — fallback
    pid="$(tr -dc '0-9' < "$LOCK_FILE" | head -c 12 || true)"
  fi
  if [ -z "$pid" ]; then return 1; fi
  echo "$pid"
}

read_lock_started_at() {
  if [ ! -f "$LOCK_FILE" ]; then return 1; fi
  json_get "$LOCK_FILE" '.started_at'
}

is_pid_alive() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

daemon_status() {
  # echo: RUNNING | STOPPED | PAUSED | STALE
  if [ ! -f "$LOCK_FILE" ]; then echo "STOPPED"; return; fi
  local pid
  if ! pid="$(read_lock_pid)"; then
    echo "STALE"; return
  fi
  if is_pid_alive "$pid"; then
    if [ -f "$PAUSE_FLAG" ]; then echo "PAUSED"; else echo "RUNNING"; fi
  else
    echo "STALE"
  fi
}

# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------
to_epoch() {
  # ISO 8601 UTC (ending in 'Z') → epoch seconds; macOS BSD date compatible
  local iso="$1"
  if [ -z "$iso" ] || [ "$iso" = "null" ]; then echo 0; return; fi
  local clean="${iso%Z}"
  clean="${clean%%.*}"   # trim fractional seconds
  clean="${clean//T/ }"
  if date -j -u -f "%Y-%m-%d %H:%M:%S" "$clean" "+%s" 2>/dev/null; then
    return
  fi
  if date -u -d "$iso" "+%s" 2>/dev/null; then
    return
  fi
  echo 0
}

format_duration() {
  # seconds → H:MM:SS
  local s="${1:-0}"
  if [ "$s" -lt 0 ]; then s=0; fi
  printf "%d:%02d:%02d" $((s/3600)) $(((s%3600)/60)) $((s%60))
}

now_epoch() { date -u +%s; }

# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------
cmd_help() {
  cat <<'HELP'
team.sh — AI Pipeline development team CLI

COMMANDS
  start "<task description>"   Start new work: runs the planner agent, then
                               spawns the pipeline daemon in the background.
                               To resume after a crash: team.sh resume-daemon
  stop                         Send SIGTERM to the pipeline daemon (graceful shutdown).
  resume-daemon                Restart daemon in background (does NOT call planner).
                               Used after crash/reboot — meta.json.role_done flags
                               let it pick up from where it left off.
  pause                        Create pause flag — no new spawns, existing
                               agents finish their work.
  resume                       Remove the pause flag.
  status [--json]              Active task table + recent completions +
                               daemon state. --json for machine-readable output.
  logs <task-id>               Tail .state/tasks/<id>/handoffs.jsonl.
  logs --daemon                Tail the daemon log file.
  help                         This help text.

EXAMPLE USAGE
  ./team.sh start "Write the formal Provider SPI contract"
  ./team.sh start "5 native provider adapters (Anthropic, OpenAI, Gemini, Mistral, Ollama)"
  ./team.sh status
  ./team.sh status --json | jq '.daemon.state'
  ./team.sh logs 20260427-1432-spi
  ./team.sh logs --daemon
  ./team.sh pause
  ./team.sh resume
  ./team.sh stop

EXIT CODES
  0  success
  1  usage error (bad argument)
  2  state error (daemon already running / not running)
  3  subprocess error (planner / daemon failed to start)

ENV
  PIPELINE_STATE_DIR     Path to .state directory (default: <repo>/.state)
  PIPELINE_DAEMON        Path to pipeline daemon python script
  LSD_LOG_FILE           Daemon log file (default: ~/.claude/logs/ai-pipeline-daemon.log)
  NO_COLOR               Disable colored output
HELP
}

# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------
cmd_start() {
  local task_desc="${1:-}"
  if [ -z "$task_desc" ]; then
    err "Task description required."
    info "Usage: team.sh start \"<task description>\""
    exit 1
  fi

  ensure_state_dirs

  local state
  state="$(daemon_status)"
  case "$state" in
    RUNNING|PAUSED)
      local pid
      pid="$(read_lock_pid 2>/dev/null || echo "?")"
      err "Pipeline daemon already running (pid ${pid}). Use 'team.sh stop' first, or pause/resume."
      exit 2
      ;;
    STALE)
      warn "Stale lock file found, dead pid. Cleaning up."
      rm -f "$LOCK_FILE"
      ;;
  esac

  if ! command -v claude >/dev/null 2>&1; then
    err "claude CLI not found — cannot invoke planner subprocess."
    exit 3
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    err "python3 not found — daemon cannot be started."
    exit 3
  fi

  if [ ! -f "$DAEMON_PATH" ]; then
    err "Daemon script not found: $DAEMON_PATH"
    err "Override the path with the PIPELINE_DAEMON env var."
    exit 3
  fi

  info "${C_BOLD}Running planner agent...${C_RESET}"
  info "Task: ${task_desc}"
  echo

  # Invoke the planner agent as a subprocess.
  # The planner writes output to .state/active.json and .state/tasks/<id>/meta.json.
  local planner_prompt
  planner_prompt="You are the AI Pipeline planner agent. Take the following task, split it into "
  planner_prompt+="subtasks, and create the .state/active.json and .state/tasks/<id>/meta.json files. "
  planner_prompt+="Conform to the .state/README.md schema. Task: ${task_desc}"

  set +e
  claude -p "$planner_prompt"
  local planner_rc=$?
  set -e

  if [ "$planner_rc" -ne 0 ]; then
    err "Planner subprocess returned an error code: ${planner_rc}"
    exit 3
  fi

  ok "Planner completed."

  # Start the daemon in the background
  info "Starting pipeline daemon in the background..."
  nohup python3 "$DAEMON_PATH" >> "$LOG_FILE" 2>&1 &
  local daemon_pid=$!
  disown "$daemon_pid" 2>/dev/null || true

  # The daemon will write its own pid into the lock; give it a brief grace period.
  sleep 1

  if is_pid_alive "$daemon_pid"; then
    ok "Team started (daemon pid ${daemon_pid})."
    info "  Status: ${C_BOLD}team.sh status${C_RESET}"
    info "  Logs:   ${C_BOLD}team.sh logs --daemon${C_RESET}"
    info "  Stop:   ${C_BOLD}team.sh stop${C_RESET}"
  else
    err "Daemon started but died immediately — log: $LOG_FILE"
    exit 3
  fi
}

# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------
cmd_stop() {
  if [ ! -f "$LOCK_FILE" ]; then
    info "Pipeline daemon is not running."
    exit 0
  fi

  local pid
  if ! pid="$(read_lock_pid)"; then
    warn "Lock file is corrupt, removing."
    rm -f "$LOCK_FILE"
    exit 0
  fi

  if ! is_pid_alive "$pid"; then
    warn "Pid (${pid}) in lock is not running. Cleaning up lock."
    rm -f "$LOCK_FILE"
    exit 0
  fi

  info "Sending SIGTERM (pid ${pid})..."
  kill -TERM "$pid" 2>/dev/null || true

  local waited=0
  while is_pid_alive "$pid"; do
    if [ "$waited" -ge "$STOP_TIMEOUT_SEC" ]; then
      warn "${STOP_TIMEOUT_SEC}s exceeded, sending SIGKILL."
      kill -KILL "$pid" 2>/dev/null || true
      sleep 1
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done

  # If the daemon did not clean its own lock, do it ourselves
  if [ -f "$LOCK_FILE" ]; then
    if ! is_pid_alive "$pid"; then
      rm -f "$LOCK_FILE"
    fi
  fi

  ok "Pipeline daemon stopped (pid ${pid})."
}

# ---------------------------------------------------------------------------
# resume-daemon — restart the daemon after crash/reboot (does NOT invoke planner)
# ---------------------------------------------------------------------------
cmd_resume_daemon() {
  ensure_state_dirs

  # Is the daemon already running?
  local state
  state="$(daemon_status)"
  case "$state" in
    RUNNING|PAUSED)
      local pid
      pid="$(read_lock_pid 2>/dev/null || echo "?")"
      err "Pipeline daemon already running (pid ${pid}). Stop it with 'team.sh stop' and try again."
      exit 2
      ;;
    STALE)
      warn "Stale lock file found, dead pid. Cleaning up."
      rm -f "$LOCK_FILE"
      ;;
  esac

  if ! command -v python3 >/dev/null 2>&1; then
    err "python3 not found — daemon cannot be started."
    exit 3
  fi

  if [ ! -f "$DAEMON_PATH" ]; then
    err "Daemon script not found: $DAEMON_PATH"
    err "Override the path with the PIPELINE_DAEMON env var."
    exit 3
  fi

  # Is active.json empty? Just a warning — the daemon still starts; the user
  # can add a new task via 'start'.
  local active_count
  active_count="$(active_tasks_count)"
  if [ -z "$active_count" ] || [ "$active_count" = "0" ]; then
    warn "active.json is empty — no task to resume. Daemon will start anyway (waiting for new tasks)."
  else
    info "Active task count: ${active_count} — daemon will resume via role_done flags."
  fi

  info "Starting pipeline daemon in the background..."
  nohup python3 "$DAEMON_PATH" >> "$LOG_FILE" 2>&1 &
  local daemon_pid=$!
  disown "$daemon_pid" 2>/dev/null || true

  sleep 1

  if is_pid_alive "$daemon_pid"; then
    ok "Daemon restarted (pid ${daemon_pid})."
    info "  Status: ${C_BOLD}team.sh status${C_RESET}"
    info "  Logs:   ${C_BOLD}team.sh logs --daemon${C_RESET}"
    info "  Stop:   ${C_BOLD}team.sh stop${C_RESET}"
  else
    err "Daemon started but died immediately — log: $LOG_FILE"
    exit 3
  fi
}

# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------
cmd_pause() {
  if [ ! -f "$LOCK_FILE" ]; then
    err "Pipeline daemon is not running, pause is meaningless."
    exit 2
  fi
  ensure_state_dirs
  touch "$PAUSE_FLAG"
  ok "Team paused."
  info "Currently running agents will finish, but no new work will start."
  info "Resume: ${C_BOLD}team.sh resume${C_RESET}"
}

cmd_resume() {
  if [ -f "$PAUSE_FLAG" ]; then
    rm -f "$PAUSE_FLAG"
    ok "Team resumed."
  else
    info "No pause flag present — team is already running."
  fi
}

# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
print_status_human() {
  local state pid started_at uptime_s
  state="$(daemon_status)"

  local header="=== AI Pipeline Team Status ==="
  printf "%s%s%s\n" "$C_BOLD" "$header" "$C_RESET"

  case "$state" in
    RUNNING)
      pid="$(read_lock_pid 2>/dev/null || echo "?")"
      started_at="$(read_lock_started_at 2>/dev/null || echo "?")"
      local s_epoch
      s_epoch="$(to_epoch "$started_at")"
      if [ "$s_epoch" -gt 0 ]; then
        uptime_s=$(( $(now_epoch) - s_epoch ))
      else
        uptime_s=0
      fi
      printf "Daemon: %sRUNNING%s (pid %s, started %s, uptime %s)\n" \
        "$C_GREEN" "$C_RESET" "$pid" "${started_at:-?}" "$(format_duration "$uptime_s")"
      ;;
    PAUSED)
      pid="$(read_lock_pid 2>/dev/null || echo "?")"
      printf "Daemon: %sPAUSED%s (pid %s) — no new spawns\n" "$C_YELLOW" "$C_RESET" "$pid"
      ;;
    STALE)
      printf "Daemon: %sSTALE%s — lock present but pid dead (clean with ./team.sh stop)\n" \
        "$C_YELLOW" "$C_RESET"
      ;;
    STOPPED|*)
      printf "Daemon: %sSTOPPED%s\n" "$C_RED" "$C_RESET"
      ;;
  esac
  echo

  # --- Active tasks ---
  local n
  n="$(active_tasks_count)"
  printf "${C_BOLD}Active Tasks (%s):${C_RESET}\n" "$n"
  if [ "$n" = "0" ] || [ -z "$n" ]; then
    printf "  ${C_DIM}(empty)${C_RESET}\n"
  else
    printf "%-26s %-12s %-14s %-15s %-10s\n" "ID" "MODULE" "STATUS" "OWNER" "UPTIME"
    printf "%-26s %-12s %-14s %-15s %-10s\n" "--------------------------" "------------" "--------------" "---------------" "----------"
    if [ "$HAS_JQ" = "1" ]; then
      jq -r '.tasks[]? | [.id, (.module // "-"), (.status // "-"), (.owner_agent // "-"), (.started_at // "")] | @tsv' \
        "$ACTIVE_JSON" 2>/dev/null | while IFS=$'\t' read -r tid tmod tstat town tstart; do
          local ep up
          ep="$(to_epoch "$tstart")"
          if [ "$ep" -gt 0 ]; then up=$(( $(now_epoch) - ep )); else up=0; fi
          printf "%-26s %-12s %-14s %-15s %-10s\n" \
            "${tid:0:26}" "${tmod:0:12}" "${tstat:0:14}" "${town:0:15}" "$(format_duration "$up")"
        done
    else
      python3 - "$ACTIVE_JSON" <<'PYEOF'
import json, sys, time
from datetime import datetime, timezone
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
except Exception:
    sys.exit(0)
def parse_iso(s):
    if not s: return 0
    try:
        s = s.rstrip('Z').split('.')[0]
        dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0
def fmt(s):
    s = max(0, s)
    return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"
now = int(time.time())
for t in d.get("tasks", []):
    ep = parse_iso(t.get("started_at"))
    up = now - ep if ep else 0
    print(f"{(t.get('id') or '-')[:26]:<26} {(t.get('module') or '-')[:12]:<12} "
          f"{(t.get('status') or '-')[:14]:<14} {(t.get('owner_agent') or '-')[:15]:<15} "
          f"{fmt(up):<10}")
PYEOF
    fi
  fi
  echo

  # --- Recent completed ---
  printf "${C_BOLD}Recent Completed (last %d):${C_RESET}\n" "$DEFAULT_RECENT_COMPLETED"
  if [ ! -s "$COMPLETED_JSONL" ]; then
    printf "  ${C_DIM}(empty)${C_RESET}\n"
  else
    printf "%-26s %-12s %-10s %-10s %-8s\n" "ID" "MODULE" "OUTCOME" "DURATION" "TOKENS"
    printf "%-26s %-12s %-10s %-10s %-8s\n" "--------------------------" "------------" "----------" "----------" "--------"
    if [ "$HAS_JQ" = "1" ]; then
      tail -n "$DEFAULT_RECENT_COMPLETED" "$COMPLETED_JSONL" 2>/dev/null \
        | jq -r '. | [(.id // "-"), (.module // "-"), (.outcome // "-"), (.duration_sec // 0), (.token_total // 0)] | @tsv' 2>/dev/null \
        | while IFS=$'\t' read -r cid cmod coutc cdur ctok; do
            local dur_s tok_s
            dur_s="$(format_duration "${cdur:-0}")"
            if [ "${ctok:-0}" -ge 1000 ]; then
              tok_s="$(( ctok / 1000 ))k"
            else
              tok_s="${ctok:-0}"
            fi
            printf "%-26s %-12s %-10s %-10s %-8s\n" \
              "${cid:0:26}" "${cmod:0:12}" "${coutc:0:10}" "$dur_s" "$tok_s"
          done
    else
      tail -n "$DEFAULT_RECENT_COMPLETED" "$COMPLETED_JSONL" 2>/dev/null | python3 - <<'PYEOF'
import json, sys
def fmt(s):
    s = max(0, int(s or 0))
    return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"
def tk(n):
    n = int(n or 0)
    return f"{n//1000}k" if n >= 1000 else str(n)
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        t = json.loads(line)
    except Exception:
        continue
    print(f"{(t.get('id') or '-')[:26]:<26} {(t.get('module') or '-')[:12]:<12} "
          f"{(t.get('outcome') or '-')[:10]:<10} {fmt(t.get('duration_sec')):<10} "
          f"{tk(t.get('token_total')):<8}")
PYEOF
    fi
  fi
  echo
  printf "%s%s%s\n" "$C_BOLD" "==============================" "$C_RESET"
}

print_status_json() {
  local state pid started_at
  state="$(daemon_status)"
  pid="$(read_lock_pid 2>/dev/null || echo "")"
  started_at="$(read_lock_started_at 2>/dev/null || echo "")"

  if [ "$HAS_JQ" = "1" ]; then
    local active_tasks="[]" completed_tail="[]"
    if [ -f "$ACTIVE_JSON" ]; then
      active_tasks="$(jq -c '.tasks // []' "$ACTIVE_JSON" 2>/dev/null || echo "[]")"
    fi
    if [ -s "$COMPLETED_JSONL" ]; then
      completed_tail="$(tail -n "$DEFAULT_RECENT_COMPLETED" "$COMPLETED_JSONL" 2>/dev/null \
        | jq -s -c '.' 2>/dev/null || echo "[]")"
    fi
    jq -n \
      --arg state "$state" \
      --arg pid "$pid" \
      --arg started_at "$started_at" \
      --arg state_dir "$STATE_DIR" \
      --argjson active "$active_tasks" \
      --argjson completed "$completed_tail" \
      '{
        daemon: {
          state: $state,
          pid: (try ($pid | tonumber) catch null),
          started_at: (if ($started_at | length) > 0 then $started_at else null end)
        },
        active_tasks: $active,
        recent_completed: $completed,
        state_dir: $state_dir
      }'
  else
    python3 - "$state" "$pid" "$started_at" "$STATE_DIR" "$ACTIVE_JSON" "$COMPLETED_JSONL" "$DEFAULT_RECENT_COMPLETED" <<'PYEOF'
import json, sys, os
state, pid, started_at, state_dir, active_path, completed_path, n_str = sys.argv[1:8]
n = int(n_str)
out = {
  "daemon": {
    "state": state,
    "pid": int(pid) if pid.isdigit() else None,
    "started_at": started_at or None,
  },
  "active_tasks": [],
  "recent_completed": [],
  "state_dir": state_dir,
}
try:
  with open(active_path) as f:
    out["active_tasks"] = json.load(f).get("tasks", [])
except Exception:
  pass
try:
  with open(completed_path) as f:
    lines = [l for l in f.read().splitlines() if l.strip()]
  for l in lines[-n:]:
    try:
      out["recent_completed"].append(json.loads(l))
    except Exception:
      continue
except Exception:
  pass
print(json.dumps(out, indent=2))
PYEOF
  fi
}

cmd_status() {
  if [ "${1:-}" = "--json" ]; then
    print_status_json
  else
    print_status_human
  fi
}

# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------
cmd_logs() {
  local arg="${1:-}"
  if [ -z "$arg" ]; then
    err "Usage: team.sh logs <task-id>  |  team.sh logs --daemon"
    exit 1
  fi

  if [ "$arg" = "--daemon" ]; then
    if [ ! -f "$LOG_FILE" ]; then
      warn "Daemon log file does not exist yet: $LOG_FILE"
      info "The daemon may have never run, or it may be writing elsewhere."
      exit 0
    fi
    info "Tail: $LOG_FILE  (CTRL+C to exit)"
    exec tail -f "$LOG_FILE"
  fi

  local task_id="$arg"
  local task_dir="${TASKS_DIR}/${task_id}"
  if [ ! -d "$task_dir" ]; then
    err "Task directory not found: $task_dir"
    info "For active task IDs: team.sh status"
    exit 1
  fi

  local handoffs="${task_dir}/handoffs.jsonl"
  if [ ! -f "$handoffs" ]; then
    warn "handoffs.jsonl missing, creating (empty)."
    : > "$handoffs"
  fi

  info "Tail: $handoffs  (CTRL+C to exit)"
  if [ "$HAS_JQ" = "1" ]; then
    # Pretty-print the tail -f output through jq
    tail -n 50 -f "$handoffs" | jq -C --unbuffered '.' 2>/dev/null || tail -f "$handoffs"
  else
    exec tail -n 50 -f "$handoffs"
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  # Evaluate --no-color flag early (accepted anywhere)
  local args=()
  for a in "$@"; do
    case "$a" in
      --no-color) USE_COLOR=0 ;;
      *) args+=("$a") ;;
    esac
  done
  set -- "${args[@]+"${args[@]}"}"

  setup_colors
  check_deps

  local cmd="${1:-help}"
  shift || true

  case "$cmd" in
    start)         cmd_start "${1:-}" ;;
    stop)          cmd_stop ;;
    resume-daemon) cmd_resume_daemon ;;
    pause)         cmd_pause ;;
    resume)        cmd_resume ;;
    status)        cmd_status "${1:-}" ;;
    logs)          cmd_logs "${1:-}" ;;
    help|-h|--help) cmd_help ;;
    *)
      err "Unknown command: $cmd"
      cmd_help
      exit 1
      ;;
  esac
}

main "$@"

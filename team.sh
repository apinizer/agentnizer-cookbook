#!/usr/bin/env bash
# team.sh — Reference CLI for the cookbook pipeline pattern.
#
# This is a working starter. Adapt freely to your repo. Pairs with
# .claude/scripts/pipeline-daemon.py (the daemon implementation).
#
# Commands:  start | stop | resume-daemon | pause | resume | status | logs | tokens | help
# Docs:      see README.md and docs/blog/sprint-contract.md

set -euo pipefail

# ---------------------------------------------------------------------------
# Bash version check (associative arrays require bash 4+)
# ---------------------------------------------------------------------------
if [ -z "${BASH_VERSINFO+x}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  echo "ERROR: bash 4+ required. Found: ${BASH_VERSION:-unknown}" >&2
  echo "On macOS: brew install bash; then run as 'bash team.sh ...'" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${COOKBOOK_STATE_DIR:-${SCRIPT_DIR}/.state}"
DAEMON_PATH="${COOKBOOK_DAEMON:-${SCRIPT_DIR}/.claude/scripts/pipeline-daemon.py}"
LOG_FILE="${LSD_LOG_FILE:-${HOME}/.claude/logs/cookbook-team-daemon.log}"
LOCK_FILE="${STATE_DIR}/locks/team.lock"
PAUSE_FLAG="${STATE_DIR}/locks/team.paused"
ACTIVE_JSON="${STATE_DIR}/active.json"
COMPLETED_JSONL="${STATE_DIR}/completed.jsonl"
TASKS_DIR="${STATE_DIR}/tasks"

STOP_TIMEOUT_SEC=30
DEFAULT_RECENT_COMPLETED=5

# ---------------------------------------------------------------------------
# Colors (NO_COLOR / --no-color aware)
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
    warn "python3 missing — daemon cannot start. brew install python"
  fi

  if ! command -v claude >/dev/null 2>&1; then
    warn "claude CLI missing — 'start' command cannot invoke the planner."
    warn "Install: https://docs.claude.com/claude-code"
  fi
}

# ---------------------------------------------------------------------------
# JSON helpers (jq if available, python3 fallback otherwise)
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
# Tiny jq subset: ".a.b", ".a[0].b", ".a // empty" (// empty stripped)
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
  # 0 → pid printed to stdout; 1 → no lock / unparseable
  if [ ! -f "$LOCK_FILE" ]; then return 1; fi
  local pid
  pid="$(json_get "$LOCK_FILE" '.pid')"
  if [ -z "$pid" ]; then
    # Maybe a plain pid was written (not JSON) — fallback
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
  # ISO 8601 UTC (ending with 'Z') → epoch seconds; macOS BSD date compatible
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
team.sh — Reference CLI for the cookbook pipeline pattern

COMMANDS
  start "<task description>"   Kick off a new task: run the planner agent,
                               then spawn the pipeline daemon in the background.
                               To resume after a crash: team.sh resume-daemon
  stop                         Send SIGTERM to the daemon, graceful shutdown.
  resume-daemon                Restart the daemon in the background (no planner).
                               Use after a crash/reboot — meta.json.role_done
                               flags let it pick up where it left off.
  pause                        Create a pause flag — no new spawns; running
                               agents complete normally.
  resume                       Remove the pause flag.
  status [--json]              Active tasks table + recent completed +
                               daemon state. --json for machine-readable.
  logs <task-id>               tail -f .state/tasks/<id>/handoffs.jsonl
  logs --daemon                tail -f the daemon log file.
  tokens [--json|--daily|--wave|--cache]  Token + cost report.
                               --daily : last 14 days
                               --wave  : group by meta.json.wave field
                               --cache : cache hit ratio breakdown
                               --json  : machine-readable full aggregation
                               (default: module/role/model breakdown)
  help                         This help text.

EXAMPLES
  ./team.sh start "Implement Provider SPI contract"
  ./team.sh start "Add five native LLM adapters"
  ./team.sh status
  ./team.sh status --json | jq '.daemon.state'
  ./team.sh logs 20260427-1432-spi
  ./team.sh logs --daemon
  ./team.sh pause
  ./team.sh resume
  ./team.sh stop

EXIT CODES
  0  success
  1  usage error (bad arguments)
  2  state error (daemon already running / not running)
  3  subprocess error (planner / daemon failed to start)

ENV
  COOKBOOK_STATE_DIR     Path to .state directory (default: <repo>/.state)
  COOKBOOK_DAEMON        Path to the daemon python script
  LSD_LOG_FILE           Daemon log file (default: ~/.claude/logs/cookbook-team-daemon.log)
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
      err "Daemon is already running (pid ${pid}). Use 'team.sh stop' or pause/resume first."
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
    err "python3 not found — cannot start daemon."
    exit 3
  fi

  if [ ! -f "$DAEMON_PATH" ]; then
    err "Daemon script not found: $DAEMON_PATH"
    err "Override with the COOKBOOK_DAEMON env var."
    exit 3
  fi

  info "${C_BOLD}Running planner agent...${C_RESET}"
  info "Task: ${task_desc}"
  echo

  # Invoke planner agent as a subprocess.
  # Planner writes .state/active.json + .state/tasks/<id>/meta.json.
  local planner_prompt
  planner_prompt="You are the planner agent. Decompose the task below into "
  planner_prompt+="sub-tasks and create .state/active.json plus "
  planner_prompt+=".state/tasks/<id>/meta.json files. Follow the schema in "
  planner_prompt+=".state/README.md. Task: ${task_desc}"

  set +e
  claude -p "$planner_prompt"
  local planner_rc=$?
  set -e

  if [ "$planner_rc" -ne 0 ]; then
    err "Planner subprocess returned error code: ${planner_rc}"
    exit 3
  fi

  ok "Planner finished."

  # Start daemon in the background
  info "Starting pipeline daemon in background..."
  nohup python3 "$DAEMON_PATH" >> "$LOG_FILE" 2>&1 &
  local daemon_pid=$!
  disown "$daemon_pid" 2>/dev/null || true

  # Daemon will write its own pid into the lock; give it a short grace window.
  sleep 1

  if is_pid_alive "$daemon_pid"; then
    ok "Pipeline started (daemon pid ${daemon_pid})."
    info "  Status: ${C_BOLD}team.sh status${C_RESET}"
    info "  Log:    ${C_BOLD}team.sh logs --daemon${C_RESET}"
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
    info "Daemon is not running."
    exit 0
  fi

  local pid
  if ! pid="$(read_lock_pid)"; then
    warn "Lock file is corrupt, removing."
    rm -f "$LOCK_FILE"
    exit 0
  fi

  if ! is_pid_alive "$pid"; then
    warn "Pid in lock (${pid}) is not running. Cleaning lock."
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

  # If the daemon didn't clean its own lock, do it now
  if [ -f "$LOCK_FILE" ]; then
    if ! is_pid_alive "$pid"; then
      rm -f "$LOCK_FILE"
    fi
  fi

  ok "Daemon stopped (pid ${pid})."
}

# ---------------------------------------------------------------------------
# resume-daemon — restart the daemon after a crash/reboot (planner NOT invoked)
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
      err "Daemon already running (pid ${pid}). Stop with 'team.sh stop' first."
      exit 2
      ;;
    STALE)
      warn "Stale lock file found, dead pid. Cleaning up."
      rm -f "$LOCK_FILE"
      ;;
  esac

  if ! command -v python3 >/dev/null 2>&1; then
    err "python3 not found — cannot start daemon."
    exit 3
  fi

  if [ ! -f "$DAEMON_PATH" ]; then
    err "Daemon script not found: $DAEMON_PATH"
    err "Override with the COOKBOOK_DAEMON env var."
    exit 3
  fi

  # Is active.json empty? Just a warning — the daemon still starts and the
  # user can add a new task via 'start'.
  local active_count
  active_count="$(active_tasks_count)"
  if [ -z "$active_count" ] || [ "$active_count" = "0" ]; then
    warn "active.json is empty — no task to continue. Daemon will start anyway and wait for new tasks."
  else
    info "Active task count: ${active_count} — daemon will resume from role_done flags."
  fi

  info "Starting pipeline daemon in background..."
  nohup python3 "$DAEMON_PATH" >> "$LOG_FILE" 2>&1 &
  local daemon_pid=$!
  disown "$daemon_pid" 2>/dev/null || true

  sleep 1

  if is_pid_alive "$daemon_pid"; then
    ok "Daemon restarted (pid ${daemon_pid})."
    info "  Status: ${C_BOLD}team.sh status${C_RESET}"
    info "  Log:    ${C_BOLD}team.sh logs --daemon${C_RESET}"
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
    err "Daemon is not running, pause is meaningless."
    exit 2
  fi
  ensure_state_dirs
  touch "$PAUSE_FLAG"
  ok "Pipeline paused."
  info "Currently running agents will finish but no new work will start."
  info "Resume: ${C_BOLD}team.sh resume${C_RESET}"
}

cmd_resume() {
  if [ -f "$PAUSE_FLAG" ]; then
    rm -f "$PAUSE_FLAG"
    ok "Pipeline resumed."
  else
    info "No pause flag present — pipeline is already running."
  fi
}

# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
print_status_human() {
  local state pid started_at uptime_s
  state="$(daemon_status)"

  local header="=== Cookbook Pipeline Status ==="
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
      printf "Daemon: %sSTALE%s — lock present but pid is dead (run ./team.sh stop to clean)\n" \
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
      info "Daemon may have never run or is writing elsewhere."
      exit 0
    fi
    info "Tail: $LOG_FILE  (CTRL+C to exit)"
    exec tail -f "$LOG_FILE"
  fi

  local task_id="$arg"
  local task_dir="${TASKS_DIR}/${task_id}"
  if [ ! -d "$task_dir" ]; then
    err "Task directory not found: $task_dir"
    info "For active task IDs run: team.sh status"
    exit 1
  fi

  local handoffs="${task_dir}/handoffs.jsonl"
  if [ ! -f "$handoffs" ]; then
    warn "handoffs.jsonl missing, creating empty file."
    : > "$handoffs"
  fi

  info "Tail: $handoffs  (CTRL+C to exit)"
  if [ "$HAS_JQ" = "1" ]; then
    # Pretty-print tail -f output through jq
    tail -n 50 -f "$handoffs" | jq -C --unbuffered '.' 2>/dev/null || tail -f "$handoffs"
  else
    exec tail -n 50 -f "$handoffs"
  fi
}

# ---------------------------------------------------------------------------
# tokens — aggregation report (active + completed + archive)
# ---------------------------------------------------------------------------
cmd_tokens() {
  local fmt="${1:-text}"
  python3 - "$STATE_DIR" "$fmt" <<'PYEOF'
import json, sys, re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

state_dir = Path(sys.argv[1])
fmt = sys.argv[2]
tasks_dir = state_dir / "tasks"
archive_dir = state_dir / "archive"

mod_agg = defaultdict(lambda: {"count":0, "tokens":0, "cost_usd":0.0})
role_agg = defaultdict(lambda: {"calls":0, "tokens":0})
model_agg = defaultdict(lambda: {"input":0, "output":0, "cache_read":0, "cache_creation":0, "cost_usd":0.0})
wave_agg = defaultdict(lambda: {"count":0, "tokens":0, "cost_usd":0.0})
daily_agg = defaultdict(lambda: {"count":0, "tokens":0, "cost_usd":0.0})
break_agg = {"input":0, "output":0, "cache_read":0, "cache_creation":0}
total_tokens = 0
total_cost = 0.0
total_tasks = 0

def task_date(meta):
    ts = meta.get("created_at") or meta.get("updated_at") or ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(ts))
    if m: return m.group(1)
    tid = meta.get("id","")
    if len(tid) >= 8 and tid[:8].isdigit():
        return f"{tid[:4]}-{tid[4:6]}-{tid[6:8]}"
    return "unknown"

def task_wave(meta):
    # Simple grouping: if meta.json has a 'wave' field, use it as-is.
    # Otherwise everything is "ungrouped". No internal classification.
    w = meta.get("wave")
    if w in (None, "", "-"):
        return "ungrouped"
    return str(w)

def absorb(meta):
    global total_tokens, total_cost, total_tasks
    if not isinstance(meta, dict): return
    total_tasks += 1
    mod = meta.get("module") or "?"
    tk = int(meta.get("token_used") or 0)
    cost = float(meta.get("token_cost_usd") or 0.0)
    mod_agg[mod]["count"] += 1
    mod_agg[mod]["tokens"] += tk
    mod_agg[mod]["cost_usd"] += cost
    total_tokens += tk
    total_cost += cost
    # daily
    d = task_date(meta)
    daily_agg[d]["count"] += 1
    daily_agg[d]["tokens"] += tk
    daily_agg[d]["cost_usd"] += cost
    # wave
    w = task_wave(meta)
    wave_agg[w]["count"] += 1
    wave_agg[w]["tokens"] += tk
    wave_agg[w]["cost_usd"] += cost
    bd = meta.get("token_breakdown") or {}
    for k in break_agg:
        break_agg[k] += int(bd.get(k) or 0)
    pr = meta.get("token_per_role") or {}
    for r, v in pr.items():
        role_agg[r]["calls"] += 1
        role_agg[r]["tokens"] += int(v or 0)
    mu = meta.get("model_usage") or {}
    for mid, m in mu.items():
        if not isinstance(m, dict): continue
        model_agg[mid]["input"] += int(m.get("inputTokens") or 0)
        model_agg[mid]["output"] += int(m.get("outputTokens") or 0)
        model_agg[mid]["cache_read"] += int(m.get("cacheReadInputTokens") or 0)
        model_agg[mid]["cache_creation"] += int(m.get("cacheCreationInputTokens") or 0)
        model_agg[mid]["cost_usd"] += float(m.get("costUSD") or 0.0)

# active
for mp in tasks_dir.glob("*/meta.json"):
    try: absorb(json.loads(mp.read_text()))
    except Exception: pass
# archive
for mp in archive_dir.glob("*/meta.json"):
    try: absorb(json.loads(mp.read_text()))
    except Exception: pass

if fmt == "--json":
    out = {
        "total_tasks": total_tasks,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "breakdown": break_agg,
        "by_module": {k: dict(v, cost_usd=round(v["cost_usd"],4)) for k,v in mod_agg.items()},
        "by_role": dict(role_agg),
        "by_model": {k: dict(v, cost_usd=round(v["cost_usd"],4)) for k,v in model_agg.items()},
        "by_wave": {k: dict(v, cost_usd=round(v["cost_usd"],4)) for k,v in wave_agg.items()},
        "by_day": {k: dict(v, cost_usd=round(v["cost_usd"],4)) for k,v in sorted(daily_agg.items())},
    }
    print(json.dumps(out, indent=2))
elif fmt == "--daily":
    print(f"=== Daily Token + Cost (last 14 days) ===")
    print(f"{'Date':<12} {'Tasks':>6} {'Tokens':>14} {'Cost USD':>10}")
    print("-"*46)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    rows = sorted([(d,v) for d,v in daily_agg.items() if d >= cutoff or d == "unknown"])
    for d, v in rows:
        print(f"{d:<12} {v['count']:>6} {v['tokens']:>14,} {v['cost_usd']:>10.2f}")
    print("-"*46)
    print(f"{'TOTAL':<12} {sum(v['count'] for _,v in rows):>6} {sum(v['tokens'] for _,v in rows):>14,} {sum(v['cost_usd'] for _,v in rows):>10.2f}")
elif fmt == "--wave":
    print(f"=== Token + Cost by wave (meta.json.wave) ===")
    print(f"{'Wave':<24} {'Tasks':>6} {'Tokens':>14} {'Cost USD':>10}  {'Avg/task':>10}")
    print("-"*72)
    for w, v in sorted(wave_agg.items()):
        avg = v['tokens']//max(v['count'],1)
        print(f"{w:<24} {v['count']:>6} {v['tokens']:>14,} {v['cost_usd']:>10.2f}  {avg:>10,}")
elif fmt == "--cache":
    # Cache hit ratio: cache_read / (input + cache_read)
    # High ratio = prefix matching working well; low ratio = unstable prefix.
    total_input = break_agg["input"]
    total_cache_read = break_agg["cache_read"]
    total_cache_creation = break_agg["cache_creation"]
    cache_eligible = total_input + total_cache_read
    hit_ratio = (100 * total_cache_read / cache_eligible) if cache_eligible else 0.0
    print(f"=== Cache Hit Ratio (prefix optimization) ===")
    print(f"Cache-eligible input  : {cache_eligible:>14,} tokens")
    print(f"  cache_read (HIT)    : {total_cache_read:>14,} ({hit_ratio:5.1f}%)")
    print(f"  fresh input (MISS)  : {total_input:>14,} ({100-hit_ratio:5.1f}%)")
    print(f"  cache_creation      : {total_cache_creation:>14,} (write-through)")
    print()
    if total_cache_read > 0:
        # Anthropic pricing: cache_read = 10% of input price, cache_creation = 125%.
        # Effective savings vs fresh-only baseline: cache_read * 0.9 input-token-equivalent.
        saved = int(total_cache_read * 0.9)
        print(f"Estimated savings     : {saved:>14,} input-token-equivalent")
        print(f"  (cache_read at 90% discount; cache_creation 25% premium accounted)")
    print()
    print("Per-role token totals:")
    print(f"  {'Role':<22} {'Calls':>6} {'Tokens':>14}")
    print("-"*46)
    for r, d in sorted(role_agg.items(), key=lambda x: -x[1]["tokens"]):
        print(f"  {r:<22} {d['calls']:>6} {d['tokens']:>14,}")
    print()
    if hit_ratio < 30:
        print("WARNING: Cache hit < 30% — prompt prefix may be unstable.")
        print("    Check the stable prefix structure in build_agent_prompt.")
    elif hit_ratio > 70:
        print("OK: Cache hit > 70% — prefix optimization is effective.")
    else:
        print(f"INFO: Cache hit {hit_ratio:.0f}% — moderate; room for improvement.")
else:
    print(f"=== Cookbook Token Report ({total_tasks} tasks) ===")
    print(f"Total tokens : {total_tokens:>14,}")
    print(f"Total cost   : ${total_cost:>13,.2f}")
    print()
    print("Breakdown (input/output/cache):")
    for k, v in break_agg.items():
        pct = 100 * v / max(total_tokens, 1)
        print(f"  {k:<16} {v:>14,}  ({pct:5.1f}%)")
    print()
    print(f"{'Module':<14} {'Tasks':>5} {'Tokens':>14} {'Cost USD':>10}")
    print("-"*48)
    for mod, d in sorted(mod_agg.items(), key=lambda x: -x[1]["tokens"]):
        print(f"{mod:<14} {d['count']:>5} {d['tokens']:>14,} {d['cost_usd']:>10.2f}")
    print()
    print(f"{'Role':<22} {'Calls':>6} {'Tokens':>14}")
    print("-"*46)
    for r, d in sorted(role_agg.items(), key=lambda x: -x[1]["tokens"]):
        print(f"{r:<22} {d['calls']:>6} {d['tokens']:>14,}")
    print()
    if model_agg:
        print(f"{'Model':<32} {'Input':>10} {'Output':>10} {'Cache R':>12} {'Cost USD':>10}")
        print("-"*78)
        for mid, d in sorted(model_agg.items(), key=lambda x: -x[1]["cost_usd"]):
            print(f"{mid:<32} {d['input']:>10,} {d['output']:>10,} {d['cache_read']:>12,} {d['cost_usd']:>10.2f}")
PYEOF
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  # Pre-evaluate --no-color flag (accept it anywhere in argv)
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
    tokens)        cmd_tokens "${1:-}" ;;
    help|-h|--help) cmd_help ;;
    *)
      err "Unknown command: $cmd"
      cmd_help
      exit 1
      ;;
  esac
}

main "$@"

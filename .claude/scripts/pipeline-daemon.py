#!/usr/bin/env python3
"""
pipeline-daemon.py — AI Pipeline Local Team Daemon

Description:
    Pure-local poll daemon for an autonomous 13-role AI pipeline. Reads
    `.state/active.json` in a loop, inspects each task's `meta.json`
    `role_done` flags, and spawns the next agent(s) as Claude Code
    subprocesses. No sprint/tracker dependency — everything is local.

Usage:
    pipeline-daemon.py [--once] [--state-dir PATH]

Environment variables:
    LSD_POLL_INTERVAL              Poll interval in seconds (default: 3)
    LSD_MAX_PARALLEL_TASKS         Max concurrent tasks (default: 3)
    LSD_MAX_PARALLEL_SUB           Max concurrent sub-agents per task (default: 4)
    LSD_AGENT_TIMEOUT_SEC          Hard timeout per agent subprocess (default: 900)
    LSD_LOG_FILE                   Log file path
                                   (default: ~/.claude/logs/ai-pipeline-daemon.log)
    PIPELINE_STATE_DIR             State directory
                                   (default: <repo>/.state)
    PIPELINE_AGENTS_DIR            Agent prompt directory
                                   (default: <repo>/.claude/agents)
    PIPELINE_CLAUDE_BIN            Claude Code binary (default: claude)

Pipeline roles (13 synchronous + 1 weekly tuner = 14 total):
    planner, planner_decompose, analyst, architect, developer,
    reviewer, review-correctness, review-convention, review-quality,
    tester, qa, security_reviewer, documenter, retrospective
    (plus tuner, run separately via .claude/scripts/weekly-tuner-trigger.sh)

Spec — full state machine, retry semantics, T1-T19 production-validated
tunings, Slack matrix — lives at <repo>/pipeline-workflow.md (repo root).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Optional structlog; fall back to stdlib logging if unavailable
# ---------------------------------------------------------------------------
try:
    import structlog  # type: ignore

    _HAS_STRUCTLOG = True
except ImportError:  # pragma: no cover
    structlog = None  # type: ignore
    _HAS_STRUCTLOG = False


# ---------------------------------------------------------------------------
# Repository root resolution
# ---------------------------------------------------------------------------
# This script lives at <repo>/.claude/scripts/pipeline-daemon.py, so the
# repository root is three levels up from the script file.
SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parent.parent.parent


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
POLL_INTERVAL_SEC = int(os.getenv("LSD_POLL_INTERVAL", "3"))
MAX_PARALLEL_TASKS = int(os.getenv("LSD_MAX_PARALLEL_TASKS", "3"))
MAX_PARALLEL_SUBAGENTS_PER_TASK = int(os.getenv("LSD_MAX_PARALLEL_SUB", "4"))

# Module conflict guard: two tasks in the same module cannot spawn in parallel
# (race condition guard). Set to 1 to bypass (regression testing only).
ALLOW_MODULE_CONFLICT = int(os.getenv("LSD_ALLOW_MODULE_CONFLICT", "0"))

# Cumulative retry context cap: total bytes for the merged
# reviews+tests+security FAIL block injected into retry prompts. Prevents
# context bloat on deep retry chains.
MAX_CUMULATIVE_BLOCK_BYTES = int(os.getenv("LSD_MAX_RETRY_BLOCK_BYTES", "4096"))

# Daily cost caps (USD). 0 = disabled. Soft → Slack info; Hard → daemon pause.
DAILY_USD_SOFT_CAP = float(os.getenv("LSD_DAILY_USD_SOFT_CAP", "0") or "0")
DAILY_USD_HARD_CAP = float(os.getenv("LSD_DAILY_USD_HARD_CAP", "0") or "0")
# Hard timeout (seconds) for each agent subprocess. When the Claude API
# stream stalls or hangs, the subprocess is terminated via SIGTERM -> SIGKILL
# and the retry mechanism kicks in. Acts as the global fallback when no
# per-role override applies.
AGENT_TIMEOUT_SEC = int(os.getenv("LSD_AGENT_TIMEOUT_SEC", "900"))


def _per_role_int_dict(
    defaults: dict[str, int],
    env_prefix: str,
) -> dict[str, int]:
    """Merge per-role defaults with env overrides of the form
    ``{env_prefix}_<ROLE_UPPER>``. Hyphens in role names become underscores
    (e.g. role ``review-correctness`` reads from
    ``{env_prefix}_REVIEW_CORRECTNESS``). Invalid env values are ignored
    with a warning and the default is kept.
    """
    merged = dict(defaults)
    for role in defaults:
        env_key = f"{env_prefix}_{role.upper().replace('-', '_')}"
        raw = os.getenv(env_key)
        if raw is None:
            continue
        try:
            merged[role] = int(raw)
        except (TypeError, ValueError):
            # Not bound to a logger yet at module load; print to stderr.
            sys.stderr.write(
                f"[pipeline-daemon] WARN: ignoring non-integer "
                f"{env_key}={raw!r}; keeping default {defaults[role]}\n"
            )
    return merged


# Per-role hard timeout override (seconds). When a role isn't listed,
# `AGENT_TIMEOUT_SEC` (the global default) is used. The defaults below were
# tuned against multi-provider runs (different vendor APIs, different output
# sizes); developer carries the highest cap because it produces the largest
# diff. Override individual roles with `LSD_AGENT_TIMEOUT_<ROLE>` env vars
# (e.g. `LSD_AGENT_TIMEOUT_DEVELOPER=3600`).
AGENT_TIMEOUT_PER_ROLE: dict[str, int] = _per_role_int_dict({
    "planner": 600,
    "planner_decompose": 600,
    "analyst": 1500,
    "architect": 1500,
    "developer": 2700,
    "reviewer": 1500,
    "review-correctness": 1200,
    "review-convention": 900,
    "review-quality": 900,
    "tester": 1200,
    "qa": 1500,
    "security_reviewer": 1200,
    "documenter": 1200,
    "retrospective": 600,
    "tuner": 600,
}, "LSD_AGENT_TIMEOUT")


def _role_timeout(role: str) -> int:
    """Return the configured hard-timeout for `role`, falling back to the
    global `AGENT_TIMEOUT_SEC` if the role isn't in the per-role table.
    """
    return AGENT_TIMEOUT_PER_ROLE.get(role, AGENT_TIMEOUT_SEC)


# Per-role output-token cap. Caps are mapped onto the
# `CLAUDE_CODE_MAX_OUTPUT_TOKENS` env var read by the `claude` CLI; setting it
# per-spawn lets the daemon trim boilerplate from low-stakes roles (planner,
# retrospective, tuner) and keep producer roles (architect, developer)
# unconstrained. ~20% token saving observed in practice with no quality drop
# on the trimmed roles. Override with `LSD_MAX_OUTPUT_<ROLE>` env vars.
DEFAULT_MAX_OUTPUT_TOKENS = 16000
AGENT_MAX_OUTPUT_PER_ROLE: dict[str, int] = _per_role_int_dict({
    "planner": 14000,
    "planner_decompose": 14000,
    "analyst": 24000,
    "architect": 32000,
    "developer": 56000,
    "reviewer": 16000,
    "review-correctness": 24000,
    "review-convention": 12000,
    "review-quality": 12000,
    "tester": 16000,
    "qa": 20000,
    "security_reviewer": 20000,
    "documenter": 14000,
    "retrospective": 10000,
    "tuner": 10000,
}, "LSD_MAX_OUTPUT")


def _role_max_output(role: str) -> int:
    """Return the configured `CLAUDE_CODE_MAX_OUTPUT_TOKENS` value for `role`,
    falling back to `DEFAULT_MAX_OUTPUT_TOKENS` if unset.
    """
    return AGENT_MAX_OUTPUT_PER_ROLE.get(role, DEFAULT_MAX_OUTPUT_TOKENS)
LOG_FILE = Path(os.path.expanduser(
    os.getenv("LSD_LOG_FILE", "~/.claude/logs/ai-pipeline-daemon.log")
))
STATE_DIR = Path(os.path.expanduser(
    os.getenv("PIPELINE_STATE_DIR", str(REPO_ROOT / ".state"))
))
AGENTS_DIR = Path(os.path.expanduser(
    os.getenv("PIPELINE_AGENTS_DIR", str(REPO_ROOT / ".claude" / "agents"))
))
CLAUDE_BIN = os.getenv("PIPELINE_CLAUDE_BIN", "claude")

# Per-role model selection. agent.md frontmatter `model:` is consumed only by
# the Agent({subagent_type=...}) dispatcher; we invoke `claude -p` directly
# as a subprocess, so without an explicit --model flag the user's session
# default model is used across all roles. Default to a cost-aware split:
# Opus for upstream producers (analyst/architect/developer), Sonnet for the
# rest (orchestration, reviewers, downstream writers).
MODEL_FOR_ROLE: dict[str, str] = {
    "planner": "sonnet",
    "planner_decompose": "sonnet",
    "analyst": "opus",
    "architect": "opus",
    "developer": "opus",
    "reviewer": "sonnet",
    "review-correctness": "sonnet",
    "review-convention": "sonnet",
    "review-quality": "sonnet",
    "tester": "sonnet",
    "qa": "sonnet",
    "security_reviewer": "sonnet",
    "documenter": "sonnet",
    "retrospective": "sonnet",
    "tuner": "sonnet",
}
DEFAULT_AGENT_MODEL: str = "sonnet"

# Idle stream watchdog. If a subprocess produces no stdout/stderr output
# for this many seconds, we send SIGTERM. Real-world cause: Claude API
# stream stuck (`Stream idle timeout - partial response received`) where
# the subprocess hangs without exiting; without this watchdog we'd wait
# the full role_timeout (15-45 minutes) before recovery.
AGENT_IDLE_HUNG_SEC = int(os.getenv("PIPELINE_AGENT_IDLE_HUNG_SEC", "180"))
SLACK_HOOK = REPO_ROOT / ".claude" / "hooks" / "notify-slack.py"

ACTIVE_JSON = STATE_DIR / "active.json"
COMPLETED_JSONL = STATE_DIR / "completed.jsonl"
TASKS_DIR = STATE_DIR / "tasks"
LOCKS_DIR = STATE_DIR / "locks"
TEAM_LOCK = LOCKS_DIR / "team.lock"
TEAM_PAUSED = LOCKS_DIR / "team.paused"

DEFAULT_MAX_RETRIES: dict[str, int] = {
    "planner": 1,
    "planner_decompose": 1,
    "analyst": 2,
    "architect": 2,
    "developer": 3,
    "reviewer": 2,
    "review-correctness": 2,
    "review-convention": 2,
    "review-quality": 2,
    "tester": 2,
    "qa": 2,
    "security_reviewer": 2,
    "documenter": 2,
    # retrospective gets max_retries=2: first-spawn Claude API stream
    # idle/hung is rare but real (`Stream idle timeout - partial response`).
    # max_retries=1 forced manual resurrect for an upstream-API hiccup;
    # bumping it lets the daemon recover automatically on the next attempt.
    "retrospective": 2,
}

# Pipeline ordering (excluding planner): the order in which role_done flags
# are inspected. The parallel review phase is handled separately.
LINEAR_ROLES_BEFORE_REVIEW: list[str] = ["analyst", "architect", "developer"]
REVIEW_PHASE_ROLES: list[str] = ["reviewer", "tester", "security_reviewer"]
LINEAR_ROLES_AFTER_REVIEW: list[str] = ["qa", "documenter", "retrospective"]

# Role -> output file name mapping (informational; surfaced in agent prompt).
ROLE_OUTPUT_FILE: dict[str, str] = {
    "analyst": "analysis.md",
    "architect": "design.md",
    "developer": "progress.md",
    "reviewer": "reviews/correctness.json",
    "tester": "tests.md",
    "security_reviewer": "reviews/security.json",
    "qa": "qa.md",
    "documenter": "docs.md",
    "retrospective": "retro.md",
}

# Status state machine: meta.json.status -> expected next role(s).
STATUS_NEXT_STATUS: dict[str, str] = {
    "queued": "analyzing",
    "analyzed": "designing",
    "designed": "developing",
    "developed": "reviewing",
    "reviewed": "qa-checking",
    "review_failed": "developing",
    "tester_failed": "developing",
    "qa_passed": "documenting",
    "qa_failed": "developing",
    "documented": "retrospecting",
    "design_revision_needed": "designing",
}

TERMINAL_STATUSES = {"done", "failed"}

# Spawn-blocked (non-terminal) statuses: tasks in these states stay in
# active.json but the daemon does NOT dispatch new agents for them. They are
# resumed by an explicit user action (or, for awaiting_api_quota, by the
# auto-resume helper after the reset window elapses).
SPAWN_BLOCKED_STATUSES: set[str] = {
    "awaiting_user_action",
    "awaiting_api_quota",
    "human-review-pending",
}

# Decomposition flow statuses:
#  - decomposition_requested: architect identified an oversized task; planner
#    will be triggered in decompose mode.
#  - decomposed: planner created sub-tasks; the parent stops spawning agents
#    and waits for its children to finish.
STATUSES_AWAITING_CHILDREN = {"decomposed"}

# Architect revision loop (Helper N — design recovery): when developer hits
# its retry cap, the daemon prefers to give the architect a chance to revise
# `design.md` rather than going straight to `awaiting_user_action`. Capped to
# avoid infinite loops.
MAX_ARCHITECT_REVISIONS: int = 2

# On `design_revision_needed` (or any review_failed / tester_failed /
# qa_failed transition), these role retry counters are reset so the cycle
# starts fresh.
_CYCLE_RESET_RETRY_ROLES: tuple[str, ...] = (
    "developer",
    "reviewer",
    "tester",
    "security_reviewer",
    "qa",
)
# On the same cycle reset, these role_done flags are cleared.
_CYCLE_RESET_ROLE_DONE_ROLES: tuple[str, ...] = (
    "developer",
    "reviewer",
    "tester",
    "security_reviewer",
    "qa",
    "documenter",
    "retrospective",
)

# FAIL outcome detection: tester / qa intentionally leave role_done null on
# FAIL so developer retry can fire. The marker is in the role's output .md.
_FAIL_OUTCOME_FILE_MAP: dict[str, str] = {
    "tester": "tests.md",
    "qa": "qa.md",
}
_FAIL_OUTCOME_STATUS_MAP: dict[str, str] = {
    "tester": "tester_failed",
    "qa": "qa_failed",
}

# API-quota / rate-limit / overload patterns. When any match shows up in a
# subprocess's stdout/stderr alongside rc != 0, the daemon does NOT count
# that as a real role failure; instead the task transitions to
# `awaiting_api_quota` and waits for the reset window. Patterns are
# lowercased before comparison.
_QUOTA_ERROR_PATTERNS: tuple[str, ...] = (
    "you've hit your limit",
    "you have hit your limit",
    "rate_limit_exceeded",
    "rate limit exceeded",
    "credit balance is too low",
    "credit_balance_too_low",
    "anthropic_api_error",
    "overloaded_error",
    "overloaded_error: overloaded",
    "max_tokens_exceeded",
    "quota exceeded",
    "usage limit reached",
)

# Vendor-supplied reset-time patterns. When the subprocess output includes a
# concrete reset hint, parsing it lets the auto-resume helper schedule
# precisely (`api_quota_reset_at` in `meta.json`) instead of falling back to
# the 60-min window. Time patterns are interpreted as UTC if no offset is
# given. The grace seconds buffer (`_QUOTA_RESET_AT_GRACE_SEC`) is added on
# top of the parsed time so we don't wake up exactly at the boundary and get
# rejected again.
_QUOTA_RESET_AT_GRACE_SEC = 180

_QUOTA_RESET_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # "resets at 7:50pm" / "reset 7:50 pm"
    ("12hour_hm", re.compile(
        r"reset(?:s|ting)?\s+(?:at\s+)?(\d{1,2}):(\d{2})\s*(am|pm)",
        re.IGNORECASE,
    )),
    # "resets at 19:50" / "reset at 19:50 utc"
    ("24hour_hm", re.compile(
        r"reset(?:s|ting)?\s+(?:at\s+)?(\d{1,2}):(\d{2})(?!\s*(?:am|pm))",
        re.IGNORECASE,
    )),
    # "resets at 7pm"
    ("12hour", re.compile(
        r"reset(?:s|ting)?\s+(?:at\s+)?(\d{1,2})\s*(am|pm)",
        re.IGNORECASE,
    )),
    # "try again in 45 minutes" / "try again in 2 hours"
    ("try_again_in", re.compile(
        r"try\s+again\s+in\s+(\d+)\s*(second|minute|hour)s?",
        re.IGNORECASE,
    )),
    # "wait 30 seconds" / "wait 5 minutes"
    ("wait_n", re.compile(
        r"\bwait\s+(\d+)\s*(second|minute|hour)s?",
        re.IGNORECASE,
    )),
    # HTTP Retry-After header echoed in body: "retry-after: 600" (seconds)
    ("retry_after", re.compile(
        r"retry[\-_ ]after[:\s]+(\d+)",
        re.IGNORECASE,
    )),
)


def _parse_quota_reset_at(haystack: str) -> str | None:
    """Scan subprocess output for a vendor-supplied reset hint and return an
    ISO8601 UTC timestamp (with the grace buffer added), or None.
    """
    now = datetime.now(timezone.utc)

    for name, regex in _QUOTA_RESET_PATTERNS:
        m = regex.search(haystack)
        if not m:
            continue
        try:
            if name == "12hour_hm":
                hh = int(m.group(1)) % 12
                mm = int(m.group(2))
                if m.group(3).lower() == "pm":
                    hh += 12
                target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
            elif name == "24hour_hm":
                hh = int(m.group(1))
                mm = int(m.group(2))
                if not (0 <= hh < 24 and 0 <= mm < 60):
                    continue
                target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
            elif name == "12hour":
                hh = int(m.group(1)) % 12
                if m.group(2).lower() == "pm":
                    hh += 12
                target = now.replace(hour=hh, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
            elif name in ("try_again_in", "wait_n"):
                n = int(m.group(1))
                unit = m.group(2).lower()
                seconds = n if unit == "second" else (
                    n * 60 if unit == "minute" else n * 3600
                )
                target = now + timedelta(seconds=seconds)
            elif name == "retry_after":
                target = now + timedelta(seconds=int(m.group(1)))
            else:
                continue
        except (ValueError, AttributeError):
            continue

        target += timedelta(seconds=_QUOTA_RESET_AT_GRACE_SEC)
        return target.isoformat().replace("+00:00", "Z")

    return None

# Auto-resume window for `awaiting_api_quota` tasks (default 1 hour: vendors
# typically reset hourly). Override with LSD_API_QUOTA_AUTO_RESUME_WINDOW_SEC.
API_QUOTA_AUTO_RESUME_WINDOW_SEC = int(
    os.getenv("LSD_API_QUOTA_AUTO_RESUME_WINDOW_SEC", "3600")
)


# ---------------------------------------------------------------------------
# Logger setup
# ---------------------------------------------------------------------------
def _setup_stdlib_logger() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pipeline_daemon")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        )
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5_000_000, backupCount=3
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    return logger


def setup_logger() -> Any:
    """Return a configured logger. Uses structlog when available, otherwise
    falls back to the stdlib logger."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _HAS_STRUCTLOG:
        std = _setup_stdlib_logger()
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.dev.ConsoleRenderer()
                if sys.stderr.isatty()
                else structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
        return structlog.get_logger("pipeline_daemon")
    return _setup_stdlib_logger()


logger = setup_logger()


def _bind(role: str | None = None, task_id: str | None = None,
          module: str | None = None) -> Any:
    """Bind contextual fields to the logger when structlog is available."""
    if _HAS_STRUCTLOG:
        ctx = {}
        if role:
            ctx["role"] = role
        if task_id:
            ctx["task_id"] = task_id
        if module:
            ctx["module"] = module
        return logger.bind(**ctx)
    return logger


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    """Safely read a JSON file. Returns None when missing or unparsable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("json decode error: path=%s err=%s", path, exc)
        return None


def atomic_write_json(path: Path, payload: Any) -> None:
    """Atomic JSON write (tmp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON record to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# STATUS.md fail-safe append (when documenter is skipped)
# ---------------------------------------------------------------------------
def status_md_failsafe_append(task_id: str, module: str,
                              status: str, summary: str = "") -> bool:
    """Append a single changelog line to STATUS.md for failed/aborted tasks.

    The documenter agent only runs on the `done` happy-path. Failed tasks
    used to disappear silently. This helper performs a minimal markdown
    patch — no agent call, no structural change. Returns True if appended.
    """
    status_path = STATE_DIR.parent / "STATUS.md"
    if not status_path.exists():
        return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    line = (
        f"- [{task_id}] {module or 'unknown'} · {status} — "
        f"{summary or '(daemon fail-safe entry — documenter did not run)'}\n"
    )
    try:
        text = status_path.read_text(encoding="utf-8")
    except OSError:
        return False
    today_marker = f"### {today}"
    if today_marker in text:
        new_text = text.replace(
            today_marker, f"{today_marker}\n{line.rstrip()}\n", 1
        )
    else:
        bugun_marker = "## Today"
        if bugun_marker not in text:
            return False
        injection = f"\n### {today}\n{line.rstrip()}\n"
        new_text = text.replace(bugun_marker,
                                bugun_marker + injection, 1)
    try:
        status_path.write_text(new_text, encoding="utf-8")
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Daily budget tracking (cost cap)
# ---------------------------------------------------------------------------
def _budget_file() -> Path:
    return LOCKS_DIR / "daily-budget.json"


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_daily_budget() -> dict[str, Any]:
    """Read today's USD total. Reset on date change."""
    today = _today_iso()
    data = read_json(_budget_file()) or {}
    if data.get("date") != today:
        return {"date": today, "spent_usd": 0.0, "soft_warned": False,
                "hard_paused": False}
    return {
        "date": today,
        "spent_usd": float(data.get("spent_usd") or 0.0),
        "soft_warned": bool(data.get("soft_warned")),
        "hard_paused": bool(data.get("hard_paused")),
    }


def save_daily_budget(data: dict[str, Any]) -> None:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_budget_file(), data)


def record_cost(usd: float) -> dict[str, Any]:
    """Add cost to the daily bucket; return updated budget state."""
    if usd <= 0:
        return load_daily_budget()
    data = load_daily_budget()
    data["spent_usd"] = round(float(data["spent_usd"]) + float(usd), 6)
    save_daily_budget(data)
    return data


# ---------------------------------------------------------------------------
# Lock management
# ---------------------------------------------------------------------------
def acquire_lock() -> None:
    """Write team.lock; raise RuntimeError if another daemon instance holds it."""
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    if TEAM_LOCK.exists():
        try:
            data = json.loads(TEAM_LOCK.read_text())
            existing_pid = int(data.get("pid", 0))
            if existing_pid > 0:
                try:
                    os.kill(existing_pid, 0)
                    raise RuntimeError(
                        f"daemon already running (pid {existing_pid})"
                    )
                except ProcessLookupError:
                    logger.warning(
                        "stale lock detected (pid %s missing), removing",
                        existing_pid,
                    )
                    TEAM_LOCK.unlink(missing_ok=True)
        except (json.JSONDecodeError, ValueError):
            logger.warning("malformed lock, removing")
            TEAM_LOCK.unlink(missing_ok=True)
    TEAM_LOCK.write_text(json.dumps({
        "pid": os.getpid(),
        "started_at": _utcnow_iso(),
    }, indent=2))


def release_lock() -> None:
    if TEAM_LOCK.exists():
        try:
            TEAM_LOCK.unlink()
        except OSError as exc:  # pragma: no cover
            logger.warning("lock release failed: %s", exc)


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------
def slack_notify(notification_type: str, **kwargs: str) -> None:
    """Fire-and-forget Slack notification. Errors are non-fatal."""
    if not SLACK_HOOK.exists():
        logger.debug("slack hook missing, skip: %s", SLACK_HOOK)
        return
    cmd = [sys.executable, str(SLACK_HOOK), "--type", notification_type]
    for k, v in kwargs.items():
        if v is None or v == "":
            continue
        cmd.extend([f"--{k}", str(v)])
    try:
        subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:  # pragma: no cover
        logger.warning("slack notify spawn failed: %s", exc)


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------
@dataclass
class Task:
    """Runtime representation of a single task entry from active.json."""

    task_id: str
    raw: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return str(self.meta.get("status") or self.raw.get("status") or "queued")

    @property
    def module(self) -> str:
        return str(self.meta.get("module") or self.raw.get("module") or "")

    @property
    def role_done(self) -> dict[str, bool]:
        rd = self.meta.get("role_done") or {}
        return {k: bool(v) for k, v in rd.items()}

    @property
    def retry_count(self) -> dict[str, int]:
        rc = self.meta.get("retry_count") or {}
        return {k: int(v) for k, v in rc.items()}

    @property
    def max_retries(self) -> dict[str, int]:
        mr = dict(DEFAULT_MAX_RETRIES)
        mr.update(self.meta.get("max_retries") or {})
        return mr

    @property
    def token_used(self) -> int:
        return int(self.meta.get("token_used") or 0)

    @property
    def token_budget(self) -> dict[str, int]:
        return dict(self.meta.get("token_budget") or {})

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def task_dir(self) -> Path:
        return TASKS_DIR / self.task_id

    def meta_path(self) -> Path:
        return self.task_dir() / "meta.json"

    def reload_meta(self) -> None:
        """Reload meta.json from disk."""
        data = read_json(self.meta_path())
        if isinstance(data, dict):
            self.meta = data

    def save_meta(self) -> None:
        atomic_write_json(self.meta_path(), self.meta)

    def has_blocking_deps(self, all_tasks: dict[str, Task]) -> bool:
        """True when any id listed in meta.blocked_by is not yet done."""
        deps = self.meta.get("blocked_by") or self.raw.get("blocked_by") or []
        if not isinstance(deps, list):
            return False
        for dep_id in deps:
            dep = all_tasks.get(str(dep_id))
            if dep is None:
                continue  # not in active list -> probably already completed
            if dep.status not in TERMINAL_STATUSES or dep.status == "failed":
                if dep.status != "done":
                    return True
        return False

    def is_decomposed_and_children_done(
        self, all_tasks: dict[str, Task]
    ) -> bool:
        """True when status is `decomposed` and every child task in
        `meta.blocks` has status `done`. Used by the daemon main loop to
        promote the parent task forward.
        """
        if self.status != "decomposed":
            return False
        children = self.meta.get("blocks") or []
        if not isinstance(children, list) or not children:
            return False
        for child_id in children:
            child = all_tasks.get(str(child_id))
            if child is None:
                # Not in active list — it may have moved to completed.jsonl,
                # but since we cannot confirm its terminal status, stay safe
                # and keep waiting.
                return False
            if child.status != "done":
                return False
        return True

    def next_actions(self) -> list[str]:
        """Compute the next role(s) to spawn based on role_done flags.

        - planner is invoked outside the daemon (e.g. via team.sh start);
          when it is missing, an empty list is returned.
        - linear roles: analyst -> architect -> developer
        - parallel roles: reviewer + tester + security_reviewer
          (skipped while status is review_failed)
        - then: qa -> documenter -> retrospective
        """
        if self.is_terminal:
            return []

        # Spawn-blocked statuses: daemon must NOT dispatch new agents.
        # Auto-resume (for awaiting_api_quota) is handled separately in the
        # main loop; awaiting_user_action and human-review-pending wait for
        # the user.
        if self.status in SPAWN_BLOCKED_STATUSES:
            return []

        rd = self.role_done

        # Decomposition flow: when architect sets status to
        # "decomposition_requested", trigger planner in decompose mode.
        if self.status == "decomposition_requested":
            # Idempotency: skip if planner_decompose already ran.
            if rd.get("planner_decompose"):
                return []
            return ["planner_decompose"]

        # Decomposition flow: status `decomposed` means do not spawn anything;
        # the daemon main loop handles child completion separately.
        if self.status in STATUSES_AWAITING_CHILDREN:
            return []

        # planner check (informational; bail out early if missing)
        if not rd.get("planner", True):
            # planner is required but has not run yet — the daemon does not
            # auto-trigger it.
            return []

        # design_revision_needed -> architect retry. Triggered when developer
        # exhausted its retry cap and architect_revision_count is below
        # MAX_ARCHITECT_REVISIONS; the daemon resets the downstream cycle and
        # gives architect a chance to revise design.md.
        if self.status == "design_revision_needed":
            return ["architect"]

        # review_failed / tester_failed / qa_failed -> developer retry.
        # tester_failed is the explicit FAIL outcome detected via
        # `_detect_failed_outcome_role()` from `tests.md` "Result: FAIL".
        if self.status in ("review_failed", "tester_failed", "qa_failed"):
            return ["developer"]

        for role in LINEAR_ROLES_BEFORE_REVIEW:
            if not rd.get(role):
                return [role]

        # parallel review phase
        pending_review: list[str] = []
        for role in REVIEW_PHASE_ROLES:
            if not rd.get(role):
                pending_review.append(role)
        if pending_review:
            return pending_review

        for role in LINEAR_ROLES_AFTER_REVIEW:
            if not rd.get(role):
                return [role]

        return []

    def retries_exceeded(self, role: str) -> bool:
        """True when retry attempts for `role` reached its configured limit."""
        limit = self.max_retries.get(role)
        if limit is None:
            return False
        used = self.retry_count.get(role, 0)
        return used >= limit


# ---------------------------------------------------------------------------
# active.json reader
# ---------------------------------------------------------------------------
def read_active_tasks() -> list[Task]:
    """Read active.json and build a Task list, hydrating each meta.json."""
    data = read_json(ACTIVE_JSON)
    if not isinstance(data, dict):
        return []
    raw_tasks = data.get("tasks") or []
    tasks: list[Task] = []
    for entry in raw_tasks:
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("task_id") or entry.get("id") or "").strip()
        if not tid:
            continue
        t = Task(task_id=tid, raw=entry)
        t.reload_meta()
        tasks.append(t)
    return tasks


# ---------------------------------------------------------------------------
# Subprocess tracking
# ---------------------------------------------------------------------------
@dataclass
class RunningProc:
    """Bookkeeping for a running agent subprocess."""

    task_id: str
    role: str
    proc: asyncio.subprocess.Process
    started_at: float
    log_path: Path
    stdout_buf: list[bytes] = field(default_factory=list)
    stderr_buf: list[bytes] = field(default_factory=list)
    # Idle stream watchdog support. last_activity is bumped each time a
    # drain task reads a chunk from the subprocess pipe; the reap loop
    # checks `time.time() - last_activity > AGENT_IDLE_HUNG_SEC` and
    # sends SIGTERM if exceeded.
    last_activity: float = 0.0
    drain_tasks: list[asyncio.Task] = field(default_factory=list)
    idle_killed: bool = False


# ---------------------------------------------------------------------------
# Agent prompt construction & spawn
# ---------------------------------------------------------------------------
def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _agent_md_path(role: str) -> Path:
    """Resolve the agent .md file under AGENTS_DIR for a given role.

    The special `planner_decompose` role reuses planner.md; mode is selected
    via prompt parameters.
    """
    fname_map = {
        "security_reviewer": "security-reviewer.md",
        "planner_decompose": "planner.md",
    }
    fname = fname_map.get(role, f"{role}.md")
    return AGENTS_DIR / fname


def _stable_prefix_for_role(role: str) -> str:
    """Cache-friendly stable prefix per role.

    Anthropic prompt cache uses prefix matching — the first N tokens must be
    byte-exact identical between calls for a cache hit. Each role's agent.md
    is stable across spawns, so we put it at the start of the prompt and
    keep all task-variable content (task_id, status, retry block, meta.json)
    in a suffix.

    Cache hit expectations:
    - Same role across different tasks (developer task A → task B share
      the agent_md prefix → 90% cheaper input tokens).
    - Same role within a task on retries (most common case).
    """
    agent_md = _read_text_safe(_agent_md_path(role))
    # NOTE: this block must be byte-exact identical between spawns of the
    # same role. Never include task_id, status, or retry context here.
    return (
        f"# AI Pipeline Local Team — Role Prefix (cacheable)\n"
        f"Role: {role}\n\n"
        f"## agent.md (stable per role)\n"
        f"{agent_md}\n\n"
        f"---\n"
        f"# Task-specific context (varies per spawn)\n\n"
    )


def build_agent_prompt(role: str, task: Task) -> str:
    """Build the `-p` prompt text passed to the Claude Code subprocess.

    Structure:
      [STABLE PREFIX]   role + agent_md  (cache-hit target)
      [VARIABLE SUFFIX] task_id + module + status + meta.json + assignment

    The stable prefix comes first so Anthropic's prompt cache (prefix
    matching) can deliver up to 90% input-token discount across spawns of
    the same role.

    For `planner_decompose`, mode parameters (MODE=decompose,
    PARENT_TASK_ID=<id>) are appended so that planner.md can branch into its
    decompose section.
    """
    meta_text = json.dumps(task.meta, indent=2, ensure_ascii=False)
    output_file = ROLE_OUTPUT_FILE.get(role, f"{role}.md")
    read_allowlist = task.meta.get("read_allowlist") or []
    allowlist_text = (
        ", ".join(str(p) for p in read_allowlist)
        if read_allowlist else "(unrestricted)"
    )

    stable_prefix = _stable_prefix_for_role(role)

    if role == "planner_decompose":
        variable_suffix = (
            f"Task ID (parent): {task.task_id}\n"
            f"Module : {task.module}\n"
            f"Status : {task.status}\n\n"
            f"## Mode parameters\n"
            f"MODE=decompose\n"
            f"PARENT_TASK_ID={task.task_id}\n\n"
            f"## meta.json (ephemeral)\n"
            f"```json\n{meta_text}\n```\n\n"
            f"## Read allowlist (informational)\n"
            f"{allowlist_text}\n\n"
            f"## Task\n"
            f"Apply the 'Decompose Mode' section of planner.md. Parse the\n"
            f"'## Sub-task Decomposition' section in the parent task's\n"
            f"design.md, create the sub-tasks, and update the parent\n"
            f"meta.json. When finished, set role_done.planner_decompose = ts,\n"
            f"status = 'decomposed', and blocks = [<new sub-task ids>].\n"
        )
        return stable_prefix + variable_suffix

    variable_suffix = (
        f"Task ID: {task.task_id}\n"
        f"Module : {task.module}\n"
        f"Status : {task.status}\n\n"
        f"## meta.json (ephemeral)\n"
        f"```json\n{meta_text}\n```\n\n"
        f"## Read allowlist (informational)\n"
        f"{allowlist_text}\n\n"
        f"## Task\n"
        f"Apply the '{role}' role for the task described in meta.json.\n"
        f"Output file: .state/tasks/{task.task_id}/{output_file}\n"
        f"When finished, set role_done.{role} = true in meta.json and\n"
        f"transition to the appropriate status.\n"
    )
    return stable_prefix + variable_suffix


async def spawn_agent(role: str, task: Task) -> RunningProc | None:
    """Spawn an agent as a Claude Code subprocess."""
    log = _bind(role=role, task_id=task.task_id, module=task.module)

    # Retry guard (developer/tester/qa, etc.)
    if role in DEFAULT_MAX_RETRIES and task.retries_exceeded(role):
        # Architect revision recovery: when developer hits its cap, give the
        # architect a chance to revise design.md before failing the task.
        # Cycle counters + downstream role_done flags reset for fresh start.
        if role == "developer":
            arch_rev = int(task.meta.get("architect_revision_count") or 0)
            if arch_rev < MAX_ARCHITECT_REVISIONS:
                rc = dict(task.meta.get("retry_count") or {})
                for r in _CYCLE_RESET_RETRY_ROLES:
                    rc[r] = 0
                task.meta["retry_count"] = rc
                rd = dict(task.meta.get("role_done") or {})
                for r in _CYCLE_RESET_ROLE_DONE_ROLES:
                    rd[r] = None
                rd["architect"] = None  # architect re-runs to revise design
                task.meta["role_done"] = rd
                task.meta["architect_revision_count"] = arch_rev + 1
                task.meta["status"] = "design_revision_needed"
                task.meta["owner_agent"] = "architect"
                task.meta.setdefault("failure_reasons", []).append(
                    f"developer max retry exceeded -> design_revision_needed "
                    f"(architect revision {arch_rev + 1}/"
                    f"{MAX_ARCHITECT_REVISIONS}); cycle counters + "
                    "role_done reset for fresh dev cycle"
                )
                task.save_meta()
                slack_notify(
                    "info",
                    issue=task.task_id,
                    agent=role,
                    module=task.module,
                    summary=(
                        f"developer retry exhausted — architect revision "
                        f"{arch_rev + 1}/{MAX_ARCHITECT_REVISIONS} triggered; "
                        "fresh dev cycle queued"
                    ),
                )
                log.warning(
                    "design recovery: developer retry exhausted -> "
                    "design_revision_needed (revision %d/%d, cycle reset)",
                    arch_rev + 1, MAX_ARCHITECT_REVISIONS,
                )
                return None
            # Architect revisions also exhausted — fall through to
            # awaiting_user_action.

        log.warning(
            "retry limit exceeded — task awaiting user action: role=%s", role,
        )
        # Spawn-blocked: NOT a terminal state. The task stays in active.json
        # and the daemon does not dispatch new agents until the user
        # resurrects, reduces scope, or cancels.
        task.meta["status"] = "awaiting_user_action"
        task.meta.setdefault("failure_reasons", []).append(
            f"{role} retry limit exceeded"
        )
        task.save_meta()
        slack_notify(
            "retry_limit",
            issue=task.task_id,
            agent=role,
            module=task.module,
            summary=(
                f"{role} retry limit exceeded — task awaiting_user_action "
                "(resurrect / scope-reduce / skip required)"
            ),
        )
        return None

    prompt = build_agent_prompt(role, task)
    task_dir = task.task_dir()
    task_dir.mkdir(parents=True, exist_ok=True)
    log_dir = task_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{role}-{int(time.time())}.log"

    cmd = [
        CLAUDE_BIN,
        "-p",
        prompt,
        "--no-session-persistence",
        # JSON output enables real API usage tracking (input/output/cache
        # tokens + total_cost_usd + per-model breakdown). The byte/4
        # heuristic in `_approx_tokens` is the fallback only.
        "--output-format",
        "json",
        # Per-role model — without --model the user's session default is
        # used (often Opus on Pro plans), defeating the cost-aware role
        # split. See MODEL_FOR_ROLE for the breakdown.
        "--model",
        MODEL_FOR_ROLE.get(role, DEFAULT_AGENT_MODEL),
    ]

    # Per-role output-token cap: trims boilerplate on low-stakes roles
    # without touching producer roles. The `claude` CLI reads this env var.
    spawn_env = os.environ.copy()
    spawn_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(_role_max_output(role))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(STATE_DIR.parent),
            env=spawn_env,
        )
    except FileNotFoundError as exc:
        log.error("claude binary missing: %s", exc)
        slack_notify(
            "error",
            issue=task.task_id,
            agent=role,
            module=task.module,
            summary=f"claude binary not found: {exc}",
        )
        return None
    except OSError as exc:
        log.error("subprocess spawn failed: %s", exc)
        return None

    log.info("agent spawned: role=%s pid=%s", role, proc.pid)

    # Increment retry counter (only for retry-aware roles).
    if role in DEFAULT_MAX_RETRIES:
        rc = dict(task.retry_count)
        rc[role] = rc.get(role, 0) + 1
        task.meta["retry_count"] = rc
        task.save_meta()

    rp = RunningProc(
        task_id=task.task_id,
        role=role,
        proc=proc,
        started_at=time.time(),
        log_path=log_path,
        last_activity=time.time(),
    )
    # Idle stream watchdog: drain stdout/stderr in background so reap loop
    # can detect a hung subprocess (no output for AGENT_IDLE_HUNG_SEC) and
    # SIGTERM early instead of waiting for the full role timeout.
    rp.drain_tasks.append(
        asyncio.create_task(_drain_stream(proc.stdout, rp.stdout_buf, rp))
    )
    rp.drain_tasks.append(
        asyncio.create_task(_drain_stream(proc.stderr, rp.stderr_buf, rp))
    )
    return rp


async def _drain_stream(
    stream: "asyncio.StreamReader | None",
    buf: list[bytes],
    rp: RunningProc,
) -> None:
    """Continuously read from a subprocess pipe, append to buf, and bump
    rp.last_activity so the idle watchdog can detect stalls."""
    if stream is None:
        return
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            buf.append(chunk)
            rp.last_activity = time.time()
    except (asyncio.CancelledError, OSError):
        return


# ---------------------------------------------------------------------------
# Subprocess reaping & token tracking
# ---------------------------------------------------------------------------
def _approx_tokens(byte_count: int) -> int:
    """Rough token approximation: 4 bytes per token.

    Fallback only — prefer `_parse_usage_from_json_output()` because the byte
    heuristic undercounts cache hits and tool-call payloads by 100x to 1000x
    in practice. The heuristic exists for older daemon versions or for runs
    where the JSON parse fails.
    """
    return max(0, byte_count // 4)


def _parse_usage_from_json_output(
    stdout: bytes,
) -> tuple[int, float, dict, dict] | None:
    """Extract real API usage from `claude --output-format json` stdout.

    Returns (total_tokens, cost_usd, breakdown, model_usage) or None if the
    parse fails (caller falls back to `_approx_tokens`).

    breakdown layout:
      {"input": int, "output": int, "cache_read": int, "cache_creation": int}
    model_usage layout:
      {"<model_id>": {"inputTokens": int, "outputTokens": int,
                      "cacheReadInputTokens": int,
                      "cacheCreationInputTokens": int,
                      "webSearchRequests": int, "costUSD": float}}
    """
    try:
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text or not text.startswith("{"):
            return None
        obj = json.loads(text)
        if not isinstance(obj, dict):
            return None
        usage = obj.get("usage") or {}
        if not isinstance(usage, dict):
            return None
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        cr_tok = int(usage.get("cache_read_input_tokens") or 0)
        cc_tok = int(usage.get("cache_creation_input_tokens") or 0)
        total = in_tok + out_tok + cr_tok + cc_tok
        cost = float(obj.get("total_cost_usd") or 0.0)
        breakdown = {
            "input": in_tok,
            "output": out_tok,
            "cache_read": cr_tok,
            "cache_creation": cc_tok,
        }
        model_usage = obj.get("modelUsage") or {}
        if not isinstance(model_usage, dict):
            model_usage = {}
        return total, cost, breakdown, model_usage
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return None


def _detect_quota_error(stdout: bytes, stderr: bytes) -> str | None:
    """Scan subprocess output for API quota / rate-limit / overload patterns.

    Returns the matched pattern name, or None.

    Used by `_post_run_update` when `rc != 0`. When a match is found, the
    daemon does NOT increment retry counters (the subprocess never had a
    chance to do its real work), sets `meta.status = "awaiting_api_quota"`,
    and emits an info-severity Slack alert.
    """
    try:
        stdout_str = stdout.decode("utf-8", errors="replace").lower()
        stderr_str = stderr.decode("utf-8", errors="replace").lower()
    except (AttributeError, UnicodeDecodeError):
        return None
    haystack = stdout_str + "\n" + stderr_str
    for pattern in _QUOTA_ERROR_PATTERNS:
        if pattern in haystack:
            return pattern
    return None


def _detect_failed_outcome_role(role: str, task_dir: Path) -> str | None:
    """For tester/qa: detect explicit FAIL verdict written in the role's .md.

    Tester and QA agents intentionally leave `role_done.<role>` null when
    they decide the verdict is FAIL — that way the developer retry pipeline
    fires. The subprocess still exits rc=0, so without this helper the
    daemon would interpret rc=0 + role_done null as a "cleanup skipped"
    failure and bump the retry counter for the wrong role.

    Detection: the first 100 lines of the role's output `.md` are scanned
    case-insensitively for the literal `Result:` ... `FAIL`. When found,
    returns the target status (`tester_failed` / `qa_failed`); otherwise
    returns None and the caller keeps the original "cleanup skipped"
    behaviour for non-tester/non-qa roles.
    """
    fname = _FAIL_OUTCOME_FILE_MAP.get(role)
    if not fname:
        return None
    md_path = task_dir / fname
    if not md_path.exists():
        return None
    try:
        with md_path.open("r", encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(100), f):
                lower = line.lower()
                if "result:" in lower and "fail" in lower:
                    return _FAIL_OUTCOME_STATUS_MAP[role]
    except OSError:
        return None
    return None


def _infer_pre_quota_status(role_done: dict[str, Any]) -> str:
    """Reverse-infer the pre-`awaiting_api_quota` status from role_done flags.

    Used by `auto_resume_quota_blocked_tasks()` so the daemon can return a
    quota-blocked task to the right place in the pipeline once the reset
    window elapses.

      planner ✓ analyst ✓ architect ✓ developer ✓     -> "developed"
      planner ✓ analyst ✓ architect ✓                 -> "designed"
      planner ✓ analyst ✓                             -> "analyzed"
      planner ✓                                       -> "queued"
      review trio (reviewer + tester + security) ✓    -> "reviewed"
      qa ✓                                            -> "qa_passed"
      documenter ✓                                    -> "documented"
    """
    rd = role_done or {}
    if rd.get("documenter"):
        return "documented"
    if rd.get("qa"):
        return "qa_passed"
    review_trio_done = (
        rd.get("reviewer") and rd.get("tester") and rd.get("security_reviewer")
    )
    if review_trio_done:
        return "reviewed"
    if rd.get("developer"):
        return "developed"
    if rd.get("architect"):
        return "designed"
    if rd.get("analyst"):
        return "analyzed"
    return "queued"


def auto_resume_quota_blocked_tasks() -> int:
    """Return `awaiting_api_quota` tasks to dispatch once the reset window passes.

    Two paths:
      1. If `meta.api_quota_reset_at` is set (vendor-provided explicit reset
         time), wait until that timestamp + 3 minutes grace.
      2. Otherwise fall back to `meta.api_quota_blocked_at` +
         API_QUOTA_AUTO_RESUME_WINDOW_SEC (default 60 min — typical hourly
         reset).

    Called from the main loop once per tick.
    """
    resumed = 0
    now_dt = datetime.now(timezone.utc)
    grace_sec = 180  # 3-minute grace after explicit reset time
    for task in read_active_tasks():
        if task.status != "awaiting_api_quota":
            continue
        meta_path = task.task_dir() / "meta.json"
        meta = read_json(meta_path) or {}

        # Path 1: explicit reset time
        reset_at_str = meta.get("api_quota_reset_at")
        if reset_at_str:
            try:
                reset_at = datetime.fromisoformat(
                    str(reset_at_str).replace("Z", "+00:00")
                )
            except ValueError:
                reset_at = None
            if reset_at is not None:
                wait_until = reset_at + timedelta(seconds=grace_sec)
                if now_dt < wait_until:
                    continue
                new_status = _infer_pre_quota_status(meta.get("role_done") or {})
                meta["status"] = new_status
                meta["owner_agent"] = None
                meta["updated_at"] = _utcnow_iso()
                meta.setdefault("failure_reasons", []).append(
                    f"auto-resume: api_quota_reset_at={reset_at_str} + "
                    f"{grace_sec}s grace elapsed -> status reset to {new_status}"
                )
                atomic_write_json(meta_path, meta)
                resumed += 1
                logger.info(
                    "quota auto-resume (reset-time): task=%s -> %s",
                    task.task_id, new_status,
                )
                continue

        # Path 2: window-based fallback
        blocked_at_str = meta.get("api_quota_blocked_at")
        if not blocked_at_str:
            continue
        try:
            blocked_at = datetime.fromisoformat(
                str(blocked_at_str).replace("Z", "+00:00")
            )
        except ValueError:
            continue
        elapsed = (now_dt - blocked_at).total_seconds()
        if elapsed < API_QUOTA_AUTO_RESUME_WINDOW_SEC:
            continue
        new_status = _infer_pre_quota_status(meta.get("role_done") or {})
        meta["status"] = new_status
        meta["owner_agent"] = None
        meta["updated_at"] = _utcnow_iso()
        meta.setdefault("failure_reasons", []).append(
            f"auto-resume: api_quota_blocked_at={blocked_at_str} + "
            f"{API_QUOTA_AUTO_RESUME_WINDOW_SEC}s elapsed -> "
            f"status reset to {new_status}"
        )
        atomic_write_json(meta_path, meta)
        resumed += 1
        logger.info(
            "quota auto-resume (window): task=%s elapsed=%.0fs -> %s",
            task.task_id, elapsed, new_status,
        )
    return resumed


async def _terminate_runaway_proc(rp: RunningProc) -> tuple[bytes, bytes]:
    """Terminate a subprocess that exceeded the hard timeout.

    Sends SIGTERM, waits 5 seconds, then SIGKILL if still alive.

    Returns: (stdout, stderr) — partial output drained from the proc streams.
    The caller reads `rp.proc.returncode` directly.
    """
    log = _bind(role=rp.role, task_id=rp.task_id)
    elapsed = time.time() - rp.started_at
    log.warning(
        "agent timeout: role=%s elapsed=%.1fs -> sigterm",
        rp.role, elapsed,
    )

    # SIGTERM
    try:
        rp.proc.terminate()
    except ProcessLookupError:
        pass

    # 5-second grace; drain tasks keep reading the pipes, then we collect
    # whatever they accumulated. (Replaces communicate() to stay compatible
    # with the idle stream watchdog pattern.)
    try:
        await asyncio.wait_for(rp.proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        log.warning(
            "agent timeout: role=%s SIGTERM insufficient -> sigkill",
            rp.role,
        )
        try:
            rp.proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(rp.proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
    try:
        await asyncio.wait_for(
            asyncio.gather(*rp.drain_tasks, return_exceptions=True),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        for t in rp.drain_tasks:
            if not t.done():
                t.cancel()
    stdout = b"".join(rp.stdout_buf)
    stderr = b"".join(rp.stderr_buf)
    return stdout, stderr


async def reap_completed_subprocesses(running: list[RunningProc]) -> list[RunningProc]:
    """Reap finished subprocesses and return the still-running list.

    Hard-timeout: subprocesses exceeding AGENT_TIMEOUT_SEC are terminated via
    SIGTERM/SIGKILL. Timeout-induced exits append to `failure_reasons`; retry
    counters are still tracked from `spawn_agent`, so the retry mechanism
    re-fires on the next main-loop iteration.
    """
    still_running: list[RunningProc] = []
    timed_out: set[int] = set()
    for rp in running:
        if rp.proc.returncode is None:
            # Hard-timeout check
            elapsed = time.time() - rp.started_at
            # Idle stream watchdog: if no output for AGENT_IDLE_HUNG_SEC,
            # SIGTERM the subprocess (typically a Claude API stream stall).
            idle_for = time.time() - rp.last_activity
            if (AGENT_IDLE_HUNG_SEC > 0
                    and idle_for >= AGENT_IDLE_HUNG_SEC
                    and not rp.idle_killed):
                rp.idle_killed = True
                log = _bind(role=rp.role, task_id=rp.task_id)
                log.warning(
                    "idle stream watchdog: role=%s pid=%d idle=%.1fs"
                    " (>=%ds) — SIGTERM",
                    rp.role, rp.proc.pid, idle_for, AGENT_IDLE_HUNG_SEC,
                )
                try:
                    rp.proc.terminate()
                except (ProcessLookupError, OSError):
                    pass
                still_running.append(rp)
                continue
            role_timeout = _role_timeout(rp.role)
            if role_timeout > 0 and elapsed >= role_timeout:
                stdout, stderr = await _terminate_runaway_proc(rp)
                # Persist partial output to the log file.
                try:
                    rp.log_path.write_bytes(
                        b"=== TIMEOUT ===\nrole timeout exceeded: "
                        + f"{rp.role}={role_timeout}s\n=== STDOUT (partial) ===\n".encode()
                        + stdout
                        + b"\n=== STDERR (partial) ===\n"
                        + stderr
                    )
                except OSError:
                    pass

                rc = rp.proc.returncode if rp.proc.returncode is not None else -1
                log = _bind(role=rp.role, task_id=rp.task_id)
                log.warning(
                    "agent timeout finalized: role=%s rc=%s elapsed=%.1fs"
                    " (cap=%ds)",
                    rp.role, rc, elapsed, role_timeout,
                )

                # Real usage parse (JSON output) — fall back to byte heuristic
                real = _parse_usage_from_json_output(stdout)
                if real is not None:
                    total_tokens, cost_usd, breakdown, model_usage = real
                else:
                    total_tokens = _approx_tokens(len(stdout) + len(stderr))
                    cost_usd = 0.0
                    breakdown = {}
                    model_usage = {}
                # _post_run_update does not flip role_done; retry_count was
                # already incremented at spawn time. Append a timeout note to
                # failure_reasons.
                tagged_stderr = (
                    f"timeout after {role_timeout}s ({rp.role}); ".encode() + stderr
                )
                await _post_run_update(
                    rp, total_tokens, rc, tagged_stderr,
                    stdout=stdout, cost_usd=cost_usd,
                    breakdown=breakdown, model_usage=model_usage,
                )
                timed_out.add(id(rp))
            else:
                still_running.append(rp)
            continue

        # Process finished: wait for drain tasks to flush, then collect
        # the buffers (replaces communicate() so the idle watchdog and
        # chunk-based drain pattern stay consistent).
        try:
            await asyncio.wait_for(
                asyncio.gather(*rp.drain_tasks, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            for t in rp.drain_tasks:
                if not t.done():
                    t.cancel()
        stdout = b"".join(rp.stdout_buf)
        stderr = b"".join(rp.stderr_buf)

        try:
            rp.log_path.write_bytes(
                b"=== STDOUT ===\n" + stdout + b"\n=== STDERR ===\n" + stderr
            )
        except OSError:
            pass

        elapsed = time.time() - rp.started_at
        rc = rp.proc.returncode
        log = _bind(role=rp.role, task_id=rp.task_id)
        log.info(
            "agent finished: role=%s rc=%s elapsed=%.1fs",
            rp.role, rc, elapsed,
        )

        # Real usage parse (JSON output) — falls back to byte heuristic
        real = _parse_usage_from_json_output(stdout)
        if real is not None:
            total_tokens, cost_usd, breakdown, model_usage = real
        else:
            total_tokens = _approx_tokens(len(stdout) + len(stderr))
            cost_usd = 0.0
            breakdown = {}
            model_usage = {}
        await _post_run_update(
            rp, total_tokens, rc, stderr,
            stdout=stdout, cost_usd=cost_usd,
            breakdown=breakdown, model_usage=model_usage,
        )

    return still_running


async def _post_run_update(rp: RunningProc, approx_tokens: int,
                           returncode: int, stderr: bytes,
                           stdout: bytes = b"",
                           cost_usd: float = 0.0,
                           breakdown: dict | None = None,
                           model_usage: dict | None = None) -> None:
    """Refresh meta.json after a subprocess finishes; check budget and errors.

    API quota detection (Helper N): if `rc != 0` and stdout/stderr matches a
    known vendor quota / rate-limit / overload pattern, the daemon does NOT
    increment retry counters. Instead the task transitions to
    `awaiting_api_quota` (a SPAWN_BLOCKED state); the auto-resume helper
    will return it to dispatch once the reset window elapses.

    FAIL outcome detection (Helper N): tester / qa rc=0 + role_done null
    combined with `Result: FAIL` in the role's output `.md` is treated as an
    explicit FAIL verdict (not a "cleanup skipped" failure). Status is set
    to `tester_failed` / `qa_failed`; the developer retry pipeline fires
    naturally on the next tick.

    Cycle reset (Helper N): on `review_failed` / `tester_failed` /
    `qa_failed` transitions, role_done flags for the trio (reviewer, tester,
    security_reviewer) plus qa are cleared so the next cycle re-runs them
    against the freshly-retried developer code (rather than letting a stale
    review verdict short-circuit the next cycle).
    """
    task_dir = TASKS_DIR / rp.task_id
    meta_path = task_dir / "meta.json"
    meta = read_json(meta_path) or {}
    meta["token_used"] = int(meta.get("token_used") or 0) + approx_tokens
    # Cache-aware billable counter. Cache-read tokens are heavily discounted
    # (~10% of full price), so counting them against a hard budget produces
    # false-positive failures: a task can hit the cap purely on cache reads
    # while real spend is trivial. token_billable_used = input + output +
    # cache_creation; budget enforcement uses this. token_used stays as the
    # raw observability total (incl. cache_read) for telemetry.
    if breakdown:
        billable_delta = (
            int(breakdown.get("input", 0) or 0)
            + int(breakdown.get("output", 0) or 0)
            + int(breakdown.get("cache_creation", 0) or 0)
        )
    else:
        billable_delta = approx_tokens
    meta["token_billable_used"] = (
        int(meta.get("token_billable_used") or 0) + billable_delta
    )

    role_key = rp.role.replace("-", "_")
    if cost_usd:
        meta["token_cost_usd"] = round(
            float(meta.get("token_cost_usd") or 0.0) + cost_usd, 6
        )
        # Daily budget tracking — soft/hard cap check
        budget = record_cost(cost_usd)
        if (DAILY_USD_HARD_CAP > 0
                and budget["spent_usd"] >= DAILY_USD_HARD_CAP
                and not budget["hard_paused"]):
            budget["hard_paused"] = True
            save_daily_budget(budget)
            try:
                LOCKS_DIR.mkdir(parents=True, exist_ok=True)
                TEAM_PAUSED.write_text(json.dumps({
                    "reason": "daily_budget_hard_cap_exceeded",
                    "spent_usd": budget["spent_usd"],
                    "hard_cap": DAILY_USD_HARD_CAP,
                    "paused_at": _utcnow_iso(),
                }, indent=2))
            except OSError as exc:  # pragma: no cover
                logger.warning("budget pause flag write failed: %s", exc)
            slack_notify(
                "budget_hard_cap",
                summary=(
                    f"Daily cost hard cap exceeded: "
                    f"${budget['spent_usd']:.2f} >= ${DAILY_USD_HARD_CAP:.2f} "
                    "— daemon paused (resume via team.sh resume)"
                ),
            )
            logger.error(
                "daily budget hard cap exceeded: spent=%.2f cap=%.2f -> paused",
                budget["spent_usd"], DAILY_USD_HARD_CAP,
            )
        elif (DAILY_USD_SOFT_CAP > 0
                and budget["spent_usd"] >= DAILY_USD_SOFT_CAP
                and not budget["soft_warned"]):
            budget["soft_warned"] = True
            save_daily_budget(budget)
            slack_notify(
                "budget_soft_cap",
                summary=(
                    f"Daily cost soft cap exceeded: "
                    f"${budget['spent_usd']:.2f} >= ${DAILY_USD_SOFT_CAP:.2f} "
                    "(warning; daemon continues)"
                ),
            )
            logger.warning(
                "daily budget soft cap exceeded: spent=%.2f cap=%.2f",
                budget["spent_usd"], DAILY_USD_SOFT_CAP,
            )
    if breakdown:
        agg = dict(meta.get("token_breakdown") or {})
        for k, v in breakdown.items():
            agg[k] = int(agg.get(k, 0) or 0) + int(v or 0)
        meta["token_breakdown"] = agg
    if approx_tokens:
        per_role = dict(meta.get("token_per_role") or {})
        per_role[role_key] = int(per_role.get(role_key, 0) or 0) + approx_tokens
        meta["token_per_role"] = per_role
    if model_usage:
        agg_mu = dict(meta.get("model_usage") or {})
        for model_id, mu in model_usage.items():
            if not isinstance(mu, dict):
                continue
            cur = dict(agg_mu.get(model_id) or {})
            for k in (
                "inputTokens",
                "outputTokens",
                "cacheReadInputTokens",
                "cacheCreationInputTokens",
                "webSearchRequests",
            ):
                cur[k] = int(cur.get(k, 0) or 0) + int(mu.get(k) or 0)
            cur["costUSD"] = round(
                float(cur.get("costUSD", 0.0) or 0.0)
                + float(mu.get("costUSD") or 0.0),
                6,
            )
            agg_mu[model_id] = cur
        meta["model_usage"] = agg_mu

    failure_reasons = list(meta.get("failure_reasons") or [])

    # API quota early-detect: rc != 0 + known pattern in output
    if returncode != 0:
        quota_pattern = _detect_quota_error(stdout, stderr)
        if quota_pattern is not None:
            meta["status"] = "awaiting_api_quota"
            meta["owner_agent"] = None
            quota_msg = (
                f"{rp.role} api_quota_exhausted: pattern='{quota_pattern}'"
                " detected; status=awaiting_api_quota (retry counter NOT"
                " incremented)"
            )
            if quota_msg not in failure_reasons:
                failure_reasons.append(quota_msg)
            meta["failure_reasons"] = failure_reasons
            meta["api_quota_blocked_at"] = _utcnow_iso()
            # Try to parse a vendor-supplied reset time so the auto-resume
            # helper can wake the task precisely instead of using the 60-min
            # fallback window.
            try:
                haystack = (
                    stdout.decode("utf-8", errors="replace")
                    + "\n"
                    + stderr.decode("utf-8", errors="replace")
                )
            except (AttributeError, UnicodeDecodeError):
                haystack = ""
            reset_at = _parse_quota_reset_at(haystack) if haystack else None
            if reset_at:
                meta["api_quota_reset_at"] = reset_at
            atomic_write_json(meta_path, meta)
            slack_notify(
                "info",
                issue=rp.task_id,
                agent=rp.role,
                module=str(meta.get("module") or ""),
                summary=(
                    f"API quota exhausted (pattern: {quota_pattern}); task "
                    "awaiting_api_quota — auto-resume after reset window"
                ),
            )
            logger.warning(
                "API quota exhausted (pattern=%s) task=%s role=%s -> "
                "awaiting_api_quota (retry counter NOT incremented)",
                quota_pattern, rp.task_id, rp.role,
            )
            return

    if returncode != 0:
        msg = stderr[-500:].decode(errors="replace") if stderr else f"rc={returncode}"
        failure_reasons.append(f"{rp.role} rc={returncode}: {msg.strip()[:200]}")
        meta["failure_reasons"] = failure_reasons
        # Do NOT force role_done; the agent itself owns that flag. Retries
        # are already tracked via retry_count.
    else:
        # rc == 0: check FAIL outcome detection for tester / qa.
        role_done_val = (meta.get("role_done") or {}).get(role_key)
        failed_outcome_role = _detect_failed_outcome_role(rp.role, task_dir)
        if not role_done_val and failed_outcome_role:
            meta["status"] = failed_outcome_role  # tester_failed / qa_failed
            meta["owner_agent"] = None
            failure_reasons.append(
                f"{rp.role} FAIL outcome detected (rc=0, role_done null, "
                f".md says 'Result: FAIL') -> status={failed_outcome_role}"
            )
            meta["failure_reasons"] = failure_reasons
            logger.info(
                "post-run: %s FAIL outcome -> status=%s (developer retry "
                "will trigger)",
                rp.role, failed_outcome_role,
            )

    # Token budget enforcement (cache-aware: uses billable, not raw total)
    budget = meta.get("token_budget") or {}
    soft = int(budget.get("soft_limit") or 0)
    hard = int(budget.get("hard_limit") or 0)
    used = int(meta.get("token_billable_used") or meta["token_used"])
    if hard > 0 and used >= hard:
        meta["status"] = "failed"
        failure_reasons.append(
            f"token hard limit exceeded (billable={used} hard={hard})"
        )
        meta["failure_reasons"] = failure_reasons
        atomic_write_json(meta_path, meta)
        slack_notify(
            "retry_limit",
            issue=rp.task_id,
            agent=rp.role,
            module=str(meta.get("module") or ""),
            summary=f"Token hard limit ({hard}) exceeded — task failed",
        )
        return
    if soft > 0 and used >= soft and not meta.get("_soft_warned"):
        meta["_soft_warned"] = True
        slack_notify(
            "info",
            issue=rp.task_id,
            agent=rp.role,
            module=str(meta.get("module") or ""),
            summary=f"Token soft limit ({soft}) exceeded — warning",
        )

    # Cycle reset on review_failed / tester_failed / qa_failed: clear stale
    # downstream role_done flags so the next dev retry's review cycle
    # actually re-runs against the new code.
    new_status = meta.get("status")
    if new_status in ("review_failed", "tester_failed", "qa_failed"):
        role_done = dict(meta.get("role_done") or {})
        invalidated: list[str] = []
        for r in ("reviewer", "tester", "security_reviewer", "qa"):
            if role_done.get(r):
                role_done[r] = None
                invalidated.append(r)
        if invalidated:
            meta["role_done"] = role_done
            logger.info(
                "cycle reset: %s transition cleared role_done for %s (task=%s)",
                new_status, ",".join(invalidated), rp.task_id,
            )

    atomic_write_json(meta_path, meta)


# ---------------------------------------------------------------------------
# Completed-task migration
# ---------------------------------------------------------------------------
def move_to_completed(task: Task) -> None:
    """Append done/failed task to completed.jsonl and remove from active.json.

    The record harvests both token counters (`token_used` raw + cache_read,
    `token_billable_used` enforcement-only) plus per-role breakdown, model
    usage, and `architect_revision_count` so post-mortem queries against
    `completed.jsonl` don't have to reconstruct any of these from logs.
    """
    record = {
        "task_id": task.task_id,
        "status": task.status,
        "module": task.module,
        "moved_at": _utcnow_iso(),
        "token_used": task.token_used,
        "token_billable_used": int(task.meta.get("token_billable_used") or 0),
        "token_cost_usd": float(task.meta.get("token_cost_usd") or 0.0),
        "token_breakdown": task.meta.get("token_breakdown") or {},
        "token_per_role": task.meta.get("token_per_role") or {},
        "model_usage": task.meta.get("model_usage") or {},
        "retry_count": task.retry_count,
        "architect_revision_count": int(
            task.meta.get("architect_revision_count") or 0
        ),
        "failure_reasons": task.meta.get("failure_reasons") or [],
    }
    append_jsonl(COMPLETED_JSONL, record)
    logger.info("task moved to completed: id=%s status=%s",
                task.task_id, task.status)


def prune_active_json(remove_ids: set[str]) -> None:
    """Strip terminal task entries from active.json."""
    if not remove_ids:
        return
    data = read_json(ACTIVE_JSON) or {}
    raw_tasks = data.get("tasks") or []
    new_tasks = []
    for entry in raw_tasks:
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("task_id") or entry.get("id") or "")
        if tid in remove_ids:
            continue
        new_tasks.append(entry)
    data["tasks"] = new_tasks
    data["updated_at"] = _utcnow_iso()
    atomic_write_json(ACTIVE_JSON, data)


def prune_orphaned_from_active() -> int:
    """Remove ids from active.json that already appear in completed.jsonl
    (orphan handling).

    After the retrospective role moves a task to `.state/archive/`, the
    `meta.json` no longer lives under `.state/tasks/<id>/`. In that case
    `read_active_tasks()` cannot construct a Task and `_harvest_terminals`
    skips the entry, leaving a minimal descriptor in active.json that the
    daemon would otherwise revisit on every dispatch tick.

    This function scans completed.jsonl and prunes any matching ids that are
    still hanging around in active.json. Idempotent: ids not present in
    completed.jsonl are left alone.
    """
    if not COMPLETED_JSONL.exists() or not ACTIVE_JSON.exists():
        return 0
    completed_ids: set[str] = set()
    try:
        for line in COMPLETED_JSONL.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = rec.get("task_id") or rec.get("id")
            if tid:
                completed_ids.add(str(tid))
    except OSError:
        return 0
    if not completed_ids:
        return 0
    data = read_json(ACTIVE_JSON) or {}
    raw_tasks = data.get("tasks") or []
    new_tasks = [
        t for t in raw_tasks
        if isinstance(t, dict)
        and str(t.get("task_id") or t.get("id") or "") not in completed_ids
    ]
    removed = len(raw_tasks) - len(new_tasks)
    if removed > 0:
        data["tasks"] = new_tasks
        data["updated_at"] = _utcnow_iso()
        atomic_write_json(ACTIVE_JSON, data)
        logger.info(
            "orphan prune: %d task(s) removed from active.json (already in completed.jsonl)",
            removed,
        )
    return removed


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
class Daemon:
    """Pipeline daemon — single-instance owner of the asyncio loop."""

    def __init__(self, run_once: bool = False) -> None:
        self.run_once = run_once
        self.stop_signal = asyncio.Event()
        self.running: list[RunningProc] = []
        self.started_at = _utcnow_iso()
        self.completed_count = 0
        self.failed_count = 0
        self.token_total = 0
        self.module_breakdown: dict[str, int] = {}

    # ---- signal handlers ----
    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._on_signal, sig)
            except NotImplementedError:  # pragma: no cover (Windows)
                signal.signal(sig, lambda *_: self.stop_signal.set())

    def _on_signal(self, sig: int) -> None:
        logger.info("signal received: %s — graceful shutdown",
                    signal.Signals(sig).name)
        self.stop_signal.set()

    # ---- spawn helpers ----
    def _active_proc_count_for_task(self, task_id: str) -> int:
        return sum(1 for rp in self.running if rp.task_id == task_id)

    def _is_role_running(self, task_id: str, role: str) -> bool:
        return any(rp.task_id == task_id and rp.role == role
                   for rp in self.running)

    def _running_modules(self, by_id: dict[str, Task]) -> dict[str, str]:
        """Map of {module: task_id} for currently running subprocesses.

        Used by the module-conflict guard in `_dispatch` so that two tasks
        in the same module never spawn agents in parallel.
        """
        mods: dict[str, str] = {}
        for rp in self.running:
            t = by_id.get(rp.task_id)
            if t and t.module:
                mods[t.module] = rp.task_id
        return mods

    async def _dispatch(self, tasks: list[Task]) -> None:
        """Run spawn dispatch over the active task list."""
        by_id = {t.task_id: t for t in tasks}

        for task in tasks:
            if self.stop_signal.is_set():
                break
            if len(self.running) >= MAX_PARALLEL_TASKS * \
                    MAX_PARALLEL_SUBAGENTS_PER_TASK:
                break
            if task.is_terminal:
                continue
            # Spawn-blocked: explicit short-circuit so logs don't show the
            # task being inspected every poll. (next_actions() also returns
            # [] for these statuses; this is just clearer.)
            if task.status in SPAWN_BLOCKED_STATUSES:
                continue
            if task.has_blocking_deps(by_id):
                continue

            # Module conflict guard: another active task in the same module
            # blocks this one (race condition guard). Set
            # LSD_ALLOW_MODULE_CONFLICT=1 to bypass.
            if not ALLOW_MODULE_CONFLICT and task.module:
                running_mods = self._running_modules(by_id)
                conflict_owner = running_mods.get(task.module)
                if conflict_owner and conflict_owner != task.task_id:
                    logger.debug(
                        "module conflict skip: task=%s module=%s busy_by=%s",
                        task.task_id, task.module, conflict_owner,
                    )
                    continue

            next_roles = task.next_actions()
            if not next_roles:
                continue

            # Per-task parallel cap
            for role in next_roles:
                if self.stop_signal.is_set():
                    break
                if self._is_role_running(task.task_id, role):
                    continue
                if self._active_proc_count_for_task(task.task_id) >= \
                        MAX_PARALLEL_SUBAGENTS_PER_TASK:
                    break
                # Global cap: task-slot ceiling
                distinct_tasks = {rp.task_id for rp in self.running}
                if (task.task_id not in distinct_tasks and
                        len(distinct_tasks) >= MAX_PARALLEL_TASKS):
                    break

                rp = await spawn_agent(role, task)
                if rp is not None:
                    self.running.append(rp)

    def _promote_decomposed_parents(self, tasks: list[Task]) -> None:
        """Promote `decomposed` parents to `documented` once every child is done.

        We leave `role_done.retrospective` as-is so that, on the next dispatch
        cycle, the standard pipeline can still spawn retrospective for the
        parent. Since the daemon harvests `done` tasks immediately, we set the
        parent's status to `documented` instead — the standard pipeline then
        runs retrospective automatically (used for collective lesson distill).
        """
        by_id = {t.task_id: t for t in tasks}
        for task in tasks:
            if task.status != "decomposed":
                continue
            if not task.is_decomposed_and_children_done(by_id):
                continue
            # Idempotency: only promote once.
            if task.meta.get("_decomposed_parent_promoted"):
                continue
            log = _bind(role="daemon", task_id=task.task_id,
                        module=task.module)
            log.info(
                "decomposed parent — all children done; promoting to "
                "documented for retrospective: id=%s", task.task_id,
            )
            task.meta["_decomposed_parent_promoted"] = True
            # Set status to `documented` so the standard pipeline spawns the
            # retrospective (collective lesson distillation).
            task.meta["status"] = "documented"
            task.meta["updated_at"] = _utcnow_iso()
            task.save_meta()

    async def _harvest_terminals(self, tasks: list[Task]) -> None:
        """Move done/failed tasks and prune them from active.json."""
        to_remove: set[str] = set()
        for task in tasks:
            if not task.is_terminal:
                continue
            # Wait if there is still a subprocess for this task.
            if self._active_proc_count_for_task(task.task_id) > 0:
                continue
            move_to_completed(task)
            if task.status == "done":
                self.completed_count += 1
                slack_notify(
                    "done",
                    issue=task.task_id,
                    module=task.module,
                    summary="Task completed",
                )
            else:
                self.failed_count += 1
                slack_notify(
                    "error",
                    issue=task.task_id,
                    module=task.module,
                    summary="Task FAILED — manual intervention required",
                )
                # STATUS.md fail-safe: failed tasks bypass the documenter.
                # Append a single changelog line so they are not lost.
                appended = status_md_failsafe_append(
                    task.task_id, task.module, task.status,
                    summary=(task.meta.get("failure_reasons") or [""])[-1][:200],
                )
                if appended:
                    logger.info(
                        "STATUS.md fail-safe entry appended for failed task=%s",
                        task.task_id,
                    )
            self.token_total += task.token_used
            mod = task.module or "unknown"
            self.module_breakdown[mod] = \
                self.module_breakdown.get(mod, 0) + task.token_used
            to_remove.add(task.task_id)
        if to_remove:
            prune_active_json(to_remove)

    async def main_loop(self) -> None:
        """Main poll loop."""
        acquire_lock()
        self.install_signal_handlers()
        logger.info(
            "pipeline daemon started: poll=%ss max_tasks=%s max_sub=%s state=%s",
            POLL_INTERVAL_SEC, MAX_PARALLEL_TASKS,
            MAX_PARALLEL_SUBAGENTS_PER_TASK, STATE_DIR,
        )
        try:
            while not self.stop_signal.is_set():
                # Pause check
                if TEAM_PAUSED.exists():
                    logger.debug("paused, sleeping")
                    await asyncio.sleep(POLL_INTERVAL_SEC)
                    continue

                tasks = read_active_tasks()

                # 1) Promote decomposed parents whose children are all done.
                self._promote_decomposed_parents(tasks)

                # 2) Harvest terminal tasks.
                await self._harvest_terminals(tasks)

                # 2b) Orphan prune — when retrospective archives a task, the
                #     meta.json disappears and harvest may skip it; remove
                #     active.json entries that are already in completed.jsonl.
                prune_orphaned_from_active()

                # 2c) Auto-resume awaiting_api_quota tasks once the reset
                #     window (or explicit reset_at) has elapsed.
                auto_resume_quota_blocked_tasks()

                # 3) Reap finished subprocesses.
                self.running = await reap_completed_subprocesses(self.running)

                # 4) Re-read (meta may have changed) and dispatch.
                tasks = read_active_tasks()
                await self._dispatch(tasks)

                # 5) Idle exit.
                if not tasks and not self.running:
                    logger.info("no active tasks and no running procs — exit")
                    break

                if self.run_once:
                    break

                try:
                    await asyncio.wait_for(
                        self.stop_signal.wait(),
                        timeout=POLL_INTERVAL_SEC,
                    )
                except asyncio.TimeoutError:
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("fatal in main loop: %s", exc)
            slack_notify(
                "error",
                summary=f"pipeline daemon fatal: {exc}",
            )
        finally:
            await self._graceful_shutdown()
            release_lock()
            self._write_final_report()

    async def _graceful_shutdown(self) -> None:
        """Wait for running subprocesses, then SIGTERM, then SIGKILL cascade.

        Cascade:
          1) Wait up to 60 s for natural exit (poll-reap)
          2) SIGTERM remaining processes
          3) Wait 10 s for SIGTERM to take effect
          4) SIGKILL anything still alive (zombie guard) + write log marker
        """
        if not self.running:
            return
        logger.info("waiting for %d running subprocess(es) to finish",
                    len(self.running))
        deadline = time.time() + 60
        while self.running and time.time() < deadline:
            self.running = await reap_completed_subprocesses(self.running)
            if self.running:
                await asyncio.sleep(1)

        # SIGTERM cascade
        for rp in self.running:
            if rp.proc.returncode is None:
                logger.warning("SIGTERM slow subprocess: role=%s pid=%s",
                               rp.role, rp.proc.pid)
                try:
                    rp.proc.terminate()
                except ProcessLookupError:
                    pass

        # 10 s grace before SIGKILL
        sigterm_deadline = time.time() + 10
        while self.running and time.time() < sigterm_deadline:
            await asyncio.sleep(1)
            still_alive: list[RunningProc] = []
            for rp in self.running:
                if rp.proc.returncode is None:
                    still_alive.append(rp)
            self.running = still_alive

        # SIGKILL fallback — zombie / idle hang guard
        for rp in self.running:
            if rp.proc.returncode is None:
                logger.error("SIGKILL stuck subprocess: role=%s pid=%s",
                             rp.role, rp.proc.pid)
                try:
                    rp.proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    rp.log_path.write_bytes(
                        b"=== SHUTDOWN_KILLED ===\n"
                        b"daemon graceful shutdown deadline exceeded, SIGKILL.\n"
                    )
                except OSError:
                    pass
        self.running = []

    def _write_final_report(self) -> None:
        stopped_at = _utcnow_iso()
        try:
            start_dt = datetime.fromisoformat(self.started_at)
            stop_dt = datetime.fromisoformat(stopped_at)
            dur = stop_dt - start_dt
            dur_s = str(dur).split(".")[0]
        except ValueError:
            dur_s = "?"

        breakdown_lines = [f"  {m}: ~{n} tok"
                           for m, n in sorted(self.module_breakdown.items())]
        report = (
            "=== Pipeline Daemon Final Report ===\n"
            f"Started: {self.started_at}\n"
            f"Stopped: {stopped_at}\n"
            f"Duration: {dur_s}\n"
            f"Tasks completed: {self.completed_count}\n"
            f"Tasks failed: {self.failed_count}\n"
            f"Total tokens (approx): {self.token_total}\n"
            "Per-module breakdown:\n"
            + ("\n".join(breakdown_lines) if breakdown_lines else "  (none)")
            + "\n=========================\n"
        )
        logger.info("%s", report)
        try:
            (LOG_FILE.parent / "ai-pipeline-daemon-final.txt").write_text(
                report, encoding="utf-8"
            )
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pipeline-daemon.py",
        description="AI Pipeline Local Team Daemon — pure-local poll loop.",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle and exit (debug).",
    )
    p.add_argument(
        "--state-dir",
        default=str(STATE_DIR),
        help=f"State directory (default: {STATE_DIR})",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    global STATE_DIR, ACTIVE_JSON, COMPLETED_JSONL, TASKS_DIR, LOCKS_DIR
    global TEAM_LOCK, TEAM_PAUSED
    if args.state_dir:
        STATE_DIR = Path(os.path.expanduser(args.state_dir))
        ACTIVE_JSON = STATE_DIR / "active.json"
        COMPLETED_JSONL = STATE_DIR / "completed.jsonl"
        TASKS_DIR = STATE_DIR / "tasks"
        LOCKS_DIR = STATE_DIR / "locks"
        TEAM_LOCK = LOCKS_DIR / "team.lock"
        TEAM_PAUSED = LOCKS_DIR / "team.paused"

    daemon = Daemon(run_once=args.once)
    try:
        asyncio.run(daemon.main_loop())
    except RuntimeError as exc:
        logger.error("startup failed: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

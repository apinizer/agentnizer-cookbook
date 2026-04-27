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

Pipeline roles (13 total):
    planner, planner_decompose, analyst, architect, developer,
    reviewer, review-correctness, review-convention, review-quality,
    tester, qa, security_reviewer, documenter, retrospective
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
# Hard timeout (seconds) for each agent subprocess. When the Claude API
# stream stalls or hangs, the subprocess is terminated via SIGTERM -> SIGKILL
# and the retry mechanism kicks in.
AGENT_TIMEOUT_SEC = int(os.getenv("LSD_AGENT_TIMEOUT_SEC", "900"))
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
    "retrospective": 1,
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
    "qa_passed": "documenting",
    "qa_failed": "developing",
    "documented": "retrospecting",
}

TERMINAL_STATUSES = {"done", "failed"}

# Decomposition flow statuses:
#  - decomposition_requested: architect identified an oversized task; planner
#    will be triggered in decompose mode.
#  - decomposed: planner created sub-tasks; the parent stops spawning agents
#    and waits for its children to finish.
STATUSES_AWAITING_CHILDREN = {"decomposed"}


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

        # review_failed / qa_failed -> developer retry
        if self.status in ("review_failed", "qa_failed"):
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


def build_agent_prompt(role: str, task: Task) -> str:
    """Build the `-p` prompt text passed to the Claude Code subprocess.

    For `planner_decompose`, mode parameters (MODE=decompose,
    PARENT_TASK_ID=<id>) are appended so that planner.md can branch into its
    decompose section.
    """
    meta_text = json.dumps(task.meta, indent=2, ensure_ascii=False)
    agent_md = _read_text_safe(_agent_md_path(role))
    output_file = ROLE_OUTPUT_FILE.get(role, f"{role}.md")
    read_allowlist = task.meta.get("read_allowlist") or []
    allowlist_text = (
        ", ".join(str(p) for p in read_allowlist)
        if read_allowlist else "(unrestricted)"
    )

    if role == "planner_decompose":
        # Decompose mode: same file as planner, special mode parameters.
        prompt = (
            f"# AI Pipeline Local Team — Role: planner (MODE=decompose)\n"
            f"Task ID (parent): {task.task_id}\n"
            f"Module : {task.module}\n"
            f"Status : {task.status}\n\n"
            f"## Mode parameters\n"
            f"MODE=decompose\n"
            f"PARENT_TASK_ID={task.task_id}\n\n"
            f"## meta.json (ephemeral)\n"
            f"```json\n{meta_text}\n```\n\n"
            f"## agent.md (ephemeral)\n"
            f"{agent_md}\n\n"
            f"## Read allowlist (informational)\n"
            f"{allowlist_text}\n\n"
            f"## Task\n"
            f"Apply the 'Decompose Mode' section of planner.md. Parse the\n"
            f"'## Sub-task Decomposition' section in the parent task's\n"
            f"design.md, create the sub-tasks, and update the parent\n"
            f"meta.json. When finished, set role_done.planner_decompose = ts,\n"
            f"status = 'decomposed', and blocks = [<new sub-task ids>].\n"
        )
        return prompt

    prompt = (
        f"# AI Pipeline Local Team — Role: {role}\n"
        f"Task ID: {task.task_id}\n"
        f"Module : {task.module}\n"
        f"Status : {task.status}\n\n"
        f"## meta.json (ephemeral)\n"
        f"```json\n{meta_text}\n```\n\n"
        f"## agent.md (ephemeral)\n"
        f"{agent_md}\n\n"
        f"## Read allowlist (informational)\n"
        f"{allowlist_text}\n\n"
        f"## Task\n"
        f"Apply the '{role}' role for the task described in meta.json.\n"
        f"Output file: .state/tasks/{task.task_id}/{output_file}\n"
        f"When finished, set role_done.{role} = true in meta.json and\n"
        f"transition to the appropriate status.\n"
    )
    return prompt


async def spawn_agent(role: str, task: Task) -> RunningProc | None:
    """Spawn an agent as a Claude Code subprocess."""
    log = _bind(role=role, task_id=task.task_id, module=task.module)

    # Retry guard (developer/tester/qa, etc.)
    if role in DEFAULT_MAX_RETRIES and task.retries_exceeded(role):
        log.warning("retry limit exceeded — task failed: role=%s", role)
        task.meta["status"] = "failed"
        task.meta.setdefault("failure_reasons", []).append(
            f"{role} retry limit exceeded"
        )
        task.save_meta()
        slack_notify(
            "retry_limit",
            issue=task.task_id,
            agent=role,
            module=task.module,
            summary=f"{role} retry limit exceeded",
        )
        return None

    prompt = build_agent_prompt(role, task)
    task_dir = task.task_dir()
    task_dir.mkdir(parents=True, exist_ok=True)
    log_dir = task_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{role}-{int(time.time())}.log"

    cmd = [CLAUDE_BIN, "-p", prompt, "--no-session-persistence"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(STATE_DIR.parent),
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

    return RunningProc(
        task_id=task.task_id,
        role=role,
        proc=proc,
        started_at=time.time(),
        log_path=log_path,
    )


# ---------------------------------------------------------------------------
# Subprocess reaping & token tracking
# ---------------------------------------------------------------------------
def _approx_tokens(byte_count: int) -> int:
    """Rough token approximation: 4 bytes per token."""
    return max(0, byte_count // 4)


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

    # 5-second grace period; collect partial output via communicate().
    stdout, stderr = b"", b""
    try:
        stdout, stderr = await asyncio.wait_for(rp.proc.communicate(), timeout=5.0)
    except asyncio.TimeoutError:
        # SIGKILL
        log.warning(
            "agent timeout: role=%s SIGTERM insufficient -> sigkill",
            rp.role,
        )
        try:
            rp.proc.kill()
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = await asyncio.wait_for(rp.proc.communicate(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        stderr = (str(exc) + " (during terminate communicate)").encode()

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
            if AGENT_TIMEOUT_SEC > 0 and elapsed >= AGENT_TIMEOUT_SEC:
                stdout, stderr = await _terminate_runaway_proc(rp)
                # Persist partial output to the log file.
                try:
                    rp.log_path.write_bytes(
                        b"=== TIMEOUT ===\nAGENT_TIMEOUT_SEC exceeded: "
                        + f"{AGENT_TIMEOUT_SEC}s\n=== STDOUT (partial) ===\n".encode()
                        + stdout
                        + b"\n=== STDERR (partial) ===\n"
                        + stderr
                    )
                except OSError:
                    pass

                rc = rp.proc.returncode if rp.proc.returncode is not None else -1
                log = _bind(role=rp.role, task_id=rp.task_id)
                log.warning(
                    "agent timeout finalized: role=%s rc=%s elapsed=%.1fs",
                    rp.role, rc, elapsed,
                )

                approx = _approx_tokens(len(stdout) + len(stderr))
                # _post_run_update does not flip role_done; retry_count was
                # already incremented at spawn time. Append a timeout note to
                # failure_reasons.
                tagged_stderr = (
                    f"timeout after {AGENT_TIMEOUT_SEC}s; ".encode() + stderr
                )
                await _post_run_update(rp, approx, rc, tagged_stderr)
                timed_out.add(id(rp))
            else:
                still_running.append(rp)
            continue

        # Process finished: read stdout/stderr and write the log file.
        try:
            stdout, stderr = await rp.proc.communicate()
        except Exception as exc:  # noqa: BLE001
            stdout, stderr = b"", str(exc).encode()

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

        # Token approximation
        approx = _approx_tokens(len(stdout) + len(stderr))
        await _post_run_update(rp, approx, rc, stderr)

    return still_running


async def _post_run_update(rp: RunningProc, approx_tokens: int,
                           returncode: int, stderr: bytes) -> None:
    """Refresh meta.json after a subprocess finishes; check budget and errors."""
    task_dir = TASKS_DIR / rp.task_id
    meta_path = task_dir / "meta.json"
    meta = read_json(meta_path) or {}
    meta["token_used"] = int(meta.get("token_used") or 0) + approx_tokens

    failure_reasons = list(meta.get("failure_reasons") or [])
    if returncode != 0:
        msg = stderr[-500:].decode(errors="replace") if stderr else f"rc={returncode}"
        failure_reasons.append(f"{rp.role} rc={returncode}: {msg.strip()[:200]}")
        meta["failure_reasons"] = failure_reasons
        # Do NOT force role_done; the agent itself owns that flag. Retries are
        # already tracked via retry_count.

    # Token budget enforcement
    budget = meta.get("token_budget") or {}
    soft = int(budget.get("soft_limit") or 0)
    hard = int(budget.get("hard_limit") or 0)
    used = meta["token_used"]
    if hard > 0 and used >= hard:
        meta["status"] = "failed"
        failure_reasons.append(f"token hard limit exceeded (used={used} hard={hard})")
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

    atomic_write_json(meta_path, meta)


# ---------------------------------------------------------------------------
# Completed-task migration
# ---------------------------------------------------------------------------
def move_to_completed(task: Task) -> None:
    """Append done/failed task to completed.jsonl and remove from active.json."""
    record = {
        "task_id": task.task_id,
        "status": task.status,
        "module": task.module,
        "moved_at": _utcnow_iso(),
        "token_used": task.token_used,
        "retry_count": task.retry_count,
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
            if task.has_blocking_deps(by_id):
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
        """Wait for running subprocesses to finish before exiting."""
        if not self.running:
            return
        logger.info("waiting for %d running subprocess(es) to finish",
                    len(self.running))
        deadline = time.time() + 60  # wait at most 60 seconds
        while self.running and time.time() < deadline:
            self.running = await reap_completed_subprocesses(self.running)
            if self.running:
                await asyncio.sleep(1)
        # Terminate any subprocess still alive past the deadline.
        for rp in self.running:
            if rp.proc.returncode is None:
                logger.warning("terminating slow subprocess: role=%s pid=%s",
                               rp.role, rp.proc.pid)
                try:
                    rp.proc.terminate()
                except ProcessLookupError:
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

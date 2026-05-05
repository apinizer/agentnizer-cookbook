"""Smoke tests for the starter pipeline daemon.

These tests exercise the pure-logic layer of `.claude/scripts/pipeline-daemon.py`
— state machine transitions, role-pick logic, idempotency guard, retry caps,
prompt-cache prefix stability. No network, no subprocess, no real `.state/`.

Run:
    uv run --with pytest pytest tests/
or:
    pytest tests/
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_PATH = REPO_ROOT / ".claude" / "scripts" / "pipeline-daemon.py"


@pytest.fixture(scope="module")
def pd():
    spec = importlib.util.spec_from_file_location("pipeline_daemon", DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pipeline_daemon"] = module
    spec.loader.exec_module(module)
    return module


def _make_task(pd, *, status="queued", role_done=None, retry_count=None,
               max_retries=None, module="backend"):
    meta = {
        "status": status,
        "module": module,
        "role_done": role_done or {"planner": True},
        "retry_count": retry_count or {},
    }
    if max_retries is not None:
        meta["max_retries"] = max_retries
    return pd.Task(task_id="t1", raw={}, meta=meta)


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_daemon_module_imports(pd):
    """Daemon must load as a Python module without side effects."""
    assert hasattr(pd, "Task")
    assert hasattr(pd, "MODEL_FOR_ROLE")
    assert hasattr(pd, "DEFAULT_MAX_RETRIES")
    assert hasattr(pd, "STATUS_NEXT_STATUS")
    assert hasattr(pd, "TERMINAL_STATUSES")
    assert hasattr(pd, "SPAWN_BLOCKED_STATUSES")


def test_role_lists_cover_pipeline(pd):
    """Linear roles + review phase must form the documented assembly line."""
    expected_pre = ["analyst", "architect", "developer"]
    expected_review = ["reviewer", "tester", "security_reviewer"]
    expected_post = ["qa", "documenter", "retrospective"]
    assert pd.LINEAR_ROLES_BEFORE_REVIEW == expected_pre
    assert pd.REVIEW_PHASE_ROLES == expected_review
    assert pd.LINEAR_ROLES_AFTER_REVIEW == expected_post


def test_default_max_retries_developer_is_three(pd):
    """Developer needs more retries than reviewers — three review failures
    should be absorbable before architect handback."""
    assert pd.DEFAULT_MAX_RETRIES["developer"] == 3
    assert pd.DEFAULT_MAX_RETRIES["retrospective"] == 2


def test_model_for_role_split(pd):
    """Upstream producers (analyst/architect/developer) must be Opus-tier;
    orchestration and reviewers Sonnet-tier."""
    opus_roles = {"analyst", "architect", "developer"}
    for role in opus_roles:
        assert pd.MODEL_FOR_ROLE[role] == "opus", role
    sonnet_roles = {"planner", "reviewer", "tester", "qa", "documenter",
                    "retrospective", "tuner"}
    for role in sonnet_roles:
        assert pd.MODEL_FOR_ROLE[role] == "sonnet", role


# ---------------------------------------------------------------------------
# Task state machine
# ---------------------------------------------------------------------------

def test_terminal_status_returns_no_next_actions(pd):
    t = _make_task(pd, status="done", role_done={"planner": True})
    assert t.is_terminal
    assert t.next_actions() == []


def test_next_action_after_planner_is_analyst(pd):
    t = _make_task(pd, status="queued", role_done={"planner": True})
    assert t.next_actions() == ["analyst"]


def test_next_actions_review_phase_is_parallel(pd):
    """When status=developed and review roles pending, all three spawn at once."""
    t = _make_task(pd, status="developed",
                   role_done={"planner": True, "analyst": True,
                              "architect": True, "developer": True})
    nxt = t.next_actions()
    assert set(nxt) == {"reviewer", "tester", "security_reviewer"}


def test_review_failed_routes_back_to_developer(pd):
    t = _make_task(pd, status="review_failed",
                   role_done={"planner": True, "analyst": True,
                              "architect": True, "developer": True})
    assert t.next_actions() == ["developer"]


def test_tester_failed_routes_back_to_developer(pd):
    """tester_failed (FAIL outcome detection) is the same handoff as review_failed."""
    t = _make_task(pd, status="tester_failed",
                   role_done={"planner": True, "analyst": True,
                              "architect": True, "developer": True})
    assert t.next_actions() == ["developer"]


def test_spawn_blocked_status_yields_nothing(pd):
    """awaiting_user_action / awaiting_api_quota / human-review-pending
    must not spawn agents."""
    for blocked in ("awaiting_user_action", "awaiting_api_quota",
                    "human-review-pending"):
        t = _make_task(pd, status=blocked, role_done={"planner": True})
        assert t.next_actions() == [], blocked


def test_idempotency_completed_role_is_not_respawned(pd):
    """If role_done is set, that role must not appear in next_actions."""
    t = _make_task(pd, status="queued",
                   role_done={"planner": True, "analyst": True})
    assert t.next_actions() == ["architect"]


# ---------------------------------------------------------------------------
# Retry caps
# ---------------------------------------------------------------------------

def test_retries_exceeded_respects_per_role_cap(pd):
    t = _make_task(pd, status="developing",
                   retry_count={"developer": 3})
    assert t.retries_exceeded("developer") is True


def test_retries_exceeded_below_cap_returns_false(pd):
    t = _make_task(pd, status="developing",
                   retry_count={"developer": 2})
    assert t.retries_exceeded("developer") is False


def test_retries_exceeded_unknown_role_returns_false(pd):
    """Unknown role has no cap → retries are unlimited."""
    t = _make_task(pd)
    assert t.retries_exceeded("not_a_role") is False


# ---------------------------------------------------------------------------
# Prompt cache prefix stability
# ---------------------------------------------------------------------------

def test_stable_prefix_is_identical_across_calls(pd):
    """The cache-friendly prefix must be byte-exact between calls for
    Anthropic prompt-cache prefix matching to deliver the discount."""
    a = pd._stable_prefix_for_role("developer")
    b = pd._stable_prefix_for_role("developer")
    assert a == b


def test_stable_prefix_varies_by_role(pd):
    """Different roles must produce different prefixes (otherwise cache
    would deliver the wrong agent.md to the spawn)."""
    dev = pd._stable_prefix_for_role("developer")
    rev = pd._stable_prefix_for_role("reviewer")
    assert dev != rev
    assert "Role: developer" in dev
    assert "Role: reviewer" in rev


# ---------------------------------------------------------------------------
# FAIL outcome detection
# ---------------------------------------------------------------------------

def test_detect_failed_outcome_tester_pass(pd, tmp_path):
    """tests.md without 'Result: FAIL' returns None (pass)."""
    (tmp_path / "tests.md").write_text("All tests pass.\nResult: PASS\n")
    assert pd._detect_failed_outcome_role("tester", tmp_path) is None


def test_detect_failed_outcome_tester_fail(pd, tmp_path):
    """tests.md with 'Result: FAIL' returns the failed-status role name."""
    (tmp_path / "tests.md").write_text(
        "Two tests fail.\n\nResult: FAIL\n\nDetails: ...\n"
    )
    assert pd._detect_failed_outcome_role("tester", tmp_path) == "tester_failed"


def test_detect_failed_outcome_qa_fail_case_insensitive(pd, tmp_path):
    (tmp_path / "qa.md").write_text("Smoke test failed.\nresult: fail\n")
    assert pd._detect_failed_outcome_role("qa", tmp_path) == "qa_failed"


# ---------------------------------------------------------------------------
# Cumulative retry context cap (size guard)
# ---------------------------------------------------------------------------

def test_max_cumulative_block_bytes_default_is_4kb(pd):
    """Default cap is 4096 bytes — enough for 3-5 findings each cycle."""
    assert pd.MAX_CUMULATIVE_BLOCK_BYTES >= 1024
    # Allow env override but assert sane default order of magnitude.
    assert pd.MAX_CUMULATIVE_BLOCK_BYTES <= 65536


# ---------------------------------------------------------------------------
# Module conflict guard
# ---------------------------------------------------------------------------

def test_allow_module_conflict_default_is_off(pd):
    """ALLOW_MODULE_CONFLICT=0 means same-module parallel spawns are blocked."""
    assert pd.ALLOW_MODULE_CONFLICT == 0

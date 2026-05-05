#!/usr/bin/env python3
"""
.claude/hooks/notify-slack.py — AI Pipeline Slack notification hook

Sends real-time Slack notifications for pipeline events. Wires the
multi-task daemon's activity into the team's chat so live ops doesn't
require polling `team.sh status`.

Slack is optional in the sense that the daemon runs without it; this hook
is fire-and-forget if `PIPELINE_SLACK_BOT_TOKEN` / `PIPELINE_SLACK_CHANNEL`
are unset (prints to stderr, exits 0). Where Slack is configured, security
alerts are unconditional regardless of channel-mute settings — a CRITICAL
OWASP finding never silently slips.

The notifications come in two clearly separated categories:

  ─────────────────────────────────────────────────────────────────
  ACTION REQUIRED — needs a human to do something now
  ─────────────────────────────────────────────────────────────────
    security_alert       CRITICAL/HIGH OWASP finding — review now.
    retry_limit          An agent exhausted its retries; task FAILED.
    error                Daemon-level fatal — pipeline halted.
    awaiting_info        Triage couldn't proceed; analyst needs human input.
    design_gate          Architect finished; design needs human approval.
    qa_gate              Developer finished; QA needs to test the running app.
    review_failed        (Optional verbose mode) Code review FAIL.
    security_failed      (Optional verbose mode) Security review FAIL.
    test_failed          (Optional verbose mode) Tests FAIL.

  ─────────────────────────────────────────────────────────────────
  INFO — visibility only, no action required
  ─────────────────────────────────────────────────────────────────
    done                 Task completed successfully.
    phase_transition     Status moved (e.g. designed → developing).
    info                 Generic informational message.
    architect_done       Architect finished (verbose mode).
    developer_done       Developer finished (verbose mode).
    tester_done          Tests passed (verbose mode).
    reviewer_done        Code review passed (verbose mode).
    security_done        Security review passed (verbose mode).
    documenter_done      Documentation updated (verbose mode).
    retrospective_done   Retrospective wrote a lesson (verbose mode).

Action-required messages get a 🔔 prefix and a colored block (red /
amber). Info messages get a single emoji and stay quiet (no bold, no
block formatting).

In production we recommend muting INFO category by default and only
enabling ACTION REQUIRED. Slack channels become noisy fast otherwise —
the team is doing real work, you don't need a play-by-play.

Setup:
  Copy .claude/.env.example to .claude/.env and set:
    PIPELINE_SLACK_BOT_TOKEN=xoxb-...
    PIPELINE_SLACK_CHANNEL=#your-channel
  If unset, the hook prints to stderr and exits 0. Nothing in the
  pipeline depends on Slack delivery succeeding.
"""

import argparse
import os
import sys
from pathlib import Path

import requests

# ── Load .env ────────────────────────────────────────────────────────────
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

SLACK_BOT_TOKEN = os.environ.get("PIPELINE_SLACK_BOT_TOKEN", "")
SLACK_CHANNEL   = os.environ.get("PIPELINE_SLACK_CHANNEL", "")

# Optional opt-out for the noisy INFO category (set to "1" to suppress).
MUTE_INFO = os.environ.get("PIPELINE_SLACK_MUTE_INFO", "") == "1"

# ── Category map ─────────────────────────────────────────────────────────
ACTION_REQUIRED = {
    "security_alert",
    "retry_limit",
    "error",
    "awaiting_info",
    "design_gate",
    "qa_gate",
    "review_failed",
    "security_failed",
    "test_failed",
    # Legacy aliases:
    "reviewer_failed",
    "security_reviewer_failed",
    "tester_failed",
    # Extended taxonomy (2026-05-05):
    "budget_hard_cap",
    "differential_escalation",
    "module_conflict",
    "human_review_pending",
}

INFO = {
    "done",
    "phase_transition",
    "info",
    "architect_done",
    "developer_done",
    "tester_done",
    "reviewer_done",
    "security_done",
    "security_reviewer_done",
    "documenter_done",
    "retrospective_done",
    # Extended taxonomy (2026-05-05):
    "budget_soft_cap",
    "tuner_started",
    "tuner_done",
}

ALL_TYPES = sorted(ACTION_REQUIRED | INFO)


# ── Slack send ───────────────────────────────────────────────────────────
def send_message(text: str, blocks: list | None = None,
                 category: str = "info") -> dict:
    """Send a message to Slack (or stdout if Slack unconfigured)."""
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL:
        prefix = "[notify-slack:ACTION-REQUIRED]" if category == "action" \
                 else "[notify-slack:INFO]"
        print(f"{prefix} {text}")
        return {}

    payload = {"channel": SLACK_CHANNEL, "text": text}
    if blocks:
        payload["blocks"] = blocks

    try:
        r = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        data = r.json()
        if not data.get("ok"):
            print(f"[notify-slack] Slack API error: {data.get('error')}",
                  file=sys.stderr)
        return data
    except Exception as exc:  # noqa: BLE001 — fire-and-forget hook
        print(f"[notify-slack] Error: {exc}", file=sys.stderr)
        return {}


def action_block(title: str, body: str, color_emoji: str) -> list:
    """Format an ACTION REQUIRED message with a header + section block."""
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{color_emoji} {title}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body},
        },
    ]


# ── Main ─────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Pipeline Slack notification (action-required vs info)"
    )
    parser.add_argument("--type", required=True, choices=ALL_TYPES)
    parser.add_argument("--issue", default="",
                        help="Task id (the .state/tasks/<id> identifier)")
    parser.add_argument("--summary", default="")
    parser.add_argument("--module", default="", help="module name")
    parser.add_argument("--agent", default="", help="agent name")
    args = parser.parse_args()

    label = args.issue if args.issue else "(no task id)"
    is_action = args.type in ACTION_REQUIRED

    # Skip INFO-category messages if the user opted out
    if not is_action and MUTE_INFO:
        return

    category = "action" if is_action else "info"

    # ── ACTION REQUIRED ─────────────────────────────────────────────
    if args.type == "security_alert":
        blocks = action_block(
            f"🔴 SECURITY ALERT — {label}",
            f"*Module:* `{args.module or '?'}`\n\n{args.summary}",
            "🔔",
        )
        send_message(f"🔔 🔴 SECURITY — {label}: {args.summary}", blocks,
                     category)

    elif args.type == "retry_limit":
        blocks = action_block(
            f"⚠️ RETRY LIMIT — {label}",
            f"*Module:* `{args.module or '?'}`\n*Agent:* `{args.agent or '?'}`"
            f"\n\nTask FAILED — manual intervention needed.\n\n{args.summary}",
            "🔔",
        )
        send_message(f"🔔 ⚠️ RETRY LIMIT — {label}: {args.summary}", blocks,
                     category)

    elif args.type == "error":
        send_message(f"🔔 ❌ {label}: Error — {args.summary}",
                     category=category)

    elif args.type == "awaiting_info":
        send_message(f"🔔 ❓ {label}: Triage needs info — {args.summary}",
                     category=category)

    elif args.type == "design_gate":
        send_message(f"🔔 ⬛ {label}: DESIGN GATE — review the design and "
                     f"approve or reject. {args.summary}",
                     category=category)

    elif args.type == "qa_gate":
        send_message(f"🔔 ⬛ {label}: QA GATE — exercise the running app "
                     f"and accept or reject. {args.summary}",
                     category=category)

    elif args.type in ("review_failed", "reviewer_failed"):
        send_message(f"🔔 🔄 {label}: Code Review FAIL — {args.summary}",
                     category=category)

    elif args.type in ("security_failed", "security_reviewer_failed"):
        send_message(f"🔔 🛡️ {label}: Security Review FAIL — {args.summary}",
                     category=category)

    elif args.type in ("test_failed", "tester_failed"):
        send_message(f"🔔 🧪 {label}: Tests FAIL — {args.summary}",
                     category=category)

    # ── INFO ────────────────────────────────────────────────────────
    elif args.type == "done":
        send_message(f"✅ {label}: Completed — {args.summary}",
                     category=category)
    elif args.type == "phase_transition":
        send_message(f"➡️ {label}: {args.summary}", category=category)
    elif args.type == "info":
        send_message(f"ℹ️ {label}: {args.summary}", category=category)
    elif args.type == "architect_done":
        send_message(f"📐 {label}: Architect done — {args.summary}",
                     category=category)
    elif args.type == "developer_done":
        send_message(
            f"💻 {label}: Developer done (`{args.module}`) — {args.summary}",
            category=category,
        )
    elif args.type == "tester_done":
        send_message(f"🧪 {label}: Tests PASS — {args.summary}",
                     category=category)
    elif args.type == "reviewer_done":
        send_message(f"✔️ {label}: Code Review PASS — {args.summary}",
                     category=category)
    elif args.type in ("security_done", "security_reviewer_done"):
        send_message(f"🛡️ {label}: Security Review PASS — {args.summary}",
                     category=category)
    elif args.type == "documenter_done":
        send_message(f"📝 {label}: Documentation updated — {args.summary}",
                     category=category)
    elif args.type == "retrospective_done":
        send_message(f"🪞 {label}: Retrospective done — {args.summary}",
                     category=category)


if __name__ == "__main__":
    main()

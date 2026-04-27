#!/usr/bin/env python3
"""
.claude/hooks/notify-slack.py — AI Pipeline Slack notification hook

Optional. The pipeline is fully local; this hook is fire-and-forget for
events the team wants visibility on:
  - security_alert (CRITICAL / HIGH findings — always sent if Slack configured)
  - retry_limit (an agent exhausted its retries; task FAILED)
  - error (daemon-level fatal)
  - done (task completed — usually disabled in dev)
  - Optional per-agent transitions (architect_done, developer_done, etc.)

If PIPELINE_SLACK_BOT_TOKEN / PIPELINE_SLACK_CHANNEL are unset, the hook
prints to stderr and exits 0. Nothing in the pipeline depends on Slack
delivery succeeding.

Setup:
  Copy .claude/.env.example to .claude/.env, set the two Slack vars.
"""

import argparse
import json
import os
import sys
import requests
from pathlib import Path

# Load .env
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

SLACK_BOT_TOKEN = os.environ.get("PIPELINE_SLACK_BOT_TOKEN", "")
SLACK_CHANNEL   = os.environ.get("PIPELINE_SLACK_CHANNEL", "")


def send_message(text: str, blocks: list = None) -> dict:
    """Send a message to Slack."""
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL:
        print(f"[notify-slack] Token/Channel missing — printing to console:\n{text}")
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
            print(f"[notify-slack] Slack API error: {data.get('error')}", file=sys.stderr)
        return data
    except Exception as e:
        print(f"[notify-slack] Error: {e}", file=sys.stderr)
        return {}


def main():
    parser = argparse.ArgumentParser(description="AI Pipeline Slack notification")
    parser.add_argument(
        "--type",
        required=True,
        choices=[
            "phase_transition", "awaiting_info", "review_fail",
            "security_alert", "test_fail", "done",
            "retry_limit", "error", "info",
            "architect_done", "developer_done", "tester_done",
            "tester_failed", "reviewer_done", "reviewer_failed",
            "security_reviewer_done", "security_reviewer_failed",
            "retrospective_done", "documenter_done",
        ],
    )
    parser.add_argument("--issue",   default="", help="Task id (the .state/tasks/<id> identifier)")
    parser.add_argument("--summary", default="")
    parser.add_argument("--module",  default="", help="module name")
    parser.add_argument("--agent",   default="", help="agent name")
    args = parser.parse_args()

    label = args.issue if args.issue else "(no task id)"

    if args.type == "security_alert":
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🔴 SECURITY ALERT — {label}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Module:* `{args.module or '?'}`\n\n{args.summary}",
                },
            },
        ]
        send_message(f"🔴 SECURITY — {label}: {args.summary}", blocks)

    elif args.type == "retry_limit":
        send_message(f"⚠️ {label}: Retry limit exceeded — {args.summary}")

    elif args.type == "error":
        send_message(f"❌ {label}: Error — {args.summary}")

    elif args.type == "done":
        send_message(f"✅ {label}: Completed — {args.summary}")

    elif args.type == "phase_transition":
        send_message(f"➡️ {label}: {args.summary}")

    elif args.type == "info":
        send_message(f"ℹ️ {label}: {args.summary}")

    # Per-agent transition events (kept for opt-in verbosity)
    elif args.type == "architect_done":
        send_message(f"📐 {label}: Architect done — {args.summary}")
    elif args.type == "developer_done":
        send_message(f"💻 {label}: Developer done (`{args.module}`) — {args.summary}")
    elif args.type == "tester_done":
        send_message(f"🧪 {label}: Tests PASS — {args.summary}")
    elif args.type == "tester_failed":
        send_message(f"🧪 {label}: Test FAIL — {args.summary}")
    elif args.type == "reviewer_done":
        send_message(f"✅ {label}: Code Review PASS — {args.summary}")
    elif args.type == "reviewer_failed":
        send_message(f"🔄 {label}: Code Review FAIL — {args.summary}")
    elif args.type == "security_reviewer_done":
        send_message(f"🛡️ {label}: Security Review PASS — {args.summary}")
    elif args.type == "retrospective_done":
        send_message(f"🪞 {label}: Retrospective done — {args.summary}")
    elif args.type == "documenter_done":
        send_message(f"📝 {label}: Documentation updated — {args.summary}")


if __name__ == "__main__":
    main()

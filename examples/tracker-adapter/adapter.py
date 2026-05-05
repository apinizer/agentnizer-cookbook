"""GitHub Issues adapter for the AI pipeline daemon.

Reference implementation. Translates issues with a trigger label into
tasks in `.state/active.json`, then mirrors agent outputs back as issue
comments. Designed to be readable, not to win benchmarks.

Usage:
    python adapter.py --owner my-org --repo my-repo --dry-run
    python adapter.py --owner my-org --repo my-repo --read-only
    python adapter.py --owner my-org --repo my-repo

See the recipe README for the full mapping table.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests  # type: ignore
except ImportError:
    print("ERROR: pip install requests", file=sys.stderr)
    sys.exit(1)


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_JSON = REPO_ROOT / ".state" / "active.json"
TASKS_DIR = REPO_ROOT / ".state" / "tasks"

GITHUB_API = "https://api.github.com"
TRIGGER_LABEL_DEFAULT = "ai-pipeline"
DONE_LABEL = "ai-pipeline-done"


# ---- GitHub client (thin) ----------------------------------------------------

class GitHub:
    def __init__(self, token: str | None, dry_run: bool, read_only: bool):
        self.token = token
        self.dry_run = dry_run
        self.read_only = read_only
        self.session = requests.Session()
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.session.headers.update({"Accept": "application/vnd.github+json"})

    def list_issues(self, owner: str, repo: str, label: str) -> list[dict[str, Any]]:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/issues"
        params = {"labels": label, "state": "open", "per_page": 50}
        if self.dry_run:
            print(f"[adapter] Would query: GET {url} {params}")
            return []
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        # GitHub returns PRs in the issues endpoint too; filter them out.
        return [i for i in r.json() if "pull_request" not in i]

    def post_comment(self, owner: str, repo: str, number: int, body: str) -> None:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/comments"
        if self.dry_run or self.read_only:
            print(f"[adapter] Would POST comment on #{number} ({len(body)} chars)")
            return
        r = self.session.post(url, json={"body": body}, timeout=30)
        r.raise_for_status()

    def transition_done(self, owner: str, repo: str, number: int, label: str) -> None:
        if self.dry_run or self.read_only:
            print(f"[adapter] Would remove label '{label}', add '{DONE_LABEL}' on #{number}")
            return
        # Remove trigger label.
        self.session.delete(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/labels/{label}",
            timeout=30,
        )
        # Add done label.
        self.session.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/labels",
            json={"labels": [DONE_LABEL]},
            timeout=30,
        )


# ---- Mapping -----------------------------------------------------------------

def labels_to_fields(labels: list[dict[str, Any]]) -> dict[str, str]:
    """Pull module / risk / complexity from labels named like 'risk:HIGH'."""
    out: dict[str, str] = {}
    for lbl in labels:
        name = lbl["name"]
        if ":" not in name:
            continue
        key, _, value = name.partition(":")
        if key in {"module", "risk", "complexity"}:
            out[{"risk": "risk_level"}.get(key, key)] = value
    return out


def issue_to_task(issue: dict[str, Any]) -> dict[str, Any]:
    """Translate a GitHub issue payload to an active.json task entry."""
    fields = labels_to_fields(issue.get("labels", []))
    return {
        "id": f"gh-{issue['number']:06d}",
        "title": issue["title"],
        "description": issue.get("body") or "",
        "module": fields.get("module", "unknown"),
        "risk_level": fields.get("risk_level", "LOW"),
        "complexity": fields.get("complexity", "S"),
        "external_ref": {
            "tracker": "github",
            "owner": issue["repository_url"].split("/")[-2],
            "repo": issue["repository_url"].split("/")[-1],
            "number": issue["number"],
            "url": issue["html_url"],
        },
        "status": "queued",
    }


# ---- Sync paths --------------------------------------------------------------

def write_active_json(tasks: list[dict[str, Any]], dry_run: bool) -> None:
    if dry_run:
        print(f"[adapter] Would write {len(tasks)} entries to {ACTIVE_JSON}")
        return
    ACTIVE_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tasks": tasks}
    ACTIVE_JSON.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"[adapter] Wrote {len(tasks)} entries to {ACTIVE_JSON}")


def mirror_outputs(gh: GitHub, owner: str, repo: str, tasks: list[dict[str, Any]]) -> None:
    """For each task, post any new agent .md files as issue comments."""
    for task in tasks:
        ref = task.get("external_ref", {})
        if ref.get("tracker") != "github":
            continue
        task_dir = TASKS_DIR / task["id"]
        if not task_dir.exists():
            continue
        for md in sorted(task_dir.glob("*.md")):
            marker = task_dir / f".posted-{md.stem}"
            if marker.exists():
                continue
            body = f"**[{md.stem}]**\n\n{md.read_text()}"
            gh.post_comment(owner, repo, ref["number"], body)
            if not (gh.dry_run or gh.read_only):
                marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")


def transition_done_issues(gh: GitHub, owner: str, repo: str,
                           tasks: list[dict[str, Any]], label: str) -> None:
    """Issues whose meta.json reports status=done get the done-label transition."""
    for task in tasks:
        meta_path = TASKS_DIR / task["id"] / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("status") == "done":
            gh.transition_done(owner, repo, task["external_ref"]["number"], label)


# ---- Loop --------------------------------------------------------------------

def poll_once(gh: GitHub, owner: str, repo: str, label: str, max_tasks: int) -> None:
    issues = gh.list_issues(owner, repo, label)
    if not issues:
        print("[adapter] No open issues with trigger label.")
        return
    issues = issues[:max_tasks]
    tasks = [issue_to_task(i) for i in issues]
    write_active_json(tasks, gh.dry_run)
    mirror_outputs(gh, owner, repo, tasks)
    transition_done_issues(gh, owner, repo, tasks, label)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owner", required=True, help="GitHub repo owner / org")
    p.add_argument("--repo", required=True, help="GitHub repo name")
    p.add_argument("--label", default=TRIGGER_LABEL_DEFAULT,
                   help=f"Trigger label (default: {TRIGGER_LABEL_DEFAULT})")
    p.add_argument("--poll-interval", type=int, default=60,
                   help="Seconds between polls (default: 60)")
    p.add_argument("--max-tasks", type=int, default=3,
                   help="Max concurrent tasks fed into the daemon (default: 3)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print actions, do not call GitHub or write .state/")
    p.add_argument("--read-only", action="store_true",
                   help="Read GitHub, write .state/, but do NOT post comments back")
    p.add_argument("--once", action="store_true",
                   help="Poll once and exit (default: loop forever)")
    args = p.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token and not args.dry_run:
        print("ERROR: set GITHUB_TOKEN or pass --dry-run", file=sys.stderr)
        return 2

    gh = GitHub(token=token, dry_run=args.dry_run, read_only=args.read_only)

    if args.once:
        poll_once(gh, args.owner, args.repo, args.label, args.max_tasks)
        return 0

    print(f"[adapter] Starting poll loop. Interval: {args.poll_interval}s. "
          f"Label: {args.label}. Dry-run: {args.dry_run}. "
          f"Read-only: {args.read_only}.")
    while True:
        try:
            poll_once(gh, args.owner, args.repo, args.label, args.max_tasks)
        except Exception as exc:  # noqa: BLE001 - reference impl, broad on purpose
            print(f"[adapter] Poll failed: {exc}", file=sys.stderr)
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())

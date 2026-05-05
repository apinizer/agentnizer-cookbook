# How to swap GitHub Issues for Jira / Linear / GitLab

The reference adapter (`adapter.py`) is ~120 lines, of which roughly 40 are GitHub-specific. The rest — the mapping shape, the polling loop, the marker-file deduplication for posted comments — is generic.

Below is the shape of a swap. Each tracker section lists the **API client class to replace**, the **label-to-field mapping** that changes, and **gotchas** to watch for.

## Jira

### What changes

Replace the `GitHub` class with a `Jira` class:

```python
class Jira:
    def __init__(self, base_url, email, api_token, dry_run, read_only):
        self.base = base_url.rstrip("/")  # e.g. https://my-org.atlassian.net
        self.session = requests.Session()
        self.session.auth = (email, api_token)
        self.session.headers.update({"Accept": "application/json"})
        self.dry_run = dry_run
        self.read_only = read_only

    def list_issues(self, project_key, label):
        # Jira uses JQL.
        jql = f'project = "{project_key}" AND labels = "{label}" AND status != Done'
        url = f"{self.base}/rest/api/3/search"
        r = self.session.get(url, params={"jql": jql, "maxResults": 50})
        r.raise_for_status()
        return r.json()["issues"]

    def post_comment(self, issue_key, body_adf):
        # Jira API v3 expects Atlassian Document Format, not raw markdown.
        # The simplest portable form is paragraph + text; markdown won't render natively.
        url = f"{self.base}/rest/api/3/issue/{issue_key}/comment"
        body = {"body": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": body_adf}]}
        ]}}
        if self.dry_run or self.read_only:
            print(f"[adapter] Would post Jira comment on {issue_key}")
            return
        self.session.post(url, json=body).raise_for_status()

    def transition_done(self, issue_key, transition_id):
        url = f"{self.base}/rest/api/3/issue/{issue_key}/transitions"
        if self.dry_run or self.read_only:
            print(f"[adapter] Would transition {issue_key} -> {transition_id}")
            return
        self.session.post(url, json={"transition": {"id": transition_id}}).raise_for_status()
```

### Mapping changes

| Field | Jira source |
|---|---|
| `id` | `issue["key"]` (e.g. `PROJ-123`) — already a stable ID |
| `title` | `fields.summary` |
| `description` | `fields.description` (ADF, you'll want to flatten to text for the analyst) |
| `module` | a custom field — find its `customfield_XXXXX` ID via `/rest/api/3/field` |
| `risk_level` | another custom field, or a label parsed like the GitHub example |
| `complexity` | story points field (`customfield_10016` on most Jira Cloud setups) |

### Gotchas

- **ADF for comments.** Jira API v3 doesn't accept raw markdown; you'll either render markdown to ADF (the `mdx-to-adf` library exists) or post text-only comments. Cookbook recommendation: post text-only — it's lossy but readable.
- **Status transitions are workflow-dependent.** "Done" might be transition ID `31` in one project and `41` in another. Query `/rest/api/3/issue/{key}/transitions` once at startup and cache the IDs.
- **Rate limit:** Jira Cloud is generous (effectively unlimited for most setups) but Atlassian throttles burst writes. Batch comments rather than one-per-file.

## Linear

### What changes

Linear is a GraphQL API.

```python
class Linear:
    def __init__(self, api_key, dry_run, read_only):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": api_key})
        self.dry_run = dry_run
        self.read_only = read_only

    def list_issues(self, team_key, label):
        query = """
        query($team: String!, $label: String!) {
          issues(filter: {
            team: {key: {eq: $team}},
            labels: {name: {eq: $label}},
            state: {type: {nin: ["completed", "canceled"]}}
          }) {
            nodes { id identifier title description labels { nodes { name } } }
          }
        }
        """
        r = self.session.post("https://api.linear.app/graphql",
                              json={"query": query, "variables": {"team": team_key, "label": label}})
        r.raise_for_status()
        return r.json()["data"]["issues"]["nodes"]

    def post_comment(self, issue_id, body):
        mutation = """
        mutation($issueId: String!, $body: String!) {
          commentCreate(input: {issueId: $issueId, body: $body}) { success }
        }
        """
        if self.dry_run or self.read_only:
            print(f"[adapter] Would post Linear comment on {issue_id}")
            return
        self.session.post("https://api.linear.app/graphql",
                          json={"query": mutation,
                                "variables": {"issueId": issue_id, "body": body}})

    def transition_done(self, issue_id, state_id):
        mutation = """
        mutation($id: String!, $stateId: String!) {
          issueUpdate(id: $id, input: {stateId: $stateId}) { success }
        }
        """
        if self.dry_run or self.read_only:
            print(f"[adapter] Would transition Linear {issue_id} -> {state_id}")
            return
        self.session.post("https://api.linear.app/graphql",
                          json={"query": mutation,
                                "variables": {"id": issue_id, "stateId": state_id}})
```

### Mapping changes

| Field | Linear source |
|---|---|
| `id` | `identifier` (e.g. `ENG-42`) |
| `title` | `title` |
| `description` | `description` (markdown — much friendlier than Jira's ADF) |
| `module` | parse from labels (Linear allows multi-label, same `module:backend` convention works) |

### Gotchas

- **GraphQL means no resource URLs.** Cache state IDs at startup (`workflowStates` query) — there's no `/transitions` shortcut.
- **Markdown native.** Comments render markdown directly. This is the easiest tracker to get nice-looking output on.
- **Workspace/team scoping.** A Linear API key is workspace-scoped, but you usually want team-scoped polling. Pass the team key.

## GitLab Issues

### What changes

GitLab REST API is closest to GitHub, so the diff is small.

```python
class GitLab:
    def __init__(self, base_url, token, dry_run, read_only):
        self.base = base_url.rstrip("/")  # e.g. https://gitlab.com
        self.session = requests.Session()
        self.session.headers.update({"PRIVATE-TOKEN": token})
        self.dry_run = dry_run
        self.read_only = read_only

    def list_issues(self, project_id, label):
        url = f"{self.base}/api/v4/projects/{project_id}/issues"
        r = self.session.get(url, params={"labels": label, "state": "opened", "per_page": 50})
        r.raise_for_status()
        return r.json()

    def post_comment(self, project_id, iid, body):
        url = f"{self.base}/api/v4/projects/{project_id}/issues/{iid}/notes"
        if self.dry_run or self.read_only:
            print(f"[adapter] Would post GitLab note on issue {iid}")
            return
        self.session.post(url, json={"body": body}).raise_for_status()

    def transition_done(self, project_id, iid):
        # GitLab "close": use state_event=close.
        url = f"{self.base}/api/v4/projects/{project_id}/issues/{iid}"
        if self.dry_run or self.read_only:
            print(f"[adapter] Would close GitLab issue {iid}")
            return
        self.session.put(url, json={"state_event": "close"}).raise_for_status()
```

### Mapping changes

| Field | GitLab source |
|---|---|
| `id` | f"gl-{project_id}-{iid:06d}" |
| `title` | `title` |
| `description` | `description` (markdown) |
| `module` / `risk_level` / `complexity` | parse from `labels` like the GitHub example (`/labels` returns `["module:backend", "risk:HIGH"]`) |

### Gotchas

- **Project ID vs path.** Most APIs accept either `123` (numeric ID) or `org%2Frepo` (URL-encoded path). Use the numeric ID for clarity in scripts.
- **Self-hosted vs gitlab.com.** Same client class, different `base_url`. Pass it as a CLI flag.
- **Issue closing transitions the state, not a label.** The cookbook's GitHub example uses `ai-pipeline-done` label; on GitLab you can either close the issue or use scoped labels. Pick one and stick to it; mixing causes confusion.

## Generic refactor (if you need to support multiple trackers)

Once you have two trackers, hide the differences behind a `TrackerAdapter` ABC:

```python
class TrackerAdapter(ABC):
    @abstractmethod
    def list_open_tasks(self) -> list[dict]: ...
    @abstractmethod
    def post_output(self, task_id: str, role: str, body: str) -> None: ...
    @abstractmethod
    def mark_done(self, task_id: str) -> None: ...
```

Then `adapter.py` becomes:

```python
adapter: TrackerAdapter = build_adapter_from_args(args)
while True:
    tasks = adapter.list_open_tasks()
    write_active_json(tasks)
    mirror_outputs(adapter, tasks)
    mark_done_tasks(adapter, tasks)
    time.sleep(args.poll_interval)
```

This is the right shape **once**; doing it before the second tracker is overengineering.

## When to NOT use a tracker adapter

- **Solo dogfooding.** `.state/active.json` is faster to inspect than any tracker UI for one user.
- **Prototyping a new agent.** The tracker round-trip adds latency; iterate on `.state/` until the agent is stable, then wire the adapter.
- **Air-gapped / offline work.** No tracker = no network = no adapter.

The adapter exists to **align AI work with where humans already coordinate**. If your team isn't on a tracker, don't introduce one just to run the pipeline.

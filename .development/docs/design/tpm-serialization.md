# Implementation Plan: TPM Agent State Persistence + Auto-Deploy

## Files to change

### 1. `src/agent006/storage/sqlite.py`
Add three methods to `SQLiteStorageManager`:
- `get_latest_snapshot_id() -> str | None`
  Query: `SELECT snapshot_id FROM snapshots ORDER BY created_at DESC LIMIT 1`
- `get_latest_snapshot_created_at() -> datetime | None`
  Query: `SELECT created_at FROM snapshots ORDER BY created_at DESC LIMIT 1`
  Return parsed UTC-aware datetime.
- `restore_latest_snapshot(agent: Agent) -> bool`
  Convenience: get latest id, call `restore_snapshot`, return True if found, False if none.

### 2. `agents/tpm-agent/tpm_agent.py`

**Imports to add:**
```python
from typing import Annotated
from agent006.storage.markers import nosnapshot
```

**Class-level field annotations** — replace bare annotations with `nosnapshot`:
```python
slack: Annotated[SlackTool, nosnapshot]
gitlab: Annotated[GitLabTool, nosnapshot]
teams: Annotated[TeamsTool | None, nosnapshot]
_digest_create_lock: Annotated[asyncio.Lock, nosnapshot]
```
(`_state_file` is removed entirely — no annotation needed.)

**`__init__` changes:**
- Remove `state_file: str | None = None` parameter
- Remove `self._state_file = ...` lines
- Replace `self.state: dict[str, Any] = self._load_state()` with `self.state: dict[str, Any] = {}`
- Move `setdefault` calls into a new `_apply_state_defaults()` helper (called from `__init__`)
- `storage=` kwarg passes through `**kwargs` to `Agent.__init__` — no change needed

**Extract `_apply_state_defaults()` method:**
```python
def _apply_state_defaults(self) -> None:
    self.state.setdefault("notifications_push", True)
    self.state.setdefault("notifications_mr", True)
    self.state.setdefault("notifications_pipeline_failed", True)
    self.state.setdefault("notifications_teams_transcript", True)
```
This is called in `__init__` AND by the runner after `restore_latest_snapshot` to handle
schema evolution (new keys added after a snapshot was taken).

**Remove methods:**
- `_load_state()`
- `_save_state()`

**Remove all `self._save_state()` call sites** (in `_add_daily_event` and `_get_or_create_daily_digest`).

**Add `reconcile_since` method:**
```python
async def reconcile_since(self, since: datetime.datetime) -> None:
```
For each `project_id` in `self.project_ids`:
1. Call `await self.gitlab.get_project(project_id)` → get `project_name = result["path_with_namespace"]`
2. Fetch commits: `await self.gitlab.get_commits(project_id, since=since, ref_name="main")`
   - Call `handle_gitlab_push(project_name, "refs/heads/main", commits, pusher=commits[0].get("author_name", "reconcile"))` if any commits found
3. Fetch MRs: `await self.gitlab.get_merge_requests(project_id, since=since, state="all")`
   - For each MR, call `handle_gitlab_mr(...)` with action=MR state, state=MR state
4. Pipelines: intentionally omitted — GitLabTool has no `get_pipelines` method and pipeline
   status is ephemeral (no value in replaying)

After replaying events, stamp the daily digest footer:
```python
import datetime as _dt
date_key = _dt.date.today().isoformat()
ts = await self._get_or_create_daily_digest(date_key)
restart_time = _dt.datetime.now().strftime("%H:%M")
commit_hash = os.getenv("GIT_COMMIT", "unknown")
text = self._render_daily_digest_text(date_key)
text += f"\n🔄 Last restarted {restart_time} · `{commit_hash}`"
await self.slack.update_message(self.channel_id, ts, text)
```

Wrap entire method in try/except — errors during reconcile must not crash the agent on boot.

### 3. `agents/tpm-agent/runner.py`

**`__init__` changes:**
- Replace `state_file: str | None = None` parameter with `db_file: str = "agent_state.db"`
- Replace `self.state_file = state_file` with `self.db_file = db_file`
- Add `self._storage: SQLiteStorageManager | None = None` (import from agent006.storage)

**Make `create_agent` async:**
```python
async def create_agent(self) -> TPMAgent:
    from agent006.storage import SQLiteStorageManager
    self._storage = SQLiteStorageManager(self.db_file)
    agent = TPMAgent(
        channel_id=self.channel_id,
        project_ids=self.project_ids,
        slack_token=self.slack_bot_token,
        storage=self._storage,
    )
    restored = self._storage.restore_latest_snapshot(agent)
    if restored:
        agent._apply_state_defaults()  # re-apply for schema evolution
        since = self._storage.get_latest_snapshot_created_at()
        if since:
            await agent.reconcile_since(since)
    return agent
```

**`run()` change:**
- `self.agent = self.create_agent()` → `self.agent = await self.create_agent()`

**`shutdown` changes:**
- First action: save snapshot before stopping anything
  ```python
  if self.agent and self._storage:
      try:
          self._storage.save_snapshot(self.agent)
          logger.info("Snapshot saved successfully")
      except Exception as e:
          logger.error(f"Failed to save snapshot: {e}", exc_info=True)
  ```
- After all cleanup: `if self._storage: self._storage.close()`

**`runner.py::main()` changes:**
- Replace `state_file = os.getenv("STATE_FILE", "agent_state.json")` with
  `db_file = os.getenv("AGENT_DB_FILE", "agent_state.db")`
- Replace `state_file=state_file` in `TPMAgentRunner(...)` with `db_file=db_file`

### 4. `agents/tpm-agent/start_tpm_agent.py`
- Replace `state_file=os.getenv("TPM_STATE_FILE", "tpm_agent_state.json")` with
  `db_file=os.getenv("AGENT_DB_FILE", "agent_state.db")`
- Remove the dead `trace_file="traces"` kwarg (not a TPMAgentRunner param → would TypeError)

### 5. `deploy/docker-compose.yml`
- Replace `- STATE_FILE=/app/data/agent_state.json` with `- AGENT_DB_FILE=/app/data/agent_state.db`
- Add `- GIT_COMMIT=${CI_COMMIT_SHORT_SHA:-unknown}` for restart stamp

### 6. `.gitlab-ci.yml`
For both `build-docker-images` and `deploy-staging`:
```yaml
rules:
  - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
    when: always   # was: manual
  - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    when: manual
```

## Tests to update

`agents/tpm-agent/tests/test_import_smoke.py`:
- `test_tpm_agent_class_creation`: no kwarg changes needed (no `state_file` was passed)
- `test_runner_class_creation`: no change needed (`db_file` has a default)

Add basic SQLiteStorageManager tests in `tests/test_sqlite_storage.py`:
- `restore_latest_snapshot` returns False on empty DB
- `restore_latest_snapshot` returns True after a save_snapshot

## Edge cases
- `_digest_create_lock` excluded from snapshot via class-level `Annotated[asyncio.Lock, nosnapshot]`
- Schema evolution: `_apply_state_defaults()` is called both in `__init__` (for first boot)
  and after `restore_latest_snapshot` (for upgrades where new keys were added post-snapshot)
- `reconcile_since` is fully wrapped in try/except to prevent boot failures on GitLab API errors
- Commit deduplication: `handle_gitlab_push` already deduplicates via `seen_commit_shas` in state
- `GIT_COMMIT` env var injected via docker-compose from `CI_COMMIT_SHORT_SHA`

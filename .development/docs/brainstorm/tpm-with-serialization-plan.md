# Plan: TPM Agent State Persistence + Auto-Deploy on Every Commit

## Background
The TPM agent currently loses its memory on every deployment because there is no
reliable way to persist its state across container restarts. Severin had to switch
the CI/CD pipeline to manual deploy (MR 482) to protect the agent's running context.

Matt's MR introduced the framework-level serialization primitives:
AgentSnapshot, SQLiteStorageManager, StorageManager protocol, nosnapshot marker.
The goal is to wire these into the TPM agent and re-enable auto-deploy on every
push to main, while preserving agent memory across restarts.

A secondary goal is to exercise the new persistence framework in production — the
TPM agent is our continuous integration target for nemo_oo_agents, so using it here gives
us real confidence in the serialization stack.

## Proposed Approach

### 1. Extend SQLiteStorageManager with "restore latest" support
Add three methods to SQLiteStorageManager:
- `get_latest_snapshot_id() -> str | None`
- `get_latest_snapshot_created_at() -> datetime | None`
- `restore_latest_snapshot(agent) -> bool` (returns True if a snapshot was restored,
  False if no snapshots exist — clean first boot)

The query is: `SELECT snapshot_id, created_at FROM snapshots ORDER BY created_at DESC LIMIT 1`

### 2. Wire TPM Agent to SQLiteStorageManager — remove all bespoke persistence
- Annotate non-serializable fields with nosnapshot:
  `slack`, `gitlab`, `teams`, `_digest_create_lock`, `_state_file`
- Pass `SQLiteStorageManager(db_path)` into the agent via the `storage=` kwarg
  already supported by `Agent.__init__`
- Remove the hand-rolled `_load_state()`, `_save_state()`, `_state_file` entirely.
  The self.state dict is a plain JSON-serializable dict and will be captured cleanly
  by AgentSnapshot.attributes — no data is lost.

### 3. Save snapshot on shutdown
In `TPMAgentRunner.shutdown()`, call `self._storage.save_snapshot(self.agent)` as
the first action before stopping anything else. Signal handlers (SIGTERM, SIGINT)
already flow through runner.shutdown() — no new signal wiring needed.

### 4. Restore snapshot on boot + GitLab catch-up reconciliation
In `TPMAgentRunner.create_agent()`, immediately after constructing the agent:
1. Call `storage.restore_latest_snapshot(agent)` — no-op (returns False) on first boot
2. If restored (True), read `snapshot_created_at` and call:
   `await agent.reconcile_since(since=snapshot_created_at)`

`reconcile_since(since: datetime)` is a new TPMAgent method that:
- Queries the GitLab API for commits, MR updates, and pipeline runs since `since`
- Replays them through the existing `handle_gitlab_push` / `handle_gitlab_mr` /
  `handle_gitlab_pipeline` handlers so the weekly push log and daily digest stay
  accurate across the downtime window
- Edits today's daily digest thread to append a footer line:
  "🔄 Last restarted HH:MM · `<commit-hash>`"
  Silent on first boot (no prior snapshot). This keeps restart visibility in the
  daily thread without generating noise, and provides a debug anchor.

### 5. Docker Compose + volume config
- Replace `STATE_FILE=/app/data/agent_state.json` env var with
  `AGENT_DB_FILE=/app/data/agent_state.db` in `deploy/docker-compose.yml`
- The existing named volume `agent-state:/app/data` already survives
  `docker compose up -d` restarts — no volume changes needed

### 6. Enable auto-deploy in CI
In `.gitlab-ci.yml`, change the rules for `build-docker-images` and `deploy-staging`
on the main branch from `when: manual` to `when: always`.
The deploy script already does `docker compose pull && docker compose up -d` which
preserves named volumes, so state is not lost during redeploy.

## Detailed Tasks
- [ ] `SQLiteStorageManager`: add `get_latest_snapshot_id()`,
      `get_latest_snapshot_created_at()`, and `restore_latest_snapshot(agent) -> bool`
- [ ] `TPMAgent`: annotate non-serializable fields with `nosnapshot`
      (slack, gitlab, teams, _digest_create_lock, _state_file)
- [ ] `TPMAgent`: remove `_load_state()`, `_save_state()`, `_state_file` entirely
- [ ] `TPMAgent.__init__`: remove `state_file` param; accept storage via Agent base class
- [ ] `TPMAgent`: add `async def reconcile_since(self, since: datetime)` — queries
      GitLab API for commits/MRs/pipelines since given timestamp, replays through
      existing handlers, edits today's daily digest footer with restart stamp
- [ ] `TPMAgentRunner.__init__`: create `SQLiteStorageManager(db_path)`, store as
      `self._storage`; close it in `shutdown()`
- [ ] `TPMAgentRunner.shutdown()`: call `self._storage.save_snapshot(self.agent)`
      as first action
- [ ] `TPMAgentRunner.create_agent()`: call `restore_latest_snapshot` +
      `reconcile_since` after constructing agent
- [ ] `start_tpm_agent.py`: replace `state_file=` kwarg with `db_file=` wired
      from `AGENT_DB_FILE` env var
- [ ] `deploy/docker-compose.yml`: replace STATE_FILE with AGENT_DB_FILE env var
- [ ] `.gitlab-ci.yml`: flip `build-docker-images` and `deploy-staging` main-branch
      rules from `when: manual` to `when: always`

## Open Questions
- Should snapshot rows be pruned after N deploys to keep the DB small? Low priority —
  one row per deploy is negligible — but worth a follow-up task.

## Out of Scope
- Migration of existing hand-rolled state JSON files to SQLite
  (first boot is a clean start — acceptable one-time reset)
- Multi-replica deployments (SQLiteStorageManager is single-writer by design)
- Snapshot signing / tamper detection
- Trace viewer state persistence (separate concern)

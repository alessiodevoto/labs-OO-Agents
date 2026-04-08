# Slack Bridge: Agent Save/Resume Design

**Context**: When the bridge auto-updates (or restarts for any reason), running
agents lose their Claude Code session context. This doc brainstorms how to
save and restore that context.

---

## What needs saving

Each `AgentHandle` has:
- `agent_id`, `branch`, `worktree_path` — fully reconstructable
- `thread_ts`, `status_msg_ts` — Slack thread anchors, must be preserved
- `username`, `icon_emoji` — identity, can be re-derived from agent_id
- `session_id` — the Claude Code session ID from the `system/init` stream event

The critical piece is **`session_id`**: Claude Code supports
`claude --resume <session-id>` to continue a previous conversation.

---

## How Claude Code session resumption works

```
claude --output-format stream-json \
       --input-format stream-json \
       --resume <session-id> \
       --verbose
```

- Claude Code loads the previous conversation from its local cache
  (`~/.claude/projects/<project-hash>/`)
- The resumed process continues exactly where the conversation left off
- The agent still works in the same `cwd` (worktree), so file state is intact

The `session_id` is emitted in the very first line of stdout:

```json
{"type": "system", "subtype": "init", "session_id": "abc-def-123", ...}
```

---

## Proposed implementation

### 1. Capture `session_id` in `AgentHandle`

`OutputParser` already logs the session_id on `system/init`. The bridge's
`read_agent_output` loop needs to pass it back to the handle:

```python
# output_parser.py: emit a new event kind
if subtype == "init":
    return ParsedEvent(kind="session_init", session_id=data.get("session_id", ""))

# bridge.py: read_agent_output
if event.kind == "session_init":
    handle.session_id = event.session_id
    continue
```

Add `session_id: str = ""` field to `AgentHandle`.

### 2. State file: `{repo_root}/.bridge-state.json`

Written atomically (write to `.bridge-state.json.tmp`, then rename) before
any restart so it survives crashes:

```json
{
  "version": 1,
  "saved_at": "2026-03-02T10:30:00Z",
  "agents": {
    "myagent": {
      "agent_id": "myagent",
      "branch": "agent/myagent",
      "worktree_path": "/repo/.worktrees/myagent",
      "thread_ts": "1740912345.123456",
      "status_msg_ts": "1740912300.000000",
      "username": "myagent",
      "icon_emoji": ":owl:",
      "session_id": "abc-def-123-456"
    }
  }
}
```

Written in `auto_update_loop` just before `os.execv`:

```python
await _save_bridge_state(repo_root)
os.execv(sys.executable, [sys.executable] + sys.argv)
```

### 3. Resume on startup

In `main()`, after creating the registry, check for a saved state file:

```python
saved = _load_bridge_state(repo_root)
if saved:
    for agent_data in saved["agents"].values():
        await resume_agent(agent_data, client)
    Path(state_file).unlink()  # consumed
```

`resume_agent` creates a new `AgentHandle` with `--resume <session_id>` in
the claude command, posts a "🔄 Resumed after bridge update" message to the
thread, and starts `read_agent_output`.

### 4. Changes to `AgentHandle.start()`

Add an optional `resume_session_id` parameter:

```python
async def start(self, initial_prompt: str, resume_session_id: str = "") -> None:
    cmd = ["claude", "--output-format", "stream-json",
           "--input-format", "stream-json", "--verbose"]
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])
    ...
    if not resume_session_id:
        await self.send_message(initial_prompt)
    # (resumed session doesn't need a new initial message)
```

---

## Edge cases to handle

| Scenario | Handling |
|---|---|
| `session_id` unknown (agent died before init) | Skip resume, post "agent lost — please re-prompt" |
| Claude session cache expired / missing | Claude will start fresh; post a warning |
| Worktree deleted | Detect missing path, post error to thread |
| Bridge restarts without update (crash) | State file still exists → resume on startup |
| Multiple restarts in quick succession | State file is deleted after first successful resume |

---

## State file location options

| Location | Pros | Cons |
|---|---|---|
| `{repo_root}/.bridge-state.json` | Simple, co-located with repo | Pollutes repo root |
| `{repo_root}/.claude/bridge-state.json` | Inside existing .claude dir | Slightly hidden |
| `~/.config/claude-bridge/state.json` | XDG-compliant, separate | Harder to find |

Recommendation: `{repo_root}/.claude/bridge-state.json` — already gitignored
(`.claude/` is typically in `.gitignore`), and co-located with other Claude config.

---

## What it looks like in Slack

**Before restart:**
> 🔄 *Bridge update available* (`abc1234` → `def5678`)
> Will apply automatically once all agents are idle.

*(agents finish their tasks)*

> ✅ All agents idle — applying bridge update and restarting…

**After restart (in each agent's thread):**
> 🔄 *Resumed after bridge update* — session `abc-def` continuing

---

## Effort estimate

| Component | Effort |
|---|---|
| Capture `session_id` from stream | ~20 lines |
| State file save/load | ~50 lines |
| `AgentHandle.start()` resume mode | ~10 lines |
| Resume on startup in `main()` | ~30 lines |
| Tests | ~60 lines |
| **Total** | **~170 lines** |

This is small enough to do as a single PR after the current one merges.

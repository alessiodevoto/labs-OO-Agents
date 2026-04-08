# Port Nexus Example to Event-Middleware Branch

## Goal

Port `examples/quickstart/13_nexus.py` from `origin/feat/nat-nexus-integration` to the current `feat/event-middleware-alt` branch, adapting it to use the middleware-based Nexus integration (`nexus_middleware.py`) instead of the hooks-based one (`_nat_nexus.py` + `nexus/__init__.py`).

## Key Differences Between Branches

| Aspect | Old (`feat/nat-nexus-integration`) | New (`feat/event-middleware-alt`) |
|--------|-------------------------------------|-----------------------------------|
| Integration module | `agent006._nat_nexus` + `agent006.nexus` | `agent006.nexus_middleware` |
| Scope activation | `set_hooks(NexusHooks())` + global `nexus_scope(name)` | Per-agent `nexus_scope(agent, name)` |
| LLM routing | `llm_call_hook_var` contextvar in unifiedllm | `event_manager.intercept("llm_call", ...)` |
| Tool routing | `_nat_nexus.tool_execute()` called from actor.py | `event_manager.intercept("execute_python", ...)` |
| Agent scoping | `NexusHooks.before_agent_call()` pushes Function scope | `agent_call` middleware kind (or manual scope push) |

## Changes Required

### 1. Add `nexus` dependency to `pyproject.toml`
- Add `nvidia-nat-nexus` git dependency
- Add `nexus` optional extra

### 2. Create `examples/quickstart/13_nexus.py`

Port the example with these adaptations:

- **Imports**: Replace `from agent006.nexus import NexusHooks, nexus_scope` and `from agent006.runtime.hooks import set_hooks` with `from agent006.nexus_middleware import nexus_scope`
- **No `set_hooks()`**: The middleware branch doesn't need global hooks — middleware is installed per-agent by `nexus_scope(agent, name)`
- **Agent creation before scope**: Since `nexus_scope()` takes an agent instance, create the agent before entering the scope
- **Multiple agents**: The example uses `ResearchAgent` and `ReturnTypeDemoAgent`. Each needs its own `nexus_scope()` call (or share middleware via `install_nexus()`)
- **`agent_call` middleware**: The new branch has `agent_call` middleware kind for wrapping method calls with Nexus scopes. Need to check if `nexus_middleware.py` already handles this or if we need to add it.

### 3. Handle `agent_call` scoping

The old branch used `NexusHooks.before_agent_call()` to push `ScopeType.Function` scopes for each method call. The new `nexus_middleware.py` has `agent_call`, `llm_call`, and `execute_python` middleware — `nexus_agent_call_middleware` pushes/pops Function scopes and is installed via `install_nexus()`.

**Options:**
- A) Add a `nexus_agent_call_middleware` to `nexus_middleware.py` that pushes/pops Function scopes
- B) Keep the example simpler — just root Agent scope, no per-method Function scopes

**Decision**: Option A — add agent_call middleware to `nexus_middleware.py` and install it in `install_nexus()`. This gives the same ATIF granularity as the old branch.

### 4. Run and validate

- Install nexus extra: `uv sync --extra nexus`
- Run: `uv run python examples/quickstart/13_nexus.py`
- Check ATIF trajectory files for:
  - No placeholder/wrong data
  - Correct agent name, model name
  - Steps with actual LLM inputs/outputs
  - Tool executions with code and results
  - Proper scope hierarchy (if supported by ATIF version)

## Files to Touch

1. `pyproject.toml` — add nexus dependency and extra
2. `src/agent006/nexus_middleware.py` — add `agent_call` middleware
3. `examples/quickstart/13_nexus.py` — new file (ported example)

## Edge Cases

- `nat_nexus` not installed: example should fail with clear error (already handled by `nexus_middleware.py`)
- Multiple agents sharing Nexus scope: each agent gets own middleware via separate `nexus_scope()` calls
- Nested generation (summarize → fact_check): must work through middleware chain

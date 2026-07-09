# NeMo Flow Integration Design

## Overview

NeMo Flow is a multi-language agent runtime providing:
- **Execution scope management**: hierarchical scopes (Agent, Task, Function) with UUID tracking
- **LLM middleware pipeline**: conditional guardrails → request intercepts → execution intercepts → response guardrails
- **Tool middleware pipeline**: same pipeline for tool calls
- **Event subscribers**: observability, ATIF trajectory export
- **Language bindings**: Rust core with Python, Go, Node.js, WASM

This design describes the integration of NeMo Flow into NeMo OO Agents.

---

## Architecture

### Integration via Middleware API

The integration uses NeMo OO Agents' `event_manager.intercept()` middleware API
to install three middleware functions that route operations through NeMo Flow pipelines:

```
User code
│
│  async with nemo_flow_scope(agent, "my-agent") as handle:
│      result = await agent.my_method(...)
│
└─► nemo_flow_middleware.py
    ├── nemo_flow_agent_call_middleware  →  Push/pop NeMo Flow Function scope per method
    ├── nemo_flow_llm_middleware         →  Route LLM calls through NeMo Flow LLM pipeline
    └── nemo_flow_tool_middleware        →  Route code execution through NeMo Flow tool pipeline
```

### Key Design Decisions

1. **Middleware-based, not hook-based**: Uses `event_manager.intercept()` rather
   than modifying core framework files (unifiedllm, actor.py, codeact.py). All
   NeMo Flow logic is contained in a single module (`nemo_flow_middleware.py`).

2. **Fully optional**: `nemo_flow` is behind a `try/except ImportError` guard.
   When not installed, `install_nemo_flow()` and `nemo_flow_scope()` raise `ImportError`
   with install instructions. Zero behavior change when NeMo Flow is not present.

3. **Scope management via agent_call middleware**: Each agent method call gets
   a NeMo Flow `ScopeType.Function` scope named `"ClassName.method_name"`, pushed
   before execution and popped after (even on exception).

4. **JSON serialization at pipeline boundaries**: The NeMo Flow Rust core requires
   JSON-serializable data. The integration handles this by:
   - **LLM input**: Stripping sensitive keys and non-serializable objects (tools,
     output_model) from params, using messages directly
   - **LLM output**: `raw_response.model_dump(mode="json")` on the litellm
     `ModelResponse` (Pydantic)
   - **Tool output**: Extracting meaningful return values (`returned_value` or
     `signal.result`) and serializing via `BestEffortAnyCodec.to_json()`

5. **Request intercept propagation**: When NeMo Flow request intercepts modify the
   LLM request (e.g., inject system messages, change temperature), those changes
   are propagated back to the middleware context so the actual LLM call sees them.

---

## `nemo_flow_middleware.py` — Single Module Design

### LLM Middleware (`nemo_flow_llm_middleware`)

Wraps LLM calls through `nemo_flow.llm.execute()`:

```python
async def nemo_flow_llm_middleware(ctx: LLMCallContext, nxt: LLMCallNext) -> LLMCallContext:
    # 1. Build LLMRequest from ctx.messages + safe params
    # 2. Define _wrapper that calls nxt(ctx) and returns JSON for NeMo Flow
    # 3. Call nemo_flow.llm.execute(model_name, request, _wrapper)
    # 4. Return the captured context (original LLMResponse preserved)
```

### Tool Middleware (`nemo_flow_tool_middleware`)

Wraps code execution through `nemo_flow.tools.execute()`:

```python
async def nemo_flow_tool_middleware(ctx: ExecutePythonContext, nxt: ExecutePythonNext) -> ExecutePythonContext:
    # 1. Build args dict from ctx.code + params
    # 2. Define _wrapper that calls nxt(ctx) and extracts return value
    # 3. Call nemo_flow.tools.execute("execute_python", args, _wrapper)
    # 4. Return the captured context (original ExecutionResult preserved)
```

Return value extraction priority:
1. `result.returned_value` (direct return)
2. `result.signal.result["result"]` (return_result() signal)
3. `result.stdout` (print output fallback)

### Agent Call Middleware (`nemo_flow_agent_call_middleware`)

Wraps each agent method in a Function scope:

```python
async def nemo_flow_agent_call_middleware(ctx: AgentCallContext, nxt: AgentCallNext) -> AgentCallContext:
    handle = nemo_flow.scope.push(f"{ClassName}.{method_name}", ScopeType.Function)
    try:
        return await nxt(ctx)
    finally:
        nemo_flow.scope.pop(handle)
```

### Public API

- `install_nemo_flow(event_manager)` → returns `uninstall()` callable
- `nemo_flow_scope(agent, scope_name)` → async context manager that pushes root
  Agent scope, installs middleware, and cleans up on exit

---

## Scope Hierarchy (ATIF)

```
Agent scope ("my-agent")              ← nemo_flow_scope()
├── Function scope ("Agent.method1")  ← nemo_flow_agent_call_middleware
│   ├── LLM call                      ← nemo_flow_llm_middleware
│   └── Tool call (execute_python)    ← nemo_flow_tool_middleware
└── Function scope ("Agent.method2")
    ├── LLM call
    └── Tool call
```

---

## Files

| File | Purpose |
|------|---------|
| `src/nooa/nemo_flow_middleware.py` | All middleware + public API |
| `tests/test_nemo_flow_middleware.py` | Integration tests (requires `nemo_flow`) |
| `examples/quickstart/13_nemo_flow.py` | Full quickstart example |

---

## Constraints

- `nemo_flow` is an **optional** dependency — all code guarded by `_HAS_NEMO_FLOW`.
- No behavior change when NeMo Flow is not installed.
- Sensitive keys (`api_key`, `api_base`, `base_url`) stripped before NeMo Flow sees them.
- Non-serializable objects (`tools`, `output_model`) excluded from LLM requests
  sent to NeMo Flow to avoid `AttributeError` in the native pipeline.

---

## Known Limitations

### Cross-language serialization boundary

The NeMo Flow Rust core requires JSON-serializable values at every pipeline
boundary. NeMo OO Agents' internal types (`ExecutionResult`, `LLMResponse`)
contain non-serializable fields (`Callable`, `Exception`).

**Resolution (implemented)**: The middleware extracts serializable data at
each boundary (see "JSON serialization at pipeline boundaries" above).

**Guidance for users**: Define return types as Pydantic models for full
visibility in NeMo Flow guardrails and ATIF.

### ATIF export flattens scope hierarchy

`AtifExporter.export()` serializes events into a flat structure. While
individual events carry `parent_uuid` for real-time subscribers, the
exported trajectory does not preserve the nested scope tree. Multi-level
agent nesting is unreadable in exported ATIF.

### ATIF tool messages show status, not output

The ATIF exporter maps tool results to `"status: complete"` / `"status:
error"` instead of the actual return value. The real output is captured by
the event system and visible to real-time subscribers, but not in ATIF
exports.

---

## Future Work

- Streaming LLM support — `nemo_flow.llm.stream_execute()` is available
  but unused (requires NeMo OO Agents strategies to expose streaming)
- Per-turn scope granularity (e.g., `method_name.turn-N` scopes in CodeAct)

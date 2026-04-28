# Benchmark Token & Trajectory Tracking

## Context

This MR (`feat/token-tracking`) adds per-task token counts (`n_input_tokens`,
`n_output_tokens`) to each Harbor trial's `agent/result.json`.  The
implementation is intentionally minimal: a `ContextVar`-based accumulator in
`token_usage.py` fed by a single new entry in `actor.py`'s unifiedllm metrics
bridge, with three lines of integration in `runner.py`.

This doc records the broader design exploration triggered by that work — what
the right long-term shape is if trajectory capture for RL/SFT becomes a
requirement — and why that doesn't change the immediate MR.

---

## Why not NeMo Flow for token counting?

`nemo_flow_middleware.py` (already on `main`) can also produce token counts as
a side effect of its ATIF trajectory export, via an `LLMEndEvent` subscriber.
We chose not to use it here for three reasons:

**1. It ignores the event system this MR uses.**  
unifiedllm already fires a `"token_usage"` event after every LLM call carrying
`{"prompt_tokens": ..., "completion_tokens": ...}`.  The NeMo Flow LLM
middleware ignores that event entirely and instead routes the full LLM call
through `nemo_flow.llm.execute()`, which requires JSON-serializing
`LLMResponse` through a Rust core.  That forces a three-path serialization
fallback:

```python
raw = getattr(resp, "raw_response", None)
if raw is not None and hasattr(raw, "model_dump"):
    return raw.model_dump(mode="json")   # litellm ModelResponse
if hasattr(resp, "model_dump"):
    return resp.model_dump(mode="json")  # Pydantic
if hasattr(resp, "assistant_message"):   # unifiedllm dataclass
    ...
```

This couples the integration to internal shapes of both `unifiedllm` and
`litellm`.  MR!124 reads the same usage dict directly from the event — no
coupling, no serialization.

**2. It has a private NVIDIA-registry dependency.**  
`nemo_flow` is an optional extra requiring internal GitLab registry
credentials.  Token counting shouldn't require auth to an internal package
registry.

**3. The trajectory it produces has known gaps.**  
The NeMo Flow ATIF export excludes `tools` (non-JSON-serializable at the Rust
boundary), maps tool outputs to `"status: complete"` rather than actual return
values, and flattens the scope hierarchy.  This makes it unsuitable as training
data without additional post-processing.

---

## The evolution path: trajectory middleware

If RL/SFT trajectory capture becomes a requirement, the right direction is a
**trajectory middleware** that uses the same `ContextVar` + `intercept()` pattern
as this MR, but collects full message content rather than just counts.

The `event_manager.intercept(MIDDLEWARE_LLM_CALL, ...)` API gives access to
`LLMCallContext` with `ctx.messages` (full input) and `ctx.response` (full
output including usage).  A lightweight middleware at that layer can accumulate
a trajectory without routing through an external runtime:

```python
# trajectory.py  (~80 lines, no external deps)

_trajectory_var: ContextVar[list[dict] | None] = ContextVar("trajectory", default=None)

def start_trajectory() -> None:
    _trajectory_var.set([])

def get_trajectory() -> list[dict]:
    return list(_trajectory_var.get() or [])

async def _trajectory_middleware(ctx: LLMCallContext, nxt: LLMCallNext) -> LLMCallContext:
    ctx = await nxt(ctx)
    t = _trajectory_var.get()
    if t is not None and ctx.response is not None:
        t.append({
            "messages": ctx.messages,
            "response": ctx.response.assistant_message,
            "usage": ctx.response.usage,
        })
    return ctx

def install_trajectory(event_manager: EventManager) -> Callable[[], None]:
    return event_manager.intercept(MIDDLEWARE_LLM_CALL, _trajectory_middleware)
```

A second intercept on `MIDDLEWARE_EXECUTE_PYTHON` captures tool calls and
outputs, fixing the gap the NeMo Flow tool middleware left:

```python
async def _tool_middleware(ctx: ExecutePythonContext, nxt: ExecutePythonNext) -> ExecutePythonContext:
    ctx = await nxt(ctx)
    t = _trajectory_var.get()
    if t is not None:
        t.append({
            "tool": "execute_python",
            "code": ctx.code,
            "output": getattr(ctx.result, "returned_value", None),
        })
    return ctx
```

In `runner.py`:

```python
uninstall = install_trajectory(agent.event_manager)
start_task_tokens()
result = await agent._run_evaluation({"user_message": instruction})
result.update(get_task_tokens())
result["trajectory"] = get_trajectory()   # list of turn dicts
uninstall()
```

---

## Comparison

| | This MR | NeMo Flow middleware | Trajectory middleware |
|---|---|---|---|
| Token counts | ✅ direct | ✅ Rust round-trip | ✅ from `ctx.response.usage` |
| Full messages | ✗ | ✅ | ✅ |
| Tool calls + outputs | ✗ | ✗ (excluded at Rust boundary) | ✅ |
| Scope / call hierarchy | ✗ | partial (flattened) | ✅ (ContextVar call stack) |
| External dependency | none | NeMo Flow (private registry) | none |
| Serialization complexity | none | 3-path fallback | single `model_dump()` |
| Lines of new code | ~55 | 355 | ~120 |

---

## Relationship between MR!124 and the trajectory design

If the trajectory middleware lands, `token_usage.py` becomes redundant — token
counts can be summed from `step["usage"]` across the trajectory.  The runner
integration would simplify to:

```python
result.update(sum_tokens(get_trajectory()))   # replaces start/get_task_tokens
result["trajectory"] = get_trajectory()
```

**This MR is still worth merging independently.**  Token counting is immediately
useful for cost reporting and doesn't block anything.  The trajectory work is a
larger, separate effort.  If and when it lands, removing `token_usage.py` is a
clean one-file deletion with no other side effects.

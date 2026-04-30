# Benchmark Token & Trajectory Tracking

## What was implemented

**Position 3** (see summary table below) — a `ContextVar`-based token
accumulator with no trajectory capture.  Three files:

- `src/nemo_oo_agents/runtime/token_usage.py` — `start_task_tokens()` /
  `accumulate_tokens()` / `get_task_tokens()` backed by a `ContextVar`
- `src/nemo_oo_agents/runtime/actor.py` — listens for `"token_usage"` events
  fired by unifiedllm after each LLM call, calls `accumulate_tokens()`
- `packages/nemo-oo-agents-benchmarks/src/.../runner.py` — calls
  `start_task_tokens()` before `_run_evaluation`, merges `get_task_tokens()`
  into the result dict so `n_input_tokens`/`n_output_tokens` land in
  `agent/result.json`

Landed on main directly (no MR) via commits `2254cd9b`, `87a60394`, `80e7e007`.
ReAct baseline required a separate fix (`87a60394`) because it calls
`self._llm.acall()` directly, bypassing `actor.py` entirely.

---

## Context

This doc records the broader design space explored before the above
implementation — what the right long-term shape is if trajectory capture for
RL/SFT becomes a requirement, and how that relates to the NeMo Flow middleware
already on `main`.

---

## NeMo Flow already has trajectory capture

`nemo_flow_middleware.py` (on `main`) bundles two concerns in its LLM and tool
middleware:

**Guardrails** — When a NeMo Flow guardrail modifies an LLM request (e.g.
injects a system message, changes temperature), the middleware propagates those
changes back into `ctx` before the real LLM call.  Without this, request-
modifying guardrails would have no effect.  Nothing else in the framework
provides this.

**Trajectory capture** — After each LLM call, the middleware serializes the
response and returns it to NeMo Flow's Rust core, which fires event subscribers
and the ATIF exporter.  The tool middleware does the same: it extracts the
execution result and passes it to `nemo_flow.tools.execute()`.

So NeMo Flow already owns trajectory capture.  The question is not whether to
add trajectory capture — it is what to do with the capture that already exists
there.  There are three positions on this.

---

## The three positions

### Position 1 — Keep NeMo Flow's capture as-is

Use `nemo_flow_middleware.py` as-is for trajectory capture.  Activate it in
the benchmark runner when trajectory data is needed.

**Gaps in NeMo Flow's capture:**
- Tools are excluded from LLM requests at the Rust serialization boundary
  (`'dict' object has no attribute 'name'` error), so the LLM's tool
  definitions are absent from every ATIF step
- Tool execution results are recorded as `"status: complete"` / `"status: error"`,
  not actual return values
- The ATIF exporter flattens scope hierarchy — nested agent method calls appear
  flat in the exported trajectory
- Response serialization uses a three-path fallback (litellm `ModelResponse` →
  Pydantic `model_dump()` → manual `LLMResponse` construction) that is fragile
  across unifiedllm and litellm version changes
- `captured_ctx` side-effect pattern throughout — control flow is non-obvious

**When this is acceptable:** if you only need token counts and rough turn
structure, and NeMo Flow is already installed for guardrails.

---

### Position 2 — Replace NeMo Flow's capture with a Python-only implementation

Remove the capture responsibility from `nemo_flow_middleware.py` and implement
it as a lightweight Python intercept middleware.  The same
`event_manager.intercept()` API is used, but without routing through a Rust
core.

`LLMCallContext` carries `ctx.messages` and `ctx.response` directly — no
serialization needed.  A second intercept on `MIDDLEWARE_EXECUTE_PYTHON`
captures tool calls and their actual return values, fixing NeMo Flow's
`"status: complete"` gap.

```python
# trajectory.py  (~100 lines, no external deps)

_trajectory_var: ContextVar[list[dict] | None] = ContextVar("trajectory", default=None)

async def _llm_step(ctx: LLMCallContext, nxt: LLMCallNext) -> LLMCallContext:
    ctx = await nxt(ctx)
    t = _trajectory_var.get()
    if t is not None and ctx.response is not None:
        t.append({
            "type": "llm",
            "messages": ctx.messages,
            "response": ctx.response.assistant_message,
            "usage": ctx.response.usage,
        })
    return ctx

async def _tool_step(ctx: ExecutePythonContext, nxt: ExecutePythonNext) -> ExecutePythonContext:
    ctx = await nxt(ctx)
    t = _trajectory_var.get()
    if t is not None:
        t.append({
            "type": "tool",
            "code": ctx.code,
            "output": getattr(ctx.result, "returned_value", None),
            "stdout": getattr(ctx.result, "stdout", None),
        })
    return ctx
```

This replaces what NeMo Flow's LLM and tool middlewares were doing for capture.
ATIF format is produced by a `trajectory_to_atif()` converter (~30 lines)
rather than NeMo Flow's exporter.  Given NeMo Flow's gaps, the converter
produces better ATIF: tool definitions present, tool outputs real, scope
hierarchy preserved.

**What remains in NeMo Flow middleware:**
- `nemo_flow_agent_call_middleware` (scope push/pop) — clean, no change needed
- `nemo_flow_llm_middleware` — now guardrails-only: propagate request
  modifications from NeMo Flow interceptors into `ctx`, return `{}` to the
  Rust core.  The three-path serialization fallback is deleted.
- `nemo_flow_tool_middleware` — same: propagate tool interceptors, return
  `codec.to_json(None)`

The `captured_ctx` side-effect pattern stays in the guardrail-only NeMo Flow
middleware — it is inherent to NeMo Flow's callback-based `execute()` API — but
only for the guardrail propagation purpose, which is simpler to reason about.

**NeMo Flow without capture is not doubling.** The two middleware chains now
serve different concerns: NeMo Flow owns guardrail processing; the trajectory
middleware owns data capture.  Each LLM call passes through both, but they do
distinct work.

**Requires NeMo Flow?** Only for guardrails.  The trajectory middleware runs
independently with no external dep.  If NeMo Flow is not installed, capture
still works; guardrails are simply absent.

---

### Position 3 — Skip NeMo Flow's capture, no replacement

Don't activate `nemo_flow_middleware.py` in the benchmark runner at all.  Use
pos. 3's token counts for cost reporting.  Build trajectory capture only when
RL/SFT is a concrete requirement.

This is the current position — NeMo Flow middleware is available on `main` for
opt-in use but is not in the benchmark runner's hot path.

---

## Relationship to pos. 3

pos. 3 (this branch) implements token counts via unifiedllm's `"token_usage"`
event — a lightweight signal that fires after every LLM call carrying
`{prompt_tokens, completion_tokens}`.  It is completely independent of NeMo
Flow.

Under Position 2, `token_usage.py` becomes redundant: token counts are summed
from `step["usage"]` across the trajectory.  It is a clean one-file deletion
with no other side effects.

Under Position 3, pos. 3 is the right scope for now — immediately useful for
cost reporting without committing to a trajectory design.

---

## Summary

| | **Implemented (pos. 3)** | NeMo Flow capture as-is (pos. 1) | Python trajectory (pos. 2) |
|---|---|---|---|
| Token counts | ✅ | ✅ (via ATIF) | ✅ derived |
| Full message content | ✗ | ✅ | ✅ |
| Tool definitions in trajectory | ✗ | ✗ (Rust boundary) | ✅ |
| Tool outputs (real values) | ✗ | ✗ (`"status: complete"`) | ✅ |
| Scope hierarchy preserved | ✗ | ✗ (flattened) | ✅ |
| NeMo Flow guardrails | ✗ | ✅ | ✅ (guardrail-only NF) |
| NeMo Flow dep required | no | yes | no (guardrails optional) |
| Serialization complexity | none | 3-path fallback | none |
| `captured_ctx` antipattern | ✗ | ✅ (full) | remains (guardrails only) |
| ATIF fidelity | — | gaps | complete |

---

## Recommendation

**Stay at Position 3 (pos. 3) for now.** Cost reporting is immediately useful;
trajectory capture is not yet required.

**Move to Position 2 when RL/SFT is a concrete requirement.** Build the Python
trajectory middleware, simplify NeMo Flow's LLM and tool middlewares to
guardrails-only, and write the `trajectory_to_atif()` converter.  At that
point `token_usage.py` is deleted.

**Avoid Position 1 as a trajectory solution** — the gaps (no tool definitions,
no tool outputs, flattened hierarchy) make it unsuitable as training data
without significant post-processing, and the post-processing would be equivalent
to rewriting the trajectory capture in Python anyway.

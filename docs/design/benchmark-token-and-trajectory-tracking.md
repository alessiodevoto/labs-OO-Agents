# Benchmark Token & Trajectory Tracking

## Context

This MR (`feat/token-tracking`) adds per-task token counts (`n_input_tokens`,
`n_output_tokens`) to each Harbor trial's `agent/result.json`.  The
implementation is intentionally minimal: a `ContextVar`-based accumulator in
`token_usage.py` fed by a single new entry in `actor.py`'s unifiedllm metrics
bridge, with three lines of integration in `runner.py`.

This doc records the broader design space — what the right long-term shape is
if trajectory capture for RL/SFT becomes a requirement, how that relates to the
NeMo Flow middleware already on `main`, and whether implementing a trajectory
layer would double the existing code.

---

## What NeMo Flow middleware uniquely provides

Before comparing options it is worth being precise about what `nemo_flow_middleware.py`
actually does, because it bundles two concerns that can be separated:

**Guardrails** — Request intercept propagation.  When a NeMo Flow guardrail
modifies an LLM request (e.g. injects a system message, changes temperature,
rewrites code), the middleware propagates those changes back into `ctx` before
the real LLM call.  Without this, request-modifying guardrails would have no
effect.  The tool middleware does the same for code rewrites.  This is the
capability that nothing else in the framework provides.

**Trajectory / ATIF capture** — After the real LLM call, the middleware
serializes the response and returns it to NeMo Flow's Rust core, which fires
event subscribers and the ATIF exporter.  This is where the three-path
serialization fallback, the `captured_ctx` side-effect pattern, and the
tools-exclusion gap all live.

`nemo_flow_agent_call_middleware` (scope push/pop) belongs to the first
category — it gives ATIF per-method granularity and is clean code with no
serialization complexity.

The design question is whether these two concerns belong in the same middleware.

---

## The four options

### Option A — Merge MR!124 as-is

Token counts only.  The `ContextVar` accumulator is fed by unifiedllm's
`"token_usage"` event — a clean, already-firing signal that carries
`{prompt_tokens, completion_tokens}` after every LLM call.

```
unifiedllm "token_usage" event → _make_llm_metrics_bridge → accumulate_tokens()
```

**What you get:** `n_input_tokens`, `n_output_tokens` per Harbor trial.  
**What you don't get:** message content, tool calls/outputs, trajectory for
RL/SFT.  
**NeMo Flow relationship:** completely independent.  Both can coexist without
interaction — they touch different code paths.  
**Verdict:** right scope for cost reporting; dead end for training data.

---

### Option B — Trajectory middleware (no NeMo Flow dep)

A lightweight intercept middleware that accumulates full trajectory data using
the same ContextVar pattern as MR!124.  Uses the `event_manager.intercept()`
API — the same one NeMo Flow uses, but without routing through a Rust core.

`LLMCallContext` carries `ctx.messages` (full input) and `ctx.response` (full
output including usage), so no serialization gymnastics are needed.  A second
intercept on `MIDDLEWARE_EXECUTE_PYTHON` captures tool calls and their actual
return values — fixing the gap NeMo Flow's tool middleware has (it only records
`"status: complete"`).

```python
# trajectory.py  (~100 lines, no external deps)

_trajectory_var: ContextVar[list[dict] | None] = ContextVar("trajectory", default=None)

async def _llm_step_middleware(ctx: LLMCallContext, nxt: LLMCallNext) -> LLMCallContext:
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

async def _tool_step_middleware(ctx: ExecutePythonContext, nxt: ExecutePythonNext) -> ExecutePythonContext:
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

def install_trajectory(event_manager: EventManager) -> Callable[[], None]:
    u1 = event_manager.intercept(MIDDLEWARE_LLM_CALL, _llm_step_middleware)
    u2 = event_manager.intercept(MIDDLEWARE_EXECUTE_PYTHON, _tool_step_middleware)
    def uninstall():
        u1(); u2()
    return uninstall
```

Token counts become a derived field — sum `step["usage"]` across the trajectory
— so `token_usage.py` from MR!124 can be deleted.

**What you get:** full trajectory (messages, responses, tool I/O), token counts
as a side effect.  
**What you don't get:** NeMo Flow guardrails, ATIF format directly.  
**ATIF:** write a `trajectory_to_atif()` converter (~30 lines).  Given NeMo
Flow's own ATIF gaps (no tool outputs, flattened scope hierarchy), a
purpose-built converter would produce *better* ATIF than NeMo Flow's exporter.  
**NeMo Flow relationship:** fully independent.  If NeMo Flow middleware is also
active, both chains run — trajectory middleware captures cleanly *after*
guardrail modifications have already been applied to `ctx`, so the trajectory
reflects what was actually sent.  
**Verdict:** right design for RL/SFT; build this when training data is a real
requirement.

---

### Option C — Simplified NeMo Flow + trajectory middleware

This is the direct answer to the doubling question.

With trajectory middleware handling capture, NeMo Flow's LLM and tool
middleware no longer need to serialize responses back to the Rust core for ATIF.
They can be simplified to **guardrails only**: propagate request modifications
from NeMo Flow interceptors into `ctx`, let the chain run (trajectory middleware
fires here), and return `{}` to NeMo Flow.

```python
async def nemo_flow_guardrail_middleware(ctx: LLMCallContext, nxt: LLMCallNext) -> LLMCallContext:
    """Apply NeMo Flow guardrails only. Trajectory middleware handles capture."""
    request = LLMRequest({}, safe_params)
    captured_ctx: LLMCallContext | None = None

    async def _wrapper(req: Any) -> Any:
        nonlocal captured_ctx
        _propagate_modifications(req, ctx)   # guardrail request modifications
        captured_ctx = await nxt(ctx)        # real call; trajectory MW fires here
        return {}                            # NeMo Flow gets no response to capture

    await nemo_flow.llm.execute(model_name, request, _wrapper)
    return captured_ctx  # type: ignore[return-value]
```

The three-path serialization fallback is gone.  The `captured_ctx` side-effect
pattern survives — it is inherent to NeMo Flow's callback-based `execute()` API
and cannot be eliminated without changes to NeMo Flow itself.

`nemo_flow_agent_call_middleware` stays unchanged — it is already clean.
`nemo_flow_tool_middleware` similarly simplifies: propagate tool interceptors,
return `codec.to_json(None)`.

**Is this doubling?** No.  The two chains handle different concerns: NeMo Flow
owns guardrail processing; trajectory middleware owns data capture.  Each LLM
call passes through both, but they do distinct work.

**What you get:** NeMo Flow guardrails + scope management; full trajectory from
the trajectory middleware; token counts as a derived field.  
**What you give up:** NeMo Flow's ATIF exporter — replaced by a converter from
trajectory data, which has better fidelity (tool outputs, correct hierarchy).  
**Verdict:** cleanest design if both guardrails and trajectory capture are
required simultaneously.

---

### Option D — Keep both full implementations as-is

Run both `nemo_flow_llm_middleware` (full response serialization for ATIF) and
trajectory middleware on every LLM call.  Both capture the response; NeMo
Flow's ATIF exporter and the trajectory accumulator produce parallel records.

This is genuine doubling for the capture concern.  NeMo Flow's ATIF has known
gaps (no tool outputs, flattened hierarchy); trajectory middleware has complete
data.  The two outputs would diverge over time as one is maintained and the
other isn't, producing two partially-correct sources of truth for the same
event.

**Verdict:** avoid.

---

## These options are layers, not alternatives

Options B and C are not mutually exclusive choices.  Option B is the trajectory
middleware — a piece of code.  Option C is that same trajectory middleware with
the simplified NeMo Flow guardrail layer added on top.  You build B first; you
optionally layer C on top when guardrails become a requirement.  The additive
structure is:

```
Option A   token counts from unifiedllm event (this MR)
Option B   + trajectory middleware — LLM + tool capture, no NeMo Flow dep
Option C   + simplified NeMo Flow guardrail middleware on top of B
Option D   + full NeMo Flow capture running in parallel with B  ← avoid
```

Adopting Option C does not remove Option B.  The same `install_trajectory()`
call is present in both.  The difference is whether you also activate the
simplified NeMo Flow guardrail middleware — which you only do when NeMo Flow is
installed and runtime guardrails are needed.

---

## Summary

| | A (this MR) | B (trajectory) | C (B + NF guardrails) | D (B + full NF) |
|---|---|---|---|---|
| Token counts | ✅ | ✅ derived | ✅ derived | ✅ both |
| Full trajectory | ✗ | ✅ | ✅ | ✅ + partial duplicate |
| Tool outputs captured | ✗ | ✅ | ✅ | ✅ + ✗ in NF |
| NeMo Flow guardrails | ✗ | ✗ | ✅ | ✅ |
| Scope management | ✗ | ✗ | ✅ | ✅ |
| ATIF export | ✗ | via converter | via converter | NeMo Flow (gaps) |
| External dep | none | none | NeMo Flow (optional) | NeMo Flow |
| Doubles NeMo Flow? | n/a | no | no | **yes** |
| `captured_ctx` antipattern | ✗ | ✗ | remains (guardrails only) | remains (full) |
| Serialization complexity | none | none | removed from NF | 3-path fallback |
| Includes Option B? | — | ✅ | ✅ | ✅ |

---

## Recommendation

**Merge MR!124 now (Option A).** It is self-contained and immediately useful
for cost reporting.  `token_usage.py` is a clean deletion when trajectory
middleware lands.

**Build trajectory middleware (Option B) when RL/SFT is a concrete
requirement.** At that point `token_usage.py` is deleted; token counts come
from the trajectory.  Option B stands alone with no NeMo Flow dependency.

**Layer on simplified NeMo Flow guardrails (reaching Option C) only if runtime
guardrails become a production requirement** — e.g. prompt injection, temperature
overrides by policy, content filtering at inference time.  Until then, the
existing NeMo Flow middleware stays on `main` available for opt-in use, without
being in the benchmark runner's hot path.  Option C does not require removing
or replacing Option B — it adds one more middleware install on top.

**The decision to avoid is Option D.**  Once Option B is built, the full NeMo
Flow capture layer (serializing responses through the Rust core for ATIF)
becomes redundant alongside it.  Running both produces two diverging records of
the same events.

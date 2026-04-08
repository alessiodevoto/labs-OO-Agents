# Missing Trace Files Bug Investigation

## ROOT CAUSE IDENTIFIED

**The LLM is replacing the class method `RouterTestWrapper.process` with a new implementation that runs OUTSIDE of `execute_python`!**

From the logs (Cell In[8]):
```python
def _make_process_method():
    async def process(self, user_message: str, values: list[float]):
        # ... creates sub-agents WITHOUT llm=self._llm ...
        validator = self.ValidatorSubAgent()  # CRASHES: No parent context
        # ...
    return process

# CRITICAL: Attaches to the CLASS, not the instance!
RouterTestWrapper.process = _make_process_method()
```

**Why this breaks everything:**
1. The factory-created `process` method exists OUTSIDE `execute_python`
2. When this method is later called, `_parent_agent_var` context is NOT set
3. Sub-agents instantiated inside this method see `_parent_agent_var.get() = None`
4. Sub-agents crash with "No LLM available"

**Why it cascades:**
- The class mutation persists across samples (Python classes are mutable)
- Once one sample's LLM replaces the method, ALL subsequent samples are affected
- This explains why all 6 models fail simultaneously at the same run number

---

## Bug Summary

When running the eval pipeline with 50 runs per model (1200 total samples), approximately 18-20% of samples fail with:
1. **0.0ms execution time** (method returns instantly)
2. **Empty result**: `{'agents_called': [], 'results': {}}`
3. **Missing trace file** (no spans exported)
4. **Error**: `No LLM available for ValidatorSubAgent`

## Key Evidence

```
[WRAPPER_FAST] method=process elapsed=0.0ms agent_id=4558439392 result_type=dict
[FAST_EXECUTE] task=router_validate_002 latency=0.0ms error=False actual=dict:{'agents_called': [], 'results': {}}

CRASH: No LLM available for ValidatorSubAgent
[EXECUTE_ERROR] task=router_validate_002 error=No LLM available for ValidatorSubAgent. Resolution attempted:
```

## Architecture Context

The system uses:
- **RouterTestWrapper**: Parent agent that delegates to sub-agents
- **ValidatorSubAgent, TransformerSubAgent, AnalyzerSubAgent**: Child agents with ellipsis methods
- **CodeActStrategy**: LLM generates Python code that instantiates sub-agents
- **ContextVar `_parent_agent_var`**: Used to propagate LLM from parent to child agents

### LLM Resolution Cascade (in `Agent._resolve_llm`)

1. Instance-level: `MyAgent(llm=explicit_llm)`
2. Class-level: `class MyAgent(Agent, llm=class_llm)`
3. MRO inheritance: Walk parent classes for `_agent_llm`
4. **Runtime parent propagation**: `_parent_agent_var.get()` ← THIS IS FAILING
5. Error: No LLM found

### Expected Flow

1. `RouterTestWrapper.process()` called (ellipsis method)
2. CodeActStrategy generates code like: `validator = self.ValidatorSubAgent()`
3. `execute_python()` sets `_parent_agent_var.set(router_agent)` before running code
4. Sub-agent instantiation should see parent via `_parent_agent_var.get()`
5. Sub-agent inherits LLM from parent

### Actual Failure

Step 4 returns `None` - the context variable is not propagating correctly.

## Relevant Files

| File | Purpose |
|------|---------|
| `src/nemo_oo_agents/agent.py` | `_parent_agent_var` definition, `_resolve_llm()` method |
| `src/nemo_oo_agents/runtime/actor.py` | `execute_python()` sets/resets parent context, `_call_plan()` creates tasks |
| `src/nemo_oo_agents/strategies/codeact.py` | CodeActStrategy that calls execute_python |
| `src/nemo_oo_agents/metaclass.py` | Method wrapper that routes to `runtime._call_plan()` |
| `tests/capability/agents/router.py` | RouterTestWrapper and sub-agent definitions |
| `util/eval_pipeline/src/eval_pipeline/` | Pipeline that runs samples in parallel |

## Key Code Sections

### Parent Context Setting (actor.py ~line 605)

```python
# Set parent agent context for LLM inheritance by subagents
parent_token = _parent_agent_var.set(self.agent)
try:
    # ... code execution happens here ...
finally:
    _parent_agent_var.reset(parent_token)
```

### LLM Resolution (agent.py ~line 292)

```python
# 4. Runtime parent propagation
parent = _parent_agent_var.get()

if parent is not None and hasattr(parent, "_llm"):
    return parent._llm

# 5. No LLM found - CRASH
```

### Task Creation (actor.py ~line 1502)

```python
def _call_plan(self, method, args, kwargs) -> asyncio.Task:
    async def _execute_with_event():
        return await self._execute_task(method, args, kwargs)

    # This creates a NEW task - context vars are copied
    return asyncio.create_task(_execute_with_event(), name=task_name)
```

---

# Investigation Tracks

## Track A: ContextVar Propagation

**Question**: Is `_parent_agent_var` correctly propagated through async execution?

**Investigate**:
1. When `asyncio.create_task()` is called in `_call_plan()`, does it copy the current context correctly?
2. Is there any async boundary between `execute_python` setting the context and the sub-agent `__init__` reading it?
3. Are there nested `execute_python` calls that might reset the context prematurely?

**Files to examine**:
- `src/nemo_oo_agents/runtime/actor.py` - lines 560-1010 (execute_python), lines 1476-1502 (_call_plan)
- `src/nemo_oo_agents/agent.py` - lines 253-348 (_resolve_llm)

**Test approach**:
```python
# Add logging to trace context var state
import contextvars
print(f"Current context: {contextvars.copy_context()}")
print(f"_parent_agent_var in context: {_parent_agent_var in contextvars.copy_context()}")
```

---

## Track B: Parallel Execution Race Condition

**Question**: Is there a race condition in parallel sample execution?

**Investigate**:
1. Multiple samples run in parallel (20 workers)
2. Each creates its own agent instance via `agent_factory()`
3. Could one task's `_parent_agent_var.reset()` affect another task?

**Files to examine**:
- `util/eval_pipeline/src/eval_pipeline/evaluator.py` - `make_factory()` closure
- `util/eval_pipeline/src/eval_pipeline/pipeline.py` - `process_sample()`
- `evaluation/concurrency.py` - parallel task execution

**Key observation**: Failures start at specific run numbers (e.g., run32) and affect ALL models simultaneously - strongly suggesting global state pollution.

---

## Track C: Sub-Agent Instantiation Timing

**Question**: When exactly is the sub-agent instantiated relative to the parent context?

**Investigate**:
1. The LLM generates code that instantiates sub-agents
2. Is the sub-agent created inside `execute_python` or somewhere else?
3. Could the sub-agent be cached or reused from a previous run?

**Files to examine**:
- `src/nemo_oo_agents/strategies/codeact.py` - how code is generated and executed
- `tests/capability/agents/router.py` - sub-agent class definitions

**Suspicious pattern**: Class attributes on RouterTestWrapper:
```python
class RouterTestWrapper(Agent):
    AnalyzerSubAgent = AnalyzerSubAgent  # Class reference
    ValidatorSubAgent = ValidatorSubAgent
    TransformerSubAgent = TransformerSubAgent
```

---

## Track D: Empty Result Origin

**Question**: Where does `{'agents_called': [], 'results': {}}` come from?

**Investigate**:
1. This is the exact structure of `RouterResult` with empty values
2. 0.0ms execution means no LLM call happened
3. Is there a default/fallback return value somewhere?

**Files to examine**:
- `src/nemo_oo_agents/strategies/codeact.py` - early return paths
- `src/nemo_oo_agents/runtime/actor.py` - `_execute_with_generation` early exits

**Check for**:
- Cached results
- Default return values on exception
- Silent error handling that returns empty dict

---

## How to Reproduce

```bash
cd /Volumes/dev/dev/nemo_oo_agents
source .venv/bin/activate

# Run the debug script (creates 50 runs per model across 6 models)
./debug_trace_issue.sh 1

# Look for these patterns in the log:
# - [WRAPPER_FAST] with elapsed=0.0ms
# - CRASH: No LLM available
# - Missing trace file warnings
```

## Debug Logging Added

Current debug statements added:
- `[PARENT_SET]` in `execute_python` when setting parent context
- `[PARENT_RESET]` in `execute_python` when resetting parent context
- `[LLM_RESOLVE]` in `_resolve_llm` showing what parent is seen

Expected good case:
```
[PARENT_SET] Setting parent to RouterTestWrapper
[LLM_RESOLVE] ValidatorSubAgent: parent=RouterTestWrapper
[PARENT_RESET] Resetting parent from RouterTestWrapper
```

Failure case would show:
```
[LLM_RESOLVE] ValidatorSubAgent: parent=None
```

---

## Deliverables

After investigation, provide:
1. **Root cause**: Exact mechanism causing the failure
2. **Reproduction**: Minimal unit test that triggers the bug
3. **Fix**: Code change to resolve the issue

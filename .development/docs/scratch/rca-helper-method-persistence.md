# RCA: Helper Methods Persisting Between Test Runs

**Date**: 2026-01-27
**Priority**: High
**Approach**: TDD (Root Cause → Repro → Failing Test → Fix)

---

## Problem Statement

LLM-defined helper methods are persisting between test runs in the capability test suite. When the LLM defines a helper method (e.g., `process_impl`) during one run, it appears in the `doc(self)` output for subsequent runs, even though each run should create a fresh agent instance.

This causes:
1. **Prompt pollution**: Models see helper methods from prior runs in their system prompt
2. **Missing `async` keyword** in some method signatures (secondary symptom)
3. **Confusion**: Models may call stale helper methods instead of proper subagents

---

## Evidence from Traces

### Observation: `process_impl` Appears and Persists

| Run | `process_impl` occurrences | First appearance |
|-----|---------------------------|------------------|
| 1 | 0 | N/A |
| 2 | 0 | N/A |
| 3 | 0 | N/A |
| 4 | 1 | Line 16 (mid-run - LLM defined it) |
| 5 | 2 | Line 3 (from start!) |
| 6 | 6 | Line 3 (from start!) |
| 7-10 | 1-6 | Line 3 (from start!) |

**Key Insight**: In Run 4, `process_impl` appears at Line 16 (meaning it was defined during that run). In Runs 5+, it appears at Line 3 (the first LLM call), proving it persisted from prior runs.

### The `<self>` Block Difference

**Run 1-3 (no helper methods)**:
```xml
<self expr="doc(self)">
  class RouterTestWrapper:
      ...
      # State and attributes:
      ValidatorSubAgent: type[ValidatorSubAgent] = ValidatorSubAgent  # methods: async validate
</self>
```

**Run 5+ (helper method leaked)**:
```xml
<self expr="doc(self)">
  class RouterTestWrapper:
      ...
      # Available methods:
      async def process_impl(values: list[float]):

      # State and attributes:
      ValidatorSubAgent: type[ValidatorSubAgent] = ValidatorSubAgent  # methods: validate
</self>
```

Note: The "Available methods" section shouldn't exist for a fresh agent, and the `async` keyword is missing from `ValidatorSubAgent`'s methods.

---

## Trace Links

Use the `trace_explorer` package to investigate these traces.

### How to Use trace_explorer

```python
from trace_explorer import TraceExplorer, set_quiet_mode
set_quiet_mode(True)

trace = TraceExplorer.from_file("path/to/trace.006trace.jsonl")
print(trace.get_overview())
print(trace.get_session("session_id"))
print(trace.get_turn("session_id", 0))  # See full LLM context
```

Or via CLI:
```bash
trace-explorer path/to/trace.006trace.jsonl
trace-explorer path/to/trace.006trace.jsonl --session abc123
```

### Key Traces to Examine

**Run 4 (where helper was first defined)**:
- Path: `results/capability_optimization_20260126_223505/traces/RouterTestWrapper_nemotron3-nano-30b_process_router_validate_20260126_223506_04_000001_nemotron3-nano-30b.006trace.jsonl`
- Viewer: http://localhost:5001/?session_id=RouterTestWrapper_nemotron3-nano-30b_process_router_validate_20260126_223506_04_000001_nemotron3-nano-30b

**Run 10 (where helper persisted from start)**:
- Path: `results/capability_optimization_20260126_223505/traces/RouterTestWrapper_nemotron3-nano-30b_process_router_validate_20260126_223506_10_000001_nemotron3-nano-30b.006trace.jsonl`
- Viewer: http://localhost:5001/?session_id=RouterTestWrapper_nemotron3-nano-30b_process_router_validate_20260126_223506_10_000001_nemotron3-nano-30b

**Main branch (no issue)**:
- Path: `/Volumes/dev/dev/viewer/results/capability_optimization_20260126_223130/traces/RouterTestWrapper_nemotron3-nano-30b_process_router_validate_20260126_223130_01_000001_nemotron3-nano-30b.006trace.jsonl`
- Viewer: http://localhost:5001/?session_id=RouterTestWrapper_nemotron3-nano-30b_process_router_validate_20260126_223130_01_000001_nemotron3-nano-30b

---

## Relevant Code Locations

### 1. HelperMethodManager (binds helper methods)

**File**: `src/agent006/strategies/generated_code.py`

```python
class HelperMethodManager:
    """Extract, compile, and bind helper methods defined in the generated code block."""

    def apply(self, code, agent, session_locals, *, namespace, target_method_name):
        # ...
        # Line 237-238: This binds to the agent
        bound = types.MethodType(func, agent)
        setattr(agent, method_name, bound)  # <-- SUSPECT
```

**Question**: Is `agent` always an instance, or could it be the class?

### 2. Where HelperMethodManager is called

**File**: `src/agent006/strategies/codeact.py` (line 1330)
```python
helper_result = helper_manager.apply(
    code,
    runtime.agent,  # <-- What is runtime.agent?
    session.session_locals,
    namespace=namespace,
    target_method_name=target_method_name,
)
```

**File**: `src/agent006/strategies/pure_python.py` (line 624)
```python
helper_result = helper_manager.apply(
    code,
    runtime.agent,  # <-- Same question
    session.session_locals,
    namespace=namespace,
    target_method_name=target_method_name,
)
```

### 3. Agent006Provider (generates doc(self) output)

**File**: `packages/agentdoc/src/agentdoc/providers/agent006.py`

```python
def __doc_full__(self, obj: Any) -> str:
    # Line 159-165: This generates the "Available methods" section
    methods_content = methods(obj, config=agent_config)
    if methods_content:
        parts.append("    # Available methods:")
        for line in methods_content.split("\n"):
            if line.strip():
                parts.append(f"    {line}")
```

### 4. Test harness (creates agents per run)

**File**: `util/eval_pipeline/src/eval_pipeline/evaluator.py`

```python
# Line 376-390: Factory should create fresh instances
def make_factory(cf=client_factory, cls=test.agent_class, method=test.method):
    def factory():
        fresh_client = cf()
        agent_instance = cls(llm=fresh_client)  # <-- Should be fresh
        return AgentWrapper(agent_instance, method)
    return factory
```

---

## TDD Approach

### Step 1: Write a Failing Test

Create a test that:
1. Creates an agent instance
2. Simulates defining a helper method (like HelperMethodManager does)
3. Creates a NEW agent instance of the same class
4. Verifies the helper method is NOT present on the new instance

```python
# tests/capability/test_helper_method_isolation.py

import types
import pytest

def test_helper_methods_do_not_persist_across_instances():
    """Helper methods bound to one instance should not appear on new instances."""
    from tests.capability.agents.router import RouterTestWrapper

    # Create first instance
    agent1 = RouterTestWrapper(llm=MockLLM())

    # Simulate what HelperMethodManager does
    def helper_impl(self, x):
        return x * 2

    bound = types.MethodType(helper_impl, agent1)
    setattr(agent1, "helper_impl", bound)

    # Verify it's on agent1
    assert hasattr(agent1, "helper_impl")
    assert "helper_impl" in dir(agent1)

    # Create second instance
    agent2 = RouterTestWrapper(llm=MockLLM())

    # CRITICAL: helper_impl should NOT be on agent2
    assert not hasattr(agent2, "helper_impl"), "Helper method leaked to new instance!"
    assert "helper_impl" not in dir(agent2), "Helper method visible in dir() of new instance!"
```

### Step 2: Run the Test

```bash
cd /Volumes/dev/dev/agent006
source .venv/bin/activate
pytest tests/capability/test_helper_method_isolation.py -v
```

If the test **passes** (helper doesn't leak), the issue is elsewhere (test harness reusing instances).
If the test **fails** (helper leaks), we found the bug.

### Step 3: Debug with Real Scenario

If Step 2 passes, the issue is in how the eval pipeline creates/reuses agents:

```python
# Add debug logging to evaluator.py
def make_factory(cf=client_factory, cls=test.agent_class, method=test.method):
    def factory():
        fresh_client = cf()
        agent_instance = cls(llm=fresh_client)
        print(f"Created agent: id={id(agent_instance)}, class={id(cls)}")
        print(f"dir(agent_instance) has helper methods: {[m for m in dir(agent_instance) if 'impl' in m]}")
        return AgentWrapper(agent_instance, method)
    return factory
```

### Step 4: Add Guard to HelperMethodManager

```python
# In generated_code.py, line 237
import inspect

# Add assertion before setattr
if inspect.isclass(agent):
    raise TypeError(f"HelperMethodManager received a class, not an instance: {agent}")

# Verify it's really an instance attribute
bound = types.MethodType(func, agent)
setattr(agent, method_name, bound)

# Double-check it didn't leak to class
if hasattr(type(agent), method_name):
    raise RuntimeError(f"Helper method {method_name} leaked to class {type(agent).__name__}")
```

---

## Hypotheses to Test

1. **Class Mutation**: `setattr(agent, method_name, bound)` is somehow mutating the class, not the instance
   - Test: Check `type(agent).__dict__` before/after setattr

2. **Shared Instance**: The eval pipeline is reusing agent instances between runs
   - Test: Add `id(agent)` logging to verify unique instances

3. **Factory Closure Bug**: The `make_factory` closure is capturing the same agent reference
   - Test: Check if factory is being called fresh or returning cached result

4. **Module-level State**: Something in the agent module is caching instances
   - Test: Check for module-level variables or singletons

---

## Success Criteria

1. **Failing test exists** that demonstrates the bug
2. **Root cause identified** with evidence
3. **Fix implemented** that makes the test pass
4. **Regression test added** to prevent future occurrences
5. **Re-run capability tests** to verify improvement

---

## Related Documentation

- Full comparison analysis: `docs/scratch/capability-branch-comparison.md`
- Trace explorer package: `packages/trace_explorer/`
- HelperMethodManager: `src/agent006/strategies/generated_code.py`
- Agent006Provider: `packages/agentdoc/src/agentdoc/providers/agent006.py`

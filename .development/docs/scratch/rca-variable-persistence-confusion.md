# RCA: Variable Persistence Confusion + History Index Bug

**Date**: 2026-01-27
**Priority**: High
**Type**: Runtime Bug + Prompt/Context Design Issue

---

## CRITICAL BUG FOUND: `self.history[N]` Index Mismatch

### The Bug

The prompt shows `expr="self.history[15].value"` (using `seq_id`), but `__getitem__` uses **list index**. When events are removed via `history.remove()`, these indices diverge:

```
Before: _events = [E0, E1, E2, E3, ...]  with seq_ids [0, 1, 2, 3, ...]
Remove E1: _events = [E0, E2, E3, ...]    with seq_ids [0, 2, 3, ...]

Prompt shows: expr="self.history[3].value"  (seq_id=3)
LLM tries: self.history[3]
__getitem__(3) returns: IndexError! (only 3 items in list, indices 0-2)
```

### Where Events Get Removed

In `src/nemo_oo_agents/strategies/codeact.py`:
- **Line 468**: Remove empty AssistantEvent, replace with ToolCallEvent
- **Line 569**: Remove assistant event when no tool calls made
- **Line 586**: Remove empty assistant event when API rejects empty content

### The Fix

Change `__getitem__` to use `get_by_seq_id()`:

```python
# In src/nemo_oo_agents/runtime/history.py line 367-369
def __getitem__(self, key: int) -> EventBase:
    """Access events by seq_id (for consistency with prompt expr attributes)."""
    event = self.get_by_seq_id(key)
    if event is None:
        raise IndexError(f"No event with seq_id={key}")
    return event
```

Or alternatively, stop using `remove()` and instead mark events as hidden.

---

## Problem Statement

LLMs are not leveraging persisted variables across execution turns. Instead of using variables like `results` that persist in `session_locals`, LLMs either:
1. Try to access values via `self.history[N].value` (often with wrong index)
2. Manually reconstruct data from memory (error-prone)

This wastes tokens, causes errors, and leads to incorrect final results.

---

## Evidence from Trace

**Trace**: `CalculateBatchAgent_nemotron3-nano-30b_calculate_calculate_batch_20260126_223506_07_000000_nemotron3-nano-30b.006trace.jsonl`

**Viewer**: http://localhost:5001/?session_id=CalculateBatchAgent_nemotron3-nano-30b_calculate_calculate_batch_20260126_223506_07_000000_nemotron3-nano-30b

### Timeline

| Turn | Action | Result |
|------|--------|--------|
| 0 | Prefill + `import math` | ERROR: import forbidden |
| 1 | Redefine gcd/lcm, compute `results` | OK, 40 results |
| 2 | Try `self.history[15].value` | ERROR: index out of range |
| 3 | Try to find results in history | Empty candidates |
| 4 | Try `globals()` | ERROR: forbidden |
| 5 | Manually reconstruct 40 items | OK but wrong values |
| ... | More confusion | Eventually returns wrong result |

### Key Observation: LLM Reconstructs Data Instead of Using Variables

In Turn 5, the LLM had `results` available (it was captured in Turn 1), but instead it:

```python
# Build the full list of items from historical data (reconstructing from memory)
items = [
    {'a': 677076, 'b': 29, 'calculation': 'Multiply a by b'},
    {'a': 51054, 'b': 10, 'calculation': 'Compute a raised to the power b...'},
    # ... 40 items manually typed from memory
]
results = [compute_item(item) for item in items]
```

This reconstruction is:
- Token-expensive
- Error-prone (LLM might misremember values)
- Unnecessary (the variable was already in scope)

---

## Root Cause Analysis

### 1. History Display Format Misleads LLM

The execution result is displayed as:

```xml
<execute_python expr="self.history[11].value" tool_call_id="..." status="complete">
([19635204, 576, 2, 39, 17287, ...], 40)
</execute_python>
```

The `expr="self.history[11].value"` suggests to the LLM that this is how to access the result. But:
- `self.history[N]` indices are fragile (they change as history grows)
- The **actual** way to access the value is just `results` (the variable name)

### 2. No Indicator of Variables in Scope

The system prompt says "Variables persist across calls" but doesn't show:
- **Which** variables are currently available
- Their current values
- How to access them (just use the variable name)

### 3. LLM Confusion Between Two Access Patterns

| Pattern | Description | Reliability |
|---------|-------------|-------------|
| `results` | Direct variable access | Reliable |
| `self.history[N].value` | History event access | Fragile (index changes) |
| `Out[N]` | Jupyter-style output | Available but LLM doesn't use it |

The LLM doesn't know which pattern to use and often picks the wrong one.

---

## Technical Details

### Variables DO Persist (This Works Correctly)

```python
# In strategies/codeact.py line 696-698
if result.captured_locals:
    session.session_locals.update(result.captured_locals)
    logger.debug(f"[CODEACT] Captured locals: {list(result.captured_locals.keys())}")

# In strategies/codeact.py line 1324-1325
namespace = ExecutionNamespaceBuilder.build(
    runtime.agent, extra={**builtins, **session.session_locals, **strategy_extras}
)
```

The `results` variable from Turn 1 **is** available in Turn 2+. The LLM just doesn't know it.

### What Gets Captured

From `runtime/actor.py` line 812-819:

```python
__repl_captured_locals__.update({
    k: v for k, v in locals().items()
    if not k.startswith('_') and k not in ('self', 'asyncio')
})
```

All non-internal variables are captured: `gcd`, `lcm`, `sum_digits`, `compute_item`, `results`, `items`, etc.

---

## Proposed Solutions

### Option 1: Show Available Variables in Context (Recommended)

Add a context block showing what's in scope:

```xml
<session_variables expr="session.variables()" timestamp="...">
Available variables from previous executions:
- items: list[dict] (40 items)
- results: list[int] (40 items, last value: [19635204, 576, 2, ...])
- gcd: function
- lcm: function
- compute_item: function
</session_variables>
```

**Implementation**: Add to `strategy_instructions()` or as a dynamic context block.

### Option 2: Change History Display Format

Instead of:
```xml
<execute_python expr="self.history[11].value" ...>
([19635204, ...], 40)
</execute_python>
```

Use:
```xml
<execute_python execution_count="2" status="complete">
# Variable 'results' now contains 40 items
# Use 'results' directly in subsequent code
Last expression value: ([19635204, ...], 40)
</execute_python>
```

### Option 3: Add Explicit Guidance to Strategy Prompt

```markdown
**Variable Persistence**:
- All variables you define persist: `x = 5` in one call → `x` is available in the next
- To see a variable's current value, just print it: `print(results)`
- DON'T use `self.history[N].value` - indices are fragile and change
```

---

## TDD Approach

### Step 1: Create Failing Test

```python
# tests/capability/test_variable_persistence_clarity.py

async def test_llm_uses_persisted_variables():
    """LLM should use persisted variables rather than reconstructing from memory."""

    # Create a scenario where:
    # 1. First turn computes a large result
    # 2. Second turn needs to use that result
    # 3. Verify LLM uses the variable name, not history access or reconstruction

    agent = TestAgent(llm=TrackedLLM())

    # After execution, check the second call's code
    second_call_code = agent.llm.calls[1].code

    # Should use variable directly
    assert "results" in second_call_code

    # Should NOT try to access via history index
    assert "self.history[" not in second_call_code

    # Should NOT manually reconstruct the data
    assert "{'a': 677076" not in second_call_code
```

### Step 2: Implement Solution

1. **Modify `codeact.py` or `context_blocks/`** to add a `<session_variables>` block
2. **Update strategy prompt** with clear guidance on variable access
3. **Change history event formatting** to de-emphasize `expr="self.history[N].value"`

### Step 3: Verify Improvement

Re-run capability tests and check:
- Fewer `self.history[N].value` accesses
- No manual data reconstruction
- Higher success rate on multi-turn tasks

---

## Relevant Code Locations

| File | Purpose |
|------|---------|
| `src/nemo_oo_agents/strategies/codeact.py` | Strategy instructions, context blocks |
| `packages/context-blocks/src/context_blocks/formatter.py` | How history is rendered |
| `packages/context-blocks/src/context_blocks/renderer.py` | Block rendering |
| `src/nemo_oo_agents/runtime/history.py` | History event structure |

---

## Related Documentation

- Full comparison analysis: `docs/scratch/capability-branch-comparison.md`
- Trace explorer: `packages/trace_explorer/`

---

## Success Criteria

1. LLMs consistently use persisted variables by name
2. No `self.history[N].value` access patterns in generated code
3. No manual data reconstruction from memory
4. Higher success rate on multi-turn computation tasks

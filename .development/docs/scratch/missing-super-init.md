# Detect Missing `super().__init__()` in Agent Subclasses

## Problem

When users forget to call `super().__init__()` in their Agent subclass constructor, they get a confusing error:

```
ValueError: @strategy method can_fulfill_order on InventoryAgent requires RuntimeServices as first argument after self. Expected: async def can_fulfill_order(self, runtime: RuntimeServices, ...)
```

The real issue is that `self.runtime` doesn't exist because `Agent.__init__()` was never called, which sets up critical infrastructure (runtime, history, context, blocks, etc.).

## Root Cause

In `src/agent006/runtime/method_wrapper.py` lines 88-200, the wrapper logic:

1. Checks `hasattr(self, "runtime")` at line 89
2. If False and method needs generation, checks if first arg is `RuntimeServices` (line 168)
3. Falls through to confusing error at lines 196-200

## Solution

Enhance the error detection in the method wrapper to check if we're dealing with an Agent instance that's missing initialization, and provide a helpful error message.

### Changes Required

**File: `src/agent006/runtime/method_wrapper.py`**

Modify the final `else` block (lines 195-200) to detect missing initialization:

```python
else:
    # Check if this is an Agent instance missing initialization
    # Import here to avoid circular dependency
    from agent006.agent import Agent

    if isinstance(self, Agent) and not hasattr(self, "runtime"):
        raise RuntimeError(
            f"Agent {type(self).__name__} is not properly initialized.\n"
            f"\n"
            f"The agent's __init__() method must call super().__init__() to set up "
            f"critical infrastructure (runtime, history, context, blocks).\n"
            f"\n"
            f"Expected pattern:\n"
            f"  def __init__(self, ...):\n"
            f"      super().__init__()\n"
            f"      # Your initialization code here\n"
            f"\n"
            f"Without super().__init__(), generation methods cannot execute."
        )

    # Original error for non-Agent cases
    raise ValueError(
        f"@strategy method {original_func.__name__} on {type(self).__name__} requires "
        f"RuntimeServices as first argument after self. "
        f"Expected: async def {original_func.__name__}(self, runtime: RuntimeServices, ...)"
    )
```

### Testing

Create a test case in `tests/edge_cases/` or `tests/runtime/` that:

1. Creates an Agent subclass with `__init__` that doesn't call `super().__init__()`
2. Attempts to call a generation method
3. Verifies the new clear error message is raised (RuntimeError with "not properly initialized")

Example test structure:

```python
async def test_missing_super_init_detection():
    """Test that missing super().__init__() is detected with clear error."""

    class BrokenAgent(Agent, llm=some_llm):
        def __init__(self):
            # Missing super().__init__() - bug!
            self.data = []

        async def process(self):
            """Process something."""
            ...

    agent = BrokenAgent()

    with pytest.raises(RuntimeError) as exc_info:
        await agent.process()

    assert "not properly initialized" in str(exc_info.value)
    assert "super().__init__()" in str(exc_info.value)
```

## Benefits

1. **Clear error message**: Users immediately understand they forgot `super().__init__()`
2. **Fast debugging**: No confusion about RuntimeServices or method signatures
3. **Better DX**: Guides users to the correct pattern with example code
4. **Minimal overhead**: Only checks when error condition occurs (not on hot path)
5. **No breaking changes**: Existing working code unaffected

## Alternative Approaches Considered

1. **__new__ sentinel pattern**: More invasive, adds overhead to every instantiation
2. **Metaclass validation**: Hard to detect at class creation time (needs runtime check)
3. **Decorator on __init__**: Requires users to remember another decorator

The chosen approach (runtime check in wrapper) provides the best balance of simplicity, clarity, and minimal overhead.

## Implementation Status

- [x] Update method_wrapper.py to detect missing super().__init__()
- [x] Create test case verifying the new error detection (6 tests in test_agent_initialization.py)
- [x] Verify all examples in README.md and quickstart/ show correct super().__init__() pattern
- [x] Fixed README.md example that was missing super().__init__()

## Implementation Complete

The feature has been successfully implemented and tested. When users forget to call `super().__init__()`, they now get a clear, actionable error message:

```
RuntimeError: Agent BrokenAgent is not properly initialized.

The agent's __init__() method must call super().__init__() to set up critical infrastructure (runtime, history, context, blocks).

Expected pattern:
  def __init__(self, ...):
      super().__init__()
      # Your initialization code here

Without super().__init__(), generation methods cannot execute.
```

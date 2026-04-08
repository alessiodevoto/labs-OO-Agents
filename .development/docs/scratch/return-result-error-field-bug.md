# return_result() Recorded as Error in Traces

## Summary

When `return_result()` is called in CodeAct agents, it gets recorded as an "error" in execution traces, even though it's successful control flow. This causes false positives in mechanical checks and confusing trace data.

## Root Cause

`return_result()` uses an exception (`_ReturnResultSignal`) for control flow:

```python
# In codeact.py
class _ReturnResultSignal(Exception):
    """Signal raised when return_result() is called..."""
    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__("return_result() called")

def return_result(*args, **kwargs):
    # ... validation ...
    raise _ReturnResultSignal(result={"result": args[0]})
```

The executor in `actor.py` catches ALL exceptions and stores them in `ExecutionResult.error`:

```python
# In actor.py:717-728
except Exception as e:
    result = ExecutionResult(
        error=e,  # _ReturnResultSignal ends up here!
        ...
    )
```

The tracing hook then records this as an error:

```python
# In _hooks_impl.py:343-346
if exception:
    span.set_status(Status(StatusCode.ERROR, str(exception)))
    span.set_attribute("error.message", str(exception))
```

## Why Exceptions?

Using exceptions for `return_result()` is actually the right mechanism because:

1. **Immediate stop** - Like `return`, it must halt execution immediately
2. **Works anywhere** - Can be called from nested functions, callbacks, etc.
3. **Python precedent** - `StopIteration`, `SystemExit`, `GeneratorExit` all use exceptions for control flow

The bug isn't the exception itself - it's conflating "control flow signal" with "error".

## Proposed Fix

Have `actor.py` recognize signals and handle them differently from errors:

```python
# Define base class in agent006.events
class ExecutionSignal(Exception):
    """Base class for control flow signals (not errors)."""
    pass

# In codeact.py
class _ReturnResultSignal(ExecutionSignal):
    ...

# In actor.py - catch signals separately
try:
    exec(code, namespace)
except ExecutionSignal as signal:
    result = ExecutionResult(
        error=None,  # Not an error!
        returned_value=signal.result,
        ...
    )
except Exception as e:
    result = ExecutionResult(
        error=e,  # Actual error
        ...
    )
```

This way:
- `ExecutionResult.error` only contains actual errors
- Traces show correct status (OK, not ERROR)
- Mechanical checks don't need workarounds
- The signal pattern is explicit in the type system

## Current Workaround

Until the proper fix is implemented, `ExecutionErrorCheck` in mechanical checks filters out known control flow patterns:

```python
EXPECTED_CONTROL_FLOW = [
    "return_result() called",
    "ReturnResult",
]

def _is_control_flow(self, error: str) -> bool:
    return any(cf in error for cf in self.EXPECTED_CONTROL_FLOW)
```

## Files Involved

- `src/agent006/strategies/codeact.py` - Defines `_ReturnResultSignal`
- `src/agent006/runtime/actor.py` - Catches exceptions, calls hooks
- `packages/openinference-instrumentation-agent006/.../hooks_impl.py` - Records to traces
- `util/e2e_optimization/src/e2e_optimization/mechanical_checks/checks.py` - Has workaround filter

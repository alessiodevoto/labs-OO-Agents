# Pure Python Error Tracebacks Plan

## Problem

The Pure Python strategy currently formats errors minimally:

```python
def _format_error(self, error: Exception) -> str:
    error_type = type(error).__name__
    error_msg = str(error)
    return f"{error_type}: {error_msg}"
```

This produces output like:
```
SyntaxError: unterminated triple-quoted string literal (detected at line 2)
```

But Python tracebacks should include the full stack trace:
```
Traceback (most recent call last):
  File "<string>", line 49, in <module>
  File "/path/to/ast.py", line 52, in parse
    return compile(source, filename, mode, flags,
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<unknown>", line 2
    """## PURE_PYTHON Mode (Code Execution Loop)
    ^
SyntaxError: unterminated triple-quoted string literal (detected at line 2)
```

## Why This Matters

Full tracebacks help the LLM:
1. **Identify exact location**: Line numbers and file context
2. **Understand call stack**: What led to the error
3. **See code context**: The `^` pointer showing exact position
4. **Debug more effectively**: More context = better fixes

## Current Flow

1. `execute_code()` in `actor.py` catches exceptions at line 305
2. Exception stored in `ExecutionResult.error`
3. `_format_error()` called in `pure_python.py` at lines 280, 355
4. Error message sent to LLM via `ErrorEvent`

## Solution

### Option A: Update `_format_error()` to include traceback

```python
import traceback

def _format_error(self, error: Exception) -> str:
    """Format error with full traceback for LLM feedback."""
    # Get full traceback if available
    if error.__traceback__:
        tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
        return "".join(tb_lines)
    else:
        # Fallback to simple format
        return f"{type(error).__name__}: {error}"
```

### Option B: Capture traceback at exception site

In `actor.py execute_code()`:

```python
except Exception as e:
    import traceback
    tb_str = traceback.format_exc()
    result = ExecutionResult(
        stdout=stdout_buffer.getvalue(),
        error=e,
        error_traceback=tb_str,  # NEW: Store formatted traceback
        defined_methods={},
    )
    return result
```

Then in `_format_error()`:

```python
def _format_error(self, error: Exception, traceback_str: str | None = None) -> str:
    if traceback_str:
        return traceback_str
    # ... fallback
```

## Recommendation

**Option A** is simpler and doesn't require changing `ExecutionResult`. The exception's `__traceback__` attribute is preserved when the exception is stored, so we can format it later.

## Implementation Steps

1. [ ] Update `_format_error()` in `pure_python.py` to use `traceback.format_exception()`
2. [ ] Test with various error types (SyntaxError, NameError, TypeError, etc.)
3. [ ] Verify traceback includes line numbers from `<execute_code>` context

## Files to Modify

| File | Changes |
|------|---------|
| `src/nemo_oo_agents/strategies/pure_python.py` | Update `_format_error()` to include traceback |

## Testing

```python
# Test that tracebacks are properly formatted
code_with_syntax_error = '''
def foo():
    return "unclosed string
'''

result = await runtime.execute_code(code_with_syntax_error)
error_msg = strategy._format_error(result.error)
assert "Traceback" in error_msg
assert "SyntaxError" in error_msg
assert "line" in error_msg
```

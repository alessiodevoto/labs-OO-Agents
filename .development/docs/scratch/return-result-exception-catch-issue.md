# Issue #55: `return_result` Exception Can Be Caught by LLM-Generated Code

## Problem Summary

The `return_result()` function uses `_ReturnResultSignal` (an exception) to exit execution and return a result. However, if the LLM generates code with a broad `except Exception:` block, the signal gets caught and a fallback `return_result()` may return the wrong value.

### Problematic Code Pattern

```python
try:
    result = await self.retrieve_number_from_alec()
    return_result(result)  # Should complete task with correct value
except Exception as e:    # <-- Catches _ReturnResultSignal!
    print(f"Retry failed: {e}")
    return_result(self.count)  # Wrong value returned instead
```

### Current Implementation

```python
# In events.py
class ExecutionSignal(Exception):  # ← Inherits from Exception
    """Base class for control flow signals (not errors)."""

# In codeact.py
class _ReturnResultSignal(ExecutionSignal):  # ← Also an Exception
    """Signal raised when return_result() is called."""
```

Since `_ReturnResultSignal` inherits from `Exception`, it's caught by `except Exception:`.

---

## Potential Solutions

### Option 1: Inherit from `BaseException` Instead of `Exception`

**Implementation**: Change `ExecutionSignal` (or just `_ReturnResultSignal`) to inherit from `BaseException`.

```python
class ExecutionSignal(BaseException):  # Changed from Exception
    """Base class for control flow signals (not errors)."""
```

**Pros**:
- Simple, single-line change
- Follows Python convention (`KeyboardInterrupt`, `SystemExit`, `GeneratorExit` all inherit from `BaseException` for this exact reason)
- `except Exception:` won't catch it (by design)
- No changes to LLM prompts or code validation

**Cons**:
- Bare `except:` (without `Exception`) will still catch it
- Some code style guides discourage bare `except:`, but LLMs might still generate them
- May require updating any internal code that catches `ExecutionSignal` via `except Exception:`

**Effort**: Low (1-5 lines of code)

---

### Option 2: Pre-Execution AST Validation

**Implementation**: Before executing code, AST-parse it to detect `try/except` blocks that could catch `_ReturnResultSignal`:

```python
def validate_no_signal_catching(code: str) -> list[str]:
    """Detect try/except blocks that would catch return_result signals."""
    tree = ast.parse(code)
    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:  # bare except:
                errors.append("Bare 'except:' blocks may interfere with return_result()")
            elif isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"):
                errors.append("'except Exception:' may catch return_result() signal")
    return errors
```

**Pros**:
- Catches problematic patterns before execution
- Can provide actionable feedback to LLM
- Works regardless of exception hierarchy

**Cons**:
- May be too restrictive (legitimate try/except blocks)
- Adds complexity to validation pipeline
- Doesn't prevent the issue, only warns about it

**Effort**: Medium (30-50 lines)

---

### Option 3: Sentinel Value + Context Variable

**Implementation**: Instead of raising an exception, `return_result()` sets a context variable and returns a sentinel:

```python
_return_result_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar('return_result')

def return_result(*args, **kwargs):
    result = {"result": args[0]} if args else kwargs
    _return_result_ctx.set(result)
    return _RETURN_SENTINEL  # Special object

# After each statement execution, check:
if _return_result_ctx.get() is not None:
    # Task complete
```

**Pros**:
- Completely immune to try/except catching
- Can work with statement-by-statement execution

**Cons**:
- Requires significant refactoring of execution model
- Doesn't immediately stop execution (code continues until next check)
- More complex implementation

**Effort**: High (100+ lines, architectural changes)

---

### Option 4: Thread Interrupt / Signal-Based

**Implementation**: Use `sys.exit()` or threading/process signals to force exit:

```python
def return_result(*args, **kwargs):
    # Store result somewhere accessible
    _result_store.set({"result": args[0]} if args else kwargs)
    sys.exit(0)  # or raise SystemExit
```

**Pros**:
- `SystemExit` inherits from `BaseException` (same as Option 1)
- Very hard to catch accidentally

**Cons**:
- `sys.exit()` is unconventional for this use case
- May have side effects with async code
- Semantically confusing

**Effort**: Low-Medium (10-20 lines)

---

### Option 5: Code Rewriting / AST Transformation

**Implementation**: Transform the code before execution to protect `return_result()` calls:

```python
# Transform:
try:
    return_result(x)
except Exception:
    ...

# Into:
try:
    return_result(x)
except _ReturnResultSignal:
    raise  # Re-raise signals
except Exception:
    ...
```

**Pros**:
- Keeps exception-based flow
- Transparent to LLM

**Cons**:
- Complex AST transformation
- May not handle all edge cases
- Adds execution overhead

**Effort**: High (100+ lines)

---

### Option 6: Prompt Engineering

**Implementation**: Add explicit instructions to not wrap `return_result()` in try/except:

```
**Important**: Never wrap `return_result()` in a try/except block.
The `return_result()` function uses a special signal that must not be caught.
```

**Pros**:
- No code changes
- Simple to implement

**Cons**:
- LLMs may still generate problematic code
- Relies on LLM following instructions
- Not a robust solution

**Effort**: Very Low (1 line in prompt)

---

### Option 7: Hybrid - BaseException + Prompt Warning

**Implementation**: Combine Option 1 (BaseException) with Option 6 (prompt engineering):

1. Change `ExecutionSignal` to inherit from `BaseException`
2. Add prompt note: "Note: `return_result()` uses a signal that cannot be caught."

**Pros**:
- Defense in depth
- Handles most cases (except bare `except:`)
- Simple implementation

**Cons**:
- Still vulnerable to bare `except:` (rare but possible)

**Effort**: Low (5-10 lines)

---

## Recommendation

**Option 1 (BaseException) is the recommended solution.**

### Rationale:

1. **Python convention**: This is exactly why `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit` inherit from `BaseException` - they're control flow mechanisms that shouldn't be caught by normal exception handlers.

2. **Minimal change**: Single line change with well-understood semantics.

3. **Robust**: Works without relying on LLM behavior.

4. **Low risk**: The only edge case is bare `except:` (without specifying exception type), which is already a code smell and relatively rare in LLM output.

### Implementation:

```python
# In events.py
class ExecutionSignal(BaseException):  # Changed from Exception
    """Base class for control flow signals (not errors).

    Inherits from BaseException (not Exception) so signals are not caught
    by 'except Exception:' blocks in LLM-generated code.
    """
```

### Follow-up:

After implementing, consider adding AST validation (Option 2) as a warning for bare `except:` blocks to provide actionable feedback to the LLM.

---

## Quick Reference

| Option | Effort | Robustness | Complexity | Recommended |
|--------|--------|------------|------------|-------------|
| 1. BaseException | Low | High | Low | ✅ **Yes** |
| 2. AST Validation | Medium | Medium | Medium | As follow-up |
| 3. Sentinel + Context | High | Very High | High | No |
| 4. sys.exit/signals | Low | High | Low | No |
| 5. Code Rewriting | High | High | Very High | No |
| 6. Prompt Only | Very Low | Low | Very Low | Supplement |
| 7. Hybrid (1+6) | Low | High | Low | Best overall |

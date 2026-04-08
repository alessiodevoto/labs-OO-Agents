# Fix: _NO_RETURN sentinel leaking into traces

## Problem

When CodeAct code blocks don't return a value, the trace shows `<object object at 0x...>`
as the return value instead of nothing or `None`.

## Root Cause

1. `ExecutionResult.returned_value` defaults to `_NO_RETURN = object()` (events.py:28,280)
2. In `after_code_execution` (hooks_impl.py:393), `result` is an `ExecutionResult` Pydantic model
3. `_safe_serialize` calls `model_dump(exclude_none=True)` — but `_NO_RETURN` is not `None`, so it's included
4. `json.dumps(..., default=str)` converts the bare `object()` to `"<object object at 0x...>"`
5. The trace viewer displays this string as the return value

## Fix

Two-pronged approach:

### 1. Filter `_NO_RETURN` in `after_code_execution` (hooks_impl.py)

Before serializing the `ExecutionResult`, check `has_return` and replace the sentinel with
`None` so it gets properly excluded. Or better: serialize a sanitized copy.

### 2. Filter in `_safe_serialize` for Pydantic models with `returned_value`

When serializing dicts from `model_dump()`, filter out any `returned_value` that's a bare
`object()` instance (type is exactly `object`).

### Chosen approach

The cleanest fix is in `after_code_execution`: before passing `result` to `_safe_serialize`,
if `result` is an `ExecutionResult` with `has_return == False`, replace `returned_value` with
`None` in the serialized dict. This keeps the sentinel logic contained.

Actually, the simplest fix: override `model_dump` on `ExecutionResult` to exclude the sentinel,
OR fix in `_safe_serialize` by checking for sentinel-like values.

Simplest: In `after_code_execution`, serialize manually instead of passing the whole object.
OR: In the Pydantic model, use a custom serializer for `returned_value`.

**Decision**: Fix in `_safe_serialize` — when we encounter a Pydantic `model_dump()` result that
has a `returned_value` key whose value's type is exactly `object`, replace with `None`. This is
the least invasive change.

Wait — even simpler. Just fix in `after_code_execution` directly:

```python
if hasattr(result, 'has_return') and not result.has_return:
    # Don't serialize the sentinel _NO_RETURN object
    result_copy = result.model_copy()
    result_copy.returned_value = None
    span.set_attribute("result", self._safe_serialize(result_copy))
```

Or even simpler — just handle it in `_safe_serialize` by filtering the `filtered_dict`:

In the Pydantic branch, after building `filtered_dict`, check for `returned_value` that is
a bare `object()` (not a subclass) and set it to `None`.

**Final decision**: The most correct and minimal fix is in `ExecutionResult` itself — add a
custom model serializer (or override `model_dump`) that replaces `_NO_RETURN` with `None`.
This ensures ALL serialization paths are covered. But this changes the model behavior globally.

Actually, the **truly simplest** fix: just change the default from `_NO_RETURN` to `None` and
use a different mechanism to track `has_return`. Use `explicit_return` + a separate bool field.

No — `has_return` distinguishes "returned None" from "no return". We need the sentinel.

**Final final decision**: Fix in `_safe_serialize`. When processing `model_dump()` output,
check if any value is `type(v) is object` (exactly `object`, not a subclass) and replace with
`None`. This catches the sentinel without importing it.

## Files to change

1. `packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/_hooks_impl.py`
   - In `_safe_serialize`, filter sentinel values from Pydantic model_dump output

2. Tests: add a test that `ExecutionResult` with no return serializes cleanly

## Edge cases

- `returned_value` could legitimately be a bare `object()` in user code — extremely unlikely
  but possible. The sentinel check `type(v) is object` would incorrectly filter it. This is
  acceptable given how rare it is.

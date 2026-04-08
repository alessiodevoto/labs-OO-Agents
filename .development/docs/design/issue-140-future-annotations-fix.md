# Fix: `from __future__ import annotations` breaks CodeAct return type resolution (issue #140)

## Problem

When an agent module uses `from __future__ import annotations` (PEP 563), all annotations become lazy strings at runtime. `inspect.signature(method).return_annotation` returns the string `"CSVResult"` instead of the actual type object. When this string reaches `pydantic.create_model(result=(return_type, ...))`, Pydantic cannot resolve it and raises a `PydanticUserError`.

Affected location: `src/nemo_oo_agents/strategies/codeact.py`, lines 539–541 (`execute()` method).

## Root Cause

```python
# Current code (broken under PEP 563)
method = getattr(runtime.agent, call.method_name)
sig = inspect.signature(method)
return_type = sig.return_annotation   # ← string like "CSVResult" under PEP 563
```

`inspect.signature` does not resolve stringified annotations. `typing.get_type_hints` does, using the module's `__globals__` namespace.

## Fix

Replace the two lines that read `return_annotation` with a call to `typing.get_type_hints`:

```python
import typing

method = getattr(runtime.agent, call.method_name)
try:
    hints = typing.get_type_hints(method, include_extras=True)
    return_type = hints.get("return", inspect.Parameter.empty)
except (NameError, TypeError, AttributeError):
    # Fall back if get_type_hints fails (e.g. forward-reference to unavailable name)
    sig = inspect.signature(method)
    return_type = sig.return_annotation
```

Key points:
- `include_extras=True` preserves `Annotated[...]` metadata (e.g. `BeforeValidator`).
- `inspect.Parameter.empty` is the same sentinel as `inspect.Signature.empty`, so the existing emptiness check at line 543 continues to work unchanged.
- The fallback ensures we don't regress on any edge cases where `get_type_hints` cannot resolve names.
- `get_type_hints` is added to the `from typing import ...` block (no new import statement needed).

## Files Changed

| File | Change |
|------|--------|
| `src/nemo_oo_agents/strategies/codeact.py` | Replace `inspect.signature` + `return_annotation` with `get_type_hints` in `execute()` |
| `src/nemo_oo_agents/strategies/predict.py` | Same fix in `PredictStrategy.execute()` |
| `src/nemo_oo_agents/strategies/current_call.py` | Same fix in `CurrentCall.from_method()` |
| `src/nemo_oo_agents/strategies/generated_code.py` | Same fix in `ReturnValueValidator.validate()` |
| `src/nemo_oo_agents/runtime/actor.py` | Same fix in actor dispatch return-type extraction |
| `src/nemo_oo_agents/strategies/pure_python.py` | Same fix in `PurePythonStrategy._is_task_complete()` |
| `tests/strategies/test_codeact_future_annotations.py` | New test file with 18 PEP 563 tests (unit + end-to-end + fallback) |

## Test Strategy

Add a focused unit test that:
1. Defines a helper module (or function) that simulates `from __future__ import annotations` by providing a method whose annotations are already stringified (mimicking PEP 563 behaviour at the object level).
2. Calls `CodeActStrategy._build_return_result_tool` with a type resolved via `get_type_hints` — or exercises the full `execute()` path using `FakeLLMClient`.
3. Confirms no `PydanticUserError` is raised and the tool schema is correct.

Since reproducing PEP 563's string-annotation effect requires actually placing the `from __future__ import annotations` declaration at the top of a real module, the test file itself will use that import and define a helper agent class there.

## Edge Cases

- `from __future__ import annotations` + plain `str` return type → should still work (string `"str"` resolves to `str`).
- `Annotated[str, BeforeValidator(...)]` with PEP 563 → `include_extras=True` preserves the validator.
- `get_type_hints` failure (e.g. forward reference that can't be resolved) → fallback to old behaviour.
- Methods with no return annotation → `hints.get("return", inspect.Parameter.empty)` returns the sentinel; existing error is raised.

## Out of Scope

- `_build_builtins` at line 1962 reads only parameter names, not return types — no change needed.
- `TemplateStrategy` is not affected (does not read return types).
- Issue #139 (non-Pydantic return types like `pd.DataFrame`) is a separate issue.

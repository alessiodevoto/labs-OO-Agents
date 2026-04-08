# Fix Issue #139: create_model fails for non-Pydantic return types

## Problem

When an agent method has a return type annotation that isn't Pydantic-serializable (e.g. `pd.DataFrame`, `np.ndarray`), the codeact strategy crashes with a `PydanticUserError` because `pydantic.create_model()` cannot handle these types.

There are 5 `create_model()` call sites in `codeact.py`:
1. **Line ~1308** — `_handle_return_result()`: validating return_result tool call
2. **Line ~1421** — `_try_validate_return_value()`: silent validation
3. **Line ~1529** — `_build_return_result_tool()`: None return type (safe, uses `type(None)`)
4. **Line ~1545** — `_build_return_result_tool()`: with Annotated description
5. **Line ~1551** — `_build_return_result_tool()`: standard case

## Approach

### Core idea: check type compatibility before `create_model()`, fall back to `Any`

Add a helper `_safe_return_type(return_type)` that:
1. Tries `create_model("_Test", result=(return_type, ...))` in a try/except
2. If it fails (PydanticSchemaGenerationError/PydanticUserError), returns `Any`
3. If it succeeds, returns the original `return_type`
4. Cache the result per type to avoid repeated checks

This approach:
- **Doesn't break existing behavior** for Pydantic-compatible types
- **Gracefully degrades** for non-Pydantic types (no validation, accepts raw value)
- **Is simple** — one function, applied at all call sites

### Where to apply

1. **`_build_return_result_tool()`**: Use safe type for schema generation. Also, when the type is non-Pydantic, we should still provide a good tool description mentioning the expected type, but the schema uses `Any` for the result field.
2. **`_handle_return_result()`**: Use safe type for validation. When type is non-Pydantic, do an `isinstance()` check instead.
3. **`_try_validate_return_value()`**: Use safe type for validation. When type is non-Pydantic, do an `isinstance()` check.

### isinstance() check for non-Pydantic types

For types that can't go through Pydantic, we do a basic `isinstance()` check:
- If value passes isinstance, accept it
- If it fails, return a clear error message telling the LLM what type was expected

### Error messages to the LLM

When the LLM returns a wrong type for a non-Pydantic return type, the error message should be:
```
Return type validation error:
Expected an instance of <TypeName>, but got <ActualType>.
Hint: Use execute_python() to construct the result and call return_result(variable) from within.
```

This guides the LLM toward constructing the object in code (where it has access to imports like pandas) rather than trying to pass it through the tool schema.

## Test Plan

### Unit tests (test first, should fail initially)

1. **Test `_safe_return_type()` with Pydantic-compatible types** — returns original type
2. **Test `_safe_return_type()` with non-Pydantic types** — returns Any
3. **Test `_build_return_result_tool()` with non-Pydantic return type** — builds tool without crash
4. **Test `_handle_return_result()` with non-Pydantic return type** — validates via isinstance
5. **Test `_try_validate_return_value()` with non-Pydantic return type** — validates via isinstance

### Integration test
6. **Full agent run with `pd.DataFrame` return type** — no crash, returns DataFrame correctly

## Files to change

- `src/nemo_oo_agents/strategies/codeact.py` — add `_safe_return_type()`, modify 4 call sites
- `tests/strategies/test_non_pydantic_return_types.py` — new test file

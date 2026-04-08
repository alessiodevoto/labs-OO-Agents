# Nested StructuredOutput Method Fix

**Date:** 2025-12-12
**Issue:** `'SummarizeBatchAgent' object has no attribute '_summarize_doc'` when LLM-generated code defines and calls a `@plan` decorated helper method with `StructuredOutputStrategy()` in the same turn.

## Problem

When a `PurePythonStrategy` method generated code like this:

```python
@plan(strategy=StructuredOutputStrategy())
async def _summarize_doc(self, doc: str) -> str:
    """Summarize a single document."""
    ...

summaries = []
for doc in documents:
    summary = await self._summarize_doc(doc)
    summaries.append(summary)
return summaries
```

The execution failed with `AttributeError: 'SummarizeBatchAgent' object has no attribute '_summarize_doc'`.

## Root Causes

### 1. Missing AsyncFunctionDef Support in Pre-Binding

**File:** `src/agent006/strategies/pure_python.py:588`

The `_extract_and_bind_methods` function only checked for `ast.FunctionDef`:

```python
if not isinstance(node, ast.FunctionDef):
    continue
```

This skipped `async def` methods, preventing them from being pre-bound to the agent class before execution.

**Fix:**
```python
if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
    continue
```

### 2. StructuredOutput JSON Format for Basic Types

**File:** `src/agent006/strategies/structured_output.py:329-336`

For basic return types (str, int, etc.), `StructuredOutputStrategy` wraps them in a Pydantic model with a `value` field:

```python
response_model = create_model(
    model_name,
    value=(return_type, ...),  # Required field with the return type
)
```

This means for a method returning `str`, the LLM must return JSON like:

```json
{"value": "hello"}
```

NOT just:
```json
"hello"
```

The `FakeLLMClient` test responses needed to match this format.

### 3. Missing Decorators in Method Globals

**File:** `src/agent006/strategies/pure_python.py:602-612`

When dynamically binding methods via `exec()`, the `plan` decorator and strategy classes weren't available in the execution namespace. This caused failures when the LLM used `@plan(strategy=StructuredOutputStrategy())`.

**Fix:** Added all necessary imports to `method_globals`:
```python
method_globals = {
    "__builtins__": __builtins__,
    "plan": __import__("agent006.decorators", fromlist=["plan"]).plan,
    "PurePythonStrategy": type(self),
    "StructuredOutputStrategy": __import__(
        "agent006.strategies.structured_output", fromlist=["StructuredOutputStrategy"]
    ).StructuredOutputStrategy,
    "ReflexionStrategy": __import__(
        "agent006.strategies.reflexion", fromlist=["ReflexionStrategy"]
    ).ReflexionStrategy,
}
```

## Testing

Created comprehensive tests in `tests/strategies/test_pure_python_nested_structured_output.py`:

1. **`test_pure_python_nested_structured_output_same_turn`**: Verifies basic nested StructuredOutput helper
2. **`test_pure_python_nested_structured_output_multiple_calls`**: Verifies nested helper called in loop (3x)

Both tests pass ✅

## Verification Flow

The complete execution flow now works correctly:

1. **Parent method starts** → `PurePythonStrategy` generates code
2. **Pre-bind scan** → Detects `AsyncFunctionDef` node for `_summarize_doc`
3. **Method pre-bound** → Added to agent class with `_plan_strategy = StructuredOutputStrategy()`
4. **Code executes** → Calls `await self._summarize_doc(doc)`
5. **Strategy dispatch** → Runtime correctly retrieves `StructuredOutputStrategy` for `_summarize_doc`
6. **LLM called** → Returns `{"value": "summary"}`
7. **JSON parsed** → `json.loads()` succeeds with proper format
8. **Validation succeeds** → Pydantic validates and unwraps `value` field
9. **Parent completes** → Returns final result

## Debug Logs Example

```
DEBUG [ACTOR] Strategy retrieval for compute: has_attr=True, strategy=PurePythonStrategy
DEBUG [PURE_PYTHON] Pre-bind scan START: contains_class_def=False top_level=['AsyncFunctionDef', 'Assign', 'Return'] top_level_async_functions=1
DEBUG [PURE_PYTHON] Pre-bound method '_summarize_doc' to class. has_plan_strategy=True, strategy=StructuredOutputStrategy
DEBUG [ACTOR] Strategy retrieval for _summarize_doc: has_attr=True, strategy=StructuredOutputStrategy
DEBUG [STRUCTURED_OUTPUT] Parsing string content for _summarize_doc: len=27, content=|{"value": "summary"}|
DEBUG [STRUCTURED_OUTPUT attempt=1] Validation successful
INFO  [PURE_PYTHON] Task complete - validating and returning result
```

## Related Files Changed

- `src/agent006/strategies/pure_python.py` - Fixed async function detection, added method_globals
- `src/agent006/strategies/structured_output.py` - Added debug logging for JSON parsing
- `tests/strategies/test_pure_python_nested_structured_output.py` - New comprehensive tests
- `tests/strategies/test_pure_python_class_definitions.py` - Tests for class definition rejection

## Impact

This fix enables a powerful pattern where LLM-generated methods can:
- Define helper methods with different strategies (e.g., StructuredOutput for data extraction)
- Call those helpers in the same turn (no round-trip delay)
- Mix strategies within a single method (e.g., PurePython loop + StructuredOutput items)

Example use case:
```python
@plan(strategy=PurePythonStrategy())
async def process_documents(self, docs: list[str]) -> list[Summary]:
    """Process multiple documents."""
    # LLM generates:
    # 1. Helper with StructuredOutput for individual doc summarization
    # 2. Loop that calls helper for each doc
    # 3. Returns aggregated results
    ...
```

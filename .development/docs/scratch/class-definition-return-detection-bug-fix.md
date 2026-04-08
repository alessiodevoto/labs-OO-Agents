# Class Definition Return Detection Bug Fix

**Date:** 2025-12-12
**Issue:** PurePythonStrategy incorrectly completed when LLM returned class definitions
**Status:** ✅ Fixed

## Problem

When an LLM generated code containing a class definition with methods that included `return` statements, PurePythonStrategy would incorrectly treat the generation as complete and return `None`.

### Example Problematic Code

```python
class SummarizeBatchAgent:
    """Agent for batch document summarization tasks."""

    async def summarize_batch(self, documents: list[str]) -> list[str]:
        reasoning("Need to summarize each document.")
        @plan(strategy=StructuredOutputStrategy())
        async def summarize_doc(self, doc: str) -> str:
            """Summarize a single document."""
            ...
        summaries = []
        for doc in documents:
            summary = await self.summarize_doc(doc)
            summaries.append(summary)
        return summaries  # ← This nested return was being detected!
```

### Root Cause

In `actor.py:292-294`, the code was checking for return statements using `ast.walk(tree)`, which recursively walks **all nodes** including those nested inside function/class definitions:

```python
# BUG: Detects returns inside nested functions/classes
has_explicit_return = any(
    isinstance(node, ast.Return) for node in ast.walk(tree)
)
```

When wrapping the class definition in `async def __repl_wrapper__():`, the wrapper function returns `None` implicitly, but since `has_explicit_return=True` (due to the nested return), it treated `None` as a valid return value.

## Solution

Changed the return detection to only check for returns at the **top level** of the code, excluding returns nested inside function/class definitions:

```python
# FIXED: Only detects top-level returns
has_explicit_return = any(
    isinstance(node, ast.Return)
    for node in tree.body
    if isinstance(node, ast.Return)
) or any(
    any(isinstance(n, ast.Return) for n in ast.walk(node))
    for node in tree.body
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
)
```

This checks:
1. Direct `return` statements at module level
2. Returns inside non-function/non-class statements (e.g., inside an `if` block)
3. **Excludes** returns inside function definitions and class definitions

## Debug Instrumentation Added

Added comprehensive debug logging in `PurePythonStrategy` to track:
- Generated code length (raw and cleaned)
- Pre-bind scan results (class definitions, function counts)
- Execution results (stdout, methods, return status)
- Loop iteration state

Example log output:
```
DEBUG [PURE_PYTHON] Generated code (raw_len=580, clean_len=581): class SummarizeBatchAgent:...
DEBUG [PURE_PYTHON] Pre-bind scan: contains_class_def=True top_level=['ClassDef'] top_level_functions=0 top_level_async_functions=0
DEBUG [PURE_PYTHON] Execution successful: stdout=0, methods=[], rejected=[], has_return=False
```

## Test Coverage

Created comprehensive test suite in `tests/strategies/test_pure_python_class_definitions.py`:

1. **`test_pure_python_class_definition_current_behavior_hits_max_iterations`**
   - Documents the corrected behavior: class definitions now correctly loop until max_iterations
   - Verifies debug breadcrumbs are emitted
   - Status: ✅ PASSING

2. **`test_pure_python_rejects_class_definitions_and_allows_retry`** (xfail)
   - Documents desired future behavior: explicit rejection of class definitions
   - Should add validation error and allow LLM to retry with REPL-style code
   - Status: 🔄 XFAIL (future enhancement)

## Verification

All test suites pass:
- ✅ 96 passed, 1 xfailed in `tests/strategies/`
- ✅ 121 passed in `tests/runtime/`
- ✅ No regressions in existing functionality

## Future Enhancements

The fix correctly prevents class definitions from being treated as complete, but they still execute and loop until exhaustion. Future enhancement could add explicit validation:

1. Add `visit_ClassDef` to `PlanningLanguageValidator` in `runtime/validator.py`
2. Return clear `ErrorEvent` to LLM: "Class definitions are not allowed. Define methods with `async def helper(self, ...):` instead."
3. Allow LLM to retry with proper REPL-style code

## Files Modified

- `src/nemo_oo_agents/runtime/actor.py` - Fixed return detection logic
- `src/nemo_oo_agents/strategies/pure_python.py` - Added debug instrumentation
- `tests/strategies/test_pure_python_class_definitions.py` - New test file

## Related Issues

This bug was discovered during investigation of why the `SummarizeBatchAgent` pattern (defining a class in generated code) was not behaving as expected. The fix ensures that such patterns are now correctly detected as incomplete and exhaust the iteration budget, making the failure mode explicit rather than silently returning `None`.

# Error Message Improvements

This document shows the before/after comparison for error messages passed to the LLM.

## Problem

Error messages included full framework tracebacks that were noisy and unhelpful for the LLM:
- Framework internal code paths (actor.py, validator.py, asyncio/runners.py)
- Python standard library frames
- No direct pointer to the error in the user's code

## Solution

New error formatting module (`agent006/errors/formatting.py`) that:
1. Filters out framework tracebacks - shows only user code frames
2. Shows syntax errors with caret (^^^) pointing to exact error location
3. Formats validation errors cleanly without tracebacks

---

## Error Type 1: Validation Errors (Import Forbidden)

### BEFORE (noisy)

```
Execution error:
```
Traceback (most recent call last):
  File "/Volumes/dev/dev/agent006/src/agent006/runtime/actor.py", line 261, in execute_code
    validate_planning_code(
  File "/Volumes/dev/dev/agent006/src/agent006/runtime/validator.py", line 113, in validate_planning_code
    validator.validate(code)
  File "/Volumes/dev/dev/agent006/src/agent006/runtime/validator.py", line 53, in validate
    raise ValidationError("\n".join(self.errors))
agent006.errors.PlanningCodeViolation: Line 1: import statements are forbidden. Add imports to the module where the agent is defined instead.

Available in scope: Agent, AnalyzerResult, AnalyzerSubAgent, CompositeStrategy, PurePythonStrategy, ReflexionStrategy, RouterResult, RouterTestWrapper, StructuredOutputStrategy, TemplateStrategy, TransformerSubAgent, TypedDict, ValidatorResult, ValidatorSubAgent, __builtins__, __cached__, __doc__, __file__, __loader__, __name__, __package__, __spec__, agent, asyncio, brief, default_strategy, doc, message, methods, plan ... (5 more)
```
Fix and try again.
```

**Problems:**
- 4 framework traceback frames that don't help the LLM
- Internal file paths exposed
- Key error message buried at the bottom

### AFTER (clean)

```
Execution error:
```
PlanningCodeViolation: Line 1: import statements are forbidden. Add imports to the module where the agent is defined instead.

Available in scope: Agent, AnalyzerResult, AnalyzerSubAgent, CompositeStrategy, PurePythonStrategy, ReflexionStrategy, RouterResult, RouterTestWrapper, StructuredOutputStrategy, TemplateStrategy, TransformerSubAgent, TypedDict, ValidatorResult, ValidatorSubAgent, __builtins__, __cached__, __doc__, __file__, __loader__, __name__, __package__, __spec__, agent, asyncio, brief, default_strategy, doc, message, methods, plan ... (5 more)
```
Fix and try again.
```

**Improvements:**
- No framework traceback - validation errors are static analysis, traceback is useless
- Error message is immediately visible
- Actionable guidance (Available in scope) is prominent

---

## Error Type 2: Runtime Errors (asyncio.run in event loop)

### BEFORE (noisy)

```
Execution error:
```
Traceback (most recent call last):
  File "/Volumes/dev/dev/agent006/src/agent006/runtime/actor.py", line 313, in execute_code
    result_value = await exec_globals["__repl_wrapper__"]()
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<execute_code>", line 6, in __repl_wrapper__
  File "/Users/pfurgale/.pyenv/versions/3.12.7/lib/python3.12/asyncio/runners.py", line 190, in run
    raise RuntimeError(
RuntimeError: asyncio.run() cannot be called from a running event loop
```
Fix and try again.
```

**Problems:**
- Framework internals exposed (actor.py line 313)
- Python standard library path exposed (asyncio/runners.py)
- User has to mentally filter to find relevant frame

### AFTER (clean)

```
Execution error:
```
  Line 5:
    result = asyncio.run(task)

RuntimeError: asyncio.run() cannot be called from a running event loop
```
Fix and try again.
```

**Improvements:**
- Only shows the user's code frame
- Line number adjusted to match original code (not wrapper)
- Clear what code caused the error

---

## Error Type 3: Syntax Errors

### BEFORE (noisy)

```
Execution error:
```
Traceback (most recent call last):
  File "/Volumes/dev/dev/agent006/src/agent006/runtime/actor.py", line 272, in execute_code
    tree = ast.parse(code)
  File "<unknown>", line 2
    def foo(
           ^
SyntaxError: '(' was never closed
```
Fix and try again.
```

**Problems:**
- Framework frame (actor.py) included
- Error location marked in traceback format

### AFTER (clean)

```
Execution error:
```
SyntaxError at line 2:
    def foo(
           ^
  '(' was never closed
```
Fix and try again.
```

**Improvements:**
- No framework traceback
- Clear line number
- Caret points to exact error location
- Error message formatted for readability

---

## Implementation Details

### Files Changed

1. **`src/agent006/errors/formatting.py`** (NEW)
   - `format_error_for_llm(error, code)` - Main entry point
   - `_format_syntax_error()` - Syntax error with caret
   - `_format_validation_error()` - Clean message without traceback
   - `_format_runtime_error()` - Filters to user code frames only
   - `_is_user_code_frame()` - Detects user vs framework frames

2. **`src/agent006/errors/__init__.py`**
   - Added export: `format_error_for_llm`

3. **`src/agent006/strategies/pure_python.py`**
   - `_format_error()` - Now uses `format_error_for_llm()`
   - `_send_execution_error()` - Passes code for better context

### Framework Path Detection

Framework frames are identified by these markers:
- `agent006/` - Framework source code
- `site-packages/` - Third-party packages
- `lib/python` - Python standard library
- `<frozen` - Frozen importlib modules

User code frames are identified by:
- `<execute_code>` - The filename used for LLM-generated code

---

## Tests

See `tests/test_error_formatting.py` for comprehensive tests including:
- Frame detection (user vs framework)
- Validation error identification
- Syntax error formatting
- Runtime error filtering
- Before/after comparison documentation

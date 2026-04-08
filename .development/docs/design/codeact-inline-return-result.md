# Supporting `return_result()` from within `execute_python()`

## Context

Currently, CodeActStrategy provides two separate tools to the LLM:
1. `execute_python(code)` - Run Python code for computation
2. `return_result(...)` - Return the final structured answer

These are separate tool calls that the LLM makes sequentially.

## The Observation

An LLM naturally tried to call `return_result()` directly from within Python code:

```python
print(self.ValidatorSubAgent().validate(values))
agents_called = ["Validator"]
results = {"Validator": self.ValidatorSubAgent().validate(values)}
print(return_result(agents_called=agents_called, results=results))
```

**This is actually smart!** It would allow the LLM to compute the result and return it in a single `execute_python()` call, rather than needing two separate tool calls.

## Current Architecture

### Two-Tool Approach
```
LLM decides to execute code
  ↓
Tool call: execute_python(code="x = 1 + 1\nprint(x)")
  ↓
Execution result: "2"
  ↓
LLM decides it has the answer
  ↓
Tool call: return_result(result=2)
  ↓
Task complete
```

### Potential One-Tool Approach
```
LLM decides to execute code and return
  ↓
Tool call: execute_python(code="x = 1 + 1\nreturn_result(result=x)")
  ↓
Task complete (result validated and returned)
```

## Benefits

1. **Efficiency**: One tool call instead of two
2. **Natural**: Feels like a return statement in programming
3. **Atomic**: Compute and return happen together
4. **Fewer round-trips**: Reduces latency and API costs

## Implementation Design

### 1. Make `return_result` Available as a Builtin

Add `return_result` to the execution namespace (in `_build_builtins`):

```python
def return_result(**kwargs):
    """Signal task completion with final result."""
    raise _ReturnResultSignal(result=kwargs)
```

The function raises a special exception to signal completion.

### 2. Define the Signal Exception

```python
class _ReturnResultSignal(Exception):
    """Exception raised when return_result() is called from within code.

    This is an internal signal (not an error) that indicates the LLM
    has computed the final result and wants to return it.
    """
    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__("return_result() called")
```

### 3. Catch the Signal in Code Execution

In `_handle_execute_python()`, check if the execution error is actually a signal:

```python
async def _handle_execute_python(
    self,
    runtime: RuntimeServices,
    tool_call: Any,
    args: dict[str, Any],
    builtins: dict[str, Any],
    session: CodeActSession,
    method_name: str,
) -> Any | None:
    """Handle execute_python tool call."""
    code = args.get("code", "")

    # ... existing validation ...

    # Execute the code
    result = await self._execute_code(runtime, code, builtins, session, method_name)

    # Check if this was a return_result signal
    if result.error and isinstance(result.error, _ReturnResultSignal):
        # The LLM called return_result() from within the code!
        # Validate and return the result directly
        validated = self._handle_return_result(
            runtime,
            tool_call,
            result.error.result,  # Extract the result dict
            return_type,
            session,
            method_name,
        )

        if validated is not None:
            logger.info("[CODEACT] Task completed via inline return_result()")
            return ("TASK_COMPLETE", validated)

        # Validation failed, continue loop with error feedback already added
        return None

    # ... existing error handling ...
```

### 4. Handle the Signal in Main Loop

In the main `execute()` loop, check for the special return:

```python
if tool_call.name == "execute_python":
    result = await self._handle_execute_python(...)

    if isinstance(result, tuple) and result[0] == "TASK_COMPLETE":
        # return_result() was called inline and validated successfully
        return result[1]

    if result is None:
        # Error occurred, already handled
        continue
```

### 5. Update Strategy Instructions

Update the `strategy_instructions()` template to mention this capability:

```python
"""## CodeAct Mode (Tool-Based Code Execution)

You have two tools available:

1. **`execute_python(code)`** - Run Python code for computation and exploration
   - Use this to perform calculations, inspect data, call methods on `self`
   - You can call this multiple times as needed
   - Variables and helper functions persist across calls
   - **EFFICIENCY TIP**: You can call `return_result(...)` from within your code
     to compute and return the final answer in one step!

2. **`return_result(...)`** - Return the final answer when you're done
   - Call this ONLY when you have computed the final result
   - Pass the result matching the expected return type
   - Can be called as a separate tool OR from within execute_python code

**Workflow**:
1. Think about what you need to do
2. Call `execute_python(code)` to run computations
   - If you know this is the final computation, call `return_result()` at the end!
3. Observe the results (if using multiple steps)
4. Repeat steps 1-3 as needed
5. When ready, call `return_result(...)` (as tool or from within code)

...
"""
```

## Implementation Checklist

- [ ] Add `_ReturnResultSignal` exception class
- [ ] Add `return_result()` to execution builtins
- [ ] Update `_handle_execute_python()` to detect and handle the signal
- [ ] Update main loop to handle "TASK_COMPLETE" return
- [ ] Update strategy instructions to document this capability
- [ ] Add tests for inline return_result usage
- [ ] Update error messages to clarify both usage patterns

## Edge Cases to Handle

1. **Type mismatch in inline return**: Same validation as tool call
2. **Multiple return_result calls**: First one wins (exception propagates)
3. **return_result in helper function**: Works naturally (exception propagates)
4. **Mixing patterns**: Both approaches should work (backward compatible)

## Backward Compatibility

This enhancement is **fully backward compatible**:
- Existing code using separate tool calls continues to work
- LLMs can choose either pattern
- No breaking changes to API or behavior

## Alternative: Return Value Instead of Exception

Instead of an exception, we could use a return value:

```python
def return_result(**kwargs):
    return _ReturnResultSignal(result=kwargs)

# In code:
return return_result(result=42)
```

**Pros**: More explicit (requires explicit return)
**Cons**: Less natural, requires the LLM to remember to return it

The exception approach is better because:
- Works anywhere in the code (not just at the end)
- Immediately stops execution (like a return statement)
- More natural for LLMs (just call the function)

## Related: `print(return_result(...))` Pattern

The LLM used `print(return_result(...))`. With the exception approach, this works fine:
- `return_result()` raises exception
- `print()` never executes
- Exception is caught and handled

This is actually a non-issue - the exception prevents the print from happening.

## Recommendation

**YES, we should implement this!**

1. It's a natural pattern that LLMs are already trying
2. It's more efficient (fewer tool calls)
3. Implementation is clean and non-invasive
4. Fully backward compatible
5. Makes the code act more like actual programming

The exception-based approach is elegant and aligns with how early returns work in programming languages.

## Implementation Priority

**Medium-High** - This is a quality-of-life improvement that:
- Reduces latency and cost (fewer tool calls)
- Makes the strategy more intuitive
- Handles a pattern LLMs naturally try
- Requires minimal code changes (~50 lines)

Should be implemented after any critical bugs but before new features.

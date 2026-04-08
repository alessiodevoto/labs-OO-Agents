---
name: PurePython REPL Refactoring
overview: Refactor PurePythonStrategy to use true REPL-style execution where LLM code executes directly as part of method execution, removing method-definition detection complexity and aligning with CodeAct principles.
todos:
  - id: phase1-wrapper
    content: Create method context wrapper for REPL execution
    status: pending
  - id: phase1-return
    content: Handle return value capture and session completion
    status: pending
  - id: phase1-execution-result
    content: Update ExecutionResult with returned_value field
    status: pending
  - id: phase2-helpers
    content: Extract and persist helper functions across iterations
    status: pending
  - id: phase2-session
    content: Update session locals management for helper reuse
    status: pending
  - id: phase3-instructions
    content: Rewrite strategy instructions to REPL style
    status: pending
  - id: phase3-errors
    content: Remove obsolete error handlers
    status: pending
  - id: phase3-feedback
    content: Simplify feedback messages
    status: pending
  - id: phase4-cleanup
    content: Remove method detection and calling logic
    status: pending
  - id: phase4-tests
    content: Update test suite for new behavior
    status: pending
---

# PurePythonStrategy REPL-Style Refactoring Plan

## Executive Summary

Refactor [`src/nemo_oo_agents/strategies/pure_python.py`](src/nemo_oo_agents/strategies/pure_python.py) from a "define method then call it" pattern to a **true REPL flow** where the LLM's code executes directly as part of the method's execution context, using return statements to complete the task.

## Current State Analysis

### How PurePythonStrategy Works Today

**Current Flow:**

1. LLM generates Python code (entire response is code)
2. Runtime executes code via [`execute_code()`](src/nemo_oo_agents/runtime/actor.py)
3. Runtime checks if `target_method_name` was defined in `result.defined_methods`
4. If found → call method, return result (session complete)
5. If not found → add feedback to history, continue loop (max 10 iterations)

**Key Problem:** The LLM must understand it needs to **define a complete method** matching the signature. This is complex because:

- LLM has to track "am I done?" state (define vs explore)
- Prompts are lengthy explaining method definition requirements (lines 164-192)
- Error handling for "return outside function" (lines 304-310)
- Complex termination logic checking method names (lines 334-382)

### CodeAct Principles

CodeAct uses **executable Python as the action space**:

- LLM generates code snippets that execute in a persistent Python REPL
- Observations (stdout, return values, errors) feed back to LLM
- No distinction between "exploration" and "completion" - code is always executing
- Return statements naturally complete tasks

## Proposed New Design

### REPL-Style Execution Model

**New Flow:**

1. Wrap LLM's code generation in an implicit execution context
2. LLM outputs code that runs **in the method's scope**
3. Use `return` statement to complete the method (natural Python)
4. Any other code (print, helper functions, exploration) executes normally
5. Loop continues until `return` statement or max iterations

**Conceptual Model:**

**Example 1: Simple REPL flow with return**

```python
# What the LLM is doing (conceptually):
async def method_name(self, param):
    # LLM turn 1:
    print(f"Received: {param}")
    # [stdout returned, loop continues]

    # LLM turn 2:
    intermediate = param.upper()
    print(f"Processed: {intermediate}")
    # [stdout returned, loop continues]

    # LLM turn 3:
    return intermediate  # <-- This completes the method!
```

**Example 2: Task decomposition with helper methods**

```python
# What the LLM is doing (conceptually):
async def analyze_text(self, text: str):
    # LLM turn 1: Define helper methods for subtasks
    def count_words(self, text: str) -> int:
        return len(text.split())

    def count_sentences(self, text: str) -> int:
        return text.count('.') + text.count('!') + text.count('?')

    # ^^ Runtime magic happens here:
    #    1. execute_code() detects functions with 'self' as first param
    #    2. Binds them: setattr(agent, 'count_words', types.MethodType(count_words, agent))
    #    3. Adds to session_locals so immediately available in next turn
    #    4. These are REAL methods on the agent instance!
    # [feedback: "Methods defined: ['count_words', 'count_sentences']", loop continues]

    # LLM turn 2: Use the helper methods (they're now on self!)
    words = self.count_words(text)
    sentences = self.count_sentences(text)
    print(f"Analysis: {words} words, {sentences} sentences")
    # [stdout returned, loop continues]

    # LLM turn 3: Return final result
    return {
        "word_count": words,
        "sentence_count": sentences,
        "avg_words_per_sentence": words / sentences if sentences > 0 else 0
    }
    # <-- This completes the method!
```

**Example 3: Helper methods persisting across multiple plan calls**

```python
# First call to agent:
await agent.analyze_text("Hello world. How are you?")
# → Defines count_words() and count_sentences() on agent
# → Returns analysis dict

# Later, a different method can use those helpers:
@plan(strategy=PurePythonStrategy())
async def quick_word_count(self, text: str) -> int:
    """Count words using existing helper if available."""
    ...

# LLM turn 1: Check if helper exists and use it
if hasattr(self, 'count_words'):
    return self.count_words(text)
else:
    # Define it ourselves
    return len(text.split())
# <-- Returns count

# The helpers become part of the agent's capabilities!
```

**Key behaviors:**

- Any function defined with `self` as first param gets bound to agent instance via `setattr()`
- Helper functions persist **beyond the current session** - they're real instance methods
- Other `@plan` methods can discover and reuse these helpers via `self.method_name()`
- Within the same session, helpers are immediately available (via `session_locals`)
- This enables incremental capability building: agent learns new methods over time
- LLM doesn't need special syntax - just `def method_name(self, ...)` like normal Python
- No namespace pollution: only methods with `self` parameter are attached to agent

### Key Changes

**1. Remove Method Definition Detection (Simplification)**

- Delete lines 328-382 (method installation and calling logic)
- Delete prompt sections about "define the complete method" (lines 186-191)
- Delete error guidance for "return outside function" (lines 304-310)

**2. Wrap Execution in Method Context**

- Execute LLM code inside a generated async function scope
- Capture return values directly
- Allow helper function definitions to persist (attach to agent)

**3. Simplified Prompts**

- "Output Python code. Use `return` to complete the task."
- Remove all language about "defining methods"
- Focus on REPL-style interaction

**4. Helper Method Support (Task Decomposition)**

- Any function definitions in LLM code get extracted and bound to agent
- These persist across iterations (stored in `session_locals`)
- Enables task decomposition: LLM defines helpers, then uses them

## Implementation Steps

### Phase 1: Core REPL Execution

**Step 1.1: Create method context wrapper**

- Modify `execute()` to wrap LLM code in an implicit async function
- Execute code as if it's inside the target method's body
- Capture return statements as method completion

**Step 1.2: Handle return values**

- Detect when code executes a return statement
- Extract the returned value
- Complete the session (validate and return)

**Step 1.3: Update ExecutionResult**

- Add `returned_value` field to track return statements
- Add `has_return` boolean property

### Phase 2: Helper Function Persistence

**Step 2.1: Extract helper functions**

- Parse code for function definitions (non-target functions)
- Execute them and bind to agent instance
- Store in session state for reuse across iterations

**Step 2.2: Update session locals**

- Merge helper functions into builtins for subsequent iterations
- Maintain persistence within the same method call session

### Phase 3: Simplify Prompts

**Step 3.1: Rewrite strategy instructions**

- Remove "define complete method" language (lines 164-192)
- Add REPL-style instructions: "You are in a Python REPL. Use return to complete."
- Simplify to ~5-10 lines max

**Step 3.2: Remove error handlers**

- Delete `error_return_outside()` (lines 132-141)
- Delete `error_method_raised()` (lines 143-156)
- Keep only: empty response, syntax error, execution error

**Step 3.3: Simplify feedback**

- Remove "Define X to complete" message (lines 159-161)
- Use: "Output shown above. Continue or return result."

### Phase 4: Cleanup

**Step 4.1: Remove legacy code**

- Delete method detection logic (lines 328-382)
- Delete `_call_generated_method()` helper (lines 679-726)
- Simplify validation (remove method-signature matching)

**Step 4.2: Update tests**

- Modify test expectations: LLM returns values, not defines methods
- Update fake LLM responses to use return statements
- Update expected prompts in tests

## Technical Details

### Execution Context Implementation

```python
# Pseudo-code for new execute() logic:
code = await runtime.generate()

# Wrap code in implicit method context
wrapped = f"""
async def __repl_method__():
{indent(code, '    ')}
"""

# Execute and capture return
result = await runtime.execute_code(wrapped, builtins=builtins)

if result.returned_value is not None:
    # Task complete!
    return validate_return_type(result.returned_value, ...)

# Otherwise, loop continues with stdout feedback
```

### Helper Function Extraction

```python
# After executing code:
for func_name, func_obj in result.defined_methods.items():
    # Bind to agent for future use
    setattr(runtime.agent, func_name, func_obj)
    # Also add to session locals for immediate reuse
    self._session_locals[func_name] = func_obj
```

## Benefits

1. **Simpler for LLM:** Natural Python REPL interaction, not "meta" method definition
2. **Shorter prompts:** ~80% reduction in instruction length
3. **Cleaner code:** ~200 lines deleted from strategy
4. **More robust:** Fewer edge cases (no method matching logic)
5. **Better task decomposition:** Helper functions naturally supported
6. **Aligned with CodeAct:** Executable code as action space

## Risks & Mitigations

**Risk:** LLM might not understand it's "in a method"

- **Mitigation:** Update prompt to clarify "You are implementing method X. Parameters available: ..."

**Risk:** Return type validation needs adjustment

- **Mitigation:** Keep existing `validate_return_type()` logic, just apply to `result.returned_value`

**Risk:** Test suite needs large updates

- **Mitigation:** This is acceptable (pre-alpha, breaking changes OK). Update ~40 test files.

## Files to Modify

Primary:

- [`src/nemo_oo_agents/strategies/pure_python.py`](src/nemo_oo_agents/strategies/pure_python.py) - Core refactoring (~300 lines modified)
- [`src/nemo_oo_agents/events.py`](src/nemo_oo_agents/events.py) - Add `returned_value` to ExecutionResult
- [`src/nemo_oo_agents/runtime/actor.py`](src/nemo_oo_agents/runtime/actor.py) - Update `execute_code()` to handle wrapping

Secondary (tests):

- [`tests/runtime/test_pure_python_executor.py`](tests/runtime/test_pure_python_executor.py) - Update all test expectations
- [`tests/strategies/test_python_task_strategy.py`](tests/strategies/test_python_task_strategy.py) - Update prompts
- All other test files using PurePythonStrategy (~15 files)

## Success Criteria

1. All tests pass with updated expectations
2. Prompt instructions ≤ 10 lines (vs current ~30)
3. No method-name matching logic remains
4. Helper functions can be defined and reused within a session
5. Return statements complete the method naturally

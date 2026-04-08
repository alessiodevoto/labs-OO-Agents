# Root Cause Analysis: calculate_batch Regression

**Date**: 2026-01-27
**Regression**: Pass rate dropped from 58.3% to 43.3% (-15.0%)
**Test**: `calculate_batch` (batch math operations from natural language)

## Executive Summary

The branch introduced a **context formatting change** that altered how execution results are presented to the LLM. This formatting change caused LLMs to generate code with **condition ordering bugs** more frequently, leading to incorrect results on multi-pattern matching tasks.

## Quantitative Impact

| Model | Main | Branch | Delta |
|-------|------|--------|-------|
| gpt-oss-120b | 80% | 30% | **-50%** |
| claude-haiku | 90% | 70% | -20% |
| gemini-2.5-flash-lite | 60% | 50% | -10% |
| qwen3-80b | 10% | 0% | -10% |
| claude-sonnet | 80% | 80% | 0% |
| nemotron3-nano-30b | 30% | 30% | 0% |
| **TOTAL** | **35/60** | **26/60** | **-15%** |

**Detailed breakdown:**
- 15 regressions (main pass → branch fail)
- 6 improvements (main fail → branch pass)
- Net: 9 additional failures

## What Changed in Context

The branch modified how execution results are rendered in the LLM context window:

### Main Branch Format
```xml
<execute_python expr="self.history.events[3].content" tool_call_id="...">
Execution successful.
Stdout:
Call: async def calculate(self, items: list[dict]) -> list[int | float]
...actual stdout content...
</execute_python>
```

```xml
<execute_python expr="self.history.events[7].content" tool_call_id="...">
Execution error:
RestrictedCodeError: import of 'math' is forbidden...
</execute_python>
```

### Branch Format (New)
```xml
<execute_python expr="self.history[3].stdout" tool_call_id="..." execution_count="0" status="complete">
Call: async def calculate(self, items: list[dict]) -> list[int | float]
...actual stdout content...
</execute_python>
```

```xml
<execute_python expr="self.history[7].error" tool_call_id="..." execution_count="1" status="error">
RestrictedCodeError: import of 'math' is forbidden...
</execute_python>
```

### Key Differences

| Aspect | Main | Branch |
|--------|------|--------|
| Expression path | `self.history.events[N].content` | `self.history[N].stdout` / `.error` |
| Content prefix | "Execution successful.\nStdout:\n" | *(none)* |
| Error prefix | "Execution error:\n" | *(none)* |
| Additional attrs | *(none)* | `execution_count`, `status` |

## What Behavior Changed

### The Bug Pattern

In **5 of 15 regressions**, the LLM generated code with a **condition ordering bug**:

**Main (correct) - `elif` chain with proper ordering:**
```python
elif 'integer-divided by' in calc and 'modulo' in calc:  # SPECIFIC check first
    result = (a // b) + (a % b)
# ... other checks ...
elif 'Compute a modulo b' in calc:  # GENERAL check last
    result = a % b
```

**Branch (buggy) - separate `if` statements with wrong ordering:**
```python
if 'modulo' in calc_lower and 'raised' not in calc_lower:  # GENERAL matches first!
    return a % b   # Catches "Compute (a integer-divided by b) plus (a modulo b)" incorrectly
# ... other checks ...
if 'integer-divided' in calc_lower and 'plus' in calc_lower:  # SPECIFIC never reached
    return (a // b) + (a % b)
```

### Concrete Example

For input: `{'a': 311030, 'b': 18, 'calculation': 'Compute (a integer-divided by b) plus (a modulo b)'}`

- **Expected**: `17287` = `(311030 // 18) + (311030 % 18)` = `17279 + 8`
- **Main output**: `17287` ✓
- **Branch output**: `8` = `311030 % 18` ✗

## Five Whys Analysis

### Why did the branch produce wrong answers?
The LLM generated code with condition ordering bugs, where a general pattern match (`'modulo' in text`) executed before a more specific one (`'integer-divided' in text and 'modulo' in text`).

### Why did the LLM generate buggy ordering?
The branch's "more structured" context format (`.stdout`, `.error`, attributes) appears to prime LLMs toward writing more structured code (helper functions, early returns), but this structure doesn't naturally enforce the careful `elif` chains needed for pattern matching.

### Why does the new format encourage this pattern?
The removal of the "Execution successful.\nStdout:\n" prefix creates a more "machine-like" presentation. The explicit `status` attribute may signal that outputs are pre-categorized, reducing the LLM's attention to careful output interpretation.

### Why does this affect some models more than others?
- **gpt-oss-120b**: -50% regression suggests high sensitivity to formatting cues
- **claude-sonnet**: 0% regression suggests robustness to formatting changes
- Models with stronger code pattern recognition may be less affected by surface formatting

### Why is pattern ordering critical for this task?
The `calculate_batch` task requires parsing 10+ different calculation patterns from natural language. Correct handling requires:
1. Checking specific patterns before general ones
2. Using mutually exclusive `elif` chains
3. Careful text matching with proper precedence

## Causal Chain Summary

```
Context Format Change
         ↓
"Execution successful.\nStdout:" prefix removed
         ↓
Context appears more "structured/machine-like"
         ↓
LLM generates more "structured" code (helper functions, early returns)
         ↓
Separate `if` statements instead of `elif` chains
         ↓
General patterns match before specific patterns
         ↓
Wrong results for compound operations (integer-div + modulo)
         ↓
15 regressions, 9 net failures
```

## Recommendations

1. **Restore the content prefixes**: The "Execution successful.\nStdout:\n" and "Execution error:\n" prefixes appear to encourage more careful output handling.

2. **A/B test context formats**: Before deploying context format changes, run capability tests to catch regressions early.

3. **Consider model-specific formatting**: gpt-oss-120b's 50% drop suggests it may need different context formatting than claude-sonnet.

4. **Add pattern-matching test coverage**: The ordering bug pattern should be explicitly tested with adversarial inputs.

---

## Trace Explorer Feedback

### What Worked Well
- `TraceExplorer.diff()` immediately identified the expression path differences
- `get_turn()` showed exact context windows for comparison
- `get_session_data()` exposed the actual executed code
- Pass/fail summary per trace was clear and actionable

### Areas for Improvement
1. **Batch comparison mode**: Comparing 60 traces one-by-one is tedious. A batch mode like `TraceExplorer.batch_diff(dir1, dir2)` would help.

2. **Code pattern analysis**: Built-in detection for common bug patterns (ordering bugs, missing edge cases) would accelerate RCA.

3. **Eval context data**: `get_eval_context_data()` returned empty for these traces. The API should surface expected/actual outputs in structured form.

4. **Output diff visualization**: A side-by-side output comparison like `[19635204, 576, 2, 39, 17287, ...] vs [19635204, 576, 2, 39, 8, ...]` with differences highlighted.

5. **Aggregate statistics**: A summary of "N traces with ordering bug pattern" would be useful for systematic analysis.

### API Usage Notes
- Session list returns objects with `.session_id` attribute (not dict)
- Turn data structure alternates: even indices = execution, odd = LLM
- Code is in `turn['code']` not `turn['tool_call']['code']`

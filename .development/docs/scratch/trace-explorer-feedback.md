# Trace Explorer Feedback

**Date**: 2026-01-27
**Context**: Used trace_explorer extensively for capability test regression analysis

---

## What Worked Well

### 1. `get_overview()` - Excellent Entry Point

The overview provides exactly what's needed to quickly assess a trace:

```
# router_validate_002_nemotron3-nano-30b_run10 - RouterTestWrapper.process()  [FAILED]

Duration: 42.8s | Sessions: 1 | Turns: 13 | Runtime Errors: 1 | Eval: FAILED

## Call Graph
RouterTestWrapper.process [17e281] ───────────────────── 13t 42751.0ms [ERR]
  IN:  user_message='check if this is valid', values=[-1, 2, 2, 5]
  ERR: return_result validation failed after 3 attempts...
```

**Strengths**:
- Call graph is visually clear and hierarchical
- Input/Output/Error shown inline
- Duration and turn count immediately visible
- `[FAILED]` / `[PASSED]` prominently displayed

### 2. `get_errors()` - Fast Error Triage

Immediately shows all errors with context:

```
Found 7 error(s):

  [17e281] status_error
    return_result validation failed after 3 attempts...
    Context: RouterTestWrapper.process()

  [17e281, turn 2] RestrictedCodeError
    import of 'pprint' is forbidden...
```

**Strengths**:
- Groups errors by session and turn
- Shows error type prominently
- Includes context about where error occurred

### 3. `get_session()` - Clear Turn-by-Turn Flow

The XML-like format for showing turns is readable:

```xml
<turn n="0" duration="3137.0ms" status="[ERR]">
  <user>
    <task expr="self.history[0].prompt">...</task>
  </user>
  <assistant>
    <tool_call name="execute_python" id="prefill_e69c3a5a">...</tool_call>
  </assistant>
  <tool_response id="prefill_e69c3a5a" status="[ERR]">...</tool_response>
</turn>
```

**Strengths**:
- XML structure makes role boundaries clear
- Tool calls and responses paired together
- Status badges on each turn
- Duration per turn helps identify slow operations

### 4. `get_turn()` - Full LLM Context When Needed

Being able to see the exact system prompt and context at any turn is valuable for debugging prompt issues.

---

## Issues and Suggestions

### Issue 1: `get_eval_context()` Output is Too Sparse

**Current output**:
```
# Evaluation Context

## Result: FAILED

## Scorer Results
- **result_check** [FAIL]: Output mismatch
```

**Problem**: Doesn't show what the actual vs expected values were.

**Suggestion**: Include the actual comparison:
```
# Evaluation Context

## Result: FAILED

## Scorer Results
- **result_check** [FAIL]: Output mismatch
  Expected: {'agents_called': ['Validator'], 'results': {...}}
  Got:      {'all_positive': False, 'is_sorted': True}
```

### Issue 2: Session IDs Not Discoverable

The short hex IDs like `17e281` are used throughout but there's no obvious way to discover them without calling `get_overview()` first.

**Suggestion**: Add `get_session_ids()` or `list_sessions()` method that just returns the IDs with minimal context.

### Issue 3: No Built-in Diff Capability

When comparing two traces (main vs branch), I had to manually load both and compare outputs.

**Suggestion**: The CLI has `--diff` but I didn't see a programmatic equivalent:
```python
diff = trace1.diff(trace2)  # Compare two traces
diff.get_prompt_differences()  # Show system prompt diffs
diff.get_error_differences()   # Show error pattern diffs
```

### Issue 4: Tool Response Content Sometimes Opaque

Tool responses sometimes just show `'<object object at 0x10a81bd20>'`:

```
<tool_response id="call_682448b..." status="[OK]">
  '<object object at 0x10a81bd20>'
</tool_response>
```

**Problem**: This is the raw object repr, not helpful for debugging.

**Suggestion**: If value is an object, try to show `brief(value)` or at least `type(value).__name__`.

### Issue 5: Large Traces Need Pagination/Filtering

For traces with many turns (10+), the output can be overwhelming.

**Suggestions**:
- `get_session(id, turns=slice(5, 10))` - only show turns 5-9
- `get_session(id, errors_only=True)` - only show turns with errors
- `get_session(id, last_n=3)` - only show last 3 turns

### Issue 6: Expression References Could Link Better

The `expr="self.history[0].prompt"` attributes are informative but I can't easily evaluate them.

**Suggestion**: Add helper to evaluate expressions:
```python
trace.eval_expr("17e281", "self.history[0].prompt")  # Get actual value
```

---

## Feature Requests

### 1. Pattern Detection / Statistics

For analyzing multiple traces (like capability runs), it would help to have:

```python
# Analyze multiple traces at once
analyzer = TraceAnalyzer.from_directory("traces/")
analyzer.get_common_errors()  # Most frequent error types
analyzer.get_slow_turns()     # Turns > 5s
analyzer.get_retry_patterns() # Cases with 3+ error retries
```

### 2. Prompt Comparison

For debugging prompt regressions:

```python
trace1.get_system_prompt("session_id")
trace2.get_system_prompt("session_id")
# Or: trace1.diff_prompts(trace2)
```

### 3. Token Usage Summary

```python
trace.get_token_usage()
# Returns: {'input': 15234, 'output': 2341, 'total': 17575}
```

### 4. Search Across Traces

```python
TraceExplorer.search_directory("traces/", pattern="import json")
# Returns list of traces containing this pattern
```

---

## Minor UI/Formatting Suggestions

1. **Consistent status indicators**: Sometimes `[ERR]`, sometimes `[FAIL]`, sometimes `status="error"`. Standardize?

2. **Truncation indicators**: When content is truncated, show `... (truncated, 2341 more chars)` instead of just `...`

3. **Color support**: For terminal output, color-code errors red, success green, warnings yellow

4. **JSON output mode**: The `--json` CLI flag is great. Consider making all methods return dataclasses that can be serialized:
   ```python
   overview = trace.get_overview(as_dict=True)  # Returns dict instead of string
   ```

---

## Overall Assessment

**Rating: 8/10**

The trace_explorer is genuinely useful for debugging. The hierarchical overview, error aggregation, and turn-by-turn breakdown saved significant time during analysis.

The main gaps are around:
1. Comparative analysis (diff between traces)
2. Richer eval context output
3. Bulk analysis across multiple traces

For single-trace debugging, it's excellent. For regression analysis across many traces, additional tooling would help.

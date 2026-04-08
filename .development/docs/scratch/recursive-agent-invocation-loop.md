# Recursive Agent Invocation Loop

## Pattern Description

A failure mode where an agent recursively invokes itself (or creates a deep chain of nested agent calls) without proper termination, leading to:
- Resource exhaustion
- Timeout errors
- Hundreds of AGENT spans in the trace

## Detection

**Symptoms:**
1. Trace contains many more AGENT spans than expected (e.g., 108 AGENT spans for what should be 1-2 sessions)
2. Most AGENT spans have `generation.parent_id` set, indicating nested calls
3. Session often ends with execution timeout
4. Message count grows linearly in LLM turns (accumulating context from recursive calls)

**Example Trace:**
- File: `NeedleTestWrapper_gemini-2.5-flash-lite_call_agent_needle_in_haystack_20260108_102810_03_000000.006trace.jsonl`
- 108 AGENT spans detected in raw trace
- Only 1 root session visible (parser filters out nested sessions)
- Execution timeout after 90 seconds
- 112 turns with empty LLM responses

## Root Cause

The agent code invokes `self.call_agent()` which starts a nested agent session. If the nested session:
1. Also generates code that calls `self.call_agent()` again
2. Doesn't properly return or terminate
3. Keeps recursing until timeout

This creates a call stack like:
```
NeedleTestWrapper.find_negative_sentiment()
  └─> call_agent() [session 1]
       └─> call_agent() [session 2]
            └─> call_agent() [session 3]
                 └─> ... (continues 108 times)
```

## Parser Limitation

The current trace parser in [trace_to_markdown.py:220](util/e2e_optimization/src/e2e_optimization/trace_to_markdown.py#L220) only processes root sessions:

```python
# Only consider root generation spans (no parent generation)
if not parent_gen_id:
    key = short_id(gen_id)
    if key not in gen_id_to_spans:
        gen_id_to_spans[key] = []
    gen_id_to_spans[key].append(span)
```

This causes:
- Nested sessions are not parsed into AgentSession objects
- TraceExplorer only shows 1 session
- Recursive call pattern is hidden from mechanical checks

## Mechanical Check

Created `ExcessiveAgentRecursionCheck` to detect this pattern by:
1. Counting total AGENT spans in raw trace
2. Comparing to number of parsed sessions
3. If ratio > threshold (e.g., 5x), flag as recursive invocation loop

## Prevention

1. Ensure nested agent calls have proper termination conditions
2. Limit recursion depth in agent runtime
3. Monitor AGENT span count during execution

## Related Issues

- [Nested Agent History Bug](nested-agent-history-ordering-bug.md) - Different issue about child session ordering

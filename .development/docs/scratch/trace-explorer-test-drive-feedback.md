# Trace Explorer Test Drive Feedback

**Date:** Mon Jan 26 2026
**Reviewer:** Agent
**Package:** `packages/trace_explorer`

## Executive Summary

I test drove the `trace_explorer` CLI and Python package using:
1. Fixture traces in the repository (initial testing)
2. **Actual regression traces** from `/Volumes/dev/dev/agent006/results/` and `/Volumes/dev/dev/viewer/results/` (RCA analysis)

**Overall Assessment:** The trace_explorer is a **well-designed, agent-friendly interface** that **successfully enabled root cause analysis**. The hierarchical navigation pattern (overview → session → turn) is intuitive and the output formatting is clean and informative.

**RCA Result:** Using trace_explorer, I identified the root cause of the regression: **history expression path changes in prompt rendering** (`self.history[0].prompt` vs `self.history.events[0].content`) caused different LLM behavior.

---

## Root Cause Analysis Result

Using trace_explorer, I analyzed a specific regression case:
- **Test:** `router_validate_002_qwen3-80b_run5`
- **MR:** FAILED (1 session, 5 turns, `agents_called: []`)
- **Main:** PASSED (2 sessions, 4 turns, `agents_called: ['Validator']`)

### How trace_explorer helped:

1. **Overview** immediately showed the eval status and call graph differences
2. **Session view** showed the MR had 5 turns vs main's 4 turns
3. **Turn view** revealed the exact prompts and LLM outputs

### Prompt Differences Found:

| Element | MR (failed) | Main (passed) |
|---------|-------------|---------------|
| Task expr | `self.history[0].prompt` | `self.history.events[0].content` |
| Tool result | `self.history[2].content` | `self.history.events[2]` |
| Execute result | `self.history[3].stdout` | `self.history.events[3].content` |

### Behavioral Impact:

Same model (qwen3-80b), same input, but different LLM-generated code:
- **Main**: Generated fallback logic that called validator
- **MR**: Generated code that printed error and returned empty result

This confirms the RCA prompt hypothesis: history/context rendering changes altered the prompts, causing different LLM behavior.

---

## Test Cases Analyzed

### 1. `analyzer_debug.006trace.jsonl`
- Agent: `TraceAnalyzerAgent.diagnose()`
- Sessions: 1, Turns: 3
- Error: Context window exceeded
- **Observation:** Error detection worked well, context was clearly shown

### 2. `execution_error_forbidden_import.006trace.jsonl`
- Agent: `CalculateSingleAgent.calculate()`
- Sessions: 1, Turns: 4
- Error: Forbidden import (math)
- **Observation:** Excellent error context, recovery pattern visible

### 3. `excessive_agent_recursion.006trace.jsonl`
- Agent: `RecursiveAgent.recursive_method()`
- Sessions: 15 (nested), Turns: 0
- **Observation:** Call graph visualization excellent for recursion patterns

### 4. `return_result_validation_error.trace.jsonl`
- Agent: `SentimentSingleAgent.classify()`
- Sessions: 1, Turns: 4
- Error: Pydantic validation error
- **Observation:** Validation error clearly surfaced

---

## What Helped with RCA

### Strengths

1. **Hierarchical Navigation Pattern**
   - `get_overview()` → `get_session()` → `get_turn()` is exactly the right mental model
   - Session IDs are short (6 chars) and easy to copy
   - Navigation hints at the bottom of each output guide next steps

2. **Call Graph Visualization**
   - ASCII art tree structure is clear and readable
   - Shows turn count, duration, and status at a glance
   - Nested sessions (recursion) are immediately visible
   - Input/output previews help identify which invocation to investigate

3. **Error Detection & Display**
   - `get_errors()` aggregates all errors across sessions
   - Error type (e.g., `PlanningCodeViolation`, `ValidationError`) is captured
   - Context includes the code that caused the error
   - Error chain for failed sessions is available

4. **Turn-Level Detail**
   - `get_turn()` output is self-documenting with headers explaining what you're looking at
   - Shows full LLM context window including system prompts
   - Tool calls are formatted cleanly with XML-style tags
   - Tool responses show status (`[OK]` / `[ERR]`)

5. **Search Functionality**
   - `search(pattern)` returns matches with location context
   - Summary by match type (message, code, error, etc.) is helpful
   - Truncated matches show enough context to understand

6. **Python API Consistency**
   - Same methods available as CLI
   - Returns strings (easy for agents to process)
   - `help()` method provides inline documentation
   - Direct access to `sessions` list for programmatic analysis

7. **Output Formatting**
   - Clean markdown formatting
   - Truncation is indicated clearly (e.g., `'text'+42`)
   - Duration in milliseconds is useful for performance analysis
   - Token counts shown when available (`tokens=1770→148`)

### CLI Usability

```bash
# All of these worked as expected
trace-explorer file.jsonl                    # Overview
trace-explorer file.jsonl --session abc123   # Session details
trace-explorer file.jsonl --session abc --turn 0  # Turn details
trace-explorer file.jsonl --errors           # All errors
trace-explorer file.jsonl --search "pattern" # Search
trace-explorer file.jsonl --api-help         # Show API guide
trace-explorer file.jsonl --verbose          # Full details
```

---

## What Was Missing or Could Be Improved

### 1. **Trace Comparison / Diff Feature** (Medium Priority for RCA)

While I successfully compared traces by running trace_explorer on both files, a built-in diff would streamline this:
- Automated MR vs main comparison
- Highlight prompt differences automatically
- Show first point of divergence

**Suggestion:** Add `TraceExplorer.diff(trace1, trace2)` or CLI `--diff other.jsonl`

**Note:** Even without this, I was able to do effective RCA by comparing outputs manually.

### 2. **Prompt-Level Diff Detection** (High Priority)

From the RCA prompt, the root cause was changes in prompt rendering:
- `<task expr="...">` path changed
- `doc(self)` output missing method signatures

The trace_explorer shows the full prompt but doesn't highlight:
- What's different from a baseline
- Token count delta
- Missing expected sections

**Suggestion:** Add prompt fingerprinting or section detection

### 3. **Raw Span Access** (Medium Priority)

Sometimes I need to see the raw span attributes:
- `generation.parent_id` for understanding generation hierarchy
- `tool_call_id` for correlating tool calls with responses
- Full `llm.invocation_parameters`

The formatted output is great but occasionally I need the raw data.

**Suggestion:** Add `get_raw_span(span_id)` or `--raw` flag

### 4. **Evaluation Context Display** (Medium Priority)

`get_eval_context()` returned "No evaluation result provided" for all traces.

For capability eval regression analysis, I need:
- Expected output
- Actual output
- Score/pass-fail
- Scorer reasoning

**Suggestion:** The API exists but the fixture traces didn't have eval data. Document how to load traces with eval results.

### 5. **Warning Noise** (Low Priority)

Some traces emit warnings during loading:
```
UserWarning: Session b2b989: Used time-range fallback to find 1 generation spans (may be inaccurate)
```

These are helpful for debugging the parser but noisy for RCA work.

**Suggestion:** Add `--quiet` flag or log level control

### 6. **Batch Analysis** (Low Priority for CLI, High for API)

For analyzing multiple test runs (e.g., 142 regressions), I need:
- Load multiple traces
- Filter by error type
- Aggregate statistics
- Export regression list

**Suggestion:** Add `TraceExplorer.from_directory(path)` or batch processing utilities

### 7. **Time-Based Navigation** (Low Priority)

For long traces, navigating by turn index requires knowing the count. Timeline-based navigation could help:
- Jump to first error
- Jump to final turn before failure
- Show turns around a specific timestamp

**Suggestion:** Add `get_timeline()` or `find_first_error()` (may already exist on TraceExplorer)

---

## Did I Need to Read Raw Traces?

**Mostly no.** For the actual RCA:

1. **trace_explorer was sufficient** to identify the root cause
2. The `get_turn()` output showed the full LLM context including expr paths
3. I could compare MR vs main by running trace_explorer on both files

I read raw JSONL early on (fixture traces) to understand span structure, but for the actual regression analysis, **trace_explorer provided everything I needed**.

The key that made this work was the **turn-level detail showing expr paths** in the XML tags (e.g., `<task expr="self.history[0].prompt">`). This immediately revealed the prompt rendering difference.

---

## What I Liked

1. **Agent-Oriented Design**
   - Help text explains the navigation pattern
   - Outputs are sized appropriately for agent context windows
   - Concise vs full detail toggle is useful
   - Methods return strings (easy for agents to process and reason about)

2. **Progressive Disclosure**
   - Overview shows just enough to decide where to drill down
   - Session view shows turn summaries
   - Turn view shows full context
   - Never overwhelmed with data

3. **Clear Status Indicators**
   - `[OK]` / `[ERR]` labels are consistent
   - Turn index in error messages (`[e1cffa, turn 1]`)
   - Session IDs are always 6 characters

4. **XML-Style Formatting in Turns**
   - The `<llm_turn>`, `<user>`, `<assistant>`, `<tool_call>`, `<tool_response>` structure is very readable
   - Status included in response tags `status="[ERR]"`
   - Model and parameters visible in context

5. **Call Graph for Nested Agents**
   - The recursive agent trace showed 15 nested sessions
   - Tree visualization made the pattern immediately obvious
   - Would be excellent for debugging child agent issues

---

## What Was Confusing

1. **Turn Numbering vs Types**
   - Some sessions show 4 turns but only 2 are displayed in `get_session()`
   - The relationship between LLM turns and execution turns isn't always clear
   - Prefill flow mentioned in help but not demonstrated

2. **Session ID Sources**
   - Session ID comes from span_id (first 6 chars)
   - Generation ID is different from session ID
   - When traces use time-range fallback, IDs might not match expectations

3. **Missing Turns in Some Traces**
   - Recursion trace showed "0t" for all sessions
   - Parser fallback warning suggests this might be a parsing issue vs actual missing turns

---

## Recommendations Summary

| Priority | Recommendation | Rationale |
|----------|----------------|-----------|
| High | Add trace diff/comparison | Essential for regression RCA |
| High | Document eval context loading | Needed for capability eval analysis |
| Medium | Add raw span access | Sometimes need underlying data |
| Medium | Add batch processing | Analyzing 100+ regressions |
| Low | Add --quiet flag | Reduce warning noise |
| Low | Add timeline navigation | Help with long traces |

---

## Conclusion

The `trace_explorer` package is a **solid foundation** for agent-driven trace analysis. For single-trace debugging, it provides everything needed. For **regression analysis** (comparing MR vs main), a **diff capability** would be the most impactful addition.

The API design is clean and the output formatting is well-suited for both human and agent consumption. With the additions suggested above, this would be an excellent tool for systematic capability evaluation debugging.

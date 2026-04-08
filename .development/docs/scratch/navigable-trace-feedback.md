# TraceExplorer API Feedback

**Evaluation Date**: 2026-01-26 (Updated)
**Evaluator**: Claude (Agent perspective)
**Purpose**: Evaluate TraceExplorer outputs for agent usability in root cause analysis

## Evaluation Criteria

For each method output, I'm evaluating:
1. **Understandability**: Can I parse and understand the output without additional context?
2. **Self-documenting**: Does the output explain what I'm looking at?
3. **Actionability**: Does this give me the info I need to drill down and root cause?

---

## Executive Summary

**Overall Assessment: VERY GOOD**

TraceExplorer provides a well-designed API for agent-driven trace analysis. The output is generally understandable and actionable. The recent updates (2026-01-26) addressed many prior issues including:
- Replaced confusing ✓/✗ icons with clear [OK]/[ERR]/[PASS]/[FAIL] labels
- Added context headers to `get_session()`
- Made `concise=True` show dramatically less content
- Improved error display with context
- Added `help()` method for discoverability
- Removed broken `what_went_wrong()` and `diff()` methods

### Remaining Issues (Prioritized)
1. **`get_turn()` doesn't show LLM context window** - Only shows execution results, not what the LLM saw
2. **`get_eval_context()` missing comparison** - Doesn't show expected vs actual values
3. **Truncation indicator `+N` is confusing** - e.g., `'text'+4` looks like string concatenation
4. **`Returned: '<object object at 0x...>'`** - Not useful information
5. **Missing output (OUT) in some views** - Verbose mode sometimes doesn't show return values

---

## Method-by-Method Analysis

### 1. `get_overview(concise=True/False)`

**Sample Output (concise=True)**:
```
# SentimentSingleAgent.classify()

Duration: 26.5s | Sessions: 1 | Turns: 2

## Call Graph
SentimentSingleAgent.classify [e0d916] ─────────────────  2t 26471.1ms [OK]
  IN:  text='I absolutely love this product, it exceeded all my expectati'+4

## Navigation
→ get_overview(concise=False) - Full I/O details
→ get_session('e0d916') - Root session details
```

**Sample Output (complex trace, concise=True)**:
```
# dabstep_49_easy - DABStepAgent._run_evaluation()  [PASSED]

Duration: 548.5s | Sessions: 5 | Turns: 95 | Eval: PASSED

## Call Graph
DABStepAgent._run_evaluation [7e31b7] ────────────────── 19t 182914.8ms [OK]
│ IN:  task_input={'system_prompt', 'user_message', 'question', ... +4}
│ OUT: {'response': 'B. BE', 'success': True, 'result': 'B. BE', 'answer': 'B. BE', 'error': None}
│ EVAL: [PASS] 1.0 (benchmark_scorer)
└── DABStepAgent.run_evaluation [241fa8] ─────────────── 19t 182780.1ms [OK]
  IN:  task=DABStepTask(question='What is the top country (ip_country) for'+34, ...)
  OUT: ComputedAnswer(answer='B. BE', explanation='Based on the relevant rules...')
    ├── RulesLawyer.find_rules [287979] ──────────────── 25t 72357.0ms [OK]
    │ IN:  question='What is the top country (ip_country) for fraud?...'
    │ OUT: { 'FRAUD_DEFINITION': "rule='Fraud is defined as...", ...}
    ...
```

#### Strengths ✓
- **Clear [OK]/[ERR]/[PASS]/[FAIL] labels** - No more confusing icons
- **Excellent call graph** - Shows hierarchy, timing, session IDs, turn counts
- **Inline I/O preview** - Shows inputs/outputs at a glance
- **Eval results inline** - Shows which scorer passed/failed
- **Stats line separates runtime errors from eval** - `Sessions: 5 | Turns: 95 | Eval: PASSED`
- **Navigation hints** - Points to next actions

#### Issues ✗

**Issue 1: Truncation indicator `+N` is confusing**
```
IN:  text='I absolutely love this product, it exceeded all my expectati'+4
```
The `+4` looks like Python string concatenation, not "4 more characters truncated."

**Recommendation**: Use clearer truncation:
```
IN:  text='I absolutely love this product, it exceeded all my expectati...' (68 chars)
```
Or:
```
IN:  text='...all my expectations!' [truncated from 68 chars]
```

**Issue 2: Simple trace concise=False doesn't show OUT**
For the simple SentimentSingleAgent trace, concise=False shows only IN, not OUT:
```
## Call Graph
SentimentSingleAgent.classify [e0d916] ─────────────────  2t 26471.1ms [OK]
  IN:  {'text': 'I absolutely love this product, it exceeded all my expectations!'}
```

But for complex traces, OUT is shown. This inconsistency is confusing.

**Recommendation**: Always show OUT in verbose mode, even if it's `OUT: 'positive'` (the return value).

---

### 2. `get_session(session_id, concise=True/False)`

**Sample Output (concise=True)**:
```
# Trace: SentimentSingleAgent.classify()
# Context: Root session

SentimentSingleAgent.classify [e0d916] (CODEACT) ───────  2t 26471.1ms [OK]
IN:  text='I absolutely love this product, it exceeded all my expectations!'

## Turns (2 total)
  Turn 0: [EXEC] reasoning("""Let me inspect the inputs. → [OK]
  Turn 1: [EXECUTE]  → [OK] (26452ms)

## Navigation
→ get_turn('e0d916', 0) - Full LLM context at turn 0
```

**Sample Output (concise=False)**:
```
# Trace: SentimentSingleAgent.classify()
# Context: Root session

SentimentSingleAgent.classify [e0d916] (CODEACT) ───────  2t 26471.1ms [OK]
IN:  {'text': 'I absolutely love this product, it exceeded all my expectations!'}

<turn n="0" duration="26452.4ms" status="[OK]">
  <user>
    <task expr="self.history.events[0].content">
    # Task: Classify the sentiment of a single text.
    ...
    </task>
  </user>
  <assistant>
    <tool_call name="execute_python" id="prefill_65e0555d">
      reasoning("""Let me inspect the inputs...
    </tool_call>
  </assistant>
  <tool_response id="prefill_65e0555d">
    <tool_result expr="self.history.events[2]">
    status: complete
    </tool_result>
  </tool_response>
  ...
</turn>
```

#### Strengths ✓
- **Context header added** - Shows "Trace: X" and "Context: Root session"
- **Dramatic difference between concise modes** - concise=True shows turn summaries; concise=False shows full turns
- **Strategy visible** - Shows `(CODEACT)`
- **XML turn structure** - Clear demarcation of conversation flow
- **Tool call/response pairing** - Shows request-response pairs with IDs

#### Issues ✗

**Issue 3: Turn summary in concise mode is truncated awkwardly**
```
Turn 0: [EXEC] reasoning("""Let me inspect the inputs. → [OK]
```
The triple-quote is opened but not closed. This looks like malformed output.

**Recommendation**: Better summarization:
```
Turn 0: [EXEC] reasoning("Let me inspect...") → [OK]
```

---

### 3. `get_turn(session_id, turn_index)`

**Sample Output**:
```
<exec_turn n="0" duration=0.4ms status="[OK]">
  <tool_call name="execute_python" id="prefill_65e0555d">
    reasoning("""Let me inspect the inputs.

    Reminders:
    - Do not call self.classify() - infinite recursion
    - Do not redefine classify - I am implementing it
    - Return directly if possible""")
    print(f"Call: {_call.format_signature()}")
    print(f"\ntext ({type(text).__name__}):")
    pprint(text, max_length=50, max_string=500, max_depth=4)
  </tool_call>
  <tool_response id="prefill_65e0555d" status="[OK]">
    Call: async def classify(self, text: str) -> str

    text (str):
    'I absolutely love this product, it exceeded all my expectations!'
    Returned: '<object object at 0x10a5db320>'
  </tool_response>
</exec_turn>
```

#### Strengths ✓
- **Shows execution details** - Code, stdout, and result
- **Tool call IDs maintained** - Can correlate request/response

#### Issues ✗

**Issue 4: get_turn doesn't show LLM context window** *(RESOLVED)*

The docstring says: "Get full context window, LLM response, and execution output for a specific turn."

But the actual output only shows the execution turn (`<exec_turn>`), NOT the LLM context window. The LLM context (what messages the LLM saw, what system prompt it had) is missing.

This was the most critical issue. An agent trying to understand "why did the LLM make this decision?" cannot see what the LLM saw.

**Resolution (2026-01-26)**: Fixed. `get_turn()` now shows LLM context from the preceding OR following turn (for prefill strategies). See "Fixed" section at end of document.

**Issue 5: `Returned: '<object object at 0x...>'` is useless**
```
Returned: '<object object at 0x10a5db320>'
```
This tells me nothing about what was actually returned.

**Recommendation**:
- If the return is a simple value, show it: `Returned: 'positive'`
- If it's complex, show a repr: `Returned: SentimentResult(label='positive')`
- If it's truly unhelpful, omit it or show `Returned: (non-displayable object)`

---

### 4. `get_errors()`

**Sample Output**:
```
Found 1 error(s):

  [e1cffa, turn 1] PlanningCodeViolation
    Line 2: import of 'math' is forbidden.

Available in scope: Agent, CalculateSingleAgent, CodeActStrategy, CompositeStrategy,
PurePythonStrategy, ReflexionStrategy, StructuredOutputStrategy, TemplateStrategy,
a, asyncio, b, brief, calculation, doc, message, methods, print, reasoning,
return_result, ...
    Context: Code:
import math

a = 983688
b = 827636

# Compute GCD using the math.gcd function
result = math.g


→ get_session('<session_id>') to see full execution details
```

#### Strengths ✓
- **Clear error location** - `[e1cffa, turn 1]`
- **Error type visible** - `PlanningCodeViolation`
- **Helpful context** - Shows "Available in scope" for forbidden import errors
- **Code snippet** - Shows the failing code
- **Navigation hint** - Points to get_session for more details

#### Issues ✗

**Issue 6: "Available in scope" line is very long**

The scope list goes on for many items. For readability, consider truncating:
```
Available in scope: Agent, CalculateSingleAgent, ..., return_result (15 more)
```

---

### 5. `get_eval_context()`

**Sample Output**:
```
# Evaluation Context

## Result: PASSED

## Scorer Results
- **benchmark_scorer** [PASS]:
```

#### Strengths ✓
- **Clear pass/fail per scorer** - Shows which scorer passed/failed

#### Issues ✗

**Issue 7: No input/expected/actual comparison**

For debugging evaluation failures, I need to see:
- What was the input?
- What was expected?
- What was actual?
- Why did the scorer pass/fail?

The current output shows only the result, not the comparison.

**Recommendation**: Add comparison section:
```
# Evaluation Context

## Result: PASSED

## Input
question: "What is the top country for fraud? A. NL, B. BE, C. ES, D. FR"

## Expected
"B. BE"

## Actual
"B. BE"

## Scorer Results
- **benchmark_scorer** [PASS]: Exact match
```

**Issue 8: No explanation after [PASS]**
```
- **benchmark_scorer** [PASS]:
```
The colon suggests there should be an explanation, but it's empty.

---

### 6. `search(pattern)`

**Sample Output**:
```
Found 2 match(es) for 'love':

Summary:
  • stdout: 1
  • message[user]: 1

Matches:
  • [e0d916 t0] stdout: ...tr  text (str): 'I absolutely love this product, it ex...
  • [e0d916 t1] message[user]: ...tr  text (str): 'I absolutely love this product, it ex...

→ get_session('<session_id>') to see full execution details
```

#### Strengths ✓
- **Category summary** - Shows where matches are concentrated
- **Location references** - Session + turn for each match
- **Ellipsis centering** - Match is centered with `...` on both sides
- **Navigation hint** - Points to get_session

#### Issues ✗

**Issue 9: Context truncation includes non-content characters**
```
• [e0d916 t0] stdout: ...tr  text (str): 'I absolutely love this product, it ex...
```
The `...tr  text` fragment is confusing. It looks like it's showing partial words.

**Recommendation**: Ensure truncation happens at word boundaries when possible:
```
• [e0d916 t0] stdout: ...text (str): 'I absolutely love this product, it...'
```

---

### 7. `help()`

**Sample Output**:
```
# TraceExplorer API Guide

## Quick Start
Use these methods to investigate agent traces and find root causes.

## Core Methods

### 1. get_overview(concise=True)
Start here. Shows the call graph with inputs, outputs, and errors.
- concise=True: Compact view with truncated I/O (~500 chars max)
- concise=False: Full details with 10-20x more content

### 2. get_session(session_id, concise=True)
Drill into a specific session to see all turns.
- session_id: 6-character ID from call graph (e.g., '278a10')
- concise=True: Turn summaries only (one line per turn)
- concise=False: Full turn content with messages and tool calls

### 3. get_turn(session_id, turn_index)
See exact LLM context and execution output for one turn.
- Shows: [CONTEXT] → [LLM OUTPUT] → [EXECUTION RESULT]
- Use this to understand why the LLM made a specific decision

### 4. get_errors()
List all errors in the trace with context.
- Shows error chain for failed sessions
- Points to specific turns where errors occurred

### 5. get_eval_context()
See evaluation inputs, expected outputs, and scorer results.
- Use this to understand why a trace failed evaluation

### 6. search(pattern)
Find occurrences of a pattern across all trace content.
- Searches messages, code, stdout, and responses
- Returns matches with session/turn location

## Navigation Pattern

1. Start with `get_overview()` to see the big picture
2. Find a failed session in the call graph
3. Use `get_session(id)` to see turn-by-turn execution
4. Use `get_turn(id, n)` to see exact LLM context at turn n
5. Use `get_eval_context()` if it's an eval failure
6. Use `get_errors()` to see all errors at once

## Status Labels
- [OK] - Session/turn completed successfully
- [ERR] - Session/turn had a runtime error
- [PASS] - Evaluation passed
- [FAIL] - Evaluation failed
```

#### Strengths ✓
- **Comprehensive** - Covers all methods with examples
- **Navigation pattern** - Shows the recommended workflow
- **Status labels explained** - Clear meaning for each label

#### Issues ✗

**Issue 10: get_turn description doesn't match reality**
```
### 3. get_turn(session_id, turn_index)
See exact LLM context and execution output for one turn.
- Shows: [CONTEXT] → [LLM OUTPUT] → [EXECUTION RESULT]
```

The actual output doesn't show [CONTEXT] (the LLM's message history). The help text promises something the method doesn't deliver.

---

## Cross-Cutting Issues

### Issue 11: Session IDs are opaque

Session IDs like `e0d916` are meaningless. Consider also accepting method names for convenience:

```python
trace.get_session('SentimentSingleAgent.classify')  # Get first matching session
trace.get_session('e0d916')  # Or by ID
```

---

## Recommendations Summary

### Fixed (2026-01-26)
1. ~~**Fix get_turn() to show LLM context window**~~ - FIXED. Now shows LLM context from preceding OR following turn (for prefill strategies)
2. ~~**Update help() text**~~ - FIXED. Now accurately describes get_turn() behavior for both LLM and execution turns
3. ~~**Document truncation indicator**~~ - FIXED. Added "Truncation Format" section to help() explaining pprint syntax

### Should Fix (High Value)
4. **Add input/expected/actual to get_eval_context()** - Essential for debugging eval failures (eval_result dict needs to include these fields)
5. **Fix unhelpful `Returned: '<object object at 0x...>'`** - Requires fix in agent tracing code, not TraceExplorer
6. **Always show OUT in verbose mode** - Inconsistent whether return value is shown

### Nice to Have
7. **Better turn summary formatting** - Don't show unclosed triple quotes
8. **Truncate "Available in scope" list** - Very long list hurts readability
9. **Word-boundary truncation in search** - Avoid `...tr  text` fragments
10. **Support method names as session selectors** - More intuitive than hex IDs
11. **Add explanation after scorer [PASS]/[FAIL]** - Empty colon looks like a bug

---

## Conclusion

TraceExplorer has significantly improved since the initial review. The key improvements include:
- Clear [OK]/[ERR]/[PASS]/[FAIL] labels instead of confusing icons
- Context headers in get_session()
- Dramatic difference between concise=True and concise=False modes
- Helpful help() method for discoverability

The most critical remaining issue is that **get_turn() doesn't show the LLM context window** as documented. An agent cannot understand "why did the LLM make this decision?" without seeing what the LLM saw. This is the highest priority fix.

After addressing the "Must Fix" items, TraceExplorer will be well-suited for agent-driven trace analysis.

---

## Test Traces Used

1. **SentimentSingleAgent_qwen3-80b** - Simple successful trace, 1 session, 2 turns
   - Path: `results/capability_20260115_103006/traces/SentimentSingleAgent_qwen3-80b_classify_sentiment_single_20260115_103006_01_000000_qwen3-80b.006trace.jsonl`

2. **CalculateSingleAgent execution error** - Trace with forbidden import error
   - Path: `util/e2e_optimization/src/e2e_optimization/mechanical_checks/fixtures/execution_error_forbidden_import.006trace.jsonl`

3. **DABStep 49 easy** - Complex trace with 5 sessions, 95 turns, nested agent calls
   - Path: `results/20260122_183234/traces/dabstep_49_easy_550740bd.006trace.jsonl`

4. **SentimentBatchAgent_qwen3-80b** - Batch processing trace
   - Path: `results/capability_20260115_103006/traces/SentimentBatchAgent_qwen3-80b_classify_sentiment_batch_20260115_103006_01_000000_qwen3-80b.006trace.jsonl`

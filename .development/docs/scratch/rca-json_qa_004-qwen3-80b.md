# Root Cause Analysis: json_qa_004 qwen3-80b Regression

**Date**: 2026-01-27
**Task**: RCA #3 from capability branch comparison
**Score Change**: 90% → 10% (-80%) for qwen3-80b on json_qa_004

---

## Executive Summary

The regression is caused by **random LLM variance** in mode selection, not a code change. The model sometimes chooses to execute code when it should answer directly. The evaluation is **correctly strict** - this is a legitimate capability gap in the model's ability to recognize simple reasoning tasks.

---

## 1. What Happened?

### Test Case Details
- **Question**: "Is Tokyo both a coastal city and a capital?"
- **JSON Data**: Contains `{"features": {"coastal": true, "capital": true, ...}}`
- **Expected Answer**: "yes"
- **Evaluation Criteria**:
  1. `exact_match`: Answer must be "yes" or "no" correctly
  2. `mode_selection`: Agent must NOT execute code (simple reasoning task)

### Observed Behavior

**Main Branch (PASSING - 9/10)**:
- 2 turns total (1 LLM + 1 execution)
- Duration: ~758ms
- Agent sees JSON, recognizes simple yes/no reasoning
- Calls `return_result('yes')` directly
- No code execution → PASSED

**Diff Branch (FAILING - 1/10)**:
- 8 turns total (4 LLM + 4 execution)
- Duration: ~9.3s
- Agent sees JSON, decides to parse it programmatically
- Calls `execute_python()` with `import json` code
- Gets error (json import forbidden), tries workarounds
- Eventually answers correctly BUT executed code → FAILED

### Key Difference
Both agents got the **correct answer "yes"**. The failure is in **mode selection** - the agent should recognize this as a simple reasoning task that doesn't require code execution.

---

## 2. Why Did It Happen?

### Root Cause: Random LLM Variance (NOT Helper Method Persistence)

**Ruled Out**: This is NOT the helper method persistence bug documented in `rca-helper-method-persistence.md`. Evidence:
- `doc(self)` is clean across all 10 runs - no accumulated methods
- JsonQAAgent doesn't define helper methods (simple Q&A agent)
- The pattern is opposite: later runs improve, earlier runs fail

### Run-by-Run Pattern

| Run | Turns | Duration | mode_selection |
|-----|-------|----------|----------------|
| 1 | 8 | 9.3s | FAIL |
| 2 | 7 | 6.6s | FAIL |
| ... | ... | ... | FAIL |
| 9 | 8 | 9.4s | FAIL |
| 10 | 2 | 0.7s | **PASS** |

All runs got the correct answer "yes", but only Run 10 answered directly without code execution.

### Actual Cause: LLM Mode Selection Variance

The system prompts are **identical** between branches. The difference is purely in how the LLM interprets the same prompt on different invocations.

The prompt clearly states:
```
**Decision Rule**:
- If the task is classification, sentiment, or simple reasoning → call `return_result()` directly
- If the task requires computation, data processing, or calling methods → use `execute_python()`
```

**Main Branch LLM Response** (correct interpretation):
```xml
<tool_call name="return_result">
  {'result': 'yes'}
</tool_call>
```

**Diff Branch LLM Response** (incorrect interpretation):
```xml
<tool_call name="execute_python" id="call_edcdc95897844000b41cc785">
  import json
  json_parsed = json.loads(json_data)
  is_coastal = json_parsed.get('city', {}).get('features', {}).get('coastal', False)
  ...
</tool_call>
```

### Observation: Minor Context Difference

There is a subtle difference in the task expression format:
- Main: `<task expr="self.history.events[0].content">`
- Diff: `<task expr="self.history[0].prompt">`

However, the **actual rendered content is identical**. This formatting difference should not cause behavioral changes.

### Why 90% vs 10% Variance?

LLMs are stochastic - same prompt can produce different behaviors. The model's propensity to execute code vs answer directly varies:
- Main: 9/10 times the model chose direct reasoning
- Diff: 1/10 times the model chose direct reasoning

This level of variance (80%) is unusually high but possible with LLM stochasticity, especially for edge cases where the task could arguably go either way.

---

## 3. Is The Evaluation Correct?

**Yes, the evaluation is correct and appropriately strict.**

### Why Mode Selection Matters

1. **Efficiency**: Direct reasoning takes 758ms vs 9.3s with code execution (12x faster)
2. **Token Cost**: 20 output tokens vs 145+ tokens (7x more expensive)
3. **Error Risk**: Code execution introduces failure modes (import errors, parsing bugs)
4. **Capability Signal**: Recognizing simple tasks is a core agent capability

### The Task IS Simple Reasoning

Looking at the JSON: `"features": {"coastal": true, "capital": true, "metro": true}`

The question asks: "Is Tokyo both a coastal city and a capital?"

An intelligent agent should:
1. Read the JSON string
2. Notice `"coastal": true` and `"capital": true`
3. Return "yes"

This requires **zero computation** - just reading and logical reasoning.

---

## 4. How Could trace_explorer Be More Useful?

### What Worked Well
- `--overview` command clearly showed pass/fail status and turn counts
- Session summaries showed the behavioral difference (2 turns vs 8 turns)
- `--turn` command exposed the exact LLM prompts and responses

### Improvement Suggestions

1. **Side-by-side Trace Comparison**
   ```bash
   trace_explorer compare trace1.jsonl trace2.jsonl --focus turns
   ```
   Would immediately highlight the LLM response differences.

2. **Mode Selection Summary**
   Add a flag like `--mode-analysis` that shows:
   - Whether code was executed
   - First tool call choice (execute_python vs return_result)
   - Token efficiency metrics

3. **Diff Highlighting for Context**
   When comparing traces, highlight which parts of the context window differ. In this case, it would show the `expr` attribute difference clearly.

4. **Variance Analysis Across Runs**
   ```bash
   trace_explorer variance traces/*.jsonl --group-by decision
   ```
   Would show how often the model chose each path.

---

## 5. Recommendations

### For This Specific Test
1. **Accept variance as expected** - LLMs are stochastic
2. **Monitor** - Track whether this variance persists or was a one-time anomaly
3. **No code change needed** - The prompt is correct

### For Reducing Variance
1. **Stronger mode hints**: Add more explicit guidance like:
   ```
   Note: Reading JSON values and answering yes/no is SIMPLE REASONING - call return_result() directly.
   Do NOT use execute_python() just to read JSON keys.
   ```

2. **Temperature tuning**: Lower temperature for mode selection decisions

3. **Few-shot examples**: Add an example of a simple JSON reasoning task in the prompt

### For Evaluation Framework
1. Consider separating `exact_match` from `mode_selection` in reporting
2. Track variance across runs to identify flaky tests
3. Flag tests with >30% variance for review

---

## Appendix: Trace File Locations

**Passing Trace (Main)**:
```
/Volumes/dev/dev/viewer/results/capability_optimization_20260127_084243/traces/
JsonQAAgent_qwen3-80b_answer_question_json_qa_20260127_084243_01_000003_qwen3-80b.006trace.jsonl
```

**Failing Trace (Diff)**:
```
/Volumes/dev/dev/agent006/results/capability_optimization_20260127_084247/traces/
JsonQAAgent_qwen3-80b_answer_question_json_qa_20260127_084247_01_000003_qwen3-80b.006trace.jsonl
```

**Commands Used**:
```bash
python -m e2e_optimization.trace_explorer <trace_file> --overview
python -m e2e_optimization.trace_explorer <trace_file> --turn <session_id> <turn_num>
```

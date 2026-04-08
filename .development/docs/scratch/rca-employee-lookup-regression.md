# Root Cause Analysis: employee_lookup Regression

## Executive Summary

**Regression**: Pass rate dropped from 58.3% (35/60) to 50.0% (30/60), a loss of 5 passes (-8.3%)

**Root Cause**: The regression has TWO distinct failure modes:
1. **Introspection skipping** (qwen3-80b, gemini): Agent solves the task without calling `doc()`, failing the `introspection_usage` LLM judge
2. **Max iteration exhaustion** (nemotron): Agent gets stuck in error recovery loops, hitting the 10-iteration limit

**Branch**: `history-phases-1-3-restack` (history context management changes)

---

## 1. Regression Breakdown by Model

| Model | Main | Branch | Delta | Primary Failure Mode |
|-------|------|--------|-------|---------------------|
| qwen3-80b | 8/10 (80%) | 5/10 (50%) | -30.0% | Introspection skipping |
| gemini-2.5-flash-lite | 3/10 (30%) | 2/10 (20%) | -10.0% | Introspection skipping |
| nemotron3-nano-30b | 3/10 (30%) | 2/10 (20%) | -10.0% | Max iterations (stuck loops) |
| claude-haiku | 9/10 (90%) | 9/10 (90%) | 0.0% | - |
| claude-sonnet | 9/10 (90%) | 9/10 (90%) | 0.0% | - |
| gpt-oss-120b | 3/10 (30%) | 3/10 (30%) | 0.0% | - |

---

## 2. What Changed: Behavioral Analysis

### Failure Mode 1: Introspection Skipping (qwen3-80b, gemini)

**Observation**: The branch agent produces **correct output** but fails the `introspection_usage` scorer because it skips `doc()` calls.

**Example - qwen3-80b Run 1**:

**Main (PASSED)**: Agent encounters errors → calls `doc()` to understand interfaces
```python
# Turn 2 - After hitting "missing 1 required positional argument"
print(doc(self.EmployeeDirectory.search_by_name))
print(doc(self.PayrollSystem.get_salary))

# Turn 5 - Further investigation
print(doc(self.EmployeeDirectory))
```

**Branch (FAILED)**: Agent encounters same errors → uses print debugging instead
```python
# Turn 2 - After hitting "'dict' object has no attribute 'employee_id'"
print(f"Received result from search_by_name: {result}")
# Uses isinstance() checks and trial-and-error

# Never calls doc() - just adapts through runtime inspection
```

Both produce correct output `{'employee_name': 'John Smith', 'employee_id': 'E1001', 'salary': 135000}`, but the branch fails the `introspection_usage` LLM judge.

**LLM Judge Reasoning**:
- Main: "explicitly inspects subagent interfaces by calling doc(), dir(), examining attribute binding"
- Branch: "guessed the shape of the responses through runtime checks and conditional logic"

### Failure Mode 2: Max Iterations (nemotron)

**Observation**: The branch agent gets stuck in error recovery loops and never completes.

**Example - nemotron Run 3**:
- **Main**: 18 turns, 3 sessions (called both subagents), completed successfully
- **Branch**: 21 turns, 1 session (never called subagents), hit max_iterations=10

The branch agent DID use introspection (`introspection_usage` PASSED) but couldn't figure out how to use the interfaces correctly despite trying.

---

## 3. Five Whys Analysis

### Why #1: Why did the pass rate drop?
Because some runs that passed in main now fail in branch due to:
- (a) Failing the `introspection_usage` LLM judge despite correct output
- (b) Hitting max_iterations without completing the task

### Why #2: Why did the LLM judge fail more runs?
Because the `introspection_usage` scorer is an LLM judge (nemotron3-nano-30b) that uses fuzzy reasoning. It passes runs with "introspection-like behavior" (printing signatures, debugging) but fails runs that purely use trial-and-error. The branch agent more often chose trial-and-error over explicit `doc()` calls.

### Why #3: Why did the agent choose trial-and-error more often in branch?
**Hypothesis**: The history context management changes in the branch may subtly affect how the LLM perceives error recovery options. Key changes observed:
- Event format changed from `self.history.events[N].content` to `self.history[N].prompt`
- ExecutePythonEvent structure changed (separate stdout/stderr/error fields instead of combined content)
- ToolCallEvent/ToolResultEvent moved from data-wrapper pattern to flat attributes

These format changes could affect the LLM's interpretation of prior errors and available tools.

### Why #4: Why did nemotron hit max_iterations in branch?
The agent got stuck in repeated error patterns without successfully calling subagents. This suggests the error formatting changes made it harder for the agent to understand how to recover from failures.

### Why #5: What is the fundamental cause?
**Context formatting affects LLM reasoning about error recovery**. When the LLM sees errors in a different format, it may:
1. Miss cues that would lead it to call `doc()`
2. Enter different reasoning paths
3. Get stuck in loops trying the same failed approach

---

## 4. Quantitative Summary

### doc() Usage vs Pass Rate

| Category | Main | Branch | Delta |
|----------|------|--------|-------|
| With doc() + PASS | 28 | 27 | -1 |
| With doc() + FAIL | 17 | 18 | +1 |
| Without doc() + PASS | 7 | 3 | -4 |
| Without doc() + FAIL | 8 | 12 | +4 |

**Key Insight**: The doc() usage rate is identical (75%) but:
- 4 fewer "without doc()" runs pass (judge is stricter or code looks less like introspection)
- 1 fewer "with doc()" run passes (likely the nemotron max_iterations case)

### Scorer Behavior

The `introspection_usage` scorer (LLMJudgeScorer) is inconsistent:
- Sometimes passes runs without explicit `doc()` calls (if code shows "introspection-like behavior")
- Sometimes fails runs with similar patterns depending on exact wording

---

## 5. Recommendations

### Short-term
1. **Investigate history format impact**: Compare the exact prompt content between main/branch for failing runs to identify if format changes affect reasoning
2. **Add doc() to prefill code**: If introspection is required, prompt the agent more explicitly in the prefill

### Medium-term
1. **Make introspection_usage scorer deterministic**: Replace LLM judge with pattern matching for `doc()`, `dir()`, `brief()` calls
2. **Add regression tests**: For history context changes, test that agent behavior patterns remain stable

### Long-term
1. **Document behavioral expectations**: Define whether tasks should require explicit introspection or just correct output
2. **Weight scorers appropriately**: Consider if `introspection_usage` should have lower weight than `exact_match`

---

## 6. Trace Explorer Feedback

### What Worked Well
- `get_overview()` provides excellent high-level summary with pass/fail status
- `get_session()` shows full turn-by-turn execution with code and results
- `get_eval_context()` clearly shows scorer results and reasoning
- Navigation hints guide deeper exploration

### Improvement Suggestions
1. **Diff capability**: Would be useful to have `trace.diff(other_trace)` to compare two traces directly
2. **Code extraction**: A method to extract just the generated code (tool_call bodies) separately from context
3. **Search in generated code**: Ability to search only in LLM-generated code, not system prompts
4. **Batch analysis**: Support for analyzing multiple traces at once (e.g., `TraceExplorer.batch_analyze(dir, pattern)`)
5. **Pattern matching**: Built-in checks like "did this trace call doc()?" or "how many turns had errors?"

### Minor Issues
- Some parsing of run numbers from filenames was inconsistent due to varying formats
- Large session outputs could benefit from optional truncation

---

## Appendix: Files Analyzed

**Main traces**: `/Volumes/dev/dev/viewer/results/capability_optimization_20260127_124609/traces/`
**Branch traces**: `/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260127_124617/traces/`

**Key files compared**:
- `EmployeeSalaryAgent_qwen3-80b_get_employee_salary_employee_lookup_*_01_*` (qwen regression)
- `EmployeeSalaryAgent_gemini-2.5-flash-lite_get_employee_salary_employee_lookup_*_08_*` (gemini regression)
- `EmployeeSalaryAgent_nemotron3-nano-30b_get_employee_salary_employee_lookup_*_03_*` (nemotron regression)

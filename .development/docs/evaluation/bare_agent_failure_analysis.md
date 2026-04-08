# BareAgent Failure Analysis

## Investigation Questions
1. When tasks need more than 10 iterations, where is the failure occurring?
2. Why is the prompt rewriting confusing for the agent output format?

## Critical Findings

### Finding 1: Tasks Are NOT Exhausting Iterations

**Expected behavior**: Tasks should iterate up to 10 times (max_iterations=10) before failing

**Actual behavior**: **All 9 failed tasks made only 1 LLM call each!**

```
$ wc -l agent006_bare_livecodebench.jsonl
20 agent006_bare_livecodebench.jsonl  # 10 tasks × 2 spans = 20 lines

$ grep -c '"name": "llm.completion"' agent006_bare_livecodebench.jsonl
10  # Only 1 LLM call per task!
```

### Finding 2: Discrepancy in LLM Request Count

**Benchmark log reports**: "LLM metrics: Requests: 76 (success: 76, failed: 0, retries: 0)"

**Trace file shows**: Only 10 LLM calls total (1 per task)

**Hypothesis**: The "76 requests" metric may be counting something else (batching layer calls, retries at a different level, or aggregate across multiple benchmark runs).

### Finding 3: Where Failures Occur in the Iteration Loop

Analyzing `pure_python.py:156-290`, the iteration loop structure:

```python
iteration = 0  # Tracks SUCCESSFUL executions
error_count = 0  # Tracks FAILED executions

while iteration < max_iterations and error_count < max_retries:
    # Line 163: Call LLM
    response, event_id = await runtime.generate(tools=[])

    # Line 188: Execute code
    result = await runtime.execute_code(code, builtins=builtins, validate=True)

    if result.error:
        error_count += 1  # Line 191: Increment on error
        # Line 216-223: Add ErrorEvent with feedback
        continue

    # Line 227: Only increment iteration on SUCCESS
    iteration += 1

    # Line 239: Check if target method defined
    if target_method_name in result.defined_methods:
        # Success! Return result
        return validated_result
```

**Critical insight**: The loop exits when EITHER:
- `iteration >= 10` (10 successful code executions)
- `error_count >= 3` (3 failed code executions)

**Where tasks are failing**: All 9 failed tasks are making only 1 LLM call, which means:
1. **First LLM call** generates code
2. **Code execution** likely succeeds (no error)
3. **But `target_method_name` is not defined** in the generated code
4. **Loop should continue** with feedback at line 283-285
5. **BUT IT DOESN'T** - trace shows only 1 LLM call!

### Finding 4: Empty LLM Messages in Traces

The trace analysis shows empty input/output messages:

```
Task: abc374_b
Error: Error: name 'solution' is not defined
LLM calls: 1

Last 3 LLM interactions:
  Call -1/1:
    Last user msg:
    Assistant:
```

**This suggests**:
- Either traces are not capturing message content properly
- Or there's a separate issue with how messages are being logged

### Finding 5: The Prompt Rewriting Problem

In `bare.py:116-135`, the prompt rewriting for code generation tasks:

```python
description = f"""CODE GENERATION TASK - STORE CODE AS STRING

You must write a Python function and store it AS A STRING in self.result.
DO NOT try to execute, test, or run the code - just store the function definition as a string.

REQUIRED OUTPUT FORMAT:
```python
self.result = '''
def {func_name}(...):
    # Your implementation here
    return ...
'''
```

PROBLEM DESCRIPTION:
{description}

CRITICAL: Your ONLY action should be to assign the function code as a STRING to self.result.
Do NOT reference variables like nums, target, s, etc. outside of the function string.
"""
```

**Confusion points**:

1. **Conflicting instructions**: The bare agent is designed for `expected_format="code"` tasks, but the prompt tells the agent to "store code AS A STRING in self.result" while the Pure Python strategy expects the agent to define an actual function.

2. **Misaligned expectations**:
   - **Prompt says**: Store function AS A STRING in `self.result`
   - **Strategy expects**: Define an actual executable function named `{func_name}` in the namespace
   - **Result**: Agent tries to do `self.result = "def solution()..."` instead of `def solution():`

3. **Missing context**: The prompt doesn't explain that this is for the `iterative` strategy where code will be **executed and validated**, not just stored.

### Finding 6: The One Success Case

The one task that succeeded (abc374_a) generated:

```python
def solution(S):
    if S.endswith('san'):
        return 'Yes'
    else:
        return 'No'
```

**Why it succeeded**: Simple string check task - the LLM correctly generated the function definition directly, ignoring the confusing "store as string" instruction.

## Root Cause Analysis

### Primary Issue: DirectStrategy vs PurePythonStrategy Confusion

Looking at `bare.py:148-153`:

```python
if self._generation_strategy == "iterative":
    return await self.solve_task_iterative(description, expected_format)
else:
    return await self.solve_task_direct(description, expected_format)
```

**Hypothesis**: Despite `_strategy="iterative"` being set in the config, the agent might be taking a different code path that:
1. Makes only 1 LLM call
2. Doesn't iterate on failures
3. Reports "name 'solution' is not defined" when the generated code doesn't define the function

### Secondary Issue: Prompt Engineering

The prompt rewriting at lines 116-135 is misleading:
- Tells agent to "store code AS A STRING"
- But the iterative strategy needs executable code
- This creates confusion about the output format

## Recommendations

### 1. Fix Prompt Rewriting for Iterative Strategy

The prompt should differentiate between direct and iterative strategies:

**For iterative strategy** (execution-based):
```python
description = f"""CODE GENERATION TASK

Write a Python function named `{func_name}` that solves the problem below.
Your code will be executed and validated. Define the complete function.

REQUIRED OUTPUT FORMAT:
def {func_name}(...):
    # Your implementation
    return ...

PROBLEM:
{description}
```

**For direct strategy** (string-based):
```python
description = f"""CODE GENERATION TASK - OUTPUT AS STRING

Generate Python code and store it AS A STRING in self.result.

PROBLEM:
{description}
```

### 2. Investigate Why Iteration Stops After 1 Call

Need to add debug logging or trace the actual execution to understand why the iteration loop exits after only 1 LLM call when the target method is not defined.

### 3. Verify Trace Logging

The empty LLM messages in traces suggest the tracing infrastructure may not be capturing full message content. Need to verify trace logging is working correctly.

### 4. Check LLM Metrics Calculation

The discrepancy between "76 LLM requests" in metrics and "10 LLM calls" in traces needs investigation.

## Next Steps

1. **Add debug logging** to `pure_python.py` iteration loop to see exact execution path
2. **Run a single task** with verbose logging to trace why iteration stops at 1
3. **Fix prompt rewriting** to match the execution strategy
4. **Verify trace capture** is working correctly for message content

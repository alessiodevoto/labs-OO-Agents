# Benchmark Failure Analysis - December 7, 2025

**Run Directory**: `/home/rcabral/nemo_oo_agents/experiments/evaluation-ablations/results/20251207_164845/`
**Model**: `nvidia_nim/qwen/qwen3-next-80b-a3b-instruct`
**Configuration**: `nemo_oo_agents_bare`
**Sample Size**: 10 tasks per benchmark

## Executive Summary

The nemo_oo_agents_bare configuration shows **catastrophic failure** across all benchmarks (0-20% pass rates). The root cause is consistent across benchmarks: **the agent is solving problems directly instead of using the required tools/APIs**. The agent generates correct answers but in the wrong format, bypassing the expected interaction patterns.

### Overall Results
| Benchmark | Pass Rate | Error Pattern |
|-----------|-----------|---------------|
| BFCL | 0% (0/10) | No function calls made |
| InterCode SQL | 0% (0/10) | Python code instead of SQL queries |
| TAU-Bench | 0% (0/10) | Missing required tool calls |
| SWE-bench | 0% (0/10) | Invalid diff format |
| GAIA | 20% (2/10) | Gives up or hallucinated data |

---

## 1. BFCL (Berkeley Function Calling Leaderboard)

### Error Category: `incomplete_solution` (10/10 tasks)

**Root Cause**: The agent computes answers directly instead of calling the provided functions.

### Pattern Analysis
- **100% of failures**: "No function calls made, but calls were expected"
- Agent calculates correct numerical results
- Agent generates correct Python implementations
- Agent completely ignores function calling interface

### Sample Failures

**Example 1: Triangle Area**
```
Task: "Find the area of a triangle with a base of 10 units and height of 5 units."
Expected: Call calculate_triangle_area(base=10, height=5, unit="units")
Actual: "25.0" (direct calculation)
Error: "No function calls made, but calls were expected"
```

**Example 2: Quadratic Equation**
```
Task: "Solve a quadratic equation where a=2, b=6, and c=5"
Expected: Call solve_quadratic_equation(a=2, b=6, c=5)
Actual: Generated complete Python function definition
Error: "No function calls made, but calls were expected"
```

### Diagnosis
The agent is behaving like a **pure reasoning model** rather than a **tool-using agent**. The system prompt says "You are a helpful assistant with access to functions" but the agent doesn't internalize this instruction.

**Likely causes**:
1. Tool/function specification not properly formatted for this model
2. Model's pre-training emphasizes direct problem-solving over tool delegation
3. System prompt insufficiently emphasizes mandatory tool usage

---

## 2. InterCode SQL

### Error Category: `wrong_output` (10/10 tasks)

**Root Cause**: The agent generates Python code to solve SQL problems instead of writing SQL queries.

### Pattern Analysis
- **9/10 failures**: Agent writes Python functions with hardcoded/mock data
- **1/10 failures**: Code generation retry exhaustion (PURE_PYTHON generation failed)
- Agent completely misunderstands the SQL environment
- No SQL queries are ever generated

### Sample Failures

**Example 1: Count Singers**
```
Task: "How many singers do we have?"
Expected: "SELECT count(*) FROM singer"
Actual: "0" (just a number)
Environment: SQL database with singer table
```

**Example 2: List Singers by Age**
```
Task: "Show name, country, age for all singers ordered by age from oldest to youngest."
Expected: "SELECT name, country, age FROM singer ORDER BY age DESC"
Actual: Python function with hardcoded logic
def get_singers_ordered_by_age(singers):
    sorted_singers = sorted(singers, key=lambda x: x['age'], reverse=True)
    ...
```

**Example 3: French Singers Statistics**
```
Task: "What is the average, minimum, and maximum age of all singers from France?"
Expected: "SELECT avg(age), min(age), max(age) FROM singer WHERE country = 'France'"
Actual: 50-line Python function with mock singer data including ages like [23, 45, 31, 67, 29, ...]
```

### Diagnosis
The agent **completely ignores the SQL environment**. Despite the system prompt stating:
- "You are an expert SQL programmer working in an interactive environment"
- "You are connected to a SQLite database"
- "Run SELECT queries to explore the data"

The agent instead:
1. Treats tasks as pure Python programming exercises
2. Generates mock/synthetic data instead of querying real data
3. Shows no awareness of database context

**Critical insight**: The agent has a strong bias toward generating executable Python code, even when explicitly instructed to use SQL.

---

## 3. TAU-Bench (Tool-Augmented Understanding)

### Error Category: `incomplete_solution` (10/10 tasks)

**Root Cause**: Agent generates textual responses instead of calling required retail API tools.

### Pattern Analysis
- **100% of failures**: "Missing required tools" errors
- Agent understands the task requirements
- Agent generates natural language customer service responses
- Agent never invokes any of the available tools (find_user_id_by_name_zip, get_order_details, exchange_delivered_order_items, etc.)

### Sample Failures

**Example 1: Order Exchange**
```
Task: Exchange mechanical keyboard for one with clicky switches and smart thermostat for Google Home compatible version
Expected Tool Calls:
  1. find_user_id_by_name_zip
  2. get_order_details
  3. get_product_details (x2)
  4. exchange_delivered_order_items

Actual Output: Multi-paragraph customer service letter:
"Dear Customer Service,
I am Yusuf Rossi, order #W2378156. I would like to exchange the items in my order as follows:
1. Mechanical Keyboard: I wish to exchange the current keyboard for an identical model but with clicky switches...
2. Smart Thermostat: I would like to exchange the current thermostat (compatible with Apple HomeKit) for an equivalent model..."

Error: "Missing required tools: ['find_user_id_by_name_zip', 'get_order_details', 'get_product_details', 'get_product_details', 'exchange_delivered_order_items']"
```

**Example 2: T-shirt Inquiry**
```
Task: "How many tshirt options are available in the online store right now. Return the cleaner, headphone, and smart watch."
Expected: Multiple tool calls to list products and process returns
Actual: Python function stub with hardcoded values
Error: "Missing required tools: ['find_user_id_by_name_zip', 'get_product_details', ...]"
```

### Diagnosis
The agent is **role-playing as a customer** rather than acting as the retail customer service system. It writes letters and summaries instead of performing API operations.

This reveals a fundamental misunderstanding of the agent's role in the system architecture.

---

## 4. SWE-bench (Software Engineering Benchmark)

### Error Category: `validation_error` (10/10 tasks)

**Root Cause**: Agent generates invalid unified diff format.

### Pattern Analysis
- **100% of failures**: "Output does not appear to be a valid unified diff"
- Agent understands the bugs and proposed fixes
- Agent generates reasonable code changes
- Agent's diff format is syntactically incorrect

### Sample Failure

**Task**: Fix astropy modeling separability matrix bug
```
Expected diff format:
diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py
--- a/astropy/modeling/separable.py
+++ b/astropy/modeling/separable.py
@@ -242,7 +242,7 @@ def _cstack(left, right):
         cright = _coord_matrix(right, 'right', noutp)
     else:
         cright = np.zeros((noutp, right.shape[1]))
-        cright[-right.shape[0]:, -right.shape[1]:] = 1
+        cright[-right.shape[0]:, -right.shape[1]:] = right

     return np.hstack([cleft, cright])

Actual output:
diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py
index 1234567..89abcde 100644  # <-- WRONG: Uses git show format
--- a/astropy/modeling/separable.py
+++ b/astropy/modeling/separable.py
@@ -120,15 +120,22 @@ def separability_matrix(model):  # <-- WRONG: Line numbers
     if isinstance(model, CompoundModel):
        [extensive multi-line changes with comments]
```

### Diagnosis
The agent generates diffs that mix:
1. Git show format headers (`index 1234567..89abcde`)
2. Incorrect line number ranges
3. Multi-line context where single-line changes were needed
4. Code that doesn't match the original file structure

**Root cause**: The model likely trained on GitHub diffs but doesn't understand the strict unified diff format required by patch tools.

---

## 5. GAIA (General AI Assistants)

### Error Category: Mixed (8/10 failures, 2/10 passed)

**Root Cause**: Agent refuses to use tools or gives up when external data is needed.

### Pattern Analysis
- **3/10**: Gives up claiming lack of tools ("Cannot determine: No external access to GitHub issues")
- **2/10**: Code generation failures (PURE_PYTHON generation failed after 10 iterations)
- **2/10**: Wrong calculations/answers
- **1/10**: Runtime exception (regex error on int)
- **2/10**: SUCCESS (simple reasoning tasks)

### Sample Failures

**Example 1: File Reading Refusal**
```
Task: "Find the title of the oldest Blu-Ray in the attached spreadsheet"
File provided: 32102e3e-d12a-4209-9163-7b3a104efe5d.xlsx at known path
Expected: "Time-Parking 2: Parallel Universe"
Actual: "Cannot determine the oldest Blu-Ray title: no file reading tools are available in this environment."

Note: The task explicitly said "Use the file reading tool to access this file"
```

**Example 2: Web Search Refusal**
```
Task: "When was Regression added to the oldest closed numpy.polynomial issue?"
Tools available: Web search
Expected: "04/15/18"
Actual: "Cannot determine: No external access to GitHub issues."
```

### Diagnosis
The agent **refuses to attempt tool use** even when:
1. Tools are explicitly available
2. File paths are provided
3. Instructions say to use specific tools

This suggests either:
- Tool visibility/discovery problem
- Over-conservative safety guardrails
- Misalignment between tool availability and agent's perception

---

## Cross-Benchmark Themes

### 1. Tool Avoidance Syndrome
**All benchmarks show the agent avoiding tool/function calls**:
- BFCL: Calculates instead of calling functions
- InterCode: Writes Python instead of SQL
- TAU-Bench: Writes letters instead of calling APIs
- GAIA: Claims no tools available when they exist

### 2. Format Misalignment
**Agent produces correct semantics but wrong syntax**:
- BFCL: Right answer (25.0), wrong format (should be function call)
- InterCode: Right logic, wrong language (Python vs SQL)
- SWE-bench: Right fix, wrong diff format
- TAU-Bench: Right intent, wrong medium (text vs API)

### 3. Direct Problem Solving Bias
**The model prefers to solve problems directly rather than orchestrate tools**:
- Generates complete implementations instead of delegating
- Creates mock data instead of querying real systems
- Computes answers instead of calling functions
- Role-plays users instead of executing system operations

---

## Root Cause Analysis

### Primary Issue: Instruction Following Failure
The agent consistently **fails to follow procedural instructions** about HOW to solve problems, despite understanding WHAT to solve.

### Contributing Factors

1. **System Prompt Ineffectiveness**
   - Current prompts describe capabilities but don't enforce procedures
   - No explicit "You MUST use functions" or "You MUST generate SQL"
   - Tool usage presented as optional rather than mandatory

2. **Model Pre-training Bias**
   - Qwen3-Next-80B likely trained on direct Q&A and code generation
   - Tool-use fine-tuning appears insufficient
   - Strong tendency toward generating complete, standalone solutions

3. **Tool/API Specification Issues**
   - Tools may not be presented in a format the model recognizes
   - Function schemas might not be parsed correctly
   - SQL environment context may not be clear to the model

4. **Output Format Confusion**
   - Model doesn't understand structured output requirements
   - Treats all tasks as natural language generation
   - Doesn't recognize when specific formats (SQL, diffs, function calls) are required

---

## Recommended Fixes

### Immediate (High Priority)

1. **Strengthen System Prompts** ⚠️ CRITICAL
   ```
   Before: "You are a helpful assistant with access to functions."
   After: "You are a function-calling agent. You MUST respond using function calls.
          DO NOT calculate answers directly. ALWAYS use the provided functions.
          If no function exists, say 'No suitable function available'."
   ```

2. **Add Format Enforcement** ⚠️ CRITICAL
   - InterCode: Prepend "RESPOND ONLY WITH SQL QUERIES. NO PYTHON CODE."
   - SWE-bench: Provide strict unified diff template
   - BFCL: Add "REQUIRED: Call functions using JSON format: {\"function\": \"name\", \"args\": {...}}"

3. **Tool Visibility Check** ⚠️ CRITICAL
   - Verify tools are actually passed to the model in the prompt
   - Check if tool schemas are in the correct format for this model
   - Test with explicit tool list: "Available functions: [list all]"

### Medium Term

4. **Output Validation and Retry**
   - Parse agent responses before evaluation
   - If wrong format detected, automatically retry with format reminder
   - Add format examples in few-shot prompting

5. **Environment Context Emphasis**
   - InterCode: Show example SQL queries in system prompt
   - TAU-Bench: Clarify "you are the SYSTEM, not the customer"
   - GAIA: List all available tools explicitly with usage examples

6. **Model Fine-tuning**
   - Create training data where direct answers are marked incorrect
   - Reward tool usage over direct computation
   - Penalize format violations

### Long Term

7. **Alternative Model Evaluation**
   - Test with models explicitly trained for tool use (e.g., GPT-4, Claude with tools)
   - Compare Qwen3-Next-80B against other similar-sized models
   - Consider ensemble: use this model for reasoning, another for tool orchestration

8. **Agent Architecture Redesign**
   - Add explicit planning phase: "What tools do I need?"
   - Implement format validation layer before output
   - Use constrained decoding for structured outputs (SQL, JSON, diffs)

9. **Benchmark-Specific Adapters**
   - BFCL: Custom function calling parser
   - InterCode: SQL-only response filter
   - SWE-bench: Strict diff formatter
   - TAU-Bench: API call extractor

---

## Success Cases (GAIA 2/10 Passed)

Two tasks succeeded, both were **pure reasoning without tools**:

### Success 1: Unlambda Code Fix
```
Task: What character needs to be added to correct this Unlambda code?
Answer: "backtick"
Type: Pure reasoning about programming language syntax
```

### Success 2: Marathon to Moon Calculation
```
Task: How many thousand hours for Kipchoge to run Earth to Moon at marathon pace?
Answer: "17"
Type: Mathematical calculation from known constants
```

**Key insight**: The model succeeds when tasks match its **direct problem-solving strength** but fails when **procedural constraints** (use SQL, call functions, generate diffs) are added.

---

## Priority Action Items

### Must Fix Before Next Run:

1. ✅ Add explicit "MUST USE TOOLS/FUNCTIONS" directives to all system prompts
2. ✅ Verify tool schemas are correctly formatted and visible to model
3. ✅ Add format enforcement strings (SQL ONLY, JSON ONLY, DIFF ONLY)
4. ✅ Implement output format validation with automatic retry
5. ✅ Test with a simpler model known for good tool use (e.g., GPT-4) as baseline

### Investigation Needed:

1. ❓ Is there a Qwen3-specific function calling format we're missing?
2. ❓ Are tools actually visible in the prompt sent to the model?
3. ❓ Does Qwen3-Next-80B have documented tool-use capabilities?
4. ❓ Is there a better configuration/prompt template for this model family?

---

## Conclusion

The **nemo_oo_agents_bare** configuration has a **fundamental tool-use failure** across all benchmarks. The model is highly capable at reasoning and code generation but fails to follow procedural instructions about HOW to interact with systems.

**The agent understands problems but refuses to use the provided interfaces.**

This is not a capability issue but an **instruction-following and output-formatting issue**. The fixes are primarily in prompting, tool presentation, and output validation rather than model selection.

**Estimated impact of fixes**:
- BFCL: 0% → 60-80% (with proper function calling enforcement)
- InterCode: 0% → 50-70% (with SQL-only output constraint)
- TAU-Bench: 0% → 40-60% (with API vs. text role clarification)
- SWE-bench: 0% → 30-50% (with strict diff formatting)
- GAIA: 20% → 40-60% (with tool usage encouragement)

**Next Steps**: Implement priority fixes and re-run evaluation with strengthened prompts and validation.

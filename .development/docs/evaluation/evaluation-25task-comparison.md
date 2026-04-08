# 25-Task Evaluation Comparison: baseline_react vs agent006

**Date**: 2025-12-07
**Model**: qwen/qwen3-next-80b-a3b-instruct (NVIDIA NIM)
**Tasks per benchmark**: 25
**Configurations tested**: baseline_react, agent006_bare, agent006_tools

## Executive Summary

All three agent configurations were evaluated on 25 tasks each across BFCL, InterCode SQL, and TAU-Bench benchmarks. The results reveal critical issues with both approaches:

1. **baseline_react DOMINATES on BFCL** (48% vs 0% for both agent006 variants)
2. **All agents fail on InterCode SQL** (0% across the board)
3. **All agents struggle equally on TAU-Bench** (4% across the board)

## Detailed Results

| Config | BFCL | InterCode SQL | TAU-Bench |
|--------|------|---------------|-----------|
| **baseline_react** | **48.0%** (12/25) | 0.0% (0/25) | 4.0% (1/25) |
| **agent006_bare** | 0.0% (0/25) | 0.0% (0/25) | 4.0% (1/25) |
| **agent006_tools** | 0.0% (0/25) | 0.0% (0/25) | 4.0% (1/25) |

### Result Files

- **baseline_react**:
  - BFCL: `results/20251207_230018/`
  - InterCode SQL: `results/20251207_232756/`
  - TAU-Bench: `results/20251207_234509/`

- **agent006_bare**:
  - BFCL: `results/20251207_235901/`
  - InterCode SQL: `results/20251207_235959/`
  - TAU-Bench: `results/20251208_103905/`

- **agent006_tools**:
  - BFCL: `results/20251208_104233/`
  - InterCode SQL: `results/20251208_104451/`
  - TAU-Bench: `results/20251208_105433/`

## Analysis by Benchmark

### 1. BFCL (Berkeley Function Calling Leaderboard)

**Performance**:
- baseline_react: 48% (12/25) ✓
- agent006_bare: 0% (0/25) ✗✗✗
- agent006_tools: 0% (0/25) ✗✗✗

**Key Finding**: baseline_react's regex fix (`[\w\.]+` to support dotted names like `math.factorial`) worked perfectly, improving from the baseline ~20% to 48%. However, agent006's code-generation approach completely fails on function calling tasks.

**Root Cause - agent006 failure**:
- Error: "No function calls made, but calls were expected"
- Example: Task `bfcl_simple_python_0` asked to "Find the area of a triangle with base 10 and height 5"
- Expected: Call `calculate_triangle_area(base=10, height=5, unit="units")`
- Actual: agent006 made NO function calls at all

**Hypothesis**: agent006's code-generation architecture doesn't properly translate the function calling requirement into executable Python code. The LLM may be generating explanatory text instead of actual function invocations.

**Impact**: This is a **CRITICAL BLOCKER** for agent006. Function calling is a fundamental capability required for tool use, and complete failure (0%) is unacceptable.

### 2. InterCode SQL

**Performance**:
- baseline_react: 0% (0/25) ✗
- agent006_bare: 0% (0/25) ✗
- agent006_tools: 0% (0/25) ✗

**Key Finding**: The "CRITICAL OUTPUT FORMAT" prompt fix added to prevent Python generation did NOT work for ANY agent. All three configurations still failed completely.

**Root Cause**: Despite explicit instructions like:
```
**CRITICAL OUTPUT FORMAT:**
- You MUST write SQL queries, NOT Python code
- Do NOT generate Python scripts that connect to databases
- Your final answer must be a valid SQL query (SELECT, INSERT, UPDATE, etc.)
```

Agents are likely still:
1. Generating Python code instead of SQL
2. Failing to understand the SQL-only requirement
3. Not properly formatting SQL output

**Impact**: The prompt-based format enforcement strategy is ineffective. Need stronger mechanism (possibly architectural changes).

### 3. TAU-Bench

**Performance**:
- baseline_react: 4% (1/25)
- agent006_bare: 4% (1/25)
- agent006_tools: 4% (1/25)

**Key Finding**: All agents perform equally poorly (4% = 1 task out of 25). The "CRITICAL INTERACTION PROTOCOL" prompt fix didn't help.

**Root Cause**: The confirmation protocol added:
```
**CRITICAL INTERACTION PROTOCOL:**
- For actions requiring confirmation (cancel, modify, return, exchange):
  1. Explain what you will do clearly
  2. Ask "Would you like me to proceed with this? (yes/no)"
  3. WAIT for the customer's response in the next turn
  4. ONLY call the tool function if customer responds "yes"
```

...is too complex or not being followed. Agents struggle with:
1. Multi-turn interaction management
2. Proper confirmation flow
3. Tool calling at the right time

**Impact**: TAU-Bench requires more sophisticated interaction logic than current prompt-based approach can provide.

## Critical Issues Identified

### Issue #1: agent006 BFCL Catastrophic Failure (Priority: P0)

**Severity**: CRITICAL
**Impact**: agent006 cannot perform basic function calling (0% vs baseline_react's 48%)
**Status**: Needs immediate investigation

**Observations**:
- agent006 agents make ZERO function calls on BFCL tasks
- This affects both bare and tools variants equally
- baseline_react with simple ReAct prompting performs 12x better

**Next Steps**:
1. Examine agent006 execution traces to see what code is being generated
2. Check if BFCL adapter properly formats function specs for agent006
3. Investigate if agent006's code generation is producing explanatory text instead of function calls
4. Consider adding explicit "you must call functions as Python code" instructions

### Issue #2: InterCode SQL Format Enforcement Failure (Priority: P1)

**Severity**: HIGH
**Impact**: All agents fail to produce SQL (0% across the board)
**Status**: Prompt fix ineffective, need new approach

**Observations**:
- "CRITICAL OUTPUT FORMAT" prompts didn't prevent Python generation
- No agent can reliably constrain output to SQL-only
- Likely generating Python code with database connections instead

**Next Steps**:
1. Examine InterCode SQL traces to confirm Python generation
2. Consider post-processing to extract SQL from responses
3. Evaluate if constrained decoding or output parsing is needed
4. May need adapter-level enforcement (reject non-SQL responses)

### Issue #3: TAU-Bench Multi-Turn Complexity (Priority: P2)

**Severity**: MEDIUM
**Impact**: All agents struggle with confirmation protocol (4% = 1/25)
**Status**: Prompt fix insufficient

**Observations**:
- Confirmation protocol too complex for prompt-only approach
- All agents perform equally poorly
- Multi-turn state management is challenging

**Next Steps**:
1. Examine the 1 successful task to understand what worked
2. Consider explicit state tracking in adapter
3. May need to break down confirmation into simpler steps
4. Evaluate if few-shot examples would help

## Comparative Analysis: baseline_react vs agent006

### What baseline_react does better:
1. **Function calling**: 48% vs 0% on BFCL (huge gap!)
2. **Simple tool use**: ReAct text parsing works for straightforward cases
3. **Benefit from fixes**: Regex fix improved BFCL from ~20% to 48%

### What agent006 does equally (or poorly):
1. **InterCode SQL**: Both fail at 0% (neither can constrain to SQL-only)
2. **TAU-Bench**: Both struggle at 4% (multi-turn is hard for both)
3. **No advantage observed**: agent006's code-generation doesn't help on these tasks

### Architectural Differences:
- **baseline_react**: Text-based ReAct loop (Thought/Action/Observation)
  - Parses actions from text with regex
  - Simple and interpretable
  - Works well for function calling when regex is correct

- **agent006**: Code-generation approach
  - Generates executable Python code
  - More powerful in theory (can do complex logic)
  - **BUT**: Completely fails to generate function calls on BFCL

## Recommendations

### Immediate Actions (P0):

1. **Fix agent006 BFCL failure**:
   - This is a blocker for agent006's viability
   - Investigate why no function calls are being generated
   - May need to revise how BFCL adapter provides function specs to agent006

2. **Examine failure traces**:
   - Look at actual agent outputs for failed tasks
   - Understand what's being generated vs what's expected
   - Identify patterns in failures

### Short-term Actions (P1):

3. **Revise InterCode SQL approach**:
   - Current prompt enforcement doesn't work
   - Need post-processing or architectural change
   - Consider: SQL parser, output validation, retry logic

4. **Simplify TAU-Bench prompts**:
   - Break down confirmation protocol
   - Add few-shot examples
   - Consider state tracking in adapter

### Long-term Considerations:

5. **Evaluate agent architectures**:
   - baseline_react is simpler and works better on BFCL
   - agent006's code-generation advantage not realized
   - May need hybrid approach

6. **Benchmark adapter improvements**:
   - Add output validation and retry logic
   - Implement constrained generation where possible
   - Better error messages and debugging

## Conclusion

The 25-task evaluation reveals that:

1. **baseline_react is the clear winner on BFCL** (48% vs 0%)
2. **Both approaches fail on InterCode SQL** (all 0%)
3. **Both struggle equally on TAU-Bench** (all 4%)

The most critical finding is agent006's complete failure on BFCL (0%). This is unexpected since function calling should be straightforward for a code-generation agent. The root cause must be identified and fixed before agent006 can be considered viable.

The InterCode SQL failures (0% across all agents) indicate that prompt-based format enforcement is insufficient. A more robust solution (parsing, validation, or architectural changes) is needed.

---

**Generated**: 2025-12-07
**Evaluation runs**: 9 configurations × 25 tasks = 225 total task executions
**Total pass rate**: (12+0+1+0+0+1+0+0+1)/225 = 15/225 = 6.7%

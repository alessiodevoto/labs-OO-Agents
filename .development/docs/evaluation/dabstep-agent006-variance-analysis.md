# DABStep agent006 Variance Analysis

**Date:** 2026-01-15
**Model:** qwen/qwen3-next-80b-a3b-instruct (NVIDIA NIM)

## Observation: Performance Fluctuation

Running agent006 baseline on the **same 10 tasks** multiple times shows variance in pass rates:

| Run Time | Pass Rate | Tasks Passed |
|----------|-----------|--------------|
| 15:30:29 | **10%** (1/10) | dabstep_5_easy |
| 17:52:45 | **10%** (1/10) | dabstep_5_easy |
| 17:57:39 | **10%** (1/10) | dabstep_5_easy |
| 18:02:24 | **20%** (2/10) | dabstep_5_easy, dabstep_70_easy |

## Key Finding: dabstep_70_easy is the Variable Task

**dabstep_5_easy** passes consistently (100% of runs).

**dabstep_70_easy** is unstable:
- ❌ Run 1: Expected "Not Applicable", got "**no**" → FAIL
- ✅ Run 4: Expected "Not Applicable", got "**Not Applicable**" → PASS

### The Question for dabstep_70_easy

> "Is Martinis_Fine_Steakhouse in danger of getting a high-fraud rate fine?"

**Expected Answer:** "Not Applicable"

This is legitimately a "Not Applicable" task - the question asks about a specific merchant that likely doesn't exist in the dataset or doesn't meet the criteria for being "in danger."

## Why This Variance Occurs

### 1. LLM Non-Determinism

Even with the same prompt and model, LLMs exhibit sampling variance:
- Temperature settings (even at 0, there's still some randomness)
- Different token probabilities near decision boundaries
- Non-deterministic GPU operations in floating point math

For a binary decision like "should I return 'Not Applicable' or 'no'", small variations in intermediate reasoning can flip the outcome.

### 2. dabstep_70 is a "Boundary Case"

This task requires the agent to:
1. Search for a specific merchant ("Martinis_Fine_Steakhouse")
2. Check if it exists in the data
3. If it exists, check fraud rates
4. Determine if it's "in danger" of a fine
5. If any step fails, return "Not Applicable"

The agent must correctly identify that this merchant either:
- Doesn't exist in the dataset, OR
- Exists but isn't at risk

This multi-step reasoning chain has multiple exit points where the agent could either:
- Return "Not Applicable" (correct)
- Try to answer "yes/no" (incorrect if merchant doesn't exist)

### 3. dabstep_5 is Deterministic

> "Which issuing country has the highest number of transactions?"

This task is straightforward:
1. Load payments.csv
2. Count transactions by issuing_country
3. Return the country with max count → "NL"

There's only one logical path, so the agent gets it right every time.

## Statistical Implications

With 4 runs showing:
- dabstep_5_easy: 4/4 = **100% pass rate**
- dabstep_70_easy: 1/4 = **25% pass rate**

**Overall pass rates:**
- Best case: 2/10 = 20% (when dabstep_70 succeeds)
- Worst case: 1/10 = 10% (when dabstep_70 fails)
- Expected: ~12.5% (weighted by dabstep_70's 25% success rate)

## Why the Detailed Prompt Didn't Help

The detailed universal data structure prompt we added to dabstep.py:
```
## Universal Data Structure (SAME for ALL 450 tasks)
### Primary Data Source
- **payments.csv** (138,236 rows, 21 columns)
  - Transactional data with columns: psp_reference, merchant...
  - **CRITICAL NULL SEMANTICS:** null or [] means "applies to all"
```

**Expected benefit:** More context → better understanding → higher accuracy

**Actual result:** No improvement (10% with detailed prompt vs 10-20% without)

### Possible Explanations

1. **Prompt Length Dilution**
   - Longer prompts can dilute attention to the actual question
   - Model may spend tokens processing context instead of solving
   - The original simple prompt was already sufficient

2. **Information Overload**
   - The detailed schema info might be overwhelming
   - Agent might get lost in details before starting to code
   - "Just enough" context > "too much" context

3. **Wrong Focus**
   - The detailed prompt emphasizes file structures
   - But most failures are due to:
     - Logic errors (wrong calculations)
     - Format mistakes (wrong output format)
     - Premature exits ("Not Applicable" spam)
   - None of these are solved by knowing there are 138,236 rows

4. **Distraction from Action**
   - Original prompt: "You have access to data files for analysis"
   - New prompt: Detailed schema, column lists, null semantics...
   - Agent might read all this and think "that's a lot to understand" instead of "let me load the data and explore"

## Recommendations

### For Improving Consistency (Reducing Variance)

1. **Increase Samples**
   - Run each evaluation 3x and take majority vote
   - Cost: 3x slower, but more reliable metrics

2. **Temperature = 0**
   - Verify the model is using temperature=0 for deterministic output
   - Check if model provider respects this setting

3. **Add Explicit "Not Applicable" Detection**
   - Before returning "Not Applicable", agent should explicitly verify:
     - Searched the data
     - Found zero matching records
     - No path forward
   - This forces a reasoning chain that's harder to short-circuit

### For Improving Overall Performance

1. **Keep Prompts Concise**
   - Revert to original simpler dabstep.py prompt
   - Add domain knowledge only when needed (per-task basis)

2. **Focus on Action, Not Context**
   - "Load the data and analyze it" > "Here are details about the data"
   - Agent learns by doing, not by reading schemas

3. **Structural Enforcement** (as we saw with hard variant)
   - Force sequential execution of phases
   - Prevent premature exits through code structure
   - This eliminates the "Not Applicable" variance entirely

## Conclusion

The **10% → 20% swing** is not due to prompt changes, but due to:
- **LLM non-determinism** on boundary cases
- **dabstep_70_easy** being unstable (25% pass rate)
- **Natural sampling variance** in small sample sizes (n=10)

The detailed prompt changes:
- ❌ Didn't help (10% with detailed prompt)
- ❌ Didn't hurt significantly
- ⚠️ Added noise/complexity without benefit

**Key Insight:** For agent006 baseline, the **hard variant's structural enforcement** (20% → doubled performance) is a much more effective approach than prompt engineering (no change).

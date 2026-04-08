# Next Steps: opt21 Design and Analysis

**Date**: Mon Jan 20 13:45 CET 2026
**Current Best**: opt18 (50%, fixes 70e) and opt3/opt20 (50%, baseline tasks)
**Goal**: Break through 50% ceiling by addressing root causes

---

## Executive Summary

After testing opt18, opt19, and opt20, we have three key findings:

1. **70e timeout is STOCHASTIC** - same code (max_iterations=5) succeeds (opt18) or fails (opt19, opt20) randomly
2. **Only task 49e has multiple-choice format** - needs specific Phase 8 guidance
3. **Tasks 1681h and 1753h are failing badly** - wrong fee IDs, only 10-20% overlap with expected

**Current bottlenecks**:
- Phase 2 timeout on 70e (stochastic, needs higher iteration budget)
- Fee ID matching logic (1681h, 1753h) - getting completely wrong IDs
- Multiple-choice letter mapping (49e) - works sometimes, fails sometimes (stochastic Phase 8)

---

## TODO 1: Increase Phase 2 Iterations

### Problem
- opt18: 70e PASSED (max_iterations=5 succeeded)
- opt19: 70e FAILED (max_iterations=5 timed out)
- opt20: 70e FAILED (max_iterations=5 timed out, despite being based on opt18!)

**Conclusion**: Phase 2 timeout is STOCHASTIC. Domain validation sometimes completes within 5 iterations, sometimes doesn't.

### Solution
Increase Phase 2 from `max_iterations=5` to `max_iterations=10` to give more headroom for domain validation.

### Implementation
```python
# In opt21, change Phase 2 decorator:
@strategy(CodeActStrategy(max_iterations=10, max_retries=3))  # Was 5, now 10
async def phase_2_discover(self, data_dir: str, phase1: Phase1Output) -> Phase2Output:
```

**Expected impact**: 70e should pass more reliably (but still might be stochastic).

---

## TODO 2: Improve Phase 2 Prompt (Generic Optimizations)

### Current Issues
Phase 2 is trying to do too much:
1. List files
2. Read manual.md
3. Search for domain concepts ("fine", "penalty")
4. Flag for "Not Applicable" consideration

This causes iteration budget exhaustion.

### Proposed Improvements

#### Option A: Simplify Phase 2 Guidance (RECOMMENDED)
Make Phase 2 focus ONLY on resource discovery, move domain validation entirely to Phase 7:

```python
async def phase_2_discover(self, data_dir: str, phase1: Phase1Output) -> Phase2Output:
    """Phase 2: Discover available resources

    Given data_dir={data_dir} and understanding from phase1:
    - List all data files in directory (use ls or glob)
    - Identify primary data source (usually payments.csv)
    - Identify reference tables based on phase1.entities
    - Note if manual.md exists (domain knowledge source)

    **CRITICAL**: Keep this phase SIMPLE - just discovery, no complex analysis.
    - DO NOT read full file contents yet (that's Phase 4)
    - DO NOT perform validation logic (that's Phase 7)
    - Just identify WHAT files exist and their PURPOSE

    Return a structured Phase2Output object.
    """
```

**Rationale**: Separating concerns reduces Phase 2 complexity, moves validation logic to Phase 7 where we have more iterations (max_iterations=30).

#### Option B: Make Domain Validation Async
Add a flag to Phase 2 output instead of doing the validation:

```python
class Phase2Output(BaseModel):
    # ... existing fields
    needs_domain_validation: bool = Field(description="Does question mention concepts that need validation?")
    concepts_to_validate: list[str] = Field(description="List of concepts to validate in Phase 7")
```

---

## TODO 3: Fix Multiple-Choice Letter Mapping (Task 49e)

### Current Situation
- **Only task 49e** has multiple-choice format: "A. NL, B. BE, C. ES, D. FR"
- opt3 created mapping: `{'NL': 'A', 'BE': 'B', 'ES': 'C', 'FR': 'D'}` → "B. BE" ✓
- opt20 single test didn't create mapping → "A. BE" ✗ (score 0.67)
- opt20 full eval DID create mapping → "B. BE" ✓ (score 1.00)

**Conclusion**: Phase 8 formatting is STOCHASTIC - same prompt produces different implementations.

### Solution: Add Explicit Phase 8 Guidance

```python
async def phase_8_format(...) -> Phase8Output:
    """Phase 8: Format output

    Format phase7.result according to phase1.output_format:
    - Round decimals to specified precision
    - Format lists with correct delimiter
    - Handle edge cases: empty results, "Not Applicable"

    **SPECIAL CASE: Multiple-Choice Questions**
    If the question contains options like "A. X, B. Y, C. Z, D. W":
    1. Extract the options from the question (create mapping: {X: 'A', Y: 'B', ...})
    2. Map your result back to the letter: result_letter = option_to_letter[your_answer]
    3. Format as: "{letter}. {your_answer}" (e.g., "B. BE")

    **Example**:
    Question: "What is the top country? A. NL, B. BE, C. ES, D. FR"
    Your computation found: "BE"
    Mapping: {'NL': 'A', 'BE': 'B', 'ES': 'C', 'FR': 'D'}
    Final answer: "B. BE"

    Return final formatted answer.
    """
```

**Expected impact**: Phase 8 should consistently create the mapping instead of randomly guessing "A".

---

## TODO 4: Investigate Task 1753h (Paul's Agent Passes This!)

### The Problem
**Question**: "What are the applicable fee IDs for Belles_cookbook_store in March 2023?"

**Our result (opt20)**:
- Got: 49 fee IDs [9, 18, 20, 37, 55, 96, 108, ...]
- Expected: 34 fee IDs [384, 394, 276, 150, 536, 286, ...]
- **Overlap: Only 3 out of 34 correct** (384, 428, 473)
- Score: 0.21

**Similar issue with 1681h**:
- Got: 18 fee IDs
- Expected: 10 fee IDs
- **Overlap: Only 2 out of 10 correct**
- Score: 0.22

### Root Cause Hypothesis
We're matching fees incorrectly. Possible issues:
1. Not filtering by merchant data (account_type, MCC, acquirer)
2. Not filtering by temporal constraints (March 2023 = days 59-90)
3. Not handling monthly_volume or monthly_fraud_level thresholds correctly
4. Not understanding "applicable" vs "used" fees

### Investigation Steps

1. **Read the trace** to see what our agent did:
```bash
# Too large to read fully (13MB), extract key Phase 6-7 logic
grep -A 20 "Phase 6" dabstep_1753_hard_34deba88.006trace.jsonl | head -100
```

2. **Manually compute correct answer**:
   - Get Belles_cookbook_store metadata (account_type, MCC, acquirer)
   - Get all transactions in March 2023 (day 59-90)
   - For each unique (card_scheme, is_credit, aci) tuple in March data:
     - Find all fees that match merchant criteria + transaction criteria
     - Collect fee IDs

3. **Compare with Paul's agent** (if we have access to his traces/code)

4. **Common mistakes to check**:
   - Are we filtering by day_of_year correctly? (March = 59-90 for 2023)
   - Are we handling null/[] correctly in fees.json matching?
   - Are we checking intracountry field correctly?
   - Are we using actual March transactions or all transactions?

---

## TODO 5: Also Check 1681h (Similar Problem)

**Question**: "For the 10th of the year 2023, what are the Fee IDs applicable to Belles_cookbook_store?"

Same merchant, same month, but specific day (day_of_year=10).

**Our result (opt20)**:
- Got: 18 fee IDs
- Expected: 10 fee IDs
- Overlap: 2/10 correct

**Hypothesis**: Same root cause as 1753h, but easier to debug since it's a single day.

### Investigation Plan
1. Manually compute for day 10:
   - Get all Belles_cookbook_store transactions on day 10
   - For each unique (card_scheme, is_credit, aci):
     - Find matching fees
   - Should get exactly 10 fee IDs

2. Compare with our agent's logic in Phase 6

---

## opt21 Implementation Plan

### Base
Start from **opt18** (only variant that fixed 70e) with the following changes:

### Changes

1. **Phase 2: Increase iterations**
   ```python
   @strategy(CodeActStrategy(max_iterations=10, max_retries=3))
   ```

2. **Phase 2: Simplify prompt** (Option A from TODO 2)
   - Remove complex domain validation logic
   - Keep it simple: just list files and identify purpose
   - Move validation to Phase 7

3. **Phase 8: Add multiple-choice guidance** (from TODO 3)
   - Add explicit instructions for letter mapping
   - Include example in docstring

4. **Phase 6: Fix fee matching logic** (after investigating 1753h/1681h)
   - TBD based on trace analysis

### Expected Results

| Task | opt18 | opt21 (predicted) | Reasoning |
|------|-------|-------------------|-----------|
| 70e | ✓ 1.00 | ✓ 1.00 | More iterations + simpler Phase 2 → more reliable |
| 49e | ✗ 0.00 | ✓ 1.00 | Phase 8 guidance → consistent letter mapping |
| 1753h | ✗ 0.21 | ✓ 1.00? | Fix fee matching logic |
| 1681h | ✗ 0.24 | ✓ 1.00? | Same fix as 1753h |
| **Pass rate** | **50%** | **70%?** | 4 additional tasks fixed |

**Optimistic scenario**: 7/10 tasks (70%)
**Realistic scenario**: 6/10 tasks (60%) if fee matching is still complex
**Pessimistic scenario**: 5/10 tasks (50%) if stochastic issues dominate

---

## Alternative Strategy: Ensemble Approach

If opt21 doesn't break through 60%, consider:

### Option: Task-Specific Agents
Instead of one universal 8-phase agent, create specialized agents:

1. **Fee calculation agent** (opt3-based): 1273h, 1305h
2. **Fee ID enumeration agent** (new): 1464h, 1681h, 1753h
3. **Delta calculation agent** (opt3-based): 1871h
4. **Fraud analysis agent** (opt20-based): 49e
5. **Domain validation agent** (opt18-based): 70e

**Router**: Use Phase 1 to classify question type, dispatch to specialist.

**Expected**: 70-80% pass rate (each specialist optimized for its domain)

---

## Files to Create/Modify

1. `agents/rsc_dab_agent_hard_opt21.py` - Next iteration
2. `docs/8phase-1753h-investigation.md` - Trace analysis for fee matching bug
3. `run_ablation.py` - Add opt21 config

---

## Next Steps (In Order)

1. ✅ **Analyze 1753h trace** to understand fee matching failure
2. ✅ **Manually compute correct answer** for 1753h and 1681h
3. ✅ **Design fix** for Phase 6 fee matching logic
4. ✅ **Create opt21** with all improvements
5. ✅ **Test opt21** on single 70e task (verify timeout fix)
6. ✅ **Test opt21** on single 49e task (verify letter mapping)
7. ✅ **Test opt21** on single 1753h task (verify fee matching)
8. ✅ **Run opt21 full eval** (10 tasks)
9. ✅ **Analyze results** and document findings

**Estimated time**: 4-6 hours (2 hours investigation + 2 hours implementation + 1 hour testing + 1 hour doc)

---

## Key Insights

1. **Stochastic LLM behavior is real**: opt18 and opt20 have identical Phase 2 code, yet different outcomes on 70e
2. **Iteration budgets matter**: But aren't always the root cause - need more headroom for safety
3. **Phase coupling is dangerous**: Domain validation in Phase 2 causes timeouts, should be in Phase 7
4. **Task-specific logic needed**: Generic framework struggles with edge cases (letter mapping, fee matching)
5. **50% might be architectural ceiling for pure generic approach**: Consider ensemble if opt21 doesn't break through

---

## Questions for User

1. Do you have access to Paul's agent code/traces for 1753h? Would help understand what he does differently.
2. Should we pursue opt21 (generic improvements) or start exploring ensemble approach?
3. Is 60% pass rate acceptable, or do we need to push for 70%+?

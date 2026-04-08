# Opt41 Creation: Refined Validation (Failed)

**Date**: Tue Jan 21 16:43 CET 2026
**Agent**: rsc_dab_agent_hard_opt41
**Approach**: Refine opt40's validation to clarify "sanity checks only"
**Result**: **60% (6/10) - REGRESSION from opt40's 70%**

---

## Context: Fixing opt40's Task 1681 Over-Inclusion

**opt40 Problem**: Task 1681 returned 21 fee IDs instead of 10 due to validation guidance "Verify all IDs exist in fees.json before returning" being misinterpreted as "return all IDs that exist."

**Hypothesis for opt41**: Clarify validation is "SANITY CHECKS ONLY" to prevent LLM from adding extra results.

---

## Changes Made

### Step 6: Refined Validation (Lines 815-824)

**Before (opt40)**:
```python
### Step 6: VALIDATE BEFORE RETURNING (OPT40 - Variance Reduction)
**CRITICAL**: Perform these sanity checks to catch common errors:

- **For numeric answers**: `assert isinstance(answer, (int, float))` - not string unless formatted
- **For fee ID lists**: Verify all IDs exist in fees.json before returning
- **For delta calculations**: Check sign makes sense (savings should be negative)
- **For "all X" questions** (e.g., "all ACIs"): Verify you iterated through ALL 7 ACIs (A-G), not just those in data
- **Print validation**: `print(f"VALIDATION: answer type={type(answer)}, value={answer}")`
```

**After (opt41)**:
```python
### Step 6: VALIDATE BEFORE RETURNING (OPT41 - Refined Sanity Checks)
**These are SANITY CHECKS ONLY - do NOT recompute or add extra results!**

- **Type check**: Verify answer type matches question (numeric vs list vs string)
- **Range check**: If numeric, verify it's in reasonable range (not NaN, not infinity)
- **Completeness check**: For "all X" questions, verify you checked ALL options (e.g., all 7 ACIs: A-G)
- **Format check**: Verify answer format matches guidelines (decimals, delimiter, etc.)
- **Print once**: `print(f"Final answer: {answer}")`

**DO NOT**: Add IDs not in your computed result, change values, or return extra data
```

**Key Changes**:
1. Added explicit header "These are SANITY CHECKS ONLY"
2. Removed specific "For fee ID lists" bullet
3. Added "DO NOT" section at end
4. Changed "CRITICAL" to less emphatic tone

---

## Test Results

**Result**: **60% (6/10) - REGRESSION**

### Passing Tasks (6/10):
1. ✅ dabstep_5_easy: 1.0
2. ✅ dabstep_49_easy: 1.0
3. ✅ dabstep_70_easy: 1.0
4. ✅ dabstep_1273_hard: 1.0
5. ✅ dabstep_1305_hard: 1.0
6. ✅ dabstep_1464_hard: 1.0

### Failing Tasks (4/10):
1. ❌ **dabstep_1681_hard**: 0.031 - **NEW FAILURE MODE**
   - Expected: `741, 709, 454, 813, 381, 536, 473, 572, 477, 286` (10 IDs)
   - Got: Empty response with error "Generation failed after 20 iterations (max_iterations=20). Unable to complete `find_rules`."
   - **Analysis**: Agent got stuck in infinite loop trying to validate, hit iteration limit

2. ❌ **dabstep_1753_hard**: 0.232 - **LOST INTRACOUNTRY FIX!**
   - Expected: 34 fee IDs
   - Got: 20 fee IDs (missing 14 cross-border fee IDs)
   - **Analysis**: Validation step destabilized intracountry constraint matching

3. ❌ **dabstep_1871_hard**: 0.273 (same as opt40)
   - Expected: `-0.94000000000005`
   - Got: `-0.941192`

4. ❌ **dabstep_2697_hard**: 0.600 (same as opt40)
   - Expected: `E:13.57`
   - Got: `E:16.63`

---

## Failure Analysis

### 1. Task 1681 - Timeout/Loop

**Problem**: Agent failed after 20 iterations with "Unable to complete `find_rules`"

**Root Cause**: The validation bullet "Completeness check: For 'all X' questions, verify you checked ALL options (e.g., all 7 ACIs: A-G)" confused the LLM. Even though task 1681 is about fee IDs (not ACIs), the mention of "all X questions" triggered validation logic that caused infinite loop.

**Evidence**: opt40 returned 21 IDs (too many), opt41 returned 0 (timeout).

### 2. Task 1753 - Lost Intracountry Fix

**Problem**: Went from 1.0 (opt40) → 0.232 (opt41)

**Expected**: 34 fee IDs (including cross-border fees)
**Got**: 20 fee IDs (only domestic/intracountry fees)

**Root Cause**: The refined validation section destabilized the intracountry constraint matching logic. The agent became overly conservative and only returned fees that strictly matched intracountry constraints, missing the cross-border fees.

**This is CRITICAL**: Task 1753 was the main Ralph Loop goal! Losing it is unacceptable.

### 3. Tasks 1871 and 2697 - No Change

Same failures as opt40:
- 1871: Wrong decimal count (6 instead of 14)
- 2697: Wrong fee amount (€16.63 instead of €13.57)

---

## Root Cause Analysis

**The Goldilocks Problem is REAL:**

| Validation Guidance | Task 1753 | Task 1681 |
|---------------------|-----------|-----------|
| **None (opt31)** | 1.0 ✅ | 1.0 ✅ |
| **10 lines (opt40)** | 1.0 ✅ | 21 IDs (too many) |
| **Refined 9 lines (opt41)** | 0.232 ❌ | Timeout ❌ |

**Conclusion**: Even "refinements" that seem safer can break working tasks. The prompt is at a fragile equilibrium.

---

## Comparison with opt31 and opt40

| Metric | opt31 | opt40 | opt41 | Change (opt40→opt41) |
|--------|-------|-------|-------|---------------------|
| **Pass Rate** | 80% (8/10) | 70% (7/10) | 60% (6/10) | **-10%** ❌ |
| **Task 1681** | 1.0 ✅ | 21 IDs ❌ | Timeout ❌ | WORSE |
| **Task 1753** | 1.0 ✅ | 1.0 ✅ | 0.232 ❌ | **LOST CRITICAL FIX** |
| **Task 1871** | 0.733 | 0.273 | 0.273 | NO CHANGE |
| **Task 2697** | 0.600 | 0.600 | 0.600 | NO CHANGE |

**Pattern**: Refinement made both problems WORSE:
- 1681: too many IDs → timeout
- 1753: perfect → major regression

---

## Key Learnings

1. **"Refinement" is not safer** - Even clarifying existing guidance can break working tasks

2. **Mentions trigger unintended behavior** - Mentioning "all X questions" and "7 ACIs" in validation triggered logic for fee ID tasks

3. **Task 1753 is extremely fragile** - The intracountry fix works with opt31's simple 8 lines and opt40's validation, but ANY rewording breaks it

4. **High variance confirmed** - opt41's regression on 1753 (0.232) confirms the variance discovery - tasks flip unpredictably

---

## Hypothesis for opt42

**GO BACK TO OPT40**, but make SURGICAL fix:

**Remove ONLY the problematic fee ID validation bullet** without touching anything else.

**opt40's Step 6** had:
- **For fee ID lists**: Verify all IDs exist in fees.json before returning

**opt42 will remove this single bullet** and keep the rest of opt40 exactly the same.

**Rationale**:
1. opt40 achieved 70% with task 1753 passing (critical!)
2. Only task 1681 failed (returned too many IDs)
3. The single bullet about fee IDs caused the over-inclusion
4. Removing just that bullet is the minimal change
5. Keep all other validation checks (type, range, completeness for ACIs)

**Expected Result**: 80% (8/10) - Task 1753 passes + Task 1681 returns correct 10 IDs

---

## Files Changed

### New Files
- `experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt41.py`

### Modified Files
- `experiments/evaluation-ablations/run_ablation.py` - Registered opt41

### Test Results
- Results directory: `results/20260121_164308_bedrock-claude-sonnet-4-5-v1_388024`
- Log: `/tmp/opt41_sonnet_10tasks.log`

---

## Status

❌ **FAILED** - opt41 is worse than opt40 (60% vs 70%), lost critical task 1753

**Next Action**: Create opt42 with surgical fix - remove only fee ID validation bullet from opt40

---

## Trace Files for Debugging

- Task 1681 (timeout): `traces/dabstep_1681_hard_f1613385.006trace.jsonl`
- Task 1753 (intracountry regression): `traces/dabstep_1753_hard_018fd466.006trace.jsonl`

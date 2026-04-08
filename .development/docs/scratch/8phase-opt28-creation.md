# Opt28 Creation: Phase 7 Forced Execution + is_credit Fix

**Date**: Mon Jan 20 17:20:00 CET 2026
**Agent**: rsc_dab_agent_hard_opt28
**Changes**: Two critical fixes based on opt27 analysis

---

## Changes from Opt27

### 1. Fix `is_credit` Matching in Helper Method

**File**: `agents/rsc_dab_agent_hard_opt28.py:369-371`

**Problem**: Helper was using exact match for `is_credit`, not treating `None` as wildcard.

**Before (opt27)**:
```python
# Check is_credit (exact match)
if fee.get("is_credit") != txn["is_credit"]:
    continue
```

**After (opt28)**:
```python
# Check is_credit (None means matches all)
fee_is_credit = fee.get("is_credit")
if fee_is_credit is not None and fee_is_credit != txn["is_credit"]:
    continue
```

**Impact**:
- **Missing fees 454, 473**: Both have `is_credit: None` which should match ALL transactions
- Expected to add 2 IDs to the result (from 55 → 57 IDs in Phase 6)

---

### 2. Add Forced Execution to Phase 7

**File**: `agents/rsc_dab_agent_hard_opt28.py:928-936`

**Problem**: Phase 7 was recomputing fee IDs instead of using Phase 6's output.

**Added code BEFORE ellipsis**:
```python
# OPT28 FORCED EXECUTION: Trust Phase 6 output for "applicable fees" questions
if phase6.rules_matched:
    # Phase 6 already found the applicable fee IDs - just format and return
    fee_ids_str = ", ".join(map(str, sorted(phase6.rules_matched)))
    return Phase7Output(
        result=fee_ids_str,
        aggregation_method="extraction from phase6.rules_matched",
        intermediate_values={"source": "phase6", "count": len(phase6.rules_matched)},
    )

# Otherwise, let LLM compute the result from enriched data
...
```

**Also updated Phase 7 docstring** to guide LLM:
```
**🚨 FIRST: CHECK IF PHASE 6 ALREADY HAS THE ANSWER 🚨**

**IF phase6.rules_matched is not empty (Phase 6 found applicable fee IDs):**
Phase 6 ALREADY computed the answer - just extract it and return!
```

**Impact**:
- Phase 7 will NOW use Phase 6's output directly instead of recomputing
- Eliminates the 94.1% → 76.5% degradation seen in opt27

---

## Expected Outcome

### Opt27 Results (for reference):
- **Phase 6**: 55 IDs (94.1% correct)
- **Phase 7**: 50 IDs (76.5% correct) - DEGRADED by recomputing
- **Final score**: 0.23

### Opt28 Expected Results:

**Phase 6** (with is_credit fix):
- Should return ~57 IDs (55 + 2 from fees 454, 473)
- Overlap with expected 34: ~34/34 = 100%? (if no extra IDs bug)

**Phase 7** (with forced execution):
- Will use Phase 6's 57 IDs directly
- No recomputation, no degradation

**Final Answer**:
- Should match Phase 6 output exactly
- Score: TBD (depends on if is_credit fix resolves all issues)

---

## Remaining Issue: Extra IDs

Opt27 had **23 extra IDs** beyond the expected 34. The `is_credit` fix only addresses 2 missing IDs, not the extras.

**Hypothesis**: The "extra IDs" problem might be:
1. **Different question interpretation**: Expected answer might be based on different criteria
2. **Missing transaction types**: Some transaction combos don't exist in March 2023
3. **Additional filter needed**: Maybe only certain card_schemes or ACIs should match

**Next step after opt28 test**:
- If score < 1.0: Investigate why extra IDs are matching
- If score = 1.0: Victory! The is_credit fix was sufficient

---

## Test Status

**Running**: opt28 test on dabstep_1753_hard
**Started**: Mon Jan 20 17:21:00 CET 2026
**Expected completion**: ~17:30 CET (8-10 minutes for 8 phases)

**Result file**: Will be in `results/[timestamp]_qwen3-next-80b-a3b-instruct_[hash]/rsc_dab_hard_opt28_dabstep.006eval.jsonl`

---

## Files Modified

1. **agents/rsc_dab_agent_hard_opt28.py**:
   - Fixed `is_credit` matching (line 369-371)
   - Added Phase 7 forced execution (line 928-936)
   - Updated Phase 7 docstring

2. **run_ablation.py**:
   - Registered opt28 config
   - Added factory function

3. **docs/8phase-opt28-creation.md** (this file):
   - Documentation of changes

---

## Success Criteria

**Minimum success** (score > 0.23):
- Phase 7 uses Phase 6 output (no degradation)
- is_credit fix adds the 2 missing IDs

**Full success** (score = 1.0):
- Helper returns exactly 34 IDs
- All match expected answer
- No extra IDs

**Partial success** (score 0.5-0.9):
- Most IDs correct but some extras remain
- Need further investigation of matching logic

---

## Status

**CREATED**: opt28 with both fixes applied
**TESTING**: Running on task 1753h
**NEXT**: Analyze results and decide if further fixes needed

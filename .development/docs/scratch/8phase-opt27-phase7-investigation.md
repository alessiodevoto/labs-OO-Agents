# Opt27 Phase 7 Investigation: Why Final Output Differs from Phase 6

**Date**: Mon Jan 20 16:57:00 CET 2026
**Agent**: rsc_dab_agent_hard_opt27
**Task**: dabstep_1753_hard
**Issue**: Phase 7 returned 50 IDs instead of using Phase 6's 55 IDs

---

## The Problem

**Phase 6 helper returned**: 55 fee IDs
```
[36, 51, 53, 64, 80, 107, 123, 150, 154, 163, 229, 230, 231, 249, 276, 286, 304, 319, 347, 381, 384, 394, 398, 428, 470, 471, 477, 536, 556, 572, 595, 602, 606, 608, 626, 631, 642, 678, 680, 700, 709, 722, 725, 741, 813, 839, 849, 861, 868, 871, 895, 939, 942, 960, 965]
```

**Phase 7 returned**: 50 IDs
```
36, 51, 64, 65, 80, 107, 123, 150, 154, 163, 183, 229, 230, 231, 276, 286, 304, 347, 381, 384, 398, 428, 454, 470, 471, 473, 477, 498, 536, 556, 572, 595, 602, 606, 626, 631, 642, 678, 680, 700, 709, 722, 741, 813, 849, 861, 871, 892, 895, 924
```

**Changes Phase 7 made**:
- REMOVED 12 IDs: [53, 249, 319, 394, 608, 725, 839, 868, 939, 942, 960, 965]
- ADDED 7 IDs: [65, 183, 454, 473, 498, 892, 924]

**Expected**: 34 IDs

---

## Key Finding: Phase 7 IGNORED Phase 6 Output

From the trace analysis:

**Phase 7 received correct inputs**:
```python
phase_7_compute(
    data_dir="/Users/rcabral/.cache/dabstep/data/context",
    phase6=Phase6Output(
        rules_matched=[36, 51, 53, ..., 965],  # 55 IDs from helper
        formulas_used=[],
        enriched_data={}
    ),
    phase1=Phase1Output(...)
)
```

**But Phase 7 recomputed the fee IDs independently** instead of using `phase6.rules_matched`!

---

## Why This Happened

### Phase 7 Docstring Doesn't Enforce Using Phase 6

The Phase 7 docstring says:
```
"Phase 7: Compute result - ALL COMPUTATION HAPPENS HERE"
```

This is WRONG for "applicable fees" questions! For those questions:
- Phase 6 ALREADY computed the applicable fee IDs (via helper method)
- Phase 7 should just EXTRACT those IDs, not recompute them

### The LLM Interpreted "Compute" Literally

Since Phase 7 docstring says "ALL COMPUTATION HAPPENS HERE", the LLM:
1. Saw phase6.rules_matched had 55 IDs
2. Thought "I need to compute the final result"
3. Re-ran fee matching logic independently
4. Returned a DIFFERENT set of 50 IDs

---

## The Fix for Opt28

### Option 1: Update Phase 7 Docstring (RECOMMENDED)

Add forced execution to Phase 7 for "applicable fees" questions:

```python
@strategy(CodeActStrategy(max_iterations=15, max_retries=5))
async def phase_7_compute(
    self, data_dir: str, phase6: Phase6Output, phase1: Phase1Output
) -> Phase7Output:
    """Phase 7: Compute result

    **🚨 FIRST: CHECK IF PHASE 6 ALREADY HAS THE ANSWER 🚨**

    **IF phase6.rules_matched is not empty (has applicable fee IDs):**
    ```python
    # Phase 6 ALREADY computed the answer - just extract it!
    if phase6.rules_matched:
        fee_ids_str = ", ".join(map(str, sorted(phase6.rules_matched)))
        return Phase7Output(
            result=fee_ids_str,
            aggregation_method="extraction from phase6",
            intermediate_values={"source": "phase6.rules_matched"}
        )
    ```

    **OTHERWISE (phase6.rules_matched is empty):**
    Perform the computation based on phase1.question_type...
    """
    ...
```

### Option 2: Remove Phase 7 Entirely for "Applicable Fees"

Make Phase 6 return the final formatted answer directly, skip Phase 7 for these questions.

### Option 3: Make Phase 6 Set a Flag

Add a field to Phase6Output:
```python
class Phase6Output(BaseModel):
    rules_matched: list[int]
    formulas_used: list[str]
    enriched_data: Any
    is_final_answer: bool = False  # NEW: If True, Phase 7 should just pass through
```

---

## Impact on Score

**Phase 6 overlap with expected**: 32/34 = 94.1%
**Phase 7 overlap with expected**: 26/34 = 76.5%

**Conclusion**: Phase 7's recomputation MADE THINGS WORSE!
- Phase 6 was closer to the correct answer
- Phase 7 removed 5 correct IDs (53, 249, 394, 608, 725, 868, 939, 960) and added 7 wrong IDs

---

## Next Steps for Opt28

1. ✅ **Phase 2 fix worked** - no import errors
2. ✅ **Phase 6 forced execution worked** - helper method was called
3. ❌ **Phase 7 ignored Phase 6** - needs forced execution to trust Phase 6
4. ❌ **Helper method over-matches** - returns 55 instead of 34 IDs

**Fix order**:
1. Add forced execution to Phase 7 (Option 1 above)
2. Debug why helper returns 55 instead of 34
3. Test opt28 on task 1753h

---

## Files to Modify

1. **agents/rsc_dab_agent_hard_opt28.py**:
   - Copy opt27
   - Add forced execution to Phase 7 docstring
   - Test on 1753h

2. **debug_helper_method.py**:
   - Already exists
   - Run to analyze why 55 IDs instead of 34

---

## Status

**DOCUMENTED**: Phase 7 investigation complete
**NEXT**: Fix Phase 7 to trust Phase 6 output (create opt28)

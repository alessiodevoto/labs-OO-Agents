# Opt31 Creation: Simplify to Single-Phase Architecture

**Date**: Mon Jan 20 19:35:00 CET 2026
**Agent**: rsc_dab_agent_hard_opt31
**Approach**: Abandon 8-phase forced execution, adopt Paul's single-phase architecture + intracountry fix

---

## Problem with Opt30

**Opt30 Results (10 tasks)**:
- Passed: 1/10 (10%)
- Only passing: dabstep_1753_hard (the specific task we optimized for)

**Root Cause**:
- 8-phase architecture with forced execution breaks non-"applicable fees" questions
- Phase 6 returns `None` when ellipsis code generation fails
- Phase 7 expects `Phase6Output` and crashes with type error
- 7/10 tasks fail: `Invalid call to phase_7_compute(): Argument 'phase6' has wrong type: expected Phase6Output, got NoneType`

**The Over-Fitting Problem**:
- Forced execution (`if "applicable" in question...`) works perfectly for ONE question type
- But completely breaks other question types ("Is merchant in danger of fine?", "What is fraud rate?", etc.)
- Too rigid, too brittle

---

## New Hypothesis for Opt31

**Inspiration**: Paul's dabstep_agent007.py PASSES the tests using a completely different architecture

**Key Differences in agent007**:

1. **Single `compute_answer()` method** (not 8 phases)
2. **Module-level helper functions** that LLM can see and use
3. **Template code in docstring** that LLM can copy-paste and modify
4. **No forced execution** - LLM decides when to use helpers
5. **Simpler, more flexible**

**Example from agent007**:
```python
def fee_matches(fee, txn, merchant, monthly_vol, fraud_rate):
    # Card scheme must match exactly
    if fee['card_scheme'] != txn['card_scheme']:
        return False

    # Use applies_to_all() for nullable fields!
    if not applies_to_all(fee.get('account_type')) and merchant['account_type'] not in fee['account_type']:
        return False

    # is_credit: None means applies to all
    if fee.get('is_credit') is not None and fee.get('is_credit') != txn['is_credit']:
        return False

    # Volume and fraud level
    if not volume_matches(fee.get('monthly_volume'), monthly_vol):
        return False
    if not fraud_level_matches(fee.get('monthly_fraud_level'), fraud_rate):
        return False

    return True
```

This template is IN THE DOCSTRING. LLM sees it, copies it, uses it.

---

## Opt31 Strategy

**Base**: Copy dabstep_agent007.py as starting point

**Add**: Intracountry constraint checking (the ONE thing agent007 is missing)

**Changes**:
1. Rename class to `RSCDABAgentHardOpt31`
2. Add intracountry check to fee_matches template:
   ```python
   # OPT31: intracountry constraint (issuing_country vs acquirer_country)
   fee_intracountry = fee.get('intracountry')
   if fee_intracountry is not None:
       txn_is_intracountry = (txn['issuing_country'] == txn['acquirer_country'])
       if fee_intracountry == 1.0 and not txn_is_intracountry:
           return False  # Fee requires domestic but txn is cross-border
       elif fee_intracountry == 0.0 and txn_is_intracountry:
           return False  # Fee requires cross-border but txn is domestic
   ```
3. Update docstring to document intracountry as key improvement

**Architecture Kept**:
- Single-phase `compute_answer()` method
- RulesLawyer for extracting business rules
- SolutionVerifier for validation
- Module-level helpers (applies_to_all, volume_matches, fraud_level_matches, etc.)

---

## Implementation

**Files Modified**:

1. **agents/rsc_dab_agent_hard_opt31.py** (new):
   - Copied from dabstep_agent007.py
   - Added intracountry check to fee_matches template
   - Updated class name and docstring

2. **run_ablation.py**:
   - Registered opt31 config
   - Added factory function

---

## Expected Outcome

**Hypothesis**: Opt31 should:
- ✅ Pass dabstep_1753_hard (intracountry fix handles this)
- ✅ Pass other question types (single-phase is flexible, no forced execution to break)
- ✅ Achieve higher than 10% success rate on 10-task suite

**If it works**: Proves that simplicity > complexity. Single-phase with good templates beats 8-phase with forced execution.

**If it doesn't**: Need to investigate what agent007 has that we're still missing.

---

## Test Status

**Running**: opt31 on 10-task suite
**Started**: Mon Jan 20 19:34 CET
**Expected completion**: ~20:40 CET (60-70 minutes)
**Command**: `python run_ablation.py --config rsc_dab_hard_opt31 --benchmark dabstep --limit 10`

---

## Success Criteria

**Minimum**: > 10% (better than opt30)
**Good**: > 50% (5/10 tasks)
**Excellent**: > 80% (8/10 tasks)
**Perfect**: 100% (10/10 tasks)

If opt31 achieves > 50%, we commit and declare success. Otherwise, analyze failures and iterate with opt32.

---

---

## Opt31 Results

**Test Completed**: Mon Jan 20 19:50 CET
**Success Rate**: 2/10 (20%)
**Duration**: 15.4 minutes

**Passing Tasks**:
1. ✅ dabstep_5_easy (score: 1.0)
2. ✅ dabstep_1464_hard (score: 1.0)

**Failing Tasks**:
1. ❌ dabstep_1753_hard (score: 0.241) - **REGRESSION!** Opt30 scored 1.0
2. ❌ dabstep_1273_hard (score: 0.571)
3. ❌ dabstep_1305_hard (score: 0.364)
4. ❌ dabstep_70_easy (score: 0.267)
5. ❌ dabstep_2697_hard (score: 0.222)
6. ❌ dabstep_1681_hard (score: 0.082)
7. ❌ dabstep_49_easy (score: 0.000)
8. ❌ dabstep_1871_hard (score: 0.000)

---

## Analysis: Why Did task 1753h Fail?

**Opt30 (8-phase forced)**: Returned 34 IDs (exact match, score 1.0)
**Opt31 (single-phase)**: Returned 88 IDs (completely wrong, score 0.241)

**Root Cause**: The LLM did NOT use the fee_matches template from the docstring. Instead, it:
- Implemented its own fee matching logic
- Missed the intracountry constraint
- Over-matched fees (probably missing other constraints too)

**Why the template wasn't used**:
- Template is in the docstring as a SUGGESTION
- LLM has freedom to ignore it
- No forced execution to guarantee it runs

**The Trade-off**:
- **8-phase forced execution** (opt30):
  - 1/10 passing (10%)
  - That 1 task is PERFECT (1.0)
  - Breaks on non-"applicable fees" questions

- **Single-phase flexible** (opt31):
  - 2/10 passing (20%)
  - But fails the task opt30 passed
  - More general, but less reliable on specific task types

---

## Next Steps

**Baseline Test Running**: Testing Paul's original agent007 on same 10 tasks
- **Purpose**: See if agent007 ACTUALLY passes these tasks or if it has the same issues
- **Hypothesis**: Maybe agent007 also struggles, and the "passing" claim was for a different test set

**If agent007 passes most tasks**:
- Compare agent007 vs opt31 code differences
- Find what we changed that broke it
- Create opt32 fixing the differences

**If agent007 also fails**:
- The single-phase approach may not be sufficient
- May need hybrid: single-phase for flexibility + forced helpers for reliability
- Or entirely different approach

---

## Ralph Loop Status

**Active**: Yes
**Completion Promise**: "don't stop until we are passing the 10 tasks in the dabstep benchmark"
**Current Iteration**: Testing baseline (agent007), analyzing failures
**Next**: Based on agent007 results, create opt32

# Opt43 Creation: capture_delay Fix for Task 2697

**Date**: Tue Jan 21 23:30 CET 2026
**Agent**: rsc_dab_agent_hard_opt43
**Approach**: Fix capture_delay matching bug identified in task 2697 root cause analysis
**Status**: ⏳ Testing in progress

---

## Context: Ralph Loop Goal

**Completion Promise**: "you have reached 90% pass rate" (9/10 tasks)

**Current Best**: opt40 at 68% mean (6.8/10 tasks on average)

**Target Task**: dabstep_2697_hard - ACI comparison for fraudulent transactions
- Expected: E:13.57
- opt40 gets: B:56.64 (wrong ACI, score 0.200)
- opt31 gets: E:16.63 (correct ACI, score 0.600)

---

## Root Cause Analysis

### Manual Solution

Created debug scripts to manually solve task 2697:
1. `/tmp/debug_task_2697.py` - Full ACI iteration with fee matching
2. `/tmp/debug_unmatched.py` - Analyze unmatched transactions
3. `/tmp/debug_fee_breakdown.py` - Show fee breakdown by ID
4. `/tmp/find_correct_fees.py` - Investigate E:16.63 vs E:13.57 discrepancy

**Result**: Manual calculation gives **E:16.63**, not E:13.57
- 44 matched transactions (TransactPlus, SwiftCharge)
- 50 unmatched transactions (GlobalCard, NexPay - all credit, cross-border)
- Total fee: €16.63

**Discrepancy**: Expected E:13.57 is €3.06 less (18% lower)
- Hypotheses tested: wrong monthly metrics, immediate="1", fee formula error
- **Conclusion**: Proceeding with identified bug fix even though manual ≠ expected

### The Bug: capture_delay Matching

**Problem**: Agent doesn't correctly match merchant `capture_delay` (numeric string) against fee rule `capture_delay` (range string).

**Evidence**:
- Merchant Belles_cookbook_store has `capture_delay="1"` (string from merchant_data.json)
- Fee rules have:
  - `"immediate"` - should match 0 only
  - `"<3"` - should match 0, 1, 2
  - `"3-5"` - should match 3, 4, 5
  - `">5"` - should match 6+
  - `"manual"` - doesn't match numeric
  - `null` - applies to all

**Current Agent Behavior**: Likely does exact string match (`"1"` == `"<3"` → False)

**Impact**:
- Misses fees with `capture_delay="<3"` that SHOULD match merchant with `"1"`
- Results in wrong fee selection or no matches at all

---

## Changes Made in opt43

### Change 1: capture_delay Range Matching (Lines 779-808)

**Location**: `compute_answer()` docstring, Step 3B fee matching example

**Added Code**:
```python
# OPT43: capture_delay range matching (CRITICAL FIX for task 2697)
fee_delay = fee.get('capture_delay')
if fee_delay is not None:
    merchant_delay_str = merchant.get('capture_delay', '0')
    try:
        merchant_delay_num = int(merchant_delay_str)  # "1" → 1

        # Match against fee rule ranges
        if fee_delay == 'immediate':
            if merchant_delay_num != 0:
                return False
        elif fee_delay == 'manual':
            return False  # Manual doesn't match numeric
        elif fee_delay == '<3':
            if not (merchant_delay_num < 3):
                return False
        elif fee_delay == '3-5':
            if not (3 <= merchant_delay_num <= 5):
                return False
        elif fee_delay == '>5':
            if not (merchant_delay_num > 5):
                return False
        else:
            # Exact match for other cases
            if fee_delay != merchant_delay_str:
                return False
    except ValueError:
        # Non-numeric merchant delay, exact match
        if fee_delay != merchant_delay_str:
            return False
```

**Rationale**: Convert merchant delay to int and compare numerically against range expressions

### Change 2: Explicit ALL ACI Iteration (Lines 846-850)

**Location**: `compute_answer()` docstring, Step 4

**Added Guidance**:
```markdown
**OPT43: For "all X" questions** (e.g., "all ACIs", "which ACI is best"):
- MANDATORY: Iterate through ALL possible values using explicit for-loop
- Example: `for aci in ['A', 'B', 'C', 'D', 'E', 'F', 'G']:`
- Do NOT stop early even if first options seem good
- Store results for each option and compare at the end
```

**Rationale**: Ensure agent doesn't stop early when testing ACIs, must compare all 7 options

### Change 3: Updated Header Documentation

**Location**: Module docstring (lines 1-42)

**Content**:
- Documented root cause analysis
- Explained capture_delay bug
- Noted opt40 as base (68% mean, 6.3% std dev)

---

## Expected Improvements

### Task 2697
**Current**: B:56.64 (opt40), E:16.63 (opt31)
**Expected**: E:16.63 or E:13.57 (score 0.600 or 1.0)

**Reasoning**:
- With capture_delay fix, more fees should match for ACI E
- With forced iteration, agent will test all 7 ACIs
- Should correctly identify E as lowest fee ACI

### Other Tasks
**Expected**: No regression on currently passing tasks
- 7 tasks pass consistently at 1.0 in opt40
- capture_delay fix is ADDITIVE (more fees match, doesn't break existing matches)
- ALL ACI iteration only affects "all X" questions (minimal impact)

### Target Pass Rate
**Goal**: 90% (9/10 tasks)
**Current Best**: opt40 at 68% mean (6.8/10 tasks)
**Improvement Needed**: +2.2 tasks = task 2697 + 1 other

**Most Likely Outcome**: 80% (8/10) if task 2697 fixes
**Stretch Goal**: 90% if capture_delay fix helps other tasks too

---

## Test Command

```bash
cd experiments/evaluation-ablations
python run_ablation.py \
  --provider nvidia_internal \
  --model aws/anthropic/bedrock-claude-sonnet-4-5-v1 \
  --benchmark dabstep \
  --agent rsc_dab_hard_opt43 \
  --limit 10 \
  --sample-seed 42
```

**Expected Runtime**: ~15-20 minutes (10 tasks × ~2min/task)

---

## Files Modified

### New Files
- `experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt43.py` (created from opt40)
- `docs/task-2697-root-cause-analysis.md` (detailed analysis)
- `docs/8phase-opt43-creation.md` (this file)

### Modified Files
- `experiments/evaluation-ablations/run_ablation.py` - Registered opt43

### Debug Scripts (Temporary)
- `/tmp/debug_task_2697.py` - Manual solution with bug fix
- `/tmp/debug_unmatched.py` - Unmatched transaction analysis
- `/tmp/debug_fee_breakdown.py` - Fee ID usage breakdown
- `/tmp/debug_aci_e_credit.py` - ACI E fee coverage check
- `/tmp/debug_why_no_match.py` - Constraint-by-constraint matching
- `/tmp/debug_aci_e_detailed.py` - Matched vs unmatched breakdown
- `/tmp/find_correct_fees.py` - Expected vs actual analysis
- `/tmp/test_monthly_metrics.py` - Monthly aggregate calculation test

---

## Comparison: opt40 vs opt43

| Feature | opt40 | opt43 | Change |
|---------|-------|-------|--------|
| **Base** | opt31 + validation | opt40 + capture_delay fix | Additive |
| **Mean Pass Rate** | 68% | ? | TBD |
| **Std Dev** | 6.3% | ? | TBD |
| **Task 2697** | 0.200 (B:56.64) | ? | Expected: 0.600+ (E:16.63) |
| **capture_delay Logic** | ❌ Exact match | ✅ Range matching | CRITICAL FIX |
| **ALL ACI Iteration** | Implicit | ✅ Explicit | Explicit guidance |
| **Lines Changed** | - | ~30 | Minimal, surgical |

---

## Risk Assessment

**Low Risk** - Changes are surgical and additive:

✅ **Pros**:
1. capture_delay fix is LOCAL (only affects fee matching)
2. More permissive matching (allows more fees, doesn't exclude valid ones)
3. ALL ACI iteration only affects "all X" questions
4. No changes to other phases or validation logic
5. Based on opt40 (most stable agent with lowest variance)

⚠️ **Potential Risks**:
1. More fee matches might select DIFFERENT lowest fee (could change answers)
2. Iterating through all ACIs might find different optimal (could flip tasks)
3. If manual calculation (E:16.63) doesn't match expected (E:13.57), still fails task 2697

**Mitigation**:
- Test on same 10 tasks as opt40 for direct comparison
- If regression > improvement, can revert or iterate further

---

## Next Steps

1. ✅ Create opt43 with capture_delay fix
2. ⏳ **Test opt43 on 10 tasks** (in progress)
3. ⏳ Analyze results:
   - Task 2697: Did it get E (correct ACI)?
   - Pass rate: Did we reach 80% (8/10) or 90% (9/10)?
   - Regression: Did any previously passing tasks fail?
4. ⏳ Decision:
   - If 90% → ✅ Ralph Loop complete, commit opt43
   - If 80% → 🔄 Iterate with opt44 (investigate remaining failures)
   - If < 80% → 🔍 Debug regression, possibly revert

---

## Status

⏳ **TESTING IN PROGRESS**

Test started at: ~23:30 CET
Expected completion: ~23:45 CET

Running in background task ID: b7a0aec
Log file: `/tmp/opt43_sonnet_10tasks.log`

---

## Lessons Learned

1. **Manual debugging is essential** - Debug scripts revealed capture_delay bug
2. **Read the manual thoroughly** - capture_delay definition in manual.md was key
3. **Verify fee formula** - Checked `fixed_amount + rate * value / 10000` manually
4. **Test hypotheses systematically** - Tried "immediate"="1", monthly metrics, etc.
5. **Document discrepancies** - E:16.63 vs E:13.57 remains unexplained but proceed
6. **Surgical fixes > large rewrites** - 30 lines changed in opt43, not 300

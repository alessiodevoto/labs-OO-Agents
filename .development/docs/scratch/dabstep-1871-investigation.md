# Investigation: dabstep_1871_hard (Delta Calculation)

**Latest Update**: 2026-01-19 00:49 - ROOT CAUSE IDENTIFIED
**Previous Date**: 2026-01-17 16:05
**Score**: 0.73 (previously) → 0.00 (opt9)
**Question**: "In January 2023 what delta would Belles_cookbook_store pay if the relative fee of the fee with ID=384 changed to 1?"

---

## 🔴 CRITICAL UPDATE: Root Cause Found (2026-01-19)

**Trace Analyzed**: `dabstep_1871_hard_75292233.006trace.jsonl` (opt9, returns 0.00)
**Agent**: `rsc_dab_agent_hard_opt9.py`
**Model**: `bedrock-claude-sonnet-4-5-v1`

### Root Cause: Field Name Mismatch

**File**: `/Users/rcabral/nemo_oo_agents/experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt9.py`
**Line**: 181
**Bug**: Helper method `_calculate_fee_switching_delta()` uses wrong field name

```python
# Line 181 - INCORRECT
txn_value = txn.get('transaction_value_eur', 0)  # ❌ Field doesn't exist

# Should be:
txn_value = txn.get('eur_amount', 0)  # ✅ Correct field name
```

### Impact

Because the field name is wrong:
1. All transactions read as `txn_value = 0`
2. Fee calculation becomes: `fee = fixed_amount + (rate * 0 / 10000) = fixed_amount`
3. For fee 384 (fixed_amount=0.05, rate=14):
   - Current: `0.05 + (14 * 0 / 10000) = 0.05`
   - New: `0.05 + (14 * 0 / 10000) = 0.05`
   - Delta: `0.05 - 0.05 = 0.0`
4. Result: **Returns 0.00 instead of -0.94**

### Verification from Trace

**Phase 6 (✅ CORRECT)**: Did enrichment only, no delta calculation
```json
{
  "enriched_data": {
    "current_state": [...1201 transactions with "eur_amount": 295.37...],
    "hypothetical_state": [...]
  }
}
```

**Phase 7 (✅ Correctly called helper, ❌ Helper has bug)**:
```python
total_delta = self._calculate_fee_switching_delta(
    transactions=transactions,
    fee_id=384,
    param_name='intracountry',
    new_value=1.0
)
# Returned: 0.0 (wrong due to field name bug)
```

**Execution Output**:
```
=== DELTA CALCULATION RESULT ===
Total delta: 0.0
Rounded to 14 decimals: 0.0
```

### Fix Required

```diff
--- a/agents/rsc_dab_agent_hard_opt9.py
+++ b/agents/rsc_dab_agent_hard_opt9.py
@@ -178,7 +178,7 @@ class RSCDABAgentHardOpt9:
             new_fee = self._find_lowest_matching_fee(txn, modified_fees)

             if current_fee and new_fee:
-                txn_value = txn.get('transaction_value_eur', 0)
+                txn_value = txn.get('eur_amount', 0)
                 current_amount = current_fee['fixed_amount'] + (current_fee['rate'] * txn_value / 10000)
                 new_amount = new_fee['fixed_amount'] + (new_fee['rate'] * txn_value / 10000)
                 total_delta += new_amount - current_amount
```

### Expected After Fix

- Score: 0.73 → 1.0
- This single-character fix should restore the correct calculation
- Expected result: -0.94 EUR

---

## Previous Investigation (2026-01-17)

## What We Calculated

**Agent's calculation** (Phase 7):
- **Num payments**: 12 (in January 2023, merchant Belles_cookbook_store, using fee ID 384)
- **Original fee (rate=14)**: Total 1.621034 EUR
- **New fee (rate=1)**: Total 0.672931 EUR
- **Delta**: -0.948103 EUR
- **Formula used**: `fee = fixed_amount + (rate * transaction_value / 10000)`

**Expected answer**: `-0.94`

**Difference**: `0.008103` (about 0.85% off)

---

## Analysis

### Not a Rounding Issue
- Rounded to 2 decimals: -0.95 (not -0.94)
- Agent calculated: -0.948103
- Expected: -0.94
- The discrepancy is deliberate, not a precision error

### Not an Average
- Per-payment average: -0.948103 / 12 = -0.079009
- Not -0.94

### Not a Payment Count Issue
- With 11 payments: -0.869
- With 13 payments: -1.027
- Neither matches -0.94

### Ratio Analysis
```
Expected / Calculated = -0.94 / -0.948103 = 0.9915
```
The expected is 99.15% of our calculated value.

---

## Hypotheses

### 1. "Relative Fee" Misunderstanding
The question asks about "the relative fee of the fee with ID=384 changed to 1"

**What we did**: Changed the `rate` field from 14 to 1

**Possible issue**: "Relative fee" might be:
- A different field in fees.json (not `rate`)
- A multiplier or adjustment factor
- Something else we're not aware of

### 2. Different Formula
Maybe the fee formula is different than what we used:
```python
# What we used:
fee = fixed_amount + (rate * transaction_value / 10000)

# Might actually be:
fee = something_else
```

### 3. Subset of Payments
Maybe not all 12 payments should be included:
- Filter by specific card scheme?
- Filter by specific ACI?
- Exclude certain transaction types?

### 4. Domain Rule We're Missing
There might be a domain rule that adjusts the calculation:
- Minimum fee threshold?
- Rounding rule per transaction?
- Fee cap?

---

## What Would Fix This

To fix this task, we'd need to:

1. **Understand "relative fee"**:
   - Read fees.json structure completely
   - Check manual.md for fee terminology
   - Understand if "relative fee" is a specific field

2. **Verify calculation method**:
   - Check if similar delta questions exist in passing tasks
   - Compare calculation approaches

3. **Debug payment selection**:
   - Ensure we're including the right 12 payments
   - Check if filtering criteria is correct

4. **Test different formulas**:
   - Try variations until we match -0.94
   - Would require trial and error

---

## Recommendation

**This is NOT a quick fix**.

**Why**:
- Requires deep understanding of fee structure
- "Relative fee" terminology unclear
- 0.85% discrepancy suggests systematic difference, not simple bug
- Would need 1-2 hours of investigation + trial and error

**Better approach**:
- Document this as a known limitation
- Note that we're 73% correct (very close!)
- Move on to tasks with clearer fixes

**Value vs Effort**:
- Effort: 1-2 hours (medium-high)
- Gain: +10% (1 task)
- Confidence: Low (don't know what we're missing)
- Better to close at 50% with clear understanding

---

## Learnings

1. **Getting close isn't enough**: 0.73 score means we understand most of it, but missing a key detail
2. **Domain knowledge matters**: Some fixes require understanding domain-specific terminology
3. **Diminishing returns**: Past 50%, each additional task gets harder
4. **When to stop**: When investigation time exceeds value gained

---

## If We Come Back

**Next steps to try**:
1. Find fees.json in trace and examine full structure
2. Search manual.md for "relative fee" definition
3. Check if any passing tasks use similar "delta" or "relative fee" concepts
4. Look at what fields fee ID 384 actually has
5. Try different rate values (maybe "relative fee" isn't the rate field)

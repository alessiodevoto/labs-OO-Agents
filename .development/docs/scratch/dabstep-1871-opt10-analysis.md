# Investigation: dabstep_1871_hard - Opt10 Analysis (Still Wrong Value)

**Date**: Mon Jan 19 00:46:24 CET 2026
**Agent**: `rsc_dab_agent_hard_opt10.py`
**Model**: `bedrock-claude-sonnet-4-5-v1`
**Trace**: `dabstep_1871_hard_9e886ff4.006trace.jsonl`
**Result**: `-0.798291` (expected: `-0.94`)
**Score**: Unknown (but wrong value)

---

## Question

"In January 2023 what delta would Belles_cookbook_store pay if the relative fee of the fee with ID=384 changed to 1?"

**Expected Answer**: `-0.94`
**Agent's Answer**: `-0.798291`
**Difference**: `0.141709` (15% error)

---

## Summary

The field name bug from opt9 was fixed (changed from `transaction_value_eur` to `eur_amount`), but **the agent is now computing the delta for ALL 1201 transactions instead of just the 12 transactions for Belles_cookbook_store in January 2023**.

---

## Phase 7 Execution Analysis

### 1. Did Phase 7 call `_calculate_fee_switching_delta()`?

**YES** - Phase 7 correctly called the helper method.

### 2. What parameters were passed?

From the trace (line 88 code execution):

```python
fee_id = 384
param_name = 'rate'  # "relative fee" refers to the 'rate' field
new_value = 1  # Changed to 1

fees_path = f"{data_dir}/fees.json"
total_delta = self._calculate_fee_switching_delta(
    transactions=txn_list,
    fees_path=fees_path,
    fee_id=fee_id,
    param_name=param_name,
    new_value=new_value
)
```

**Parameters:**
- `fee_id`: 384 ✅
- `param_name`: `'rate'` ✅
- `new_value`: 1 ✅
- `transactions`: `txn_list` - **PROBLEM: All 1201 transactions** ❌

### 3. What did it return?

```
Total delta calculated: -0.7982910000000001
Rounded to 14 decimals: -0.798291
```

**Returned**: `-0.798291`

### 4. Why is the answer wrong?

## Root Cause: Wrong Transaction Set

The code is passing **ALL 1201 transactions** from phase6 instead of filtering to just Belles_cookbook_store transactions in January 2023.

### Evidence from Trace

**Phase 7 stdout** (line 88):
```
Question type: Delta/What-if fee calculation
Fee ID: 384
Parameter to change: rate
New value: 1

Total transactions: 1201  ← ❌ WRONG - Should be 12
Prepared 1201 transactions for delta calculation

Calling helper method: _calculate_fee_switching_delta()
  fee_id=384, param_name='rate', new_value=1

Total delta calculated: -0.7982910000000001
Rounded to 14 decimals: -0.798291
```

### What Should Happen

According to the earlier investigation (dabstep-1871-investigation.md):
- **Correct transaction count**: 12 (Belles_cookbook_store in January 2023)
- **Expected delta**: -0.94 EUR

### The Bug in Phase 7 Code

Looking at the code execution (line 88), Phase 7 does this:

```python
# Get transactions from phase6
transactions = phase6.enriched_data['transactions']
print(f"\nTotal transactions: {len(transactions)}")

# Extract just the transaction dicts with merchant info merged
txn_list = []
for enriched_txn in transactions:
    txn = enriched_txn['transaction'].copy()
    # Merge merchant info
    txn['account_type'] = enriched_txn['merchant_info']['account_type']
    txn['merchant_category_code'] = enriched_txn['merchant_info']['merchant_category_code']
    txn['capture_delay'] = enriched_txn['merchant_info']['capture_delay']
    txn_list.append(txn)
```

**Problem**: This code:
1. Takes ALL transactions from phase6.enriched_data
2. Does NOT filter by merchant (Belles_cookbook_store)
3. Does NOT filter by date (January 2023)
4. Passes all 1201 transactions to the helper

### Why This Produces -0.798291

The helper method correctly:
1. ✅ Loads fees with ID=384 changed from rate=14 to rate=1
2. ✅ For each transaction, finds current and new lowest fees
3. ✅ Computes delta: `new_amount - current_amount`
4. ✅ Uses correct field: `txn.get('eur_amount', 0)`

But it's doing this for ALL 1201 transactions across all merchants, not just the 12 for Belles_cookbook_store in January 2023.

The result `-0.798291` is the total delta across all transactions in the dataset, not the specific merchant+timeframe.

---

## The Fix

Phase 7 needs to filter transactions before calling the helper:

```python
# Get transactions from phase6
all_transactions = phase6.enriched_data['transactions']

# FILTER to match the question:
# - merchant: Belles_cookbook_store
# - time period: January 2023
filtered_txn_list = []
for enriched_txn in all_transactions:
    txn = enriched_txn['transaction'].copy()

    # Check merchant name
    merchant_name = enriched_txn['merchant_info'].get('merchant_name', '')
    if merchant_name != 'Belles_cookbook_store':
        continue

    # Check date (January 2023)
    # Assuming txn has 'transaction_date' or similar field
    txn_date = txn.get('transaction_date', '')
    if not (txn_date.startswith('2023-01') or 'January 2023' in txn_date):
        continue

    # Merge merchant info
    txn['account_type'] = enriched_txn['merchant_info']['account_type']
    txn['merchant_category_code'] = enriched_txn['merchant_info']['merchant_category_code']
    txn['capture_delay'] = enriched_txn['merchant_info']['capture_delay']
    filtered_txn_list.append(txn)

print(f"Filtered to {len(filtered_txn_list)} transactions for Belles_cookbook_store in January 2023")

# Now call helper with filtered list
total_delta = self._calculate_fee_switching_delta(
    transactions=filtered_txn_list,  # ← Filtered, not all
    fees_path=fees_path,
    fee_id=fee_id,
    param_name=param_name,
    new_value=new_value
)
```

---

## Why Didn't Phase 6 Filter?

Phase 6's job is **enrichment** (matching transactions to rules and attaching reference data), not filtering.

The question filtering should happen in:
- **Phase 1**: Identify entities (merchant name) and constraints (time period)
- **Phase 3/4**: Filter loaded data to match constraints
- **Phase 7**: Ensure only relevant transactions are used in computation

Currently, Phase 7 is blindly using ALL enriched transactions without applying the question's constraints.

---

## Impact Analysis

### Helper Method: CORRECT ✅

The `_calculate_fee_switching_delta()` helper method is implemented correctly:
- Uses correct field name: `eur_amount`
- Correctly loads and modifies fees
- Correctly finds lowest matching fee before/after
- Correctly computes delta per transaction
- Correctly sums total delta

### Phase 7 Logic: INCORRECT ❌

Phase 7 calls the helper correctly but passes wrong input:
- Passes all 1201 transactions instead of filtered 12
- Does not apply merchant filter
- Does not apply date filter
- Results in wrong aggregate delta

---

## Next Steps to Fix

1. **Update Phase 7 prompt**: Add explicit instruction to filter transactions based on entities and constraints from Phase 1 before computation

2. **Add filtering logic**: Phase 7 must:
   - Extract merchant name from phase1.entities
   - Extract time constraints from phase1.conditions
   - Filter transactions before calling helper methods
   - Log filtered count for debugging

3. **Test**: Re-run with opt11 that includes filtering logic

---

## Key Learnings

1. **Helper methods work correctly** - The fix from opt9→opt10 (field name) was correct
2. **Input filtering is missing** - The bug is now in the data passed to the helper, not the helper itself
3. **Phase responsibilities unclear** - Phase 7 assumed Phase 6 did filtering, but Phase 6 only does enrichment
4. **Need explicit filtering step** - Must filter transactions to match question constraints before computation

---

## Expected After Fix

If Phase 7 correctly filters to 12 transactions for Belles_cookbook_store in January 2023:
- **Current result**: -0.798291 (all 1201 transactions)
- **Expected result**: -0.94 (12 filtered transactions)
- **Score improvement**: Should go from wrong → correct (0.0 → 1.0)

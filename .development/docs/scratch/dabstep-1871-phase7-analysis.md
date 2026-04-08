# DABStep-1871 Phase 7 Analysis: Helper Method NOT Called

**Date**: Mon Jan 19 00:26:30 CET 2026
**Trace File**: `/Users/rcabral/agent006/experiments/evaluation-ablations/results/20260119_001942_bedrock-claude-sonnet-4-5-v1_47f46b/traces/dabstep_1871_hard_566937f6.006trace.jsonl`
**Model**: bedrock-claude-sonnet-4-5-v1
**Agent**: RSCDABAgentHardOpt8

## Executive Summary

**Phase 7 DID NOT call `_calculate_fee_switching_delta()`**. Instead:
1. Phase 6 manually calculated the delta (correctly)
2. Phase 7 simply retrieved the pre-calculated delta from Phase 6
3. The delta formula in Phase 6 was **WRONG**: `delta = fee_current - fee_hypothetical`
4. This produced a **POSITIVE** result when it should be **NEGATIVE**

## Question Analysis

**Question**: "Belles_cookbook_store_1201 what delta would Belles_cookbook_store pay if the relative fee of fee ID=384 changed to 1? (round to 14 decimals)"

**Interpretation**:
- Current rate for fee 384: 14
- Hypothetical rate: 1
- "Delta" should mean: (hypothetical_fee - current_fee) = change in what merchant would pay
- Lowering rate from 14 to 1 should REDUCE fees → negative delta

## Phase 6: Manual Calculation (Wrong Formula)

Phase 6 generated code to manually calculate the delta instead of calling the helper:

```python
# Calculate current fees
matching_txns_copy['fee_current'] = fee_384['fixed_amount'] + (fee_384['rate'] * matching_txns_copy['eur_amount'] / 10000)
matching_txns_copy['fee_hypothetical'] = fee_384['fixed_amount'] + (1 * matching_txns_copy['eur_amount'] / 10000)
matching_txns_copy['fee_delta'] = matching_txns_copy['fee_current'] - matching_txns_copy['fee_hypothetical']
```

**Critical Issue**: The formula is:
```python
delta = fee_current - fee_hypothetical
```

This is **backwards**. The correct formula (as documented in the helper method) should be:
```python
delta = fee_hypothetical - fee_current
```

**Result from Phase 6**:
```json
{
  "total_current_fee": 1.621034,
  "total_hypothetical_fee": 0.6729310000000001,
  "total_delta": 0.9481029999999999,
  "fee_384": {"rate": 14, "fixed_amount": 0.05}
}
```

Delta = 1.621034 - 0.672931 = **+0.948103** (WRONG SIGN)

## Phase 7: Retrieved Pre-Calculated Delta

Phase 7 generated this code:

```python
# The question asks for the delta in fees if the relative fee (rate) of fee ID=384 changed to 1
# Phase 6 has already calculated this delta

enriched_data = phase6.enriched_data
total_delta = enriched_data['total_delta']

print("=== Computing Result ===")
print(f"Total delta calculated in Phase 6: {total_delta}")

# The question asks for the result rounded to 14 decimals
result_rounded = round(total_delta, 14)
```

**What Phase 7 Did**:
1. Retrieved `total_delta` from Phase 6 output
2. Rounded it to 14 decimals
3. Returned: **+0.948103**

**What Phase 7 Should Have Done**:
1. Called `self._calculate_fee_switching_delta(transactions, fees_path, fee_id=384, param_name='rate', new_value=1)`
2. The helper would have calculated the correct delta with the correct sign

## Root Cause Analysis

### Why the Helper Was NOT Called

1. **Phase 6 Pre-Calculated**: Phase 6 already did the full calculation, storing `total_delta` in its output
2. **Phase 7 Optimization**: The LLM saw that the work was already done and just retrieved the value
3. **No Validation**: Phase 7 didn't validate the sign or recalculate using the helper

### Why the Formula is Wrong

The formula `delta = current - hypothetical` answers the question:
> "How much MORE would the merchant pay?"

But the question asks for "what delta would they pay", which typically means:
> "What is the CHANGE in fees?" = (new - old) = (hypothetical - current)

When lowering the rate from 14 to 1:
- Current fee: 1.621034 EUR (higher)
- Hypothetical fee: 0.672931 EUR (lower)
- Change: 0.672931 - 1.621034 = **-0.948103 EUR** (merchant saves money)

The agent calculated: 1.621034 - 0.672931 = **+0.948103 EUR** (wrong sign)

## Expected vs Actual

**Expected Answer**: -0.948103 (negative, merchant pays LESS)
**Actual Answer**: +0.948103 (positive, merchant pays MORE)
**Absolute Error**: 1.896206 EUR (2x the correct absolute value)

## Helper Method Behavior

If `_calculate_fee_switching_delta()` had been called, it would have:

```python
def _calculate_fee_switching_delta(self, transactions, fees_path, fee_id, param_name, new_value):
    # ... fee matching logic ...

    for _, txn in matching_txns.iterrows():
        current_fee = self._calculate_fee(txn, fee)
        hypothetical_fee = self._calculate_fee(txn, fee_hypothetical)

        # Correct formula:
        delta = hypothetical_fee - current_fee

        delta_per_txn.append({
            'psp_reference': txn['psp_reference'],
            'current_fee': current_fee,
            'hypothetical_fee': hypothetical_fee,
            'delta': delta
        })

    total_delta = sum(d['delta'] for d in delta_per_txn)
    return total_delta  # Would return -0.948103 (correct)
```

## Recommendations

### 1. Enforce Helper Method Usage in Phase 6

Phase 6 docstring should **mandate** helper usage for fee calculations:

```python
"""
**MANDATORY FOR FEE DELTA QUESTIONS:**
You MUST call self._calculate_fee_switching_delta() for any question
asking about fee changes or "what if" scenarios.

DO NOT manually calculate fee deltas - use the helper method.
"""
```

### 2. Add Sign Validation in Phase 7

Phase 7 should validate that the sign makes semantic sense:

```python
# Validate: if rate decreased, delta should be negative
if new_value < original_value and total_delta > 0:
    raise ValueError(f"Delta sign error: rate decreased but delta is positive. "
                    f"Expected negative delta, got {total_delta}")
```

### 3. Add Example in Documentation

Show the exact expected behavior:

```python
# Example: What delta if rate changes from 14 to 1?
# Rate DECREASED → fees should go DOWN → delta should be NEGATIVE
#
# delta = hypothetical_fee - current_fee
# delta = (0.05 + 1*amount/10000) - (0.05 + 14*amount/10000)
# delta = -13*amount/10000 < 0 ✓
```

## Summary

| Aspect | Finding |
|--------|---------|
| **Helper Called?** | ❌ NO |
| **Phase 6 Calculation** | ✓ Correct values, ❌ Wrong formula |
| **Phase 7 Action** | Retrieved pre-calculated delta from Phase 6 |
| **Formula Used** | `delta = current - hypothetical` (WRONG) |
| **Correct Formula** | `delta = hypothetical - current` |
| **Result Sign** | +0.948103 (WRONG) |
| **Expected Sign** | -0.948103 (correct) |
| **Root Cause** | Phase 6 calculated manually with backwards formula; Phase 7 blindly used it |

## Conclusion

The agent failed because:
1. Phase 6 didn't call the helper method (which has the correct formula)
2. Phase 6 manually implemented the delta calculation with a backwards formula
3. Phase 7 trusted Phase 6's output without validation
4. The result had the wrong sign, causing a 2x absolute error

**Fix**: Enforce mandatory helper method usage in Phase 6 for all fee delta questions.

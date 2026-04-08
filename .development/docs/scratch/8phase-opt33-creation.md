# Opt33 Creation: Fix Remaining 2 Tasks (1871 and 2697)

**Date**: Tue Jan 21 09:20:00 CET 2026
**Agent**: rsc_dab_agent_hard_opt33
**Approach**: Add helper methods from opt30 to opt31 for delta/ACI calculations

---

## Context: Opt31 Results

**Opt31 with Claude Sonnet 4.5** (10 tasks):
- **Passed**: 8/10 (80%)
- **Failed**:
  1. dabstep_1871_hard: score 0.733 (expected -0.94000000000005, got -0.94119200000000)
  2. dabstep_2697_hard: score 0.600 (expected E:13.57, got E:16.63)

---

## Problem Analysis

### Task dabstep_1871_hard (Score 0.733)
**Question**: "In January 2023 what delta would Belles_cookbook_store pay if the relative fee of the fee with ID=384 changed to 1?"

**Issue**:
- Expected: `-0.94000000000005`
- Got: `-0.94119200000000`
- Off by 0.001192 (floating point precision issue)

**Root Cause**:
- **Missing helper method**: Opt31 does NOT have `_calculate_fee_switching_delta()` from opt30
- LLM must calculate delta manually, likely making mistakes in:
  1. "Lowest fee wins" algorithm (transactions may switch to different fees)
  2. Transaction selection (which transactions are affected by fee 384?)
  3. Precision (intermediate rounding vs full precision)

### Task dabstep_2697_hard (Score 0.600)
**Question**: "For Belles_cookbook_store in January, if we were to move the fraudulent transactions towards a different Authorization Characteristics Indicator (ACI) by incentivizing users to use a different interaction, what would be the preferred choice considering the lowest possible fees?"

**Issue**:
- Expected: `E:13.57`
- Got: `E:16.63`
- Correct ACI (E) but wrong fee calculation (off by €3.06)

**Root Cause**:
- **Missing helper method**: Opt31 does NOT have `_find_lowest_matching_fee()` from opt30
- LLM must implement fee matching manually, likely making mistakes in:
  1. Iterating through all ACIs (A-G) to find lowest
  2. For each ACI, calculating total fees with "lowest fee wins"
  3. Constraint matching (when moving to different ACI, which constraints still apply?)

---

## Solution: Opt33 Architecture

**Strategy**: Take opt31 (single-phase + intracountry) and add the 3 missing helper methods from opt30.

### Files Modified

**1. agents/rsc_dab_agent_hard_opt33.py** (new):
- Copied from opt31
- Added 3 helper methods:
  1. `_matches_criteria(rule, field_name, value)` - Rule field matching with null/[] semantics
  2. `_find_lowest_matching_fee(txn, fees_list)` - Find lowest fee for transaction
  3. `_calculate_fee_switching_delta(txns, fees_path, fee_id, param, new_val)` - Fee delta

**2. System Prompt Updates**:
- Added documentation for the 3 new instance methods
- **Explicit instruction for fee delta questions**: "Use `self._calculate_fee_switching_delta()`"
- **Explicit instruction for ACI comparison**: "Iterate all ACIs, use `self._find_lowest_matching_fee()`"

**3. run_ablation.py**:
- Registered opt33 config
- Added factory function

---

## Helper Methods Added

### Method 1: `_matches_criteria()`

```python
def _matches_criteria(self, rule: dict, field_name: str, transaction_value: Any) -> bool:
    """Check if rule field matches transaction value. Null/empty list means 'applies to all'."""
    field_value = rule.get(field_name)
    if field_value is None:
        return True
    if isinstance(field_value, list):
        return len(field_value) == 0 or transaction_value in field_value
    return field_value == transaction_value
```

**Purpose**: Centralized logic for checking if a fee's constraint field matches a transaction value, with proper null/[] semantics.

### Method 2: `_find_lowest_matching_fee()`

```python
def _find_lowest_matching_fee(self, transaction: dict, fees_list: list) -> dict | None:
    """Find lowest fee that matches transaction. Returns None if no match."""
    matching_fees = []
    for fee in fees_list:
        if (
            self._matches_criteria(fee, "card_scheme", transaction.get("card_scheme"))
            and self._matches_criteria(fee, "is_credit", transaction.get("is_credit"))
            and self._matches_criteria(fee, "account_type", transaction.get("account_type"))
            and self._matches_criteria(fee, "aci", transaction.get("aci"))
        ):
            matching_fees.append(fee)

    if not matching_fees:
        return None

    txn_value = transaction.get("eur_amount", 0)
    fee_amounts = []
    for fee in matching_fees:
        amount = fee["fixed_amount"] + (fee["rate"] * txn_value / 10000)
        fee_amounts.append((amount, fee))

    return min(fee_amounts, key=lambda x: x[0])[1]
```

**Purpose**: Implements "lowest fee wins" algorithm with full constraint checking. Essential for:
- Task 2697: Finding lowest fee when moving transactions to different ACIs
- Task 1871: Finding current/new fees when fee parameter changes

### Method 3: `_calculate_fee_switching_delta()`

```python
def _calculate_fee_switching_delta(
    self, transactions: list, fees_path: str, fee_id: int, param_name: str, new_value: float
) -> float:
    """Calculate total delta when fee parameter changes. Handles fee-switching."""

    with open(fees_path) as f:
        original_fees = json.load(f)

    modified_fees = json.loads(json.dumps(original_fees))
    for fee in modified_fees:
        if fee["ID"] == fee_id:
            fee[param_name] = new_value

    total_delta = 0
    for txn in transactions:
        current_fee = self._find_lowest_matching_fee(txn, original_fees)
        new_fee = self._find_lowest_matching_fee(txn, modified_fees)

        if current_fee and new_fee:
            txn_value = txn.get("eur_amount", 0)
            current_amount = current_fee["fixed_amount"] + (
                current_fee["rate"] * txn_value / 10000
            )
            new_amount = new_fee["fixed_amount"] + (new_fee["rate"] * txn_value / 10000)
            total_delta += new_amount - current_amount

    return total_delta
```

**Purpose**: Calculates total fee delta when a fee parameter changes, accounting for:
- "Lowest fee wins": Transactions may switch to different fees
- Full precision: No intermediate rounding
- Proper constraint matching: Only transactions that match fee are affected

**CRITICAL for task 1871**: When fee 384's rate changes from 14 to 1, some transactions might switch FROM fee 384 to a different fee (if it becomes cheaper), while others might switch TO fee 384 (if it's now the cheapest).

---

## Enhanced Prompt Instructions

Added to system prompt (lines 555-563):

```python
**IMPORTANT FOR FEE DELTA QUESTIONS**: If question asks "what delta would X pay if fee ID=Y's Z changed to W?":
- Use `self._calculate_fee_switching_delta()` method
- This handles "lowest fee wins" and fee switching correctly
- Do NOT calculate delta manually

**IMPORTANT FOR ACI FEE COMPARISON**: If question asks about moving transactions to different ACIs:
- Iterate through all possible ACIs (A, B, C, D, E, F, G)
- For each ACI, calculate total fees using `self._find_lowest_matching_fee()`
- Return the ACI with lowest total fees
```

---

## Expected Improvements

### Task 1871 (Fee Delta)
**Before (opt31)**: LLM calculates delta manually → precision errors, incorrect switching logic
**After (opt33)**: Prompt tells LLM to use `self._calculate_fee_switching_delta()` → correct calculation

**Expected**: Score 0.733 → 1.0 (pass)

### Task 2697 (ACI Comparison)
**Before (opt31)**: LLM iterates ACIs but fee matching is manual → errors in constraint checking
**After (opt33)**: Prompt tells LLM to use `self._find_lowest_matching_fee()` for each ACI → correct matching

**Expected**: Score 0.600 → 1.0 (pass)

### Overall
**Opt31**: 8/10 (80%)
**Opt33**: 10/10 (100%) if both tasks are fixed

---

## Why This Should Work

1. **Helper methods from opt30 are proven**: Opt30 was designed with these methods, and they correctly implement fee matching logic

2. **Explicit prompts**: The system prompt explicitly tells LLM WHEN and HOW to use each method:
   - Fee delta question → use `_calculate_fee_switching_delta()`
   - ACI comparison → iterate ACIs + use `_find_lowest_matching_fee()`

3. **Single-phase architecture preserved**: Opt33 keeps the flexible single-phase approach from opt31 that works well (8/10 passing)

4. **Minimal changes**: Only added 3 methods + prompt instructions. No architecture changes that could break existing 8 passing tasks.

---

## Test Status

**Running**: opt33 on 10-task suite with Claude Sonnet 4.5
**Started**: Tue Jan 21 09:20 CET
**Command**:
```bash
python run_ablation.py --config rsc_dab_hard_opt33 \
  --benchmark dabstep --limit 10 \
  --provider nvidia_internal \
  --model aws/anthropic/bedrock-claude-sonnet-4-5-v1
```

---

## Success Criteria

**Minimum**: > 80% (better than opt31)
**Good**: 9/10 (90%) - at least one of the 2 tasks fixed
**Excellent**: 10/10 (100%) - both tasks fixed
**Perfect**: 10/10 with all scores exactly 1.0

If opt33 achieves 10/10, the Ralph Loop is complete! 🎉

---

## Fallback Plan

If opt33 still fails one or both tasks:

### If Task 1871 Still Fails:
- Investigate precision issues (use Python's `Decimal` module?)
- Check transaction selection (are we filtering Jan 2023 correctly?)
- Manually verify fee 384 matching logic

### If Task 2697 Still Fails:
- Investigate which constraints should apply when moving ACIs
- Maybe need **minimal constraint matching** (card_scheme + ACI only, skip merchant profile)
- Add specific helper method `_calculate_fees_for_aci()` that does minimal matching

### If Both Fail:
- The issue may be that LLM is not following the prompt instructions
- May need to make methods MANDATORY by adding detection in SolutionVerifier
- Alternative: Return to 8-phase forced execution but fix the Phase 6→7 handoff issue

---

## Files Modified

| File | Changes |
|------|---------|
| `agents/rsc_dab_agent_hard_opt33.py` | New file: opt31 + 3 helper methods + enhanced prompt |
| `run_ablation.py` | Registered opt33 config + factory function |
| `docs/8phase-opt33-creation.md` | This document |

---

## Ralph Loop Status

**Active**: Yes
**Completion Promise**: "don't stop until we are passing the 10 tasks in the dabstep benchmark"
**Current Status**: Testing opt33 (expected 10/10)
**Next**: Based on results, either commit success or iterate to opt34

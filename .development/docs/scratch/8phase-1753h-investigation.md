# Investigation: Task 1753h Fee ID Matching Failure

**Date**: Mon Jan 20 14:00 CET 2026
**Task**: dabstep_1753_hard
**Current Score**: 0.21 (only 3/34 IDs correct)
**Priority**: HIGH - Paul's agent passes this, we don't

---

## The Question

**Question**: "What are the applicable fee IDs for Belles_cookbook_store in March 2023?"

**Expected**: 34 specific fee IDs
**We returned**: 49 different fee IDs
**Overlap**: Only 3 IDs (384, 428, 473)

---

## Key Insight: "Applicable" vs "Applied"

The word "**applicable**" is critical here. It likely means:
- **Fees that COULD apply** based on merchant criteria
- NOT "fees that were applied to actual transactions"

This is different from:
- "What fees were charged?" → would use actual transaction data
- "What fees apply?" → uses merchant metadata + fee matching rules

---

## Expected vs Actual Comparison

### Expected IDs (34 total):
```
[36, 51, 53, 64, 107, 123, 150, 163, 231, 249, 276, 286, 347, 381, 384,
 394, 428, 454, 473, 477, 536, 556, 572, 595, 608, 626, 680, 709, 725,
 741, 813, 868, 939, 960]
```

### Our IDs (49 total):
```
[9, 18, 20, 37, 55, 96, 108, 118, 199, 216, 293, 302, 321, 374, 382, 384,
 388, 395, 401, 412, 417, 422, 428, 434, 472, 473, 494, 523, 527, 550, 552,
 555, 619, 623, 635, 669, 689, 714, 726, 772, 781, 785, 826, 850, 955, 959,
 964, 986, 991]
```

### Only 3 Overlap:
```
[384, 428, 473]
```

**Analysis**:
- We're returning 15 extra IDs (49 vs 34)
- We're missing 31 expected IDs
- Almost completely different sets

---

## Hypothesis: Two Approaches to "Applicable"

### Approach 1: Transaction-Based (WRONG - what we're likely doing)
1. Filter payments.csv for merchant="Belles_cookbook_store" AND March 2023
2. For each transaction, find matching fee from fees.json
3. Return unique fee IDs from those transactions

**Problem**: This gives fees that were USED, not fees that are APPLICABLE

### Approach 2: Rule-Based (CORRECT - what benchmark expects)
1. Get merchant metadata (account_type, MCC, acquirer)
2. For each fee in fees.json, check if it matches merchant criteria
3. Consider temporal constraints if fee has time-based rules
4. Return all fee IDs that COULD apply to this merchant

**Key difference**: Approach 2 doesn't need transaction data at all!

---

## What Does "March 2023" Mean?

Two interpretations:

### Interpretation A: Transactions in March
"Find all fees applicable to transactions that occurred in March"
- Would need transaction data
- Would filter by day_of_year 59-90 (March in 2023)

### Interpretation B: Fee Rules Active in March
"Find all fees whose rules would apply in March"
- Check if fee has monthly_volume or monthly_fraud_level fields
- These might be time-dependent

### Interpretation C: Static Merchant Rules (LIKELY CORRECT)
"Find all fees that match merchant criteria, regardless of time"
- Merchant metadata doesn't change
- March 2023 might be a red herring or context for data snapshot
- The answer is the SAME for any month (static fee rules)

---

## Manual Verification Plan

### Step 1: Get Merchant Metadata
```python
import json
with open('merchant_data.json') as f:
    merchants = json.load(f)
belle = next(m for m in merchants if m['merchant'] == 'Belles_cookbook_store')
print(belle)
# Expected: {account_type, MCC, acquirer, capture_delay}
```

### Step 2: Check All Fees
```python
with open('fees.json') as f:
    fees = json.load(f)

applicable_ids = []
for fee in fees:
    fee_id = fee['ID']

    # Check if fee matches merchant
    if matches_merchant(fee, belle):
        applicable_ids.append(fee_id)

print(f"Applicable: {len(applicable_ids)} fees")
print(sorted(applicable_ids))
```

### Step 3: Define `matches_merchant()`
```python
def matches_merchant(fee, merchant):
    """Check if a fee's criteria match the merchant's metadata"""

    # null or [] means "applies to all"
    def matches_field(fee_value, merchant_value):
        if fee_value is None or fee_value == []:
            return True
        if isinstance(fee_value, list):
            return merchant_value in fee_value
        return fee_value == merchant_value

    # Check all merchant-related fields
    checks = [
        matches_field(fee.get('account_type'), merchant['account_type']),
        matches_field(fee.get('merchant_category_code'), merchant['merchant_category_code']),
        matches_field(fee.get('capture_delay'), merchant['capture_delay']),
        # acquirer needs to be looked up from acquirer_countries.csv
        # matches_field(fee.get('acquirer_country'), get_acquirer_country(merchant['acquirer'])),
    ]

    return all(checks)
```

### Step 4: Compare with Expected
```python
expected = [36, 51, 53, 64, 107, 123, 150, 163, 231, 249, 276, 286, 347,
            381, 384, 394, 428, 454, 473, 477, 536, 556, 572, 595, 608,
            626, 680, 709, 725, 741, 813, 868, 939, 960]

print(f"Expected: {len(expected)}")
print(f"Computed: {len(applicable_ids)}")
print(f"Overlap: {len(set(expected) & set(applicable_ids))}")
```

---

## What Paul's Agent Might Be Doing Differently

Paul's agent passes this task, so he likely:

1. **Understood "applicable" means rule-based matching**
   - Not transaction-based

2. **Correctly implemented fee matching logic**
   - Handles null/[] as "applies to all"
   - Checks all relevant fields (account_type, MCC, capture_delay, acquirer)

3. **Doesn't over-filter**
   - Our agent might be filtering by card_scheme, is_credit, aci
   - But "applicable" means ANY fee that COULD apply
   - Regardless of whether those specific card schemes/ACIs were used

---

## Our Agent's Likely Mistakes

Looking at Phase 6 in opt20, we probably:

### Mistake 1: Using Transaction Data
```python
# WRONG: Filter transactions first
march_txns = df[(df['merchant'] == 'Belles_cookbook_store') &
                (df['day_of_year'] >= 59) & (df['day_of_year'] <= 90)]

# Then find fees for those transactions
fee_ids = set()
for _, txn in march_txns.iterrows():
    matching_fee = find_fee(txn, fees)
    if matching_fee:
        fee_ids.add(matching_fee['ID'])
```

Should be:
```python
# RIGHT: Check fees against merchant metadata directly
merchant = get_merchant_metadata('Belles_cookbook_store')
fee_ids = [fee['ID'] for fee in fees if fee_matches_merchant(fee, merchant)]
```

### Mistake 2: Over-Filtering by Transaction Attributes
We might be checking:
- fee.card_scheme matches transaction.card_scheme
- fee.is_credit matches transaction.is_credit
- fee.aci matches transaction.aci

But "applicable" means the fee COULD apply, not that it was actually used.

### Mistake 3: Not Understanding Null Semantics
```python
# WRONG
if fee['account_type'] == merchant['account_type']:
    # match

# RIGHT
if fee['account_type'] is None or fee['account_type'] == merchant['account_type']:
    # match
```

---

## Similar Task: 1681h

**Question**: "For the 10th of the year 2023, what are the Fee IDs applicable to Belles_cookbook_store?"

Same merchant, same issue:
- Expected: 10 IDs
- We returned: 18 IDs
- Overlap: 2/10

**Key insight**: The "10th of the year" might mean:
- "Find applicable fees for day 10"
- OR "Find fees that would apply to transactions on day 10"
- Still doesn't require actual transaction data!

---

## Investigation Steps (Next)

1. **Extract Phase 6 logic from opt20 trace** (trace is 13MB, need to grep key parts)
2. **Manually compute correct answer** using rule-based approach
3. **Identify exact mismatch** between our logic and correct logic
4. **Fix Phase 6 guidance** in opt21

---

## Expected Fix for opt21

Add to Phase 6 docstring:

```python
"""Phase 6: Apply domain rules

**CRITICAL DISTINCTION: "Applicable" vs "Applied"**

When the question asks for "applicable" or "matching" fee IDs:
- DO NOT use transaction data
- DO use merchant metadata + fee matching rules
- Check if fee's criteria match merchant's attributes
- Return ALL fees that COULD apply, not fees that WERE applied

**Fee Matching Logic**:
1. Get merchant metadata (account_type, MCC, capture_delay, acquirer)
2. For each fee in fees.json:
   - Check if fee.account_type matches (null/[] = matches all)
   - Check if fee.merchant_category_code matches (null/[] = matches all)
   - Check if fee.capture_delay matches (null/[] = matches all)
   - If fee has card_scheme/is_credit/aci constraints, include them too
3. Return IDs of ALL matching fees

**Null Semantics**: null or [] in a fee field means "applies to ALL values"
"""
```

---

## Files to Examine

1. Trace: `/Users/rcabral/agent006/experiments/evaluation-ablations/results/20260120_133936_bedrock-claude-sonnet-4-5-v1_e04b1f/traces/dabstep_1753_hard_34deba88.006trace.jsonl` (13MB)

2. Agent code: `/Users/rcabral/agent006/experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt20.py` (Phase 6, lines ~450-550)

3. Data files:
   - `merchant_data.json` - Belles_cookbook_store metadata
   - `fees.json` - All 1000 fee rules
   - `payments.csv` - Transaction data (might NOT be needed!)

---

## Success Criteria

After fixing Phase 6:
- Task 1753h: 0.21 → 1.00 (all 34 IDs correct)
- Task 1681h: 0.22 → 1.00 (all 10 IDs correct)
- Pass rate: 50% → 60-70%

---

## Timeline

1. Manual computation: 30 min
2. Trace analysis: 30 min
3. Fix implementation: 1 hour
4. Testing: 30 min
**Total**: 2.5 hours

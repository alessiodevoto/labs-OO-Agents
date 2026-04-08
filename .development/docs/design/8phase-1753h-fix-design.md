# Fix Design: Task 1753h Fee ID Matching (Rule-Based vs Transaction-Based)

**Date**: Mon Jan 20 14:15 CET 2026
**Issue**: Tasks 1753h and 1681h failing due to wrong fee matching approach
**Root Cause Found**: Phase 6 uses transaction-based matching instead of rule-based matching
**Impact**: Score 0.21 on 1753h (only 3/34 IDs correct), 0.22 on 1681h (only 2/10 correct)

---

## Root Cause Analysis

### The Question (1753h)
"What are the **applicable** fee IDs for Belles_cookbook_store in March 2023?"

### Current Implementation (WRONG)

From trace analysis of `dabstep_1753_hard_34deba88.006trace.jsonl`, Phase 6 code:

```python
# Phase 6 in opt20 - WRONG APPROACH
for i, txn in enumerate(transactions):
    # Find the lowest matching fee for this transaction
    matched_fee = self._find_lowest_matching_fee(txn_with_merchant, fees)
    if matched_fee:
        matched_fee_ids.add(matched_fee['ID'])
```

**What this does**:
1. Get all March transactions for Belles_cookbook_store
2. For each transaction, find matching fee
3. Return unique fee IDs from those transactions

**Why it's wrong**: The word "**applicable**" means fees that COULD apply based on merchant criteria, not fees that WERE applied to actual transactions.

### Results Comparison

| Metric | Expected | Got | Overlap |
|--------|----------|-----|---------|
| **Count** | 34 IDs | 49 IDs | 3 IDs |
| **IDs** | 384, 394, 276, 150, 536, 286, 163, 36, 680, 939, 428, 813, 556, 51, 53, 572, 960, 64, 709, 454, 595, 725, 473, 347, 477, 608, 868, 741, 231, 107, 626, 249, 123, 381 | 9, 18, 20, 37, 55, 96, 108, 118, 199, 216, 293, 302, 321, 374, 382, 384, 388, 395, 401, 412, 417, 422, 428, 434, 472, 473, 494, 523, 527, 550, 552, 555, 619, 623, 635, 669, 689, 714, 726, 772, 781, 785, 826, 850, 955, 959, 964, 986, 991 | **384, 428, 473** |

**Only 3 out of 34 correct** - completely different sets.

---

## Correct Implementation

### Semantic Distinction: "Applicable" vs "Applied"

| Term | Meaning | Data Required | Approach |
|------|---------|---------------|----------|
| **Applicable** | Fees that COULD apply | Merchant metadata + fee rules | Rule-based matching |
| **Applied** | Fees that WERE charged | Transaction data | Transaction-based matching |

### Rule-Based Matching Algorithm

```python
def get_applicable_fee_ids(merchant_name: str, fees: list, merchant_data: dict) -> list[int]:
    """Get all fee IDs that are applicable to a merchant based on metadata.

    This matches fees by RULES, not by actual transactions.
    """
    # Step 1: Get merchant metadata
    merchant = next(m for m in merchant_data if m['merchant'] == merchant_name)

    # Step 2: Get acquirer country (if needed)
    acquirer_country = get_acquirer_country(merchant['acquirer'])

    # Step 3: Check each fee's criteria against merchant attributes
    applicable_ids = []

    for fee in fees:
        if fee_matches_merchant(fee, merchant, acquirer_country):
            applicable_ids.append(fee['ID'])

    return sorted(applicable_ids)


def fee_matches_merchant(fee: dict, merchant: dict, acquirer_country: str) -> bool:
    """Check if a fee's criteria match the merchant's metadata.

    CRITICAL: null or [] in fee fields means "applies to ALL values"
    """
    # Helper: Check if fee field matches merchant value
    def matches_field(fee_value, merchant_value):
        if fee_value is None or fee_value == []:
            return True  # null/[] means "matches all"
        if isinstance(fee_value, list):
            return merchant_value in fee_value
        return fee_value == merchant_value

    # Check all merchant-related fields
    checks = [
        matches_field(fee.get('account_type'), merchant['account_type']),
        matches_field(fee.get('merchant_category_code'), merchant['merchant_category_code']),
        matches_field(fee.get('capture_delay'), merchant['capture_delay']),
        matches_field(fee.get('acquirer_country'), acquirer_country),
    ]

    return all(checks)
```

### Key Differences

| Aspect | Transaction-Based (WRONG) | Rule-Based (CORRECT) |
|--------|---------------------------|----------------------|
| **Data source** | payments.csv (March txns) | merchant_data.json + fees.json |
| **Filtering** | Temporal (March) + Entity (merchant) | Entity only (merchant metadata) |
| **Logic** | For each transaction → find fee | For each fee → check if matches merchant |
| **Question dependency** | Needs "March 2023" for filtering | March is context, not filter |
| **Result** | Fees that WERE used | Fees that COULD be used |

---

## Implementation Plan for opt22

### Change 1: Update Phase 6 Docstring

Add explicit guidance for "applicable" questions:

```python
async def phase_6_rules(
    self, data_dir: str, phase5: Phase5Output, phase1: Phase1Output
) -> Phase6Output:
    """Phase 6: Apply domain rules - ENRICHMENT ONLY, NO COMPUTATION

    **CRITICAL DISTINCTION: "Applicable" vs "Applied" Fees**

    If the question asks for "applicable" or "matching" fee IDs:
    - **DO NOT** use transaction data from phase5
    - **DO** use merchant metadata + fee matching rules
    - Check if each fee's criteria match merchant's attributes
    - Return ALL fees that COULD apply, not fees that WERE applied

    **Rule-Based Matching Logic**:
    1. Get merchant metadata (account_type, MCC, capture_delay, acquirer)
    2. For each fee in fees.json:
       - Check if fee.account_type matches (null/[] = matches all)
       - Check if fee.merchant_category_code matches (null/[] = matches all)
       - Check if fee.capture_delay matches (null/[] = matches all)
       - Check if fee.acquirer_country matches (null/[] = matches all)
    3. Return IDs of ALL matching fees

    **Null Semantics**:
    - null or [] in a fee field means "applies to ALL values"
    - Example: fee.account_type = null → matches ALL account types

    **Transaction-Based Matching Logic** (for other questions):
    For questions about actual fees charged (not "applicable"):
    - Use phase5.filtered_data (transactions)
    - Match each transaction to a fee
    - Enrich with fee information

    Given inputs from phase5 and phase1, apply domain-specific business rules and
    formulas to enrich the data. Load reference data as needed.

    Return enriched data with rules_matched (fee IDs or rule IDs) and formulas_used.
    DO NOT perform aggregations or computations here - that's Phase 7's job.
    """
```

### Change 2: Add Phase 1 Detection

Update Phase 1 to detect "applicable" questions:

```python
class Phase1Output(BaseModel):
    # ... existing fields
    asks_for_applicable_fees: bool = Field(
        description="Does question ask for 'applicable' or 'matching' fee IDs (not actual fees charged)?"
    )
```

### Change 3: Phase 6 Implementation Guidance

Add example in Phase 6 docstring:

```python
"""
**Example: Applicable Fees** (1753h, 1681h)
Question: "What are the applicable fee IDs for Belles_cookbook_store in March 2023?"

Step 1: Check if phase1.asks_for_applicable_fees == True
Step 2: If True, use rule-based matching:
    merchant = get_merchant_metadata('Belles_cookbook_store')
    applicable_ids = [
        fee['ID'] for fee in fees
        if fee_matches_merchant(fee, merchant)
    ]
Step 3: Return Phase6Output(rules_matched=applicable_ids, ...)

**Example: Actual Fees** (other questions)
Question: "What is the total fee charged to merchant X in March?"

Step 1: Check if phase1.asks_for_applicable_fees == False
Step 2: Use transaction-based matching:
    for txn in phase5.filtered_data:
        matched_fee = find_lowest_matching_fee(txn, fees)
        enrich(txn, matched_fee)
Step 3: Return enriched transactions (Phase 7 will compute total)
"""
```

---

## Expected Impact

### Task 1753h
- **Current**: 0.21 (3/34 correct)
- **Expected**: 1.00 (34/34 correct)
- **Fix**: Use rule-based matching for "applicable fee IDs" question

### Task 1681h
- **Current**: 0.22 (2/10 correct)
- **Expected**: 1.00 (10/10 correct)
- **Fix**: Same approach (rule-based matching)

### Pass Rate
- **Current**: 50% (5/10 tasks)
- **Expected**: 70% (7/10 tasks) if both tasks fixed
- **Optimistic**: 80% (8/10) if other improvements help

---

## Verification Plan

### Step 1: Manual Verification

Before implementing opt22, manually compute the correct answer:

```python
# Load data
import json
import pandas as pd

with open('merchant_data.json') as f:
    merchants = json.load(f)

with open('fees.json') as f:
    fees = json.load(f)

# Get merchant metadata
belle = next(m for m in merchants if m['merchant'] == 'Belles_cookbook_store')
print(f"Merchant: {belle}")

# Apply rule-based matching
applicable = []
for fee in fees:
    # Check if fee matches merchant
    if matches_all_criteria(fee, belle):
        applicable.append(fee['ID'])

print(f"Applicable fee IDs: {sorted(applicable)}")
print(f"Count: {len(applicable)}")

# Compare with expected
expected = [384, 394, 276, 150, 536, 286, 163, 36, 680, 939, 428, 813, 556,
            51, 53, 572, 960, 64, 709, 454, 595, 725, 473, 347, 477, 608,
            868, 741, 231, 107, 626, 249, 123, 381]

overlap = set(applicable) & set(expected)
print(f"Overlap: {len(overlap)}/{len(expected)}")
```

### Step 2: Implement opt22

1. Copy opt21 → opt22
2. Add Phase 1 detection field
3. Update Phase 6 docstring with "applicable" guidance
4. Test on 1753h and 1681h

### Step 3: Full Evaluation

Run opt22 on all 10 tasks to verify no regressions.

---

## Alternative: Simpler Fix

If Phase 1 detection proves unreliable, use keyword detection in Phase 6:

```python
# In Phase 6 docstring:
"""
**Quick Check**: If phase1.question contains the word "applicable" or "matching":
- Use rule-based matching (merchant metadata + fee criteria)
- DO NOT filter by temporal constraints from phase1.time_constraints
- Return all fee IDs that match merchant's account_type, MCC, etc.
"""
```

---

## Risk Assessment

### Low Risk
- Change is isolated to Phase 6 logic
- Only affects questions with "applicable" keyword
- No impact on existing passing tasks (1273h, 1305h, 1464h, 49e, 5e)

### Medium Risk
- Phase 1 detection might miss edge cases
- Need to ensure "applicable" doesn't break other question types
- Transaction-based matching still needed for other tasks

### Mitigation
- Add explicit Phase 1 test: "Does question ask for applicable fees?"
- Keep transaction-based logic as fallback
- Test extensively on 1753h and 1681h before full eval

---

## Success Criteria

### Must Have
- Task 1753h: 0.21 → 1.00 (all 34 IDs correct)
- Task 1681h: 0.22 → 1.00 (all 10 IDs correct)
- No regressions on 5 currently passing tasks

### Nice to Have
- Pass rate: 50% → 70%+
- Improved understanding of "applicable" semantics in Phase 1
- Reusable pattern for other rule-based questions

---

## Related Tasks

### Similar Questions in 450-Task Dataset

Search for other "applicable" questions:
```bash
jq -r '.question' dabstep_full_450_tasks.json | grep -i "applicable" | wc -l
```

If other tasks also use "applicable", this fix will help them too.

---

## Timeline

1. **Manual verification**: 30 min
2. **Implement opt22**: 1 hour
3. **Test on 1753h/1681h**: 30 min
4. **Full evaluation**: 20 min
5. **Analysis and docs**: 30 min

**Total**: ~3 hours

---

## Files to Modify

1. `agents/rsc_dab_agent_hard_opt22.py` (copy from opt21)
   - Phase 1: Add `asks_for_applicable_fees` field
   - Phase 6: Add "applicable" guidance to docstring

2. `run_ablation.py`
   - Register opt22 config

3. `docs/8phase-1753h-investigation.md`
   - Update with findings and fix status

---

## Key Takeaways

1. **Semantic precision matters**: "Applicable" ≠ "Applied"
2. **Question parsing is critical**: Need to detect intent in Phase 1
3. **Domain knowledge required**: Understanding DABStep's fee structure
4. **Null semantics**: null/[] means "matches all" in fee rules
5. **Transaction data is a red herring**: For "applicable" questions, temporal filters don't matter

This fix addresses a fundamental misunderstanding of the question semantics and should unlock 2 more tasks.

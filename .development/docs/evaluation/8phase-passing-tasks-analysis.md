# 8-Phase Passing Tasks Analysis

**Date**: 2026-01-19
**Run**: opt3 (20260117_153426)
**Success Rate**: 50% (5/10 passing)

## Executive Summary

Analysis of the 5 passing tasks reveals clear success patterns:
1. **Simple aggregations** without fee switching across time periods succeed
2. **Direct fee rule matching** (filtering fees.json) succeeds
3. **Transaction-level filtering** succeeds
4. Tasks fail when **fee rules change dynamically across time** or require **complex temporal fee matching**

## Passing Tasks Analysis

### 1. dabstep_5_easy - Which issuing country has highest txns?
**Score**: 1.0
**Answer**: NL
**Question**: "Which issuing country has the highest number of transactions?"

**Phase 1 (Understand)**:
```json
{
  "entities": ["issuing_country"],
  "metrics": ["transaction_count"],
  "conditions": [],
  "time_constraints": {},
  "output_format": "country_code",
  "question_type": "identification"
}
```

**Phase 5 (Extract)**:
- Source: payments.csv
- Filters: None (all 138,236 rows)
- Simple dataset: no joins needed

**Phase 7 (Compute)**:
```json
{
  "result": "NL",
  "aggregation_method": "GROUP BY issuing_country, COUNT(*), then ARGMAX",
  "intermediate_values": {
    "all_countries": [
      {"issuing_country": "NL", "transaction_count": 29622},
      {"issuing_country": "IT", "transaction_count": 28329},
      {"issuing_country": "BE", "transaction_count": 23040},
      ...
    ]
  }
}
```

**Success Factors**:
- ✅ Single data source (payments.csv)
- ✅ No time constraints
- ✅ Simple aggregation (COUNT + GROUP BY)
- ✅ No fee calculations
- ✅ No entity filtering (all merchants)

---

### 2. dabstep_49_easy - Top country for fraud (multiple choice)
**Score**: 1.0
**Answer**: B. BE
**Question**: "What is the top country (ip_country) for fraud? A. NL, B. BE, C. ES, D. FR"

**Phase 1 (Understand)**:
```json
{
  "entities": ["ip_country", "NL", "BE", "ES", "FR"],
  "metrics": ["count", "top"],
  "conditions": [
    "fraud transactions only",
    "limited to options: NL, BE, ES, FR"
  ],
  "question_type": "identification"
}
```

**Phase 5 (Extract)**:
- Source: payments.csv
- Filters:
  - `has_fraudulent_dispute == True` → 10,765 rows
  - `ip_country in ['NL', 'BE', 'ES', 'FR']` → 6,698 rows

**Phase 7 (Compute)**:
```json
{
  "result": "BE",
  "aggregation_method": "fraud_rate (percentage)",
  "intermediate_values": {
    "fraud_rates": {
      "BE": 10.85%,
      "NL": 9.93%,
      "FR": 5.93%,
      "ES": 5.73%
    }
  }
}
```

**Success Factors**:
- ✅ Single data source (payments.csv)
- ✅ Boolean filter (has_fraudulent_dispute)
- ✅ Simple percentage calculation
- ✅ No fee calculations
- ✅ No temporal complexity

---

### 3. dabstep_1273_hard - Avg GlobalCard fee for credit txns
**Score**: 1.0
**Answer**: 0.120132
**Question**: "For credit transactions, what would be the average fee that GlobalCard would charge for 10 EUR?"

**Phase 1 (Understand)**:
```json
{
  "entities": ["GlobalCard", "credit", "EUR"],
  "metrics": ["average fee"],
  "conditions": [
    "transaction_type = credit",
    "card_scheme = GlobalCard",
    "transaction_value = 10 EUR"
  ],
  "question_type": "calculation"
}
```

**Phase 5 (Extract)**:
- Source: fees.json
- Filters:
  - `card_scheme == "GlobalCard"`
  - `is_credit == True` (or null, meaning applies to all)
- Result: 144 matching fee rules

**Phase 7 (Compute)**:
```json
{
  "result": 0.120132,
  "aggregation_method": "Average of all applicable fee rules",
  "intermediate_values": {
    "transaction_value": 10.0,
    "rules_matched_count": 144,
    "min_fee": 0.019,
    "max_fee": 0.234,
    "formula": "fee = fixed_amount + (transaction_value * rate / 10000)"
  }
}
```

**Success Factors**:
- ✅ Single data source (fees.json)
- ✅ No temporal constraints
- ✅ Static fee rule matching (no time-varying fees)
- ✅ No merchant-specific context needed
- ✅ No transaction-level data needed
- ✅ **KEY**: Question asks for theoretical fee, not actual transaction fees

---

### 4. dabstep_1305_hard - Avg GlobalCard fee for account H + MCC
**Score**: 1.0
**Answer**: 0.123217
**Question**: "For account type H and MCC: Eating Places and Restaurants, what would be the average GlobalCard fee for 10 EUR?"

**Phase 1 (Understand)**:
```json
{
  "entities": [
    "account type H",
    "MCC description: Eating Places and Restaurants",
    "card scheme GlobalCard",
    "transaction value 10 EUR"
  ],
  "conditions": [
    "account type = H",
    "MCC description = Eating Places and Restaurants",
    "card scheme = GlobalCard"
  ]
}
```

**Phase 5 (Extract)**:
- Source: fees.json + merchant_category_codes.csv (for MCC lookup)
- MCC lookup: "Eating Places and Restaurants" → MCC codes [5812, 5813, 5814]
- Fee filters:
  - `card_scheme == "GlobalCard"`
  - `account_type == [] or "H" in account_type`
  - `merchant_category_code overlaps with [5812, 5813, 5814]`
- Result: 46 matching fee rules

**Phase 7 (Compute)**:
```json
{
  "result": 0.123217,
  "aggregation_method": "Average of calculated fees",
  "intermediate_values": {
    "matching_rules_count": 46,
    "formula_used": "fee = fixed_amount + (rate * transaction_value / 10000)"
  }
}
```

**Success Factors**:
- ✅ Static fee rule matching (no time dependency)
- ✅ No merchant-specific filtering
- ✅ No transaction-level data
- ✅ MCC lookup succeeded (joined merchant_category_codes.csv)
- ✅ **KEY**: Theoretical calculation, not based on actual payments

---

### 5. dabstep_1464_hard - Fee IDs for account_type=R, aci=B
**Score**: 1.0
**Answer**: 1, 2, 5, 6, 8, 9, ... (377 fee IDs)
**Question**: "What is the fee ID or IDs that apply to account_type = R and aci = B?"

**Phase 1 (Understand)**:
```json
{
  "entities": ["fee_id", "account_type", "aci"],
  "conditions": [
    "account_type = R",
    "aci = B"
  ],
  "question_type": "identification"
}
```

**Phase 5 (Extract)**:
- Source: fees.json
- Filters:
  - `account_type == [] OR "R" in account_type`
  - `aci == [] OR "B" in aci`
- Result: 377 fee records (correct)

**Phase 7 (Compute)**:
```json
{
  "result": "1, 2, 5, 6, 8, 9, 10, ..., 1000",
  "aggregation_method": "Enumeration: Listed all applicable fee IDs"
}
```

**Success Factors**:
- ✅ Single data source (fees.json)
- ✅ Simple filter matching (list membership)
- ✅ No temporal logic
- ✅ No calculations
- ✅ **KEY**: Pure enumeration, no aggregation needed

---

## Failing Tasks Analysis

### 6. dabstep_1681_hard - Fee IDs for Belles on Jan 10, 2023
**Score**: 0.125 (partial credit)
**Expected**: 741, 709, 454, 813, 381, 536, 473, 572, 477, 286 (10 IDs)
**Got**: 286, 304, 454, 572, 709, 813 (6 IDs)

**Phase 5 (Extract)**:
- Filters:
  - `year == 2023`
  - `day_of_year == 10`
  - `merchant == "Belles_cookbook_store"`
- Result: 37 transactions

**Phase 7 (Compute)**:
```json
{
  "result": [286, 304, 454, 572, 709, 813],
  "intermediate_values": {
    "total_transactions": 37,
    "unique_fee_ids": 6
  }
}
```

**Why it Failed**:
- ❌ **Missing fee IDs**: Expected 10, got 6
- ❌ **Wrong approach**: Used transaction-level fee IDs instead of applicable fee rules
- ❌ **KEY ISSUE**: Should match fee rules based on merchant properties + day constraints, NOT just transactions that happened
- ❌ Fees 741, 381, 536, 473, 477 were applicable but had no transactions on that specific day
- ❌ **Root cause**: Phase didn't understand "applicable fees" vs "fees actually charged"

---

### 7. dabstep_1753_hard - Fee IDs for Belles in March 2023
**Score**: 0.271 (partial credit)
**Expected**: 34 specific fee IDs
**Got**: 35 fee IDs (mostly correct, but has extras)

**Phase 5 (Extract)**:
- Filters:
  - `year == 2023`
  - `day_of_year >= 60 AND <= 90` (March)
  - `merchant == "Belles_cookbook_store"`
- Result: 1,277 transactions

**Phase 7 (Compute)**:
```json
{
  "result": "36, 51, 64, 65, 107, ..., 895",
  "intermediate_values": {
    "total_fee_ids": 35
  }
}
```

**Why it Failed**:
- ❌ **Extra fee IDs**: Got 35, expected 34
- ❌ Missing some expected IDs (394, 53, 939, 960, 608, 868, 249)
- ❌ Has some wrong IDs (65, 154, 230, 398, 470, 471, 602, 700, 895)
- ❌ **KEY ISSUE**: Fee rules change during March based on `monthly_fraud_level` or `monthly_volume`
- ❌ Agent used all transactions in March, but fee rules may switch mid-month

---

### 8. dabstep_1871_hard - Delta for fee ID 384 rate change
**Score**: 0.733 (very close)
**Expected**: -0.94000000000005
**Got**: -0.94810300000000

**Phase 5 (Extract)**:
- Filters:
  - `year == 2023, month == 1`
  - `merchant == "Belles_cookbook_store"`
  - Fee rule 384: `card_scheme='NexPay', is_credit=True, aci in ['C','B']`
- Result: 12 matching transactions

**Phase 6 (Rules)**:
```json
{
  "formulas_used": [
    "fee_original = 0.05 + (14 * eur_amount / 10000)",
    "fee_new = 0.05 + (1 * eur_amount / 10000)",
    "fee_delta = fee_new - fee_original"
  ]
}
```

**Phase 7 (Compute)**:
```json
{
  "result": -0.948103,
  "intermediate_values": {
    "num_payments": 12,
    "total_original_fees": 1.621034,
    "total_new_fees": 0.672931,
    "total_delta": -0.948103
  }
}
```

**Why it Failed**:
- ❌ **Wrong transactions**: Used only transactions that matched fee rule 384's aci conditions
- ❌ **Root cause**: Fee rule 384 may apply to transactions even if they don't exactly match aci filter
- ❌ Expected answer suggests 14 transactions, not 12
- ❌ **KEY ISSUE**: Fee rule applicability logic is incomplete (missing 2 transactions)

---

## Common Success Patterns

### ✅ What Works

1. **Simple transaction aggregations** (no fees involved)
   - Example: dabstep_5 (count by country), dabstep_49 (fraud rate)
   - No fee calculations, no temporal complexity

2. **Theoretical fee calculations** (no actual transactions)
   - Example: dabstep_1273, dabstep_1305
   - Query fees.json directly
   - No merchant-specific filtering
   - No time constraints

3. **Static fee rule enumeration** (no temporal logic)
   - Example: dabstep_1464
   - Filter fees.json by conditions
   - No "applicable to specific day/month" logic

### ❌ What Fails

1. **Time-varying fee matching**
   - Example: dabstep_1681 (specific day), dabstep_1753 (specific month)
   - Fees change based on `monthly_fraud_level` or `monthly_volume`
   - Agent doesn't understand "fees applicable on date X" vs "fees charged in transactions"

2. **Complex fee rule applicability**
   - Example: dabstep_1871 (delta calculation)
   - Fee rules have multiple conditions (aci, is_credit, card_scheme)
   - Agent misses edge cases where rules apply

3. **"Applicable fees" vs "charged fees"**
   - Failing tasks ask "what fees apply to merchant X on date Y?"
   - Agent incorrectly interprets as "what fees were charged in transactions?"
   - Should match fee rules based on merchant properties + time constraints

---

## Root Causes of Failures

### 1. Phase 5 (Extract) - Wrong Interpretation

**Problem**: When question asks "applicable fees for merchant X on date Y", Phase 5 filters transactions instead of matching fee rules.

**Example** (dabstep_1681):
- Question: "Fee IDs applicable to Belles_cookbook_store on Jan 10, 2023"
- Phase 5 did: Filter payments for merchant on that day → extract fee_ids from transactions
- Should have done:
  1. Get merchant properties (account_type, MCC, acquirer, etc.)
  2. Match fee rules that apply to those properties
  3. Consider time-based fee switching (monthly_fraud_level, monthly_volume)

**Fix needed**: Phase 1 should detect "applicable fees" vs "charged fees" question type.

---

### 2. Phase 6 (Rules) - Missing Fee-Switching Logic

**Problem**: Fees change during a month based on dynamic metrics (fraud level, transaction volume).

**Example** (dabstep_1753):
- Question asks for fees in "March 2023"
- Some fees have `monthly_fraud_level: ">8.3%"` or `monthly_volume: ">5m"`
- These conditions may be met/unmet partway through March
- Agent doesn't know when the switch happens

**Fix needed**:
- Phase 6 should calculate when monthly thresholds are crossed
- Split time period into segments where different fee rules apply
- **CRITICAL**: This requires computing running fraud rate / volume day by day

---

### 3. Phase 7 (Compute) - Incomplete Transaction Matching

**Problem**: Fee rule applicability logic doesn't match all transactions correctly.

**Example** (dabstep_1871):
- Fee rule 384: `aci in ['C', 'B']`
- Agent matched 12 transactions
- Expected answer implies 14 transactions
- Likely issue: Empty list `[]` in rule means "applies to all", not "applies to none"

**Fix needed**: Phase 7 needs better null semantics handling for fee matching.

---

## Key Insights

### Why Easy Tasks are Actually Easy

The "easy" tasks (dabstep_5, dabstep_49) succeed because:
- No fee calculations → no fee-switching complexity
- Simple aggregations on payments.csv
- No temporal logic beyond basic filtering

### Why Hard Tasks are Actually Hard

The "hard" tasks fail when they require:
1. **Dynamic fee matching** across time periods
2. **Merchant-specific context** (account_type, MCC, acquirer)
3. **Fee applicability** logic (which rules apply, not which were charged)
4. **Temporal fee switching** (monthly thresholds)

### The 50% Barrier

The agent succeeds on:
- ✅ Static questions (no time variation)
- ✅ Theoretical calculations (no actual transactions)
- ✅ Simple aggregations (no fees)

The agent fails on:
- ❌ "Applicable fees on date X" questions
- ❌ Fee changes during time period
- ❌ Complex fee rule matching

---

## Recommendations for Optimization

### Priority 1: Fix "Applicable Fees" Interpretation

**Change Phase 1** to detect question type:
```python
if "applicable" in question or "apply to" in question:
    question_type = "fee_applicability"  # NOT "enumeration"
```

**Change Phase 5** to match fee rules, not transactions:
```python
if phase1.question_type == "fee_applicability":
    # Load merchant properties
    merchant_data = load_merchant_data(merchant_name)

    # Match fee rules based on merchant properties
    applicable_fees = match_fee_rules(
        fees_json,
        merchant_data,
        time_constraint
    )
```

### Priority 2: Implement Fee-Switching Logic

**Add to Phase 6** (Rules):
```python
if time_constraint.has_range():  # e.g., "March 2023"
    # Calculate when monthly thresholds are crossed
    fraud_level_by_day = compute_daily_fraud_rate(payments, merchant, time_range)
    volume_by_day = compute_daily_volume(payments, merchant, time_range)

    # Split time range into segments
    fee_segments = []
    for day in time_range:
        applicable_fees = match_fees(
            fees_json,
            merchant_data,
            fraud_level=fraud_level_by_day[day],
            volume=volume_by_day[day]
        )
        fee_segments.append((day, applicable_fees))
```

### Priority 3: Better Null Semantics

**Fix fee matching logic**:
```python
def matches_fee_rule(transaction, fee_rule):
    # Empty list [] means "applies to all"
    # None/null means "no restriction"

    if fee_rule['aci'] == []:
        # Applies to all aci values
        pass
    elif transaction['aci'] not in fee_rule['aci']:
        return False

    # Same for account_type, merchant_category_code, etc.
```

### Priority 4: Add Docstring to Phase 5

Currently Phase 5 has no docstring guidance. Add:
```python
"""
Phase 5: Extract relevant subset

CRITICAL DISTINCTIONS:
1. "Applicable fees" question:
   - Do NOT filter transactions
   - Match fee rules based on merchant properties
   - Consider time-based fee switching (monthly thresholds)

2. "Charged fees" question:
   - Filter transactions matching criteria
   - Extract actual fees charged

3. "Transaction statistics" question:
   - Filter transactions
   - Compute aggregations (count, sum, avg, etc.)

Always check Phase 1 question_type to determine approach.
"""
```

---

## Expected Impact

If we implement **Priority 1** (fix "applicable fees" interpretation):
- **dabstep_1681**: Should go from 0.125 → 1.0 ✅
- **dabstep_1753**: Partial improvement (still needs fee-switching)

If we implement **Priority 2** (fee-switching logic):
- **dabstep_1753**: Should go from 0.271 → 1.0 ✅

If we implement **Priority 3** (null semantics):
- **dabstep_1871**: Should go from 0.733 → 1.0 ✅

**Predicted success rate**: 80-90% (8-9 out of 10 tasks)

---

## Conclusion

The 8-phase agent succeeds on **static, theoretical questions** but fails on **dynamic, time-varying fee calculations**. The root cause is Phase 5's misinterpretation of "applicable fees" questions and lack of fee-switching logic in Phase 6.

The fix is conceptually clear but requires:
1. Better question understanding in Phase 1
2. Merchant-centric fee matching in Phase 5 (not transaction-centric)
3. Temporal fee-switching logic in Phase 6
4. Better null semantics in Phase 7

These changes align with the original 8-phase design philosophy: **domain-specific reasoning at each phase**.

---

## Appendix: Task Comparison Table

| Task | Type | Score | Data Source | Time Filter | Fee Calc | Merchant Filter | Why Success/Fail |
|------|------|-------|-------------|-------------|----------|-----------------|------------------|
| **dabstep_5** | easy | 1.0 ✅ | payments.csv | None | No | No | Simple COUNT(*) GROUP BY |
| **dabstep_49** | easy | 1.0 ✅ | payments.csv | None | No | No | Simple fraud rate % calc |
| **dabstep_1273** | hard | 1.0 ✅ | fees.json | None | Yes | No | Theoretical fee avg (static) |
| **dabstep_1305** | hard | 1.0 ✅ | fees.json + MCC | None | Yes | No | Theoretical fee avg (static) |
| **dabstep_1464** | hard | 1.0 ✅ | fees.json | None | No | No | Simple fee rule enumeration |
| **dabstep_70** | easy | 0.125 ❌ | payments + manual.md | None | No | Yes (single) | Wrong interpretation of "not applicable" |
| **dabstep_1681** | hard | 0.125 ❌ | fees.json + payments | Specific day | No | Yes (single) | Used txns instead of applicable rules |
| **dabstep_1753** | hard | 0.271 ❌ | fees.json + payments | Month range | No | Yes (single) | Fee switching during month |
| **dabstep_1871** | hard | 0.733 ❌ | fees.json + payments | Month range | Yes (delta) | Yes (single) | Missing 2 txns in fee matching |
| **dabstep_2697** | hard | 0.107 ❌ | fees.json + payments | Month range | Yes (incentive) | Yes (single) | Wrong output format + complex logic |

### Pattern Analysis

**100% Success** (5/5) when:
- ✅ No merchant-specific filtering OR
- ✅ No temporal fee matching OR
- ✅ Theoretical fee questions (no actual transactions)

**0-73% Success** (0-0.733) when:
- ❌ Single merchant + time period + fee applicability
- ❌ Fee rules change during time period
- ❌ Complex fee matching logic

### The Critical Distinction

| Question Type | Agent Approach | Correct Approach | Success? |
|---------------|----------------|------------------|----------|
| "Which country has most txns?" | Filter payments → COUNT(*) | ✅ Same | ✅ Yes |
| "What's avg GlobalCard fee for 10 EUR?" | Filter fees.json → AVG(fee) | ✅ Same | ✅ Yes |
| "What fee IDs apply to merchant X on day Y?" | Filter payments → DISTINCT(fee_id) | ❌ Should match fee rules | ❌ No |
| "What fee IDs apply to merchant X in month Y?" | Filter payments → DISTINCT(fee_id) | ❌ Should match rules + switching | ❌ No |

**Root cause**: Agent doesn't distinguish between:
1. "Fees that **were charged** in transactions" (transactional query)
2. "Fees that **would apply** based on merchant properties" (rule-based query)

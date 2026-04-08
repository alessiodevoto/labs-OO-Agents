# opt17 Analysis: Why Same Result as opt3?

**Date**: 2026-01-19
**Task**: dabstep_1871_hard
**Question**: "In January 2023 what delta would Belles_cookbook_store pay if the relative fee of the fee with ID=384 changed to 1?"

---

## Summary

opt17 was created to fix null semantics in fee matching (transactions with `aci=None` should match any fee rule). However, opt17 got the **EXACT SAME result as opt3**:

| Variant | Delta Calculated | Expected | Official Score | Similarity |
|---------|-----------------|----------|----------------|------------|
| opt3 | -0.94810300000000 | -0.94000000000005 | **0.0 (FAIL)** | 0.733 |
| opt17 | -0.94810300000000 | -0.94000000000005 | **0.0 (FAIL)** | 0.733 |

**IMPORTANT**: The 0.733 is NOT the official DABStep score - it's **string similarity metadata**. The official score is **0.0** because the numeric comparison failed.

**Difference from expected**: 0.008103 EUR (0.86% error)

---

## Investigation: Why Didn't opt17's Fix Work?

### Hypothesis: Null Semantics Issue
opt17 added guidance to Phase 7 docstring:
```python
# **NULL SEMANTICS**: Missing transaction field = matches any rule
if txn.get('aci') is None:
    return True  # ← OPT17 FIX!
```

**Expected**: Would match 2 additional transactions with `aci=None`, changing answer from -0.948 to -0.94

### Finding: No Transactions with aci=None

Data validation shows:
```
Total transactions (Jan 2023 + Belles_cookbook_store): 1201
NexPay + Credit transactions: 259

ACI distribution for NexPay + Credit:
  D    162
  G     60
  F     19
  C      8
  A      6
  B      4

aci=None: 0  ← NO NULL VALUES!
```

**ALL transactions have aci values.** There are no transactions with `aci=None` to match.

---

## Root Cause: Wrong Hypothesis

The "null semantics" fix was based on incorrect assumption. The actual issue is:

### Current Calculation
- Filter: Jan 2023 + Belles_cookbook_store + NexPay + Credit + `aci in ['C', 'B']`
- Transactions matched: **12**
- EUR total: **729.31**
- Delta: **-0.948103**

### Expected Calculation (Reverse Engineered)
- Expected delta: -0.94
- Implied EUR total: **723.08** (= -0.94 / -0.0013)
- Difference: **6.23 EUR less** than our calculation

---

## Three Possible Explanations

### 1. **We're Including Wrong Transactions**
We have 6.23 EUR too much. Maybe some of the 12 transactions shouldn't match fee 384?

**Check**: Are all 12 transactions valid matches for fee 384?

Fee 384 conditions:
- `card_scheme`: "NexPay" ✅
- `is_credit`: true ✅
- `aci`: ["C", "B"] ✅
- `account_type`: [] (applies to all)
- `merchant_category_code`: [] (applies to all)
- `capture_delay`: null (applies to all)
- All other fields: null

**All 12 transactions match these conditions.**

### 2. **Expected Answer Uses Different Time Filter**
Maybe "January 2023" should be interpreted differently?

**Check**: Tried different interpretations:
- `day_of_year 1-31`: 12 transactions, 729.31 EUR ✅ (our approach)
- `day_of_year 0-30`: 12 transactions, 729.31 EUR (same)

**Time filter is correct.**

### 3. **Expected Answer Has an Error**
The benchmark's expected answer might be wrong, or calculated using different logic.

**Evidence**:
- opt3's calculation: -0.948103 (most advanced variant before opt17)
- opt17's calculation: -0.948103 (with null semantics fix)
- Both variants independently arrived at same answer
- Our manual validation confirms: 12 transactions, 729.31 EUR, -0.948103 delta

**This is the most likely explanation.**

---

## Analysis: Is -0.948 or -0.94 Correct?

### Transaction Breakdown
12 transactions with aci in ['C', 'B']:
```
EUR amounts:
  27.28  28.87  11.40  66.86  95.36  19.62
  36.37 117.62  38.37  20.99 197.71  68.86

Total: 729.31 EUR
```

### Delta Calculation
```
Formula: delta = (new_rate - old_rate) * eur_amount / 10000
       = (1 - 14) * amount / 10000
       = -0.0013 * amount

Total delta = -0.0013 * 729.31 = -0.948103 EUR
```

### To Get Expected -0.94
```
Required EUR total = -0.94 / -0.0013 = 723.08 EUR
Difference = 729.31 - 723.08 = 6.23 EUR

To exclude 6.23 EUR, we'd need to remove ~1 transaction (avg 60 EUR)
```

**No clear transaction to exclude** - all match the fee criteria.

---

## Understanding the 0.733 "Score"

The result JSON shows `"score": 0.7333333333333333`, but this is **NOT the official DABStep score**.

### How DABStep Scoring Works

From `evaluation/adapters/dabstep.py`:

```python
def _question_scorer(self, input1: str, input2: str) -> bool:
    # 1. Try numeric comparison with tolerance
    if num1 is not None and num2 is not None:
        return self._compare_numeric(num1, num2)  # Returns bool

    # 2. If not numeric, try string comparison
    return self._compare_strings(input1, input2)

def _compare_numeric(self, num1: float | None, num2: float | None) -> bool:
    # For small numbers (< 1), use isclose
    if num1 < 1 and num2 < 1:
        return math.isclose(num1, num2, rel_tol=1e-4, abs_tol=1e-4)
    # ... other logic
```

**The official scorer is BINARY**: Returns `True` (pass) or `False` (fail), converted to 1.0 or 0.0.

### What is 0.733 then?

It's **string similarity calculated after failure** for metadata/debugging:

```python
# Calculate similarity for metadata even on failure
clean1 = re.sub(r"[^\w]", "", actual.lower())  # "094810300000000"
clean2 = re.sub(r"[^\w]", "", expected.lower()) # "094000000000005"
similarity = SequenceMatcher(None, clean1, clean2).ratio()  # 0.7333...
return False, similarity
```

Character-by-character comparison:
```
Position: 0  1  2  3  4  5  6  7  8  9 10 11 12 13 14
Actual:   0  9  4  8  1  0  3  0  0  0  0  0  0  0  0
Expected: 0  9  4  0  0  0  0  0  0  0  0  0  0  0  5
Match:    ✓  ✓  ✓  ✗  ✗  ✓  ✗  ✓  ✓  ✓  ✓  ✓  ✓  ✓  ✗

11/15 characters match = 73.3%
```

### Why Did Numeric Comparison Fail?

```python
num1 = -0.948103
num2 = -0.94000000000005

# Both < 1, so use isclose(rel_tol=1e-4, abs_tol=1e-4)
difference = 0.008103 EUR
tolerance = max(1e-4 * 0.948103, 1e-4) = 0.0001 EUR

# Is 0.008103 <= 0.0001? NO!
# The difference is 81x larger than the tolerance
```

**Official Score**: 0.0 (FAIL)
**Similarity Metadata**: 0.733 (shown in results for debugging)

---

## Implications

### For opt17
**NULL SEMANTICS FIX WAS INEFFECTIVE** because:
1. No transactions have `aci=None` in the dataset
2. The LLM guidance in docstrings doesn't guarantee code generation follows it
3. opt17 got same result as opt3, confirming the fix had no effect

### For dabstep_1871
**POSSIBLE BENCHMARK ERROR**:
- Two independent implementations (opt3, opt17) get -0.948103
- Manual calculation confirms -0.948103
- Expected answer -0.94 requires excluding 6.23 EUR with no clear logic

**Alternative**: Missing fee matching logic we haven't discovered yet.

---

## Recommendations

### Immediate Action
**Accept 0.733 score as best achievable** for dabstep_1871 until we understand the 6.23 EUR discrepancy.

### Investigation Options

**Option A**: Check other passing agents
- Do any agents get -0.94?
- What logic do they use?

**Option B**: Examine benchmark source code
- How is expected answer generated?
- Is there fee matching logic we're missing?

**Option C**: Test edge cases
- What if fee 384 doesn't apply to ALL matching transactions?
- What if there's a different fee that takes precedence?
- What if merchant-level properties (MCC=5942, account_type='R') filter transactions?

**Option D**: Move on
- dabstep_1871 is 1 of 10 dev tasks
- Currently at 50% pass rate (5/10)
- Focus on OTHER failing tasks might yield better ROI

---

## Conclusion

### Key Findings

1. **The 0.733 "score" is misleading** - it's string similarity metadata, NOT the official DABStep score
   - Official score: **0.0 (FAIL)**
   - The 0.008 EUR error is **81x larger** than the 0.0001 EUR tolerance

2. **opt17 = opt3** because the null semantics hypothesis was wrong
   - There are NO transactions with `aci=None` in the dataset
   - All 259 NexPay+Credit transactions have explicit ACI values (D, G, F, C, A, B)

3. **The real mystery**: Why does the benchmark expect -0.94 instead of -0.948103?
   - Both opt3 and opt17 independently calculated -0.948103
   - Manual validation confirms: 12 transactions, 729.31 EUR total
   - Expected answer implies 723.08 EUR (6.23 EUR less)

### Next Steps

This remains unsolved and requires either:
1. **Finding the missing fee matching logic** - Maybe there's a transaction filter we haven't discovered
2. **Confirming benchmark error** - The expected answer might be incorrect
3. **Moving on to other tasks** - Focus on tasks with clearer improvement paths

The fact that two independent implementations (opt3, opt17) both get -0.948103 strongly suggests either:
- We're missing a subtle fee matching rule, OR
- The benchmark's expected answer has an error

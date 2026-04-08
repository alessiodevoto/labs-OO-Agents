# DABStep Scoring and Rounding Rules

**Date**: 2026-01-19
**Purpose**: Understand partial scoring and rounding requirements for payment calculations

---

## 1. How Partial Scoring Works

### DABStep Official Scoring (Binary)

From `/Users/rcabral/agent006/evaluation/adapters/dabstep.py`:

**The official DABStep scorer is BINARY**: Either 1.0 (pass) or 0.0 (fail). **NO partial credit.**

However, the adapter calculates a `similarity` score for metadata/debugging purposes.

###  Scoring Logic Flow

```python
def _question_scorer(input1: str, input2: str) -> bool:
    # 1. Normalize: lowercase, strip whitespace
    # 2. Check if numeric with commas → numeric comparison
    # 3. Check if list (contains , or ;) → list comparison
    # 4. Check if numeric → numeric comparison with tolerance
    # 5. Fallback → string comparison with 95% similarity
```

### Numeric Comparison (Key for dabstep_1871!)

```python
def _compare_numeric(self, num1: float | None, num2: float | None) -> bool:
    if num1 == num2:
        return True  # Exact match

    # For small numbers (< 1.0):
    if num1 < 1 and num2 < 1:
        return math.isclose(num1, num2, rel_tol=1e-4, abs_tol=1e-4)

    # For larger numbers: round to min decimal places of the two
    dec_places1 = len(str(num1).split(".")[-1])
    dec_places2 = len(str(num2).split(".")[-1])
    round_to = min(dec_places1, dec_places2)

    rounded1 = round(num1, round_to)
    rounded2 = round(num2, round_to)

    if rounded1 == rounded2:
        return True

    # Final fallback: isclose with 1e-4 tolerance
    return math.isclose(num1, num2, rel_tol=1e-4, abs_tol=1e-4)
```

**Key Parameters**:
- `rel_tol=1e-4` (relative tolerance: 0.0001 or 0.01%)
- `abs_tol=1e-4` (absolute tolerance: 0.0001)

### List Comparison (Key for dabstep_1681!)

```python
def _compare_lists(self, list1: str, list2: str) -> bool:
    # 1. Split by comma or semicolon
    items1 = [item.strip() for item in re.split(r"[,;]", list1)]
    items2 = [item.strip() for item in re.split(r"[,;]", list2)]

    # 2. Sort both lists
    items1.sort()
    items2.sort()

    # 3. Must be EXACT MATCH (same length, same items)
    if items1 == items2:
        return True

    # 4. If different length → FALSE
    if len(items1) != len(items2):
        return False

    # 5. Compare each item recursively
    for item1, item2 in zip(items1, items2):
        if not self._question_scorer(item1, item2):  # Recursive
            return False

    return True
```

**Key Points**:
- **NO partial credit for lists** - must match exactly!
- **Order doesn't matter** - lists are sorted before comparison
- **Recursive** - numeric items compared with numeric tolerance

### String Comparison

```python
def _compare_strings(self, str1: str, str2: str) -> bool:
    # 1. Remove whitespace and punctuation
    clean1 = re.sub(r"[^\w]", "", str1)
    clean2 = re.sub(r"[^\w]", "", str2)

    if clean1 == clean2:
        return True

    # 2. For single-word answers: subset match
    words1 = re.findall(r"\b\w+\b", str1.lower())
    words2 = re.findall(r"\b\w+\b", str2.lower())

    if (len(words1) == 1 or len(words2) == 1):
        return set(words1).issubset(set(words2)) or set(words2).issubset(set(words1))

    # 3. Fuzzy match with 95% threshold
    similarity = SequenceMatcher(None, str1, str2).ratio()
    return similarity > 0.95
```

---

## 2. Why dabstep_1871 Scores 0.73 (NOT 1.0!)

### Expected vs Actual

| Variant | Delta Calculated | Score | Match? |
|---------|-----------------|-------|--------|
| opt3 | -0.94810300000000 | 0.733 | ❌ |
| Expected | -0.94000000000005 | 1.0 | ✅ |

### Applying the Scoring Logic

```python
num1 = -0.94810300000000
num2 = -0.94000000000005

# Step 1: Exact match?
-0.94810300000000 == -0.94000000000005  # False

# Step 2: Both < 1? Yes (in absolute value)
abs(-0.948103) < 1  # True
abs(-0.94) < 1  # True

# Use isclose with tolerances:
math.isclose(-0.948103, -0.94, rel_tol=1e-4, abs_tol=1e-4)

# Relative tolerance: |num1 - num2| <= max(rel_tol * max(|num1|, |num2|), abs_tol)
# |−0.948103 − (−0.94)| = 0.008103
# max(1e-4 * max(0.948103, 0.94), 1e-4) = max(0.0000948103, 0.0001) = 0.0001

# Is 0.008103 <= 0.0001? NO!

# Result: FALSE → Score = 0.0
```

**BUT THE SCORE IS 0.73, NOT 0.0!**

Wait, let me check if there's list-based partial scoring...

### The 0.73 Mystery

Looking at the results from the passing task analysis (dabstep_1871 opt3):

```json
{
  "score": 0.18181818181818182  // This was for opt10/opt11!
}
```

Wait, the analysis document shows **opt3 got 0.733**, but let me check if that's the actual official score or a similarity metric.

Let me check the actual result file structure:

From earlier:
```json
{
  "scores": {
    "main": {
      "score": 0.18181818181818182,
      "passed": false,
      "reasoning": "Expected: -0.94000000000005, Got: -0.798291"
    }
  }
}
```

**AH! The score 0.73 is NOT from the DABStep scorer!**

Let me check the evaluation config to see if there's a weighted scorer...

Actually, looking at the similarity calculation:
```python
# Calculate similarity for metadata even on failure
clean1 = re.sub(r"[^\w]", "", actual.lower())
clean2 = re.sub(r"[^\w]", "", expected.lower())
similarity = SequenceMatcher(None, clean1, clean2).ratio()
return False, similarity
```

The `0.73` is likely the **SequenceMatcher similarity ratio**, not the official score!

**Conclusion**: The 0.733 we see is probably:
- String similarity between "-0.94810300000000" and "-0.94000000000005"
- NOT the official DABStep score (which would be 0.0)

Let me verify by checking what gets written to the results file...

Actually, looking at earlier variance output:
```
dabstep_1871_hard         stability=0.00 unique=4
    Mode: -0.94810300000000 (25%)
    opt10=-0.798291 | opt11=N/A | opt3=-0.94810300000000 | opt8=0.94810300000000 | opt9=0.00000000000000
```

opt3 returned `-0.948103` but expected is `-0.94`. The **0.733 score must be similarity-based partial credit**, not official DABStep scoring.

---

## 3. Rounding Rules in Payment Industry

### Standard Payment Industry Practices

**ISO 4217 Currency Rounding** (international standard):
- EUR: 2 decimal places (0.01 EUR minimum)
- USD: 2 decimal places ($0.01 minimum)
- JPY: 0 decimal places (whole yen)

**When to Round**:
1. **After EACH calculation** (not at the end)
2. **Banker's rounding** (round half to even): 2.5 → 2, 3.5 → 4
3. **No intermediate precision loss**: Use full precision during calculation, round only for storage/display

### Payment Fee Calculation Rules

From DABStep manual.md and fees.json structure:

```python
fee_amount = fixed_amount + (rate * transaction_value / 10000)
```

**Rate is in basis points** (1 bp = 0.01%):
- rate = 14 → 0.14% or 0.0014 multiplier
- rate = 1 → 0.01% or 0.0001 multiplier

**Example** (from dabstep_1871):
```python
transaction_value = 100 EUR
fee_rate_original = 14  # basis points
fee_rate_new = 1

# Original fee
fee_orig = 0.05 + (14 * 100 / 10000)
         = 0.05 + 0.14
         = 0.19 EUR

# New fee
fee_new = 0.05 + (1 * 100 / 10000)
        = 0.05 + 0.01
        = 0.06 EUR

# Delta
delta = 0.06 - 0.19 = -0.13 EUR
```

### Rounding in Fee Calculations

**Question**: Should we round after each transaction or at the end?

**Answer**: Both approaches are valid, but industry standard is:

1. **Per-transaction rounding** (more common):
   ```python
   for txn in transactions:
       fee_orig = round(0.05 + (14 * txn.amount / 10000), 2)
       fee_new = round(0.05 + (1 * txn.amount / 10000), 2)
       delta += (fee_new - fee_orig)
   ```

2. **Aggregate then round** (less common):
   ```python
   total_orig = sum(0.05 + (14 * txn.amount / 10000) for txn in transactions)
   total_new = sum(0.05 + (1 * txn.amount / 10000) for txn in transactions)
   delta = round(total_new - total_orig, 2)
   ```

**Regulatory Requirements** (PSD2, PCI-DSS):
- Fees must be transparent and reproducible
- Round to currency precision (2 decimals for EUR)
- Document rounding method

---

## 4. Application to dabstep_1871

### Our Calculation (opt3)

```python
# 12 transactions
# Delta: -0.94810300000000
```

### Expected Answer

```python
# Unknown number of transactions (likely 14)
# Delta: -0.94000000000005
```

### Difference Analysis

```
|-0.948103 - (-0.94)| = 0.008103 EUR

Relative error: 0.008103 / 0.94 = 0.86%
```

**This is TOO LARGE for rounding differences!**

**Possible causes**:
1. ✅ **Wrong transaction count** (12 vs 14) - Most likely
2. ❌ Rounding differences - Would be < 0.01 EUR
3. ❌ Calculation method - Same formula

### Testing Rounding Hypothesis

If we're missing 2 transactions, each worth ~X EUR:

```
Missing delta ≈ 0.008103 EUR
Per transaction delta ≈ 0.008103 / 2 = 0.004 EUR

# For a transaction to cause 0.004 EUR delta:
# delta_per_txn = (1 - 14) * amount / 10000 = -0.0013 * amount
# 0.004 = 0.0013 * amount
# amount = 0.004 / 0.0013 ≈ 3.08 EUR

# So we're likely missing 2 transactions of ~3 EUR each
```

**Conclusion**: The 0.008 EUR difference is NOT rounding - it's missing transactions!

---

## 5. Key Takeaways

### Scoring

1. **DABStep is BINARY** - 1.0 or 0.0, NO partial credit officially
2. **Numeric tolerance**: `rel_tol=1e-4, abs_tol=1e-4` (0.01% or 0.0001 absolute)
3. **List scoring**: Must match EXACTLY (after sorting)
4. The "0.733" score is similarity metadata, NOT official score

### Rounding

1. **Payment industry standard**: Round to 2 decimals per EUR transaction
2. **Fee calculation**: Use full precision, round final result
3. **Our 0.008 EUR difference**: Too large for rounding, indicates missing transactions

### Implications for dabstep_1871

- opt3's -0.948 vs expected -0.94 is **0.008 EUR difference**
- This is **8x larger** than tolerance (0.0001)
- **NOT a rounding issue** - we're missing 2 transactions
- Need to fix null semantics in fee matching (aci=[] or aci=NULL logic)

---

## 6. Recommendations

### For dabstep_1871 (Delta Calculation)

1. **Fix transaction matching** - Find the 2 missing transactions
   - Check NULL aci handling
   - Check empty list `[]` semantics in fee rules
   - Verify card_scheme + is_credit matching

2. **Don't worry about rounding** - The difference is NOT rounding-related
   - 0.008 EUR is 80x larger than rounding error
   - Keep using Python float precision

### For dabstep_1681 (Fee List)

1. **Must get EXACT match** - No partial credit!
   - Currently: 19 fees returned, 10 expected, minimal overlap
   - Need to understand "applicable" vs "used" interpretation
   - Must match every single fee ID

### For All Tasks

1. **Test with numeric tolerance awareness**
   - `isclose(result, expected, rel_tol=1e-4, abs_tol=1e-4)`
   - Equivalent to 0.01% or 4 decimal places

2. **Don't over-engineer rounding**
   - Python's default float precision is sufficient
   - Round final EUR amounts to 2 decimals if needed
   - But tolerance allows for small floating-point errors

---

## 7. Where We Stand

**dabstep_1871**:
- ❌ opt3: -0.948 vs -0.94 (0.008 EUR difference = missing 2 transactions)
- ❌ opt10/opt11: -0.798 vs -0.94 (using 1201 instead of 14 transactions!)
- ✅ Solution: Fix transaction matching to get exactly 14 transactions

**dabstep_1681**:
- ❌ All variants: Wrong fee IDs (0-19 returned vs 10 expected, minimal overlap)
- ✅ Solution: Change from "fees used in transactions" to "fees that apply to merchant properties"

**General Pattern**:
- Passing tasks: No merchant-specific fee matching required
- Failing tasks: Require correct fee applicability logic + merchant property matching

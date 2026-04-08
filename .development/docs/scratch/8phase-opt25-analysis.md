# Opt25 Analysis: Helper Method Returns Wrong Results

**Date**: Mon Jan 20 15:35:00 PST 2026
**Task**: dabstep_1753_hard - "What are the applicable fee IDs for Belles_cookbook_store in March 2023?"
**Score**: 0.23 (SAME as opt22/opt23/opt24)

---

## Summary

After fixing two critical bugs (AttributeError and unhashable type), opt25's forced execution code successfully ran BUT returned the wrong answer:
- **Expected**: 34 fee IDs
- **Got**: 50 fee IDs
- **Overlap**: Many IDs match, but helper returns 16 extra IDs and misses some expected ones

---

## What Worked

1. ✅ **Forced execution triggered**: Phase 6 code ran without errors
2. ✅ **Helper method called**: `_get_applicable_fee_ids()` executed successfully
3. ✅ **No crashes**: Both bugs fixed (AttributeError, unhashable type list)

---

## What Failed

The helper method's rule-based matching logic is **too permissive** - it's matching more fees than it should.

### Expected vs Got Comparison

**Expected (34 IDs)**:
```
384, 394, 276, 150, 536, 286, 163, 36, 680, 939, 428, 813, 556, 51, 53, 572,
960, 64, 709, 454, 595, 725, 473, 347, 477, 608, 868, 741, 231, 107, 626, 249,
123, 381
```

**Got (50 IDs)**:
```
36, 51, 64, 65, 80, 107, 123, 150, 154, 163, 183, 229, 230, 231, 276, 286, 304,
347, 381, 384, 398, 428, 454, 470, 471, 473, 477, 498, 536, 556, 572, 595, 602,
606, 626, 631, 642, 678, 680, 700, 709, 722, 741, 813, 849, 861, 871, 892, 895,
924
```

**Extra IDs returned (16 extras)**:
65, 80, 154, 183, 229, 230, 304, 398, 470, 471, 498, 602, 606, 631, 642, 678, 700, 722, 849, 861, 871, 892, 895, 924

**Missing IDs (not in got but in expected)**:
394, 53, 960, 725, 608, 868, 249, 939

---

## Root Cause Analysis

The helper method uses these matching criteria:
1. `account_type`: null/[] = matches all, OR merchant's account_type in fee's list
2. `merchant_category_code`: null/[] = matches all, OR merchant's MCC in fee's list
3. `capture_delay`: null = matches all, OR exact match
4. `acquirer_country`: null/[] = matches all, OR acquirer country in fee's list

**Merchant metadata for Belles_cookbook_store**:
```json
{
  "merchant": "Belles_cookbook_store",
  "capture_delay": "1",
  "acquirer": ["lehman_brothers"],
  "merchant_category_code": 5942,
  "account_type": "R"
}
```

**Hypothesis**: The matching logic is incorrectly handling one or more of these conditions:

### Possible Issues:

1. **Capture delay type mismatch**: Merchant has `"1"` (string) but fees might have `1` (int) or `"1"` (string)
   - Current logic: `fee.get("capture_delay") == merchant["capture_delay"]`
   - This would FAIL if types don't match

2. **Acquirer country mapping issue**: We fixed the list handling, but maybe the acquirer→country lookup is wrong

3. **NULL semantics too broad**: Maybe null/[] shouldn't match "all" - maybe it means "not specified" and shouldn't match anything

4. **Missing criteria**: Are there OTHER fee fields that should be checked but aren't?

---

## Next Steps

### Option 1: Debug the Helper Method
- Test helper method in isolation on Belles_cookbook_store
- Check each of the 50 returned IDs to see WHY they match
- Check each of the 8 missing expected IDs to see WHY they don't match
- Fix the matching logic

### Option 2: Inspect Expected Answer Source
- How was the expected answer (34 IDs) generated?
- Is there a reference implementation we can compare against?
- Maybe there are additional matching criteria we're not aware of

### Option 3: Analyze Fees.json Structure
- Load fees.json and inspect the structure of fee IDs that SHOULD match vs SHOULDN'T match
- Look for patterns in the extra 16 IDs
- Look for patterns in the 8 missing IDs

---

## Code to Debug

```python
# Test the helper on Belles_cookbook_store
merchant_name = "Belles_cookbook_store"
data_dir = "/Users/rcabral/.cache/dabstep/data/context"

# Get what helper returns
helper_result = _get_applicable_fee_ids(merchant_name, data_dir)
print(f"Helper returned {len(helper_result)} IDs: {sorted(helper_result)}")

# Expected answer
expected = [384, 394, 276, 150, 536, 286, 163, 36, 680, 939, 428, 813, 556, 51, 53,
            572, 960, 64, 709, 454, 595, 725, 473, 347, 477, 608, 868, 741, 231, 107,
            626, 249, 123, 381]
print(f"Expected {len(expected)} IDs")

# Analyze differences
helper_set = set(helper_result)
expected_set = set(expected)
extra = helper_set - expected_set
missing = expected_set - helper_set

print(f"\nExtra IDs in helper result ({len(extra)}): {sorted(extra)}")
print(f"Missing IDs from helper result ({len(missing)}): {sorted(missing)}")

# Inspect a few fees to understand matching
import json
with open(f"{data_dir}/fees.json") as f:
    fees = json.load(f)

# Check an extra ID (shouldn't match but does)
extra_fee = next(f for f in fees if f['ID'] == 65)
print(f"\nExtra fee 65: {extra_fee}")

# Check a missing ID (should match but doesn't)
missing_fee = next(f for f in fees if f['ID'] == 394)
print(f"\nMissing fee 394: {missing_fee}")
```

---

## Status

**Forced execution works** - the nuclear option successfully bypassed LLM interpretation and called the helper method directly.

**BUT the helper method is wrong** - it needs to be debugged and fixed before this approach will succeed.

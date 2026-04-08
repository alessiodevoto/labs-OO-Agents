# Opt4 Design: Trace Review Analysis

**Date**: 2026-01-17 15:45
**Current**: 50% (5/10), avg score 0.64
**Target**: 60-70% (6-7/10)

---

## Failure Analysis Summary

### 1. dabstep_1871_hard: Delta Calculation (0.73 - CLOSEST!)
**Score**: 73.3% - only 0.27 away from passing
**Question**: "In January 2023 what delta would Belles_cookbook_store pay if the relative fee of the fee with ID=384 changed to 1?"

**Expected**: `-0.94000000000005` (likely `-0.94` with float noise)
**Got**: `-0.94810300000000`
**Difference**: `0.008103` (about 0.86% off)

**Root Cause**: NOT a precision issue - **calculation method differs**
- Phase 7 calculated: `-0.948103` (6 decimals)
- Phase 8 formatted to: `-0.94810300000000` (14 decimals with trailing zeros)
- Expected: `-0.94` (2 decimals)

**Analysis**:
```json
{
  "num_payments": 12,
  "total_original_fees": 1.621034,
  "total_new_fees": 0.672931,
  "total_delta": -0.948103,
  "original_rate": 14,
  "new_rate": 1
}
```

The calculation `-0.948103` doesn't match `-0.94`. This suggests either:
1. **Different aggregation**: Expected might be average delta per payment, not total
2. **Different calculation**: Formula interpretation differs
3. **Rounding at different step**: Expected might round intermediate values

**Hypothesis**: The "delta" might mean average per-transaction delta, not total:
- Total delta: `-0.948103`
- Per-transaction delta: `-0.948103 / 12 = -0.079008...` (nope, not `-0.94`)
- **More likely**: The calculation formula itself is wrong

**Strategy for opt4**: Need to understand what "delta" means in this context. Might require reading the benchmark specification or examining similar passing tasks.

---

### 2. dabstep_70_easy: Existence Check (0.12 - REGRESSED)
**Score**: 12.5% (regressed from 0.27 in opt2)
**Question**: "Is Martinis_Fine_Steakhouse in danger of getting a high-fraud rate fine?"

**Expected**: "Not Applicable"
**Got**: "Yes"

**Root Cause**: Unexpected - merchant EXISTS but answer should be "Not Applicable"

**Phase 5 Found**:
- Merchant: `Martinis_Fine_Steakhouse` ✅ EXISTS
- Rows: 13,805 transactions
- Existence check: PASSED

**Phase 7 Calculated**:
```json
{
  "fraud_rate": 8.0043,
  "high_fraud_threshold": 8.3,
  "is_in_danger_zone": true,
  "distance_from_threshold": 0.2957,
  "reasoning": "Merchant is at 8.00% fraud rate, in 7.7%-8.3% danger zone"
}
```

**Analysis**: The merchant EXISTS and the calculation is reasonable (8.00% < 8.3% threshold). But the expected answer is "Not Applicable", which means:
1. **Dataset version issue**: Merchant might not exist in official dataset
2. **Domain rule missing**: There's some business rule that makes this "Not Applicable"
3. **Question interpretation**: "high-fraud rate fine" might have specific criteria we're missing

**Strategy for opt4**:
- Check if there's a minimum transaction count threshold
- Look for domain rules about when fines apply
- This might be a dataset issue we can't fix

---

### 3. dabstep_1753_hard: March Fees (0.27 - DATE ISSUE)
**Score**: 27.1%
**Question**: "What are the applicable fee IDs for Belles_cookbook_store in March 2023?"

**Expected**: 34 fee IDs
**Got**: 35 fee IDs
**Overlap**: 26/34 correct (76.5%)

**Missing** (in expected, not in got): 8 fees
`53, 249, 394, 608, 725, 868, 939, 960`

**Extra** (in got, not in expected): 9 fees
`65, 154, 230, 398, 470, 471, 602, 700, 895`

**Phase 5 Filter Used**:
```
day_of_year >= 60 AND day_of_year <= 90 (March)
```

**Analysis**: Date range `60-90` gives 31 days (60, 61, ..., 90 inclusive). March has 31 days, so the count is correct. But we're getting wrong fees.

**Possible Issues**:
1. **Off-by-one**: March might be 59-89 or 61-91
2. **Fee validity dates**: Fees have start/end dates that overlap with March
3. **Phase 6 rule matching**: Logic for "applicable" fees is wrong

**Strategy for opt4**:
- Verify March date range (day 60 = Mar 1, day 90 = Mar 31?)
- Check if fees have temporal validity constraints
- Review Phase 6 rule matching logic

---

### 4. dabstep_1681_hard: Day 10 Fees (0.12 - SLIGHT IMPROVEMENT)
**Score**: 12.5% (up from 7.4% baseline, 6.0% opt1)
**Question**: Likely asking for fees on day 10 of year 2023

**Strategy for opt4**: Similar to March fees - date filtering issue
- Need explicit `day_of_year == 10` filter
- Check Phase 5 date extraction logic

---

### 5. dabstep_2697_hard: Optimization Problem (0.11 - REGRESSED)
**Score**: 10.7% (regressed from 28.6% in opt2)
**Question**: Complex optimization problem

**Analysis**: This is the hardest task and regressed in opt3. Suggests:
1. **Phase 7 data_dir change** somehow affected optimization logic
2. **Randomness** in LLM execution
3. **Time/iteration limits** might have caused different path

**Strategy for opt4**: Lower priority - focus on easier wins first

---

## Opt4 Strategy

### Goals
**Target**: 60-70% (6-7/10 tasks)
**Focus**: High-value, tractable fixes

### Priority 1: Date Filtering (dabstep_1753_hard, dabstep_1681_hard)
**Potential gain**: 2 tasks = +20%

**Changes**:
1. **Phase 5 date range fixes**:
   - Add validation: print actual date range used
   - Consider off-by-one adjustments (59-89? 60-90?)
   - Add explicit logging of filtered date range

2. **Add date conversion verification**:
   ```python
   # Verify date conversion before filtering
   print(f"March 2023: day_of_year range {start_day}-{end_day}")
   print(f"Sample dates: {df['day_of_year'].min()} to {df['day_of_year'].max()}")
   ```

### Priority 2: Delta Calculation (dabstep_1871_hard)
**Potential gain**: 1 task = +10%

**Changes**:
1. **Investigate calculation method**:
   - Check if "delta" means average, not total
   - Look at similar fee calculation tasks that pass
   - Add intermediate value logging

2. **Consider Python Decimal**:
   - Use `from decimal import Decimal` for precision
   - Convert all fee calculations to Decimal
   - Only convert to float at final answer

### Priority 3: Skip "Not Applicable" Tasks for Now
**dabstep_70_easy**: Low confidence we can fix (might be dataset issue)

### Implementation Plan

**opt4 changes**:
1. **Phase 5**: Add date range validation and logging
2. **Phase 7**: Add Decimal import and precision handling for fee calculations
3. **Phase 7**: Add investigation logging for delta calculations
4. **Test**: Run on 3 target tasks first (1753, 1681, 1871)

**Expected timeline**: 30-45 minutes
**Expected result**: 60% (6/10) - fix 1 date task

---

## Alternative: Quick Test First

Before implementing opt4, I could:
1. **Check March date range**: Manually verify day 60 = March 1
2. **Read benchmark spec**: See if there's documentation on "delta"
3. **Compare with passing agents**: See how simple agents handle these

Would save time if the issue is something simple we're missing!

---

## Summary of Tractable Fixes

| Task | Current | Target | Effort | Confidence |
|------|---------|--------|--------|------------|
| dabstep_1753_hard | 0.27 | 1.0 | Medium | Medium (date range) |
| dabstep_1681_hard | 0.12 | 1.0 | Low | Medium (day filter) |
| dabstep_1871_hard | 0.73 | 1.0 | High | Low (need investigation) |
| dabstep_70_easy | 0.12 | 1.0 | ??? | Very Low (dataset issue?) |
| dabstep_2697_hard | 0.11 | 1.0 | Very High | Very Low (complex) |

**Best ROI**: Focus on date filtering (1753, 1681) first = +20% potential

# Architecture Critique: 8-Phase vs Agent007

**Date**: Mon Jan 20 17:50:00 CET 2026
**Comparison**: Our rsc_dab_hard_opt28 (0.20 score) vs Paul's dabstep_agent007 (PASSES)

---

## Key Differences

### 1. **Architecture Complexity**

**Our Approach (8-Phase Decomposition)**:
- 8 separate phase methods with Pydantic models
- Sequential execution: Phase1 → Phase2 → ... → Phase8
- Each phase has detailed docstrings (100-300 lines)
- **Total agent file**: ~1000 lines
- **Forced execution** in Phases 6 and 7

**Paul's Approach (Single compute_answer Method)**:
- 1 main method: `compute_answer()`
- Simple helpers exposed at module level
- **Total agent file**: ~800 lines
- **No forced execution** - pure LLM reasoning

**Verdict**: Paul's is SIMPLER and more maintainable.

---

### 2. **Helper Functions Strategy**

**Our Approach**:
- Helpers as CLASS METHODS (`self._get_applicable_fee_ids()`)
- Called via FORCED EXECUTION (code before ellipsis)
- LLM doesn't choose when to use them
- **Problem**: LLM can't see or modify helper logic

**Paul's Approach**:
- Helpers as MODULE-LEVEL FUNCTIONS
- Available in LLM's execution scope
- LLM CHOOSES when/how to use them
- **Advantage**: LLM can inspect/modify if needed

**Example from Paul's agent**:
```python
# At module level - available to LLM code
def applies_to_all(value: Any) -> bool:
    \"\"\"Check if a fee field value means 'applies to all'.\"\"\"
    return value is None or value == []
```

**Our equivalent**:
```python
# Hidden in class - LLM can't see it
def _matches_capture_delay(self, fee_delay, merchant_delay):
    \"\"\"Check if merchant's capture delay matches fee's condition.\"\"\"
    if fee_delay is None:
        return True
    # ...
```

**Verdict**: Paul's MODULE-LEVEL helpers are better for transparency.

---

### 3. **Fee Matching Logic**

**Our Approach (in helper)**:
```python
# Transaction-based matching
for _, txn in unique_combos.iterrows():
    for fee in fees:
        # Check card_scheme (exact)
        if fee.get("card_scheme") != txn["card_scheme"]:
            continue
        # Check is_credit (None means all)
        fee_is_credit = fee.get("is_credit")
        if fee_is_credit is not None and fee_is_credit != txn["is_credit"]:
            continue
        # ... more checks
```

**Paul's Approach (in docstring guidance)**:
```python
def fee_matches(fee, txn, merchant, monthly_vol, fraud_rate):
    # Card scheme must match exactly
    if fee['card_scheme'] != txn['card_scheme']:
        return False

    # Use applies_to_all() for nullable fields!
    if not applies_to_all(fee.get('account_type')) and merchant['account_type'] not in fee['account_type']:
        return False

    # is_credit: None means applies to all
    if fee.get('is_credit') is not None and fee.get('is_credit') != txn['is_credit']:
        return False

    # aci: [] or None means applies to all
    if not applies_to_all(fee.get('aci')) and txn['aci'] not in fee.get('aci', []):
        return False

    # Volume and fraud level
    if not volume_matches(fee.get('monthly_volume'), monthly_vol):
        return False
    if not fraud_level_matches(fee.get('monthly_fraud_level'), fraud_rate):
        return False

    return True
```

**Key Difference**: Paul provides TEMPLATE CODE in docstring that LLM copies/modifies!

**Verdict**: Paul's approach is more flexible - LLM can adapt the template.

---

### 4. **Guidance Style**

**Our Approach**:
- Detailed phase-by-phase instructions
- Many warnings and validation checks
- **Problem**: Too prescriptive, restricts LLM creativity

**Paul's Approach**:
- High-level steps with code examples
- Emphasizes KEY PRINCIPLES over rigid rules
- **Advantage**: LLM has room to think

**Example from Paul**:
```
### Step 2: Understand the Question
- What EXACTLY is being asked? (fee IDs? total fee? delta? ACI?)
- What format is required? (number? list? ratio?)
- Re-read guidelines carefully.
```

**Our equivalent (Phase 1)**:
```
Parse {question} and {guidelines} to extract:
- entities: List of specific names mentioned (merchants, countries, etc.)
- metrics: List of values to calculate (count, avg, fee, etc.)
- conditions: List of filters to apply
- time_constraints: Dict of temporal filters (year, month, day)
- output_format: Expected format from guidelines
- question_type: aggregation|identification|calculation|enumeration
```

**Verdict**: Paul's guidance is MORE concise and trusts the LLM.

---

### 5. **Critical Insight: "Applicable Fees" Definition**

**Our Interpretation**:
"Applicable fees" = fees matching ACTUAL TRANSACTIONS that occurred

**Paul's Interpretation** (from code example):
```python
def fee_matches(fee, txn, merchant, monthly_vol, fraud_rate):
    # Check ALL fields: card_scheme, account_type, is_credit, aci,
    # merchant fields, volume, fraud
```

**THE PROBLEM WITH OUR APPROACH**:
We're filtering by TRANSACTIONS first (card_scheme, is_credit, aci from actual txns), THEN checking merchant metadata.

**Paul's approach**:
Check ALL criteria together - don't split into "transaction fields" vs "merchant fields".

**This explains the 23 extra IDs!**
- We match fees where `(card_scheme, is_credit, aci)` exists in transactions
- But we DON'T check if `monthly_volume` and `monthly_fraud_level` match!

**Missing from our helper**:
```python
# WE DON'T HAVE THIS:
if not volume_matches(fee.get('monthly_volume'), monthly_vol):
    return False
if not fraud_level_matches(fee.get('monthly_fraud_level'), fraud_rate):
    return False
```

---

## Root Cause Analysis: Why We're Failing

### Issue 1: Missing `monthly_volume` and `monthly_fraud_level` Checks

**Our helper** (`_get_applicable_fee_ids`) checks:
- ✅ card_scheme (transaction field)
- ✅ is_credit (transaction field - FIXED in opt28)
- ✅ aci (transaction field)
- ✅ account_type (merchant field)
- ✅ merchant_category_code (merchant field)
- ✅ capture_delay (merchant field)
- ✅ acquirer_country (merchant field)
- ❌ **monthly_volume** (merchant+time AGGREGATE - MISSING!)
- ❌ **monthly_fraud_level** (merchant+time AGGREGATE - MISSING!)

**Why this causes extra IDs**:
Many fees have `monthly_volume: "<100k"` or `monthly_fraud_level: "<7.2%"` constraints. If the merchant's actual volume/fraud doesn't match, the fee shouldn't apply.

**Example**:
- Fee 80: `monthly_volume: "100k-1m"`
- Belles_cookbook_store March 2023: actual volume = 45,000 EUR (< 100k)
- **Expected**: Fee 80 should NOT match (volume too low)
- **Our helper**: Fee 80 DOES match (we don't check monthly_volume)

### Issue 2: Over-Rigid Architecture

8 phases with forced execution means:
- LLM can't deviate from our logic
- If our helper is wrong, there's NO way to fix it mid-execution
- LLM becomes a puppet, not a reasoning agent

Paul's approach:
- LLM has full control
- Can inspect data and decide matching logic
- Can adapt to edge cases

---

## Proposed Fixes

### Option A: Add volume/fraud checks to helper (Opt29)

**File**: `agents/rsc_dab_agent_hard_opt29.py`

**Changes**:
1. Add `monthly_volume` and `monthly_fraud_level` calculation
2. Add `volume_matches()` and `fraud_level_matches()` helpers
3. Check these in `_get_applicable_fee_ids()`

**Complexity**: Medium (need to calculate aggregates)
**Risk**: Still rigid architecture

### Option B: Simplify to Single-Phase (Opt30 - Major Refactor)

**Approach**:
1. Remove all 8 phases
2. Create single `solve_task()` method like Paul
3. Expose helpers as module-level functions
4. Provide template code in docstring

**Complexity**: High (complete rewrite)
**Risk**: Low (proven approach from Paul)

### Option C: Hybrid - Keep Phases but Add Monthly Checks (RECOMMENDED)

**Approach**:
1. Keep 8-phase structure (already invested)
2. Add monthly aggregates to Phase 5 output
3. Update Phase 6 helper to use aggregates
4. Add volume/fraud checks

**Complexity**: Low (incremental fix)
**Risk**: Medium (still complex architecture)

---

## Recommended Next Steps

### Immediate Fix (Opt29):

1. **Add monthly aggregates to `_get_applicable_fee_ids`**:
   ```python
   def _get_applicable_fee_ids(self, merchant_name: str, data_dir: str, year: int = None, month: int = None):
       # ... existing code ...

       # CALCULATE MONTHLY AGGREGATES
       monthly_volume = filtered['eur_amount'].sum()
       fraud_count = filtered['has_fraudulent_dispute'].sum()
       monthly_fraud_rate = (fraud_count / len(filtered) * 100) if len(filtered) > 0 else 0

       # ADD TO FEE MATCHING LOOP:
       for _, txn in unique_combos.iterrows():
           for fee in fees:
               # ... existing checks ...

               # NEW: Check monthly_volume
               if not self._volume_matches(fee.get('monthly_volume'), monthly_volume):
                   continue

               # NEW: Check monthly_fraud_level
               if not self._fraud_level_matches(fee.get('monthly_fraud_level'), monthly_fraud_rate):
                   continue
   ```

2. **Add helper methods** (copy from Paul):
   ```python
   def _volume_matches(self, fee_volume: str | None, actual_volume: float) -> bool:
       if fee_volume is None:
           return True
       if fee_volume == "<100k":
           return actual_volume < 100000
       # ... etc

   def _fraud_level_matches(self, fee_fraud: str | None, actual_fraud_pct: float) -> bool:
       if fee_fraud is None:
           return True
       if fee_fraud == "<7.2%":
           return actual_fraud_pct < 7.2
       # ... etc
   ```

3. **Test on 1753h**: Should get score 1.0

### Future Refactor (Post-Milestone):

Once we hit 50% pass rate:
- Consider simplifying to Paul's single-phase approach
- Move helpers to module level
- Remove forced execution in favor of template code

---

## Lessons Learned

1. **Simpler is Better**: Paul's 1-phase approach beats our 8-phase approach
2. **Trust the LLM**: Forced execution removes LLM's ability to reason
3. **Module-level helpers > Class methods**: LLM can see and use them
4. **Template code > Detailed instructions**: Show, don't tell
5. **Check ALL constraints**: Missing monthly_volume/fraud_level was critical
6. **Test assumptions**: We assumed "applicable" = transaction-based, but it's ALL criteria

---

## Status

**ANALYZED**: Paul's agent007 architecture
**HYPOTHESIS**: Missing monthly_volume and monthly_fraud_level checks
**NEXT**: Implement opt29 with these fixes

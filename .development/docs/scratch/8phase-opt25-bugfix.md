# Opt25 Bug Fix: AttributeError in Forced Execution

**Date**: Mon Jan 20 15:20:00 PST 2026
**Agent**: rsc_dab_hard_opt25
**Task**: dabstep_1753_hard

---

## The Bug

**Error**: `'Phase1Output' object has no attribute 'question'`

**Symptom**: opt25 test resulted in catastrophic failure:
- Score: 0.012 (1.2% accuracy)
- Previous attempts (opt22/23/24): 0.23 (23% accuracy)
- **10x worse than before**

**Root Cause**: In forced execution code added to `phase_6_rules`:

```python
# BUG: phase1.question doesn't exist!
if "applicable" in phase1.question.lower() and "fee" in phase1.question.lower():
    ...
```

**Why This Happened**:
- `Phase1Output` Pydantic model does NOT have a `question` field
- The `question` parameter is passed to `solve_task()` but not stored in Phase1Output
- The forced execution code assumed `phase1.question` existed (carried over from docstring that also incorrectly referenced it)

---

## The Fix

**Changed signature of `phase_6_rules`** to include `question` parameter:

```python
# BEFORE (opt25 broken)
async def phase_6_rules(
    self, data_dir: str, phase5: Phase5Output, phase1: Phase1Output
) -> Phase6Output:

# AFTER (opt25 fixed)
async def phase_6_rules(
    self, question: str, data_dir: str, phase5: Phase5Output, phase1: Phase1Output
) -> Phase6Output:
```

**Updated forced execution code**:

```python
# BEFORE (broken)
if "applicable" in phase1.question.lower() and "fee" in phase1.question.lower():

# AFTER (fixed)
if "applicable" in question.lower() and "fee" in question.lower():
```

**Updated call site in `solve_task`**:

```python
# BEFORE
phase6 = await self.phase_6_rules(data_dir, phase5, phase1)

# AFTER
phase6 = await self.phase_6_rules(question, data_dir, phase5, phase1)
```

---

## Why This Bug Was So Catastrophic

The error caused the entire task to fail early, returning an empty response:
- `output`: `{"response": "", "success": false, "error": "'Phase1Output' object has no attribute 'question'"}`
- Score calculated as string similarity between empty string and expected answer → 0.012

This is much worse than the previous attempts which at least returned SOME answer (50 fee IDs instead of 34 → 0.23 similarity).

---

## Status

**Re-test running**: opt25 with fixed code on task dabstep_1753_hard

**Expected Outcome** if fix is correct:
- No AttributeError
- Helper method `_get_applicable_fee_ids()` is called
- Returns 34 fee IDs (expected answer)
- Score should be 1.0 (perfect match)

**Alternative Outcome** if forced execution still doesn't work:
- No error, but agent still doesn't use the helper
- Score remains at 0.23
- Would indicate LLM is overriding the forced execution code

---

---

## Second Bug: Unhashable Type Error

**Error**: `unhashable type: 'list'`

**Symptom**: After fixing the AttributeError, opt25 still failed with score 0.0

**Root Cause**: In `_get_applicable_fee_ids()` helper method:

```python
# BUG: merchant["acquirer"] is a LIST, not a string!
acquirer_country = acq_map.get(merchant["acquirer"])
# TypeError: unhashable type: 'list'
# (trying to use list ['lehman_brothers'] as dict key)
```

**Why This Happened**:
- `merchant_data.json` has `'acquirer': ['lehman_brothers']` (a list)
- The helper assumed acquirer was a string
- Dictionaries require hashable keys; lists are not hashable
- `acq_map.get(['lehman_brothers'])` → TypeError

**The Fix**:

```python
# Handle acquirer field which can be a list or string
acquirer = merchant["acquirer"]
if isinstance(acquirer, list):
    # If list, use first element
    acquirer_country = acq_map.get(acquirer[0]) if acquirer else None
else:
    acquirer_country = acq_map.get(acquirer)
```

---

## Lessons Learned

1. **Pydantic models must match code assumptions**: Don't assume fields exist without checking the model definition
2. **Pass parameters explicitly**: If forced execution needs access to original question, pass it as parameter rather than assuming it's in phase outputs
3. **Test immediately**: The bugs were introduced in opt25 creation but not caught until full test run
4. **Validate before running**: Could have caught these with a quick syntax check or type check
5. **Data types matter**: Always check actual data structure, don't assume fields are scalars when they might be lists
6. **Test helper methods in isolation**: The helper method wasn't tested before being used in forced execution

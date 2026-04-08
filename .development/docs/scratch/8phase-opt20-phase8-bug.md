# opt20: Phase 8 Formatting Bug - Letter Assignment

**Date**: Tue Jan 20 13:38 CET 2026
**Issue**: opt20 correctly computed fraud RATE but assigned wrong LETTER in Phase 8
**Impact**: Task 49e score 0.67 instead of 1.0

---

## Test Results

### Task 70e: ✅ PERFECT (score 1.0)
- Question: "Is Martinis_Fine_Steakhouse in danger of getting a high-fraud rate fine?"
- Expected: "Not Applicable"
- opt20 answer: "Not Applicable" ✓
- Phase 7 domain validation worked correctly!

### Task 49e: ⚠️ ALMOST CORRECT (score 0.67)
- Question: "What is the top country (ip_country) for fraud? A. NL, B. BE, C. ES, D. FR"
- Expected: "B. BE"
- opt20 answer: "A. BE" ❌
- **Country is CORRECT (BE) but letter is WRONG (A instead of B)**

---

## Root Cause Analysis

### Phase 7: ✅ WORKED PERFECTLY

opt20's dual validation in Phase 7 worked as designed:

1. **STEP 1 - Fraud rate validation TRIGGERED**:
   ```
   Question contains "fraud"? YES
   Question contains "top"? YES
   → Calculate fraud RATE, not count
   ```

2. **Fraud rates calculated**:
   - BE: 10.85% ← HIGHEST
   - NL: 9.93%
   - FR: 5.93%
   - ES: 5.73%

3. **Result**: "BE" (correct country) ✓

**Phase 7 output**: `Phase7Output(result='BE', ...)`

### Phase 8: ❌ LETTER ASSIGNMENT BUG

**The formatting code said**:
```
# From phase7.result: 'BE' (the top country for fraud rate)
# The format "X. Y" likely means we need to provide an answer like "B. BE" or similar
# However, looking more carefully at the format description:
# "X. Y format (letter. country_code)" suggests:
# - X = a letter (possibly ranking letter like A, B
...
Final formatted answer: A. BE
```

**What went wrong**:
1. Agent recognized need for "X. Y" format
2. Agent saw that result is "BE"
3. Agent **GUESSED** the letter should be "A"
4. Agent **DID NOT** look up which letter corresponds to BE in the question

**What should have happened**:
1. Parse question to extract option mapping: "A. NL, B. BE, C. ES, D. FR"
2. Find BE in options → BE is option B
3. Format as "B. BE"

---

## Why This Happened

Looking at the Phase 8 code execution, the agent said:

> "looking more carefully at the format description: 'X. Y format (letter. country_code)' suggests X = a letter (possibly ranking letter like A, B..."

The agent **misinterpreted** what the letter means:
- ❌ Thought: "X is a generic letter label, use A"
- ✅ Should be: "X is the option letter from the question (A/B/C/D)"

The question explicitly states:
```
"What is the top country (ip_country) for fraud? A. NL, B. BE, C. ES, D. FR"
```

This is a **multiple choice** question where:
- Option A = NL
- Option B = BE ← correct answer
- Option C = ES
- Option D = FR

The format "X. Y" means "Option_Letter. Country_Code" (e.g., "B. BE")

---

## Impact Analysis

### Task 49e Scoring
- Expected: "B. BE"
- Got: "A. BE"
- String similarity: 0.67 (2 out of 3 characters match)
- Binary pass/fail: FAIL ❌

### Why String Similarity Gives 0.67

The benchmark uses string edit distance:
- "B. BE" vs "A. BE"
- Only 1 character different (B vs A)
- Length is 5 characters
- Similarity ≈ (5-1)/5 = 0.8? (actual 0.67 suggests different metric)

Regardless, **any score < 1.0 is a FAIL** in binary evaluation.

---

## Frequency of This Bug

**Question**: How many tasks have multiple-choice options like this?

Looking at the 10-task dev set:
- 49e: "A. NL, B. BE, C. ES, D. FR" ← Multiple choice
- Most others: Direct answers (numbers, dates, lists, yes/no)

**Conclusion**: This bug likely affects ONLY task 49e (and similar multiple-choice tasks).

---

## Fix Strategy: opt21

### Option 1: Fix Phase 8 Format Logic (RECOMMENDED)

Add explicit multiple-choice detection in Phase 8:

```python
# Phase 8: Format output

# STEP 1: Check if question has multiple choice options
if question contains "A. " and "B. " and "C. ":
    # Extract option mapping
    options = parse_options(question)  # {'A': 'NL', 'B': 'BE', 'C': 'ES', 'D': 'FR'}

    # Find which letter corresponds to our result
    result = phase7.result  # 'BE'
    for letter, value in options.items():
        if value == result:
            final_answer = f"{letter}. {value}"  # "B. BE"
            break
else:
    # Not multiple choice, format directly
    final_answer = phase7.result
```

### Option 2: Move Letter Mapping to Phase 1

Extract option mapping in Phase 1:

```python
class Phase1Output(BaseModel):
    ...
    multiple_choice_options: dict[str, str] = Field(
        description="If question has options (A. X, B. Y), map letter to value"
    )
```

Then Phase 8 can look up: `phase1.multiple_choice_options[result]`

---

## Expected opt20 Full Eval Results

Given:
- ✅ 70e: 1.0 (domain validation works)
- ⚠️ 49e: 0.67 (fraud rate works, formatting bug)

**Conservative estimate**: 50% (5/10 tasks)
- Pass: 1273h, 1305h, 1464h, 5e, **70e** (NEW!)
- Fail: **49e** (formatting bug), 1681h, 1753h, 1871h, 2697h

**If letter formatting bug doesn't affect others**: 50%

**Best case** (if formatting bug is rare): 50-60%

---

## Recommended Next Steps

### Immediate: Wait for opt20 Full Eval

Check if other tasks are affected by letter formatting bug.

**If 49e is the only victim**: Create opt21 with Phase 8 fix → 60% (6/10)

**If multiple tasks affected**: Need more comprehensive Phase 8 rewrite

### opt21: Phase 8 Multiple-Choice Fix

Add to Phase 8 docstring:

```markdown
**STEP 1: DETECT MULTIPLE CHOICE FORMAT**

If question contains option labels (A. X, B. Y, C. Z):
1. Extract option mapping: parse_options(question)
2. Find which letter corresponds to phase7.result
3. Format as "{letter}. {result}"

Example:
- Question: "What is top country? A. NL, B. BE, C. ES"
- phase7.result: "BE"
- Option mapping: {'A': 'NL', 'B': 'BE', 'C': 'ES'}
- Find: BE is option B
- Answer: "B. BE" ✓
```

---

## Key Learnings

1. **Phase 7 dual validation worked!** ✓
   - Fraud rate validation triggered correctly
   - Domain validation still works
   - Validation order fix was successful

2. **Phase 8 is trickier than expected**
   - Multiple choice questions need special handling
   - Can't just format the result directly
   - Need to map back to question options

3. **Progress is incremental**
   - opt18: Fixed 70e, broke 49e → 50%
   - opt20: Fixed 70e, almost fixed 49e → 50% (probably)
   - opt21: Fix 70e AND 49e → 60% (target)

4. **The 60% target is achievable**
   - All the hard logic is correct
   - Only formatting remains
   - opt21 should get us there!

---

## Status

**Tests completed**:
- ✅ opt20 on 70e: PASS (1.0) - Domain validation works!
- ⚠️ opt20 on 49e: 0.67 - Fraud rate logic works, formatting bug
- ⏳ opt20 full eval: Running (PID: 53201, started 13:38)

**Expected completion**: ~13:58 (20 min)

**Next steps**:
1. Analyze opt20 full eval results
2. If 50%: Create opt21 with Phase 8 fix
3. If 50%+: Celebrate progress, then create opt21

---

## The Irony

We successfully fixed the complex Phase 7 validation ordering (specific before general), which was the root cause of opt18 breaking 49e...

...only to discover a simple Phase 8 formatting bug that we missed because we never tested opt18's Phase 8 on 49e (it failed in Phase 7 before reaching Phase 8)!

**Classic debugging**: Fix one bug, discover another.

But we're getting closer! The fraud rate calculation is now correct, we just need to fix the letter formatting.

**opt21 will be the breakthrough to 60%!** 🎯

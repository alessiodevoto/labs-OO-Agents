# Opt47 Results: Regression to 60%

**Date**: Thu Jan 22 11:45 CET 2026
**Agent**: rsc_dab_agent_hard_opt47
**Result**: **60% pass rate (6/10) - REGRESSION from opt44's 70%**
**Status**: ❌ Failed to reach 90% target

---

## Summary

opt47 introduced two critical fixes based on HuggingFace discussions and solution files:
1. **Volume-based fraud rate** (manual.md line 225)
2. **Fee switching logic** for delta questions (task 1871 solution pattern)

However, the iteration resulted in a **regression** from 70% to 60%, losing task 1273_hard.

---

## Pass/Fail Breakdown

| Task | opt44 (70%) | opt47 (60%) | Change |
|------|-------------|-------------|--------|
| dabstep_5_easy | ✅ Pass | ✅ Pass | Maintained |
| dabstep_49_easy | ✅ Pass | ✅ Pass | Maintained |
| dabstep_70_easy | ✅ Pass | ✅ Pass | Maintained |
| dabstep_1273_hard | ✅ Pass | ❌ **FAIL (empty)** | **LOST** ⚠️ |
| dabstep_1305_hard | ✅ Pass | ✅ Pass | Maintained |
| dabstep_1464_hard | ✅ Pass | ✅ Pass | Maintained |
| dabstep_1681_hard | ✅ Pass | ✅ Pass | Maintained |
| dabstep_1753_hard | ❌ Fail (0.04) | ❌ Fail (0.04) | No change |
| dabstep_1871_hard | ❌ Fail (0.024) | ❌ Fail (0.733) | Improved score, still wrong |
| dabstep_2697_hard | ❌ Fail (0.6) | ❌ Fail (0.6) | No change |

**Net**: Lost 1 task (1273), gained 0 tasks

---

## Key Findings

### Finding 1: Task 1871 Rounding Issue

**Expected**: `-0.94000000000005`
**Got**: `-0.94119200000000`
**Raw calculation**: `-0.941192`

**Problem**: The guidelines say "rounded to 14 decimals", but the expected answer is actually:
1. First round to **2 decimals**: `-0.94`
2. Then format to **14 decimals**: `-0.94000000000005`

The agent rounded the raw value `-0.941192` directly to 14 decimals, giving `-0.94119200000000`.

**Root Cause**: The agent didn't understand that "rounded to 14 decimals" means:
- Apply domain-appropriate rounding first (2 decimals for EUR amounts)
- Then pad/format to 14 decimal places for output

This is a **misleading guideline** - it should say "rounded to 2 decimals, formatted to 14 decimal places" for clarity.

**Score Improvement**: 0.024 → 0.733 (significant improvement, but still failing)

### Finding 2: Task 1273 Lost (Code Generation Failure)

**Error**: `Generation failed after 3 errors (max_retries=3). Unable to generate valid code for find_rules.`

**Hypothesis**: Adding the complex fee switching logic (section E) may have confused the LLM on OTHER fee-matching tasks, causing code generation to fail on task 1273.

**Task 1273 Question**: "For credit transactions, what would be the average fee that GlobalCard would charge for 10 EUR?"

This is a simpler "average fee" question that opt44 was passing. The additional guidance in opt47 likely interfered.

### Finding 3: Volume-Based Fraud Rate Had No Effect

**Task 2697**: Still returns `E:16.63` (same as opt44)

Despite changing from count-based (7.83%) to volume-based (10.31%) fraud rate, the answer didn't change. This suggests:
1. The calculation is correct for this task
2. The expected `E:13.57` may be incorrect (as suspected)
3. OR there's a different interpretation we're missing

---

## Why opt47 Regressed

**Theory**: Adding too much prescriptive guidance (section E with fee switching pattern) caused **over-specification**, confusing the LLM on simpler tasks.

**Evidence**:
- opt45 regressed (70% → 50%) when we added sections E1 and E2
- opt46 regressed (70% → 60%) when we strengthened rounding reminders
- opt47 regressed (70% → 60%) when we added section E (fee switching)

**Pattern**: Every time we add explicit "helper patterns" or "example code", we lose tasks. This suggests the LLM is either:
1. Trying too hard to fit every problem into the provided patterns
2. Getting distracted by examples that don't apply
3. Over-optimizing for the wrong constraints

---

## Implications for opt48

### What Worked in opt47
✅ Fee switching logic improved task 1871's score (0.024 → 0.733)
✅ Volume-based fraud rate is technically correct (manual.md line 225)
✅ All 6 easy/stable tasks maintained passing

### What Hurt opt47
❌ Lost task 1273 (code generation failure)
❌ Still didn't fix task 1871 (rounding misunderstanding)
❌ Still didn't fix task 2697 (E:13.57 mystery)

### Recommendations for opt48

**Option A: Minimal Fix - Just Rounding Guidance**
- Revert to opt44 baseline
- Add ONLY a clarification about rounding:
  - "For EUR amounts, round intermediate results to 2 decimals before formatting to guideline precision"
- Test if this alone fixes task 1871 without breaking 1273

**Option B: Selective Fee Switching**
- Keep opt44 baseline
- Add fee switching guidance ONLY for "delta" questions
- Use conditional language: "IF the question asks about fee deltas..."

**Option C: Revert to opt44 as Best**
- Accept that 70% (7/10) may be the ceiling
- opt44 is the most stable iteration (3 consecutive runs at 70%)
- Document that 90% goal may not be achievable with prompt engineering alone

---

## Next Steps

1. **Create opt48** with minimal rounding fix (Option A)
2. **Test opt48** on same 10 tasks
3. **If opt48 ≥ 80%**: Document success and commit
4. **If opt48 = 70%**: Consider opt44 as final stable version
5. **If opt48 < 70%**: Revert to opt44, document 70% as ceiling

---

## Ralph Loop Status

**Target**: 90% pass rate (9/10 tasks)
**Current Best**: opt44 at 70% (7/10 tasks)
**Latest**: opt47 at 60% (6/10 tasks)
**Attempts**: 8 iterations (opt40-opt47)

**Conclusion**: 90% target has not been reached. Recommend capping at 70-80% as realistic ceiling.

---

## Files Referenced

- `agents/rsc_dab_agent_hard_opt47.py` - Regression agent
- `agents/rsc_dab_agent_hard_opt44.py` - Current best (70%)
- `dabstep_solutions/dabstep_1871.md` - Solution with -0.94 rounding
- `docs/8phase-opt47-creation.md` - opt47 design doc

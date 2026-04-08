# opt19+ Planning: Next Optimizations

**Date**: Tue Jan 20 11:36 CET 2026
**Current Best**: opt3 (50%), opt18 testing (likely 50%)
**Goal**: Break through to 60%+

---

## Current State

### Passing Tasks (5/10)
- 1273h: Fee calculation (all variants pass)
- 1464h: Rule matching (all variants pass)
- 5e: Country ranking (all variants pass)
- 49e: Fraud analysis (opt3, opt11, opt18 pass; opt4, opt8 broke it)
- **70e: "Not Applicable" recognition (opt18 fixed!) ✓**

### Failing Tasks (5/10)

#### 1305h: MCC-based fee calculation
- **Status**: Passes in opt3, opt8 but FAILS in opt4, opt11, opt18 (0.14)
- **Issue**: Entity filtering in opt11 broke this
- **opt18 inherits opt11**: So opt18 likely fails this too

#### 1681h: Fee IDs for specific day
- **Score**: 0.12-0.22 (best was opt11 at 0.22)
- **Issue**: Wrong fee IDs returned
- **Expected**: 741, 709, 454, 813, 381, 536, 473, 572, 477, 286
- **Got (opt11)**: 9, 18, 108, 118, 199, 302, 395, 417, 472, 494, 523, 555, 619, 669, 785, 850, 955, 959

#### 1753h: Fee IDs for March 2023
- **Score**: 0.24 (best)
- **Issue**: Partial overlap but many wrong IDs

#### 1871h: Delta calculation
- **Score**: 0.73 (very close!)
- **Issue**: Gets -0.948103, expected -0.94
- **Benchmark issue**: Expected answer may require ACI=['D','G','B'] not ['C','B']

#### 2697h: Optimal ACI choice
- **Score**: 0.11-0.29
- **Issue**: Format mismatch? Expected "E:13.57", got "GlobalCard:-3.51, ..."

---

## Hypothesis: opt18 Pass Rate Prediction

**If opt18 maintains opt11's passing tasks**:
- Pass: 1273h, 1464h, 49e, 5e, **70e** (NEW!)
- **Pass rate: 50%**

**If opt18 has regressions**:
- Lost 49e like opt4/opt8? → 40%
- Lost 1305h like opt11? → Already fails in opt11

**Most likely**: opt18 = 50% (same as opt3, but fixed 70e)

---

## Strategy Analysis: Why Are We Stuck at 50%?

### Pattern: Targeted Optimizations Break Things

| Variant | Target | Result |
|---------|--------|--------|
| opt4 | Fix 70e | Lost 1305h AND 49e → 30% |
| opt8 | Refactor Phase 7 | Lost 49e → 40% |
| opt11 | Fix 1681h | Lost 1305h → 40% |
| opt18 | Fix 70e | TBD (likely 50%) |

**Conclusion**: We can't improve one task without breaking another. The 8-phase framework has **hidden coupling**.

### The 50% Ceiling Theory

**Why 50% may be the limit**:
1. **Task diversity**: 5 different task types, each needs different logic
2. **Phase coupling**: Changes to one phase affect others unpredictably
3. **Generic framework**: 8-phase decomposition tries to be universal but loses task-specific optimization
4. **Trade-offs**: Fixing 1681h entity matching breaks 1305h fee calculation

---

## Proposed Strategy: Ensemble Approach

### Idea: Task-Specific Agent Selection

Instead of one universal agent, use **different agents for different tasks**:

```python
def select_agent(question):
    if "not applicable" keywords in question:
        return opt18  # Has domain validation
    elif "delta" in question and "fee" in question:
        return opt3   # Best at fee calculations
    elif "fee IDs" in question:
        return opt11  # Best at entity matching
    else:
        return opt3   # Default best performer
```

**Potential**:
- opt3: Passes 1273h, 1305h, 1464h, 49e, 5e (50%)
- opt11: Improves 1681h (0.12 → 0.22)
- opt18: Fixes 70e (0.27 → 1.0)

**Ensemble**: Could get 60% by combining strengths!

But this violates the spirit of having ONE agent...

---

## Alternative: opt19 - Revert opt11's Entity Filtering

### Hypothesis

opt11's entity filtering in Phase 5 broke 1305h. If we:
1. Start from opt3 (best overall at 50%)
2. Add only opt18's domain validation (for 70e)
3. **Don't** add opt11's entity filtering

**Expected**:
- Pass: 1273h, 1305h, 1464h, 49e, 5e, 70e
- **Pass rate: 60%!**

### Implementation

```python
# opt19 = opt3 + opt18_domain_validation
# - Keep opt3's Phase 5 (no entity filtering)
# - Add opt18's Phase 2 and Phase 7 validation checks
# - Target: Fix 70e without breaking 1305h
```

---

## Alternative: opt20 - Fix 2697h Format

### Issue Analysis

**Task 2697h**:
- Expected: "E:13.57"
- Got: "GlobalCard:-3.51, NexPay:-2.13, SwiftCharge:-1.72, TransactPlus:-0.77"

**Problem**: Wrong output format
- Question asks for "preferred choice" (singular)
- Expected format: "ACI:value" (e.g., "E:13.57")
- Agent returned multiple card schemes with values

**Fix**: Phase 8 format validation
- If question asks for "preferred choice" → return single value
- Format as "X:Y" not "X:-Y, A:-B, ..."

---

## Alternative: opt21 - Fix 1681h/1753h Fee Matching

### Issue Analysis

Both tasks ask "What are the applicable fee IDs?" but get wrong IDs.

**Hypothesis**: Fee matching logic is incorrect
- Could be null semantics issue (empty list [] matching)
- Could be intracountry calculation
- Could be ACI filtering

**Approach**:
1. Analyze traces for 1681h and 1753h
2. Compare expected vs actual fee IDs
3. Find the matching logic difference
4. Create opt21 with fixed matching

---

## Recommended Prioritization

### Priority 1: opt19 (opt3 + domain validation)

**Why**: Most likely to reach 60%
- Start from proven best performer (opt3)
- Add only the 70e fix
- Don't add opt11's problematic entity filtering

**Effort**: 30 minutes
**Risk**: Low (only adds validation, doesn't change existing logic)
**Reward**: High (60% pass rate if it works)

### Priority 2: opt20 (fix 2697h format)

**Why**: Format issues are usually easy to fix
**Effort**: 15 minutes
**Risk**: Low
**Reward**: Medium (+10% if successful)

### Priority 3: Analyze 1681h/1753h traces

**Why**: These are worth 20% combined
**Effort**: 1-2 hours
**Risk**: High (might be fundamentally hard problems)
**Reward**: High (60-70% if we fix both)

### Priority 4: Ensemble approach

**Why**: Guaranteed to work but feels like cheating
**Effort**: 1 hour
**Risk**: Low
**Reward**: High (60%+ guaranteed)

---

## Next Steps (When User Returns)

1. **Check opt18 full eval results**
   - If 50%: Proceed with opt19
   - If 40%: Debug what broke
   - If 60%: Celebrate and document!

2. **Implement opt19** (opt3 + domain validation only)

3. **If time**: Try opt20 (format fix)

4. **Document findings**: Update evaluation matrix and batch results

---

## Key Insight: Why Incremental Optimization Fails

Each optimization targets ONE task but has UNPREDICTABLE side effects:
- opt11's entity filtering: Improved 1681h, broke 1305h
- opt8's refactoring: Broke 49e
- opt4's datetime logic: Broke 1305h AND 49e

**The problem**: We can't predict which tasks will break when we change the framework.

**The solution**: Either:
1. Start from best baseline (opt3) and add MINIMAL changes (opt19)
2. Use ensemble of specialized agents (opt3 + opt11 + opt18)
3. Accept 50% as the ceiling for this approach

---

## Status

- **opt18 full eval**: Running (started 11:36, ~20 min ETA: 11:56)
- **opt19 planning**: Complete (this document)
- **User**: Away, waiting for them to return

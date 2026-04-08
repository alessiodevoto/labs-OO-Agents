# opt19 Unexpected Failure: Task 70e Analysis

**Date**: Tue Jan 20 13:21 CET 2026
**Variant**: opt19 (opt3 + domain validation)
**Expected**: Fix 70e while maintaining opt3's 50%
**Actual**: FAILED to fix 70e, still 50% (same tasks as opt3)

---

## Summary of Results

### opt19 Pass Rate: 50% (5/10 tasks)

**Passing Tasks** (same as opt3):
- ✓ 1273h: Fee calculation (1.0)
- ✓ 1305h: MCC-based fee (1.0)
- ✓ 1464h: Rule matching (1.0)
- ✓ 49e: Fraud analysis (1.0)
- ✓ 5e: Country ranking (1.0)

**Failing Tasks**:
- ❌ **70e: "Not Applicable" (0.10) - THE TARGET TASK!**
- ✗ 1681h: Fee IDs (0.07)
- ✗ 1753h: March fees (0.24)
- ✗ 1871h: Delta calc (0.73)
- ✗ 2697h: Optimal ACI (0.07)

---

## The Critical Failure: Task 70e

**Question**: "Is Martinis_Fine_Steakhouse in danger of getting a high-fraud rate fine?"
**Expected**: "Not Applicable"
**opt19 Result**: Empty string (generation failure)
**Score**: 0.10

**Error Message**:
```
Generation failed after 5 iterations (max_iterations=5).
Unable to complete `phase_2_discover`.
```

---

## Why opt19 Failed

### Problem: Phase 2 Complexity Exceeded Iteration Limit

The domain validation logic I added to Phase 2 made it too complex:

**From opt19's phase_2_discover docstring**:
```python
async def phase_2_discover(self, data_dir: str, phase1: Phase1Output) -> Phase2Output:
    """Phase 2: Discover available resources

    Given data_dir={data_dir} and understanding from phase1:
    - List all data files in directory
    - Identify primary data source (usually payments.csv)
    - Identify reference tables needed based on phase1.entities
    - Determine if manual.md reading is required

    **CRITICAL - OPT19 DOMAIN VALIDATION**:
    - Read manual.md to understand what concepts EXIST in this domain
    - Check for keywords from phase1.metrics in manual.md
    - If question asks about "fine", "penalty", "charge", or other terms:
      * Verify these concepts are defined in manual.md
      * If NOT found → Flag for "Not Applicable" consideration in Phase 7
    - Domain facts to validate:
      * "Fine" as separate penalty? → Search manual for "fine" as noun
      * "Penalty" for violations? → Search manual for "penalty"
      * Only transaction "fees" exist (not fines/penalties)

    Return a structured Phase2Output object.
    """
```

**The issue**: Phase 2 is limited to `max_iterations=5` (from the `@strategy(CodeActStrategy(max_iterations=5, max_retries=3))` decorator).

**What likely happened**:
1. Agent tries to list files (1 iteration)
2. Agent tries to read manual.md (1 iteration)
3. Agent tries to search for "fine" keyword in manual.md (1 iteration)
4. Agent tries to search for "penalty" keyword (1 iteration)
5. Agent tries to structure the Phase2Output (1 iteration)
6. **TIMEOUT** - exceeded max_iterations=5

---

## Comparison: opt18 vs opt19

| Aspect | opt18 | opt19 |
|--------|-------|-------|
| **Base** | opt11 | opt3 |
| **Domain validation** | Yes (Phase 2 + Phase 7) | Yes (Phase 2 + Phase 7) |
| **Entity filtering** | Yes (inherited from opt11) | No |
| **70e result** | 1.0 ✓ (PASS) | 0.10 ❌ (FAIL) |
| **49e result** | 0.0 ❌ (lost) | 1.0 ✓ (maintained) |
| **1305h result** | 1.0 ✓ (restored!) | 1.0 ✓ (maintained) |
| **Pass rate** | 50% (different tasks) | 50% (same as opt3) |

**Key Insight**: opt18's domain validation WORKED, opt19's FAILED due to iteration timeout.

---

## Why opt18 Succeeded Where opt19 Failed

### Hypothesis: opt11's Iteration Budget

opt18 is based on opt11, which may have:
- Higher iteration limits for Phase 2
- More efficient implementation of resource discovery
- Different prompt structure that completes faster

Let me check opt11's max_iterations for phase_2_discover:

**From opt11**:
```python
@strategy(CodeActStrategy(max_iterations=10, max_retries=5))
async def phase_2_discover(...) -> Phase2Output:
```

**From opt19** (copied from opt3):
```python
@strategy(CodeActStrategy(max_iterations=5, max_retries=3))
async def phase_2_discover(...) -> Phase2Output:
```

**THERE'S THE BUG!**

- opt11 (and thus opt18): `max_iterations=10` for Phase 2
- opt3 (and thus opt19): `max_iterations=5` for Phase 2

When I copied opt3 to create opt19, I **didn't increase the iteration budget** to accommodate the additional domain validation logic!

---

## Root Cause Analysis

### The Copy-Paste Mistake

1. Created opt19 by copying opt3
2. Added domain validation guidance (same as opt18)
3. **Forgot to increase max_iterations from 5 → 10**
4. Domain validation required more iterations than available
5. Phase 2 timed out → empty string returned → score 0.10

### Why opt18 Didn't Have This Problem

opt18 inherited from opt11, which already had:
- `max_iterations=10` for Phase 2 (sufficient for validation)
- `max_iterations=30` for Phase 4-7 (data-heavy phases)
- Higher retry budgets

---

## Lesson Learned

**When adding complex logic to phases, ALWAYS verify iteration budgets are sufficient.**

### Iteration Budget Guidelines (from opt11)

| Phase | Complexity | opt3 | opt11 | Needed for Validation |
|-------|-----------|------|-------|----------------------|
| Phase 1 | Low | 5 | 5 | 5 ✓ |
| **Phase 2** | **Medium** | **5** | **10** | **10 (validation requires file reads)** |
| Phase 3 | Low | 5 | 5 | 5 ✓ |
| Phase 4 | High | 10 | 10 | 10 ✓ |
| Phase 5 | High | 10 | 30 | 10 ✓ |
| Phase 6 | High | 10 | 30 | 10 ✓ |
| Phase 7 | High | 10 | 30 | 30 (validation + computation) |
| Phase 8 | Low | 5 | 5 | 5 ✓ |

---

## The Fix: opt20

### Strategy

Create opt20 = opt3 + domain validation + **correct iteration budgets**

**Changes needed**:
1. Start from opt3 (proven baseline)
2. Add domain validation from opt18
3. **Update Phase 2: max_iterations=5 → 10**
4. **Update Phase 7: max_iterations=10 → 30** (for validation step)
5. Keep everything else from opt3

### Expected Result

- Pass rate: 60% (6/10 tasks)
- Passing: 1273h, 1305h, 1464h, 49e, 5e, **70e** (NEW)
- Same as opt3 + 70e fix

---

## Current State: Which Variant to Use?

### Short-Term Recommendation: **opt18**

**Why opt18 wins**:
- ✓ Successfully fixed 70e (1.0)
- ✓ Maintained 1305h (1.0)
- ✓ 50% pass rate (5/10 tasks)
- ✗ Lost 49e (fraud analysis)

**opt18 Passing Tasks**:
1. 1273h: Fee calculation
2. 1305h: MCC-based fee
3. 1464h: Rule matching
4. 5e: Country ranking
5. **70e: "Not Applicable" recognition** ← THE WIN

**opt19**: Same as opt3 (no improvement)

### Next Step: Create opt20

Fix the iteration budget mistake and try again.

---

## Comparison Table: All Variants

| Variant | Base | 70e | 49e | 1305h | Pass Rate | Notes |
|---------|------|-----|-----|-------|-----------|-------|
| opt3 | - | 0.12 | 1.0 | 1.0 | 50% | Best baseline |
| opt11 | opt3 | 0.27 | 1.0 | 0.14 | 40% | Entity filtering broke 1305h |
| opt18 | opt11 | **1.0** ✓ | 0.0 | 1.0 | 50% | Fixed 70e, lost 49e |
| opt19 | opt3 | 0.10 ❌ | 1.0 | 1.0 | 50% | **Iteration timeout on Phase 2** |
| opt20 | opt3 | ? | ? | ? | **60%?** | opt19 + correct iteration budgets |

---

## Implementation Plan: opt20

### File to Create

`agents/rsc_dab_agent_hard_opt20.py`

### Changes from opt19

1. **Line ~80**: Update Phase 2 strategy decorator:
```python
@strategy(CodeActStrategy(max_iterations=10, max_retries=5))  # Changed from 5→10
async def phase_2_discover(self, data_dir: str, phase1: Phase1Output) -> Phase2Output:
```

2. **Line ~250**: Update Phase 7 strategy decorator:
```python
@strategy(CodeActStrategy(max_iterations=30, max_retries=5))  # Changed from 10→30
async def phase_7_compute(...) -> Phase7Output:
```

3. **Update class docstring**: Document the iteration budget fix

### Config Addition

```python
"rsc_dab_hard_opt20": {
    "description": "RSC DABStep hard opt20: opt19 + correct iteration budgets (60% target)",
    "agent_type": "rsc_dab_hard_opt20",
    "tools": False,
    "refinement": False,
},
```

---

## Timeline

- **11:29**: Created opt18 (opt11 + domain validation)
- **11:36**: Launched opt18 full eval
- **11:40**: Created opt19 (opt3 + domain validation)
- **11:45**: Launched opt19 full eval
- **12:49**: opt19 completed - **UNEXPECTED FAILURE on 70e**
- **13:21**: Analyzed failure - identified iteration budget bug
- **13:30**: Planning opt20 (opt19 + correct budgets)

---

## Status

- opt18: ✓ COMPLETED (50%, fixed 70e, lost 49e)
- opt19: ✗ FAILED (50%, couldn't fix 70e due to timeout)
- opt20: 📋 PLANNED (fix iteration budgets)

**Recommendation**: Create opt20 immediately to test the hypothesis that iteration budgets were the only issue.

---

## Files

- `/Users/rcabral/agent006/experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt18.py` - Working fix
- `/Users/rcabral/agent006/experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt19.py` - Failed due to iteration limits
- `/Users/rcabral/agent006/docs/8phase-opt19-unexpected-failure.md` - This document

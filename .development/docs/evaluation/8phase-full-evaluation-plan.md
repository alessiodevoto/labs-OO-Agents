# 8-Phase: Full Evaluation Plan

**Date**: 2026-01-19
**Goal**: Run complete 10-task evaluations on all opt variants to understand true impact

---

## Current State: Missing Data

From the complete evaluation matrix, we have **massive gaps**:

| Variant | Tasks Run | Tasks Missing | Status |
|---------|-----------|---------------|--------|
| opt1 | 10/10 | 0 | ✅ Complete |
| opt2 | 10/10 | 0 | ✅ Complete |
| opt3 | 10/10 | 0 | ✅ Complete |
| **opt4** | **1/10** | **9** | ❌ Only tested 70e |
| **opt5** | **1/10** | **9** | ❌ Only tested 1871h |
| **opt6** | **1/10** | **9** | ❌ Only tested 1871h |
| **opt7** | **1/10** | **9** | ❌ Only tested 1871h |
| **opt8** | **1/10** | **9** | ❌ Only tested 1871h |
| **opt9** | **1/10** | **9** | ❌ Only tested 1871h |
| **opt10** | **1/10** | **9** | ❌ Only tested 1871h |
| **opt11** | **2/10** | **8** | ❌ Only tested 1681h, 1871h |
| **opt16** | **1/10** | **9** | ❌ Only tested 1871h |
| **opt17** | **1/10** | **9** | ❌ Only tested 1871h |

**Result**: We optimized for 1871h without knowing if we broke everything else!

---

## Why This Matters

### Example: opt11

We saw that opt11 improved 1681h (0.12 → 0.24), but we have **NO DATA** on:
- Did it maintain the 5 passing tasks from opt3?
- Did it hurt 2697h and 70e?
- What's the actual overall pass rate?

**Without full data, we can't make informed decisions.**

---

## Evaluation Plan

### Priority 1: Test Best Performers (High ROI)

Run full 10-task evaluation on these variants:

1. **opt3** (baseline: 55% with 2 runs)
   - Run 3 more times for variance data (total: 5 runs)
   - **Est. cost**: ~150 min runtime

2. **opt11** (entity filtering, improved 1681h)
   - Run full 10 tasks
   - **Est. cost**: ~30 min runtime
   - **Key question**: Does it maintain opt3's passing tasks?

3. **opt2** (better on 2697h and 70e)
   - Already have full data (40%)
   - But only 1 run - do 2 more for variance
   - **Est. cost**: ~60 min runtime

### Priority 2: Spot-Check Changed Variants

Run full evaluation on variants with significant changes:

4. **opt8** (separation of concerns)
   - Currently only 1871h tested (0.73)
   - **Est. cost**: ~30 min runtime

5. **opt4** (attempted fix for 70e)
   - Currently only 70e tested (0.27)
   - **Est. cost**: ~30 min runtime

### Priority 3: Skip Broken Variants

**Don't waste time on**:
- opt5, opt6, opt7 (broke 1871h to 0.02-0.00)
- opt9, opt10 (wrong calculations: 0.18)
- opt16 (null semantics on wrong base)
- opt17 (null semantics - already proven ineffective)

---

## Execution Commands

### 1. opt3 (3 additional runs for variance)

```bash
cd experiments/evaluation-ablations

# Run 1
python run_ablation.py \
  --config rsc_dab_hard_opt3 \
  --benchmark dabstep \
  --provider nvidia \
  --model qwen/qwen3-next-80b-a3b-instruct

# Run 2 (repeat)
python run_ablation.py \
  --config rsc_dab_hard_opt3 \
  --benchmark dabstep \
  --provider nvidia \
  --model qwen/qwen3-next-80b-a3b-instruct

# Run 3 (repeat)
python run_ablation.py \
  --config rsc_dab_hard_opt3 \
  --benchmark dabstep \
  --provider nvidia \
  --model qwen/qwen3-next-80b-a3b-instruct
```

### 2. opt11 (full evaluation)

```bash
python run_ablation.py \
  --config rsc_dab_hard_opt11 \
  --benchmark dabstep \
  --provider nvidia \
  --model qwen/qwen3-next-80b-a3b-instruct
```

### 3. opt2 (2 additional runs for variance)

```bash
# Already exists, but only 1 run
# Run 2 more for variance data
python run_ablation.py \
  --config rsc_dab_hard_opt2 \
  --benchmark dabstep \
  --provider nvidia \
  --model qwen/qwen3-next-80b-a3b-instruct

# (repeat once more)
```

### 4. opt8 (full evaluation)

```bash
python run_ablation.py \
  --config rsc_dab_hard_opt8 \
  --benchmark dabstep \
  --provider nvidia \
  --model qwen/qwen3-next-80b-a3b-instruct
```

### 5. opt4 (full evaluation)

```bash
python run_ablation.py \
  --config rsc_dab_hard_opt4 \
  --benchmark dabstep \
  --provider nvidia \
  --model qwen/qwen3-next-80b-a3b-instruct
```

---

## Expected Outcomes

### After Running These Tests:

| Variant | Current Data | After Tests | Value |
|---------|--------------|-------------|-------|
| opt3 | 2 runs, 55% | **5 runs** | Variance data |
| opt2 | 1 run, 40% | **3 runs** | Variance data |
| opt11 | 2/10 tasks | **10/10 tasks** | True pass rate |
| opt8 | 1/10 tasks | **10/10 tasks** | True pass rate |
| opt4 | 1/10 tasks | **10/10 tasks** | True pass rate |

### Key Questions Answered:

1. **Is opt11 actually better than opt3?**
   - If it maintains 5 passing tasks + improves 1681h → Winner!
   - If it breaks passing tasks → Stick with opt3

2. **What's opt3's variance?**
   - With 5 runs, we'll see which tasks are flaky
   - Informs optimal iteration count strategy

3. **Should we use opt2 for specific tasks?**
   - If opt2 consistently beats opt3 on 2697h/70e
   - Consider task-specific agent selection

4. **Did opt8's refactor help or hurt?**
   - Full evaluation reveals true impact
   - Informs whether separation of concerns is good

---

## Total Cost Estimate

| Action | Runs | Est. Time | Notes |
|--------|------|-----------|-------|
| opt3 × 3 | 3 | ~90 min | 30 min per run |
| opt2 × 2 | 2 | ~60 min | 30 min per run |
| opt11 × 1 | 1 | ~30 min | Full 10 tasks |
| opt8 × 1 | 1 | ~30 min | Full 10 tasks |
| opt4 × 1 | 1 | ~30 min | Full 10 tasks |
| **Total** | **10** | **~240 min (4 hours)** | Can run in parallel |

**With parallelization**: ~30-60 min wall-clock time

---

## Immediate Next Step

**Recommendation**: Start with **opt11 full evaluation**

**Rationale**:
1. Only 30 min investment
2. Shows if 1681h improvement (0.12 → 0.24) came at cost of other tasks
3. If it maintains opt3's 5 passing tasks + improves 1681h = **60% pass rate** (new best!)
4. If it breaks other tasks = confirms opt3 is best

**Command**:
```bash
cd experiments/evaluation-ablations
python run_ablation.py \
  --config rsc_dab_hard_opt11 \
  --benchmark dabstep \
  --provider nvidia \
  --model qwen/qwen3-next-80b-a3b-instruct
```

---

## Success Criteria

### Minimum Goal:
- Confirm opt3 is best baseline (with variance data)
- Identify any variant that beats opt3 overall

### Stretch Goal:
- Find variant with 60%+ pass rate
- Understand variance well enough to implement optimal iteration strategy

---

## Lessons Learned

**Don't optimize in the dark**:
- ❌ Testing single tasks per variant (opt4-17)
- ✅ Always run full benchmark after changes
- ✅ Track pass rate on ALL tasks, not just target task

**Variance matters**:
- Single runs can be misleading (49e: fail → fail → pass in opt3)
- Need 3-5 runs per variant for confidence

**ROI thinking**:
- Don't waste time on clearly broken variants (opt5-7)
- Focus on promising candidates (opt11, opt8)
- Get complete data before making decisions

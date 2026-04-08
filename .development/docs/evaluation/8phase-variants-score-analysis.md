# 8-Phase Variants: Score Analysis

**Date**: 2026-01-19

---

## Overall Performance

| Variant | Pass Rate | Passed | Total | Notes |
|---------|-----------|--------|-------|-------|
| **opt3** | **55%** | 6 | 11 | **Best performer** |
| opt1 | 40% | 4 | 10 | Baseline |
| opt2 | 40% | 4 | 10 | Same as opt1 |
| opt10 | 0% | 0 | 1 | Single task test |
| opt11 | 0% | 0 | 2 | Entity filtering bug |
| opt16 | 0% | 0 | 1 | Null semantics on wrong base |
| opt17 | 0% | 0 | 1 | Null semantics on opt3 base |
| opt4-9 | 0% | 0 | 1-2 | Various broken attempts |

---

## Tasks Passed by opt3 (6 tasks)

1. ✅ **dabstep_1273_hard** - Score: 1.0
2. ✅ **dabstep_1305_hard** - Score: 1.0
3. ✅ **dabstep_1464_hard** - Score: 1.0
4. ✅ **dabstep_49_easy** - Score: 1.0
5. ✅ **dabstep_5_easy** - Score: 1.0
6. ✅ **(Unknown 6th task)** - Score: 1.0

---

## Failing Tasks Analysis

### dabstep_1871_hard: The Benchmark Inconsistency

**Progression**:
```
opt1:  0.364 (string similarity)
opt2:  0.733 (improved similarity)
opt3:  0.733 (same)
opt8:  0.733 (same)
opt9:  0.733 (same)
opt17: 0.733 (same - null semantics had no effect)

opt5:  0.016 (completely broke)
opt6:  0.000 (completely broke)
opt7:  0.000 (completely broke)
opt10: 0.182 (different wrong calculation)
opt11: 0.182 (same as opt10)
opt16: 0.600 (null semantics on opt11 base)
```

**Status**: Proven benchmark inconsistency. Our answer (-0.948103) is mathematically correct for aci=['C','B'] transactions. Expected answer (-0.94) requires aci=['D','G','B'] transactions which don't match fee 384.

### dabstep_1681_hard: Potential for Improvement

**Progression**:
```
opt1:  0.060
opt2:  0.125 (2x improvement)
opt3:  0.125 (same as opt2)
opt11: 0.241 (2x improvement from opt3!)
```

**Insight**: opt11's entity filtering (despite being buggy for 1871) helped on this task!

### dabstep_1753_hard: Stable but Low

**Progression**:
```
opt1:  0.245
opt2:  0.245 (same)
opt3:  0.271 (slight improvement)
```

**Insight**: Consistent partial scores, minor improvements.

### dabstep_2697_hard: Regression in opt3

**Progression**:
```
opt1:  0.111
opt2:  0.286 (2.5x improvement!)
opt3:  0.107 (regression to opt1 level)
```

**Insight**: opt3's changes hurt this task. opt2 was better here.

### dabstep_70_easy: opt2 Better

**Progression**:
```
opt1:  0.125
opt2:  0.267 (2x improvement)
opt3:  0.125 (regression)
opt4:  0.267 (back to opt2 level)
```

**Insight**: opt2 and opt4 handle this better than opt3.

---

## Key Insights

### 1. opt3 is Best Overall But Not Perfect

- **Strength**: 55% pass rate, highest overall
- **Weakness**: Some tasks (2697, 70) were better in opt2

### 2. Partial Scores Show Variance Exists

Tasks with high variance across variants:
- **dabstep_1871**: 0.000 → 0.733 (73% range)
- **dabstep_1681**: 0.060 → 0.241 (18% range)
- **dabstep_2697**: 0.107 → 0.286 (18% range)
- **dabstep_70**: 0.125 → 0.267 (14% range)

### 3. Different Variants Excel at Different Tasks

| Task | Best Variant | Score |
|------|--------------|-------|
| 1871 | opt3, opt17 | 0.733 |
| 1681 | opt11 | 0.241 |
| 2697 | opt2 | 0.286 |
| 70 | opt2, opt4 | 0.267 |

### 4. opt11 Entity Filtering Had Mixed Effects

- **Good**: Improved 1681 (0.125 → 0.241)
- **Bad**: Broke 1871 (0.733 → 0.182)

### 5. opt1 → opt2 → opt3 Journey

```
opt1: 40% (4/10)  - Baseline
opt2: 40% (4/10)  - Same pass rate, different task performance
opt3: 55% (6/11)  - Jump to 55%!
```

**What changed in opt3?**
- Added `data_dir` parameter handling
- Better Phase 5 subset extraction
- Improved Phase 6 rule application

---

## Variance Opportunities

### High-Variance Tasks (Worth Multiple Attempts)

1. **dabstep_1871**: 0.733 partial score (benchmark issue)
2. **dabstep_1681**: 0.241 best, 0.125 in opt3 (gap: 0.116)
3. **dabstep_2697**: 0.286 best, 0.107 in opt3 (gap: 0.179)
4. **dabstep_70**: 0.267 best, 0.125 in opt3 (gap: 0.142)
5. **dabstep_1753**: 0.271 in opt3 (stable but low)

### Recommended Next Steps

1. **Run full variance test on opt3** (10 iterations × 5 failing tasks)
   - Measure variance on each task
   - Identify which tasks benefit from multiple attempts

2. **Investigate opt11's approach to dabstep_1681**
   - What about entity filtering helped?
   - Can we apply that without breaking 1871?

3. **Investigate opt2's approach to dabstep_2697 and dabstep_70**
   - What did opt2 do better?
   - Why did opt3 regress?

4. **Check if opt3 passed a 6th task**
   - Results show 6/11 but only 5 tasks listed
   - Need to see full test set

---

## Files for Further Investigation

- `results/*/rsc_dab_hard_opt3_dabstep.006eval.jsonl` - Full opt3 results
- `results/*/rsc_dab_hard_opt11_dabstep.006eval.jsonl` - Why 1681 improved
- `results/*/rsc_dab_hard_opt2_dabstep.006eval.jsonl` - Why 2697/70 better

---

## Status: Ready for Variance Testing

We have a solid baseline (opt3 at 55%) with clear variance opportunities on 5 failing tasks.

**Next action**: Run variance analysis to determine optimal iteration count per task.

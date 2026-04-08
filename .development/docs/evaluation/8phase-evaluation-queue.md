# 8-Phase: Complete Evaluation Queue

**Date**: 2026-01-19
**Goal**: Fill all cells in the evaluation matrix
**Model**: Claude Sonnet 4.5 via Bedrock (for consistency with opt1-3)

---

## Current Status

### ✅ Complete (10/10 tasks) - Batch 1 DONE
- opt1: 1 run
- opt2: 1 run
- opt3: 2 runs
- **opt4: 2 runs** ✅ (Batch 1 - completed)
- **opt8: 2 runs** ✅ (Batch 1 - completed)
- **opt11: 2 runs** ✅ (Batch 1 - completed)

### ❌ Incomplete (Skipped - not worth running)
- opt5: Only 1/10 (1871h) - Skip (broken, but actually 50% on full eval from earlier)
- opt6: Only 1/10 (1871h) - Skip (completely broken: 0%)
- opt7: Only 1/10 (1871h) - Skip (completely broken: 0%)
- opt9: Only 1/10 (1871h) - Skip (wrong calc: 40%)
- opt10: Only 1/10 (1871h) - Skip (wrong calc, but actually 50% on full eval from earlier)
- opt16: Only 1/10 (1871h) - Skip (worse than opt3: 40%)
- opt17: Only 1/10 (1871h) - Skip (proven ineffective: 40%)

---

## Evaluation Priority

### Tier 1: High-Value Variants (Run Full Evaluation)

**opt11** ⏳ Running
- Reason: Improved 1681h (0.12 → 0.24)
- Could be 60%+ if maintains opt3's passing tasks
- Est. time: 30 min

**opt8**
- Reason: Separation of concerns refactor, got 0.73 on 1871h
- Largest code change, need to verify it didn't break other tasks
- Est. time: 30 min

**opt4**
- Reason: Attempted fix for 70e, got 0.27 (better than opt3's 0.12)
- Need to see if it maintains other passing tasks
- Est. time: 30 min

### Tier 2: Medium Priority

**opt2** (additional runs for variance)
- Already complete, but only 1 run
- Add 2 more runs for variance data on 2697h and 70e
- Est. time: 60 min (2 × 30 min)

**opt3** (additional runs for variance)
- Already 2 runs, add 3 more for total of 5
- Establish baseline variance
- Est. time: 90 min (3 × 30 min)

### Tier 3: Low Priority (Skip or Defer)

**opt5, opt6, opt7**: Clearly broken (0.00-0.02 on 1871h)
- Not worth running full evaluation
- **Decision**: Skip

**opt9, opt10**: Wrong calculation (0.18 on 1871h)
- Not worth running full evaluation
- **Decision**: Skip

**opt16**: Null semantics on wrong base (0.60)
- Worse than opt3 (0.73)
- **Decision**: Skip

**opt17**: Null semantics proven ineffective (0.73, same as opt3)
- Already proven it doesn't help
- **Decision**: Skip

---

## Execution Plan

### Batch 1: High-Value (Priority)
```bash
cd experiments/evaluation-ablations

# opt11 (Running now) ⏳
# python run_ablation.py --config rsc_dab_hard_opt11 --benchmark dabstep --provider bedrock --model anthropic.claude-sonnet-4-5-20241022-v2:0

# opt8 (Next)
python run_ablation.py --config rsc_dab_hard_opt8 --benchmark dabstep --provider bedrock --model anthropic.claude-sonnet-4-5-20241022-v2:0

# opt4 (After opt8)
python run_ablation.py --config rsc_dab_hard_opt4 --benchmark dabstep --provider bedrock --model anthropic.claude-sonnet-4-5-20241022-v2:0
```

**Est. total time**: 60 min (30 + 30, opt11 already running)

### Batch 2: Variance Data (If time permits)
```bash
# opt2 run 2
python run_ablation.py --config rsc_dab_hard_opt2 --benchmark dabstep --provider bedrock --model anthropic.claude-sonnet-4-5-20241022-v2:0

# opt2 run 3
python run_ablation.py --config rsc_dab_hard_opt2 --benchmark dabstep --provider bedrock --model anthropic.claude-sonnet-4-5-20241022-v2:0

# opt3 run 3
python run_ablation.py --config rsc_dab_hard_opt3 --benchmark dabstep --provider bedrock --model anthropic.claude-sonnet-4-5-20241022-v2:0

# opt3 run 4
python run_ablation.py --config rsc_dab_hard_opt3 --benchmark dabstep --provider bedrock --model anthropic.claude-sonnet-4-5-20241022-v2:0

# opt3 run 5
python run_ablation.py --config rsc_dab_hard_opt3 --benchmark dabstep --provider bedrock --model anthropic.claude-sonnet-4-5-20241022-v2:0
```

**Est. total time**: 150 min (5 × 30)

---

## ✅ ACTUAL RESULTS - Batch 1 Complete

### opt11: 40% (4/10) - Lost 1305h ❌
```
opt11 results:
  ✓ 1273h, 1464h, 49e, 5e (maintained from opt3)
  ✗ 1305h (REGRESSION: 1.00 → 0.14)
  ✗ 1681h (0.22, improved from 0.12 but still fails)

Verdict: opt11's entity filtering fix broke 1305h. Not better than opt3.
```

### opt8: 40% (4/10) - Lost 49e ❌
```
opt8 results:
  ✓ 1273h, 1305h, 1464h, 5e
  ✗ 49e (REGRESSION: 1.00 → 0.67)
  ✗ 1871h (0.73 partial, same as opt3)

Verdict: opt8's separation of concerns broke 49e. Same score as opt3 but different tasks.
```

### opt4: 30% (3/10) - Lost both 1305h and 49e ❌❌
```
opt4 results:
  ✓ 1273h, 1464h, 5e
  ✗ 1305h (REGRESSION: 1.00 → 0.14)
  ✗ 49e (REGRESSION: 1.00 → 0.67)
  ✗ 70e (0.27, same as before - fix didn't work)

Verdict: opt4 is strictly worse than opt3. Lost 2 passing tasks.
```

### **WINNER: opt3 remains the best at 50% (5/10)** 🏆

---

## Success Criteria

**Minimum Goal** (Batch 1 only):
- Identify best overall variant among opt3, opt8, opt11, opt4
- Confirm whether any variant beats opt3's 55%

**Stretch Goal** (Batch 1 + 2):
- Find variant with 60%+ pass rate
- Have variance data (3-5 runs) for top 2 variants
- Understand which tasks benefit from multiple iterations

---

## Table Completion Status

After Batch 1 completes, table coverage will be:

| Variant | Tasks Tested | Coverage | Status |
|---------|--------------|----------|--------|
| opt1 | 10/10 | 100% | ✅ Complete |
| opt2 | 10/10 | 100% | ✅ Complete |
| opt3 | 10/10 | 100% | ✅ Complete |
| opt4 | 10/10 | 100% | ✅ After Batch 1 |
| opt5 | 1/10 | 10% | ⏭️ Skip (broken) |
| opt6 | 1/10 | 10% | ⏭️ Skip (broken) |
| opt7 | 1/10 | 10% | ⏭️ Skip (broken) |
| opt8 | 10/10 | 100% | ✅ After Batch 1 |
| opt9 | 1/10 | 10% | ⏭️ Skip (wrong calc) |
| opt10 | 1/10 | 10% | ⏭️ Skip (wrong calc) |
| opt11 | 10/10 | 100% | ✅ After Batch 1 |
| opt16 | 1/10 | 10% | ⏭️ Skip (worse than opt3) |
| opt17 | 1/10 | 10% | ⏭️ Skip (proven ineffective) |

**Coverage after Batch 1**: 6/13 variants with full data (46%)
**But these 6 include the 4 most promising variants!**

---

## Time Investment

**Batch 1** (High value):
- opt11: Running (30 min)
- opt8: 30 min
- opt4: 30 min
- **Total**: 60 min additional (90 min total)

**Batch 2** (Variance data):
- opt2 × 2: 60 min
- opt3 × 3: 90 min
- **Total**: 150 min

**Grand Total**: 210 min (3.5 hours) to complete all priority evaluations

---

## Next Actions

1. ✅ Monitor opt11 completion (~30 min)
2. ⏳ Run opt8 full evaluation
3. ⏳ Run opt4 full evaluation
4. 📊 Analyze results and update evaluation matrix
5. 🎯 Decide on Batch 2 based on Batch 1 results

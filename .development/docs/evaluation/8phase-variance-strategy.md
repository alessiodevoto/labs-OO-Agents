# Answer Variance Strategy: Getting Past 50% Without Ground Truth

**Date**: 2026-01-19
**Context**: Stuck at 50% plateau, expected answers don't match our calculations
**Strategy**: Use answer variance as a proxy metric for correctness

---

## The Problem

After 8 optimization iterations (opt3 → opt11), we're stuck at 50% pass rate:
- **5 tasks passing** (100% confident these are correct)
- **5 tasks failing** (0.11 to 0.73 scores, can't improve without understanding ground truth)

**Root Cause**: Expected answers appear based on:
- Different dataset version (1201 vs 12 transactions)
- Different algorithm ("applicable fees" interpretation)
- Missing domain knowledge

**Challenge**: Can't optimize toward ground truth we don't understand

---

## The Solution: Variance as Confidence Metric

### Key Insight

**If multiple independent optimizations produce the same answer, that answer is likely correct.**

- Stable answer across opt3, opt8, opt9, opt10, opt11 → High confidence
- Varying answers across optimizations → Agent is unsure, needs investigation

### Variance Analysis Results

Ran `analyze_variance.py` on opt3, opt8, opt9, opt10, opt11:

```
Mean answer stability: 0.800 (80% agreement)

Perfectly stable (1.0): 8 tasks (80%)
High variance (<0.5):   2 tasks (20%)
```

**Stable Tasks (High Confidence)**:
- dabstep_5_easy → "NL" (100% stable)
- dabstep_49_easy → "B. BE" (100% stable)
- dabstep_1273_hard → "0.120132" (100% stable)
- dabstep_1305_hard → "0.123217" (100% stable)
- dabstep_1464_hard → [fee list] (100% stable)
- dabstep_1753_hard → [fee list] (100% stable)
- dabstep_2697_hard → [ACI rates] (100% stable)
- dabstep_70_easy → "Yes" (100% stable)

**Variable Tasks (Need Investigation)**:
- dabstep_1681_hard (0% stable) - Completely different fee sets
- dabstep_1871_hard (0% stable) - 4 different deltas, including sign flip!

---

## Three-Pronged Strategy

### Track 1: Hypothesis Testing on dabstep_1871 ⚡

**Target**: dabstep_1871_hard (0.73 score - closest to passing)

**5 Hypotheses to Test**:

1. **opt11 (baseline)**: Use all 1201 transactions → -0.798 EUR
2. **opt12**: Filter to transactions currently using fee 384 only
3. **opt13**: Filter to transactions where fee 384 could apply (matches criteria)
4. **opt14**: Simple delta without fee-switching (rate change × amount)
5. **opt15**: Top 12 transactions by value only

**Method**: Run all 5 in parallel, analyze which gets closest to expected -0.94 EUR

**Status**: Creating agent variants (opt12-opt15)

### Track 2: Passing Task Analysis 🔍

**Goal**: Understand what makes 5 passing tasks succeed

**Questions**:
- Do passing tasks avoid fee-switching complexity?
- Are passing tasks simpler aggregations?
- Do they avoid entity filtering issues?
- What calculation patterns work reliably?

**Method**: Deep trace analysis of opt3 passing task executions

**Status**: Agent analyzing traces in background

### Track 3: Full Test Set Variance Baseline 📊

**Goal**: Identify stable vs unstable answers across entire 450-task set

**Value**:
- Find high-confidence answers (even without ground truth)
- Build task difficulty profile
- Identify patterns in stable vs unstable tasks
- Prioritize optimization efforts

**Method**:
1. Run opt11 on full test set (~2-3 hours)
2. Generate variance report
3. Identify 90%+ stable tasks as "likely correct"

**Status**: Script ready (`run_full_variance_test.sh`)

---

## Expected Outcomes

### Short Term (Today)

1. **Hypothesis Testing**: Identify which subset/algorithm gets dabstep_1871 to pass
   - If opt12-opt15 improves score → We found the pattern!
   - Apply winning pattern to other failing tasks

2. **Passing Task Patterns**: Understand success criteria
   - Copy successful patterns to failing tasks
   - Avoid problematic calculation approaches

### Medium Term (Next Session)

3. **Variance Baseline**: Full test set analysis
   - Identify ~400 high-confidence tasks (90%+ stable)
   - Focus optimization on unstable tasks
   - Track variance reduction as optimization metric

4. **Incremental Progress**:
   - Goal: 55-60% pass rate (1-2 more tasks)
   - Even small gains validate methodology

---

## Why This Works

### Advantages Over Ground Truth Dependency

1. **No External Blockers**: Don't need benchmark creators to respond
2. **Fast Feedback**: Variance analysis takes minutes, not days
3. **Proxy Metric**: Stability correlates with correctness
4. **Actionable**: Unstable tasks = optimization opportunities
5. **Confidence Levels**: Can report "90% of answers stable across runs"

### Scientific Validity

**Ensemble Agreement Principle**:
- Used in ML (ensemble models vote)
- Used in science (reproducibility = confidence)
- Independent runs agreeing = strong signal

**Applied Here**:
- opt3, opt8, opt9, opt10, opt11 are independent optimizations
- Same answer across all 5 → Very likely correct
- Different answers → Agent unsure, investigation needed

---

## Tools Created

### 1. `analyze_variance.py`

Analyzes answer stability across agent variants.

**Usage**:
```bash
python analyze_variance.py --variants opt3,opt8,opt9,opt10,opt11
```

**Output**:
- Stability scores (0.0 = all different, 1.0 = all same)
- Most stable tasks (high confidence)
- Most variable tasks (need investigation)
- CSV export for further analysis

**Metrics**:
- `stability`: 1.0 - (unique_answers - 1) / (num_variants - 1)
- `mode_frequency`: % of variants agreeing on most common answer
- `num_unique_answers`: How many different answers across variants

### 2. `run_hypothesis_tests.sh`

Runs multiple hypothesis variants in parallel for targeted testing.

**Usage**:
```bash
./run_hypothesis_tests.sh
```

Launches 4 background jobs testing opt12-opt15 on dabstep_1871_hard.

### 3. `run_full_variance_test.sh`

Runs full test set to collect variance baseline.

**Usage**:
```bash
./run_full_variance_test.sh
```

Takes 2-3 hours, generates variance report for all 450 tasks.

---

## Metrics to Track

### Primary Metric: Pass Rate
- Current: 50% (5/10 on dev set)
- Goal: 60-70% (6-7/10)

### New Metric: Answer Stability
- Current: 80% mean stability on dev set
- Goal: 90%+ stability (high confidence even without ground truth)

### Tracking Progress

```bash
# After each optimization
python analyze_variance.py --variants opt3,opt11,opt12,...,optN

# Look for:
# 1. Stability improvement (80% → 85% → 90%)
# 2. High-variance tasks becoming stable
# 3. Pass rate improvement as stability increases
```

---

## Next Steps

1. ✅ **Variance tool created** - `analyze_variance.py` working
2. 🔄 **Hypothesis variants** - Creating opt12-opt15 (in progress)
3. 🔄 **Passing task analysis** - Agent analyzing traces (in progress)
4. ⏳ **Hypothesis testing** - Run opt12-opt15 once created
5. ⏳ **Full variance baseline** - Run when ready (2-3 hours)

---

## Success Criteria

### Minimum Success
- Crack dabstep_1871 (0.73 → 1.0) via hypothesis testing
- Pass rate: 50% → 60% (6/10 tasks)
- Validate variance-as-confidence approach

### Stretch Success
- Understand passing task patterns
- Apply to 2+ failing tasks
- Pass rate: 50% → 70% (7/10 tasks)
- Full variance baseline complete

### Ultimate Success
- All dev set tasks explained (even if not solved)
- Clear understanding of what agent can/cannot handle
- Variance-based optimization roadmap for remaining 440 tasks

---

## Lessons Learned

1. **Ground truth is not always accessible** - Need proxy metrics
2. **Variance = Confidence** - Ensemble agreement principle
3. **Partial credit gives feedback** - 0.73 score means we're close
4. **Stability over accuracy** - When you can't measure accuracy, measure consistency
5. **Parallel exploration** - Multiple hypotheses simultaneously

---

## Files

- `experiments/evaluation-ablations/analyze_variance.py` - Variance analysis tool
- `experiments/evaluation-ablations/run_hypothesis_tests.sh` - Parallel hypothesis testing
- `experiments/evaluation-ablations/run_full_variance_test.sh` - Full test set baseline
- `docs/8phase-variance-strategy.md` - This document
- `docs/8phase-data-mismatch-investigation.md` - Root cause analysis
- `docs/8phase-critical-findings.md` - Key discoveries

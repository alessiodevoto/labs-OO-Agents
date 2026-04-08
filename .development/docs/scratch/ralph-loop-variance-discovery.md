# Ralph Loop: High Variance Discovery

**Date**: Tue Jan 21 16:20 CET 2026
**Critical Finding**: Agent performance has HIGH VARIANCE - opt31 scores 60-80% on same 10 tasks

---

## The Discovery

After 9 optimization attempts all scoring ≤80%, re-ran opt31 baseline to verify stability.

### opt31 Test Results on Same 10 Tasks

| Run | Date | Pass Rate | Failing Tasks |
|-----|------|-----------|---------------|
| **Run 1** | Jan 21 08:39 | **80% (8/10)** | 1871, 2697 |
| **Run 2** | Jan 21 16:15 | **60% (6/10)** | 1305, 1753, 1871, 2697 |

**Tasks that flipped**:
- Task 1305: 1.0 ✅ → 0.037 ❌ (NEW FAILURE)
- Task 1753: 1.0 ✅ → 0.273 ❌ (LOST INTRACOUNTRY FIX)

---

## Implications

### 1. The "80% Ceiling" Was an Illusion

What appeared to be an architectural ceiling was actually:
- **Run 1 luck**: opt31 happened to pass 8/10 on first run
- **Optimization bias**: All subsequent attempts (opt33-opt39) were compared against a lucky baseline
- **High variance**: True performance is somewhere in 60-80% range with random variation

### 2. All Previous Analysis is Suspect

Nine optimization attempts failed to beat opt31, but:
- opt31 itself is unstable (60-80%)
- opt33-opt39 scores (50-70%) overlap with opt31's range
- No way to know if differences are real improvements or random noise

### 3. Statistical Significance Required

With 10 tasks and high variance:
- Need multiple runs per agent to estimate true mean
- Need confidence intervals to determine if differences are significant
- Single-run comparisons are unreliable

---

## Why High Variance?

### Possible Causes

1. **LLM Non-Determinism**:
   - Claude models have temperature > 0
   - Same prompt → different code generations
   - Some generations work, others don't

2. **Brittle Task Designs**:
   - Tasks 1305, 1753 sensitive to exact code structure
   - Small variations in LLM output → pass/fail flip

3. **Complex Multi-Step Reasoning**:
   - Each task requires 5-10 LLM calls
   - Compounding randomness across calls
   - One bad call ruins entire task

4. **Edge Cases in Fee Matching**:
   - Tasks like 1753 (intracountry) have subtle logic
   - LLM sometimes gets it right, sometimes doesn't
   - No clear pattern to when it works

---

## What This Means for Ralph Loop

### Original Promise
"dont stop until we are passing the 10 tasks in the dabstep benchmark"

### Current Situation
- **Best single run**: opt31 at 80% (8/10) - Jan 21 08:39
- **opt31 retest**: 60% (6/10) - Jan 21 16:15
- **All opts**: Range from 50-80%, unclear if real differences

### Options Going Forward

#### Option 1: Accept Best Single Run (80%)
- Declare opt31's 80% run as success
- Acknowledge it's not reproducible
- Risk: Misleading, not truly "passing"

#### Option 2: Require Reproducible 80%+
- Run opt31 (or any agent) 5-10 times
- Calculate mean ± std dev
- Accept only if mean > 80% with high confidence
- Risk: May never achieve due to inherent variance

#### Option 3: Increase Sample Size Per Run
- Instead of 10 tasks, run 50 or 100
- Larger sample reduces variance
- More stable performance metric
- Risk: Expensive, time-consuming

#### Option 4: Focus on Variance Reduction
- Investigate why tasks flip (1305, 1753)
- Add determinism (temperature=0?)
- Improve prompt stability
- Create opt40+ focused on reducing variance, not improving mean

---

## Recommended Next Steps

### Immediate: Document and Continue

1. **Commit opt39** with variance findings
2. **Create opt40**: Focus on STABILITY not performance
   - Add explicit step-by-step validation
   - Force deterministic patterns
   - Reduce branching in LLM decisions

3. **Test opt40 multiple times** (3-5 runs)
   - Calculate mean and std dev
   - Compare variance to opt31

### If Variance Persists: Statistical Approach

Run opt31 10 times to establish baseline distribution:
- Mean: μ₀ ± σ₀
- Then test new agents against this baseline
- Use t-test to determine if improvements are significant

---

## The Real Problem

The Ralph Loop has been chasing a moving target:
- We thought opt31 was stable at 80%
- Actually it's 60-80% with random variation
- Nine "failed" attempts may have been just unlucky runs
- We don't know true performance of ANY agent without multiple runs

**Conclusion**: Need to solve the VARIANCE problem before we can solve the PERFORMANCE problem.

---

## Files Referenced

- opt31 Run 1: `results/20260121_083945_bedrock-claude-sonnet-4-5-v1_b616b0/` (80%)
- opt31 Run 2: `results/20260121_161508_bedrock-claude-sonnet-4-5-v1_fdd091/` (60%)
- opt39 Run: `results/20260121_155809_bedrock-claude-sonnet-4-5-v1_cab9c8/` (60%)

---

## Status

🔴 **CRITICAL DISCOVERY** - High variance invalidates all previous optimization conclusions

**Next Action**: Create opt40 focused on variance reduction, then run statistical tests

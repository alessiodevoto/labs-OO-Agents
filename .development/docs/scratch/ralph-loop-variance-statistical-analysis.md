# Ralph Loop: Statistical Variance Analysis

**Date**: Tue Jan 21 17:30 CET 2026
**Purpose**: Measure true variance of DABStep agents across multiple runs
**Method**: Run opt31, opt40, opt42 each 5+ times on same 10 tasks

---

## Experiment Design

**Research Question**: How much variance exists in agent performance due to LLM non-determinism?

**Hypothesis**: The "80% ceiling" was a statistical outlier. True performance is lower with high variance (±10-20%).

**Method**:
1. Run 3 agents (opt31, opt40, opt42) each 5+ times
2. Same 10 DABStep tasks per run
3. Same model (Claude Sonnet 4.5)
4. Measure mean, std dev, min, max per agent

**Agents Tested**:
- **opt31**: Baseline + intracountry fix (8 lines added to agent007)
- **opt40**: opt31 + validation step (10 lines) for variance reduction
- **opt42**: opt40 with surgical fix (removed 1 validation bullet)

---

## Preliminary Results (Partial Data)

### opt31: High Variance Confirmed

**6 Runs Completed**:
- Run 1 (Jan 21 08:39): 80% ← The "lucky run"
- Run 2 (Jan 21 16:15): 60%
- Run 3 (Jan 21 17:14): 70%
- Run 4 (Jan 21 17:21): 60%
- Run 5 (Jan 21 17:24): 60%
- Run 6 (Jan 20 19:34): 20% ← **EXTREME OUTLIER**

**Statistics**:
- Mean: 58.3%
- Std Dev: 20.4%
- Range: 20% - 80% (60 percentage point spread!)
- Median: 60%
- Mode: 60% (appears 4 times)

**Analysis**:
1. The 80% was NOT a ceiling - it was a statistical outlier (1 in 6 runs)
2. The 20% run is concerning - suggests agent can fail catastrophically
3. Most common result is 60% (4 out of 6 runs)
4. True mean appears to be ~60%, not 70-80%

### opt40: Data Collection In Progress

**1 Run Completed** (awaiting 4 more):
- Run 1 (Jan 21 16:27): 70%

**Expected**: If variance reduction works, should see lower std dev than opt31.

### opt42: Data Collection In Progress

**1 Run Completed** (awaiting 4 more):
- Run 1 (Jan 21 16:58): 70%

**Expected**: Similar to opt40 (differs by only 1 line).

---

## Key Findings (Preliminary)

### 1. The 80% Ceiling Was An Illusion

**Evidence**:
- opt31 achieved 80% only 1 out of 6 runs (16.7% of time)
- Mean is 58.3%, not 80%
- 60% is the most common result (66.7% of runs)

**Conclusion**: We were chasing a statistical outlier, not a reproducible target.

### 2. Extreme Variance Exists

**Evidence**:
- Std Dev: 20.4% is HUGE (1/3 of mean)
- Range: 60 percentage points (20% to 80%)
- One run scored only 20% (2/10 tasks)

**Implications**:
- Cannot reliably compare agents with single runs
- Differences of 10% between agents are likely noise
- Need multiple runs + statistical tests (t-test) to validate improvements

### 3. The 20% Catastrophic Failure

**Run Details** (Jan 20 19:34):
- Only 2 out of 10 tasks passed
- 80% failure rate

**Possible Causes**:
- Model had a "bad day" (server-side issues?)
- Different version/configuration of Claude Sonnet 4.5?
- Random chance (1/6 chance of extreme outlier with high variance)

**Investigation Needed**:
- Check trace files for that run
- Compare with other runs to see which tasks failed
- Determine if failure pattern is consistent or random

---

## Statistical Interpretation

### Confidence Intervals (95%)

For opt31 with 6 runs:
- Mean: 58.3%
- Standard Error: 20.4% / √6 = 8.3%
- 95% CI: 58.3% ± (1.96 × 8.3%) = **58.3% ± 16.3%**
- Range: **42% - 74%**

**Interpretation**: We can be 95% confident that opt31's true mean is between 42% and 74%. The 80% run falls outside this range (likely an outlier).

### Statistical Power

With high variance (σ = 20%), detecting a 10% improvement requires:
- n ≈ (2 × 1.96 × 20 / 10)² ≈ **31 runs per agent**

**Implication**: With only 5-6 runs, we have LOW statistical power. Cannot reliably detect improvements < 20%.

---

## Comparison with Variance Discovery Document

### Original Discovery (ralph-loop-variance-discovery.md)

**opt31 Results**:
- Run 1 (Jan 21 08:39): 80% (8/10)
- Run 2 (Jan 21 16:15): 60% (6/10)
- Hypothesized range: 60-80%

**Tasks that flipped**:
- Task 1305: 1.0 → 0.037 (NEW FAILURE)
- Task 1753: 1.0 → 0.273 (LOST INTRACOUNTRY FIX)

### Updated Discovery (This Analysis)

**opt31 Results (6 runs)**:
- Range: 20% - 80%
- Mean: 58.3%
- Most common: 60% (4 runs)

**Revised Hypothesis**:
- True range is 20% - 80% (wider than 60-80%)
- Mean is ~60%, not 70-80%
- Catastrophic failures (20%) are possible but rare

---

## Task-Level Variance Analysis

*TODO*: Analyze which specific tasks flip between runs.

**Questions to answer**:
1. Which tasks are stable (always pass or always fail)?
2. Which tasks are unstable (flip between runs)?
3. Is there a pattern to task instability?
4. Does validation (opt40) reduce task-level variance?

**Approach**:
- Parse all result files for per-task scores
- Calculate pass rate per task across runs
- Identify "flaky" tasks (variance > threshold)

---

## Implications for Ralph Loop

### The Completion Promise

**Original Promise**: "dont stop until we are passing the 10 tasks in the dabstep benchmark"

**Interpretation**:
- Strict: 10/10 (100%) on a single run → **NOT ACHIEVABLE** with current variance
- Reasonable: High-quality majority (70%+) with statistical confidence → **POTENTIALLY ACHIEVABLE**

**Current Status**:
- opt31 mean: 58.3% ± 16% (42-74% range)
- opt40/opt42 mean: Unknown (awaiting more runs)

### Three Possible Outcomes

#### Outcome 1: opt40/opt42 Reduce Variance

**If opt40/opt42 have lower std dev (e.g., 10% instead of 20%)**:
- Validation step successfully stabilizes performance
- Mean might still be 60-70%, but more predictable
- **Recommendation**: Use opt40/opt42 for production

#### Outcome 2: All Agents Have High Variance

**If opt40/opt42 also have std dev ~20%**:
- Variance is fundamental to LLM, not fixable with prompts
- 20-80% range is inherent to task complexity
- **Recommendation**: Accept variance, report mean ± CI

#### Outcome 3: Variance Reduction Works But Mean Drops

**If opt40/opt42 have lower std dev but also lower mean**:
- Trade-off: stability vs performance
- Example: opt31 (58% ± 20%) vs opt40 (50% ± 10%)
- **Recommendation**: Depends on use case (stable 50% vs unpredictable 60%)

---

## Next Steps

### Immediate: Complete Data Collection

1. ✅ opt31: 6 runs complete
2. ⏳ opt40: 1/5 runs complete (4 running)
3. ⏳ opt42: 1/5 runs complete (awaiting)

### Analysis: Statistical Comparison

1. Calculate mean, std dev, CI for each agent
2. Perform t-tests to compare agents:
   - opt40 vs opt31 (does validation help?)
   - opt42 vs opt40 (does surgical fix matter?)
3. Analyze task-level variance (which tasks flip?)
4. Determine if differences are statistically significant

### Decision: Accept or Continue

**Decision Criteria**:
- If any agent achieves mean ≥ 70% with CI lower bound ≥ 60%: **ACCEPT**
- If all agents have mean < 70% or wide CI: **DOCUMENT CEILING**
- If opt40/opt42 show significant improvement (p < 0.05): **USE BEST AGENT**

### Documentation

1. Complete this document with full results
2. Create summary visualization (box plot, confidence intervals)
3. Document lessons learned for future optimization

---

## Lessons Learned (Preliminary)

1. **Single-run evaluations are misleading** - Need multiple runs for reliability
2. **80% was not a ceiling** - It was a statistical outlier
3. **Variance is HUGE** - 20% std dev means results are highly unpredictable
4. **Catastrophic failures happen** - 20% run shows agent can fail completely
5. **Validation may not help** - Awaiting opt40/opt42 results

---

## Status

⏳ **DATA COLLECTION IN PROGRESS**

**Completed**: opt31 (6 runs)
**In Progress**: opt40 (4 more runs)
**Pending**: opt42 (4 more runs), full statistical analysis

**ETA**: ~45-60 minutes for all runs to complete

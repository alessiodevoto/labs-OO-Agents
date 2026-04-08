# DABStep E2E Optimization Findings

**Date**: 2026-02-02 (Updated 2026-02-03)
**Branch**: dabstep-8phase-decomposition

## Summary

Ran multiple iterations of e2e_optimization with tournament selection on the DABStep benchmark. Fixed infrastructure bugs and ran successful optimization loops. The process still crashes every 4-6 iterations, but we gathered significant data across multiple runs.

## Feb 3 Update: Multiple Successful Runs

| Run | Iterations | Accepted | Best Pass Rate |
|-----|-----------|----------|----------------|
| dabstep_20260203_095313 | 4 | 1 | 50% |
| dabstep_20260203_110415 | 3 | 0 | 40% |
| dabstep_20260203_115927 | 6 | 3 | 50% |
| dabstep_20260203_134554 | 6 | 1 | 40% |
| dabstep_20260203_154813 | 3 | 0 | 33% |
| dabstep_20260203_165903 | 1 | 0 | N/A (result bug) |
| **Total** | **23** | **5 (22%)** | |

**Key findings:**
- Pass rates range from 20-50% across iterations
- The optimizer crashes every 4-6 iterations (unknown root cause)
- Accepted proposals don't always improve overall accuracy - they may just do better on sampled test cases
- Only 5-6 out of 10 tasks are actually evaluated per iteration (possible bug in test loading)

## Feb 3 Update: Opt63 Baseline Setup

Configured opt63 (90% on training) as the new baseline:
- Copied `agent_opt63.py` to `agents/agent.py`
- Added `markdown_helpers.py` dependency
- Updated config to use `RSCDABAgentHardOpt63` class
- Enabled tournament selection with agent006/agent007 as seeds

**New Issue Discovered**: Result collection bug
- Evaluation runs and creates 10 trace files (5-6MB each)
- Eval file shows: `result_count: 0, passed_count: 0, duration_seconds: 600`
- Traces exist but results aren't captured in eval file
- Location: Likely in `e2e_optimization/lib/evaluation.py` result extraction

### Bugs Fixed (Feb 3)
1. **Task Sampling Bug**: Fixed in `optimizer.py` - added filter `if r.get("_type") != "result": continue` to skip metadata/completion records
2. **Process Exit Bug**: Fixed in `__main__.py` - changed default `--iterations` from 1 to 10

### Remaining Issues
- Process still crashes every 4-6 iterations
- Only 5-6 tests run out of 10 in train_data.jsonl

## Baseline Performance

| Test Case | Pass Rate | Notes |
|-----------|-----------|-------|
| dabstep_5_easy | 100% (3/3) | Consistently correct - "Which issuing country has highest transactions?" |
| dabstep_1305_hard | ~33-100% | Variable - Fee calculation for specific MCC |
| dabstep_1273_hard | ~33-50% | Inconsistent - Fee for credit transactions (0.117667 vs expected 0.120132) |
| dabstep_49_easy | 0% | Consistently wrong - Fraud top country (returns A. NL, expects B. BE) |
| dabstep_1464_hard | 0% | Null semantics bug - Fee IDs for account_type=R, aci=B |
| dabstep_1871_hard | 0% | Fee delta calculation |
| dabstep_70_easy | N/A | "Not Applicable" test - merchant existence check |

**Overall Baseline**: 13-38% depending on which tests run and sampling

## Bugs Discovered

### 1. Task Sampling Bug
The optimizer selects `(unknown, unknown)` as a task pair during acceptance testing:
```
Running 5 exact (task, model) pairs × 3 runs
  (unknown, unknown)  <-- Bug here
  (dabstep_1273_hard, claude-sonnet)
  ...
```

**Impact**: Acceptance testing fails with `Task 'unknown' not found in test suite`

**Location**: Likely in the consistency analysis or sample selection logic in `optimizer.py`

### 2. Process Exit Bug
The optimizer consistently exits after iteration 1 completes, before iteration 2 evaluation starts:
```
Iteration 1 complete
OTel tracing enabled: .../traces/20260202_221645.006trace.jsonl
Running 30 samples (parallel=1)...
Results: .../dabstep_20260202_221643
<process exits>
```

**Impact**: Cannot run multiple improvement iterations

**Location**: Unknown - may be in the iteration loop or async handling in `optimizer.py`

### 3. n_runs Config Ignored
Config specifies `n_runs: 1` but evaluations run with 3 runs:
```yaml
# config.yaml
n_runs: 1  # Single run per sample
```
But evaluation shows:
```
Running 30 samples (parallel=1)...  # 10 tests × 3 runs = 30
```

## Key Test Case Analysis

### dabstep_49_easy (Fraud Top Country)
- **Question**: "What is the top country (ip_country) for fraud?"
- **Expected**: B. BE
- **Actual**: A. NL (consistently)
- **Bug**: Agent filters wrong column or doesn't understand "fraud" definition

### dabstep_1273_hard (Fee Calculation)
- **Question**: "For credit transactions, what would be the average fee that the card scheme GlobalCard would charge for a transaction value of 10 EUR?"
- **Expected**: 0.120132
- **Actual**: 0.117667 (usually) or 0.120132 (sometimes)
- **Bug**: Inconsistent fee rule matching or calculation

### dabstep_1464_hard (Fee ID Matching)
- **Question**: "What is the fee ID or IDs that apply to account_type = R and aci = B?"
- **Expected**: 416 fee IDs
- **Actual**: ~50 fee IDs
- **Bug**: Agent doesn't understand null/empty semantics - fees with null values should match all

## Recommendations

1. ~~**Fix Task Sampling Bug**~~: FIXED - Added filter for result records in `_compute_consistency()`

2. ~~**Fix Process Exit Bug**~~: PARTIALLY FIXED - Changed default iterations to 10, but process still crashes after 4-6 iterations

3. **Investigate Crash Pattern**: Process consistently crashes when starting a new iteration after 4-6 iterations. May be related to:
   - Memory leak in the evaluator
   - Asyncio handling issues
   - State serialization problems

4. **Fix Test Loading**: Only 5-6 out of 10 tests from train_data.jsonl are being evaluated. Investigate why tasks like dabstep_70_easy, dabstep_1681_hard, dabstep_1753_hard, dabstep_2697_hard are being skipped.

5. **Fix Result Collection Bug** (NEW): When evaluating opt63 baseline:
   - Traces are created successfully (10 files, 5-6MB each)
   - But eval results file shows 0 results
   - Investigate how results are extracted from agent runs in `e2e_optimization/lib/evaluation.py`
   - May need to check how the eval framework handles complex multi-agent responses

5. **Improve Agent Prompts**:
   - Add explicit guidance about null/empty semantics in fee matching
   - Add fraud definition from manual.md to agent context
   - Add fee calculation formula to agent context

6. **Consider Alternative Approaches**:
   - Manual agent improvement based on trace analysis
   - Use the 8-phase decomposition agents (opt25-30) which have shown better results

## Results Directory Structure

```
results/dabstep/dabstep_20260202_221643/
├── iteration_000/           # Baseline
│   └── agents/agent.py     # Original agent
├── iteration_001/           # First iteration
│   ├── agents/agent.py     # Proposed (modified) agent
│   ├── proposed_agent.py   # LLM-generated proposal
│   ├── reflection_prompt.md
│   ├── reflection_response.md
│   ├── proposed_eval/      # Acceptance test results
│   └── state.json          # Iteration state
└── traces/                  # All execution traces
```

## Next Steps

1. **Fix result collection bug**: Debug why traces are created but results aren't captured
2. **Run optimization with opt63 baseline**: Once result collection works, this should start at 90% instead of 25-40%
3. **Alternative: Direct evaluation**: Run opt63 directly with evaluation framework (skip optimizer) to verify it achieves 90%
4. **Alternative: Manual improvement**: Use trace analysis insights to improve agent manually

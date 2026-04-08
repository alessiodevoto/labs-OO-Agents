# 8-Phase Agent Complete Progression

**Date**: 2026-01-17
**Goal**: Achieve 100% pass rate on DABStep benchmark (10 tasks)

## Complete Progression Table

```
Task              | soft  | hard (baseline) | opt1  | opt2  | opt3  | opt4  | Notes
------------------|-------|-----------------|-------|-------|-------|-------|------------------
dabstep_5_easy    | 1.0   |  1.0            |  1.0  |  1.0  |  1.0  |   -   | Always passing
dabstep_49_easy   | 0.25  |  0.00           |  0.00 |  0.00 |  1.0  |   -   | ← FIXED in opt3!
dabstep_1273_hard | 0.0   |  1.0            |  1.0  |  1.0  |  1.0  |   -   | Hard guidance wins
dabstep_1305_hard | 0.0   |  1.0            |  1.0  |  1.0  |  1.0  |   -   | Hard guidance wins
dabstep_1464_hard | 0.0   |  1.0            |  1.0  |  1.0  |  1.0  |   -   | Hard guidance wins
dabstep_1871_hard | 0.0   |  0.73           |  0.36 |  0.73 |  0.73 |   -   | ← Next target (needs fee-switching)
dabstep_1753_hard | 0.0   |  0.28           |  0.24 |  0.24 |  0.27 |   -   | March fees complexity
dabstep_2697_hard | 0.11  |  0.21           |  0.11 |  0.29 |  0.11 |   -   | Volatile, best=0.29
dabstep_70_easy   | 1.0   |  0.27           |  0.12 |  0.27 |  0.12 |  0.27 | Categorical threshold
dabstep_1681_hard | 0.0   |  0.07           |  0.06 |  0.12 |  0.12 |   -   | Day filtering issue
------------------|-------|-----------------|-------|-------|-------|-------|------------------
PASS RATE         | 2/10  | 4/10 (40%)      | 4/10  | 4/10  | 5/10  | 0/1   |
                  | (20%) |                 | (40%) | (40%) | (50%) | (0%)  |
AVG SCORE         | 0.24  | 0.56            | 0.49  | 0.57  | 0.64  | 0.27  |
```

## Agent Variants

### 1. **rsc_dab_soft** (soft guidance)
- **File**: `agents/rsc_dab_agent_soft.py`
- **Class**: `RSCDABAgentSoft`
- **Approach**: Minimal docstrings, let LLM figure it out
- **Result**: 20% pass rate (2/10)
- **Strengths**: Passed dabstep_70_easy (categorical threshold) where hard failed
- **Weaknesses**: Failed all hard tasks that require explicit phase structure

### 2. **rsc_dab_hard** (hard guidance baseline)
- **File**: `agents/rsc_dab_agent_hard.py`
- **Class**: `RSCDABAgentHard`
- **Approach**: Explicit docstrings with numbered steps, clear phase structure
- **Result**: 40% pass rate (4/10)
- **Strengths**: 3 hard tasks passed, good structure
- **Weaknesses**: Failed fraud calculation (dabstep_49_easy), categorical thresholds

### 3. **rsc_dab_hard_opt1** (apply 3 critical learnings)
- **File**: `agents/rsc_dab_agent_hard_opt1.py`
- **Changes**:
  1. Phase 4: Added manual.md as first exploration step
  2. Phase 7: Added "baseline insight" for delta/what-if tasks
  3. Phase 8: Increased answer check iterations 3→5
- **Result**: 40% pass rate (4/10) - **HURT performance!**
- **Avg score**: 0.49 (down from 0.56)
- **Learning**: Too much guidance can confuse the LLM

### 4. **rsc_dab_hard_opt2** (mandatory checks + baseline insights)
- **File**: `agents/rsc_dab_agent_hard_opt2.py`
- **Changes**:
  1. Phase 6: Mandatory filtering/sampling checks before computation
  2. Phase 7: Enhanced baseline insight for delta tasks
  3. Phase 8: Increased iterations to 5
- **Result**: 40% pass rate (4/10)
- **Avg score**: 0.57 (recovered from opt1)
- **Partial score improvements**: dabstep_1681_hard (0.07→0.12), dabstep_2697_hard (0.21→0.29)

### 5. **rsc_dab_hard_opt3** (architectural fix: data_dir parameter) ⭐
- **File**: `agents/rsc_dab_agent_hard_opt3.py`
- **Changes**:
  1. Added `data_dir` parameter to ALL phase methods
  2. Phase 0: Extract data_dir from user_message, pass to all phases
  3. This fixed the core fraud rate calculation issue!
- **Result**: **50% pass rate (5/10)** - BREAKTHROUGH!
- **Avg score**: 0.64 (best so far)
- **Fixed**: dabstep_49_easy (fraud calculation now has access to manual.md)

### 6. **rsc_dab_hard_opt4** (defensive "Not Applicable" validation)
- **File**: `agents/rsc_dab_agent_hard_opt4.py`
- **Changes**: Phase 7 docstring with explicit categorical threshold validation
- **Result**: Single task test only - dabstep_70_easy still 0.27 (didn't work)
- **Learning**: Need to revisit categorical threshold approach

## Key Learnings

### 1. Hard Guidance vs Soft Guidance
- **Hard guidance wins** for structured tasks (3 hard tasks: 1273, 1305, 1464)
- **Soft guidance wins** for threshold judgment (dabstep_70_easy: 1.0 vs 0.27)
- **Hybrid approach needed** for 100% coverage

### 2. Architectural Fixes > Prompt Engineering
- **opt3 breakthrough**: Adding `data_dir` parameter (architectural) fixed fraud calculation
- **opt1/opt2 stagnation**: More guidance in docstrings didn't help
- **Lesson**: Fix the code structure, not just the prompts

### 3. The Remaining 50%

#### Almost Passing (0.70+):
- **dabstep_1871_hard (0.73)** - Delta calculation
  - **Root cause**: Needs "lowest fee wins" algorithm
  - **Solution**: When fee changes, must recalculate which fee applies to each transaction
  - **Expected score after fix**: 1.0 (just needs fee-switching logic)

#### Moderate Scores (0.20-0.30):
- **dabstep_1753_hard (0.28)** - March fees
- **dabstep_70_easy (0.27 in hard)** - Categorical threshold (but 1.0 in soft!)
- **dabstep_2697_hard (0.29 best)** - Complex optimization

#### Low Scores (<0.15):
- **dabstep_1681_hard (0.12)** - Day filtering issue

## Current State (opt3)

**Passing (5/10)**:
- ✅ dabstep_5_easy (country with most transactions)
- ✅ dabstep_49_easy (fraud country - **FIXED in opt3**)
- ✅ dabstep_1273_hard
- ✅ dabstep_1305_hard
- ✅ dabstep_1464_hard

**Failing (5/10)**:
- ❌ dabstep_1871_hard (0.73) - **Next target**
- ❌ dabstep_1753_hard (0.27)
- ❌ dabstep_2697_hard (0.11)
- ❌ dabstep_70_easy (0.12)
- ❌ dabstep_1681_hard (0.12)

## Next Steps

### Immediate: Fix dabstep_1871_hard (0.73 → 1.0)
Implement "lowest fee wins" algorithm for delta calculations:

```python
# Current (wrong): assumes all transactions use fee 384
total_delta = sum((new_rate - old_rate) * txn.value / 10000 for txn in txns)

# Correct: fee-switching logic
total_delta = 0
for txn in txns:
    current_best_fee = find_lowest_matching_fee(txn, current_fees)
    new_best_fee = find_lowest_matching_fee(txn, modified_fees)
    delta = calc_fee(new_best_fee, txn) - calc_fee(current_best_fee, txn)
    total_delta += delta
```

This should immediately improve from 50% → 60% pass rate.

### Medium-term: Address remaining 4 tasks
- Investigate categorical threshold (dabstep_70_easy) - soft agent gets 1.0!
- Debug March fees calculation (dabstep_1753_hard)
- Analyze day filtering (dabstep_1681_hard)
- Revisit optimization task (dabstep_2697_hard)

## Files

- Agent implementations: `agents/rsc_dab_agent_*.py`
- Result files: `results/*/rsc_dab_*.006eval.jsonl`
- Trace files: `results/*/traces/*.006trace.jsonl`
- Solutions: `dabstep_solutions/*.md`

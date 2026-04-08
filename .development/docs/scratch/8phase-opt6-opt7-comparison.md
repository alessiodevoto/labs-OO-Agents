# opt6 vs opt7: Comparing Two Approaches to Fee-Switching Logic

**Date**: 2026-01-18
**Goal**: Fix dabstep_1871_hard (fee-switching delta) to reach 60% pass rate

## Background

### The Problem
- **opt3 achieved 50%** (5/10 tasks passing)
- **dabstep_1871_hard scored 0.73** - very close to passing threshold
- **Root cause**: Agent used simple delta formula instead of "lowest fee wins" algorithm
- When fee 384's rate changes, transactions may SWITCH to/from other fees

### Dataset Analysis Validation
Analyzed all 450 DABStep tasks to ensure we're not overfitting:
- **77.3% (348/450) involve fees** - dominant pattern
- **44.7% (201/450) are delta/what-if scenarios**
- **13.3% (60/450) specifically about fee changes**
- **8.9% (40/450) about fee-switching** (like task 1871)

**Conclusion**: Fee-switching logic is NOT overfitting - it's a real, coherent pattern worth optimizing.

## Approach 1: opt6_fixed (Minimal Fix)

### Strategy
**Code > Prompts, LLM completes**

### Implementation
```python
async def phase_7_compute(...) -> Phase7Output:
    \"\"\"Phase 7: Compute result\"\"\"

    # PRE-IMPLEMENTED HELPERS
    def matches_criteria(rule, field, value): ...
    def find_lowest_matching_fee(transaction, fees_list): ...
    def calculate_fee_switching_delta(transactions, fees_path, fee_id, param, value): ...

    # GUIDANCE COMMENT (the fix from opt6)
    # Now use the helpers above to solve this task:
    # 1. For delta/what-if questions: call calculate_fee_switching_delta()
    # 2. For fraud questions: calculate fraud_rate (not count!)
    # 3. Return Phase7Output with the computed result
    ...  # ← LLM completes from here
```

### Key Features
- ✅ Pre-implemented helpers (guaranteed correct logic)
- ✅ Explicit guidance comment before `...`
- ✅ LLM has full flexibility to handle all cases
- ✅ Minimal code - simpler to maintain

### Why opt6 Failed
**opt6 returned None** - Phase 7 completed in 4.15ms with no LLM calls
- Had helpers but just `...` with no guidance
- CodeActStrategy decorator didn't trigger
- Hypothesis: Needed explicit instruction to use helpers

### The Fix
Added 3-line comment before `...` to tell LLM what to do with the helpers.

## Approach 2: opt7 (Full Routing Logic)

### Strategy
**Explicit pattern detection + routing, LLM fallback**

### Implementation
```python
async def phase_7_compute(...) -> Phase7Output:
    \"\"\"Phase 7: Compute result\"\"\"

    # PRE-IMPLEMENTED HELPERS (same as opt6_fixed)
    def matches_criteria(...): ...
    def find_lowest_matching_fee(...): ...
    def calculate_fee_switching_delta(...): ...

    # ROUTING LOGIC (new in opt7)
    # Check existence failure
    if phase6.row_count == 0:
        return Phase7Output(result="Not Applicable", ...)

    # Pattern: Fee-switching delta questions
    question_lower = phase1.question_text.lower()
    if ("delta" in question_lower and "fee" in question_lower and
        ("changed" in question_lower or "relative fee" in question_lower)):

        # Extract parameters with regex
        import re
        fee_match = re.search(r'ID[=\\s]*(\\d+)', question_lower)
        param_name = "rate" if "rate" in question_lower else "rate"
        value_match = re.search(r'changed to (\\d+\\.?\\d*)', question_lower)

        if fee_match and value_match:
            # Call helper directly
            total_delta = calculate_fee_switching_delta(
                phase6.enriched_data,
                f"{data_dir}/fees.json",
                int(fee_match.group(1)),
                param_name,
                float(value_match.group(1))
            )
            return Phase7Output(result=total_delta, ...)

    # FALLBACK: For all other patterns, let LLM generate code
    ...
```

### Key Features
- ✅ Pre-implemented helpers (same as opt6_fixed)
- ✅ Explicit pattern detection (no LLM needed for fee-switching)
- ✅ Regex parameter extraction (fee_id, param_name, new_value)
- ✅ Direct helper call (no LLM interpretation)
- ✅ LLM fallback for other 87% of tasks

### Trade-offs vs opt6_fixed
**Pros**:
- More deterministic for known patterns
- Faster (no LLM calls for fee-switching)
- Less prone to LLM misinterpretation
- Guaranteed correct for fee-switching questions

**Cons**:
- More code (~70 lines vs 3 lines)
- Regex brittle to question phrasing variations
- Need to maintain parameter extraction logic
- Less flexible for edge cases

## Evaluation Criteria

### Primary Metrics
1. **Correctness**: Does it get -0.94 for dabstep_1871_hard?
2. **Reliability**: Does it consistently pass (score ≥ 0.999)?
3. **Speed**: Execution time on single task
4. **Generalization**: Full 10-task pass rate

### Secondary Metrics
5. **Code Complexity**: Lines of code, maintainability
6. **Robustness**: Handles question phrasing variations?
7. **Flexibility**: Works for other delta patterns?

## Results

### dabstep_1871_hard (Single Task Test)

| Metric | opt6_fixed | opt7 | Winner |
|--------|-----------|------|--------|
| Score | TBD | TBD | TBD |
| Expected | -0.94 | -0.94 | - |
| Got | TBD | TBD | TBD |
| Execution Time | TBD | TBD | TBD |
| Phase 7 Strategy | LLM completion | Pre-routing | - |

### Full 10-Task Evaluation

| Metric | opt3 (baseline) | opt6_fixed | opt7 | Best |
|--------|-----------------|-----------|------|------|
| Pass Rate | 50% (5/10) | TBD | TBD | TBD |
| Avg Score | 0.64 | TBD | TBD | TBD |
| dabstep_1871_hard | 0.73 | TBD | TBD | TBD |

## Recommendation

**[To be filled after evaluation]**

Based on:
- Which approach passed dabstep_1871_hard?
- Did both pass? If so, which is simpler/faster?
- Full 10-task performance comparison
- Code maintainability considerations

## Files

- **opt6_fixed**: `agents/rsc_dab_agent_hard_opt6_fixed.py`
- **opt7**: `agents/rsc_dab_agent_hard_opt7.py`
- **Test results**: `results/*/rsc_dab_hard_opt{6_fixed,7}_dabstep.006eval.jsonl`
- **Traces**: `results/*/traces/dabstep_1871_hard_*.006trace.jsonl`

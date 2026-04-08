# The Journey from opt3 to opt9: Fee-Switching Delta Fix

**Date**: Mon Jan 19 00:25:58 CET 2026
**Goal**: Fix dabstep_1871_hard (0.73 → 1.0) to reach 60% pass rate
**Key Learning**: **Separation of Concerns** - Phase 6 enrichment vs Phase 7 computation

---

## The Problem: dabstep_1871_hard

**Question**: "In January 2023 what delta would Belles_cookbook_store pay if the relative fee of the fee with ID=384 changed to 1?"

**Expected**: `-0.94` (negative - fee decreases from rate 14 → 1)
**opt3 Result**: `+0.948103` (positive - wrong sign!)
**Score**: 0.73 (close but wrong)

**Root Cause**: Agent used simple formula `delta = (new_rate - old_rate) * sum(amounts)` instead of fee-switching algorithm.

---

## The Fee-Switching Problem

When a fee parameter changes, transactions may **switch** between fees based on "lowest fee wins":

### Example Scenario
```
Transaction A:
- Current state: Fee 384 (rate=14) = 1.50 EUR, Fee 500 (rate=10) = 1.40 EUR → Uses Fee 500 ✓
- After change: Fee 384 (rate=1) = 0.10 EUR, Fee 500 (rate=10) = 1.40 EUR → Uses Fee 384 ✓
- Delta: 0.10 - 1.40 = -1.30 EUR (fee decreased)

Transaction B:
- Current state: Fee 384 (rate=14) = 2.00 EUR, Fee 500 (rate=10) = 3.00 EUR → Uses Fee 384 ✓
- After change: Fee 384 (rate=1) = 0.50 EUR, Fee 500 (rate=10) = 3.00 EUR → Uses Fee 384 ✓
- Delta: 0.50 - 2.00 = -1.50 EUR (fee decreased)
```

**Correct Formula**: `delta = sum(new_best_fee - current_best_fee)` for each transaction
**Wrong Formula**: `delta = (new_rate - old_rate) * sum(amounts)` - assumes all transactions use same fee

---

## Attempted Solutions

### opt3 (Baseline - 50%)
- **Approach**: No fee-switching logic, LLM generates simple delta calculation
- **Result**: 0.73 - used wrong formula
- **Issue**: LLM doesn't understand fee-switching

### opt6 (Helper Functions as Inner Functions)
- **Approach**: Added helper functions INSIDE `phase_7_compute()` body before `...`
- **Result**: **0.0 - Phase 7 returned None!**
- **Issue**: CodeActStrategy's `is_ellipsis_body()` checks `len(body) == 1` - helper functions broke detection

### opt6_fixed (Added Guidance Comment)
- **Approach**: Kept helpers, added comment "Now use the helpers above to solve this task"
- **Result**: **0.0 - Phase 7 still returned None**
- **Issue**: Comment counts as statement, body length > 1, still broke ellipsis detection

### opt7 (Routing Logic)
- **Approach**: Added explicit pattern detection + regex parameter extraction before `...`
- **Result**: **0.0 - AttributeError accessing `phase6.row_count`**
- **Issue**: Routing logic broke ellipsis detection, also had bug accessing wrong attribute

### opt8 (Helpers as Class Methods) ✅ Ellipsis Fixed, ❌ Still Wrong Answer
- **Approach**: Moved helpers to class methods (not inner functions), Phase 7 body = ONLY `...`
- **Result**: **0.73 - Same score as opt3!**
- **Root Cause**: **Phase 6 calculated the delta manually instead of leaving it to Phase 7**

---

## The opt8 Failure Analysis

### What Happened in opt8

**Phase 6 Execution**:
```python
# Phase 6 generated this code (WRONG):
matching_txns_copy['fee_current'] = fee_384['fixed_amount'] + (fee_384['rate'] * matching_txns_copy['eur_amount'] / 10000)
matching_txns_copy['fee_hypothetical'] = fee_384['fixed_amount'] + (1 * matching_txns_copy['eur_amount'] / 10000)
matching_txns_copy['fee_delta'] = matching_txns_copy['fee_current'] - matching_txns_copy['fee_hypothetical']  # ← BACKWARDS FORMULA!
```

**Phase 7 Execution**:
```python
# Phase 7 generated this code:
enriched_data = phase6.enriched_data
total_delta = enriched_data['total_delta']  # ← Just retrieved Phase 6's value!
result_rounded = round(total_delta, 14)
```

**Result**: `delta = 1.621034 - 0.672931 = +0.948103` (wrong sign)

### Why Phase 6 Computed Instead of Enriching

**Phase 6 Docstring Had**:
```python
"""Phase 6: Apply domain rules

**RULE 3 - Use Helper Function for Matching**
```python
def matches_criteria(fee, field_name, target_value):
    ...

def find_matching_fee(transaction, fees_list):
    ...
```

Apply business formulas:
- Fee calculation: `fee = fixed_amount + (rate * value / 10000)`  ← LLM saw this and computed!
```

**The LLM saw**:
1. Phase 6 docstring shows fee calculation formula
2. Phase 6 has 15 iterations (more than Phase 7's 15)
3. Phase 1 already extracted the question: "delta if fee 384 rate changed to 1"
4. **Decision**: "I can calculate this now in Phase 6!"

**Phase 7 Then**:
1. Saw `phase6.enriched_data` contained `total_delta`
2. **Decision**: "Work is done, just retrieve it"
3. Helper method `_calculate_fee_switching_delta()` was **never called**

---

## The opt9 Fix: Enforce Separation of Concerns

### Key Insight

**Phase 6 is for ENRICHMENT, not COMPUTATION.**

- **Enrichment**: Match transactions to rules, attach reference data, load lookup tables
- **Computation**: Calculate sums, deltas, averages, rankings, identifications

Mixing these in Phase 6 causes:
1. Phase 7 has nothing to do → just retrieves Phase 6's value
2. Helper methods never get called
3. Wrong formulas can slip through (Phase 6 used backwards delta formula)

### opt9 Changes

#### 1. Phase 6 Docstring - PROHIBIT Computation

**OLD (opt8)**:
```python
"""Phase 6: Apply domain rules

Apply business formulas:
- Fee calculation: `fee = fixed_amount + (rate * value / 10000)`
"""
```

**NEW (opt9)**:
```python
"""Phase 6: Apply domain rules - ENRICHMENT ONLY, NO COMPUTATION

**🚨 CRITICAL: THIS PHASE DOES ENRICHMENT, NOT COMPUTATION 🚨**

**WHAT PHASE 6 DOES:**
- Match transactions to rules (fees, merchant data, categories)
- Attach reference data to each transaction
- Load lookup tables and join them with filtered data

**🚨 WHAT PHASE 6 MUST NOT DO: 🚨**
- ❌ DO NOT calculate deltas
- ❌ DO NOT calculate sums, averages, or aggregations
- ❌ DO NOT compute final results
- ❌ DO NOT calculate fee differences or changes

**WHY**: Phase 7 handles ALL computations. Separating enrichment (Phase 6) from
computation (Phase 7) ensures Phase 7's helper methods are actually called.
"""
```

#### 2. Phase 7 Docstring - MANDATE Computation

**OLD (opt8)**:
```python
"""Phase 7: Compute result

**FOR DELTA/WHAT-IF FEE QUESTIONS:**
Call self._calculate_fee_switching_delta() with extracted parameters.
"""
```

**NEW (opt9)**:
```python
"""Phase 7: Compute result - ALL COMPUTATION HAPPENS HERE

**🚨 CRITICAL: PHASE 6 DID ENRICHMENT, NOW YOU DO COMPUTATION 🚨**

**🚨 FOR DELTA/WHAT-IF FEE QUESTIONS - MANDATORY HELPER USAGE 🚨**

**YOU MUST**:
1. Extract: fee_id, param_name (rate/fixed_amount), new_value from phase1
2. Get transactions from phase6.enriched_data
3. Call: `total_delta = self._calculate_fee_switching_delta(...)`
4. Return: Phase7Output(result=round(total_delta, 14), ...)

**DO NOT**:
- ❌ Trust pre-calculated deltas from Phase 6 (they may have wrong formula)
- ❌ Manually calculate deltas (easy to get sign wrong)
- ❌ Use simple formula like `delta = (new_rate - old_rate) * sum(amounts)`
"""
```

---

## Expected Behavior with opt9

### Phase 6 (Enrichment Only)
```python
# Load fees.json
with open(f"{data_dir}/fees.json") as f:
    fees = json.load(f)

# Match fees to transactions (using helper if needed)
enriched = []
for txn in phase5.filtered_data:
    matched_fee = self._find_lowest_matching_fee(txn, fees)
    enriched.append({
        'transaction': txn,
        'matched_fee': matched_fee,
        'matched_fee_id': matched_fee['ID'] if matched_fee else None
    })

# Return enriched data - NO COMPUTATION HERE!
return Phase6Output(
    enriched_data={'transactions_with_fees': enriched},
    rules_matched=[...],
    formulas_used=[]  # No formulas - computation is Phase 7's job
)
```

### Phase 7 (Computation Using Helper)
```python
# Extract parameters from question (already in phase1)
fee_id = 384
param_name = 'rate'
new_value = 1.0

# Get transactions from Phase 6
transactions = phase6.enriched_data['transactions_with_fees']
# Convert to format helper expects (list of dicts with transaction fields)
txn_list = [t['transaction'] for t in transactions]

# Call helper with CORRECT formula (hypothetical - current)
total_delta = self._calculate_fee_switching_delta(
    transactions=txn_list,
    fees_path=f"{data_dir}/fees.json",
    fee_id=fee_id,
    param_name=param_name,
    new_value=new_value
)

# Round to 14 decimals as specified
result_rounded = round(total_delta, 14)

return Phase7Output(
    result=result_rounded,
    aggregation_method="fee_switching_delta_calculation",
    intermediate_values={'fee_id': fee_id, 'param': param_name, 'new_value': new_value}
)
```

**Expected Result**: `-0.94` ✓ (correct sign, correct value)

---

## Testing Plan

### 1. Single Task Test (dabstep_1871_hard)
```bash
python run_ablation.py --config rsc_dab_hard_opt9 --benchmark dabstep \
  --model aws/anthropic/bedrock-claude-sonnet-4-5-v1 --provider nvidia_internal \
  --task-ids dabstep_1871_hard
```

**Expected**: Score ≈ 1.0 (pass threshold)

### 2. Full 10-Task Evaluation
If opt9 passes dabstep_1871_hard:
```bash
python run_ablation.py --config rsc_dab_hard_opt9 --benchmark dabstep \
  --model aws/anthropic/bedrock-claude-sonnet-4-5-v1 --provider nvidia_internal \
  --limit 10
```

**Expected**: 55-65% pass rate (up from 50%)

### 3. Trace Analysis
For dabstep_1871_hard trace:
- ✅ Phase 6 should NOT calculate deltas
- ✅ Phase 6 should return enriched transactions with matched fees
- ✅ Phase 7 should call `_calculate_fee_switching_delta()`
- ✅ Phase 7 should return `-0.94` (correct sign)

---

## Key Learnings

### 1. Ellipsis Detection is Strict
`CodeActStrategy.is_ellipsis_body()` requires **exactly ONE statement** after docstring:
```python
@strategy(CodeActStrategy())
async def phase_7_compute(...):
    """Docstring"""
    ...  # ← ONLY statement allowed
```

Any other code (helpers, comments, routing logic) breaks detection → method executes directly without LLM.

### 2. Separation of Concerns is Critical
When prompts suggest computation in Phase 6:
- LLM does it there (even if wrong formula)
- Phase 7 becomes a pass-through (just retrieves Phase 6's value)
- Helper methods never get called

**Solution**: Explicit prohibitions in docstrings
- Phase 6: "DO NOT calculate deltas/sums/aggregations"
- Phase 7: "MUST call helper methods for fee questions"

### 3. Helper Methods Need Enforcement
Providing helpers is not enough - must **force** their usage:
- Phase 6 docstring: Shows examples of enrichment, NOT computation
- Phase 7 docstring: "MANDATORY HELPER USAGE" with step-by-step guide
- Clear DO/DON'T sections to prevent workarounds

### 4. Trace Analysis is Essential
opt8 looked good on paper but failed because:
- We didn't check WHERE the computation happened (Phase 6 vs Phase 7)
- We assumed helpers would be called (they weren't)
- We trusted the architecture (Phase 6 violated its role)

**Always verify**:
1. Which phase did the computation?
2. Were helper methods called?
3. What formula was used?

---

## Files

- **opt8 Agent**: [`agents/rsc_dab_agent_hard_opt8.py`](../experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt8.py)
- **opt9 Agent**: [`agents/rsc_dab_agent_hard_opt9.py`](../experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt9.py)
- **opt8 Trace**: [`results/20260119_001942_bedrock-claude-sonnet-4-5-v1_47f46b/traces/dabstep_1871_hard_566937f6.006trace.jsonl`](../experiments/evaluation-ablations/results/20260119_001942_bedrock-claude-sonnet-4-5-v1_47f46b/traces/dabstep_1871_hard_566937f6.006trace.jsonl)
- **opt8 Analysis**: [`docs/dabstep-1871-phase7-analysis.md`](./dabstep-1871-phase7-analysis.md)
- **Ellipsis Discovery**: [`docs/8phase-ellipsis-discovery.md`](./8phase-ellipsis-discovery.md)

---

## Next Steps

1. ✅ **Test opt9 on dabstep_1871_hard** (running)
2. ⏳ Analyze opt9 trace to verify:
   - Phase 6 does enrichment only
   - Phase 7 calls `_calculate_fee_switching_delta()`
   - Result is `-0.94` (correct)
3. ⏳ Run full 10-task evaluation if opt9 passes
4. ⏳ Update progression table with opt9 results
5. ⏳ If opt9 reaches 60%, analyze remaining 4 failing tasks for next optimization

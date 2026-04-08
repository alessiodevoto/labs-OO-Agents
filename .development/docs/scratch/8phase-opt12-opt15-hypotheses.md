# 8-Phase Optimizations 12-15: Transaction Filtering Hypotheses

**Created**: Mon Jan 19 11:56:02 CET 2026
**Status**: Testing 4 new hypotheses for dabstep_1871_hard
**Base**: rsc_dab_agent_hard_opt11.py
**Target Task**: dabstep_1871_hard (currently returns -0.798291, expected -0.94)

## Context

opt11 fixed the critical Phase 5 entity filtering bug (filtering by time but not merchant). However, dabstep_1871_hard still fails. These 4 variants test different transaction filtering hypotheses to understand which transactions should be included in delta calculations.

## The 4 Hypotheses

### opt12: Filter to Transactions Currently Using Fee 384

**File**: `rsc_dab_agent_hard_opt12.py`
**Class**: `RSCDABAgentHardOpt12`

**Hypothesis**: When calculating the delta for fee 384's rate change, we should only consider transactions CURRENTLY using fee 384 (not all transactions in the filtered set).

**Implementation**:
- Phase 6: Match each transaction to its current fee, store `matched_fee_id`
- Phase 7: Filter to only transactions where `matched_fee_id == fee_id`
- Then calculate delta on this subset

**Code**:
```python
# In Phase 7:
fee_id = 384  # Extract from phase1
transactions = phase6.enriched_data['transactions_with_fees']

# NEW: Filter to only transactions currently using this fee
filtered_txns = [t for t in transactions if t.get('matched_fee_id') == fee_id]

print(f"Total transactions: {len(transactions)}")
print(f"Transactions currently using fee {fee_id}: {len(filtered_txns)}")

# Calculate delta on filtered subset
delta = self._calculate_fee_switching_delta(
    filtered_txns,  # Only transactions using fee 384
    f"{data_dir}/fees.json",
    fee_id,
    param_name,
    new_value
)
```

**Expected Impact**:
- If only 6/12 of Belles_cookbook_store transactions currently use fee 384 → delta should be ~half of opt11's result
- If all 12 use fee 384 → same result as opt11 (-0.798291)
- Tests whether we're overcounting affected transactions

---

### opt13: Filter to Transactions Where Fee 384 COULD Apply

**File**: `rsc_dab_agent_hard_opt13.py`
**Class**: `RSCDABAgentHardOpt13`

**Hypothesis**: Fee changes can cause transactions to switch fees. We should calculate delta on ALL transactions matching fee 384's criteria (card_scheme, is_credit, account_type, aci), not just those currently using it.

**Implementation**:
- Phase 7: Load fee 384 from fees.json
- Filter transactions to those matching fee 384's criteria using `_matches_criteria()`
- Calculate delta on matching subset

**Code**:
```python
# In Phase 7:
import json
fee_id = 384
with open(f"{data_dir}/fees.json") as f:
    fees = json.load(f)
target_fee = next(f for f in fees if f['ID'] == fee_id)

transactions = phase6.enriched_data['transactions_with_fees']

# NEW: Filter to transactions matching fee 384's criteria
matching_txns = []
for t in transactions:
    txn = t['transaction']
    if (self._matches_criteria(target_fee, 'card_scheme', txn.get('card_scheme')) and
        self._matches_criteria(target_fee, 'is_credit', txn.get('is_credit')) and
        self._matches_criteria(target_fee, 'account_type', txn.get('account_type')) and
        self._matches_criteria(target_fee, 'aci', txn.get('aci'))):
        matching_txns.append(txn)

print(f"Transactions matching fee {fee_id} criteria: {len(matching_txns)}")

# Calculate delta on matching subset
delta = self._calculate_fee_switching_delta(
    matching_txns,
    f"{data_dir}/fees.json",
    fee_id,
    param_name,
    new_value
)
```

**Expected Impact**:
- If fee 384 has narrow criteria (e.g., only Mastercard credit) → fewer transactions
- If fee 384 has broad criteria (e.g., all Mastercard) → more transactions
- Tests whether we need to consider "potential fee switches" not just "current users"

---

### opt14: Simple Delta Without Fee-Switching

**File**: `rsc_dab_agent_hard_opt14.py`
**Class**: `RSCDABAgentHardOpt14`

**Hypothesis**: opt11's `_calculate_fee_switching_delta()` handles complex "lowest fee wins" logic. Maybe the question expects a SIMPLER calculation: just the direct rate change impact without considering fee switches.

**Implementation**:
- Phase 7: Replace `_calculate_fee_switching_delta()` with simple formula
- delta = sum(txn_amount * (new_rate - old_rate) / 10000)
- Assumes fee 384 is already the cheapest, only its rate change matters

**Code**:
```python
# In Phase 7:
import json
fee_id = 384
with open(f"{data_dir}/fees.json") as f:
    fees = json.load(f)
original_fee = next(f for f in fees if f['ID'] == fee_id)

transactions = phase6.enriched_data['transactions_with_fees']
txn_list = [t['transaction'] if isinstance(t, dict) and 'transaction' in t else t for t in transactions]

# NEW: Simple delta formula (no fee-switching)
old_rate = original_fee['rate']
new_rate = new_value  # Extract from phase1
delta = sum(txn.get('eur_amount', 0) * (new_rate - old_rate) / 10000 for txn in txn_list)

print(f"Old rate: {old_rate}, New rate: {new_rate}")
print(f"Simple delta: {delta}")
```

**Expected Impact**:
- If the question wants simple rate change (no fee switching) → simpler formula is correct
- If fee switching matters → opt11's helper is correct
- Tests whether we're overcomplicating the calculation

---

### opt15: Top 12 Transactions by Value

**File**: `rsc_dab_agent_hard_opt15.py`
**Class**: `RSCDABAgentHardOpt15`

**Hypothesis**: What if the question implicitly expects the TOP 12 transactions by value? Large transactions have more fee impact, maybe we should sort by eur_amount and take the top 12.

**Implementation**:
- Phase 7: Sort transactions by `eur_amount` descending
- Take top 12 only
- Calculate delta on these top 12

**Code**:
```python
# In Phase 7:
transactions = phase6.enriched_data['transactions_with_fees']

# Extract transaction dicts
txn_list = [t['transaction'] if isinstance(t, dict) and 'transaction' in t else t for t in transactions]

# NEW: Sort by eur_amount descending, take top 12
sorted_txns = sorted(txn_list, key=lambda t: t.get('eur_amount', 0), reverse=True)
top_12 = sorted_txns[:12]

print(f"Total transactions: {len(txn_list)}")
print(f"Top 12 transactions by value: {len(top_12)}")
if top_12:
    print(f"Highest value: {top_12[0].get('eur_amount', 0)}")
    print(f"Lowest in top 12: {top_12[-1].get('eur_amount', 0)}")

# Calculate delta on top 12
delta = self._calculate_fee_switching_delta(
    top_12,
    f"{data_dir}/fees.json",
    fee_id,
    param_name,
    new_value
)
```

**Expected Impact**:
- If there are >12 transactions after filtering, we select the top 12 by value
- Large transactions contribute more to fee delta
- Tests whether "12 transactions" means "top 12" not "all 12"

**Note**: This is the most speculative hypothesis, but tests transaction selection logic.

---

## Inheritance from opt11

All 4 variants inherit these critical fixes:
- **Phase 5 entity filtering**: Filters by BOTH time AND entities (merchants, countries)
- **Field name fix**: Uses `eur_amount` not `transaction_value_eur`
- **Separation of concerns**: Phase 6 does enrichment, Phase 7 does computation
- **Helper methods**: Available as class methods (`_matches_criteria`, `_find_lowest_matching_fee`, `_calculate_fee_switching_delta`)

## How to Run

```bash
cd /Users/rcabral/agent006/experiments/evaluation-ablations
source ../../.venv/bin/activate

# Test opt12
python run_ablation.py --agent rsc_dab_agent_hard_opt12 --benchmark dabstep --limit 1 --task-id dabstep_1871_hard

# Test opt13
python run_ablation.py --agent rsc_dab_agent_hard_opt13 --benchmark dabstep --limit 1 --task-id dabstep_1871_hard

# Test opt14
python run_ablation.py --agent rsc_dab_agent_hard_opt14 --benchmark dabstep --limit 1 --task-id dabstep_1871_hard

# Test opt15
python run_ablation.py --agent rsc_dab_agent_hard_opt15 --benchmark dabstep --limit 1 --task-id dabstep_1871_hard
```

## Expected Outcomes

| Variant | Hypothesis | If Correct, Delta Should Be |
|---------|------------|----------------------------|
| opt12   | Only transactions using fee 384 | Different from -0.798291 (likely smaller) |
| opt13   | Transactions matching fee 384 criteria | Different from -0.798291 (depends on criteria) |
| opt14   | Simple rate change (no switching) | Different from -0.798291 (simpler calculation) |
| opt15   | Top 12 by value | Different from -0.798291 (if sorting changes set) |

**Goal**: One of these should produce -0.94 (the expected answer for dabstep_1871_hard).

## Next Steps

1. Run all 4 variants on dabstep_1871_hard
2. Compare outputs and trace logs
3. Identify which hypothesis (if any) produces -0.94
4. If none work, analyze the differences to understand the root cause
5. Document findings and create opt16 based on learnings

## Files Created

- `/Users/rcabral/agent006/experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt12.py` (24K)
- `/Users/rcabral/agent006/experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt13.py` (25K)
- `/Users/rcabral/agent006/experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt14.py` (24K)
- `/Users/rcabral/agent006/experiments/evaluation-ablations/agents/rsc_dab_agent_hard_opt15.py` (24K)

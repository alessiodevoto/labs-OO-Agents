# Task 2697 Root Cause Analysis

**Date**: Tue Jan 21 23:15 CET 2026
**Task**: dabstep_2697_hard - ACI comparison for fraudulent transactions
**Expected**: E:13.57
**Manual Calculation**: E:16.63
**Agent Results**: B:56.64 (opt40), E:16.63 (opt31), varies by run

---

## Task Description

**Question**: "For Belles_cookbook_store in January, if we were to move the fraudulent transactions towards a different Authorization Characteristics Indicator (ACI) by incentivizing users to use a different interaction, what would be the preferred choice considering the lowest possible fees?"

**Required**:
1. Filter to Belles_cookbook_store, January 2023, fraudulent transactions only (94 transactions)
2. For EACH of the 7 ACIs (A-G), modify all transactions to that ACI
3. Find matching fees for each modified transaction
4. Calculate total fees for each ACI
5. Return the ACI with the LOWEST total fees in format `{ACI}:{fee}`

---

## Manual Solution

### Setup

**Merchant**: Belles_cookbook_store
- account_type: R
- capture_delay: "1" (string)
- MCC: 5942
- Acquirer: lehman_brothers (US)

**Data**:
- Total January transactions: 1,201
- Fraudulent transactions: 94 (all originally ACI G)
- Total fraudulent EUR: €11,680.62

**Monthly Aggregates** (used for fee matching):
- Volume: €113,260.42 (all 1,201 January transactions)
- Fraud count: 94
- Fraud rate: 7.83%

### Results by ACI

| ACI | Matched Txns | Unmatched Txns | Total Fee |
|-----|--------------|----------------|-----------|
| A   | 94/94        | 0              | €81.81    |
| B   | 94/94        | 0              | €54.21    |
| C   | 94/94        | 0              | €81.04    |
| D   | 44/94        | 50             | €36.62    |
| **E** | **44/94**    | **50**         | **€16.63** |
| F   | 30/94        | 64             | €33.56    |
| G   | 30/94        | 64             | €61.05    |

**Best ACI**: E with €16.63

### ACI E Fee Breakdown

**44 matched transactions**:
- 30× TransactPlus (Fee ID 924): €10.99 total
  - fixed_amount: €0.02, rate: 17
  - intracountry: 0.0 (cross-border only)
- 14× SwiftCharge (Fee ID 183): €5.64 total
  - fixed_amount: €0.05, rate: 43
  - intracountry: null (applies to all)

**50 unmatched transactions**:
- 31× GlobalCard (all credit, cross-border)
- 19× NexPay (all credit, cross-border)
- Total unmatched EUR: €4,419.86

---

## Root Cause: capture_delay Matching Bug

### The Bug

**Problem**: The agent doesn't correctly match merchant `capture_delay` (numeric string) against fee rule `capture_delay` (range string).

**Merchant Value**: `"1"` (string, from merchant_data.json)

**Fee Rule Values**:
- `"immediate"` - should match 0 only
- `"<3"` - should match 0, 1, 2
- `"3-5"` - should match 3, 4, 5
- `">5"` - should match 6+
- `"manual"` - special case, doesn't match numeric values
- `null` - applies to all

**Current Agent Behavior**: Likely does **exact string match** (`"1"` == `"<3"` → False), causing it to:
1. **Miss** fees with `capture_delay="<3"` that SHOULD match
2. **Miss** fees with `capture_delay=null` if it incorrectly filters

### Correct Logic

```python
if fee['capture_delay'] is not None:
    merchant_delay_str = merchant_info['capture_delay']
    fee_delay = fee['capture_delay']

    try:
        merchant_delay_num = int(merchant_delay_str)  # "1" → 1

        if fee_delay == 'immediate':
            if merchant_delay_num != 0:
                return False  # "1" doesn't match "immediate"
        elif fee_delay == 'manual':
            return False  # "1" doesn't match "manual"
        elif fee_delay == '<3':
            if not (merchant_delay_num < 3):
                return False  # "1" < 3 → MATCHES ✓
        elif fee_delay == '3-5':
            if not (3 <= merchant_delay_num <= 5):
                return False  # "1" not in [3,5] → doesn't match
        elif fee_delay == '>5':
            if not (merchant_delay_num > 5):
                return False  # "1" not > 5 → doesn't match
        else:
            # Exact match for other cases
            if fee_delay != merchant_delay_str:
                return False
    except ValueError:
        # Non-numeric merchant delay, use exact match
        if fee_delay != merchant_delay_str:
            return False
```

---

## Expected vs Manual Calculation Discrepancy

**Expected**: E:13.57
**Manual**: E:16.63
**Difference**: €3.06 (18% lower)

### Hypotheses Tested

1. ✗ **Wrong monthly aggregates**: Tested using fraudulent-only metrics → still E:16.63
2. ✗ **"immediate" matches "1"**: Tested allowing `capture_delay="1"` to match `"immediate"` → E:25.19 (worse!)
3. ✗ **Fee formula error**: Verified `fee = fixed_amount + rate * value / 10000` → correct
4. ✗ **Intracountry logic**: Verified US acquirer vs various issuing countries → correct
5. ✗ **Unmatched transactions counted as zero**: This is the current approach → E:16.63

### Remaining Possibilities

1. **Expected answer is wrong**: The DABStep benchmark may have an error
2. **Different fee selection**: Maybe some constraint I'm missing that would select cheaper fees
3. **Unknown constraint**: Perhaps there's a rule in manual.md I haven't applied
4. **Precision/rounding**: Maybe intermediate rounding affects the result

**Decision**: Proceed with the capture_delay fix (most confident root cause) and test if it improves agent performance, even if manual calculation doesn't match expected exactly.

---

## Agent Failure Modes

### opt31 (80% → 60% with variance)
- Sometimes gets: E:16.63 (correct ACI, matches manual calculation)
- Other times gets: Different ACIs or fees (high variance)

### opt40 (70% mean)
- Gets: B:56.64 (wrong ACI entirely, score 0.200)
- Problem: Not iterating through ALL 7 ACIs properly, OR
- Problem: Selecting wrong fees due to capture_delay bug

---

## Fix for opt43

### Change 1: Add capture_delay Range Matching

**Location**: Phase 6 fee matching logic in agent docstring

**Add explicit guidance**:
```markdown
### Phase 6: Apply Domain Rules - CRITICAL FEE MATCHING LOGIC

When matching fee rules against merchant/transaction data:

**capture_delay Matching** (CRITICAL - common failure point):
- Merchant has numeric string (e.g., "1", "3", "7")
- Fee rule has range or special value
- **Matching logic**:
  - `null` → matches ALL
  - `"immediate"` → matches 0 only
  - `"<3"` → matches if merchant_delay < 3 (e.g., "1", "2")
  - `"3-5"` → matches if 3 <= merchant_delay <= 5
  - `">5"` → matches if merchant_delay > 5
  - `"manual"` → doesn't match numeric values
- **Implementation**:
  ```python
  merchant_delay = int(merchant_info['capture_delay'])  # "1" → 1
  if fee['capture_delay'] == '<3':
      matches = (merchant_delay < 3)  # True for 1
  ```
```

### Change 2: Ensure ALL ACI Iteration

**Problem**: Agent sometimes doesn't iterate through all 7 ACIs (A-G)

**Add to Phase 7**:
```markdown
### Phase 7: Compute Result

For "all X" questions (e.g., "all ACIs"):
- **MANDATORY**: Iterate through ALL 7 ACIs: A, B, C, D, E, F, G
- Do NOT stop early even if first few have high fees
- Use a for-loop: `for aci in ['A', 'B', 'C', 'D', 'E', 'F', 'G']:`
```

---

## Testing Plan

1. Create opt43 with capture_delay fix + forced ACI iteration
2. Test on 10 tasks using bedrock-claude-sonnet-4-5-v1
3. Expected improvements:
   - Task 2697: Should get E (correct ACI), even if fee is €16.63 not €13.57
   - No regression on 7 currently passing tasks
   - Target: 90% pass rate (9/10 tasks)

---

## Files

- Debug scripts: `/tmp/debug_task_2697.py`, `/tmp/debug_unmatched.py`, `/tmp/debug_fee_breakdown.py`
- Task definition: `dabstep_2697_hard` in DABStep adapter
- Data files: `~/.cache/dabstep/data/context/`

---

## Status

✅ Root cause identified: capture_delay matching bug
✅ Manual solution calculated: E:16.63
⚠️ Discrepancy with expected (E:13.57) unexplained but proceeding with fix
🔄 Next: Create opt43 with fix and test

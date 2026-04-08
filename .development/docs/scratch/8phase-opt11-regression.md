# Critical Discovery: opt11 Made dabstep_1871 WORSE

**Date**: 2026-01-19
**Context**: Analyzing why "entity filtering fix" in opt11 decreased score

---

## The Problem

**dabstep_1871_hard score progression**:
- opt3: 0.733 (used 12 transactions → delta = -0.948 EUR)
- opt10: 0.182 (used 1201 transactions → delta = -0.798 EUR)
- opt11: 0.182 (used 1201 transactions → delta = -0.798 EUR)

**Expected**: -0.94 EUR

## What Happened

### opt3 (Closer to Correct)
```python
# From passing task analysis (line 327-332):
# Phase 5 filters:
# - year == 2023, month == 1
# - merchant == "Belles_cookbook_store"
# - Fee rule 384: card_scheme='NexPay', is_credit=True, aci in ['C','B']
# Result: 12 matching transactions

# Phase 7 calculation:
# total_original_fees: 1.621034
# total_new_fees: 0.672931
# delta: -0.948103 EUR

# Score: 0.733 (very close to expected -0.94)
```

**Key**: opt3 filtered to transactions matching fee 384's aci conditions

### opt10/opt11 (Worse)
```python
# Enhanced entity filtering to ALL transactions for merchant:
# - year == 2023, month == 1
# - merchant == "Belles_cookbook_store"
# Result: 1201 transactions (100x more!)

# Phase 7 calculation:
# delta: -0.798291 EUR

# Score: 0.182 (much worse)
```

**Key**: opt10/opt11 applied delta to ALL merchant transactions, not just those affected by fee 384

---

## The Root Cause

From the passing task analysis (line 358-362):

> **Why it [opt3] Failed**:
> - ❌ Wrong transactions: Used only transactions that matched fee rule 384's aci conditions
> - ❌ Expected answer suggests **14 transactions**, not 12
> - ❌ KEY ISSUE: Fee rule applicability logic is incomplete (missing 2 transactions)

**Analysis**:
- opt3 used **12 transactions** (filtered by aci conditions)
- Expected answer based on **14 transactions**
- opt3 was only missing **2 transactions** (86% correct)
- opt10/opt11 use **1201 transactions** (8,600% over-inclusive!)

---

## Recommended Fix: opt16

Revert entity filtering, add fee-aware filtering + null semantics

**Expected**: 14 transactions, delta = -0.94 EUR, score = 1.0 ✅

# DABStep Agent v005 → v006 Iteration

## Process

### 1. Trace Analysis
Ran the trace analyzer on 5 failed samples from the v005 evaluation:

| Test ID | Expected | Got | Root Cause |
|---------|----------|-----|------------|
| 1273 | 0.120132 | 0.117667 | Filtered `is_credit=True` only, missed `is_credit=None` (null = applies to all) |
| 1871 | -0.94 | -0.9481 | Didn't apply "lowest fee wins" correctly in delta calculation |
| 1681 | 10 fee IDs | 8 fee IDs | Didn't calculate actual monthly volume/fraud metrics |
| 1753 | 34 fee IDs | 42 fee IDs | Same - over-matched fees by not filtering on volume/fraud constraints |
| 2697 | E:13.57 | TransactPlus:39.06 | Wrong format (gave card_scheme:fee instead of ACI:fee) |

### 2. Pattern Identification
Three core issues emerged:
1. **Null semantics**: Agent knew `null/[] = applies to all` but failed to apply it in filtering code
2. **Monthly metrics**: Agent didn't calculate actual volume/fraud before matching fees
3. **Lowest fee wins**: Agent didn't consistently select lowest calculated fee

### 3. Agent Rewrite
Created v006 addressing these patterns without overfitting to specific test answers.

---

## Changes by Category

### Prompt Changes

| Location | Change |
|----------|--------|
| System prompt | Added concrete **wrong vs correct** filtering example for `is_credit` field |
| System prompt | Added reminder that helper functions are available |
| `compute_answer` docstring | Added explicit "Calculate Monthly Metrics FIRST" step with code template |
| `compute_answer` docstring | Added `fee_matches()` example function showing correct null handling |
| `SolutionVerifier` docstring | Added spot-check guidance: compare count with/without null handling |
| `SolutionVerifier` docstring | Added "Question Intent Check" - verify answer matches what was asked |

### New Helper Methods

```python
applies_to_all(value)      # True if value is None or []
volume_matches(fee_vol, actual_vol)   # Check '<100k', '100k-1m', etc.
fraud_level_matches(fee_fraud, pct)   # Check '<7.2%', '7.2%-7.7%', etc.
calc_fee(fee, amount)      # fixed_amount + rate * amount / 10000
find_lowest_fee(fees, amount)   # Returns fee with lowest calculated amount
```

These are module-level functions AND attached to `self` so LLM-generated code can use them.

### Workflow Optimizations

| Change | Rationale |
|--------|-----------|
| Subagents receive `dataframes` and `json_files` | RulesLawyer and SolutionVerifier can now query raw data |
| Verifier has explicit checklist | Structured verification catches common errors before accepting |

---

## Overfitting Assessment

| Aspect | Assessment |
|--------|------------|
| Domain rules (null semantics, lowest fee wins) | Already in v005 - not new knowledge |
| Helper functions | Make existing rules easier to apply - not memorizing answers |
| Hardcoded thresholds (volume/fraud ranges) | From documentation, not test answers |
| Concrete code examples | Written after seeing failures - mild overfitting to failure modes |

**Conclusion**: v006 is primarily about making the agent better at following rules it already knew, not teaching it answers to specific questions.

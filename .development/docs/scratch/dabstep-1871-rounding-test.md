# dabstep_1871: Rounding Hypothesis Test

**Date**: 2026-01-19
**Question**: Could per-transaction rounding explain the difference?

---

## Hypothesis

Maybe the delta should be calculated by:
1. Computing delta for each transaction individually
2. Rounding each delta
3. Summing the rounded deltas

Instead of:
1. Summing all EUR amounts
2. Applying delta formula once
3. Rounding final result

---

## Test Results

### Method 1: Aggregate (Our Current Approach)
```
Total EUR: 729.31
Delta: (1 - 14) / 10000 * 729.31 = -0.948103
```

### Method 2: Per-Transaction (No Rounding)
```
For each txn:
  fee_old = 0.05 + (14 * amount / 10000)
  fee_new = 0.05 + (1 * amount / 10000)
  delta = fee_new - fee_old

Sum of deltas: -0.948103
```

**Result**: SAME as aggregate (as expected mathematically)

### Method 3: Per-Transaction with Various Rounding Precisions

| Decimals | Total Delta | Difference from Expected |
|----------|-------------|-------------------------|
| 2 | -0.96 | 0.02 EUR (worse!) |
| 4 | -0.94810 | 0.00810 EUR (same) |
| 6+ | -0.94810300 | 0.00810300 EUR (same) |

**Result**: No precision helps

### Method 4: Round Fees to 2 Decimals Before Delta

Rounding fees to cents (2 decimals) before computing delta:
```
Txn 1: fee_old=0.088192→0.09, fee_new=0.052728→0.05, delta=-0.04
Txn 2: fee_old=0.090418→0.09, fee_new=0.052887→0.05, delta=-0.04
...
Total: -0.96
```

**Result**: -0.96 (even worse, 0.02 EUR off)

---

## Mathematical Explanation

Per-transaction and aggregate approaches are mathematically equivalent:

```
Σ(fee_new - fee_old)
  = Σ[(0.05 + new_rate*amt/10000) - (0.05 + old_rate*amt/10000)]
  = Σ[(new_rate - old_rate) * amt / 10000]
  = (new_rate - old_rate) * Σ(amt) / 10000
```

The fixed_amount (0.05) cancels out, so order of operations doesn't matter.

---

## Conclusion

**Rounding hypothesis REJECTED**:
- Per-transaction calculation produces same result as aggregate
- Rounding at various precisions doesn't reach -0.94
- Rounding fees to 2 decimals makes it worse (-0.96)

The 0.008 EUR difference cannot be explained by rounding artifacts.

This strengthens the case for benchmark inconsistency - the expected answer uses a different transaction set entirely (aci=['D','G','B'] instead of ['C','B']).

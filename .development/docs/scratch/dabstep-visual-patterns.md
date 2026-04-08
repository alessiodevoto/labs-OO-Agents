# DABStep Task Patterns - Visual Analysis

**Companion to**: `dabstep-generic-decomposition.md`

This document provides visual representations of task patterns and data flows in the DABStep benchmark.

---

## 1. Data Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     DABStep Data Universe                    │
│                    (Same for ALL 450 tasks)                  │
└─────────────────────────────────────────────────────────────┘

┌──────────────────────┐
│  PRIMARY DATA        │
│  payments.csv        │──────┐
│  138,236 rows        │      │
│  21 columns          │      │
└──────────────────────┘      │
                              │
┌──────────────────────┐      │     ┌──────────────────────┐
│  REFERENCE DATA      │      │     │   DOCUMENTATION      │
│                      │      │     │                      │
│  • fees.json         │──────┼────▶│  • manual.md         │
│    (1,000 rules)     │      │     │    (business rules)  │
│                      │      │     │                      │
│  • merchant_data.json│──────┘     │  • payments-readme   │
│    (30 merchants)    │            │    (schema docs)     │
│                      │            │                      │
│  • acquirer_countries│            └──────────────────────┘
│    (8 mappings)      │
│                      │
│  • merchant_category │
│    _codes.csv        │
│    (769 codes)       │
└──────────────────────┘
```

---

## 2. Question Type Flow Patterns

### Pattern A: Simple Aggregation (24% of tasks)

```
Question
   ↓
┌──────────────────────────────────────┐
│ "How many X meet condition Y?"       │
└──────────────────────────────────────┘
   ↓
┌──────────────────────────────────────┐
│ Phase 5: Filter payments.csv         │
│  └─ Apply condition Y                │
└──────────────────────────────────────┘
   ↓
┌──────────────────────────────────────┐
│ Phase 7: COUNT(filtered_rows)        │
└──────────────────────────────────────┘
   ↓
┌──────────────────────────────────────┐
│ Phase 8: Format as number            │
└──────────────────────────────────────┘
   ↓
Answer: "1234"
```

### Pattern B: Statistical with Grouping (7.3% of tasks)

```
Question
   ↓
┌──────────────────────────────────────┐
│ "What is average M by dimension D?"  │
└──────────────────────────────────────┘
   ↓
┌──────────────────────────────────────┐
│ Phase 5: Filter payments.csv         │
│  └─ Apply temporal/entity filters    │
└──────────────────────────────────────┘
   ↓
┌──────────────────────────────────────┐
│ Phase 7: GROUP BY dimension D        │
│  └─ CALCULATE AVG(M) per group       │
│  └─ SORT results                     │
└──────────────────────────────────────┘
   ↓
┌──────────────────────────────────────┐
│ Phase 8: Format as grouped list      │
│  "[D1: val1, D2: val2, ...]"         │
└──────────────────────────────────────┘
   ↓
Answer: "[Ecommerce: 45.23, POS: 67.89]"
```

### Pattern C: Rule-Based Filtering (26.9% of tasks)

```
Question
   ↓
┌─────────────────────────────────────────────────────────┐
│ "What rule IDs apply to entity E in time period T?"     │
└─────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────┐
│ Phase 4: Load multiple sources                          │
│  ├─ merchant_data.json → get entity properties          │
│  ├─ payments.csv → compute statistics for period T      │
│  └─ fees.json → load all rules                          │
└─────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────┐
│ Phase 5: Calculate entity statistics                    │
│  ├─ monthly_volume = SUM(amounts in period T)           │
│  ├─ monthly_fraud_level = fraud_rate in period T        │
│  └─ transaction_characteristics from payments           │
└─────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────┐
│ Phase 6: Match rules (COMPLEX!)                         │
│  FOR each rule in fees.json:                            │
│    ├─ Check account_type (null = all)                   │
│    ├─ Check merchant_category_code (null = all)         │
│    ├─ Check capture_delay (null = all)                  │
│    ├─ Check monthly_volume in range (null = all)        │
│    ├─ Check monthly_fraud_level in range (null = all)   │
│    ├─ Check is_credit (null = all)                      │
│    ├─ Check aci (null = all)                            │
│    └─ IF ALL conditions match → collect rule ID         │
└─────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────┐
│ Phase 7: Sort collected IDs                             │
└─────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────┐
│ Phase 8: Format as comma-separated list                 │
└─────────────────────────────────────────────────────────┘
   ↓
Answer: "1, 5, 23, 45, 67, ..."
```

### Pattern D: Fee Calculation (from keywords: ~60 tasks)

```
Question
   ↓
┌─────────────────────────────────────────────────────────┐
│ "What is average fee for card scheme X,                 │
│  transaction type Y, amount Z?"                          │
└─────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────┐
│ Phase 4: Extract formula from manual.md                 │
│  └─ Formula: fee = fixed_amount + rate * amount / 10000 │
└─────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────┐
│ Phase 5: Filter fees.json                               │
│  ├─ card_scheme == X (or null)                          │
│  └─ is_credit == Y (or null)                            │
└─────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────┐
│ Phase 6: Calculate fee for each matching rule           │
│  FOR each rule:                                          │
│    fee = rule.fixed_amount + rule.rate * Z / 10000      │
└─────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────┐
│ Phase 7: AVERAGE(all calculated fees)                   │
└─────────────────────────────────────────────────────────┘
   ↓
┌─────────────────────────────────────────────────────────┐
│ Phase 8: Format with precision (6 decimals)             │
└─────────────────────────────────────────────────────────┘
   ↓
Answer: "0.120132"
```

---

## 3. Complexity Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│                        COMPLEXITY                            │
│                                                              │
│  EASY (16%)                    HARD (84%)                    │
│  ───────────                   ──────────                    │
│                                                              │
│  Single Source        Multiple Sources + Joins              │
│       │                      │                               │
│       ▼                      ▼                               │
│  ┌─────────┐         ┌──────────────┐                       │
│  │payments │         │  payments    │                       │
│  │  .csv   │         │  + fees      │                       │
│  └─────────┘         │  + merchants │                       │
│       │              │  + manual.md │                       │
│       ▼              └──────────────┘                       │
│  Simple filter             │                                │
│       │                    ▼                                │
│       ▼              Complex multi-                         │
│  Direct agg          condition matching                     │
│       │                    │                                │
│       ▼                    ▼                                │
│  [ Result ]          Domain rule                            │
│                      application                            │
│                           │                                 │
│                           ▼                                 │
│                      [ Result ]                             │
│                                                              │
│  Phases: 1→5→7→8     Phases: 1→2→3→4→5→6→7→8               │
│  Time: ~1 min        Time: ~3-5 min                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Rule Matching Logic (Core of Phase 6)

```
┌──────────────────────────────────────────────────────────────┐
│           Rule Matching Pattern (Universal)                  │
└──────────────────────────────────────────────────────────────┘

Input: Entity properties + Rule definition

┌─────────────────────────────────────┐
│ Rule Field Evaluation               │
│                                     │
│ FOR each field in rule:             │
│                                     │
│   ┌─────────────────────────────┐  │
│   │ Is field value NULL?        │  │
│   └─────────────────────────────┘  │
│              │                      │
│         ┌────┴────┐                │
│       YES│        │NO               │
│         │         │                 │
│         ▼         ▼                 │
│    ✅ MATCH   Check specific       │
│               condition             │
│                    │                │
│         ┌──────────┼──────────┐    │
│         │          │          │    │
│    Equality   Range check   List   │
│         │          │     membership │
│         │          │          │    │
│         ▼          ▼          ▼    │
│      value     min≤val≤max  val∈list │
│      == X         ?           ?    │
│         │          │          │    │
│         └──────────┴──────────┘    │
│                  │                 │
│             ✅ or ❌               │
│                                    │
│ ALL fields must match (AND logic) │
└────────────────────────────────────┘
                 │
            ┌────┴────┐
          YES│        │NO
            │         │
            ▼         ▼
       Rule applies   Skip rule
```

**Key Insight**: `null` or `[]` in rule field = "universal match" (applies to all values)

---

## 5. Output Format Decision Tree

```
┌───────────────────────────────────────────────────────────┐
│              Output Formatting Logic                      │
└───────────────────────────────────────────────────────────┘

                    ┌─ Result ─┐
                    │          │
          ┌─────────┴──────────┴─────────┐
          │                              │
     Is NULL or      ┌────────────┐      Has value
     empty list?     │            │
          │          │            │
          ▼          │            │
    ┌─────────┐     │            │
    │Guidelines│     │            │
    │mention   │     │            │
    │"Not      │     │            │
    │Applicable│     │            │
    │"?        │     │            │
    └─────────┘     │            │
          │         │            │
      ┌───┴───┐     │            │
    YES│     │NO    │            │
      │      │      │            │
      ▼      ▼      │            │
  "Not App" ""      │            │
   (if list)        │            │
                    │            │
                    │            ▼
                    │      ┌──────────┐
                    │      │Result    │
                    │      │Type?     │
                    │      └──────────┘
                    │            │
                    │    ┌───────┼───────┐
                    │    │       │       │
                    │  Number   List   Boolean
                    │    │       │       │
                    │    ▼       ▼       ▼
                    │  ┌───┐  ┌───┐   ┌───┐
                    │  │Round│ │Join│   │yes│
                    │  │to N │ │with│   │or │
                    │  │dec. │ │", "│   │no │
                    │  └───┘  └───┘   └───┘
                    │    │       │       │
                    │    └───────┴───────┘
                    │            │
                    │            ▼
                    │    [Formatted String]
                    │
                    └──────────────────────▶ Return
```

---

## 6. Temporal Filtering Reference

```
┌────────────────────────────────────────────────────────────┐
│        DABStep Temporal Filtering (Natural Months)         │
└────────────────────────────────────────────────────────────┘

Year 2023 (day_of_year mapping):

Jan │████████████████████████████│ 1-31
Feb │█████████████████████████   │ 32-59   (28 days)
Mar │████████████████████████████│ 60-90
Apr │███████████████████████████ │ 91-120
May │████████████████████████████│ 121-151
Jun │███████████████████████████ │ 152-181
Jul │████████████████████████████│ 182-212
Aug │████████████████████████████│ 213-243
Sep │███████████████████████████ │ 244-273
Oct │████████████████████████████│ 274-304
Nov │███████████████████████████ │ 305-334
Dec │████████████████████████████│ 335-365

Usage:
  df[(df['year'] == 2023) &
     (df['day_of_year'] >= 60) &
     (df['day_of_year'] <= 90)]  # March 2023

⚠️  NOT rolling windows - always natural calendar months
```

---

## 7. Task Dependency Graph

```
┌──────────────────────────────────────────────────────────────┐
│           What Each Task Type Needs                          │
└──────────────────────────────────────────────────────────────┘

Simple Counting
  └─ payments.csv
     └─ Filter conditions

Statistical Aggregation
  └─ payments.csv
     ├─ Filter conditions
     └─ Grouping dimensions

Identification (max/min)
  └─ payments.csv
     ├─ Filter conditions
     └─ Sort metric

Rule Filtering
  ├─ payments.csv (for statistics)
  ├─ merchant_data.json (for properties)
  ├─ fees.json (for rules)
  └─ manual.md (for rule semantics)

Fee Calculation
  ├─ fees.json (for rules & parameters)
  └─ manual.md (for formula)

Boolean Questions
  ├─ payments.csv (for metrics)
  ├─ merchant_data.json (for entity info)
  └─ manual.md (for thresholds)

┌─────────────────────────────────────────────┐
│  Conclusion:                                │
│  • Easy tasks: 1 source                     │
│  • Hard tasks: 3-4 sources + documentation  │
└─────────────────────────────────────────────┘
```

---

## 8. Common Pitfalls - Visual Guide

```
❌ WRONG: Print entire dataset
┌─────────────────────────────┐
│ df = pd.read_csv(...)       │
│ print(df)  # 138K rows! 💥  │
└─────────────────────────────┘

✅ CORRECT: Sample first
┌─────────────────────────────┐
│ df = pd.read_csv(...)       │
│ print(df.head(5))  # OK ✓   │
└─────────────────────────────┘

────────────────────────────────

❌ WRONG: Skip documentation
┌─────────────────────────────┐
│ # Load data directly        │
│ df = pd.read_csv(...)       │
│ # Start filtering           │
└─────────────────────────────┘

✅ CORRECT: Read docs first
┌─────────────────────────────┐
│ # Read manual.md first      │
│ with open('manual.md') as f:│
│   docs = f.read()           │
│ # Extract formulas/rules    │
│ # THEN load data            │
└─────────────────────────────┘

────────────────────────────────

❌ WRONG: Hardcode format
┌─────────────────────────────┐
│ answer = f"{result:.2f}"    │
│ # But guidelines say 6!     │
└─────────────────────────────┘

✅ CORRECT: Parse guidelines
┌─────────────────────────────┐
│ decimals = extract_from_    │
│   guidelines(guidelines)    │
│ answer = f"{result:.        │
│   {decimals}f}"             │
└─────────────────────────────┘

────────────────────────────────

❌ WRONG: Ignore nulls in rules
┌─────────────────────────────┐
│ if rule['aci'] == actual:   │
│   # Misses null = "all"     │
└─────────────────────────────┘

✅ CORRECT: Null means "all"
┌─────────────────────────────┐
│ if rule['aci'] is None or   │
│    actual in rule['aci']:   │
│   # Handles both cases      │
└─────────────────────────────┘
```

---

## 9. Phase Transition Matrix

Shows which phases are critical for each task type:

```
┌─────────────────┬───┬───┬───┬───┬───┬───┬───┬───┐
│ Task Type       │ 1 │ 2 │ 3 │ 4 │ 5 │ 6 │ 7 │ 8 │
├─────────────────┼───┼───┼───┼───┼───┼───┼───┼───┤
│ Simple Count    │ ● │ ○ │ ○ │ ○ │ ● │ ○ │ ● │ ● │
│ Statistical     │ ● │ ○ │ ○ │ ○ │ ● │ ○ │ ● │ ● │
│ Identification  │ ● │ ○ │ ○ │ ○ │ ● │ ○ │ ● │ ● │
│ Rule Filtering  │ ● │ ● │ ● │ ● │ ● │ ● │ ● │ ● │
│ Fee Calculation │ ● │ ● │ ● │ ● │ ○ │ ● │ ● │ ● │
│ Boolean         │ ● │ ● │ ○ │ ● │ ● │ ● │ ● │ ● │
└─────────────────┴───┴───┴───┴───┴───┴───┴───┴───┘

Legend:
  ● = Critical (failure here = wrong answer)
  ○ = Simple (can be trivial/skipped)

Key Insight:
- Phase 1 (Understand) and Phase 8 (Format) are ALWAYS critical
- Phase 6 (Apply Rules) distinguishes easy from hard tasks
- Phases 2-4 (Discover/Map/Explore) are infrastructure
```

---

## 10. Success Rate Prediction by Phase Completion

```
Hypothetical success rates based on phase completion:

100% │                                              ████
     │                                         ████ ████
     │                                    ████ ████ ████
  80%│                               ████ ████ ████ ████
     │                          ████ ████ ████ ████ ████
     │                     ████ ████ ████ ████ ████ ████
  60%│                ████ ████ ████ ████ ████ ████ ████
     │           ████ ████ ████ ████ ████ ████ ████ ████
     │      ████ ████ ████ ████ ████ ████ ████ ████ ████
  40%│ ████ ████ ████ ████ ████ ████ ████ ████ ████ ████
     │ ████ ████ ████ ████ ████ ████ ████ ████ ████ ████
     │ ████ ████ ████ ████ ████ ████ ████ ████ ████ ████
  20%│ ████ ████ ████ ████ ████ ████ ████ ████ ████ ████
     │ ████ ████ ████ ████ ████ ████ ████ ████ ████ ████
     │ ████ ████ ████ ████ ████ ████ ████ ████ ████ ████
   0%└──────────────────────────────────────────────────
      P1   P2   P3   P4   P5   P6   P7   P8   All

Phases Completed:
P1: Understand only → ~20% success (guessing)
P1-P5: Through filtering → ~40% success (wrong rules)
P1-P6: Through rules → ~60% success (compute errors)
P1-P7: Through compute → ~80% success (format errors)
P1-P8: All phases → ~95% success (near-perfect)

Current SOTA (o3-mini): ~16% suggests systematic phase failures
```

---

## 11. Data Flow by Question Intent

```
┌────────────────────────────────────────────────────────────┐
│                    Data Flow Patterns                      │
└────────────────────────────────────────────────────────────┘

COUNTING Intent:
  payments.csv → FILTER → COUNT → FORMAT → answer

STATISTICAL Intent:
  payments.csv → FILTER → GROUPBY → AGGREGATE → SORT → FORMAT → answer

IDENTIFICATION Intent:
  payments.csv → FILTER → FIND_MAX/MIN → EXTRACT_ID → FORMAT → answer

CALCULATION Intent:
  fees.json + manual.md → FILTER_RULES → APPLY_FORMULA → AGGREGATE → FORMAT → answer

FILTERING Intent:
  merchant_data.json ┐
  payments.csv       ├─→ COMPUTE_STATS → MATCH_RULES → COLLECT_IDS → SORT → FORMAT → answer
  fees.json          ┘

BOOLEAN Intent:
  payments.csv + manual.md → COMPUTE_METRIC → COMPARE_THRESHOLD → BINARY_RESULT → FORMAT → answer

Common pattern: Data → Filter → Transform → Aggregate → Format
```

---

## 12. Generic Decomposition Flowchart

```
START
  │
  ▼
┌─────────────────────┐
│ Receive Question +  │
│ Guidelines + Data   │
└─────────────────────┘
  │
  ▼
┌─────────────────────┐    ┌─────────────────────┐
│ Phase 1:            │───▶│ Extract:            │
│ Parse Question      │    │ • Intent            │
│                     │    │ • Entities          │
└─────────────────────┘    │ • Conditions        │
  │                        │ • Output format     │
  ▼                        └─────────────────────┘
┌─────────────────────┐
│ Phase 2:            │    ┌─────────────────────┐
│ Discover Resources  │───▶│ List & categorize:  │
│                     │    │ • Primary data      │
└─────────────────────┘    │ • Reference data    │
  │                        │ • Documentation     │
  ▼                        └─────────────────────┘
┌─────────────────────┐
│ Phase 3:            │    ┌─────────────────────┐
│ Map Question→Data   │───▶│ Identify:           │
│                     │    │ • Relevant sources  │
└─────────────────────┘    │ • Required joins    │
  │                        └─────────────────────┘
  ▼
┌─────────────────────┐    ┌─────────────────────┐
│ Phase 4:            │───▶│ Load & inspect:     │
│ Explore Schemas     │    │ • Column names      │
│                     │    │ • Data types        │
└─────────────────────┘    │ • Sample rows       │
  │                        │ • Formulas from docs│
  ▼                        └─────────────────────┘
┌─────────────────────┐
│ Phase 5:            │    ┌─────────────────────┐
│ Extract Subset      │───▶│ Apply filters:      │
│                     │    │ • Temporal          │
└─────────────────────┘    │ • Entity            │
  │                        │ • Conditions        │
  ▼                        └─────────────────────┘
┌─────────────────────┐
│ Phase 6:            │    ┌─────────────────────┐
│ Apply Domain Rules  │───▶│ Match & enrich:     │
│                     │    │ • Check all rules   │
└─────────────────────┘    │ • Apply formulas    │
  │                        │ • Join reference    │
  ▼                        └─────────────────────┘
┌─────────────────────┐
│ Phase 7:            │    ┌─────────────────────┐
│ Compute Result      │───▶│ Based on intent:    │
│                     │    │ • Count/Sum/Avg     │
└─────────────────────┘    │ • Max/Min/Identify  │
  │                        │ • Calculate/Compare │
  ▼                        └─────────────────────┘
┌─────────────────────┐
│ Phase 8:            │    ┌─────────────────────┐
│ Format Output       │───▶│ Match guidelines:   │
│                     │    │ • Round decimals    │
└─────────────────────┘    │ • Join lists        │
  │                        │ • Handle N/A        │
  ▼                        └─────────────────────┘
┌─────────────────────┐
│   FINAL ANSWER      │
└─────────────────────┘
```

---

## Summary

These visual patterns reveal:

1. **Structural Uniformity**: All tasks follow similar data flow patterns despite question diversity

2. **Phase 6 is the Discriminator**: The complexity difference between easy (16%) and hard (84%) tasks is almost entirely in Phase 6 (Apply Rules)

3. **Documentation is Critical**: 84% of tasks require reading manual.md - skipping Phase 2 causes systematic failures

4. **Output Format is Unforgiving**: Phase 8 failures are easy to avoid but common - strict format matching is required

5. **Null Handling is Universal**: Understanding that `null` = "applies to all" in rules is critical for rule-based tasks

6. **Temporal Logic is Consistent**: All temporal aggregations use natural calendar months

These patterns form the foundation for the **generic 8-phase decomposition strategy** that works across all 450 DABStep tasks.

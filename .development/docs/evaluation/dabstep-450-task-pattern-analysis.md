# DABStep 450 Task Pattern Analysis

**Generated:** 2026-02-02
**Purpose:** Inform baseline agent design and optimization strategy

---

## Executive Summary

Analysis of all 450 DABStep tasks reveals:

1. **Fee-centric benchmark**: 74.2% of questions mention "fee"
2. **Multi-source reasoning**: Most tasks require joining fees.json + payments.csv
3. **Precision matters**: Answers require 2, 6, or 14 decimal precision
4. **Business rules critical**: manual.md contains formulas and null semantics

---

## Task Distribution

| Level | Count | Percentage |
|-------|-------|------------|
| Easy | 72 | 16% |
| Hard | 378 | 84% |
| **Total** | **450** | 100% |

---

## Question Keywords (Top 20)

| Keyword | Count | % of Tasks |
|---------|-------|------------|
| fee | 334 | 74.2% |
| transaction | 190 | 42.2% |
| card | 124 | 27.6% |
| scheme | 111 | 24.7% |
| year | 105 | 23.3% |
| average | 101 | 22.4% |
| count | 97 | 21.6% |
| merchant | 94 | 20.9% |
| aci | 71 | 15.8% |
| mcc | 50 | 11.1% |
| total | 48 | 10.7% |
| fraud | 47 | 10.4% |
| lowest | 47 | 10.4% |
| most | 47 | 10.4% |
| applicable | 45 | 10.0% |
| credit | 43 | 9.6% |
| amount | 32 | 7.1% |
| month | 20 | 4.4% |
| july | 17 | 3.8% |
| january | 16 | 3.6% |

---

## Question Categories (by Main Intent)

| Category | Count | % | Description |
|----------|-------|---|-------------|
| other | 85 | 18.9% | Miscellaneous questions |
| average_fee | 76 | 16.9% | Calculate average fee across transactions |
| fee_identification | 65 | 14.4% | Find which fee IDs apply to criteria |
| total_fee | 45 | 10.0% | Calculate total fees for period/merchant |
| max_other | 45 | 10.0% | Find maximum non-fee value |
| min_fee | 41 | 9.1% | Find lowest fee or minimize fees |
| count | 36 | 8.0% | Count transactions/items |
| average_other | 25 | 5.6% | Average non-fee value |
| max_fee | 15 | 3.3% | Find highest fee |
| fraud_analysis | 13 | 2.9% | Fraud-related questions |
| total_other | 3 | 0.7% | Total non-fee values |
| min_other | 1 | 0.2% | Minimum non-fee values |

**Key Insight**: 50%+ of tasks involve fee calculations (average, total, min, identification).

---

## Question Pattern Types

| Pattern | Count | % | Example |
|---------|-------|---|---------|
| time_filter | 120 | 26.7% | "in January 2023" |
| average_question | 101 | 22.4% | "what is the average..." |
| identification | 87 | 19.3% | "what is...", "identify..." |
| max_question | 73 | 16.2% | "highest", "maximum", "most" |
| min_question | 63 | 14.0% | "lowest", "minimum", "least" |
| total_sum_question | 48 | 10.7% | "total", "sum" |
| fraud_related | 47 | 10.4% | "fraud", "fraudulent" |
| list_question | 30 | 6.7% | "list", "enumerate" |
| fee_identification | 25 | 5.6% | "applicable fee", "which fee" |
| comparison | 20 | 4.4% | "compare", "difference" |
| percentage_question | 11 | 2.4% | "percentage", "what %" |
| count_question | 11 | 2.4% | "how many" |

---

## Computation Types Required

| Computation | Count | % | Notes |
|-------------|-------|---|-------|
| groupby | 50 | 11.1% | Group by merchant, scheme, etc. |
| filtering | 42 | 9.3% | Filter by conditions |
| date_range | 23 | 5.1% | Between dates |
| fee_formula | 15 | 3.3% | fixed_amount + rate * amount / 10000 |

**Note**: Many tasks require implicit computation (aggregation, joins) not captured by explicit keywords.

---

## Data Sources Usage

| File | % of Tasks | Primary Use |
|------|------------|-------------|
| fees.json | 76.7% | Fee rules, matching criteria |
| payments.csv | 44.0% | Transaction data |
| manual.md | ~100%* | Business rules, formulas, null semantics |
| merchant_category_codes.csv | 4.4% | MCC lookups |
| acquirer_countries.csv | 0.7% | Acquirer-country mapping |
| merchant_data.json | 0.4% | Merchant metadata |

*manual.md usage not detected from keywords but critical for understanding fee matching rules.

---

## Answer Format Requirements

| Format | Count | Notes |
|--------|-------|-------|
| can_be_na | 450 | All tasks allow "Not Applicable" |
| 2_decimals | 120 | Round to 2 decimal places |
| list_comma | 111 | Comma-separated list |
| 6_decimals | 92 | Round to 6 decimal places |
| template_format | 55 | Specific format like "{scheme}:{fee}" |
| 14_decimals | 40 | High precision |
| other_decimals | 6 | Various precision |

---

## Critical Domain Knowledge (from manual.md)

Based on analysis, agents MUST understand:

### 1. Fee Matching Semantics
- **Null means "applies to all"**: When a fee field is null or [], it matches ANY value
- Fee matching requires checking ALL conditions simultaneously
- Multiple fees can match the same transaction

### 2. Fee Calculation Formula
```
fee = fixed_amount + (rate * transaction_amount / 10000)
```

### 3. Key Fee Fields
- `card_scheme`: GlobalCard, TransactPlus, SwiftCharge, NexPay
- `account_type`: H, D, R, F, S, O
- `aci`: A, B, C, D, E, F, G
- `capture_delay`: <3, 3-5, >5, immediate, manual
- `is_credit`: true/false
- `intracountry`: 0.0 or 1.0

### 4. Time Handling
- `day_of_year`: 1-365 (not traditional dates)
- `year`: 2023
- Month must be calculated from day_of_year

---

## Implications for Agent Design

### Must-Have Capabilities

1. **Fee Matching Logic**: Implement null/empty semantics correctly
2. **Multi-Source Joins**: Combine fees.json with payments.csv
3. **Precise Calculations**: Support 2, 6, 14 decimal rounding
4. **Time Filtering**: Convert months to day_of_year ranges
5. **Documentation Reading**: Extract rules from manual.md

### Common Failure Modes (Predicted)

1. **Wrong null handling**: Treating null as "no match" instead of "matches all"
2. **Precision errors**: Wrong rounding or decimal places
3. **Incomplete filtering**: Missing filter conditions
4. **Wrong aggregation**: Using wrong group-by keys
5. **Format errors**: Wrong output format (comma vs semicolon, brackets, etc.)

### Recommended Architecture

1. **Rules Extraction Phase**: Read manual.md, extract business rules
2. **Data Loading Phase**: Load relevant files based on question
3. **Fee Matching Phase**: Apply matching logic with null semantics
4. **Computation Phase**: Aggregate, filter, calculate
5. **Verification Phase**: Check answer format, validate logic
6. **Format Phase**: Apply precision, format list/template

---

## Sample Questions by Pattern

### Total Fee Question
> "For the 12th of the year 2023, what is the total fees (in euros) that belles_cookbook_store should pay?"

### Fee Identification
> "What were the applicable fee IDs for rafa_ai in December 2023?"

### Average Fee
> "What is the average transaction value grouped by shopper_interaction for crossfit_hanna's transactplus transactions between January and April 2023?"

### Max/Min Question
> "Looking at the year 2023, to which card scheme should the merchant golfclub_baron_friso steer traffic to in order to pay the maximum fees?"

---

## References

- DABStep Dataset: https://huggingface.co/datasets/adyen/DABstep
- Official Scorer: https://huggingface.co/spaces/adyen/DABstep/blob/main/dabstep_benchmark/evaluation/scorer.py
- Adapter Code: `evaluation/adapters/dabstep.py`

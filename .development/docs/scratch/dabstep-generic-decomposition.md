# DABStep Generic Task Decomposition Strategy

**Analysis Date**: January 15, 2026
**Dataset**: DABStep (Data Agent Benchmark for Multi-step Reasoning)
**Total Tasks Analyzed**: 450 tasks

## Executive Summary

This document provides a comprehensive analysis of the DABStep benchmark dataset and proposes a **generic, task-agnostic decomposition strategy** that works across all 450 tasks. The strategy is designed to be domain-independent and applicable to any structured data analysis task.

## 1. Dataset Statistics

### 1.1 Task Distribution

| Metric | Value |
|--------|-------|
| **Total Tasks** | 450 |
| **Easy Tasks** | 72 (16.0%) |
| **Hard Tasks** | 378 (84.0%) |
| **Avg Question Length** | 131.9 characters |
| **Min Question Length** | 44 characters |
| **Max Question Length** | 323 characters |

### 1.2 Question Type Distribution

| Question Type | Count | Percentage |
|--------------|-------|------------|
| Other (complex multi-condition) | 159 | 35.3% |
| Counting/Aggregation | 108 | 24.0% |
| Identification | 69 | 15.3% |
| Summation | 46 | 10.2% |
| Statistical (average, mean) | 33 | 7.3% |
| Enumeration (list generation) | 30 | 6.7% |
| Boolean (yes/no) | 5 | 1.1% |

### 1.3 Question Starters

| Starter Word | Count | Percentage |
|--------------|-------|------------|
| "for" | 140 | 31.1% |
| "what" | 132 | 29.3% |
| "in" | 63 | 14.0% |
| "looking" | 35 | 7.8% |
| "during" | 22 | 4.9% |
| "imagine" | 20 | 4.4% |

### 1.4 Output Format Requirements

| Format Type | Count | Percentage |
|-------------|-------|------------|
| Allows "Not Applicable" | 450 | 100.0% |
| Decimal Precision Required | 258 | 57.3% |
| Numeric Output | 200 | 44.4% |
| List Output | 131 | 29.1% |
| Delimited List (comma-separated) | 111 | 24.7% |
| Name Output | 31 | 6.9% |
| Code Output | 25 | 5.6% |
| Boolean (yes/no) | 8 | 1.8% |

## 2. Common Data Resources (Always Available)

All 450 DABStep tasks have access to the **same 7 data files**:

### 2.1 Primary Data Source
1. **payments.csv** (138,236 rows, 21 columns)
   - Main transactional data
   - Columns: psp_reference, merchant, card_scheme, year, hour_of_day, minute_of_hour, day_of_year, is_credit, eur_amount, ip_country, issuing_country, device_type, ip_address, email_address, card_number, shopper_interaction, card_bin, has_fraudulent_dispute, is_refused_by_adyen, aci, acquirer_country

### 2.2 Reference/Lookup Data
2. **fees.json** (1,000 fee structures)
   - Complex conditional rules with multiple fields
   - Fields: ID, card_scheme, account_type, capture_delay, monthly_fraud_level, monthly_volume, merchant_category_code, is_credit, aci, fixed_amount, rate, intracountry

3. **merchant_data.json** (30 merchants)
   - Merchant metadata: merchant, capture_delay, acquirer, merchant_category_code, account_type

4. **acquirer_countries.csv** (8 rows)
   - Mapping: acquirer → country_code

5. **merchant_category_codes.csv** (769 rows)
   - Reference: mcc → description

### 2.3 Documentation
6. **manual.md** (22,126 characters)
   - Domain knowledge and business rules
   - Sections: Account Type, MCC, ACI, Fee structures, Fraud prevention, Best practices

7. **payments-readme.md** (1,719 characters)
   - Column documentation for payments.csv

## 3. Common Instruction Patterns

### 3.1 Universal Guidelines (100% of tasks)
- **Fallback requirement**: All tasks allow "Not Applicable" as a valid answer when question has no relevant answer

### 3.2 Top Guideline Templates

**Template 1** (102 tasks, 22.7%):
```
Answer must be a list of values in comma separated list, eg: a, b, c.
If the answer is an empty list, reply with an empty string.
If a question does not have a relevant or applicable answer for the task, please respond with 'Not Applicable'
```

**Template 2** (45 tasks, 10.0%):
```
Answer must be just a number rounded to 2 decimals.
If a question does not have a relevant or applicable answer for the task, please respond with 'Not Applicable'
```

**Template 3** (40 tasks, 8.9%):
```
Answer must be just a number rounded to 6 decimals.
If a question does not have a relevant or applicable answer for the task, please respond with 'Not Applicable'
```

**Template 4** (40 tasks, 8.9%):
```
Answer must be just a number expressed in EUR rounded to 6 decimals.
If a question does not have a relevant or applicable answer for the task, please respond with 'Not Applicable'
```

### 3.3 Common Constraints
- Decimal precision (2, 6, or 14 decimals)
- List formatting (comma-separated)
- Grouping and sorting requirements
- Empty list handling
- Not Applicable fallback

## 4. Common Task Patterns

### 4.1 Data Analysis Patterns

**Pattern 1: Simple Aggregation** (108 tasks, 24%)
- Count entities matching conditions
- Example: "How many transactions meet criteria X?"

**Pattern 2: Statistical Computation** (33 tasks, 7.3%)
- Calculate averages, means, totals
- Example: "What is the average value for entity type Y?"

**Pattern 3: Conditional Filtering** (45 tasks, 10%)
- Apply complex multi-condition filters
- Example: "What entities satisfy conditions A, B, and C?"

**Pattern 4: Rule-Based Matching** (121 tasks, 26.9%)
- Match against reference data with complex conditions
- Example: "What rules apply to entity X with properties Y and Z?"

**Pattern 5: Identification** (69 tasks, 15.3%)
- Find entities with max/min/specific properties
- Example: "Which entity has the highest/lowest metric M?"

**Pattern 6: Optimization** (30 tasks, 6.7%)
- Find best/worst option among alternatives
- Example: "What is the most/least expensive option?"

## 5. Generic Task Decomposition Framework

### 5.1 High-Level Strategy

The following **task-agnostic decomposition** works for ALL DABStep tasks:

```
Phase 1: UNDERSTAND THE QUESTION
├─ Step 1.1: Parse question structure and intent
├─ Step 1.2: Identify required output format from guidelines
└─ Step 1.3: Extract key entities, metrics, and conditions

Phase 2: DISCOVER AVAILABLE RESOURCES
├─ Step 2.1: List all available data sources
├─ Step 2.2: Read documentation files for domain knowledge
└─ Step 2.3: Identify relevant reference/lookup tables

Phase 3: MAP QUESTION TO DATA
├─ Step 3.1: Determine which data sources contain required information
├─ Step 3.2: Identify necessary joins/relationships between sources
└─ Step 3.3: Map question conditions to data fields

Phase 4: EXPLORE DATA SCHEMAS
├─ Step 4.1: Inspect primary data source structure (columns, types, samples)
├─ Step 4.2: Inspect reference data structures
└─ Step 4.3: Understand value domains and constraints

Phase 5: EXTRACT RELEVANT SUBSET
├─ Step 5.1: Apply temporal filters (date/time constraints)
├─ Step 5.2: Apply entity filters (specific merchants, categories, etc.)
└─ Step 5.3: Apply condition filters (multi-condition WHERE clauses)

Phase 6: APPLY DOMAIN RULES
├─ Step 6.1: Load rule definitions from reference data
├─ Step 6.2: Match entities against rule conditions
└─ Step 6.3: Apply business logic from documentation

Phase 7: COMPUTE RESULT
├─ Step 7.1: Perform aggregations (count, sum, average, etc.)
├─ Step 7.2: Apply ranking/sorting if required
└─ Step 7.3: Perform calculations or transformations

Phase 8: FORMAT OUTPUT
├─ Step 8.1: Round numeric results to required precision
├─ Step 8.2: Format lists with correct delimiters
├─ Step 8.3: Apply "Not Applicable" if no valid answer exists
└─ Step 8.4: Validate output matches guideline template
```

### 5.2 Detailed Step Descriptions

#### Phase 1: Understand the Question

**Generic Actions:**
1. Parse the question text to identify:
   - Intent (count, identify, calculate, list, compare, etc.)
   - Target entities (what are we analyzing?)
   - Metrics (what are we measuring?)
   - Conditions (what filters/constraints apply?)

2. Parse guidelines to extract:
   - Output data type (number, list, text, boolean)
   - Formatting requirements (decimals, delimiters, sorting)
   - Fallback behavior (empty list, Not Applicable)

3. Create a mental model:
   - "I need to [ACTION] the [METRIC] for [ENTITY] where [CONDITIONS]"

#### Phase 2: Discover Available Resources

**Generic Actions:**
1. List available data files in the data directory
2. Categorize files:
   - Primary transactional data (typically CSV with many rows)
   - Reference/lookup data (typically JSON or small CSV)
   - Documentation (typically .md files)
3. Read all documentation files FIRST to understand:
   - Domain concepts and terminology
   - Business rules and formulas
   - Data schemas and relationships

#### Phase 3: Map Question to Data

**Generic Actions:**
1. For each entity/condition in the question, identify:
   - Which data source(s) contain this information?
   - What fields/columns are relevant?
   - What joins/lookups are needed?

2. Create a data flow diagram (mental or written):
   - Start with primary data source
   - Identify necessary joins to reference data
   - Identify where conditions will be applied

3. Check for special cases:
   - Does the question reference documentation-only concepts?
   - Are there derived fields that need calculation?
   - Are there temporal aggregations needed?

#### Phase 4: Explore Data Schemas

**Generic Actions:**
1. For primary data source:
   - Load first 5-10 rows to understand structure
   - Print column names and data types
   - Identify key fields for filtering/grouping
   - Check for missing values

2. For each reference data source:
   - Load and inspect structure
   - Understand the schema (nested fields, lists, etc.)
   - Identify key fields for matching/joining

3. For documentation:
   - Extract relevant formulas, rules, or mappings
   - Create lookup tables if needed

#### Phase 5: Extract Relevant Subset

**Generic Actions:**
1. Start with primary data source
2. Apply filters in order of selectivity:
   - Temporal filters (year, month, date range)
   - Entity filters (specific IDs, names, categories)
   - Categorical filters (type, status, flags)
   - Numeric filters (amount ranges, thresholds)

3. Verify filtered subset:
   - Check row count is reasonable
   - Validate no unexpected empty results

#### Phase 6: Apply Domain Rules

**Generic Actions:**
1. If question involves rules/fees/conditions:
   - Load rule definitions from reference data
   - For each record in filtered subset:
     - Check all rule conditions (AND logic within rule)
     - Collect all matching rules
     - Apply rule logic (calculations, selections)

2. If question involves lookups:
   - Perform joins or lookups to reference data
   - Enrich primary data with reference attributes

3. If question involves documentation concepts:
   - Apply business logic from documentation
   - Use formulas or mappings from manual

#### Phase 7: Compute Result

**Generic Actions:**
1. Based on question intent:
   - **Counting**: `len(filtered_data)` or `filtered_data.groupby(...).count()`
   - **Summation**: `filtered_data[column].sum()` or `groupby().sum()`
   - **Statistical**: `mean()`, `median()`, `std()`, etc.
   - **Identification**: `idxmax()`, `idxmin()`, `unique()`
   - **Ranking**: `sort_values()` + `head()` or `tail()`
   - **Enumeration**: `unique()` or `groupby().groups.keys()`
   - **Boolean**: Compare computed metric to threshold

2. Handle grouping/aggregation:
   - Group by required dimensions
   - Apply aggregation functions
   - Sort if required

3. Handle edge cases:
   - Empty results → "Not Applicable" or empty list
   - Ties in ranking → use tie-breaking rules or return all
   - Division by zero → handle gracefully

#### Phase 8: Format Output

**Generic Actions:**
1. Apply numeric formatting:
   - Round to specified decimal places
   - Convert to string if needed
   - Add currency symbols if required

2. Apply list formatting:
   - Join items with correct delimiter (usually ", ")
   - Sort if required (ascending/descending)
   - Format: "A, B, C" or "[A, B, C]" based on guidelines

3. Apply special formats:
   - Key-value pairs: "key:value"
   - Grouped results: "[group1: value1, group2: value2]"
   - Boolean: "yes" or "no" (lowercase)

4. Apply fallback logic:
   - If result is empty/None/invalid → "Not Applicable"
   - If result is empty list → "" (empty string)

5. Final validation:
   - Does output match guideline format exactly?
   - Are decimals correct?
   - Is sorting correct?

## 6. Generic Decomposition Template (Pseudo-code)

```python
def decompose_task_generic(question, guidelines, data_dir):
    """
    Generic task decomposition that works for any data analysis task.

    Returns: List of concrete execution steps
    """
    steps = []

    # Phase 1: Understand Question
    steps.append({
        "phase": "understand_question",
        "actions": [
            "parse_question_intent(question)",
            "extract_entities_and_conditions(question)",
            "parse_output_format(guidelines)",
        ]
    })

    # Phase 2: Discover Resources
    steps.append({
        "phase": "discover_resources",
        "actions": [
            f"list_files({data_dir})",
            "categorize_files_by_type(file_list)",
            "read_all_documentation_files(docs)",
        ]
    })

    # Phase 3: Map Question to Data
    steps.append({
        "phase": "map_to_data",
        "actions": [
            "identify_primary_data_source(question, files)",
            "identify_reference_sources(question, files)",
            "plan_joins_and_lookups(question, sources)",
        ]
    })

    # Phase 4: Explore Schemas
    steps.append({
        "phase": "explore_schemas",
        "actions": [
            "inspect_primary_data_schema(primary_source)",
            "inspect_reference_schemas(reference_sources)",
            "extract_rules_from_documentation(docs)",
        ]
    })

    # Phase 5: Extract Subset
    steps.append({
        "phase": "filter_data",
        "actions": [
            "apply_temporal_filters(data, conditions)",
            "apply_entity_filters(data, conditions)",
            "apply_condition_filters(data, conditions)",
        ]
    })

    # Phase 6: Apply Rules
    steps.append({
        "phase": "apply_rules",
        "actions": [
            "load_rule_definitions(reference_sources)",
            "match_records_to_rules(filtered_data, rules)",
            "apply_business_logic(data, rules, documentation)",
        ]
    })

    # Phase 7: Compute Result
    steps.append({
        "phase": "compute",
        "actions": [
            "determine_computation_type(question_intent)",
            "perform_aggregation_or_calculation(data)",
            "apply_ranking_or_sorting(result)",
        ]
    })

    # Phase 8: Format Output
    steps.append({
        "phase": "format_output",
        "actions": [
            "round_numeric_values(result, guidelines)",
            "format_list_output(result, guidelines)",
            "apply_not_applicable_fallback(result)",
            "validate_output_format(result, guidelines)",
        ]
    })

    return steps
```

## 7. Example Applications

### 7.1 Example 1: Counting Task (Easy)

**Question**: "Which issuing country has the highest number of transactions?"

**Guidelines**: "Answer must be just the country code. If a question does not have a relevant or applicable answer for the task, please respond with 'Not Applicable'"

**Generic Decomposition Applied**:

| Phase | Concrete Steps |
|-------|----------------|
| **1. Understand** | - Intent: IDENTIFY (find entity with max metric)<br>- Entity: country<br>- Metric: transaction count<br>- Conditions: none<br>- Output: country code (string) |
| **2. Discover** | - List files: payments.csv, fees.json, manual.md, etc.<br>- Primary source: payments.csv<br>- Read manual.md for context |
| **3. Map to Data** | - Primary source: payments.csv<br>- Relevant field: issuing_country<br>- No joins needed |
| **4. Explore** | - Load payments.csv<br>- Inspect columns: issuing_country is present<br>- Check for nulls |
| **5. Extract** | - No filters needed (all transactions) |
| **6. Apply Rules** | - No rules needed |
| **7. Compute** | - Group by issuing_country<br>- Count transactions per group<br>- Find country with max count |
| **8. Format** | - Output country code as-is<br>- Result: "NL" |

### 7.2 Example 2: Fee Calculation Task (Hard)

**Question**: "For credit transactions, what would be the average fee that the card scheme GlobalCard would charge for a transaction value of 10 EUR?"

**Guidelines**: "Answer must be just a number expressed in EUR rounded to 6 decimals. If a question does not have a relevant or applicable answer for the task, please respond with 'Not Applicable'"

**Generic Decomposition Applied**:

| Phase | Concrete Steps |
|-------|----------------|
| **1. Understand** | - Intent: CALCULATE (compute average)<br>- Entity: fee rules<br>- Metric: fee amount<br>- Conditions: is_credit=True, card_scheme="GlobalCard", transaction_value=10<br>- Output: numeric (6 decimals) |
| **2. Discover** | - List files: fees.json, manual.md, etc.<br>- Primary source: fees.json<br>- Read manual.md for fee formula |
| **3. Map to Data** | - Primary source: fees.json<br>- Reference: manual.md (fee formula)<br>- No joins needed |
| **4. Explore** | - Load fees.json<br>- Inspect structure: ID, card_scheme, is_credit, fixed_amount, rate, etc.<br>- Extract formula from manual.md: `fee = fixed_amount + rate * transaction_value / 10000` |
| **5. Extract** | - Filter fees.json:<br>  - card_scheme == "GlobalCard" (or null)<br>  - is_credit == True (or null) |
| **6. Apply Rules** | - For each matching fee rule:<br>  - Calculate: fee = fixed_amount + rate * 10 / 10000 |
| **7. Compute** | - Compute average of all calculated fees |
| **8. Format** | - Round to 6 decimals<br>- Result: "0.120132" |

### 7.3 Example 3: Filtering Task (Hard)

**Question**: "What are the applicable fee IDs for Belles_cookbook_store in March 2023?"

**Guidelines**: "Answer must be a list of values in comma separated list, eg: A, B, C. If the answer is an empty list, reply with an empty string. If a question does not have a relevant or applicable answer for the task, please respond with 'Not Applicable'"

**Generic Decomposition Applied**:

| Phase | Concrete Steps |
|-------|----------------|
| **1. Understand** | - Intent: FILTER + ENUMERATE (list matching IDs)<br>- Entity: fee rules<br>- Metric: fee IDs<br>- Conditions: merchant="Belles_cookbook_store", month=March, year=2023<br>- Output: comma-separated list |
| **2. Discover** | - List files: payments.csv, fees.json, merchant_data.json, manual.md<br>- Primary source: fees.json<br>- Supporting: payments.csv, merchant_data.json<br>- Read manual.md for applicability rules |
| **3. Map to Data** | - Need merchant metadata from merchant_data.json<br>- Need transaction stats from payments.csv (for monthly_volume, monthly_fraud_level)<br>- Need to match against fees.json rules |
| **4. Explore** | - Load merchant_data.json → get merchant properties (account_type, MCC, capture_delay)<br>- Load payments.csv → filter for merchant + March 2023<br>- Load fees.json → inspect rule conditions |
| **5. Extract** | - Filter payments.csv:<br>  - merchant == "Belles_cookbook_store"<br>  - day_of_year between 60 and 90 (March)<br>  - year == 2023<br>- Compute monthly_volume (sum of amounts)<br>- Compute monthly_fraud_level (fraud ratio) |
| **6. Apply Rules** | - For each fee rule in fees.json:<br>  - Check if rule applies (match ALL conditions):<br>    - card_scheme matches (or null)<br>    - account_type matches (or null)<br>    - merchant_category_code matches (or null)<br>    - capture_delay matches (or null)<br>    - monthly_volume in range (or null)<br>    - monthly_fraud_level in range (or null)<br>    - etc.<br>  - Collect matching fee IDs |
| **7. Compute** | - Extract unique fee IDs from matches<br>- Sort in ascending order |
| **8. Format** | - Join with ", "<br>- Result: "384, 394, 276, 150, ..." |

## 8. Key Insights for Generic Decomposition

### 8.1 Task-Agnostic Principles

1. **Always read documentation first** - Domain knowledge is critical for 84% of tasks (hard difficulty)

2. **Schema inspection is mandatory** - Never assume data structure

3. **Conditions are compositional** - Most tasks have multiple AND conditions that must all be satisfied

4. **Rules use nullable fields** - In rule matching, `null` means "applies to all values"

5. **Output format is strictly specified** - Guidelines must be followed exactly (100% of tasks have specific format requirements)

6. **Fallback behavior is universal** - All tasks allow "Not Applicable" response

7. **Temporal aggregation is common** - Many tasks aggregate by natural months (not 30-day windows)

### 8.2 Common Pitfalls to Avoid

1. **Don't print entire datasets** - Always use `.head()` or limits when inspecting data
2. **Don't skip documentation** - Manual contains formulas and business rules not in data
3. **Don't assume field semantics** - Read column documentation (payments-readme.md)
4. **Don't ignore null handling** - Null in rules means "applies to all"
5. **Don't hardcode format** - Parse guidelines programmatically for decimal places, delimiters
6. **Don't compute mentally** - Always write and execute code (per DABStep requirements)

### 8.3 Domain-Independent Vocabulary

The generic decomposition uses these task-agnostic terms:

| Generic Term | DABStep Examples |
|--------------|------------------|
| Entity | merchant, transaction, fee rule, country |
| Metric | transaction count, fee amount, fraud rate, volume |
| Condition | temporal filter, entity ID, categorical match, numeric range |
| Reference data | fees.json, merchant_data.json, MCCs, acquirers |
| Documentation | manual.md, payments-readme.md |
| Rule | fee rule, fraud threshold, business constraint |
| Aggregation | count, sum, average, min, max |
| Grouping | by country, by card scheme, by merchant |

## 9. Validation and Success Criteria

### 9.1 Decomposition Quality Checklist

A well-formed generic decomposition should:

- [ ] Work for any question type (counting, calculation, identification, etc.)
- [ ] Not contain domain-specific keywords (fee, merchant, fraud, etc.)
- [ ] Use generic terms (entity, metric, condition, reference data)
- [ ] Follow all 8 phases in order
- [ ] Handle edge cases (empty results, Not Applicable)
- [ ] Respect output format requirements
- [ ] Include schema inspection before data manipulation
- [ ] Read documentation before applying business logic

### 9.2 Expected Outcomes

When applied correctly, this generic decomposition should:

1. **Achieve high task coverage** - Work for 100% of DABStep's 450 tasks
2. **Be transferable** - Apply to other data analysis benchmarks beyond DABStep
3. **Be teachable** - Clear enough for LLMs to follow without task-specific fine-tuning
4. **Be debuggable** - Clear phase boundaries make it easy to identify failure points
5. **Be extensible** - New task types can be accommodated by extending existing phases

## 10. Conclusion

The DABStep benchmark reveals that data analysis tasks, despite their apparent diversity, follow **predictable structural patterns**. By analyzing all 450 tasks, we identified 8 generic phases that apply universally:

1. Understand the question
2. Discover available resources
3. Map question to data
4. Explore data schemas
5. Extract relevant subset
6. Apply domain rules
7. Compute result
8. Format output

This framework is **task-agnostic** and uses **domain-independent vocabulary**, making it suitable for:
- Automated task decomposition systems
- LLM prompt engineering
- Agent planning systems
- Transfer learning to other data analysis benchmarks

The key insight is that **all data analysis tasks are fundamentally the same**: understand what's being asked, find the relevant data, filter and transform it, compute the answer, and format it correctly. Domain-specific knowledge (fees, merchants, fraud) is data, not process.

## Appendix A: Full Dataset Statistics

- Total tasks analyzed: 450
- Data files available: 7 (always the same across all tasks)
- Unique guideline templates: 38
- Task types identified: 7 major categories
- Common keywords: 40 domain-specific terms
- Output formats: 8 distinct format patterns
- Difficulty levels: 2 (easy 16%, hard 84%)

## Appendix B: File Locations

- Full dataset: `/Users/rcabral/.cache/dabstep/data/context/`
- Analysis results: `/Users/rcabral/nemo_oo_agents/experiments/dabstep-analysis/`
  - `dabstep_full_450_tasks.json` - All 450 task details
  - `dabstep_statistics.json` - Statistical summary
- Adapter implementation: `/Users/rcabral/nemo_oo_agents/evaluation/adapters/dabstep.py`

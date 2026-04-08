# Capability Test Comparison Report

## Overview

- **Main timestamp**: 2026-01-27T12:46:09.635009
- **Branch timestamp**: 2026-01-27T12:46:17.793949
- **Runs per sample**: 10

## Summary

| Metric | Main | Branch | Delta |
|--------|------|--------|-------|
| Overall Pass Rate | 66.0% (1940/2940) | 66.2% (1945/2940) | +0.2% |

## 1. Pass Rate Changes by Test Type

| Test Type | Main | Branch | Delta |
|-----------|------|--------|-------|
| calculate_batch | 58.3% (35/60) | 43.3% (26/60) | -15.0% |
| employee_lookup | 58.3% (35/60) | 50.0% (30/60) | -8.3% |
| repl_exploration | 70.0% (42/60) | 61.7% (37/60) | -8.3% |
| large_data_count_generated | 76.7% (46/60) | 71.7% (43/60) | -5.0% |
| json_extract | 11.7% (7/60) | 8.3% (5/60) | -3.3% |
| calculate_complex | 84.4% (152/180) | 81.1% (146/180) | -3.3% |
| large_data_extract_generated | 90.0% (54/60) | 86.7% (52/60) | -3.3% |
| context_notes | 1.7% (1/60) | 0.0% (0/60) | -1.7% |
| needle_in_haystack | 73.3% (44/60) | 71.7% (43/60) | -1.7% |
| calculate_simple | 93.9% (169/180) | 93.3% (168/180) | -0.6% |
| fast_food_order | 47.2% (170/360) | 46.7% (168/360) | -0.6% |
| json_qa | 43.8% (263/600) | 43.8% (263/600) | 0.0% |
| router_multi_analyze_validate | 95.0% (57/60) | 95.0% (57/60) | 0.0% |
| sentiment_single | 93.3% (224/240) | 93.8% (225/240) | +0.4% |
| fast_food_cancel | 87.5% (105/120) | 89.2% (107/120) | +1.7% |
| router_transform | 92.5% (111/120) | 94.2% (113/120) | +1.7% |
| router_analyze | 88.3% (106/120) | 91.7% (110/120) | +3.3% |
| router_multi_transform_validate | 86.7% (52/60) | 90.0% (54/60) | +3.3% |
| large_data_find_generated | 85.8% (103/120) | 89.2% (107/120) | +3.3% |
| sentiment_batch | 3.3% (2/60) | 8.3% (5/60) | +5.0% |
| error_recovery | 70.0% (42/60) | 76.7% (46/60) | +6.7% |
| refinement | 38.3% (23/60) | 46.7% (28/60) | +8.3% |
| router_validate | 80.8% (97/120) | 93.3% (112/120) | +12.5% |

## 2. Pass Rate Changes by Sample

### Samples with Changes

| Sample | Model | Main | Branch | Delta |
|--------|-------|------|--------|-------|
| calculate_complex_003 | nemotron3-nano-30b | 60% (6/10) | 0% (0/10) | -60.0% |
| json_qa_004 | qwen3-80b | 70% (7/10) | 10% (1/10) | -60.0% |
| calculate_batch_001 | gpt-oss-120b | 80% (8/10) | 30% (3/10) | -50.0% |
| json_qa_003 | qwen3-80b | 80% (8/10) | 30% (3/10) | -50.0% |
| json_qa_009 | qwen3-80b | 50% (5/10) | 0% (0/10) | -50.0% |
| repl_exploration_001 | gemini-2.5-flash-lite | 60% (6/10) | 10% (1/10) | -50.0% |
| calculate_complex_002 | nemotron3-nano-30b | 60% (6/10) | 30% (3/10) | -30.0% |
| employee_lookup_001 | qwen3-80b | 80% (8/10) | 50% (5/10) | -30.0% |
| fast_food_order_003 | nemotron3-nano-30b | 90% (9/10) | 60% (6/10) | -30.0% |
| json_extract_001 | nemotron3-nano-30b | 70% (7/10) | 40% (4/10) | -30.0% |
| large_data_count_generated_001 | qwen3-80b | 40% (4/10) | 10% (1/10) | -30.0% |
| sentiment_single_001 | gpt-oss-120b | 100% (10/10) | 70% (7/10) | -30.0% |
| calculate_batch_001 | claude-haiku | 90% (9/10) | 70% (7/10) | -20.0% |
| error_recovery_001 | gemini-2.5-flash-lite | 70% (7/10) | 50% (5/10) | -20.0% |
| json_qa_004 | gemini-2.5-flash-lite | 100% (10/10) | 80% (8/10) | -20.0% |
| large_data_extract_generated_001 | nemotron3-nano-30b | 80% (8/10) | 60% (6/10) | -20.0% |
| large_data_find_generated_001 | gpt-oss-120b | 100% (10/10) | 80% (8/10) | -20.0% |
| needle_in_haystack_001 | qwen3-80b | 80% (8/10) | 60% (6/10) | -20.0% |
| repl_exploration_001 | qwen3-80b | 90% (9/10) | 70% (7/10) | -20.0% |
| sentiment_single_002 | nemotron3-nano-30b | 100% (10/10) | 80% (8/10) | -20.0% |
| calculate_batch_001 | gemini-2.5-flash-lite | 60% (6/10) | 50% (5/10) | -10.0% |
| calculate_batch_001 | qwen3-80b | 10% (1/10) | 0% (0/10) | -10.0% |
| calculate_complex_001 | claude-haiku | 100% (10/10) | 90% (9/10) | -10.0% |
| calculate_complex_001 | gemini-2.5-flash-lite | 100% (10/10) | 90% (9/10) | -10.0% |
| calculate_complex_002 | claude-sonnet | 100% (10/10) | 90% (9/10) | -10.0% |
| calculate_complex_002 | gemini-2.5-flash-lite | 90% (9/10) | 80% (8/10) | -10.0% |
| calculate_complex_002 | gpt-oss-120b | 100% (10/10) | 90% (9/10) | -10.0% |
| calculate_simple_001 | gemini-2.5-flash-lite | 100% (10/10) | 90% (9/10) | -10.0% |
| calculate_simple_002 | gemini-2.5-flash-lite | 100% (10/10) | 90% (9/10) | -10.0% |
| calculate_simple_002 | qwen3-80b | 100% (10/10) | 90% (9/10) | -10.0% |
| calculate_simple_003 | claude-haiku | 100% (10/10) | 90% (9/10) | -10.0% |
| calculate_simple_003 | gemini-2.5-flash-lite | 100% (10/10) | 90% (9/10) | -10.0% |
| calculate_simple_003 | qwen3-80b | 100% (10/10) | 90% (9/10) | -10.0% |
| context_notes_001 | claude-haiku | 10% (1/10) | 0% (0/10) | -10.0% |
| employee_lookup_001 | gemini-2.5-flash-lite | 30% (3/10) | 20% (2/10) | -10.0% |
| employee_lookup_001 | nemotron3-nano-30b | 30% (3/10) | 20% (2/10) | -10.0% |
| error_recovery_001 | claude-haiku | 80% (8/10) | 70% (7/10) | -10.0% |
| fast_food_cancel_001 | gpt-oss-120b | 100% (10/10) | 90% (9/10) | -10.0% |
| fast_food_cancel_001 | qwen3-80b | 100% (10/10) | 90% (9/10) | -10.0% |
| fast_food_order_001 | claude-haiku | 100% (10/10) | 90% (9/10) | -10.0% |
| fast_food_order_001 | gpt-oss-120b | 80% (8/10) | 70% (7/10) | -10.0% |
| fast_food_order_001 | nemotron3-nano-30b | 80% (8/10) | 70% (7/10) | -10.0% |
| fast_food_order_001 | qwen3-80b | 40% (4/10) | 30% (3/10) | -10.0% |
| fast_food_order_003 | claude-haiku | 100% (10/10) | 90% (9/10) | -10.0% |
| fast_food_order_003 | claude-sonnet | 100% (10/10) | 90% (9/10) | -10.0% |
| fast_food_order_004 | claude-sonnet | 80% (8/10) | 70% (7/10) | -10.0% |
| fast_food_order_004 | gpt-oss-120b | 50% (5/10) | 40% (4/10) | -10.0% |
| fast_food_order_004 | nemotron3-nano-30b | 70% (7/10) | 60% (6/10) | -10.0% |
| fast_food_order_004 | qwen3-80b | 20% (2/10) | 10% (1/10) | -10.0% |
| fast_food_order_005 | claude-haiku | 100% (10/10) | 90% (9/10) | -10.0% |
| fast_food_order_005 | claude-sonnet | 80% (8/10) | 70% (7/10) | -10.0% |
| fast_food_order_005 | gemini-2.5-flash-lite | 30% (3/10) | 20% (2/10) | -10.0% |
| fast_food_order_005 | gpt-oss-120b | 60% (6/10) | 50% (5/10) | -10.0% |
| json_qa_001 | gemini-2.5-flash-lite | 80% (8/10) | 70% (7/10) | -10.0% |
| json_qa_003 | gemini-2.5-flash-lite | 90% (9/10) | 80% (8/10) | -10.0% |
| json_qa_004 | nemotron3-nano-30b | 90% (9/10) | 80% (8/10) | -10.0% |
| json_qa_007 | gemini-2.5-flash-lite | 90% (9/10) | 80% (8/10) | -10.0% |
| json_qa_009 | claude-haiku | 10% (1/10) | 0% (0/10) | -10.0% |
| json_qa_010 | claude-haiku | 10% (1/10) | 0% (0/10) | -10.0% |
| large_data_extract_generated_001 | claude-sonnet | 100% (10/10) | 90% (9/10) | -10.0% |
| large_data_extract_generated_001 | gpt-oss-120b | 90% (9/10) | 80% (8/10) | -10.0% |
| large_data_find_generated_002 | claude-sonnet | 100% (10/10) | 90% (9/10) | -10.0% |
| refinement_001 | gpt-oss-120b | 10% (1/10) | 0% (0/10) | -10.0% |
| repl_exploration_001 | gpt-oss-120b | 50% (5/10) | 40% (4/10) | -10.0% |
| router_analyze_001 | claude-haiku | 100% (10/10) | 90% (9/10) | -10.0% |
| router_analyze_002 | gpt-oss-120b | 70% (7/10) | 60% (6/10) | -10.0% |
| router_transform_001 | gpt-oss-120b | 80% (8/10) | 70% (7/10) | -10.0% |
| router_transform_002 | nemotron3-nano-30b | 100% (10/10) | 90% (9/10) | -10.0% |
| sentiment_batch_001 | claude-sonnet | 10% (1/10) | 0% (0/10) | -10.0% |
| sentiment_batch_001 | gpt-oss-120b | 10% (1/10) | 0% (0/10) | -10.0% |
| sentiment_single_001 | claude-haiku | 100% (10/10) | 90% (9/10) | -10.0% |
| sentiment_single_001 | claude-sonnet | 100% (10/10) | 90% (9/10) | -10.0% |
| sentiment_single_002 | gpt-oss-120b | 100% (10/10) | 90% (9/10) | -10.0% |
| sentiment_single_003 | gpt-oss-120b | 100% (10/10) | 90% (9/10) | -10.0% |
| calculate_complex_003 | claude-haiku | 90% (9/10) | 100% (10/10) | +10.0% |
| calculate_complex_003 | claude-sonnet | 90% (9/10) | 100% (10/10) | +10.0% |
| calculate_complex_003 | gemini-2.5-flash-lite | 90% (9/10) | 100% (10/10) | +10.0% |
| calculate_complex_003 | gpt-oss-120b | 70% (7/10) | 80% (8/10) | +10.0% |
| calculate_complex_003 | qwen3-80b | 90% (9/10) | 100% (10/10) | +10.0% |
| calculate_simple_001 | claude-sonnet | 90% (9/10) | 100% (10/10) | +10.0% |
| calculate_simple_002 | claude-sonnet | 90% (9/10) | 100% (10/10) | +10.0% |
| calculate_simple_002 | nemotron3-nano-30b | 90% (9/10) | 100% (10/10) | +10.0% |
| calculate_simple_003 | claude-sonnet | 90% (9/10) | 100% (10/10) | +10.0% |
| calculate_simple_003 | nemotron3-nano-30b | 90% (9/10) | 100% (10/10) | +10.0% |
| error_recovery_001 | gpt-oss-120b | 70% (7/10) | 80% (8/10) | +10.0% |
| error_recovery_001 | qwen3-80b | 80% (8/10) | 90% (9/10) | +10.0% |
| fast_food_cancel_001 | gemini-2.5-flash-lite | 80% (8/10) | 90% (9/10) | +10.0% |
| fast_food_cancel_002 | gemini-2.5-flash-lite | 70% (7/10) | 80% (8/10) | +10.0% |
| fast_food_cancel_002 | nemotron3-nano-30b | 80% (8/10) | 90% (9/10) | +10.0% |
| fast_food_cancel_002 | qwen3-80b | 80% (8/10) | 90% (9/10) | +10.0% |
| fast_food_order_002 | gemini-2.5-flash-lite | 0% (0/10) | 10% (1/10) | +10.0% |
| fast_food_order_003 | gpt-oss-120b | 70% (7/10) | 80% (8/10) | +10.0% |
| fast_food_order_004 | claude-haiku | 80% (8/10) | 90% (9/10) | +10.0% |
| fast_food_order_005 | qwen3-80b | 0% (0/10) | 10% (1/10) | +10.0% |
| fast_food_order_006 | gpt-oss-120b | 40% (4/10) | 50% (5/10) | +10.0% |
| json_extract_001 | gemini-2.5-flash-lite | 0% (0/10) | 10% (1/10) | +10.0% |
| json_qa_001 | gpt-oss-120b | 90% (9/10) | 100% (10/10) | +10.0% |
| json_qa_002 | nemotron3-nano-30b | 90% (9/10) | 100% (10/10) | +10.0% |
| json_qa_003 | nemotron3-nano-30b | 90% (9/10) | 100% (10/10) | +10.0% |
| json_qa_005 | gpt-oss-120b | 70% (7/10) | 80% (8/10) | +10.0% |
| json_qa_005 | nemotron3-nano-30b | 70% (7/10) | 80% (8/10) | +10.0% |
| json_qa_007 | claude-haiku | 0% (0/10) | 10% (1/10) | +10.0% |
| json_qa_008 | gpt-oss-120b | 70% (7/10) | 80% (8/10) | +10.0% |
| json_qa_009 | gpt-oss-120b | 90% (9/10) | 100% (10/10) | +10.0% |
| large_data_extract_generated_001 | claude-haiku | 90% (9/10) | 100% (10/10) | +10.0% |
| large_data_extract_generated_001 | gemini-2.5-flash-lite | 80% (8/10) | 90% (9/10) | +10.0% |
| large_data_find_generated_001 | gemini-2.5-flash-lite | 70% (7/10) | 80% (8/10) | +10.0% |
| large_data_find_generated_001 | nemotron3-nano-30b | 80% (8/10) | 90% (9/10) | +10.0% |
| large_data_find_generated_002 | gpt-oss-120b | 90% (9/10) | 100% (10/10) | +10.0% |
| needle_in_haystack_001 | claude-haiku | 80% (8/10) | 90% (9/10) | +10.0% |
| refinement_001 | gemini-2.5-flash-lite | 0% (0/10) | 10% (1/10) | +10.0% |
| refinement_001 | qwen3-80b | 20% (2/10) | 30% (3/10) | +10.0% |
| router_analyze_001 | gemini-2.5-flash-lite | 90% (9/10) | 100% (10/10) | +10.0% |
| router_analyze_001 | nemotron3-nano-30b | 90% (9/10) | 100% (10/10) | +10.0% |
| router_analyze_001 | qwen3-80b | 90% (9/10) | 100% (10/10) | +10.0% |
| router_analyze_002 | gemini-2.5-flash-lite | 90% (9/10) | 100% (10/10) | +10.0% |
| router_analyze_002 | nemotron3-nano-30b | 90% (9/10) | 100% (10/10) | +10.0% |
| router_analyze_002 | qwen3-80b | 90% (9/10) | 100% (10/10) | +10.0% |
| router_multi_transform_validate_001 | claude-haiku | 90% (9/10) | 100% (10/10) | +10.0% |
| router_multi_transform_validate_001 | qwen3-80b | 80% (8/10) | 90% (9/10) | +10.0% |
| router_transform_002 | qwen3-80b | 60% (6/10) | 70% (7/10) | +10.0% |
| router_validate_001 | claude-haiku | 90% (9/10) | 100% (10/10) | +10.0% |
| router_validate_001 | claude-sonnet | 90% (9/10) | 100% (10/10) | +10.0% |
| router_validate_001 | nemotron3-nano-30b | 90% (9/10) | 100% (10/10) | +10.0% |
| router_validate_001 | qwen3-80b | 90% (9/10) | 100% (10/10) | +10.0% |
| router_validate_002 | nemotron3-nano-30b | 80% (8/10) | 90% (9/10) | +10.0% |
| sentiment_single_001 | gemini-2.5-flash-lite | 90% (9/10) | 100% (10/10) | +10.0% |
| sentiment_single_002 | claude-haiku | 90% (9/10) | 100% (10/10) | +10.0% |
| sentiment_single_002 | claude-sonnet | 90% (9/10) | 100% (10/10) | +10.0% |
| sentiment_single_004 | gpt-oss-120b | 90% (9/10) | 100% (10/10) | +10.0% |
| error_recovery_001 | claude-sonnet | 80% (8/10) | 100% (10/10) | +20.0% |
| fast_food_order_003 | qwen3-80b | 20% (2/10) | 40% (4/10) | +20.0% |
| json_qa_006 | gpt-oss-120b | 60% (6/10) | 80% (8/10) | +20.0% |
| json_qa_007 | nemotron3-nano-30b | 60% (6/10) | 80% (8/10) | +20.0% |
| json_qa_009 | gemini-2.5-flash-lite | 70% (7/10) | 90% (9/10) | +20.0% |
| json_qa_010 | gpt-oss-120b | 80% (8/10) | 100% (10/10) | +20.0% |
| large_data_find_generated_002 | gemini-2.5-flash-lite | 70% (7/10) | 90% (9/10) | +20.0% |
| large_data_find_generated_002 | nemotron3-nano-30b | 70% (7/10) | 90% (9/10) | +20.0% |
| refinement_001 | claude-haiku | 60% (6/10) | 80% (8/10) | +20.0% |
| refinement_001 | nemotron3-nano-30b | 40% (4/10) | 60% (6/10) | +20.0% |
| calculate_complex_001 | nemotron3-nano-30b | 30% (3/10) | 60% (6/10) | +30.0% |
| error_recovery_001 | nemotron3-nano-30b | 40% (4/10) | 70% (7/10) | +30.0% |
| json_qa_010 | gemini-2.5-flash-lite | 60% (6/10) | 90% (9/10) | +30.0% |
| repl_exploration_001 | nemotron3-nano-30b | 40% (4/10) | 70% (7/10) | +30.0% |
| router_transform_002 | gpt-oss-120b | 70% (7/10) | 100% (10/10) | +30.0% |
| router_validate_002 | gpt-oss-120b | 50% (5/10) | 80% (8/10) | +30.0% |
| router_validate_002 | qwen3-80b | 40% (4/10) | 70% (7/10) | +30.0% |
| sentiment_single_003 | nemotron3-nano-30b | 70% (7/10) | 100% (10/10) | +30.0% |
| sentiment_single_003 | qwen3-80b | 60% (6/10) | 90% (9/10) | +30.0% |
| fast_food_order_003 | gemini-2.5-flash-lite | 40% (4/10) | 80% (8/10) | +40.0% |
| fast_food_order_006 | claude-haiku | 40% (4/10) | 80% (8/10) | +40.0% |
| router_validate_001 | gpt-oss-120b | 40% (4/10) | 80% (8/10) | +40.0% |
| json_qa_008 | gemini-2.5-flash-lite | 50% (5/10) | 100% (10/10) | +50.0% |
| sentiment_batch_001 | claude-haiku | 0% (0/10) | 50% (5/10) | +50.0% |

## 3. RCA Prompts for Top Changes

### Top 3 Most Degraded Test Types

#### 1. calculate_batch (-15.0%)

**RCA Prompt for Agent:**

```
TASK: Root Cause Analysis for calculate_batch regression

REGRESSION: Pass rate dropped from 58.3% to 43.3% (-15.0%)
Main: 35/60 passed
Branch: 26/60 passed

INSTRUCTIONS:
1. Use @packages/trace_explorer/ to analyze traces from both runs
2. Find WHAT behavior changed between main and branch traces
3. Hypothesize WHY the context changes led to the behavior change (five-whys style)
4. Provide deep analysis of the causal chain
5. Document feedback on trace_explorer as a tool

TRACE LOCATIONS:
- Main traces: /Volumes/dev/dev/viewer/results/capability_optimization_20260127_124609/traces/
- Branch traces: /Volumes/dev/dev/agent006/results/capability_optimization_20260127_124617/traces/
- Filter for test type: calculate_batch

WRITE REPORT TO: docs/scratch/rca-calculate-batch-regression.md
```

#### 2. employee_lookup (-8.3%)

**RCA Prompt for Agent:**

```
TASK: Root Cause Analysis for employee_lookup regression

REGRESSION: Pass rate dropped from 58.3% to 50.0% (-8.3%)
Main: 35/60 passed
Branch: 30/60 passed

INSTRUCTIONS:
1. Use @packages/trace_explorer/ to analyze traces from both runs
2. Find WHAT behavior changed between main and branch traces
3. Hypothesize WHY the context changes led to the behavior change (five-whys style)
4. Provide deep analysis of the causal chain
5. Document feedback on trace_explorer as a tool

TRACE LOCATIONS:
- Main traces: /Volumes/dev/dev/viewer/results/capability_optimization_20260127_124609/traces/
- Branch traces: /Volumes/dev/dev/agent006/results/capability_optimization_20260127_124617/traces/
- Filter for test type: employee_lookup

WRITE REPORT TO: docs/scratch/rca-employee-lookup-regression.md
```

#### 3. repl_exploration (-8.3%)

**RCA Prompt for Agent:**

```
TASK: Root Cause Analysis for repl_exploration regression

REGRESSION: Pass rate dropped from 70.0% to 61.7% (-8.3%)
Main: 42/60 passed
Branch: 37/60 passed

INSTRUCTIONS:
1. Use @packages/trace_explorer/ to analyze traces from both runs
2. Find WHAT behavior changed between main and branch traces
3. Hypothesize WHY the context changes led to the behavior change (five-whys style)
4. Provide deep analysis of the causal chain
5. Document feedback on trace_explorer as a tool

TRACE LOCATIONS:
- Main traces: /Volumes/dev/dev/viewer/results/capability_optimization_20260127_124609/traces/
- Branch traces: /Volumes/dev/dev/agent006/results/capability_optimization_20260127_124617/traces/
- Filter for test type: repl_exploration

WRITE REPORT TO: docs/scratch/rca-repl-exploration-regression.md
```

### Top 3 Most Improved Test Types

#### 1. router_validate (+12.5%)

**RCA Prompt for Agent:**

```
TASK: Root Cause Analysis for router_validate improvement

IMPROVEMENT: Pass rate increased from 80.8% to 93.3% (+12.5%)
Main: 97/120 passed
Branch: 112/120 passed

INSTRUCTIONS:
1. Use @packages/trace_explorer/ to analyze traces from both runs
2. Find WHAT behavior changed between main and branch traces
3. Hypothesize WHY the context changes led to the behavior change (five-whys style)
4. Provide deep analysis of the causal chain
5. Document feedback on trace_explorer as a tool

TRACE LOCATIONS:
- Main traces: /Volumes/dev/dev/viewer/results/capability_optimization_20260127_124609/traces/
- Branch traces: /Volumes/dev/dev/agent006/results/capability_optimization_20260127_124617/traces/
- Filter for test type: router_validate

WRITE REPORT TO: docs/scratch/rca-router-validate-improvement.md
```

#### 2. refinement (+8.3%)

**RCA Prompt for Agent:**

```
TASK: Root Cause Analysis for refinement improvement

IMPROVEMENT: Pass rate increased from 38.3% to 46.7% (+8.3%)
Main: 23/60 passed
Branch: 28/60 passed

INSTRUCTIONS:
1. Use @packages/trace_explorer/ to analyze traces from both runs
2. Find WHAT behavior changed between main and branch traces
3. Hypothesize WHY the context changes led to the behavior change (five-whys style)
4. Provide deep analysis of the causal chain
5. Document feedback on trace_explorer as a tool

TRACE LOCATIONS:
- Main traces: /Volumes/dev/dev/viewer/results/capability_optimization_20260127_124609/traces/
- Branch traces: /Volumes/dev/dev/agent006/results/capability_optimization_20260127_124617/traces/
- Filter for test type: refinement

WRITE REPORT TO: docs/scratch/rca-refinement-improvement.md
```

#### 3. error_recovery (+6.7%)

**RCA Prompt for Agent:**

```
TASK: Root Cause Analysis for error_recovery improvement

IMPROVEMENT: Pass rate increased from 70.0% to 76.7% (+6.7%)
Main: 42/60 passed
Branch: 46/60 passed

INSTRUCTIONS:
1. Use @packages/trace_explorer/ to analyze traces from both runs
2. Find WHAT behavior changed between main and branch traces
3. Hypothesize WHY the context changes led to the behavior change (five-whys style)
4. Provide deep analysis of the causal chain
5. Document feedback on trace_explorer as a tool

TRACE LOCATIONS:
- Main traces: /Volumes/dev/dev/viewer/results/capability_optimization_20260127_124609/traces/
- Branch traces: /Volumes/dev/dev/agent006/results/capability_optimization_20260127_124617/traces/
- Filter for test type: error_recovery

WRITE REPORT TO: docs/scratch/rca-error-recovery-improvement.md
```

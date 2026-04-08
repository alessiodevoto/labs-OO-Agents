# Capability Test Branch Comparison Report

**Generated**: Tue Jan 28 00:30:00 CET 2026
**Diff Stack Branch**: `history-phases-1-3-unified-validator`
**Comparison**: Diff Stack vs Main (10 runs per sample)

## Summary

| Metric | Diff Stack | Main | Delta |
|--------|------------|------|-------|
| Total Results | 2940 | 2940 | - |
| Passed | 1976 | 2077 | -101 |
| **Overall Pass Rate** | **67.2%** | **70.6%** | **-3.4%** |

---

## Table 1: Test Type Pass Rates (Sorted: Most Degraded → Most Improved)

| Test Type | Diff Stack | Main | Delta |
|-----------|------------|------|-------|
| router_multi_transform_validate | 6.7% | 93.3% | **-86.7%** |
| router_transform | 54.2% | 85.8% | **-31.7%** |
| router_validate | 53.3% | 81.7% | **-28.3%** |
| refinement | 36.7% | 53.3% | **-16.7%** |
| calculate_batch | 66.7% | 75.0% | **-8.3%** |
| error_recovery | 83.3% | 85.0% | -1.7% |
| router_multi_analyze_validate | 96.7% | 98.3% | -1.7% |
| json_qa | 48.8% | 50.3% | -1.5% |
| context_notes | 0.0% | 0.0% | 0.0% |
| calculate_simple | 100.0% | 99.4% | +0.6% |
| sentiment_batch | 5.0% | 3.3% | +1.7% |
| large_data_find_generated | 95.8% | 94.2% | +1.7% |
| repl_exploration | 75.0% | 73.3% | +1.7% |
| employee_lookup | 71.7% | 70.0% | +1.7% |
| calculate_complex | 94.4% | 92.2% | +2.2% |
| fast_food_order | 52.8% | 50.0% | +2.8% |
| needle_in_haystack | 80.0% | 76.7% | +3.3% |
| large_data_extract_generated | 91.7% | 88.3% | +3.3% |
| sentiment_single | 98.3% | 94.6% | +3.7% |
| router_analyze | 100.0% | 95.8% | **+4.2%** |
| fast_food_cancel | 99.2% | 95.0% | **+4.2%** |
| json_extract | 6.7% | 1.7% | **+5.0%** |
| large_data_count_generated | 86.7% | 81.7% | **+5.0%** |

### Key Observations

**Critical Regressions** (requiring investigation):
- `router_multi_transform_validate`: **-86.7%** - Severe regression, went from 93.3% → 6.7%
- `router_transform`: **-31.7%** - Significant regression
- `router_validate`: **-28.3%** - Significant regression

**Notable Improvements**:
- `large_data_count_generated`: **+5.0%**
- `json_extract`: **+5.0%**
- `fast_food_cancel`: **+4.2%**
- `router_analyze`: **+4.2%**

---

## Table 2: Per-Sample Pass Rates (Sorted: Most Degraded → Most Improved)

### Most Degraded Samples (Top 30)

| Model | Test Case | Diff Stack | Main | Delta |
|-------|-----------|------------|------|-------|
| gpt-oss-120b | router_multi_transform_validate_001 | 0/10 | 9/10 | **-90.0%** |
| claude-haiku | router_multi_transform_validate_001 | 1/10 | 10/10 | **-90.0%** |
| claude-sonnet | router_multi_transform_validate_001 | 1/10 | 10/10 | **-90.0%** |
| nemotron3-nano-30b | router_multi_transform_validate_001 | 1/10 | 10/10 | **-90.0%** |
| gemini-2.5-flash-lite | router_multi_transform_validate_001 | 1/10 | 9/10 | **-80.0%** |
| qwen3-80b | json_qa_004 | 1/10 | 9/10 | **-80.0%** |
| qwen3-80b | router_multi_transform_validate_001 | 0/10 | 8/10 | **-80.0%** |
| claude-haiku | router_validate_002 | 1/10 | 8/10 | **-70.0%** |
| qwen3-80b | router_transform_002 | 1/10 | 8/10 | **-70.0%** |
| gemini-2.5-flash-lite | router_validate_002 | 1/10 | 8/10 | **-70.0%** |
| claude-sonnet | router_validate_002 | 1/10 | 8/10 | **-70.0%** |
| claude-haiku | router_transform_002 | 1/10 | 8/10 | **-70.0%** |
| claude-sonnet | router_transform_002 | 1/10 | 8/10 | **-70.0%** |
| gemini-2.5-flash-lite | router_transform_002 | 1/10 | 8/10 | **-70.0%** |
| nemotron3-nano-30b | router_transform_002 | 0/10 | 6/10 | **-60.0%** |
| qwen3-80b | json_qa_003 | 1/10 | 7/10 | **-60.0%** |
| qwen3-80b | json_qa_009 | 0/10 | 6/10 | **-60.0%** |
| nemotron3-nano-30b | router_validate_002 | 1/10 | 7/10 | **-60.0%** |
| gpt-oss-120b | router_transform_002 | 1/10 | 7/10 | **-60.0%** |
| qwen3-80b | router_validate_002 | 0/10 | 5/10 | **-50.0%** |
| gpt-oss-120b | fast_food_order_004 | 1/10 | 5/10 | -40.0% |
| gpt-oss-120b | router_validate_002 | 1/10 | 4/10 | -30.0% |
| gpt-oss-120b | refinement_001 | 3/10 | 6/10 | -30.0% |
| nemotron3-nano-30b | calculate_batch_001 | 4/10 | 7/10 | -30.0% |
| claude-haiku | refinement_001 | 4/10 | 7/10 | -30.0% |
| gemini-2.5-flash-lite | large_data_extract_generated_001 | 7/10 | 9/10 | -20.0% |
| gemini-2.5-flash-lite | large_data_find_generated_001 | 7/10 | 9/10 | -20.0% |
| nemotron3-nano-30b | employee_lookup_001 | 3/10 | 5/10 | -20.0% |
| qwen3-80b | json_qa_001 | 0/10 | 2/10 | -20.0% |
| gemini-2.5-flash-lite | refinement_001 | 0/10 | 2/10 | -20.0% |

### Most Improved Samples (Top 10)

| Model | Test Case | Diff Stack | Main | Delta |
|-------|-----------|------------|------|-------|
| gemini-2.5-flash-lite | json_qa_001 | 10/10 | 5/10 | **+50.0%** |
| gemini-2.5-flash-lite | fast_food_order_001 | 5/10 | 0/10 | **+50.0%** |
| qwen3-80b | sentiment_single_003 | 9/10 | 4/10 | **+50.0%** |
| gpt-oss-120b | router_analyze_001 | 10/10 | 6/10 | **+40.0%** |
| nemotron3-nano-30b | calculate_complex_002 | 9/10 | 5/10 | **+40.0%** |
| qwen3-80b | employee_lookup_001 | 10/10 | 6/10 | **+40.0%** |
| nemotron3-nano-30b | json_extract_001 | 4/10 | 1/10 | +30.0% |
| nemotron3-nano-30b | fast_food_order_001 | 9/10 | 6/10 | +30.0% |
| nemotron3-nano-30b | fast_food_cancel_001 | 10/10 | 7/10 | +30.0% |
| nemotron3-nano-30b | fast_food_order_004 | 8/10 | 5/10 | +30.0% |

---

## Root Cause Analysis Prompts

Use these prompts with a subagent that has access to `trace_explorer`. Each prompt provides the score change and all relevant trace files. The agent should determine the root cause independently.

### What Makes a Good Root Cause Analysis

A good RCA should answer:
1. **What happened?** - What specific behavior did the agent exhibit that caused the test to fail/pass?
2. **Why did it happen?** - What in the context window (system prompt, history, tool descriptions) caused the LLM to behave differently?
3. **Is the evaluation correct?** - Is the test evaluation too strict, wrong, or is this a legitimate failure?
4. **How could trace_explorer be more useful?** - What information was missing or hard to find?

---

### RCA Prompt 1: `router_multi_transform_validate` (DEGRADED -86.7%)

```
## Root Cause Analysis Task

**Score Change**: 93.3% → 6.7% (-86.7%)
**Test**: router_multi_transform_validate_001
**Models Affected**: All 6 models show similar regression

**Trace Files (Diff Stack - Failing)**:
/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260127_084247/traces/RouterTestWrapper_*_process_router_multi_transform_validate_*.006trace.jsonl

**Trace Files (Main - Passing)**:
/Volumes/dev/dev/viewer/results/capability_optimization_20260127_084243/traces/RouterTestWrapper_*_process_router_multi_transform_validate_*.006trace.jsonl

**Your Task**:
Use trace_explorer to analyze these traces and determine:
1. What happened? What behavior did the agent exhibit that caused the test to fail?
2. Why did it happen? What in the context window caused the LLM to behave differently?
3. Is the evaluation correct? Or is the test too strict?
4. How could trace_explorer be more useful for this analysis?
```

---

### RCA Prompt 2: `router_transform` (DEGRADED -31.7%)

```
## Root Cause Analysis Task

**Score Change**: 85.8% → 54.2% (-31.7%)
**Test**: router_transform_002
**Models Affected**: All models show regression

**Trace Files (Diff Stack - Failing)**:
/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260127_084247/traces/RouterTestWrapper_*_process_router_transform_*.006trace.jsonl

**Trace Files (Main - Passing)**:
/Volumes/dev/dev/viewer/results/capability_optimization_20260127_084243/traces/RouterTestWrapper_*_process_router_transform_*.006trace.jsonl

**Your Task**:
Use trace_explorer to analyze these traces and determine:
1. What happened? What behavior did the agent exhibit that caused the test to fail?
2. Why did it happen? What in the context window caused the LLM to behave differently?
3. Is the evaluation correct? Or is the test too strict?
4. How could trace_explorer be more useful for this analysis?
```

---

### RCA Prompt 3: `json_qa` qwen3-80b (DEGRADED -80%)

```
## Root Cause Analysis Task

**Score Change**: 90% → 10% (-80%) for qwen3-80b on json_qa_004
**Test**: json_qa (multiple test cases)
**Models Affected**: Primarily qwen3-80b

**Trace Files (Diff Stack)**:
/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260127_084247/traces/JsonQAAgent_qwen3-80b_*.006trace.jsonl

**Trace Files (Main)**:
/Volumes/dev/dev/viewer/results/capability_optimization_20260127_084243/traces/JsonQAAgent_qwen3-80b_*.006trace.jsonl

**Your Task**:
Use trace_explorer to analyze these traces and determine:
1. What happened? What behavior did the agent exhibit that caused the test to fail?
2. Why did it happen? What in the context window caused the LLM to behave differently?
3. Is the evaluation correct? Or is the test too strict?
4. How could trace_explorer be more useful for this analysis?
```

---

### RCA Prompt 4: `json_qa_001` gemini (IMPROVED +50%)

```
## Root Cause Analysis Task

**Score Change**: 50% → 100% (+50%)
**Test**: json_qa_001
**Model**: gemini-2.5-flash-lite

**Trace Files (Diff Stack - Now Passing)**:
/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260127_084247/traces/JsonQAAgent_gemini-2.5-flash-lite_*.006trace.jsonl

**Trace Files (Main - Was Failing)**:
/Volumes/dev/dev/viewer/results/capability_optimization_20260127_084243/traces/JsonQAAgent_gemini-2.5-flash-lite_*.006trace.jsonl

**Your Task**:
Use trace_explorer to analyze these traces and determine:
1. What happened? What change in behavior caused the test to now pass?
2. Why did it happen? What in the context window changed to enable success?
3. Is this a legitimate improvement or did something else change?
4. How could trace_explorer be more useful for this analysis?
```

---

### RCA Prompt 5: `fast_food_order_001` gemini (IMPROVED +50%)

```
## Root Cause Analysis Task

**Score Change**: 0% → 50% (+50%)
**Test**: fast_food_order_001
**Model**: gemini-2.5-flash-lite

**Trace Files (Diff Stack - Some Passing)**:
/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260127_084247/traces/OrderTestWrapper_gemini-2.5-flash-lite_run_conversation_fast_food_order_*.006trace.jsonl

**Trace Files (Main - All Failing)**:
/Volumes/dev/dev/viewer/results/capability_optimization_20260127_084243/traces/OrderTestWrapper_gemini-2.5-flash-lite_run_conversation_fast_food_order_*.006trace.jsonl

**Your Task**:
Use trace_explorer to analyze these traces and determine:
1. What happened? What change in behavior caused the test to now pass?
2. Why did it happen? What in the context window changed to enable success?
3. Is this a legitimate improvement or did something else change?
4. How could trace_explorer be more useful for this analysis?
```

---

### RCA Prompt 6: `sentiment_single_003` qwen3-80b (IMPROVED +50%)

```
## Root Cause Analysis Task

**Score Change**: 40% → 90% (+50%)
**Test**: sentiment_single_003
**Model**: qwen3-80b

**Trace Files (Diff Stack - Now Passing)**:
/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260127_084247/traces/SentimentSingleAgent_qwen3-80b_classify_sentiment_single_*_000002_*.006trace.jsonl

**Trace Files (Main - Was Failing)**:
/Volumes/dev/dev/viewer/results/capability_optimization_20260127_084243/traces/SentimentSingleAgent_qwen3-80b_classify_sentiment_single_*_000002_*.006trace.jsonl

**Your Task**:
Use trace_explorer to analyze these traces and determine:
1. What happened? What change in behavior caused the test to now pass?
2. Why did it happen? What in the context window changed to enable success?
3. Is this a legitimate improvement or did something else change?
4. How could trace_explorer be more useful for this analysis?
```

---

## Next Steps

1. Run RCA prompts 1-3 (degraded) to identify root causes of regressions
2. Run RCA prompts 4-6 (improved) to understand what changes helped
3. Based on RCA findings, fix blocking issues or revert problematic changes

---

## Appendix: Full Data

Full comparison data saved to: `/Volumes/dev/dev/nemo_oo_agents/results/comparison_data.json`

### File Locations

- **Diff Stack Results**: `/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260127_084247/capabilityoptimization_20260127_084247.006eval.jsonl`
- **Main Results**: `/Volumes/dev/dev/viewer/results/capability_optimization_20260127_084243/capabilityoptimization_20260127_084243.006eval.jsonl`
- **Diff Stack Traces**: `/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260127_084247/traces/`
- **Main Traces**: `/Volumes/dev/dev/viewer/results/capability_optimization_20260127_084243/traces/`

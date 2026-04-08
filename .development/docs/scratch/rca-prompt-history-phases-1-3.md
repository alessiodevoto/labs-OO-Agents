# Root Cause Analysis: history-phases-1-3 MR Regression

## Context

MR !285 (history-phases-1-3) shows a **-2.3pp regression** in capability eval compared to main. Your task is to identify the root cause of this regression.

## Bake-off Results Summary

| Branch | Overall | Stable | Frontier |
|--------|---------|--------|----------|
| **MR** | 67.3% (1981/2942) | 72.1% (1558/2160) | 54.2% (423/780) |
| **main** | 69.6% (1578/2266) | 74.7% (1258/1685) | 55.2% (320/580) |
| **Delta** | **-2.3pp** | **-2.5pp** | **-0.9pp** |

**Per-Model Regression:**

| Model | Delta |
|-------|-------|
| qwen3-80b | **-5.6pp** (worst) |
| nemotron3-nano-30b | -2.5pp |
| gemini-2.5-flash-lite | -2.1pp |
| claude-haiku | -1.7pp |
| claude-sonnet | -1.2pp |
| gpt-oss-120b | -0.7pp |

## Files to Analyze

**MR results:**
```
/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260126_120328/capabilityoptimization_20260126_120328.006eval.jsonl
/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260126_120328/traces/
```

**main results:**
```
/Volumes/dev/dev/viewer/results/capability_optimization_20260126_120319/capabilityoptimization_20260126_120319.006eval.jsonl
/Volumes/dev/dev/viewer/results/capability_optimization_20260126_120319/traces/
```

## MR vs Main by Test Type

| Test Type | MR Pass Rate | Main Pass Rate | Delta | MR (pass/total) | Main (pass/total) |
|---|---|---|---|---|---|
| error_recovery | 60.0% | 85.1% | -25.1pp | 36/60 | 40/47 |
| router_transform | 76.7% | 96.8% | -20.1pp | 92/120 | 91/94 |
| router_validate | 71.7% | 91.7% | -20.0pp | 86/120 | 88/96 |
| router_multi_analyze_validate | 75.0% | 93.8% | -18.8pp | 45/60 | 45/48 |
| router_multi_transform_validate | 75.0% | 91.5% | -16.5pp | 45/60 | 43/47 |
| router_analyze | 77.5% | 93.6% | -16.1pp | 93/120 | 88/94 |
| calculate_batch | 60.0% | 66.7% | -6.7pp | 36/60 | 32/48 |
| refinement | 43.3% | 47.9% | -4.6pp | 26/60 | 23/48 |
| calculate_complex | 93.3% | 96.5% | -3.2pp | 168/180 | 139/144 |
| repl_exploration | 78.3% | 80.9% | -2.5pp | 47/60 | 38/47 |
| json_qa | 47.7% | 49.8% | -2.1pp | 286/600 | 239/480 |
| json_extract | 0.0% | 2.1% | -2.1pp | 0/60 | 1/48 |
| context_notes | 0.0% | 0.0% | +0.0pp | 0/60 | 0/41 |
| calculate_simple | 100.0% | 99.3% | +0.7pp | 180/180 | 143/144 |
| fast_food_order | 50.8% | 49.8% | +1.0pp | 183/360 | 128/257 |
| large_data_find_generated | 95.0% | 92.9% | +2.1pp | 114/120 | 78/84 |
| sentiment_single | 98.3% | 95.3% | +3.0pp | 236/240 | 183/192 |
| sentiment_batch | 3.3% | 0.0% | +3.3pp | 2/60 | 0/48 |
| fast_food_cancel | 97.5% | 94.0% | +3.5pp | 117/120 | 79/84 |
| large_data_count_generated | 90.0% | 85.7% | +4.3pp | 54/60 | 36/42 |
| employee_lookup | 56.7% | 50.0% | +6.7pp | 34/60 | 21/42 |
| needle_in_haystack | 86.7% | 77.1% | +9.6pp | 52/60 | 37/48 |
| large_data_extract_generated | 81.7% | 14.3% | +67.4pp | 49/60 | 6/42 |

## MR vs Main by Test Sample

| Test Sample | MR Pass Rate | Main Pass Rate | Delta | MR (pass/total) | Main (pass/total) |
|---|---|---|---|---|---|
| error_recovery_001 | 60.0% | 85.1% | -25.1pp | 36/60 | 40/47 |
| router_validate_001 | 76.7% | 97.9% | -21.2pp | 46/60 | 47/48 |
| router_transform_002 | 75.0% | 95.7% | -20.7pp | 45/60 | 45/47 |
| router_transform_001 | 78.3% | 97.9% | -19.5pp | 47/60 | 46/47 |
| router_multi_analyze_validate_001 | 75.0% | 93.8% | -18.8pp | 45/60 | 45/48 |
| router_validate_002 | 66.7% | 85.4% | -18.8pp | 40/60 | 41/48 |
| router_analyze_001 | 76.7% | 93.6% | -17.0pp | 46/60 | 44/47 |
| router_multi_transform_validate_001 | 75.0% | 91.5% | -16.5pp | 45/60 | 43/47 |
| router_analyze_002 | 78.3% | 93.6% | -15.3pp | 47/60 | 44/47 |
| fast_food_order_004 | 46.7% | 61.9% | -15.2pp | 28/60 | 26/42 |
| json_qa_003 | 53.3% | 60.4% | -7.1pp | 32/60 | 29/48 |
| calculate_batch_001 | 60.0% | 66.7% | -6.7pp | 36/60 | 32/48 |
| calculate_complex_002 | 91.7% | 97.9% | -6.2pp | 55/60 | 47/48 |
| json_qa_004 | 48.3% | 54.2% | -5.8pp | 29/60 | 26/48 |
| json_qa_006 | 45.0% | 50.0% | -5.0pp | 27/60 | 24/48 |
| json_qa_009 | 51.7% | 56.2% | -4.6pp | 31/60 | 27/48 |
| refinement_001 | 43.3% | 47.9% | -4.6pp | 26/60 | 23/48 |
| json_qa_007 | 41.7% | 45.8% | -4.2pp | 25/60 | 22/48 |
| repl_exploration_001 | 78.3% | 80.9% | -2.5pp | 47/60 | 38/47 |
| calculate_complex_001 | 93.3% | 95.8% | -2.5pp | 56/60 | 46/48 |
| json_qa_002 | 50.0% | 52.1% | -2.1pp | 30/60 | 25/48 |
| json_extract_001 | 0.0% | 2.1% | -2.1pp | 0/60 | 1/48 |
| json_qa_001 | 46.7% | 47.9% | -1.2pp | 28/60 | 23/48 |
| calculate_complex_003 | 95.0% | 95.8% | -0.8pp | 57/60 | 46/48 |
| fast_food_order_003 | 73.3% | 73.8% | -0.5pp | 44/60 | 31/42 |
| calculate_simple_001 | 100.0% | 100.0% | +0.0pp | 60/60 | 48/48 |
| calculate_simple_002 | 100.0% | 100.0% | +0.0pp | 60/60 | 48/48 |
| context_notes_001 | 0.0% | 0.0% | +0.0pp | 0/60 | 0/41 |
| fast_food_order_001 | 70.0% | 68.9% | +1.1pp | 42/60 | 31/45 |
| sentiment_single_003 | 95.0% | 93.8% | +1.2pp | 57/60 | 45/48 |
| large_data_find_generated_002 | 96.7% | 95.2% | +1.4pp | 58/60 | 40/42 |
| fast_food_order_002 | 1.7% | 0.0% | +1.7pp | 1/60 | 0/44 |
| json_qa_008 | 43.3% | 41.7% | +1.7pp | 26/60 | 20/48 |
| calculate_simple_003 | 100.0% | 97.9% | +2.1pp | 60/60 | 47/48 |
| json_qa_010 | 50.0% | 47.9% | +2.1pp | 30/60 | 23/48 |
| fast_food_cancel_002 | 95.0% | 92.9% | +2.1pp | 57/60 | 39/42 |
| fast_food_order_006 | 50.0% | 47.6% | +2.4pp | 30/60 | 20/42 |
| sentiment_single_004 | 98.3% | 95.8% | +2.5pp | 59/60 | 46/48 |
| large_data_find_generated_001 | 93.3% | 90.5% | +2.9pp | 56/60 | 38/42 |
| sentiment_batch_001 | 3.3% | 0.0% | +3.3pp | 2/60 | 0/48 |
| sentiment_single_001 | 100.0% | 95.8% | +4.2pp | 60/60 | 46/48 |
| sentiment_single_002 | 100.0% | 95.8% | +4.2pp | 60/60 | 46/48 |
| large_data_count_generated_001 | 90.0% | 85.7% | +4.3pp | 54/60 | 36/42 |
| fast_food_cancel_001 | 100.0% | 95.2% | +4.8pp | 60/60 | 40/42 |
| json_qa_005 | 46.7% | 41.7% | +5.0pp | 28/60 | 20/48 |
| employee_lookup_001 | 56.7% | 50.0% | +6.7pp | 34/60 | 21/42 |
| needle_in_haystack_001 | 86.7% | 77.1% | +9.6pp | 52/60 | 37/48 |
| fast_food_order_005 | 63.3% | 47.6% | +15.7pp | 38/60 | 20/42 |
| large_data_extract_generated_001 | 81.7% | 14.3% | +67.4pp | 49/60 | 6/42 |

## Router Validate: MR vs Main by Model

| Model | MR Pass Rate | Main Pass Rate | Delta | MR (pass/total) | Main (pass/total) |
|---|---|---|---|---|---|
| nemotron3-nano-30b | 65.0% | 93.8% | -28.8pp | 13/20 | 15/16 |
| gemini-2.5-flash-lite | 75.0% | 100.0% | -25.0pp | 15/20 | 16/16 |
| claude-haiku | 80.0% | 100.0% | -20.0pp | 16/20 | 16/16 |
| claude-sonnet | 80.0% | 100.0% | -20.0pp | 16/20 | 16/16 |
| gpt-oss-120b | 60.0% | 75.0% | -15.0pp | 12/20 | 12/16 |
| qwen3-80b | 70.0% | 81.2% | -11.2pp | 14/20 | 13/16 |

## Analysis Steps

### Step 1: Compare Test Distribution
First, understand if the test distributions differ between runs:

```python
# Load both JSONL files
# Compare by: test_id, category, tier, difficulty
# Identify any tests that exist in one but not the other
# Note: MR has 2942 entries vs main's 2266 - understand why
```

### Step 2: Identify Flipped Tests
Find tests that passed on main but failed on MR:

```python
# For each (test_id, model) pair:
#   - Did it pass on main and fail on MR? (regression)
#   - Did it fail on main and pass on MR? (improvement)
# Group by category/tier to find patterns
```

### Step 3: Analyze Failure Patterns
For the flipped tests, analyze:
- Which test categories have the most regressions?
- Are certain test types (e.g., multi-turn, tool use, reasoning) disproportionately affected?
- Is there a pattern by model (qwen3-80b has -5.6pp, what test types does it fail on?)

### Step 4: Trace Comparison
For the top 5-10 regression cases:
1. Load the trace from both MR and main runs
2. Compare the agent's behavior step-by-step
3. Identify where the MR behavior diverges
4. Look for patterns:
   - Context/history rendering differences?
   - Event formatting changes?
   - Different tool call patterns?

### Step 5: Map to Code Changes

The MR made these key changes:
- Added `type` field to `EventBase`
- Added `explicit_return` field to `ExecutePythonEvent` (return vs value distinction)
- Fixed `BlockRenderer` truncation metadata bug
- Changed formatter field name stripping (`return_` → `<return>`)
- Fixed circular import for NemoOOAgentsProvider registration
- Changed `StructuredOutputStrategy` logging level

Check if any trace differences correlate with these changes.

## Output Expected

1. **Summary table** of regression by test category
2. **List of specific test cases** that regressed (test_id, model, category)
3. **Root cause hypothesis** based on trace analysis
4. **Code location** in the MR that likely caused the regression
5. **Recommended fix** or investigation direction

## Quick Start Commands

```bash
# Count entries
wc -l /Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260126_120328/*.jsonl
wc -l /Volumes/dev/dev/viewer/results/capability_optimization_20260126_120319/*.jsonl

# View a trace
cat /Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260126_120328/traces/<trace_id>.jsonl | python -m json.tool

# Parse JSONL and find flipped tests
python3 -c "
import json

mr_results = {}
with open('/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260126_120328/capabilityoptimization_20260126_120328.006eval.jsonl') as f:
    for line in f:
        r = json.loads(line)
        key = (r.get('test_id'), r.get('model'))
        mr_results[key] = r.get('passed', False)

main_results = {}
with open('/Volumes/dev/dev/viewer/results/capability_optimization_20260126_120319/capabilityoptimization_20260126_120319.006eval.jsonl') as f:
    for line in f:
        r = json.loads(line)
        key = (r.get('test_id'), r.get('model'))
        main_results[key] = r.get('passed', False)

# Find regressions (passed on main, failed on MR)
regressions = []
for key in set(mr_results.keys()) & set(main_results.keys()):
    if main_results[key] and not mr_results[key]:
        regressions.append(key)

print(f'Found {len(regressions)} regressions')
for test_id, model in regressions[:20]:
    print(f'  {model}: {test_id}')
"
```

---

## Findings (Summary)

### Result Distribution Check

- **Result entries (excluding metadata/completion):** MR = 2940, main = 2265
- **Overlap (same test_id + model):** 2265
- **MR-only entries:** 675 (consistent with higher per-test run count; MR has 60 per test_case vs main 42–48)
- **Tier distribution:** MR = 2160 stable / 780 frontier, main = 1685 stable / 580 frontier (same ratio, more runs)

### Regression / Improvement Counts (Overlap Only)

- **Regressions:** 142
- **Improvements:** 162
- **Net:** -20
- **Regressions by tier:** stable 92, frontier 50
- **Regressions by model (count):** gpt-oss-120b 36, nemotron3-nano-30b 35, gemini-2.5-flash-lite 31, qwen3-80b 31, claude-haiku 7, claude-sonnet 2

### Regression Summary by Test Case

| test_case | regressions | improvements | net |
|---|---:|---:|---:|
| calculate_batch_001 | 10 | 6 | 4 |
| calculate_complex_001 | 2 | 2 | 0 |
| calculate_complex_002 | 4 | 1 | 3 |
| calculate_complex_003 | 2 | 1 | 1 |
| calculate_simple_003 | 0 | 1 | -1 |
| employee_lookup_001 | 3 | 8 | -5 |
| error_recovery_001 | 14 | 3 | 11 |
| fast_food_cancel_001 | 0 | 2 | -2 |
| fast_food_cancel_002 | 2 | 3 | -1 |
| fast_food_order_001 | 5 | 5 | 0 |
| fast_food_order_003 | 7 | 4 | 3 |
| fast_food_order_004 | 9 | 3 | 6 |
| fast_food_order_005 | 3 | 10 | -7 |
| fast_food_order_006 | 5 | 6 | -1 |
| json_extract_001 | 1 | 0 | 1 |
| json_qa_001 | 3 | 2 | 1 |
| json_qa_002 | 1 | 0 | 1 |
| json_qa_003 | 5 | 2 | 3 |
| json_qa_004 | 5 | 2 | 3 |
| json_qa_005 | 3 | 5 | -2 |
| json_qa_006 | 3 | 1 | 2 |
| json_qa_007 | 2 | 2 | 0 |
| json_qa_008 | 3 | 4 | -1 |
| json_qa_009 | 4 | 2 | 2 |
| json_qa_010 | 0 | 1 | -1 |
| large_data_count_generated_001 | 4 | 5 | -1 |
| large_data_extract_generated_001 | 0 | 27 | -27 |
| large_data_find_generated_001 | 3 | 4 | -1 |
| large_data_find_generated_002 | 2 | 2 | 0 |
| needle_in_haystack_001 | 2 | 7 | -5 |
| refinement_001 | 8 | 5 | 3 |
| repl_exploration_001 | 7 | 6 | 1 |
| router_analyze_001 | 2 | 3 | -1 |
| router_analyze_002 | 1 | 3 | -2 |
| router_multi_transform_validate_001 | 1 | 3 | -2 |
| router_transform_001 | 1 | 1 | 0 |
| router_transform_002 | 3 | 2 | 1 |
| router_validate_001 | 2 | 1 | 1 |
| router_validate_002 | 7 | 6 | 1 |
| sentiment_batch_001 | 0 | 2 | -2 |
| sentiment_single_001 | 0 | 2 | -2 |
| sentiment_single_002 | 0 | 2 | -2 |
| sentiment_single_003 | 3 | 3 | 0 |
| sentiment_single_004 | 0 | 2 | -2 |

### Regressed Test Cases (Sample)

```
calculate_batch_001_claude-haiku_run8, claude-haiku, calculate_batch_001, frontier
calculate_batch_001_gemini-2.5-flash-lite_run4, gemini-2.5-flash-lite, calculate_batch_001, frontier
calculate_batch_001_gemini-2.5-flash-lite_run6, gemini-2.5-flash-lite, calculate_batch_001, frontier
calculate_batch_001_gemini-2.5-flash-lite_run7, gemini-2.5-flash-lite, calculate_batch_001, frontier
calculate_batch_001_gpt-oss-120b_run1, gpt-oss-120b, calculate_batch_001, frontier
calculate_batch_001_gpt-oss-120b_run6, gpt-oss-120b, calculate_batch_001, frontier
calculate_batch_001_nemotron3-nano-30b_run1, nemotron3-nano-30b, calculate_batch_001, frontier
calculate_batch_001_nemotron3-nano-30b_run6, nemotron3-nano-30b, calculate_batch_001, frontier
calculate_batch_001_qwen3-80b_run1, qwen3-80b, calculate_batch_001, frontier
calculate_batch_001_qwen3-80b_run4, qwen3-80b, calculate_batch_001, frontier
calculate_complex_001_nemotron3-nano-30b_run1, nemotron3-nano-30b, calculate_complex_001, frontier
calculate_complex_001_nemotron3-nano-30b_run2, nemotron3-nano-30b, calculate_complex_001, frontier
calculate_complex_002_gemini-2.5-flash-lite_run4, gemini-2.5-flash-lite, calculate_complex_002, frontier
calculate_complex_002_nemotron3-nano-30b_run2, nemotron3-nano-30b, calculate_complex_002, frontier
calculate_complex_002_nemotron3-nano-30b_run3, nemotron3-nano-30b, calculate_complex_002, frontier
calculate_complex_002_nemotron3-nano-30b_run4, nemotron3-nano-30b, calculate_complex_002, frontier
calculate_complex_003_nemotron3-nano-30b_run2, nemotron3-nano-30b, calculate_complex_003, frontier
calculate_complex_003_nemotron3-nano-30b_run5, nemotron3-nano-30b, calculate_complex_003, frontier
employee_lookup_001_gemini-2.5-flash-lite_run5, gemini-2.5-flash-lite, employee_lookup_001, frontier
employee_lookup_001_gpt-oss-120b_run3, gpt-oss-120b, employee_lookup_001, frontier
employee_lookup_001_qwen3-80b_run3, qwen3-80b, employee_lookup_001, frontier
error_recovery_001_claude-haiku_run5, claude-haiku, error_recovery_001, stable
error_recovery_001_claude-sonnet_run3, claude-sonnet, error_recovery_001, stable
error_recovery_001_gemini-2.5-flash-lite_run2, gemini-2.5-flash-lite, error_recovery_001, stable
error_recovery_001_gemini-2.5-flash-lite_run3, gemini-2.5-flash-lite, error_recovery_001, stable
error_recovery_001_gemini-2.5-flash-lite_run6, gemini-2.5-flash-lite, error_recovery_001, stable
error_recovery_001_gemini-2.5-flash-lite_run7, gemini-2.5-flash-lite, error_recovery_001, stable
error_recovery_001_gpt-oss-120b_run1, gpt-oss-120b, error_recovery_001, stable
error_recovery_001_gpt-oss-120b_run3, gpt-oss-120b, error_recovery_001, stable
error_recovery_001_gpt-oss-120b_run4, gpt-oss-120b, error_recovery_001, stable
```

---

## Trace Comparison Findings

### Prompt Structure Differences (MR vs main)

Observed consistently in multiple regression cases:

- **Task block expr changed**:
  - main: `<task expr="self.history.events[0].content">`
  - MR: `<task expr="self.history[0].prompt">`
- **System prompt differences in `doc(self)` content**:
  - `ErrorRecoveryTestAgent` lost the method docstring line:
    - main: `"""Retrieve a single number from a Alec."""`
    - MR: line missing
  - `OrderTestWrapper` lost a method signature line:
    - main: `async def process_message_impl(user_message: str):`
    - MR: line missing

These are the only non-timestamp diffs found in those prompts; prompt token counts were slightly lower in MR (typically -1 to -11 tokens).

---

## Root Cause Hypothesis

The regression aligns with **history/context rendering changes** in MR that alter LLM-visible prompts:

1. **Task event expression path changed** to `self.history[0].prompt` (instead of `self.history.events[0].content`), indicating a shift in how history events are referenced and rendered.
2. **`doc(self)` output is missing method signatures or docstring lines** for some agents in MR traces, reducing the LLM’s access to critical tool/behavior guidance.

These changes likely reduce the agent’s ability to correctly choose tools or implement method logic in tasks like **error_recovery**, **fast_food_order**, and **calculate** (which show the highest regression counts).

---

## Code Locations to Audit

- `packages/context-blocks/src/context_blocks/formatter.py`
  - `XMLBlockFormatter.format_event()` (expr path rendering and field-name stripping)
- `src/nemo_oo_agents/events.py`
  - `TaskEvent.render_spec()` (uses `prompt`)
  - `ExecutePythonEvent.render_spec()` (return_ vs value tag selection)
- `src/nemo_oo_agents/runtime/history.py`
  - `HistoryManager.__getitem__` and `_rendering_expr` reporting
- `packages/agentdoc/src/agentdoc/core.py`
  - `methods()` and docstring inclusion (why some doc lines/methods are missing)

---

## Recommended Next Steps

1. **Reproduce prompt diffs deterministically** by rendering `doc(self)` for a fixed agent class on main vs MR and diffing the output (focus on missing method lines).
2. **Verify task prompt integrity** by comparing `TaskEvent` rendering output for identical input across main/MR.
3. **Add a regression test** in `packages/context-blocks/tests/test_renderer.py` to ensure prompt blocks preserve method signatures/docstrings and stable event expressions.
4. If confirmed, **restore or stabilize doc(self) output** in `agentdoc` or block rendering and re-run a small capability bake-off slice.

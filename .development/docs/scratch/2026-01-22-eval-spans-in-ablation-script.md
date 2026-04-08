# Eval Spans in Ablation Script Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add eval span writing to the ablation experiment runner (`run_ablation.py`) so evaluation results appear in trace files for the trace viewer.

**Architecture:** The ablation script already creates per-sample trace files and evaluates tasks using benchmark adapters. We'll integrate the existing `write_eval_span_to_trace` function (from the eval_pipeline) to write eval spans after each task evaluation completes.

**Tech Stack:** Existing eval_pipeline.trace_eval_span module, run_ablation.py

---

## Background

The ablation script (`experiments/evaluation-ablations/run_ablation.py`) runs agent configurations against benchmarks. It:
- Creates per-sample trace files (`*.006trace.jsonl`)
- Evaluates tasks using `adapter.evaluate()` which returns `EvalResult` with `success`, `score`, `error_category`, `error_message`
- Currently adds eval results as attributes to the task span (lines 1297-1300)
- Writes results to JSONL output files with a "scores" dict containing a single "benchmark_scorer"

We just implemented `write_eval_span_to_trace` in the eval_pipeline (MR !278). We can reuse this function in the ablation script to write eval spans to trace files.

---

### Task 1: Add Eval Span Writing to Ablation Script

**Files:**
- Modify: `experiments/evaluation-ablations/run_ablation.py`
- Test: Manual verification (no new test file needed)

**Step 1: Add import for write_eval_span_to_trace**

Add to imports section (around line 54, after other imports):

```python
# Import eval span writer from eval_pipeline
try:
    from eval_pipeline.trace_eval_span import write_eval_span_to_trace
    HAS_EVAL_SPAN_WRITER = True
except ImportError:
    HAS_EVAL_SPAN_WRITER = False
    print("Warning: eval_pipeline not available, eval spans will not be written")
```

**Step 2: Find the eval span writing location**

The eval span should be written after evaluation completes and before the task span ends.

Location: After line 1300 (after setting task_span attributes) and before line 1304 (before closing per_sample_trace_file)

**Step 3: Add eval span writing call**

Insert after line 1300:

```python
            # Write eval span to trace (for trace viewer rendering)
            if per_sample_trace_file and HAS_EVAL_SPAN_WRITER:
                from eval_pipeline.eval_types import ScoreDetail

                # Create scorer detail from benchmark evaluation
                scores = {
                    "benchmark_scorer": ScoreDetail(
                        score=eval_result.score,
                        passed=eval_result.success,
                        reasoning=eval_result.error_message,
                    )
                }

                # Extract model name from provider/model
                model = provider or "unknown"
                if "/" in model:
                    model = model.split("/")[-1]

                write_eval_span_to_trace(
                    trace_file=per_sample_trace_file,
                    test_id=task.id,
                    passed=eval_result.success,
                    weighted_score=eval_result.score,
                    model=model,
                    agent_class=config_name,  # Use config_name as agent identifier
                    method=benchmark_name,     # Use benchmark as method identifier
                    scores=scores,
                )
```

**Step 4: Verify the code compiles**

Run: `cd experiments/evaluation-ablations && python -c "import run_ablation; print('OK')"`
Expected: `OK` (no syntax errors)

**Step 5: Run a small ablation test**

Test with a minimal run to verify eval spans are written:

```bash
cd experiments/evaluation-ablations
uv run python run_ablation.py \
    --provider openai \
    --model gpt-4o-mini \
    --benchmark bfcl \
    --limit 2 \
    --config-name test_config \
    --output-dir /tmp/ablation_test \
    --traces-dir /tmp/ablation_test/traces
```

**Step 6: Verify eval span in trace file**

Check that the eval span was written:

```bash
cat /tmp/ablation_test/traces/*.006trace.jsonl | grep '"name": "eval"' | head -1 | python -m json.tool
```

Expected: JSON output showing eval span with attributes including:
- `eval.test_id`
- `eval.passed`
- `eval.weighted_score`
- `eval.model`
- `eval.agent_class`
- `eval.method`
- `eval.scorer.benchmark_scorer.score`
- `eval.scorer.benchmark_scorer.passed`

**Step 7: Commit**

```bash
git add experiments/evaluation-ablations/run_ablation.py
git commit -m "feat(ablations): add eval span writing to ablation experiment runner"
```

---

### Task 2: Handle the Agent-File Code Path

**Files:**
- Modify: `experiments/evaluation-ablations/run_ablation.py`

**Context:** The ablation script has two code paths:
1. Direct model API calls (lines 1150-1321) - we handled this in Task 1
2. Agent file execution (lines 1323-1410) - needs the same treatment

**Step 1: Find the second eval location**

Location: After line 1375 (after task_span attribute setting in agent-file path)

**Step 2: Add eval span writing call**

Insert after line 1375:

```python
            # Write eval span to trace (for trace viewer rendering)
            if per_sample_trace_file and HAS_EVAL_SPAN_WRITER:
                from eval_pipeline.eval_types import ScoreDetail

                # Create scorer detail from benchmark evaluation
                scores = {
                    "benchmark_scorer": ScoreDetail(
                        score=eval_result.score,
                        passed=eval_result.success,
                        reasoning=eval_result.error_message,
                    )
                }

                # Extract agent class name from agent file
                agent_class = Path(agent_file).stem if agent_file else "unknown"

                write_eval_span_to_trace(
                    trace_file=per_sample_trace_file,
                    test_id=task.id,
                    passed=eval_result.success,
                    weighted_score=eval_result.score,
                    model="agent_file",  # Placeholder since agent file doesn't use a model param
                    agent_class=agent_class,
                    method=benchmark_name,
                    scores=scores,
                )
```

**Step 3: Verify compilation**

Run: `cd experiments/evaluation-ablations && python -c "import run_ablation; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add experiments/evaluation-ablations/run_ablation.py
git commit -m "feat(ablations): add eval span writing to agent-file code path"
```

---

### Task 3: End-to-End Verification

**Step 1: Run full ablation test**

Test both code paths with a real ablation run:

```bash
cd experiments/evaluation-ablations
uv run python run_ablation.py \
    --provider openai \
    --model gpt-4o-mini \
    --benchmark bfcl \
    --limit 5 \
    --config-name test_eval_spans \
    --output-dir /tmp/ablation_final_test \
    --traces-dir /tmp/ablation_final_test/traces
```

**Step 2: Inspect trace files**

Verify eval spans in multiple trace files:

```bash
# Count how many eval spans were written
grep -c '"name": "eval"' /tmp/ablation_final_test/traces/*.006trace.jsonl

# Inspect first eval span
cat /tmp/ablation_final_test/traces/*.006trace.jsonl | grep '"name": "eval"' | head -1 | python -m json.tool
```

Expected:
- 5 eval spans total (one per task with --limit 5)
- Each span has all required attributes
- Scorer details include benchmark_scorer with score, passed, reasoning

**Step 3: Check git status**

```bash
git status
```

Expected: Only `run_ablation.py` modified, working tree clean after commits

**Step 4: Document verification results**

Create summary note:
- Number of trace files created
- Number of eval spans written
- Sample eval span structure validated
- Both code paths tested (if applicable)

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add eval span writing to main code path | `run_ablation.py` |
| 2 | Add eval span writing to agent-file path | `run_ablation.py` |
| 3 | E2E verification | Manual test |

## Expected Outcome

After implementation:
- Every ablation run writes eval spans to trace files
- Trace viewer can render ablation evaluation results inline
- Compatible with existing eval_pipeline eval span structure
- Minimal changes (import + 2 function calls)
- No new dependencies (reuses eval_pipeline.trace_eval_span)

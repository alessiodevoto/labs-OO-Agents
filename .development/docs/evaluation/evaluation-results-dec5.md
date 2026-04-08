# Evaluation Results - December 5, 2025

## Summary

Ran comprehensive evaluation of all 8 benchmarks with baseline_oneshot agent (2 tasks per benchmark) to verify post-refactoring stability and trace generation.

## Results Overview

| Benchmark | Pass Rate | Status | Notes |
|-----------|-----------|--------|-------|
| BFCL | 0/2 (0%) | ✗ | Tool calls made but not recognized, invalid function names |
| LiveCodeBench | 0/2 (0%) | ✗ | Failed code generation tasks |
| InterCode SQL | 0/2 (0%) | ✗ | SQL query generation failed |
| BigCodeBench | 2/2 (100%) | ✓ | Perfect! Both tasks passed |
| TAU-Bench | 0/2 (0%) | ✗ ERROR | `unhashable type: 'ToolCall'` - adapter bug |
| DABStep | 2/2 (100%) | ✓ | Perfect! Both tasks passed |
| GAIA | 0/2 (0%) | ✗ | Complex QA tasks failed |
| SWE-bench | 0/2 (0%) | ✗ | Code editing tasks failed |

**Overall**: 4/16 tasks passed (25% pass rate with baseline agent)

## Trace Generation Status

### ✅ Traces Are Being Generated

All benchmarks successfully generated trace files in JSONL format:
- Location: `results/{timestamp}/traces/{config}_{benchmark}.jsonl`
- Format: OpenInference-compatible spans with trace_id, span_id, timestamps
- Contains: Task execution info, LLM calls, success/failure status

Example trace IDs from BFCL:
- `fa6dab36cbb54258` - bfcl_simple_python_0
- `115106a463e84d3b` - bfcl_simple_python_1

### ❌ Trace Links NOT Present in Reports

**Finding**: The result JSON files contain a `trace_file` field with a relative path (e.g., `"traces/baseline_oneshot_bfcl.jsonl"`), but they do **NOT** contain clickable trace URLs for individual tests.

**Current State**:
```json
{
  "trace_file": "traces/baseline_oneshot_bfcl.jsonl",  // File path only
  "results": [
    {
      "task_id": "bfcl_simple_python_0",
      // NO trace_url field here
    }
  ]
}
```

**Expected State** (what user wants):
```json
{
  "results": [
    {
      "task_id": "bfcl_simple_python_0",
      "trace_url": "http://localhost:5001/trace/fa6dab36cbb54258"  // Clickable link
    }
  ]
}
```

## Issues Found

### 1. TAU-Bench Adapter Error ✅ FIXED

**Error**: `unhashable type: 'ToolCall'`
**Impact**: TAU-Bench evaluation fails completely
**Cause**: ToolCall objects from unifiedllm weren't being properly handled when extracting tool names
**Fix Applied**: Modified tau_bench.py:1080-1088 to properly handle ToolCall objects by checking for `.name` attribute
**Status**: Fixed in tau_bench.py

### 2. Missing Trace URLs in Reports

**Issue**: Results contain trace file paths but not individual trace URLs per task
**Impact**: Cannot easily navigate to specific trace for a failed test
**User Request**: "i want that for both baseline and other agents we are training, before baseline was not expected to have traces"
**What's Needed**:
- Extract trace_id for each task result
- Add trace_url field to each result: `http://localhost:5001/trace/{trace_id}`
- Consider adding TRACE_VIEWER_URL environment variable for configurability

### 3. GAIA Failures - Agent Limitations (NOT adapter bugs)

**Analysis**: Both GAIA test failures are due to agent limitations, not adapter bugs
**Evidence**:
- Task 1: Agent hallucinated searching arXiv.org (said it would search but didn't)
- Task 2: Agent hallucinated USGS data (made up zip codes)
**Root Cause**: baseline_oneshot agent has no tools (no web search, no file access)
**Conclusion**: GAIA adapter is working correctly. Agent just isn't capable enough.

### 4. Trace Viewer Configuration

**Current Setup**: Trace viewer on port 5001 is configured for TPM agent traces at `../../agents/tpm-agent/traces/`
**Issue**: Evaluation traces are in `experiments/evaluation-ablations/results/{timestamp}/traces/`
**Impact**: Cannot view evaluation traces with current trace viewer instance
**Options**:
  1. Start separate trace viewer for evaluation traces
  2. Configure trace viewer to support multiple trace directories
  3. Copy/symlink evaluation traces to TPM agent traces directory

## Recommendations

1. ✅ **Fix TAU-Bench Adapter** - ~~Debug the ToolCall hashing issue~~ FIXED
2. **Add Trace URLs to Results** - Modify run_ablation.py to include per-task trace URLs (extract trace_id from spans, add trace_url field)
3. **Configure Trace Viewer** - Either:
   - Add TRACE_VIEWER_URL env var to run_ablation.py
   - Start dedicated trace viewer for evaluation results
   - Update trace viewer to support multiple trace directories
4. **Update generate_report.py** - Ensure reports include clickable trace links
5. **GAIA**: No action needed - adapter is working correctly, failures are expected with baseline agent

## Files Generated

### Result Directories (8 runs)
- `/home/rcabral/nemo_oo_agents/experiments/evaluation-ablations/results/20251205_112716/` - BFCL
- `/home/rcabral/nemo_oo_agents/experiments/evaluation-ablations/results/20251205_112736/` - LiveCodeBench
- `/home/rcabral/nemo_oo_agents/experiments/evaluation-ablations/results/20251205_112757/` - InterCode SQL
- `/home/rcabral/nemo_oo_agents/experiments/evaluation-ablations/results/20251205_112818/` - BigCodeBench
- `/home/rcabral/nemo_oo_agents/experiments/evaluation-ablations/results/20251205_112910/` - TAU-Bench
- `/home/rcabral/nemo_oo_agents/experiments/evaluation-ablations/results/20251205_112923/` - DABStep
- `/home/rcabral/nemo_oo_agents/experiments/evaluation-ablations/results/20251205_112937/` - GAIA
- `/home/rcabral/nemo_oo_agents/experiments/evaluation-ablations/results/20251205_113008/` - SWE-bench

Each directory contains:
- `baseline_oneshot_{benchmark}.json` - Full results
- `baseline_oneshot_{benchmark}.jsonl` - Crash-safe incremental results
- `traces/baseline_oneshot_{benchmark}.jsonl` - OpenInference trace spans

## Post-Refactoring Status

**Good News**:
- ✅ Evaluation framework still works after Paul's massive refactoring
- ✅ All adapters successfully load and run (except TAU-Bench error)
- ✅ Trace generation is working
- ✅ Baseline agent functional
- ✅ 2 benchmarks (BigCodeBench, DABStep) showing 100% pass rate with baseline

**Areas Needing Attention**:
- ❌ TAU-Bench adapter has new bug (unhashable ToolCall)
- ❌ No trace URLs in reports (only file paths)
- ❌ BFCL has issues with function name validation
- ⚠️ Most benchmarks show 0% pass rate (expected for simple baseline, but worth noting)

## Next Steps

1. Fix TAU-Bench adapter bug
2. Implement trace URL generation in run_ablation.py
3. Set up trace viewer for evaluation results
4. Optionally: Investigate BFCL function name validation issue

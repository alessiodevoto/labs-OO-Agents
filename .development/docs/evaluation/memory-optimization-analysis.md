# Memory Optimization Analysis

**Date:** Mon Jan 13 16:38:00 CET 2026

## Problem
BigCodeBench evaluation runs consume ~20GB memory and get killed by macOS OOM after ~5 minutes (completing only 50-150 of 1140 tasks).

## Root Cause: In-Memory Result Accumulation

### Current Memory-Intensive Pattern

**File:** [run_ablation.py:1001](experiments/evaluation-ablations/run_ablation.py#L1001)

```python
results = [None] * len(tasks)  # Pre-allocate list for ALL 1140 tasks

for coro in asyncio.as_completed(coros):
    idx, result = await coro
    results[idx] = result  # Keep EVERYTHING in memory
    # ...write to disk...
```

**Memory Accumulation Sources:**

1. **Full result objects** (1140 × ~5-15MB each):
   - `result["input"]` - full task input (code, context, instructions)
   - `result["output"]` - agent's solution (code, execution output)
   - `result["expected"]` - expected output
   - `result["trace_file"]` - trace data path
   - Various metadata fields

2. **Trace objects** (if in-memory tracing enabled):
   - Full execution traces for each task
   - LLM API call logs with full prompts/responses

3. **Agent state** (5 concurrent agents):
   - Each agent keeps conversation history
   - Each keeps full context of their task
   - Docker containers and execution environments

4. **BigCodeBench dataset loading**:
   - Full dataset loaded into memory (1140 tasks)
   - Each task contains full problem statement, test cases

**Total estimated memory (BEFORE optimization):**
- Results list (kept in memory): 1140 tasks × ~10MB = **~11GB** ⚠️
- BigCodeBench dataset (loaded once): **~1GB**
- Agent overhead (5 concurrent agents with state): 5 × ~500MB = **~2.5GB**
- Trace buffers (in-memory before flush): **~500MB**
- Python runtime + libraries: **~500MB**
- **Total: ~15.5GB** ✅ Close to observed ~20GB (rest is OS buffers/overhead)

Note: Trace files (~5.7GB total) are written to disk, NOT kept in memory after flushing.

## Solution: Stream Processing Pattern

### Key Optimization: Don't Keep Results in Memory

Results are already written incrementally to JSONL (line 1035-1083). After writing, we **don't need them anymore** except for computing pass rate.

### Proposed Changes

#### 1. Replace Full Results List with Summary Stats

**Before:**
```python
results = [None] * len(tasks)  # Keeps everything in memory
```

**After:**
```python
# Only track summary statistics, not full results
result_stats = {
    "completed": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0,
    "errors": 0,
}
```

#### 2. Stream Results to Disk and Discard

**Before:**
```python
results[idx] = result  # Keep in memory
# Write to disk
```

**After:**
```python
# Update stats only
if result.get("success"):
    result_stats["passed"] += 1
elif result.get("skipped"):
    result_stats["skipped"] += 1
elif result.get("error_category"):
    result_stats["errors"] += 1
else:
    result_stats["failed"] += 1
result_stats["completed"] += 1

# Write to disk
# ... existing JSONL write code ...
# result object is garbage collected after this
```

#### 3. Compute Final Stats from Stats, Not Full Results

**Before:**
```python
passed = sum(1 for r in results if r and r.get("success"))
total_evaluated = sum(1 for r in results if r and not r.get("skipped"))
```

**After:**
```python
passed = result_stats["passed"]
total_evaluated = result_stats["passed"] + result_stats["failed"] + result_stats["errors"]
pass_rate = passed / total_evaluated if total_evaluated > 0 else 0
```

#### 4. Load from JSONL for Final Summary (Only at End)

If full results needed for final report:
```python
def load_results_from_jsonl(jsonl_path: Path) -> list[dict]:
    """Load results from JSONL only when needed."""
    results = []
    with open(jsonl_path) as f:
        for line in f:
            entry = json.loads(line)
            if entry.get("_type") == "result":
                results.append(entry)
    return results

# At end of run (not during):
if need_detailed_report:
    results = load_results_from_jsonl(jsonl_path)
```

### Expected Memory Savings

**P0 optimization (streaming results) alone:**
- **Before:** ~15.5GB total (11GB results + 4.5GB other)
- **After:** ~4.5GB total (0GB results + 4.5GB other)
- **Savings:** **~11GB** (71% reduction)

**Breakdown of remaining ~4.5GB:**
- BigCodeBench dataset: ~1GB
- Agent overhead (5 concurrent): ~2.5GB
- Trace buffers: ~500MB
- Python runtime: ~500MB

With additional optimizations below (P1-P4), total memory can drop to ~3GB (reducing agents to 3 concurrent saves ~1GB, clearing agent state saves ~500MB).

## Additional Optimizations

### 5. Clear Agent State After Each Task

Agents may accumulate conversation history. Clear it:

```python
# In run_single_task() after task completes
if hasattr(agent, "clear_history"):
    agent.clear_history()
```

### 6. Reduce Concurrent Tasks

**Current:** 5 concurrent tasks
**Proposed:** 3 concurrent tasks

Memory vs speed tradeoff:
- 5 tasks: faster, but 5 × agent overhead in memory
- 3 tasks: 40% slower, but 40% less concurrent memory

Change in command:
```bash
python run_ablation.py --concurrent-tasks 3 ...
```

### 7. Disable In-Memory Trace Buffering

If OTel tracing is writing to files, ensure it's not also buffering in memory:

```python
# In enable_tracing() configuration
exporter = OTELJSONLExporter(
    trace_file=trace_file,
    buffer_size=1,  # Flush immediately, don't buffer
)
```

### 8. Force Garbage Collection Periodically

```python
import gc

# After writing each result batch
if completed % 100 == 0:
    gc.collect()  # Force GC to reclaim memory from discarded results
```

## Implementation Priority

| Priority | Optimization | Expected Savings | Complexity |
|----------|-------------|------------------|------------|
| **P0** | Stream results (don't keep in memory) | ~11GB | Medium |
| **P1** | Reduce concurrent tasks (5→3) | ~2GB | Trivial |
| **P2** | Force GC every 100 tasks | ~1GB | Trivial |
| **P3** | Clear agent state after task | ~500MB | Easy |
| **P4** | Disable trace buffering | ~500MB | Easy |

**P0 alone should reduce memory from ~15-20GB → ~4.5GB**, which should be sufficient to prevent OOM kills on most systems.

## Testing Plan

1. **Implement P0 optimization** (streaming results)
2. **Test with full 1140-task run:**
   ```bash
   cd experiments/evaluation-ablations
   python run_ablation.py --provider nvidia --model qwen/qwen3-next-80b-a3b-instruct \
       --benchmark bfcl --concurrent-tasks 5
   ```
3. **Monitor memory usage:**
   ```bash
   watch -n 10 'ps -p [PID] -o pid,rss,vsz,%mem,command'
   ```
4. **Success criteria:**
   - Memory stays under 10GB
   - Run completes all 1140 tasks without OOM kill
   - Pass rate matches previous partial runs

## References

- Original issue: Processes killed at ~5 minutes with ~20GB memory
- [run_ablation.py:1001](experiments/evaluation-ablations/run_ablation.py#L1001) - Results accumulation
- [watchdog-timeout-implementation.md](docs/watchdog-timeout-implementation.md) - Prior debugging history

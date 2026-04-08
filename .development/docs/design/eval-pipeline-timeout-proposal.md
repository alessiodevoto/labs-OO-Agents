# Eval Pipeline Timeout Proposal

## Problem Statement

The eval pipeline can get stuck on samples that hang indefinitely (e.g., LLM not responding, agent in infinite loop). We need:

1. **A timeout mechanism** to abort long-running samples
2. **Proper logging** so timeout events are recorded in results for analysis

## Current Architecture

### Execution Flow

```
Evaluator.run()
  → run_evaluation(samples, config, writer, max_concurrent)
    → process_sample(sample, config, writer)   [for each sample]
      → execute_task(agent, task, trace_file)
        → agent.run(task.input)  ← This is where we need timeout
```

### Key Files

| File | Role |
|------|------|
| `execute.py` | `execute_task()` runs agent, captures timing/errors |
| `pipeline.py` | `process_sample()` orchestrates execute→score→write |
| `eval_types.py` | `EvalTestResult` with `error` and `error_type` fields |
| `config.py` | `PipelineConfig` with `trace_dir`, `pass_threshold` |

### Existing Error Handling

```python
# execute.py
try:
    actual = await agent.run(task.input)
except Exception as e:
    error = str(e)
```

```python
# pipeline.py - already classifies timeout errors!
def _classify_error_type(error_msg: str | None) -> str | None:
    if "timeout" in error_lower:
        return "TimeoutError"
```

## Proposed Solution

### 1. Add Timeout to `execute_task()`

```python
# execute.py
async def execute_task(
    agent: Agent,
    task: Task,
    trace_file: Path,
    timeout_seconds: float | None = None,  # NEW
) -> ExecutionResult:
    error = None
    actual = None

    start = time.perf_counter()
    try:
        if timeout_seconds:
            async with asyncio.timeout(timeout_seconds):
                actual = await agent.run(task.input)
        else:
            actual = await agent.run(task.input)
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        error = f"Timeout after {elapsed:.1f}s (limit: {timeout_seconds}s)"
    except Exception as e:
        error = str(e)
    latency_ms = (time.perf_counter() - start) * 1000

    return ExecutionResult(...)
```

### 2. Add Timeout Config to `PipelineConfig`

```python
# pipeline.py
@dataclass
class PipelineConfig:
    trace_dir: Path
    on_progress: ProgressCallback | None = None
    pass_threshold: float = 0.5
    timeout_seconds: float | None = None  # NEW: Global timeout
```

### 3. Pass Timeout Through Pipeline

```python
# pipeline.py - process_sample()
result = await execute_task(
    agent=agent,
    task=sample.task,
    trace_file=trace_file,
    timeout_seconds=config.timeout_seconds,  # NEW
)
```

### 4. Add CLI Option

```python
# cli.py
parser.add_argument(
    "--timeout", type=float, default=None,
    help="Timeout per sample in seconds (default: no timeout)"
)
```

### 5. Add YAML Config Option

```yaml
# config.yaml
name: my_eval
timeout_seconds: 120  # 2 minutes per sample
pass_threshold: 0.5
```

## Result Logging

### EvalTestResult (`.006eval.jsonl`)

When a timeout occurs, the `EvalTestResult` will contain:

```json
{
  "_type": "result",
  "test_id": "sentiment_001_gpt4_run1",
  "passed": false,
  "error": "Timeout after 120.0s (limit: 120s)",
  "error_type": "TimeoutError",
  "trace_file": "traces/SentimentAgent_gpt4_classify_20241219_143022.006trace.jsonl",
  "latency_ms": 120034.5
}
```

### Trace File (`.006trace.jsonl`) - Already Works!

The existing tracing infrastructure handles exceptions properly via the hooks pattern:

**In `decorators.py`:**
```python
try:
    result = await runtime._call_plan(func, args, kwargs)
except Exception as e:
    exception_caught = e
    raise
finally:
    call_after_hook("after_agent_call", hook_context, ..., exception=exception_caught)
```

**In `_hooks_impl.py`:**
```python
def after_agent_call(self, ..., exception: Exception | None, ...):
    if exception:
        span.set_status(Status(StatusCode.ERROR, str(exception)))
        span.set_attribute("error.type", type(exception).__name__)
        span.set_attribute("error.message", str(exception))
    span.end()  # Properly closes the span!
```

**Key insight: `asyncio.TimeoutError` IS an `Exception`!**

When `asyncio.timeout()` raises `TimeoutError`, it propagates through the existing `try/except/finally` blocks, and the hooks will:
1. Catch the exception in `finally`
2. Call `after_agent_call` with `exception=TimeoutError`
3. Set `error.type: "TimeoutError"` on the span
4. Properly close the span with `span.end()`

### Resulting Trace

The trace file will show:

```
plan.classify_single (span)
├── status: ERROR
├── error.type: TimeoutError
├── error.message: "..." (asyncio timeout message)
├── end_time: properly set
└── children:
    └── generation (span)
        ├── status: ERROR
        ├── error.type: TimeoutError
        ├── end_time: properly set
        └── children:
            └── code_execution (if in progress - also closed properly)
```

All spans get properly closed because the exception propagates through all the `finally` blocks.

### Implementation is Simpler Than Originally Proposed

No need for a wrapper span. Just wrap with `asyncio.timeout()` and let the exception propagate:

```python
# execute.py
async def execute_task(..., timeout_seconds: float | None = None):
    try:
        if timeout_seconds:
            async with asyncio.timeout(timeout_seconds):
                actual = await agent.run(task.input)
        else:
            actual = await agent.run(task.input)
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        error = f"Timeout after {elapsed:.1f}s (limit: {timeout_seconds}s)"
    except Exception as e:
        error = str(e)
```

The tracing hooks handle the rest automatically.

## Configuration Hierarchy (Future)

For more flexibility, we could support timeout at multiple levels:

| Level | Config Location | Overrides |
|-------|-----------------|-----------|
| Global | `PipelineConfig.timeout_seconds` | - |
| Per-test | `EvalTest.timeout_seconds` | Global |
| Per-sample | `Sample.timeout_seconds` | Per-test |

**MVP**: Start with global only. Add per-test later if needed.

## Implementation Steps

1. **Add timeout param to `execute_task()`** - `execute.py`
2. **Add `timeout_seconds` to `PipelineConfig`** - `pipeline.py`
3. **Pass timeout in `process_sample()`** - `pipeline.py`
4. **Add `--timeout` CLI flag** - `cli.py`
5. **Add `timeout_seconds` to YAML schema** - `config.py`
6. **Add tests** - verify timeout triggers error correctly

## Open Questions

1. **Should per-test timeouts be supported from the start?**
   - Pro: Different tests may have different expected durations
   - Con: Adds complexity to config and API

2. **Should we also add a global evaluation timeout?**
   - E.g., "abort entire evaluation after 2 hours"
   - This would be different from per-sample timeout

3. **What's a good default timeout?**
   - `None` (no timeout) for backwards compatibility
   - Could suggest `300` (5 min) as reasonable default in docs

## Estimated Effort

- Core implementation: ~30 minutes
- Tests: ~20 minutes
- Documentation: ~10 minutes

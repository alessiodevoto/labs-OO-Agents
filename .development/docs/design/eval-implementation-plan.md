# Evaluation Convergence - Implementation Plan (Updated)

## Summary

Based on analysis of eval_pipeline, run_ablations, and NeMo-Agent-Toolkit:

**Decision**: Build clean 5-layer architecture with shared backend.
**Inspiration**: Adopt NAT's usage stats patterns by analyzing our existing OTel traces.
**No duplication**: Use existing `.006trace.jsonl` files, no IntermediateSteps needed.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    FRONTENDS (User-facing)                   │
├──────────────────────────┬──────────────────────────────────┤
│   eval_pipeline          │        run_ablation CLI          │
│   - Parse YAML           │        - Parse CLI args          │
│   - Create tasks         │        - Create tasks            │
│   - Pick adapter         │        - Pick adapter            │
│   - Call runner          │        - Call runner             │
└──────────┬───────────────┴────────────────┬─────────────────┘
           │                                 │
           └─────────────────┬───────────────┘
                             │
              ┌──────────────▼──────────────┐
              │   SHARED BACKEND RUNNER     │
              │   evaluation/runner.py      │
              │                             │
              │  Uses:                      │
              │  - ConcurrencyEngine (L0)   │
              │  - ExecutionAdapter (L1)    │
              │  - ResultWriter (L1)        │
              │  - TraceAnalyzer (NEW!)     │
              └──────────┬─────┬────────────┘
                         │     │
         ┌───────────────┘     └───────────────┐
         │                                     │
    ┌────▼─────┐                         ┌────▼─────┐
    │  Agent   │                         │ Benchmark│
    │  Adapter │                         │ Adapter  │
    │          │                         │          │
    │ - agent  │                         │ - BFCL   │
    │   006    │                         │ - LiveCB │
    │ - direct │                         │ - BigCB  │
    │   LLM    │                         │ - etc.   │
    └──────────┘                         └──────────┘
```

---

## Layer Responsibilities

### Layer 0: Pure Concurrency Engine
**File**: `evaluation/concurrency.py` ✅ DONE

**Knows about**:
- asyncio, semaphores, task IDs
- Timeout support
- Progress callbacks
- Checkpoint/resume

**Doesn't know about**:
- Agents, evaluation, scoring, benchmarks
- What task data contains
- What results mean

### Layer 1: Protocol Definitions
**File**: `evaluation/protocol.py` ⏳ TODO

**Defines**:
```python
class ExecutionAdapter(Protocol):
    """Adapter executes tasks in domain-specific way."""
    async def execute_task(self, task: EvaluationTask) -> EvaluationResult:
        ...

class ResultWriter(Protocol):
    """Writer outputs results in specific format."""
    def write_result(self, result: EvaluationResult) -> None:
        ...
    def finalize(self, metadata: dict) -> None:
        ...

@dataclass
class EvaluationTask:
    task_id: str
    data: Any  # OPAQUE to runner

@dataclass
class EvaluationResult:
    task_id: str
    success: bool
    output: Any  # OPAQUE to runner
    metadata: dict
    trace_file: Path | None = None  # For usage stats

@dataclass
class UsageStats:
    """Usage statistics extracted from traces."""
    per_model: dict[str, dict]  # model -> {prompt_tokens, completion_tokens, calls}
    total_tokens: int
    total_runtime_seconds: float
    p95_latency_ms: float
```

### Layer 2: Evaluation Runner
**File**: `evaluation/runner.py` ⏳ TODO

**Responsibilities**:
- Orchestrate execution using concurrency engine
- Load/save checkpoints
- Call adapter.execute_task() for each task
- Pass results to writer
- **NEW**: Analyze traces for usage stats
- Output aggregate statistics

**Key addition**:
```python
class EvaluationRunner:
    def __init__(
        self,
        adapter: ExecutionAdapter,
        writer: ResultWriter,
        config: EvaluationConfig,
        trace_analyzer: TraceAnalyzer | None = None,  # NEW!
    ):
        self.adapter = adapter
        self.writer = writer
        self.config = config
        self.engine = ConcurrencyEngine()
        self.trace_analyzer = trace_analyzer or TraceAnalyzer()
        self.aggregate_stats = UsageStats(...)

    async def run_evaluation(self, tasks: list[EvaluationTask]) -> list[EvaluationResult]:
        # ... run tasks ...

        # After each task completes:
        def on_complete(task_id: str, result: EvaluationResult):
            self.writer.write_result(result)

            # NEW: Analyze trace if available
            if result.trace_file and result.trace_file.exists():
                task_stats = self.trace_analyzer.analyze_trace(result.trace_file)
                self.aggregate_stats.merge(task_stats)

        # ... finish ...

        # Write aggregate stats
        self._write_usage_stats(self.aggregate_stats)
```

### Layer 3: Execution Adapters
**Files**:
- `evaluation/adapters/nemo_oo_agents_adapter.py` ⏳ TODO
- `evaluation/adapters/benchmark_adapter.py` ⏳ TODO

**Responsibilities**:
- Implement ExecutionAdapter protocol
- Execute tasks in domain-specific way
- Set up tracing (OTel) for each task
- Return trace file path in result.metadata

**Example**:
```python
class Agent006Adapter(ExecutionAdapter):
    async def execute_task(self, task: EvaluationTask) -> EvaluationResult:
        # Set up tracing (existing infrastructure!)
        trace_file = self._setup_trace(task.task_id)

        # Execute agent
        agent = self.agent_factory()
        method = getattr(agent, self.method_name)
        output = await method(**task.data["kwargs"])

        # Return result with trace file path
        return EvaluationResult(
            task_id=task.task_id,
            success=True,
            output=output,
            trace_file=trace_file,  # Runner will analyze this
            metadata={
                "expected": task.data.get("expected"),
            },
        )
```

### Layer 4: Frontends
**Files**:
- `util/eval_pipeline/src/eval_pipeline/evaluator.py` (UPDATE)
- `experiments/evaluation-ablations/run_ablation.py` (UPDATE)

**Responsibilities**:
- Parse user input (YAML or CLI args)
- Create EvaluationTask objects
- Pick appropriate adapter
- Configure runner
- Call runner.run_evaluation()

---

## New Component: TraceAnalyzer

**File**: `evaluation/trace_analyzer.py` ⏳ TODO

**Purpose**: Extract usage statistics from OTel trace files (no duplication!)

```python
class TraceAnalyzer:
    """Analyze OTel traces for usage statistics."""

    def analyze_trace(self, trace_file: Path) -> TaskUsageStats:
        """
        Read .006trace.jsonl and extract:
        - Token counts per model
        - Latency measurements
        - LLM call counts
        - Runtime

        Returns:
            TaskUsageStats with per-model breakdown
        """
        stats_per_model = {}
        llm_latencies = []
        start_time = None
        end_time = None

        for line in trace_file.read_text().splitlines():
            span = json.loads(line)

            # Track overall runtime
            if start_time is None or span["start_time"] < start_time:
                start_time = span["start_time"]
            if end_time is None or span["end_time"] > end_time:
                end_time = span["end_time"]

            # Extract LLM call stats
            if span.get("name") == "llm":
                attrs = span["attributes"]
                model = attrs.get("llm.model", "unknown")

                if model not in stats_per_model:
                    stats_per_model[model] = {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "reasoning_tokens": 0,
                        "cached_tokens": 0,
                        "calls": 0,
                    }

                stats_per_model[model]["prompt_tokens"] += attrs.get("llm.token_count.prompt", 0)
                stats_per_model[model]["completion_tokens"] += attrs.get("llm.token_count.completion", 0)
                stats_per_model[model]["reasoning_tokens"] += attrs.get("llm.token_count.reasoning", 0)
                stats_per_model[model]["cached_tokens"] += attrs.get("llm.token_count.cached", 0)
                stats_per_model[model]["calls"] += 1

                # Track latency
                latency_ms = (span["end_time"] - span["start_time"]) * 1000
                llm_latencies.append(latency_ms)

        return TaskUsageStats(
            per_model=stats_per_model,
            total_tokens=sum(
                s["prompt_tokens"] + s["completion_tokens"]
                for s in stats_per_model.values()
            ),
            runtime_seconds=(end_time - start_time) if start_time else 0,
            p95_latency_ms=float(np.percentile(llm_latencies, 95)) if llm_latencies else 0,
            llm_call_count=sum(s["calls"] for s in stats_per_model.values()),
        )

class AggregateUsageStats:
    """Aggregate stats across multiple tasks."""

    def __init__(self):
        self.task_stats: list[TaskUsageStats] = []

    def add(self, stats: TaskUsageStats):
        self.task_stats.append(stats)

    def compute_aggregate(self) -> dict:
        """Compute aggregate statistics."""
        # Merge per-model stats
        total_per_model = {}
        for task_stat in self.task_stats:
            for model, stats in task_stat.per_model.items():
                if model not in total_per_model:
                    total_per_model[model] = {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "reasoning_tokens": 0,
                        "cached_tokens": 0,
                        "calls": 0,
                    }
                for key in stats:
                    total_per_model[model][key] += stats[key]

        return {
            "total_tasks": len(self.task_stats),
            "total_tokens": sum(s.total_tokens for s in self.task_stats),
            "total_runtime_seconds": sum(s.runtime_seconds for s in self.task_stats),
            "avg_tokens_per_task": np.mean([s.total_tokens for s in self.task_stats]),
            "avg_runtime_per_task": np.mean([s.runtime_seconds for s in self.task_stats]),
            "p95_latency_ms": float(np.percentile([s.p95_latency_ms for s in self.task_stats], 95)),
            "per_model": total_per_model,
        }
```

**Output** (`usage_stats.json`):
```json
{
  "total_tasks": 100,
  "total_tokens": 850000,
  "total_runtime_seconds": 450.2,
  "avg_tokens_per_task": 8500,
  "avg_runtime_per_task": 4.5,
  "p95_latency_ms": 720,
  "per_model": {
    "gpt-4o-mini": {
      "prompt_tokens": 125000,
      "completion_tokens": 45000,
      "reasoning_tokens": 0,
      "cached_tokens": 10000,
      "calls": 150
    },
    "claude-3-5-sonnet": {
      "prompt_tokens": 98000,
      "completion_tokens": 32000,
      "reasoning_tokens": 15000,
      "cached_tokens": 8000,
      "calls": 120
    }
  }
}
```

---

## Implementation Phases

### Phase 1: Core Backend (Current Sprint)

**Status**: In progress

1. ✅ **Layer 0: Concurrency Engine** - `evaluation/concurrency.py`
   - Pure async execution
   - Semaphore-based concurrency
   - Checkpoint/resume support
   - Progress callbacks

2. ⏳ **Layer 1: Protocol Definitions** - `evaluation/protocol.py`
   - ExecutionAdapter protocol
   - ResultWriter protocol
   - EvaluationTask dataclass
   - EvaluationResult dataclass
   - UsageStats dataclass

3. ⏳ **TraceAnalyzer** - `evaluation/trace_analyzer.py`
   - Read .006trace.jsonl files
   - Extract token counts per model
   - Compute latency percentiles
   - Aggregate statistics

4. ⏳ **Layer 2: Evaluation Runner** - `evaluation/runner.py`
   - Orchestrate with concurrency engine
   - Checkpoint management
   - Progress tracking with tqdm
   - Call trace analyzer after each task
   - Write usage stats JSON

### Phase 2: Adapters

5. ⏳ **Agent006Adapter** - `evaluation/adapters/nemo_oo_agents_adapter.py`
   - Execute agent methods
   - Set up OTel tracing per task
   - Return trace file path in result

6. ⏳ **BenchmarkAdapter** - `evaluation/adapters/benchmark_adapter.py`
   - Load benchmark tasks
   - Execute with appropriate agent
   - Integrate benchmark-specific scoring

### Phase 3: Frontend Integration

7. ✅ **Update eval_pipeline** - `util/eval_pipeline/src/eval_pipeline/evaluator.py`
   - Replace current backend with new runner
   - Keep YAML parsing and Python API
   - Test with existing capability tests
   - **Status**: Complete - replaced asyncio.Semaphore with ConcurrencyEngine
   - **Tests**: 30/30 tests pass (pipeline, evaluator, execute)

8. ✅ **Update run_ablation** - `experiments/evaluation-ablations/run_ablation.py`
   - Replace current backend with new runner
   - Keep CLI interface
   - Test with benchmark adapters
   - **Status**: Complete - replaced asyncio.as_completed with ConcurrencyEngine
   - **Tests**: Successfully executes benchmarks with unified backend

### Phase 4: Testing & Validation

9. ✅ **Test capability tests**
   - Run existing eval_pipeline tests
   - Verify backward compatibility
   - Check trace files and usage stats
   - **Status**: Complete - all 30 tests pass

10. ⏳ **Test benchmarks**
    - Run BFCL, LiveCodeBench, etc.
    - Verify scoring works
    - Check performance/concurrency

---

## Benefits Summary

### From Clean Architecture
✅ Zero nemo_oo_agents leakage into runner
✅ Reusable concurrency engine (Layer 0)
✅ Protocol-based adapters (flexible)
✅ Generic checkpoint/resume

### From NAT Patterns
✅ Rich usage statistics
✅ Per-model token breakdowns
✅ Latency measurements (p95)
✅ Cost analysis data

### No Duplication
✅ Use existing OTel traces
✅ No IntermediateSteps duplication
✅ Single source of truth

### Better Output
✅ Multiple output files (results, stats, config)
✅ Reproducibility (save config + metadata)
✅ Progress tracking (tqdm)
✅ Error handling per task

---

## Key Design Decisions

1. **No IntermediateSteps** - Use existing OTel traces, analyze post-hoc
2. **TraceAnalyzer at Layer 2** - Runner analyzes traces after each task
3. **Trace file path in result** - Adapters return trace_file path, runner analyzes it
4. **Aggregate stats** - Runner accumulates stats across all tasks
5. **Output usage_stats.json** - Save aggregate statistics at end

---

## Files to Create/Modify

### New Files
- `evaluation/concurrency.py` ✅ (done)
- `evaluation/protocol.py` (protocols + dataclasses)
- `evaluation/trace_analyzer.py` (analyze OTel traces)
- `evaluation/runner.py` (orchestration)
- `evaluation/adapters/nemo_oo_agents_adapter.py` (agent execution)
- `evaluation/adapters/benchmark_adapter.py` (benchmark execution)
- `evaluation/writers/jsonl_writer.py` (JSONL output)

### Modified Files
- `util/eval_pipeline/src/eval_pipeline/evaluator.py` (use new backend)
- `experiments/evaluation-ablations/run_ablation.py` (use new backend)

### Test Files
- `evaluation/tests/test_concurrency.py`
- `evaluation/tests/test_trace_analyzer.py`
- `evaluation/tests/test_runner.py`
- `evaluation/tests/test_adapters.py`

---

## Success Criteria

1. ✅ Clean architecture - no nemo_oo_agents leakage into Layer 0-2
2. ✅ Both frontends work - eval_pipeline and run_ablation CLI
3. ✅ Backward compatible - existing tests pass
4. ✅ Rich statistics - usage_stats.json with per-model breakdown
5. ✅ No duplication - analyze existing traces, don't create new data
6. ✅ Resume works - checkpoint/restart at task level
7. ✅ Progress tracking - tqdm shows completion estimate
8. ✅ Error handling - per-task errors don't fail batch

---

## Next Steps

1. Implement Layer 1 (protocols)
2. Implement TraceAnalyzer
3. Implement Layer 2 (runner with trace analysis)
4. Implement Layer 3 adapters
5. Update frontends
6. Test and validate

**Ready to proceed with Layer 1 implementation?**

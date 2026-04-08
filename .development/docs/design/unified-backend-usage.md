# Unified Backend Architecture - Usage Guide

## Overview

The unified backend provides a clean, layered architecture for agent evaluation:

```
Layer 4: High-level APIs (Evaluator, runner.py)
Layer 3: Adapters (Agent006Adapter, BenchmarkExecutionAdapter)
Layer 2: Task execution (TaskRunner, TraceAnalyzer)
Layer 1: Protocols (ExecutionEngine, TraceAnalyzer)
Layer 0: Execution engines (ConcurrencyEngine, future: MultiprocessEngine, RayEngine)
```

## When to Use Each System

### New Unified Backend (`evaluation/`)
**Use for:**
- Benchmark evaluations (BFCL, InterCode, TAU-Bench, etc.)
- Agent capability testing with improvement loops
- Distributed or HPC cluster execution (future)
- Direct agent method invocation
- Usage statistics and cost tracking

**Benefits:**
- Swappable execution strategies
- Automatic trace analysis and usage stats
- Checkpoint/resume support
- Clean separation of concerns
- Extensible for new execution patterns

### Eval Pipeline (`util/eval_pipeline/`)
**Use for:**
- Quick capability tests with YAML config
- Test-driven development workflows
- Multi-model comparison
- Exact match and LLM judge scoring
- Integration with existing test suites

**Benefits:**
- Simple YAML configuration
- Built-in scoring infrastructure
- CLI and Python API
- Proven for capability tests

## Using the Unified Backend

### 1. Simple Agent Execution

```python
from evaluation.agent_adapter import execute_agent_on_tasks
from evaluation.task_runner import EvaluationTask

# Define tasks
tasks = [
    EvaluationTask(
        task_id="task1",
        data={"prompt": "What is 2+2?", "expected": "4"}
    ),
    EvaluationTask(
        task_id="task2",
        data={"prompt": "What is 3+3?", "expected": "6"}
    ),
]

# Execute with agent factory
results = await execute_agent_on_tasks(
    agent_factory=lambda: MyAgent(),
    tasks=tasks,
    max_concurrent=10,
    timeout_seconds=60,
)

# Results include usage stats automatically
for result in results:
    print(f"{result.task_id}: success={result.success}")
    if result.usage_stats:
        print(f"  Tokens: {result.usage_stats.total_tokens}")
        print(f"  Runtime: {result.usage_stats.total_runtime_seconds}s")
```

### 2. Benchmark Evaluation

```python
from evaluation.benchmark_adapter import evaluate_benchmark
from evaluation.adapters import BFCLAdapter

# Create benchmark adapter
benchmark = BFCLAdapter()

# Run evaluation with improvement loop
results, metrics = await evaluate_benchmark(
    benchmark=benchmark,
    agent_factory=lambda: MyAgent(),
    max_concurrent=5,
    task_limit=10,
    max_improvement_iterations=3,  # Self-improvement
)

# Metrics include usage statistics
print(f"Success rate: {metrics['success_rate']:.1%}")
print(f"Total tokens: {metrics['total_tokens']:,}")
print(f"Models used: {metrics['models_used']}")
```

### 3. Custom Execution Strategy

```python
from evaluation.task_runner import TaskRunner, RunnerConfig
from evaluation.agent_adapter import Agent006Adapter
from evaluation.concurrency import ConcurrencyEngine

# Create custom execution engine (future: Ray, NemoRun, etc.)
engine = ConcurrencyEngine()  # Or RayEngine(), NemoRunEngine()

# Configure runner
config = RunnerConfig(
    max_concurrent=10,
    analyze_traces=True,
)

runner = TaskRunner(engine=engine, config=config)

# Create agent adapter
agent_adapter = Agent006Adapter(
    agent_factory=lambda: MyAgent()
)
agent_adapter.register_tasks(tasks)

# Execute
results = await runner.run_tasks(
    tasks=tasks,
    task_fn=agent_adapter.execute_task_by_id,
)

# Get usage statistics
stats = runner.get_usage_stats()
print(f"Total tokens: {stats.total_tokens}")
print(f"Per-model breakdown:")
for model, model_stats in stats.models_breakdown.items():
    print(f"  {model}: {model_stats.total_tokens} tokens, "
          f"p95 latency: {model_stats.p95_latency_ms:.0f}ms")
```

### 4. Integration with Existing Tests

The unified backend can execute existing capability tests:

```python
from evaluation.agent_adapter import Agent006Adapter, AgentConfig
from evaluation.task_runner import EvaluationTask, TaskRunner

# Load existing tests
test_module = importlib.import_module("tests.capability.my_test")

# Convert to EvaluationTasks
tasks = []
for test_case in test_module.TEST_CASES:
    task = EvaluationTask(
        task_id=test_case["id"],
        data=test_case,
        metadata={"agent_class": test_case.get("agent_class")},
    )
    tasks.append(task)

# Execute with adapter
agent_adapter = Agent006Adapter()
agent_adapter.register_tasks(tasks)

runner = TaskRunner()
results = await runner.run_tasks(
    tasks=tasks,
    task_fn=agent_adapter.execute_task_by_id,
)
```

## Usage Statistics

The unified backend automatically extracts usage statistics from OTel traces:

```python
# After running evaluation
stats = runner.get_usage_stats()

# Per-task statistics
for task_stats in stats.per_task_stats:
    print(f"Task {task_stats.task_id}:")
    print(f"  Runtime: {task_stats.total_runtime_seconds:.2f}s")
    print(f"  LLM calls: {task_stats.total_llm_calls}")
    for model in task_stats.models_used:
        print(f"  {model.model_name}:")
        print(f"    Tokens: {model.total_tokens}")
        print(f"    Latency: {model.p95_latency_ms:.0f}ms (p95)")

# Aggregate statistics
print(f"\nAggregate:")
print(f"  Total tokens: {stats.total_tokens:,}")
print(f"  Total runtime: {stats.total_runtime_seconds:.1f}s")
print(f"  Total tasks: {stats.num_tasks}")

# Per-model breakdown
for model_name, model_stats in stats.models_breakdown.items():
    print(f"\n{model_name}:")
    print(f"  Total calls: {model_stats.call_count}")
    print(f"  Total tokens: {model_stats.total_tokens:,}")
    print(f"    Prompt: {model_stats.prompt_tokens:,}")
    print(f"    Completion: {model_stats.completion_tokens:,}")
    print(f"  Latency p95: {model_stats.p95_latency_ms:.0f}ms")
```

## Architecture Benefits

### Swappable Execution Engines

The `ExecutionEngine` protocol allows different execution strategies without changing higher-level code:

```python
# Local async I/O (default)
engine = ConcurrencyEngine()

# Future: Multiprocess for CPU-bound tasks
engine = MultiprocessEngine(num_workers=8)

# Future: Distributed with Ray
engine = RayEngine(cluster_address="ray://...")

# Future: HPC cluster submission
engine = NemoRunEngine(
    cluster="slurm",
    partition="gpu",
    nodes=10,
)

# Same runner API for all engines
runner = TaskRunner(engine=engine)
results = await runner.run_tasks(tasks, task_fn)
```

### Automatic Trace Analysis

No need to manually track tokens and latency:

```python
# Tracing is automatic via OTel
# Usage stats extracted from .006trace.jsonl files
# No duplication of data structures

result = await agent_adapter.execute_task(task)

# Usage stats automatically populated
print(result.usage_stats.total_tokens)  # Extracted from trace
print(result.usage_stats.total_runtime_seconds)  # From span timestamps
print(result.usage_stats.models_used)  # From LLM spans
```

### Checkpoint and Resume

Built-in support for crash recovery:

```python
runner = TaskRunner(
    config=RunnerConfig(enable_checkpointing=True)
)

# Run with checkpoint file
results = await runner.run_tasks(
    tasks=tasks,
    task_fn=task_fn,
    checkpoint_file=Path("checkpoint.jsonl"),
)

# If crashed, resume from same checkpoint file
# Completed tasks are skipped automatically
```

## Migration Path

For existing code using eval_pipeline:

1. **Keep using eval_pipeline** for simple capability tests - it works well
2. **Use unified backend** for new benchmark evaluations
3. **Migrate incrementally** - both can coexist

Example migration:

```python
# Before (eval_pipeline)
from eval_pipeline import Evaluator

evaluator = Evaluator.from_config("config.yaml")
results = await evaluator.run()

# After (unified backend)
from evaluation.benchmark_adapter import evaluate_benchmark
from evaluation.adapters import get_adapter

benchmark = get_adapter("my_benchmark")
results, metrics = await evaluate_benchmark(
    benchmark=benchmark,
    agent_factory=lambda: MyAgent(),
)
```

## Next Steps

1. **Try the examples** above with your agents
2. **Read the implementation** in `evaluation/` for details
3. **Extend with custom adapters** for new benchmarks
4. **Contribute execution engines** (Ray, NemoRun, etc.)

## Complete Working Examples

See `examples/advanced/` for runnable scripts:

- `unified_backend_with_llm.py` — real LLM integration with usage stats and checkpoint/resume
- `swappable_execution_engines.py` — ConcurrencyEngine pattern (SubprocessEngine requires direct use, not via TaskRunner)

## Related Documentation

- [Implementation Summary](unified-backend-implementation-summary.md) - Complete implementation details
- [Implementation Plan](eval-implementation-plan.md) - Architecture details
- [NeMo Skills Analysis](nemo-skills-analysis.md) - Patterns adopted
- [Slurm Backend](slurm-backend-analysis.md) - Future distributed execution

# Clean Evaluation Architecture

## Problem Statement

**Current issues:**
- nemo_oo_agents details leak into runners (OTel setup, strategy knowledge, tool awareness)
- Adapters know too much about execution details (tracing, timeout, progress)
- Hard to test runner in isolation
- Hard to add non-nemo_oo_agents evaluations

**Goal:** Clear layer separation with dependency inversion

## Architecture Layers

### Layer 0: Pure Concurrency Engine (No domain knowledge)

**Location:** `evaluation/concurrency.py`

**Responsibilities:**
- Async task execution with semaphores
- Progress tracking (counts only)
- Error handling (generic exceptions)
- Resume/checkpoint support (generic state)

**Knows about:**
- `asyncio`, `Semaphore`, `gather()`
- Generic callables and results
- Nothing else

**Does NOT know about:**
- Agents, benchmarks, adapters
- Scoring, evaluation, tests
- Traces, outputs, formats

```python
# evaluation/concurrency.py

from typing import TypeVar, Callable, Any
from dataclasses import dataclass
import asyncio

T = TypeVar('T')
R = TypeVar('R')

@dataclass
class ConcurrencyConfig:
    """Pure concurrency config - no domain knowledge."""
    max_concurrent: int = 10
    timeout_seconds: float | None = None

@dataclass
class TaskState:
    """Generic task state for checkpointing."""
    task_id: str
    completed: bool
    result: Any | None = None
    error: str | None = None

class ConcurrencyEngine:
    """Pure async execution engine with no domain knowledge.

    This is a reusable concurrency primitive that knows nothing about
    evaluation, agents, or any domain concepts.
    """

    async def run_tasks(
        self,
        task_ids: list[str],
        task_fn: Callable[[str], Awaitable[R]],
        config: ConcurrencyConfig,
        checkpoint_state: list[TaskState] | None = None,
        on_task_complete: Callable[[str, R | Exception], None] | None = None,
    ) -> list[R]:
        """Run tasks in parallel with concurrency control.

        Args:
            task_ids: List of task identifiers (opaque strings)
            task_fn: Async function that takes a task_id and returns a result
            config: Concurrency configuration
            checkpoint_state: Optional previous state for resume
            on_task_complete: Optional callback for progress tracking

        Returns:
            List of results in same order as task_ids

        This function knows NOTHING about:
        - What a "task" contains (just IDs)
        - How to execute a task (delegates to task_fn)
        - What results mean (just passes them through)
        - Evaluation, scoring, agents, benchmarks
        """
        # Implementation using semaphores and gather
        # See below for full code
```

### Layer 1: Evaluation Protocol (Abstract interfaces)

**Location:** `evaluation/protocol.py`

**Responsibilities:**
- Define interfaces for evaluation concepts
- Pure protocols with no implementation
- Type definitions for evaluation domain

**Knows about:**
- Abstract evaluation concepts (task, result, adapter)
- Type hints and protocols
- Nothing concrete

**Does NOT know about:**
- nemo_oo_agents specifics
- How to actually run tasks
- Trace formats, output formats

```python
# evaluation/protocol.py

from typing import Protocol, Any, runtime_checkable
from dataclasses import dataclass

@dataclass
class EvaluationTask:
    """Abstract evaluation task.

    This is an opaque container - the runner doesn't look inside.
    Only adapters know what's in here.
    """
    task_id: str
    data: Any  # Opaque - adapter-specific

@dataclass
class EvaluationResult:
    """Abstract evaluation result.

    The runner just passes these through.
    Only adapters and writers need to understand the contents.
    """
    task_id: str
    success: bool
    output: Any  # Opaque - adapter-specific
    metadata: dict[str, Any]  # Opaque - adapter-specific

@runtime_checkable
class ExecutionAdapter(Protocol):
    """Protocol for task execution.

    Adapters implement this to execute different types of tasks.
    The runner just calls these methods - it doesn't know how they work.
    """

    async def execute_task(self, task: EvaluationTask) -> EvaluationResult:
        """Execute a single task.

        The adapter is responsible for:
        - Interpreting task.data
        - Actually running the evaluation
        - Handling errors
        - Creating result with appropriate output/metadata

        The adapter can do ANYTHING inside here:
        - Instantiate agents
        - Set up tracing
        - Run in Docker
        - Call LLMs
        - Whatever is needed

        The runner doesn't care - it just calls this method.
        """
        ...

@runtime_checkable
class ResultWriter(Protocol):
    """Protocol for writing results.

    Writers implement this to save results in different formats.
    The runner just calls these methods.
    """

    def write_result(self, result: EvaluationResult) -> None:
        """Write a single result."""
        ...

    def finalize(self, summary: dict[str, Any]) -> None:
        """Finalize output (close file, write summary, etc)."""
        ...
```

### Layer 2: Evaluation Runner (Orchestration)

**Location:** `evaluation/runner.py`

**Responsibilities:**
- Orchestrate evaluation using Layer 0 + Layer 1
- Wire together: concurrency, adapters, writers
- Handle checkpointing and resume
- Track progress at high level

**Knows about:**
- `ConcurrencyEngine` (Layer 0)
- `ExecutionAdapter`, `ResultWriter` protocols (Layer 1)
- Generic orchestration patterns

**Does NOT know about:**
- nemo_oo_agents specifics
- How adapters execute tasks
- What's in results
- Trace formats

```python
# evaluation/runner.py

from .concurrency import ConcurrencyEngine, ConcurrencyConfig, TaskState
from .protocol import EvaluationTask, EvaluationResult, ExecutionAdapter, ResultWriter
from dataclasses import dataclass
from pathlib import Path

@dataclass
class EvaluationConfig:
    """High-level evaluation config - still generic."""
    max_concurrent_tasks: int = 10
    timeout_seconds: float | None = None
    resume_from: Path | None = None
    output_file: Path

class EvaluationRunner:
    """High-level evaluation orchestrator.

    This class knows about evaluation concepts (tasks, results, adapters)
    but has NO KNOWLEDGE of:
    - nemo_oo_agents internals
    - How adapters work internally
    - What's in task data or results

    It just orchestrates: tasks → adapter → results → writer
    """

    def __init__(
        self,
        adapter: ExecutionAdapter,
        writer: ResultWriter,
        config: EvaluationConfig,
    ):
        self.adapter = adapter
        self.writer = writer
        self.config = config
        self.engine = ConcurrencyEngine()

    async def run_evaluation(
        self,
        tasks: list[EvaluationTask],
    ) -> list[EvaluationResult]:
        """Run evaluation on all tasks.

        This method:
        1. Loads checkpoint if resuming
        2. Delegates to ConcurrencyEngine for parallel execution
        3. Passes results to writer
        4. Returns results

        It does NOT:
        - Know how to execute tasks (delegates to adapter)
        - Know how to write results (delegates to writer)
        - Know anything about agents, benchmarks, etc.
        """
        # Load checkpoint for resume
        checkpoint = self._load_checkpoint() if self.config.resume_from else None

        # Extract task IDs
        task_ids = [t.task_id for t in tasks]
        task_map = {t.task_id: t for t in tasks}

        # Create execution function that wraps adapter
        async def execute_one(task_id: str) -> EvaluationResult:
            task = task_map[task_id]
            return await self.adapter.execute_task(task)

        # Delegate to concurrency engine
        results = await self.engine.run_tasks(
            task_ids=task_ids,
            task_fn=execute_one,
            config=ConcurrencyConfig(
                max_concurrent=self.config.max_concurrent_tasks,
                timeout_seconds=self.config.timeout_seconds,
            ),
            checkpoint_state=checkpoint,
            on_task_complete=self._handle_result,
        )

        # Finalize
        self.writer.finalize({"total": len(results)})
        return results

    def _handle_result(self, task_id: str, result: EvaluationResult | Exception):
        """Progress callback - writes result incrementally."""
        if isinstance(result, Exception):
            # Convert to failed result
            result = EvaluationResult(
                task_id=task_id,
                success=False,
                output=None,
                metadata={"error": str(result)},
            )

        self.writer.write_result(result)
        self._save_checkpoint(task_id, result)
```

### Layer 3: Adapter Implementations (Concrete)

**Location:** `evaluation/adapters/`

**Responsibilities:**
- Implement `ExecutionAdapter` protocol
- Know domain specifics (nemo_oo_agents, benchmarks, etc.)
- Set up execution environment (tracing, Docker, etc.)
- Handle domain-specific errors

**Knows about:**
- nemo_oo_agents internals (this layer CAN know!)
- Benchmark specifics
- How to set up tracing
- Scoring logic

**Does NOT know about:**
- How runner orchestrates tasks
- Concurrency details
- File formats (delegates to writers)

```python
# evaluation/adapters/nemo_oo_agents_adapter.py

from ..protocol import ExecutionAdapter, EvaluationTask, EvaluationResult
from typing import Any, Callable
import asyncio

class Agent006Adapter(ExecutionAdapter):
    """Adapter for running nemo_oo_agents agents.

    THIS LAYER KNOWS ABOUT AGENT006 - that's its job!
    The runner doesn't need to know about nemo_oo_agents because
    it only interacts with this adapter through the protocol.
    """

    def __init__(
        self,
        agent_factory: Callable[[], Any],
        method_name: str,
        enable_tracing: bool = True,
        trace_dir: Path | None = None,
    ):
        self.agent_factory = agent_factory
        self.method_name = method_name
        self.enable_tracing = enable_tracing
        self.trace_dir = trace_dir

    async def execute_task(self, task: EvaluationTask) -> EvaluationResult:
        """Execute nemo_oo_agents task.

        This method can do ANYTHING nemo_oo_agents-specific:
        - Set up OTel tracing
        - Instantiate agent with strategy
        - Call agent methods
        - Handle agent-specific errors

        The runner doesn't know or care about any of this.
        """
        # Set up tracing (nemo_oo_agents-specific!)
        trace_file = None
        if self.enable_tracing and self.trace_dir:
            from openinference_instrumentation_nemo_oo_agents import set_trace_file
            trace_file = self.trace_dir / f"{task.task_id}.006trace.jsonl"
            set_trace_file(trace_file)

        try:
            # Create agent (nemo_oo_agents-specific!)
            agent = self.agent_factory()

            # Call method (nemo_oo_agents-specific!)
            method = getattr(agent, self.method_name)
            kwargs = task.data.get("kwargs", {})
            output = await method(**kwargs)

            # Flush traces (nemo_oo_agents-specific!)
            if trace_file:
                from openinference_instrumentation_nemo_oo_agents import get_current_exporter
                exporter = get_current_exporter()
                if exporter:
                    exporter.force_flush()
                    await asyncio.sleep(0.1)

            return EvaluationResult(
                task_id=task.task_id,
                success=True,
                output=output,
                metadata={
                    "trace_file": str(trace_file) if trace_file else None,
                    "expected": task.data.get("expected"),
                },
            )

        except Exception as e:
            return EvaluationResult(
                task_id=task.task_id,
                success=False,
                output=None,
                metadata={"error": str(e)},
            )


# evaluation/adapters/benchmark_adapter.py

class BenchmarkAdapter(ExecutionAdapter):
    """Adapter for running benchmark evaluations.

    THIS LAYER KNOWS ABOUT BENCHMARKS - that's its job!
    """

    def __init__(
        self,
        benchmark_name: str,
        agent_factory: Callable[[], Any],
        environment_factory: Callable | None = None,
    ):
        self.benchmark_name = benchmark_name
        self.agent_factory = agent_factory
        self.environment_factory = environment_factory

        # Load benchmark adapter from evaluation/adapters/
        from evaluation.adapters import get_adapter
        self.benchmark = get_adapter(benchmark_name)

    async def execute_task(self, task: EvaluationTask) -> EvaluationResult:
        """Execute benchmark task.

        This method can do ANYTHING benchmark-specific:
        - Set up Docker environments
        - Run multi-step interactions
        - Call benchmark-specific evaluators

        The runner doesn't know or care about any of this.
        """
        # Get benchmark task from opaque data
        benchmark_task = task.data["benchmark_task"]

        # Create agent
        agent = self.agent_factory()

        # Set up environment if needed (Docker, etc.)
        env = None
        if self.environment_factory:
            env = self.environment_factory(benchmark_task)
            await env.setup()

        try:
            # Run benchmark-specific execution
            # This might be multi-step, stateful, etc.
            agent_input = self.benchmark.format_for_agent(benchmark_task)

            if env:
                # Multi-step environment
                observation = await env.reset(agent_input)
                done = False
                while not done:
                    action = await agent.act(observation)
                    observation, done = await env.step(action)
                output = observation
            else:
                # Single-step
                output = await agent.run(agent_input)

            # Benchmark-specific evaluation
            eval_result = self.benchmark.evaluate(benchmark_task, output)

            return EvaluationResult(
                task_id=task.task_id,
                success=eval_result.success,
                output=output,
                metadata={
                    "score": eval_result.score,
                    "error_category": eval_result.error_category,
                },
            )

        finally:
            if env:
                await env.teardown()
```

### Layer 4: Frontends (User-facing)

**Location:** `util/eval_pipeline/`, `evaluation/cli.py`

**Responsibilities:**
- Parse user input (YAML, CLI args)
- Create appropriate adapters
- Create writers
- Call runner
- Display results

**Knows about:**
- User-facing concepts (configs, CLI flags)
- Which adapters to use for which test types
- How to create writers

**Does NOT know about:**
- How runner orchestrates
- How adapters work internally
- Concurrency details

```python
# util/eval_pipeline/src/eval_pipeline/evaluator.py

from evaluation.runner import EvaluationRunner, EvaluationConfig
from evaluation.protocol import EvaluationTask
from evaluation.adapters.nemo_oo_agents_adapter import Agent006Adapter
from evaluation.writers.jsonl_writer import JsonlWriter

class Evaluator:
    """eval_pipeline frontend.

    This is user-facing code that translates YAML/Python API
    into runner concepts.
    """

    async def run(self, models: list[str], runs: int = 1, parallel: int = 10):
        """Run evaluation using unified runner."""

        for test_name, test in self.tests.items():
            # Create adapter (frontend decides which adapter to use)
            adapter = Agent006Adapter(
                agent_factory=lambda: test.agent_class(model=model),
                method_name=test.method,
                enable_tracing=True,
                trace_dir=self.output_dir / "traces",
            )

            # Create writer
            writer = JsonlWriter(output_file=self.output_dir / f"{test_name}.006eval.jsonl")

            # Create runner config
            config = EvaluationConfig(
                max_concurrent_tasks=parallel,
                timeout_seconds=self.timeout_seconds,
                output_file=writer.output_file,
            )

            # Create runner
            runner = EvaluationRunner(
                adapter=adapter,
                writer=writer,
                config=config,
            )

            # Convert test data to tasks (opaque to runner!)
            tasks = [
                EvaluationTask(
                    task_id=f"{test_name}_{i}",
                    data={"kwargs": item["kwargs"], "expected": item["expected"]},
                )
                for i, item in enumerate(test.data)
            ]

            # Run (runner knows nothing about nemo_oo_agents!)
            results = await runner.run_evaluation(tasks)
```

## Dependency Graph

```
┌─────────────────────────────────────────────────────────┐
│ Layer 4: Frontends                                       │
│ - eval_pipeline (YAML + Python API)                     │
│ - evaluation.cli (CLI)                                   │
│ Knows: User configs, which adapters to use              │
│ Depends on: Layers 0,1,2,3                              │
└───────────────────┬─────────────────────────────────────┘
                    │
┌───────────────────▼──────────────────┬──────────────────┐
│ Layer 3: Adapters (Concrete)         │ Layer 3: Writers │
│ - Agent006Adapter                    │ - JsonlWriter    │
│ - BenchmarkAdapter                   │ - WandbWriter    │
│ Knows: nemo_oo_agents, benchmarks          │ Knows: Formats   │
│ Depends on: Layer 1 (protocols)      │ Depends on: L1   │
└───────────────────┬──────────────────┴──────────────────┘
                    │
┌───────────────────▼─────────────────────────────────────┐
│ Layer 2: Evaluation Runner (Orchestration)              │
│ - EvaluationRunner                                       │
│ Knows: Orchestration patterns                           │
│ Depends on: Layers 0,1 (protocols only!)                │
└───────────────────┬─────────────────────────────────────┘
                    │
┌───────────────────▼─────────────────────────────────────┐
│ Layer 1: Protocol (Abstract)                            │
│ - ExecutionAdapter (protocol)                           │
│ - ResultWriter (protocol)                               │
│ - EvaluationTask, EvaluationResult (data)               │
│ Knows: Abstract interfaces only                         │
│ Depends on: Nothing!                                    │
└───────────────────┬─────────────────────────────────────┘
                    │
┌───────────────────▼─────────────────────────────────────┐
│ Layer 0: Concurrency Engine (Pure async)               │
│ - ConcurrencyEngine                                      │
│ Knows: asyncio, semaphores                              │
│ Depends on: Nothing!                                    │
└─────────────────────────────────────────────────────────┘
```

## Key Design Principles

### 1. Dependency Inversion

**Bad (current):**
```python
# Runner knows about nemo_oo_agents
class Runner:
    async def run_task(self, task):
        from openinference_instrumentation_nemo_oo_agents import set_trace_file
        set_trace_file(...)  # LEAK!
        agent = Agent006(...)  # LEAK!
```

**Good (proposed):**
```python
# Runner only knows about protocols
class Runner:
    async def run_task(self, task):
        result = await self.adapter.execute_task(task)  # Adapter handles details
```

### 2. Opaque Data

**Runner treats task data as opaque:**
```python
@dataclass
class EvaluationTask:
    task_id: str
    data: Any  # Runner never looks inside!
```

**Only adapters interpret data:**
```python
class Agent006Adapter:
    async def execute_task(self, task):
        kwargs = task.data.get("kwargs")  # Adapter knows structure
```

### 3. No Type Coupling

**Runner uses protocols, not concrete types:**
```python
def __init__(self, adapter: ExecutionAdapter):  # Protocol, not Agent006Adapter
    self.adapter = adapter
```

### 4. Single Responsibility

Each layer has ONE job:
- Layer 0: Concurrency
- Layer 1: Interfaces
- Layer 2: Orchestration
- Layer 3: Implementation
- Layer 4: User interface

## Testing Strategy

### Layer 0: Test with dummy functions

```python
async def test_concurrency_engine():
    engine = ConcurrencyEngine()

    # No domain knowledge needed!
    results = await engine.run_tasks(
        task_ids=["1", "2", "3"],
        task_fn=lambda id: asyncio.sleep(0.1, id),  # Dummy
        config=ConcurrencyConfig(max_concurrent=2),
    )

    assert len(results) == 3
```

### Layer 2: Test with mock adapters

```python
class MockAdapter(ExecutionAdapter):
    async def execute_task(self, task):
        return EvaluationResult(task.task_id, True, "mock", {})

async def test_runner():
    runner = EvaluationRunner(
        adapter=MockAdapter(),  # No nemo_oo_agents needed!
        writer=MockWriter(),
        config=EvaluationConfig(...),
    )

    results = await runner.run_evaluation([...])
```

### Layer 3: Test adapters independently

```python
async def test_nemo_oo_agents_adapter():
    adapter = Agent006Adapter(
        agent_factory=lambda: MyAgent(),
        method_name="classify",
    )

    task = EvaluationTask("test1", {"kwargs": {"text": "foo"}})
    result = await adapter.execute_task(task)

    assert result.success
```

## Migration Path

### Phase 1: Extract Layer 0
- Move pure concurrency to `evaluation/concurrency.py`
- No domain knowledge
- Test independently

### Phase 2: Define Layer 1
- Create `evaluation/protocol.py`
- Define protocols
- No implementations yet

### Phase 3: Refactor Layer 2
- Rewrite runner to use protocols
- Remove all nemo_oo_agents knowledge
- Test with mocks

### Phase 4: Implement Layer 3
- Create concrete adapters
- Move nemo_oo_agents knowledge here
- Test independently

### Phase 5: Update Layer 4
- Update frontends to use new layers
- Preserve user-facing API

## Benefits

✅ **Testability**: Each layer tested in isolation with mocks
✅ **Flexibility**: Easy to add new execution models (not just nemo_oo_agents)
✅ **Maintainability**: Clear boundaries, single responsibility
✅ **Reusability**: Layer 0 and 2 are reusable for any evaluation
✅ **Evolution**: Can change adapters without touching runner

## Example: Adding New Execution Model

Want to add direct LLM evaluation (no nemo_oo_agents)?

**Just add a new adapter (Layer 3):**
```python
class DirectLLMAdapter(ExecutionAdapter):
    """Adapter for direct LLM calls - no nemo_oo_agents!"""

    async def execute_task(self, task):
        # Call LLM directly
        response = await llm.complete(task.data["prompt"])
        return EvaluationResult(task.task_id, True, response, {})
```

**No changes needed to:**
- Layer 0 (concurrency engine)
- Layer 1 (protocols)
- Layer 2 (runner)
- Layer 4 (frontends can use new adapter)

## Summary

**Clean separation:**
- Layer 0: Pure async (no domain)
- Layer 1: Interfaces (abstract domain)
- Layer 2: Orchestration (uses interfaces)
- Layer 3: Implementation (concrete domain)
- Layer 4: User interface

**nemo_oo_agents knowledge is ONLY in Layer 3 (adapters)**

**Runner (Layer 2) is completely generic and reusable**

This is true dependency inversion and clean architecture!

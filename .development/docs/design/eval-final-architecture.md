# Final Evaluation Architecture (No Adapter Layer)

## Simplified Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FRONTENDS (User-facing)                   │
├──────────────────────────┬──────────────────────────────────┤
│   eval_pipeline          │        evaluation.cli            │
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

**No adapter layer between frontend and runner!**

## How Frontends Use the Backend

### eval_pipeline Frontend

```python
# util/eval_pipeline/src/eval_pipeline/evaluator.py

from evaluation.runner import EvaluationRunner, EvaluationConfig
from evaluation.protocol import EvaluationTask
from evaluation.adapters.agent006_adapter import Agent006Adapter
from evaluation.adapters.benchmark_adapter import BenchmarkAdapter
from evaluation.writers.jsonl_writer import JsonlWriter

class Evaluator:
    """eval_pipeline frontend - parses YAML, creates backend objects."""

    async def run(self, models: list[str], runs: int = 1, parallel: int = 10):
        """Run evaluation using shared backend.

        No adapter layer needed - frontend directly:
        1. Decides which execution adapter to use
        2. Creates EvaluationTask objects
        3. Calls runner
        """

        for test_name, test in self.tests.items():
            # Decide which execution adapter based on test type
            if hasattr(test, 'benchmark'):
                # Benchmark test - use BenchmarkAdapter
                adapter = BenchmarkAdapter(
                    benchmark_name=test.benchmark,
                    agent_factory=lambda: test.agent_class(model=model),
                )
            else:
                # Capability test - use Agent006Adapter
                adapter = Agent006Adapter(
                    agent_factory=lambda: test.agent_class(model=model),
                    method_name=test.method,
                    enable_tracing=True,
                    trace_dir=self.output_dir / "traces",
                )

            # Create writer
            writer = JsonlWriter(
                output_file=self.output_dir / f"{test_name}.006eval.jsonl"
            )

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

            # Create tasks (frontend knows how to do this)
            tasks = self._create_tasks(test, model)

            # Run!
            results = await runner.run_evaluation(tasks)

    def _create_tasks(self, test, model) -> list[EvaluationTask]:
        """Frontend converts test data to EvaluationTask objects.

        This is frontend responsibility - it knows its data format.
        """
        if hasattr(test, 'benchmark'):
            # Benchmark: load tasks from adapter
            from evaluation.adapters import get_adapter
            benchmark = get_adapter(test.benchmark)
            benchmark_tasks = benchmark.get_tasks(split="test", limit=test.limit)

            return [
                EvaluationTask(
                    task_id=f"{test.name}_{t.id}",
                    data={"benchmark_task": t},  # Opaque to runner
                )
                for t in benchmark_tasks
            ]
        else:
            # Capability: use JSONL data
            return [
                EvaluationTask(
                    task_id=f"{test.name}_{i}",
                    data={
                        "kwargs": item["kwargs"],
                        "expected": item["expected"],
                    },
                )
                for i, item in enumerate(test.data)
            ]
```

### CLI Frontend

```python
# evaluation/cli.py

from evaluation.runner import EvaluationRunner, EvaluationConfig
from evaluation.protocol import EvaluationTask
from evaluation.adapters import Agent006Adapter, BenchmarkAdapter
from evaluation.writers import JsonlWriter
import argparse

async def main():
    """CLI frontend - parses args, creates backend objects."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", choices=["agent006", "react_agent", "direct_llm"])
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--parallel", type=int, default=10)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    # Load config (CLI knows how to do this)
    agent_factory = _get_agent_factory(args.config)

    # Create execution adapter (CLI decides based on args)
    adapter = BenchmarkAdapter(
        benchmark_name=args.benchmark,
        agent_factory=agent_factory,
    )

    # Create writer
    output_file = Path(f"results/{args.config}_{args.benchmark}.006eval.jsonl")
    writer = JsonlWriter(output_file=output_file)

    # Create runner config
    config = EvaluationConfig(
        max_concurrent_tasks=args.parallel,
        resume_from=output_file.with_suffix('.checkpoint.json') if args.resume else None,
        output_file=output_file,
    )

    # Create runner
    runner = EvaluationRunner(
        adapter=adapter,
        writer=writer,
        config=config,
    )

    # Load tasks (CLI knows how to do this)
    from evaluation.adapters import get_adapter
    benchmark = get_adapter(args.benchmark)
    benchmark_tasks = benchmark.get_tasks(split="test", limit=args.limit)

    tasks = [
        EvaluationTask(
            task_id=t.id,
            data={"benchmark_task": t},
        )
        for t in benchmark_tasks
    ]

    # Run!
    results = await runner.run_evaluation(tasks)
    print(f"Completed: {len(results)} tasks")

def _get_agent_factory(config_name: str):
    """CLI knows how to create agent factories."""
    if config_name == "agent006":
        from agents.agent006_tools import ToolsAgent
        return lambda: ToolsAgent()
    elif config_name == "react_agent":
        from agents.react_agent import ReactAgent
        return lambda: ReactAgent()
    # etc.
```

## What Each Component Does

### Frontends (eval_pipeline, CLI)

**Responsibilities:**
- Parse user input (YAML or CLI args)
- Decide which execution adapter to use
- Create `EvaluationTask` objects from user data
- Configure runner
- Call `runner.run_evaluation()`

**Does NOT need an adapter layer - just creates the right objects!**

### Runner (evaluation/runner.py)

**Responsibilities:**
- Orchestrate execution using concurrency engine
- Load/save checkpoints
- Call adapter.execute_task() for each task
- Pass results to writer

**Receives everything it needs from frontend.**

### Execution Adapters (Layer 3)

**Responsibilities:**
- Implement `ExecutionAdapter` protocol
- Execute tasks in domain-specific way
- Know about agent006, benchmarks, etc.

**Created by frontend, used by runner.**

## Frontend Differences

| Aspect | eval_pipeline | CLI |
|--------|---------------|-----|
| **Input parsing** | YAML → Python objects | argparse → args |
| **Agent creation** | From config `agent.module.class` | From string `"agent006"` |
| **Task creation** | From JSONL or benchmark | From benchmark |
| **Adapter selection** | Based on test type | Based on args |
| **Config** | YAML models section | CLI flags |

**Both create same backend objects:**
- `EvaluationTask`
- `ExecutionAdapter` (Agent006 or Benchmark)
- `EvaluationRunner`
- `ResultWriter`

## Why No Adapter Layer?

**Original thought:** Need layer to translate between frontends and runner.

**Reality:** Frontends already know how to create the objects runner needs!

```python
# Frontend already does this:
tasks = [EvaluationTask(...) for item in data]
adapter = Agent006Adapter(...)
runner = EvaluationRunner(adapter=adapter, ...)

# No need for intermediate layer!
```

**The translation happens inside the frontend** - that's already its job (parse user input → create objects).

## Example: Adding New Frontend

Want a web UI frontend?

```python
# frontends/web_ui.py

from flask import Flask, request
from evaluation.runner import EvaluationRunner
from evaluation.adapters import Agent006Adapter

app = Flask(__name__)

@app.route("/run", methods=["POST"])
async def run_evaluation():
    # Parse web request (frontend responsibility)
    config = request.json

    # Create adapter (frontend decides)
    adapter = Agent006Adapter(
        agent_factory=lambda: get_agent(config["agent"]),
        method_name=config["method"],
    )

    # Create runner
    runner = EvaluationRunner(adapter=adapter, ...)

    # Create tasks (frontend knows format)
    tasks = [
        EvaluationTask(task_id=str(i), data=item)
        for i, item in enumerate(config["tasks"])
    ]

    # Run!
    results = await runner.run_evaluation(tasks)
    return jsonify(results)
```

**No adapter layer needed - web frontend directly creates backend objects!**

## Simplified Layer Diagram

```
Layer 4: Frontends
         ↓ (creates)
Layer 3: Execution Adapters ────┐
         ↓ (passed to)           │
Layer 2: Runner ←────────────────┘
         ↓ (uses)
Layer 1: Protocols
         ↓ (implements)
Layer 0: Concurrency Engine
```

**No translation layer between 4 and 2!**

Frontend directly creates Layer 3 adapters and passes to Layer 2 runner.

## Benefits of No Adapter Layer

✅ **Simpler** - One less layer to understand
✅ **More direct** - Frontend → Runner, no indirection
✅ **Clearer responsibility** - Frontend's job is to create backend objects
✅ **Less code** - No adapter layer to maintain
✅ **Easier testing** - Test frontend and runner separately

## Summary

**Original plan had:**
```
Frontend → Adapter Layer → Runner
```

**Better design:**
```
Frontend → Runner (with execution adapters)
```

**Frontends already know:**
- How to parse their input format
- What execution adapter to use
- How to create tasks

**They don't need an adapter layer - they just create the objects runner needs!**

This is cleaner, simpler, and more direct. The frontend IS the adapter from user-space to backend-space.

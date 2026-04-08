# Resume/Restart Architecture

## How Resume Works with Clean Layers

**Key insight:** Checkpointing is a **generic concurrency concern**, not an evaluation concern.

The concurrency engine just tracks:
- Which task IDs completed
- Whether they succeeded or failed
- Their results (opaque data)

**Adapters don't need to know about checkpointing!**

## Flow Diagram

```
User runs evaluation (100 tasks)
    ↓
50 tasks complete, program crashes
    ↓
Checkpoint file created:
{
  "task_1": {"completed": true, "result": {...}},
  "task_2": {"completed": true, "result": {...}},
  ...
  "task_50": {"completed": true, "result": {...}}
}
    ↓
User restarts with --resume-from checkpoint.json
    ↓
Runner loads checkpoint
    ↓
Runner tells concurrency engine: "skip task_1 through task_50"
    ↓
Concurrency engine only executes task_51-100
    ↓
Adapters execute as normal (they don't know about restart!)
```

## Implementation

### Layer 0: Concurrency Engine with Resume

```python
# evaluation/concurrency.py

from dataclasses import dataclass
from typing import Any

@dataclass
class TaskState:
    """Generic checkpoint state - no domain knowledge."""
    task_id: str
    completed: bool
    result: Any | None = None  # Opaque - engine doesn't look inside
    error: str | None = None
    timestamp: float | None = None

class ConcurrencyEngine:
    """Pure concurrency engine with resume support."""

    async def run_tasks(
        self,
        task_ids: list[str],
        task_fn: Callable[[str], Awaitable[R]],
        config: ConcurrencyConfig,
        checkpoint_state: list[TaskState] | None = None,
        on_task_complete: Callable[[str, R | Exception], None] | None = None,
    ) -> list[R]:
        """Run tasks with optional resume from checkpoint.

        Args:
            task_ids: All task IDs to run
            task_fn: Function to execute each task
            config: Concurrency config
            checkpoint_state: Optional previous state for resume
            on_task_complete: Callback after each task (for incremental writes)

        The engine:
        1. Identifies which tasks already completed (from checkpoint)
        2. Skips those tasks, returns cached results
        3. Only executes incomplete tasks
        4. Calls on_task_complete for incremental checkpoint updates

        The engine does NOT know:
        - What tasks contain
        - What results mean
        - How to serialize/deserialize results (just passes them through)
        """
        # Build map of completed tasks
        completed: dict[str, TaskState] = {}
        if checkpoint_state:
            completed = {
                state.task_id: state
                for state in checkpoint_state
                if state.completed and state.result is not None
            }

        # Separate into completed and pending
        results_dict: dict[str, R] = {}
        pending_ids: list[str] = []

        for task_id in task_ids:
            if task_id in completed:
                # Use cached result (opaque - we don't look inside)
                results_dict[task_id] = completed[task_id].result
            else:
                pending_ids.append(task_id)

        # Only run pending tasks
        if pending_ids:
            semaphore = asyncio.Semaphore(config.max_concurrent)

            async def run_with_checkpoint(task_id: str) -> R:
                """Execute and notify for incremental checkpoint."""
                try:
                    async with semaphore:
                        if config.timeout_seconds:
                            result = await asyncio.wait_for(
                                task_fn(task_id),
                                timeout=config.timeout_seconds,
                            )
                        else:
                            result = await task_fn(task_id)

                    # Notify for incremental checkpoint
                    if on_task_complete:
                        on_task_complete(task_id, result)

                    return result

                except Exception as e:
                    # Notify about error for checkpoint
                    if on_task_complete:
                        on_task_complete(task_id, e)
                    raise

            # Run all pending tasks
            tasks = [run_with_checkpoint(tid) for tid in pending_ids]
            pending_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Add to results dict
            for task_id, result in zip(pending_ids, pending_results):
                results_dict[task_id] = result

        # Return results in original order
        return [results_dict[tid] for tid in task_ids]
```

### Layer 2: Runner with Checkpoint Management

```python
# evaluation/runner.py

import json
from pathlib import Path
from datetime import datetime

class EvaluationRunner:
    """Runner with checkpoint/resume support."""

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
        self.checkpoint_file = config.output_file.with_suffix('.checkpoint.json')

    async def run_evaluation(
        self,
        tasks: list[EvaluationTask],
    ) -> list[EvaluationResult]:
        """Run evaluation with resume support."""

        # Load checkpoint if resuming
        checkpoint = None
        if self.config.resume_from:
            checkpoint = self._load_checkpoint(self.config.resume_from)
            print(f"Resuming from checkpoint: {len(checkpoint)} tasks cached")

        # Extract task IDs and create map
        task_ids = [t.task_id for t in tasks]
        task_map = {t.task_id: t for t in tasks}

        # Wrapper that calls adapter
        async def execute_one(task_id: str) -> EvaluationResult:
            task = task_map[task_id]
            return await self.adapter.execute_task(task)

        # Progress callback for incremental checkpoint
        def handle_completion(task_id: str, result: EvaluationResult | Exception):
            """Called after each task - writes result and updates checkpoint."""
            if isinstance(result, Exception):
                result = EvaluationResult(
                    task_id=task_id,
                    success=False,
                    output=None,
                    metadata={"error": str(result)},
                )

            # Write result incrementally
            self.writer.write_result(result)

            # Update checkpoint incrementally
            self._save_checkpoint_entry(task_id, result)

        # Run tasks (engine handles resume logic)
        results = await self.engine.run_tasks(
            task_ids=task_ids,
            task_fn=execute_one,
            config=ConcurrencyConfig(
                max_concurrent=self.config.max_concurrent_tasks,
                timeout_seconds=self.config.timeout_seconds,
            ),
            checkpoint_state=checkpoint,
            on_task_complete=handle_completion,
        )

        # Finalize
        self.writer.finalize({"total": len(results)})
        return results

    def _load_checkpoint(self, checkpoint_path: Path) -> list[TaskState]:
        """Load checkpoint file.

        Format:
        {
          "task_1": {"completed": true, "result": {...}, "timestamp": ...},
          "task_2": {"completed": false, "error": "...", "timestamp": ...},
          ...
        }

        The 'result' field is OPAQUE - we don't interpret it.
        We just load and pass through to engine.
        """
        if not checkpoint_path.exists():
            return []

        with open(checkpoint_path) as f:
            data = json.load(f)

        return [
            TaskState(
                task_id=task_id,
                completed=entry["completed"],
                result=entry.get("result"),
                error=entry.get("error"),
                timestamp=entry.get("timestamp"),
            )
            for task_id, entry in data.items()
        ]

    def _save_checkpoint_entry(self, task_id: str, result: EvaluationResult):
        """Update checkpoint file incrementally after each task.

        This is crash-safe - if we crash after task_50, we have
        task_1 through task_50 in checkpoint.
        """
        # Load existing checkpoint
        checkpoint = {}
        if self.checkpoint_file.exists():
            with open(self.checkpoint_file) as f:
                checkpoint = json.load(f)

        # Add/update entry (result is opaque - just serialize it)
        checkpoint[task_id] = {
            "completed": True,
            "result": result.model_dump() if hasattr(result, 'model_dump') else result.__dict__,
            "timestamp": datetime.now().isoformat(),
        }

        # Write atomically (write to temp, then rename)
        temp_file = self.checkpoint_file.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            json.dump(checkpoint, f, indent=2)
        temp_file.replace(self.checkpoint_file)
```

### Layer 3: Adapters Are Oblivious

```python
# evaluation/adapters/nemo_oo_agents_adapter.py

class Agent006Adapter(ExecutionAdapter):
    """Adapter has NO KNOWLEDGE of checkpointing!"""

    async def execute_task(self, task: EvaluationTask) -> EvaluationResult:
        """Just execute the task - no checkpoint logic needed.

        If this task was already completed and cached in checkpoint,
        the concurrency engine won't even call this method.

        If this task needs to run, we execute it normally.

        Either way, adapter doesn't need to know about checkpoints!
        """
        # Set up tracing
        trace_file = self._setup_trace(task.task_id)

        # Execute agent
        agent = self.agent_factory()
        method = getattr(agent, self.method_name)
        output = await method(**task.data["kwargs"])

        # Return result (runner will checkpoint this)
        return EvaluationResult(
            task_id=task.task_id,
            success=True,
            output=output,
            metadata={
                "trace_file": str(trace_file),
                "expected": task.data.get("expected"),
            },
        )
```

## Checkpoint Format

**Generic JSON format:**

```json
{
  "task_1": {
    "completed": true,
    "result": {
      "task_id": "task_1",
      "success": true,
      "output": "positive",
      "metadata": {
        "trace_file": "traces/task_1.006trace.jsonl",
        "expected": "positive"
      }
    },
    "timestamp": "2026-01-14T12:34:56"
  },
  "task_2": {
    "completed": false,
    "error": "TimeoutError: Task timed out after 60s",
    "timestamp": "2026-01-14T12:35:23"
  },
  "task_3": {
    "completed": true,
    "result": {
      "task_id": "task_3",
      "success": false,
      "output": null,
      "metadata": {
        "error": "Agent returned incorrect output"
      }
    },
    "timestamp": "2026-01-14T12:36:01"
  }
}
```

**Key points:**
- Task-level checkpointing (not step-level)
- Result is opaque (runner doesn't interpret)
- Works for any adapter type
- Incremental writes (crash-safe)

## Multi-Step Environments

**Question:** What about multi-step benchmarks like InterCode? If we crash at step 10 of 20, do we resume from step 10?

**Answer:** No - we restart the whole task.

**Why:**
- Much simpler (no need to serialize environment state)
- Matches current run_ablation.py behavior
- Environment state is in adapter, not checkpoint
- Still valuable (resume at task level, not step level)

**Example:**
```
100 InterCode tasks, each with 20 steps
    ↓
Task 1-50 complete (all 20 steps each)
Task 51 crashes at step 10
    ↓
Checkpoint has task 1-50 marked complete
    ↓
Resume reruns task 51 from step 1 (not step 10)
```

**If you REALLY wanted step-level resume:**
You'd need to:
1. Make adapter serialize environment state
2. Store in checkpoint result
3. Adapter checks for cached state on start
4. Restores environment to that state

But this adds complexity and isn't needed for most use cases.

## Usage Examples

### eval_pipeline with Resume

```yaml
# config.yaml
name: bfcl_eval
test_suite:
  - name: bfcl
    benchmark: bfcl
    limit: 1000
```

```bash
# First run (crashes after 500 tasks)
python -m eval_pipeline --config config.yaml

# Resume from checkpoint
python -m eval_pipeline --config config.yaml --resume
```

### CLI with Resume

```bash
# First run
python run_ablation.py --config nemo_oo_agents --benchmark bfcl --limit 1000

# Crashes after 500 tasks, checkpoint saved to:
# experiments/results/nemo_oo_agents_bfcl.checkpoint.json

# Resume
python run_ablation.py \
  --config nemo_oo_agents \
  --benchmark bfcl \
  --limit 1000 \
  --resume-from experiments/results/nemo_oo_agents_bfcl.checkpoint.json
```

### Programmatic API

```python
from evaluation.runner import EvaluationRunner, EvaluationConfig
from evaluation.adapters import Agent006Adapter

# Configure with resume
config = EvaluationConfig(
    max_concurrent_tasks=10,
    output_file=Path("results.jsonl"),
    resume_from=Path("results.checkpoint.json"),  # Resume from here
)

runner = EvaluationRunner(
    adapter=Agent006Adapter(...),
    writer=JsonlWriter(...),
    config=config,
)

# Runner automatically:
# 1. Loads checkpoint
# 2. Skips completed tasks
# 3. Only runs remaining tasks
results = await runner.run_evaluation(tasks)
```

## Incremental Checkpointing

**Checkpoint is updated after EACH task:**

```
Task 1 completes → Write to checkpoint
Task 2 completes → Write to checkpoint
Task 3 completes → Write to checkpoint
[CRASH]
Task 4 starts...
```

**After crash:**
- Checkpoint has task 1-3
- Resume skips 1-3, starts at task 4

**Implementation uses atomic writes:**
```python
# Write to temp file
with open('checkpoint.tmp', 'w') as f:
    json.dump(checkpoint, f)

# Atomic rename (crash-safe)
Path('checkpoint.tmp').replace('checkpoint.json')
```

## Failed Tasks

**Question:** What happens to failed tasks?

**Answer:** They're marked as completed (with error) in checkpoint.

**On resume:**
- Skip successful tasks (use cached result)
- **Re-run failed tasks** (give them another chance)

**Implementation:**
```python
def _load_checkpoint(self, checkpoint_path):
    # Only skip successfully completed tasks
    return [
        TaskState(task_id=tid, completed=True, result=entry["result"])
        for tid, entry in checkpoint.items()
        if entry["completed"] and not entry.get("error")
    ]
    # Failed tasks (with errors) are NOT in checkpoint
    # So they'll be re-run
```

**Alternative:** Add `--skip-failed` flag to skip failed tasks too.

## Two-Level Resume (Ablation Matrices)

**For ablation matrices (multiple configs × benchmarks):**

```python
# Top-level checkpoint: which (config, benchmark) pairs completed
ablation_checkpoint = {
  "nemo_oo_agents_bfcl": {"completed": True, "pass_rate": 0.85},
  "nemo_oo_agents_livecodebench": {"completed": False, "error": "..."},
  "react_agent_bfcl": {"completed": True, "pass_rate": 0.72},
}

# Per-benchmark checkpoint: which tasks completed
bfcl_checkpoint = {
  "task_1": {"completed": True, "result": {...}},
  "task_2": {"completed": True, "result": {...}},
  ...
}
```

**Resume skips at both levels:**
1. Skip completed (config, benchmark) pairs
2. Within running pairs, skip completed tasks

## Benefits

✅ **Crash-safe** - Checkpoint written after every task
✅ **Incremental** - Resume from exact point of failure
✅ **Generic** - Works for any adapter type
✅ **Clean separation** - Adapters don't know about checkpointing
✅ **Testable** - Can test checkpointing with mock adapters
✅ **Task-level** - Simple (no environment state serialization)

## Summary

**Resume works perfectly with clean architecture:**

- **Layer 0 (Engine)**: Handles skip logic for completed tasks
- **Layer 2 (Runner)**: Loads/saves checkpoint files
- **Layer 3 (Adapters)**: Completely unaware of checkpointing
- **Checkpoint format**: Generic JSON with opaque results

**Adapters just execute tasks. If a task was already done, engine doesn't call adapter. Adapter never knows about restarts.**

This is the beauty of clean architecture - orthogonal concerns (concurrency, checkpointing) don't leak into domain logic (execution)!

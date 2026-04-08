# NeMo-Agent-Toolkit Evaluation System Analysis

## Executive Summary

**Recommendation: Do NOT use NeMo-Agent-Toolkit's evaluation system as our backend.**

**Reason**: Architecture mismatch - NAT's evaluation system is deeply coupled to their workflow framework, violating our key requirement that "none of the internals of agent006 leak into the parallel runner."

**Instead**: Continue with our clean 5-layer architecture, but adopt several good patterns from NAT.

---

## NeMo-Agent-Toolkit Evaluation Architecture

### Core Components

Located in `3p/NeMo-Agent-Toolkit/src/nat/eval/`:

1. **evaluate.py** - Main orchestration (`EvaluationRun` class)
2. **config.py** - Configuration models
3. **evaluator/base_evaluator.py** - Abstract evaluator base class
4. **evaluator/evaluator_model.py** - Data models
5. **dataset_handler/** - Dataset loading and preprocessing

### Data Model

```python
class EvalInputItem(BaseModel):
    id: Any
    input_obj: Any                      # Input to workflow
    expected_output_obj: Any            # Expected answer
    output_obj: Any = None              # Populated by workflow
    expected_trajectory: list[IntermediateStep] = []
    trajectory: list[IntermediateStep] = []  # Populated by workflow
    full_dataset_entry: Any

class EvalInput(BaseModel):
    eval_input_items: list[EvalInputItem]

class EvalOutputItem(BaseModel):
    id: Any
    score: Any          # float or any serializable type
    reasoning: Any

class EvalOutput(BaseModel):
    average_score: Any
    eval_output_items: list[EvalOutputItem]
```

### Execution Flow

**Two-phase approach:**

1. **Phase 1: Run Workflow** (lines 167-268 in evaluate.py)
   ```python
   async def run_workflow_local(self, session_manager: SessionManager):
       async def run_one(item: EvalInputItem):
           async with session_manager.session(user_id=...) as session:
               async with session.run(item.input_obj, ...) as runner:
                   base_output = await runner.result()
                   intermediate_steps = await pull_intermediate()

                   item.output_obj = output
                   item.trajectory = intermediate_steps

       # Run all items in parallel
       await asyncio.gather(*[wrapped_run(item) for item in eval_input_items])
   ```

2. **Phase 2: Run Evaluators** (lines 486-501 in evaluate.py)
   ```python
   async def run_evaluators(self, evaluators: dict[str, Any]):
       tasks = [self.run_single_evaluator(name, evaluator)
                for name, evaluator in evaluators.items()]
       await asyncio.gather(*tasks)
   ```

### Concurrency Implementation

**Uses same patterns as ours:**

```python
# In base_evaluator.py
class BaseEvaluator(ABC):
    def __init__(self, max_concurrency: int = 4, tqdm_desc: str = "Evaluating"):
        self.max_concurrency = max_concurrency
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def evaluate(self, eval_input: EvalInput) -> EvalOutput:
        async def wrapped(item):
            async with self.semaphore:
                output_item = await self.evaluate_item(item)
                return output_item

        output_items = await asyncio.gather(*[wrapped(item)
                                               for item in eval_input.eval_input_items])
```

**Exactly like our Layer 0!** Semaphore + asyncio.gather + progress tracking.

### Resume/Checkpoint Support

**Simple approach** (lines 249-255 in evaluate.py):

```python
if self.config.skip_completed_entries:
    eval_input_items = [item for item in self.eval_input.eval_input_items
                        if not item.output_obj]
    if not eval_input_items:
        logger.warning("All items have a non-empty output. Skipping workflow pass.")
        return
```

**Strategy**: If `output_obj` field is non-empty, skip that item.

Simpler than our checkpoint approach, but less explicit about success/failure.

### Usage Stats Tracking

**Excellent pattern** (lines 112-165 in evaluate.py):

```python
def _compute_usage_stats(self, item: EvalInputItem):
    """Compute usage stats for a single item using intermediate steps"""
    usage_stats_per_llm = {}
    total_tokens = 0

    for step in steps:
        if step.event_type == "LLM_END":
            llm_name = step.llm_name
            usage_stats_per_llm[llm_name].prompt_tokens += step.token_usage.prompt_tokens
            usage_stats_per_llm[llm_name].completion_tokens += ...
            usage_stats_per_llm[llm_name].reasoning_tokens += ...
            total_tokens += step.token_usage.total_tokens

    # Calculate p95 LLM latency
    llm_latencies = [...]
    llm_latency = float(np.percentile(llm_latencies, 95))

    return UsageStatsItem(
        usage_stats_per_llm=usage_stats_per_llm,
        runtime=runtime,
        total_tokens=total_tokens,
        llm_latency=llm_latency
    )
```

**Per-task metrics:**
- Token counts (prompt, completion, reasoning, cached)
- Runtime (start to end timestamp)
- LLM latency (p95 of all LLM calls)
- Per-LLM breakdown

**Aggregate metrics:**
- Total runtime across all tasks
- Sum of tokens per LLM

**This is valuable for benchmark analysis!**

### Dataset Handling

**DatasetHandler supports:**
- Multiple formats: JSON, JSONL, CSV, Excel, Parquet
- Filtering by fields
- Deduplication
- Replication (run same item N times with `reps` parameter)
- Dataset size adjustment (align to concurrency multiples)

**Example:**
```python
dataset_handler = DatasetHandler(
    dataset_config=dataset_config,
    reps=3,  # Run each item 3 times
    concurrency=10,
    adjust_dataset_size=True,  # Adjust to multiple of 10
)
eval_input = dataset_handler.get_eval_input_from_dataset(dataset_file)
```

### Configuration System

```python
class EvaluationRunConfig(BaseModel):
    config_file: Path | BaseModel
    dataset: str | None = None
    result_json_path: str = "$"
    skip_workflow: bool = False
    skip_completed_entries: bool = False
    endpoint: str | None = None  # Remote execution
    endpoint_timeout: int = 300
    reps: int = 1
    override: tuple[tuple[str, str], ...] = ()
    write_output: bool = True
    adjust_dataset_size: bool = False
    num_passes: int = 0
    export_timeout: float = 60.0
    user_id: str = "nat_eval_user_id"
```

**Supports:**
- YAML config files
- Programmatic config (Pydantic models)
- Override mechanism for config values
- Remote execution via endpoint
- Resume via `skip_completed_entries`

### Output Structure

```python
class EvaluationRunOutput(BaseModel):
    workflow_output_file: Path | None
    evaluator_output_files: list[Path]
    workflow_interrupted: bool
    eval_input: EvalInput
    evaluation_results: list[tuple[str, EvalOutput]]
    usage_stats: UsageStats | None
    profiler_results: ProfilerResults
    config_original_file: Path | None
    config_effective_file: Path | None
    config_metadata_file: Path | None
```

**Writes multiple output files:**
- `workflow_output.json` - Full input/output/trajectory data
- `{evaluator_name}_output.json` - Per-evaluator scores
- `config_original.yml` - Original config file
- `config_effective.yml` - Config with overrides applied
- `config_metadata.json` - Metadata about run

**Good pattern for reproducibility!**

---

## Why NAT Evaluation Won't Work for Us

### 1. Architecture Coupling

**Critical issue**: NAT's evaluation is deeply integrated with their workflow framework.

**Evidence** (lines 184-238 in evaluate.py):

```python
async def run_one(item: EvalInputItem):
    async with session_manager.session(user_id=self.config.user_id) as session:
        async with session.run(item.input_obj, runtime_type=RuntimeTypeEnum.EVALUATE) as runner:
            if not session.workflow.has_single_output:
                raise NotImplementedError("Multiple outputs are not supported")

            runner_result = None
            intermediate_future = None

            intermediate_future = pull_intermediate()
            runner_result = runner.result()
            base_output = await runner_result
            intermediate_steps = await intermediate_future
```

**Dependencies on NAT internals:**
- `SessionManager` - NAT's session management
- `session.workflow` - NAT's workflow objects
- `RuntimeTypeEnum` - NAT-specific runtime types
- `pull_intermediate()` - NAT-specific intermediate step collection
- `WorkflowEvalBuilder` - NAT's workflow builder

**To use this, we'd need to:**
1. Adopt NAT's SessionManager
2. Wrap agent006 in NAT's workflow framework
3. Integrate with NAT's runtime system

**This violates our key requirement**: "Can you make it so that none of the internals of agent006 leak into the parallel runner part?"

With NAT, agent006 would need to become a NAT workflow, and NAT-specific knowledge would leak into our runner.

### 2. Two-Phase Execution Model

**NAT's model:**
```
Phase 1: Run all workflows → populate output_obj
Phase 2: Run all evaluators → produce scores
```

**This doesn't fit our benchmarks:**

- **BFCL**: Needs to call agent, then immediately score with Berkeley's scorer
- **InterCode**: Multi-turn environment simulation with step-by-step scoring
- **TAU-Bench**: Retail environment with complex state management
- **LiveCodeBench**: Code execution and test validation

**Our adapter pattern is more flexible** - each adapter decides how to:
- Execute the task (single-turn, multi-turn, with environment, etc.)
- Score the output (immediate, deferred, multi-step, etc.)
- Structure the result

NAT's two-phase model assumes:
1. Run workflow to get output
2. Evaluate output separately

But some benchmarks need execution and evaluation interleaved.

### 3. Domain Knowledge Leakage

**NAT's system knows about:**

```python
class EvalInputItem(BaseModel):
    input_obj: Any
    expected_output_obj: Any
    output_obj: Any
    trajectory: list[IntermediateStep]  # NAT-specific!
    expected_trajectory: list[IntermediateStep]  # NAT-specific!
```

**NAT-specific concepts:**
- Intermediate steps
- Trajectories
- Workflow sessions
- Runtime types

**Our Layer 0 concurrency engine:**
```python
async def run_tasks(
    task_ids: list[str],              # Just IDs!
    task_fn: Callable[[str], Awaitable[R]],  # Generic function!
    config: ConcurrencyConfig,         # Pure concurrency config!
):
    # Knows NOTHING about:
    # - What tasks contain
    # - How to execute them
    # - What results mean
```

**NAT's approach violates clean architecture principles** - the evaluation framework has domain knowledge baked in.

### 4. Not a Reusable Primitive

**NAT's evaluation system** is a complete framework:
- Workflow execution
- Session management
- Dataset handling
- Evaluator orchestration
- Output formatting
- Profiling integration
- Weave/MLflow logging

**It's "all or nothing"** - you can't just take the concurrency engine.

**Our Layer 0** is a pure primitive:
- Generic async task execution
- Semaphore-based concurrency
- Timeout support
- Checkpoint/resume
- Progress callbacks

**Reusable for ANY parallel workload**, not just evaluation.

---

## What We Should Learn From NAT

Despite the architecture mismatch, NAT has excellent patterns we should adopt:

### 1. ✅ Usage Stats Tracking

**Adopt this pattern:**

```python
@dataclass
class TaskUsageStats:
    """Usage statistics for a single task."""
    task_id: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    runtime_seconds: float
    llm_latency_p95: float
    usage_per_llm: dict[str, dict]  # Per-model breakdown
    start_timestamp: float
    end_timestamp: float

class UsageStatsCollector:
    """Collect and aggregate usage stats across tasks."""

    def __init__(self):
        self.stats: dict[str, TaskUsageStats] = {}

    def record_task(self, task_id: str, stats: TaskUsageStats):
        self.stats[task_id] = stats

    def aggregate(self) -> dict:
        """Compute aggregate statistics."""
        return {
            "total_tasks": len(self.stats),
            "total_tokens": sum(s.total_tokens for s in self.stats.values()),
            "total_runtime": max(s.end_timestamp for s in self.stats.values()) -
                           min(s.start_timestamp for s in self.stats.values()),
            "avg_latency": np.mean([s.llm_latency_p95 for s in self.stats.values()]),
            "per_llm": self._aggregate_per_llm(),
        }
```

**Benefits:**
- Track token usage per benchmark run
- Measure latency characteristics
- Compare models on cost/performance
- Identify performance bottlenecks

**Implementation:** Add to Layer 2 (Runner) or Layer 3 (Adapters)

### 2. ✅ Progress Bars with tqdm

**Adopt this pattern:**

```python
from tqdm import tqdm

class EvaluationRunner:
    async def run_evaluation(self, tasks: list[EvaluationTask]):
        pbar = tqdm(total=len(tasks), desc="Running evaluation")

        def on_complete(task_id: str, result: Any):
            pbar.update(1)
            # Also write to checkpoint
            self._save_checkpoint_entry(task_id, result)

        results = await self.engine.run_tasks(
            task_ids=[t.task_id for t in tasks],
            task_fn=execute_one,
            config=self.config,
            on_task_complete=on_complete,  # Update progress!
        )

        pbar.close()
        return results
```

**Benefits:**
- Visual feedback for long-running evals
- Shows estimated completion time
- Helps identify stalls

### 3. ✅ Error Handling Per Task

**Adopt this pattern** (from base_evaluator.py):

```python
async def wrapped(item):
    async with self.semaphore:
        try:
            output_item = await self.evaluate_item(item)
            pbar.update(1)
            return output_item
        except Exception as e:
            # Don't fail entire batch!
            pbar.update(1)
            return EvalOutputItem(
                id=item.id,
                score=0.0,
                reasoning={"error": f"Evaluator error: {str(e)}"}
            )
```

**Benefits:**
- One failed task doesn't stop entire evaluation
- Errors are captured in results
- Can continue processing remaining tasks

**Our current implementation** (concurrency.py) already does this via `return_exceptions=True` in gather():

```python
pending_results = await asyncio.gather(*tasks, return_exceptions=True)
```

We should formalize error handling in adapters.

### 4. ✅ Output Structure

**Adopt this pattern:**

```python
@dataclass
class EvaluationRunOutput:
    """Complete output of an evaluation run."""

    # Data files
    results_file: Path              # Main results JSONL
    checkpoint_file: Path           # Checkpoint for resume

    # Config files
    config_original_file: Path      # Original config
    config_effective_file: Path     # With overrides applied
    config_metadata_file: Path      # Metadata about run

    # Stats
    usage_stats_file: Path          # Token/latency stats
    summary_file: Path              # Aggregate metrics

    # Status
    completed: bool
    interrupted: bool
    total_tasks: int
    successful_tasks: int
    failed_tasks: int
```

**Benefits:**
- Complete reproducibility
- Easy to analyze results
- Config versioning
- Audit trail

### 5. ✅ Dataset Handling Utilities

**Consider adopting:**

```python
class DatasetLoader:
    """Load datasets from multiple formats."""

    @staticmethod
    def load(file_path: Path) -> list[dict]:
        """Load from JSON, JSONL, CSV, Excel, Parquet."""
        if file_path.suffix == '.json':
            return json.loads(file_path.read_text())
        elif file_path.suffix == '.jsonl':
            return [json.loads(line) for line in file_path.read_text().splitlines()]
        elif file_path.suffix == '.csv':
            return pd.read_csv(file_path).to_dict('records')
        elif file_path.suffix in ['.xlsx', '.xls']:
            return pd.read_excel(file_path).to_dict('records')
        elif file_path.suffix == '.parquet':
            return pd.read_parquet(file_path).to_dict('records')
        else:
            raise ValueError(f"Unsupported format: {file_path.suffix}")
```

**Benefits:**
- Support diverse dataset formats
- Easier for users to provide test data
- Compatible with benchmark datasets

---

## Comparison: NAT vs Our Clean Architecture

| Aspect | NeMo-Agent-Toolkit | Our 5-Layer Architecture |
|--------|-------------------|-------------------------|
| **Concurrency** | Semaphore + gather | ✅ Same (Layer 0) |
| **Domain Knowledge** | ❌ Coupled to NAT workflows | ✅ Isolated in Layer 3 |
| **Execution Model** | Two-phase (run, then evaluate) | ✅ Flexible (adapter decides) |
| **Reusability** | ❌ All-or-nothing framework | ✅ Layer 0 is pure primitive |
| **Agent Integration** | Must wrap as NAT workflow | ✅ Direct agent006 calls |
| **Benchmark Adapters** | Would need custom workflows | ✅ Native adapter support |
| **Checkpoint/Resume** | ✅ `skip_completed_entries` | ✅ Generic checkpoint |
| **Usage Stats** | ✅ Excellent tracking | ⚠️ Need to add |
| **Progress Tracking** | ✅ tqdm integration | ⚠️ Need to add |
| **Error Handling** | ✅ Per-task try/catch | ✅ Already supported |
| **Output Structure** | ✅ Multiple files + metadata | ⚠️ Need to formalize |
| **Dataset Loading** | ✅ Multi-format support | ⚠️ Currently JSONL only |

---

## Recommendation

### Don't Use NAT's Evaluation System

**Reasons:**
1. Architecture mismatch - requires adopting NAT's workflow framework
2. Violates clean separation - agent006 knowledge would leak into runner
3. Two-phase model doesn't fit our benchmark needs
4. Not a reusable primitive - all-or-nothing framework

### Continue With Our Clean Architecture

**Our advantages:**
- ✅ Pure concurrency engine (Layer 0) with no domain knowledge
- ✅ Protocol-based adapters (Layer 3) with full flexibility
- ✅ Direct agent006 integration without wrappers
- ✅ Supports diverse benchmark patterns
- ✅ Truly reusable across different domains

### Adopt Good Patterns From NAT

**Add to our implementation:**

1. **Usage stats tracking** (Layer 2 or Layer 3)
   - Token counts per task
   - Latency measurements (p95)
   - Per-LLM breakdown
   - Aggregate statistics

2. **Progress bars** (Layer 2)
   - tqdm integration
   - Show completion estimate
   - Update on each task completion

3. **Formalize error handling** (Layer 3)
   - Per-task try/catch
   - Return error results instead of failing batch
   - Include error details in result metadata

4. **Output structure** (Layer 2)
   - Multiple output files
   - Save config (original + effective)
   - Save metadata (timestamp, args, overrides)
   - Usage stats file
   - Summary file

5. **Dataset utilities** (Frontends)
   - Support JSON, JSONL, CSV, Excel, Parquet
   - Filtering and deduplication
   - Dataset replication (reps)

---

## Implementation Plan Updates

Based on NAT analysis, update our plan:

### Phase 1: Core Implementation (Current)
- ✅ Layer 0: Concurrency engine (DONE)
- ⏳ Layer 1: Protocol definitions (IN PROGRESS)
- ⏳ Layer 2: Evaluation runner (IN PROGRESS)
- ⏳ Layer 3: Adapters (IN PROGRESS)

### Phase 2: Enhancements (From NAT Patterns)
- Add usage stats tracking
- Add progress bars with tqdm
- Formalize error handling in adapters
- Improve output structure
- Add dataset format support

### Phase 3: Frontend Updates
- Update eval_pipeline to use new backend
- Update run_ablation CLI to use new backend
- Test with capability tests
- Test with benchmark adapters

---

## Conclusion

**NeMo-Agent-Toolkit has an excellent evaluation system** for their use case (framework-agnostic workflow evaluation with standard metrics).

**But it doesn't fit our requirements** because:
- We need clean separation between agent006 and the parallel runner
- We need flexible execution patterns for diverse benchmarks
- We want a reusable concurrency primitive, not a complete framework

**Instead**, we should:
- ✅ Continue with our clean 5-layer architecture
- ✅ Adopt usage stats, progress tracking, and output structure patterns from NAT
- ✅ Keep our advantages: pure concurrency engine, protocol-based adapters, no domain leakage

**Our architecture is cleaner and more suitable for our needs.**

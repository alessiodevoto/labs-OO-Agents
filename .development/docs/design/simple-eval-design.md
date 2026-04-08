# Simple Evaluation Runner Design

**Status:** Draft
**Date:** 2025-12-12
**Goal:** Simple, testable components that compose cleanly

---

## Per-Sample Pipeline

The pipeline runs **per sample** - each sample flows through all stages independently:

```
Sample 1: ┌────────┐   ┌───────┐   ┌───────┐   ┌───────┐
          │EXECUTE │ → │ TRACE │ → │ SCORE │ → │ WRITE │ → result line 1
          └────────┘   └───────┘   └───────┘   └───────┘

Sample 2: ┌────────┐   ┌───────┐   ┌───────┐   ┌───────┐
          │EXECUTE │ → │ TRACE │ → │ SCORE │ → │ WRITE │ → result line 2
          └────────┘   └───────┘   └───────┘   └───────┘

Sample N: ┌────────┐   ┌───────┐   ┌───────┐   ┌───────┐
          │EXECUTE │ → │ TRACE │ → │ SCORE │ → │ WRITE │ → result line N
          └────────┘   └───────┘   └───────┘   └───────┘
```

**Benefits of per-sample design:**
1. **Natural parallelism** - samples are independent, run N at once
2. **Incremental output** - each sample writes result immediately on completion
3. **Crash resilience** - completed samples are saved, only lose in-progress work
4. **Testable stages** - test each stage with a single sample

**Decoupling via trace files:**
- Re-score existing traces without re-running agents
- Inspect traces before scoring
- Test scorers in isolation with fixture traces

---

## Stage 1: Execute

```python
async def execute_task(agent: Agent, task: Task, trace_file: Path) -> ExecutionResult:
    """Run agent on task, write trace, return result."""
    configure_tracing(agent, trace_file)

    start = time.perf_counter()
    actual = await agent.run(task.input)
    latency_ms = (time.perf_counter() - start) * 1000

    return ExecutionResult(
        task_id=task.id,
        input=task.input,
        expected=task.expected,
        actual=actual,
        trace_file=trace_file,
        latency_ms=latency_ms,
    )
```

**Output:** Trace file + ExecutionResult

---

## Stage 2: Build Context (from trace)

```python
def build_scoring_context(result: ExecutionResult) -> ScoringContext:
    """Build context for scoring from execution result + trace."""
    code = extract_code_from_trace(result.trace_file)
    token_count = count_tokens_from_trace(result.trace_file)

    return ScoringContext(
        task_id=result.task_id,
        input=result.input,
        expected=result.expected,
        actual=result.actual,
        code=code,
        trace_file=result.trace_file,
        latency_ms=result.latency_ms,
        token_count=token_count,
    )
```

**Output:** ScoringContext (all data scorers need)

---

## Stage 3: Score

```python
def score_task(ctx: ScoringContext, scorers: list[ScorerConfig]) -> dict:
    """Run all scorers on context."""
    results = {}
    for scorer_config in scorers:
        scorer = create_scorer(scorer_config)
        result = scorer.score(ctx)
        results[scorer_config.name] = {
            "score": result.score,
            "weight": scorer_config.weight,
            "reasoning": result.reasoning,
        }
    return results
```

**Output:** Scores dict

---

## Stage 4: Write

```python
def write_result(writer: ExperimentWriter, ctx: ScoringContext, scores: dict):
    """Write single result to experiment file."""
    weighted = sum(s["score"] * s["weight"] for s in scores.values())
    total_weight = sum(s["weight"] for s in scores.values())

    writer.append_result({
        "task_id": ctx.task_id,
        "passed": weighted / total_weight >= 0.5,
        "input": ctx.input,
        "expected": ctx.expected,
        "actual": ctx.actual,
        "scores": scores,
        "trace_file": str(ctx.trace_file),
        "latency_ms": ctx.latency_ms,
    })
```

**Output:** Line in .006eval.jsonl

---

## Composition: Full Pipeline

### Sequential (simple)

```python
async def run_evaluation(config: Config):
    """Run full evaluation pipeline."""
    writer = ExperimentWriter(output_dir="experiments", experiment_name=config.name)
    writer.start(metadata={"config": asdict(config)})

    try:
        for test in config.test_suite:
            agent = create_agent(test.agent, config.rollout_model)
            tasks = load_tasks(test.data_file)

            for task in tasks:
                trace_file = Path(f"traces/{test.name}/{task.id}.006trace.jsonl")

                # Stage 1: Execute
                result = await execute_task(agent, task, trace_file)

                # Stage 2: Build context
                ctx = build_scoring_context(result)

                # Stage 3: Score
                scores = score_task(ctx, test.scorers)

                # Stage 4: Write (incremental - visible immediately)
                write_result(writer, ctx, scores)

    finally:
        writer.finalize()
```

### Parallel with concurrency limit

```python
async def run_evaluation(
    samples: Iterable[Sample],
    config: PipelineConfig,
    writer: ExperimentWriter,
    max_concurrent: int = 1,  # Default sequential
) -> list[dict[str, Any]]:
    """Run evaluation with concurrency control."""
    samples_list = list(samples)

    if max_concurrent <= 1:
        # Sequential
        results = []
        for sample in samples_list:
            result = await process_sample(sample, config, writer)
            results.append(result)
        return results

    # Parallel with limit
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_with_limit(sample: Sample):
        async with semaphore:
            return await process_sample(sample, config, writer)

    tasks = [process_with_limit(s) for s in samples_list]
    return await asyncio.gather(*tasks)
```

Results write as each sample completes. Concurrency is configurable.

---

## Data Types

```python
@dataclass
class Task:
    id: str
    input: str
    expected: Any

@dataclass
class ScoringContext:
    task_id: str
    input: str
    expected: Any
    actual: Any
    code: str | None = None
    trace_file: Path | None = None
    latency_ms: float | None = None

@dataclass
class ScoreResult:
    score: float  # 0.0 to 1.0
    reasoning: str
```

---

## Scorer Interface

Scorers receive the full `ScoringContext` and extract what they need:

```python
class Scorer(Protocol):
    def score(self, ctx: ScoringContext) -> ScoreResult: ...

# ExactMatchScorer - uses ctx.expected, ctx.actual
class ExactMatchScorer:
    def score(self, ctx: ScoringContext) -> ScoreResult:
        match = str(ctx.expected).lower() == str(ctx.actual).lower()
        return ScoreResult(score=1.0 if match else 0.0, reasoning="exact match")

# CodeQualityScorer - uses ctx.code, ctx.actual
class CodeQualityScorer:
    def score(self, ctx: ScoringContext) -> ScoreResult:
        code = ctx.code or ""
        has_keyword_list = "positive_words" in code or "negative_words" in code
        return ScoreResult(
            score=0.0 if has_keyword_list else 1.0,
            reasoning="Uses keyword list" if has_keyword_list else "Direct approach",
        )
```

---

## Testing

```python
def test_exact_match():
    ctx = ScoringContext(task_id="1", input="x", expected="a", actual="a")
    assert ExactMatchScorer().score(ctx).score == 1.0

def test_exact_match_fail():
    ctx = ScoringContext(task_id="1", input="x", expected="a", actual="b")
    assert ExactMatchScorer().score(ctx).score == 0.0
```

---

## Getting `code` from Trace

Parse from trace file after execution:

```python
def extract_code_from_trace(trace_file: Path) -> str | None:
    for line in trace_file.read_text().splitlines():
        span = json.loads(line)
        if span.get("name") == "llm.completion":
            return span["attributes"].get("llm.output")
    return None
```

Trace already has the data - no strategy changes needed.

---

## Development Journal

### 2024-12-12: Initial Implementation

**Components created:**
- `util/eval_pipeline/models.py` - Core data types
- `util/eval_pipeline/execute.py` - Task execution stage
- `util/eval_pipeline/scoring.py` - Scoring with weighted scorer support
- `util/eval_pipeline/pipeline.py` - Main orchestrator with parallelization
- `util/eval_pipeline/tests/` - Unit tests with mock agents

**Issues resolved:**
- Renamed `types.py` → `models.py` (Python stdlib conflict)
- Moved to `util/eval_pipeline/` for isolation (was nested in e2e_optimization)
- Tests use absolute imports

**Running tests:**
```bash
cd util/eval_pipeline && PYTHONPATH=".:$PYTHONPATH" python -m pytest tests/ -v
# 36 tests pass
```

**Next:** Add LLM judge to sentiment-single test

### 2024-12-12: Real Agent Setup

**Environment:**
```bash
source .venv/bin/activate
pip install -e packages/context-blocks -e packages/unifiedllm
```

**Agent source:** `util/prompt-optimization/test_agents/capability_tests.py`
- `SentimentAgent.classify_single(text)` - single sentiment classification
- `SentimentAgent.classify_batch(texts)` - batch classification
- `CalculateAgent.calculate_single(a, b)` - single multiplication
- `CalculateAgent.calculate_batch(pairs)` - batch multiplication

**Model factory:** `util/models/` - centralized model config
- `models.yaml` - model definitions (endpoints, API keys)
- `models.py` - factory functions: `client()`, `client_for()`
- Usage: `llm = models.client("aws/anthropic/claude-haiku-4-5-v1")`

**Running with real agents:**
```bash
source .venv/bin/activate
python util/eval_pipeline/run_sentiment.py
```

**First successful run:** 4/4 passed with Claude Haiku
```
Results: 4/4 passed
Output file: util/e2e_optimization/experiments/sentiment_20251212_105249.006eval.jsonl
```

**Output format (.006eval.jsonl):**
```jsonl
{"metadata": {"status": "completed", "model": "...", "passed": 4, "total": 4}, "results": []}
{"test": "sentiment_classify", "task_id": "sentiment_001", "passed": true, "actual": "positive", ...}
```

### 2024-12-12: Tracing and Viewer Format

**Trace file naming:** Each sample gets its own timestamped trace file:
```
traces/{variant}_{model}_{test}_{timestamp}_{uuid}.006trace.jsonl
```
Example: `traces/default_claude-haiku-4-5-v1_sentiment_classify_20251212_110500_a1b2c3.006trace.jsonl`

**Enabling tracing:** Use `enable_tracing()` at startup, then `switch_file()` per sample:
```python
from openinference_instrumentation_agent006 import enable_tracing, get_current_exporter

# At startup - enable global tracing
enable_tracing(trace_dir="experiments/sentiment_YYYYMMDD/traces")

# Per sample - switch to new trace file
exporter = get_current_exporter()
if exporter:
    exporter.switch_file(trace_file_path)
```

**Viewer-compatible output format:**
- Field names: `test_id`, `output`, `model`, `variant`, `test_type`
- Scores format: each scorer produces `{passed, score, reasoning, metrics}`
- Two standard scorers per test:
  1. `output_judge` - checks output correctness (metrics: `result`, `expected`, `output_correct`)
  2. `method_judge` - checks approach correctness (metrics: `method_correct`, `approach_used`)

### 2024-12-12: Standardized Data File Format

**Problem:** Agent methods have different signatures:
- `classify_single(text: str)` - single string arg
- `classify_batch(texts: list[str])` - single list arg
- `calculate_single(a: int, b: int)` - two keyword args
- `calculate_batch(pairs: list)` - single list arg

**Solution:** Standardized data file format with explicit `args` and `kwargs`:

```jsonl
{"args": [], "kwargs": {"text": "I love this!"}, "expected": "positive"}
{"args": [], "kwargs": {"a": 17, "b": 23}, "expected": 391}
{"args": [], "kwargs": {"pairs": [[3, 4], [5, 6]]}, "expected": [12, 30]}
```

**AgentWrapper unpacking:**
```python
class AgentWrapper:
    async def run(self, input: tuple) -> Any:
        args, kwargs = input
        return await self.method(*args, **kwargs)
```

**Benefits:**
1. **Explicit** - data files show exactly what gets passed
2. **Flexible** - supports any method signature (positional + keyword)
3. **Simple config** - no `input_field` / `input_fields` needed in config.yaml
4. **Testable** - easy to verify data file format

**Task.input type:** `tuple[tuple, dict]` - (args, kwargs) tuple

### 2024-12-12: Full 4-Test Run Successful

**Test suite:** All 4 agent types working:
```
=== sentiment_single ===
Results: 4/4 passed

=== sentiment_batch ===
Results: 1/1 passed

=== calculate_single ===
Results: 4/4 passed

=== calculate_batch ===
Results: 1/1 passed
```

**Method signatures successfully handled:**
- `classify_single(text: str)` → `kwargs={"text": "..."}`
- `classify_batch(texts: list[str])` → `kwargs={"texts": [...]}`
- `calculate_single(a: int, b: int)` → `kwargs={"a": 17, "b": 23}`
- `calculate_batch(pairs: list)` → `kwargs={"pairs": [[...]]}`

**Files involved:**
- [config.yaml](util/eval_pipeline/config.yaml) - 4 test configs
- [config_loader.py](util/eval_pipeline/config_loader.py) - loads args/kwargs from JSONL
- [cli.py](util/eval_pipeline/cli.py) - CLI entry point, AgentWrapper unpacks with `*args, **kwargs`
- Data files: `data_sentiment_single.jsonl`, `data_sentiment_batch.jsonl`, `data_calculate_single.jsonl`, `data_calculate_batch.jsonl`

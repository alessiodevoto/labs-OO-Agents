# E2E Optimization: File-Based Architecture

## Overview

Restructure the e2e optimization system to be config-driven with explicit target source files. Each generation produces a complete, self-contained directory with all artifacts.

## Scorer Hierarchy

```
Scorer (protocol)
├── ExactMatchScorer           # String comparison
├── LLMJudgeScorer            # nemo_oo_agents agent that evaluates using LLM
└── Custom scorers             # User-defined
```

### Built-in Scorers

#### ExactMatchScorer

Compares agent output to expected value using string comparison.

```python
class ExactMatchScorer(Scorer):
    """Simple string comparison scorer."""

    def __init__(
        self,
        case_insensitive: bool = False,
        strip_whitespace: bool = True,
        normalize_unicode: bool = False,
    ):
        self.case_insensitive = case_insensitive
        self.strip_whitespace = strip_whitespace
        self.normalize_unicode = normalize_unicode

    def score(self, expected: Any, actual: Any) -> ScoreResult:
        """Return 1.0 if match, 0.0 otherwise."""
        exp_str = str(expected)
        act_str = str(actual)

        if self.strip_whitespace:
            exp_str = exp_str.strip()
            act_str = act_str.strip()

        if self.case_insensitive:
            exp_str = exp_str.lower()
            act_str = act_str.lower()

        match = exp_str == act_str
        return ScoreResult(
            score=1.0 if match else 0.0,
            reasoning=f"{'Match' if match else 'No match'}: expected={exp_str!r}, actual={act_str!r}",
        )
```

Config usage:
```yaml
scorer:
  class: ExactMatchScorer
  case_insensitive: true
  strip_whitespace: true
```

### LLMJudgeScorer as an Agent006 Agent

```python
@agent
class LLMJudgeScorer(Scorer):
    """Scorer that uses LLM-as-judge pattern.

    This is itself an nemo_oo_agents agent, so it:
    - Gets traced like any other agent
    - Can use the same strategies (PURE_PYTHON, STRUCTURED_OUTPUT)
    - Benefits from the same prompt optimization
    """

    def __init__(self, rubric: str, model: str = "claude-sonnet-4-5"):
        self.rubric = rubric
        self.model = model

    @plan
    async def score(self, expected: Any, actual: Any) -> ScoreResult:
        """Compare actual output to expected using LLM reasoning.

        Args:
            expected: The expected output
            actual: The actual output from the agent under test

        Returns:
            ScoreResult with score 0-1 and reasoning
        """
        # LLM sees: rubric + expected + actual
        # LLM returns: score and reasoning
        ...
```

**Why this is powerful:**
- Evaluation traces are captured alongside agent traces
- Can optimize the judge prompts too (meta-optimization)
- Consistent infrastructure - one framework for agents AND scorers

## Current State (Problems)

- Optimizer hardcodes `SentimentStrategy` as base class
- Dynamic class generation via `exec()` - hard to debug
- Traces scattered across directories
- No clear lineage between generations

## Target Architecture

### Config-Driven Targets

```yaml
# examples/sentiment/config.yaml
name: sentiment
description: Sentiment classification optimization

# Files to optimize (can be anywhere in the codebase)
target_files:
  - src/nemo_oo_agents/strategies/pure_python.py    # Shared strategy prompts
  - src/nemo_oo_agents/strategies/base.py           # Optional: base strategy

# Test suite: multiple agents/entry points
# Each test specifies scorers (can be single or list)
test_suite:
  # Sentiment: multiple scorers (output correctness + code quality)
  - name: sentiment
    agent:
      module: examples.sentiment.agent
      class: SentimentAgent
    method: classify_single
    data_file: examples/sentiment/data.jsonl
    input_field: text
    expected_field: expected
    weight: 1.0
    scorers:
      # All scorers must pass for task to pass
      - class: ExactMatchScorer
        case_insensitive: true
        # Scores the output
        input: output
      - class: LLMJudgeScorer
        model: claude-sonnet-4-5
        rubric: |
          Evaluate code quality. FAIL if uses brittle keyword matching
          (word lists, "in text" checks). PASS if uses proper reasoning.
        # Scores the generated code from trace
        input: trace.generated_code

  # Planning - single scorer
  - name: planning
    agent:
      module: agents.planner
      class: PlannerAgent
    method: create_plan
    data_file: tests/planning/data.jsonl
    input_field: goal
    expected_field: expected_steps
    weight: 1.5
    scorers:
      - class: LLMJudgeScorer
        model: claude-sonnet-4-5
        rubric: |
          Compare generated plan steps to expected.
          Score 0-1 based on coverage and correctness.

  # Custom scorer - full control
  - name: capability_tests
    scorers:
      - module: tests.capabilities.scorer
        class: CapabilityTestScorer
    weight: 3.0

# Optimization objectives
objectives:
  - accuracy
  - token_cost
  - latency
```

### Generation Directory Structure

Each generation gets its own directory:

```
util/e2e_optimization/experiments/pure_python_optimization_20251210/
├── config.yaml              # Copy of original config (frozen)
├── generations/
│   ├── gen_000_baseline/
│   │   ├── source/
│   │   │   ├── pure_python.py   # Original strategy file
│   │   │   └── base.py
│   │   ├── traces/
│   │   │   ├── sentiment/       # Per-test subdirectories
│   │   │   │   ├── task_000.006trace.jsonl
│   │   │   │   └── task_001.006trace.jsonl
│   │   │   ├── code_gen/
│   │   │   │   └── ...
│   │   │   ├── planning/
│   │   │   │   └── ...
│   │   │   └── capability_tests/
│   │   │       └── ...
│   │   ├── results.006eval.json  # Aggregated eval results
│   │   └── metadata.json         # Generation metadata
│   │
│   ├── gen_001_evolved/
│   │   ├── source/
│   │   │   ├── pure_python.py   # Evolved strategy
│   │   │   └── base.py
│   │   ├── traces/
│   │   │   ├── sentiment/
│   │   │   ├── code_gen/
│   │   │   ├── planning/
│   │   │   └── capability_tests/
│   │   ├── results.006eval.json
│   │   ├── metadata.json
│   │   └── diff/                 # Diffs from parent
│   │       └── pure_python.py.diff
│   │
│   └── gen_002_evolved/
│       └── ...
│
├── pareto_front.json            # Best candidates across all gens
└── experiment_summary.json      # Overall experiment status
```

### Metadata Files

**metadata.json** (per generation):
```json
{
  "generation": 1,
  "parent": "gen_000_baseline",
  "timestamp": "2025-12-10T12:00:00Z",
  "model": "nvidia_nim/qwen/qwen3-next-80b-a3b-instruct",
  "evolver_model": "claude-sonnet-4-5",

  // Aggregated metrics (weighted across all tests)
  "aggregate_metrics": {
    "weighted_accuracy": 0.92,
    "total_token_cost": 15200,
    "total_latency_ms": 45000
  },

  // Per-test breakdown
  "test_results": {
    "sentiment": {
      "accuracy": 0.95,
      "token_cost": 1250,
      "latency_ms": 3200,
      "weight": 1.0,
      "pass_count": 19,
      "total_count": 20
    },
    "code_gen": {
      "accuracy": 0.88,
      "token_cost": 8500,
      "latency_ms": 25000,
      "weight": 2.0,
      "pass_count": 22,
      "total_count": 25
    },
    "planning": {
      "accuracy": 0.93,
      "token_cost": 3200,
      "latency_ms": 12000,
      "weight": 1.5,
      "pass_count": 14,
      "total_count": 15
    },
    "capability_tests": {
      "accuracy": 0.91,
      "token_cost": 2250,
      "latency_ms": 4800,
      "weight": 3.0,
      "pass_count": 41,
      "total_count": 45
    }
  },

  "files_modified": ["pure_python.py"],
  "evolution_prompt": "Focus on reducing token usage while maintaining accuracy across all test types..."
}
```

**experiment_summary.json**:
```json
{
  "name": "sentiment_20251210_120000",
  "config": "sentiment",
  "started": "2025-12-10T12:00:00Z",
  "status": "running",
  "current_generation": 2,
  "total_generations": 5,
  "best_generation": "gen_001_evolved",
  "best_metrics": {
    "accuracy": 0.95,
    "token_cost": 1100
  }
}
```

## Implementation Components

### 1. Experiment Manager

```python
class ExperimentManager:
    """Manages experiment directories and generation lifecycle."""

    def __init__(self, config_path: Path):
        self.config = load_config(config_path)
        self.experiment_dir = create_experiment_dir(self.config.name)

    def create_generation(self, parent: str | None = None) -> GenerationContext:
        """Create a new generation directory."""
        gen_num = self._next_generation_number()
        gen_name = f"gen_{gen_num:03d}_{'baseline' if parent is None else 'evolved'}"
        gen_dir = self.experiment_dir / "generations" / gen_name

        # Copy source files from parent (or original)
        source_dir = gen_dir / "source"
        if parent:
            shutil.copytree(parent / "source", source_dir)
        else:
            self._copy_original_sources(source_dir)

        return GenerationContext(gen_dir, self.config)

    def get_generation(self, name: str) -> GenerationContext:
        """Load an existing generation."""
        return GenerationContext(
            self.experiment_dir / "generations" / name,
            self.config
        )
```

### 2. Generation Context

```python
class GenerationContext:
    """Context for running evaluations within a generation."""

    def __init__(self, gen_dir: Path, config: Config):
        self.gen_dir = gen_dir
        self.config = config
        self.source_dir = gen_dir / "source"
        self.traces_dir = gen_dir / "traces"
        self.traces_dir.mkdir(exist_ok=True)

    def load_agent(self) -> Agent:
        """Dynamically load agent from this generation's source."""
        # Add source_dir to sys.path temporarily
        # Import and instantiate agent
        pass

    def get_trace_path(self, task_id: str) -> Path:
        """Get path for a new trace file."""
        return self.traces_dir / f"{task_id}.006trace.jsonl"

    def save_results(self, results: EvalResults):
        """Save evaluation results."""
        path = self.gen_dir / "results.006eval.json"
        path.write_text(json.dumps(results, indent=2))

    def save_metadata(self, metadata: dict):
        """Save generation metadata."""
        path = self.gen_dir / "metadata.json"
        path.write_text(json.dumps(metadata, indent=2))
```

### 3. File Evolver

```python
class FileEvolver:
    """Evolves source files based on feedback."""

    def __init__(self, model: str = "claude-sonnet-4-5"):
        self.model = model

    async def evolve(
        self,
        source_files: dict[str, str],  # filename -> content
        eval_results: EvalResults,
        config: Config,
        focus: str | None = None,
    ) -> dict[str, str]:
        """Generate evolved versions of source files.

        Args:
            source_files: Current source file contents
            eval_results: Evaluation results from current generation
            config: Optimization config
            focus: Optional focus area (e.g., "reduce tokens", "improve accuracy")

        Returns:
            Dict of evolved file contents
        """
        prompt = self._build_evolution_prompt(
            source_files, eval_results, config, focus
        )

        response = await litellm.acompletion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )

        return self._parse_evolved_files(response.choices[0].message.content)
```

### 4. Updated Optimizer Loop

```python
async def optimize(
    config_path: Path,
    generations: int = 5,
    model: str = "nvidia_nim/qwen/qwen3-next-80b-a3b-instruct",
):
    """Main optimization loop."""

    manager = ExperimentManager(config_path)
    evolver = FileEvolver()

    # Generation 0: Baseline
    gen_ctx = manager.create_generation(parent=None)
    results = await evaluate_generation(gen_ctx, model)
    gen_ctx.save_results(results)
    gen_ctx.save_metadata({"generation": 0, "parent": None, ...})

    best_gen = gen_ctx
    best_accuracy = results.accuracy

    # Evolution loop
    for gen_num in range(1, generations + 1):
        # Evolve from best generation
        source_files = best_gen.load_source_files()
        evolved_files = await evolver.evolve(
            source_files,
            best_gen.load_results(),
            manager.config,
        )

        # Create new generation with evolved files
        gen_ctx = manager.create_generation(parent=best_gen.gen_dir)
        gen_ctx.write_source_files(evolved_files)

        # Evaluate
        results = await evaluate_generation(gen_ctx, model)
        gen_ctx.save_results(results)
        gen_ctx.save_metadata({
            "generation": gen_num,
            "parent": best_gen.gen_dir.name,
            ...
        })

        # Update best if improved
        if results.accuracy > best_accuracy:
            best_gen = gen_ctx
            best_accuracy = results.accuracy

    # Save final summary
    manager.save_summary(best_gen)
```

## Migration Path

1. **Phase 1**: Create `ExperimentManager` and `GenerationContext`
2. **Phase 2**: Update eval runner to use generation directories
3. **Phase 3**: Implement `FileEvolver` to generate whole files
4. **Phase 4**: Remove dynamic `exec()` class generation
5. **Phase 5**: Update viewers to discover `.006trace.jsonl` / `.006eval.json`

## Benefits

1. **Reproducibility**: Each generation is a complete, runnable snapshot
2. **Debuggability**: Can inspect/edit actual Python files, not generated code
3. **Lineage tracking**: Clear parent-child relationships between generations
4. **Easy cleanup**: Delete experiment folder to remove everything
5. **Shareable**: Zip an experiment folder to share with others
6. **Viewer-friendly**: Structured directories with discoverable file patterns

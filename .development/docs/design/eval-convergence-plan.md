# Evaluation System Convergence Plan

## Executive Summary

**Goal**: Converge `eval_pipeline` and `run_ablation.py` into a unified evaluation system with:
- ✅ Two frontends preserved (YAML + Python API, CLI)
- ✅ Shared backend runner
- ✅ Universal adapter support (capability tests + benchmarks)
- ✅ Best parallelism features from both

## Current State Analysis

### eval_pipeline

**Frontend:**
- YAML configuration
- Python API (`Evaluator` class)
- Simple, clean interface

**Backend:**
- Location: `util/eval_pipeline/src/eval_pipeline/pipeline.py`
- Parallelism: `asyncio.Semaphore` + `asyncio.gather()`
- Concurrency: Single-level (sample parallelism only)
- Output: `.006eval.jsonl`
- Features:
  - Multiple runs per test (self-consistency)
  - Multiple scorers with weighted scores
  - Model abstraction
  - Timeout support
  - Simple progress tracking

**Test Types:**
- Custom capability tests via JSONL data files
- Direct agent method invocation

### run_ablation.py

**Frontend:**
- CLI with argparse
- Config strings (`nemo_oo_agents`, `react_agent`, `direct_llm`)
- Extensive flags

**Backend:**
- Location: `experiments/evaluation-ablations/run_ablation.py`
- Parallelism: `asyncio.Semaphore` + `asyncio.gather()`
- Concurrency: **Two-level** (benchmark-level + task-level)
- Output: `.006eval.jsonl` with typed `_type` field
- Features:
  - Resume support (skip completed tasks)
  - Incremental file writes
  - Per-sample OTel tracing
  - Dynamic agent loading from files
  - Benchmark environments (Docker support)
  - Two-level semaphore control
  - Crash-safe operation

**Test Types:**
- Benchmark evaluation via `evaluation/adapters/`
- Multi-step environments via `evaluation/environments/`

## Key Backend Differences

| Feature | eval_pipeline | run_ablation.py | Winner |
|---------|---------------|-----------------|---------|
| **Parallelism model** | Semaphore + gather | Semaphore + gather | Tie |
| **Concurrency levels** | 1 (sample) | 2 (benchmark + sample) | **run_ablation** |
| **Resume support** | ❌ No | ✅ Yes | **run_ablation** |
| **Incremental writes** | ✅ Yes | ✅ Yes | Tie |
| **Per-sample tracing** | ✅ Yes | ✅ Yes | Tie |
| **Timeout** | ✅ Per-sample | ❌ No | **eval_pipeline** |
| **Progress callbacks** | ✅ Yes | ⚠️ Print-based | **eval_pipeline** |
| **Multiple scorers** | ✅ Yes | ⚠️ Single evaluator | **eval_pipeline** |
| **Multiple runs** | ✅ Yes | ❌ No | **eval_pipeline** |
| **Dynamic agent loading** | ❌ No | ✅ Yes | **run_ablation** |
| **Benchmark adapters** | ❌ No | ✅ Yes | **run_ablation** |
| **Multi-step environments** | ❌ No | ✅ Yes | **run_ablation** |

## Unified Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│                    FRONTENDS (Preserved)                     │
├──────────────────────────┬──────────────────────────────────┤
│   eval_pipeline          │        run_ablation.py           │
│   - YAML config          │        - CLI interface           │
│   - Python API           │        - Argparse flags          │
│   - Evaluator class      │        - Config strings          │
└──────────┬───────────────┴────────────────┬─────────────────┘
           │                                 │
           └─────────────────┬───────────────┘
                             │
                    ┌────────▼────────┐
                    │  Adapter Layer  │
                    │  - Translate    │
                    │  - Normalize    │
                    └────────┬────────┘
                             │
              ┌──────────────▼──────────────┐
              │   SHARED BACKEND RUNNER     │
              │   evaluation/runner.py      │
              │                             │
              │  Core Features:             │
              │  - Universal parallelism    │
              │  - Resume support           │
              │  - Adapter dispatch         │
              │  - Incremental writes       │
              │  - Progress tracking        │
              └──────────┬─────┬────────────┘
                         │     │
         ┌───────────────┘     └───────────────┐
         │                                     │
    ┌────▼─────┐                         ┌────▼─────┐
    │ Capability│                         │Benchmark │
    │ Adapters  │                         │ Adapters │
    │           │                         │          │
    │ - Direct  │                         │ - BFCL   │
    │   method  │                         │ - LiveCB │
    │   call    │                         │ - BigCB  │
    │ - JSONL   │                         │ - etc.   │
    │   data    │                         │          │
    └───────────┘                         └──────────┘
```

### Shared Backend Components

#### 1. Universal Runner (`evaluation/runner.py`)

**New module** that provides:
```python
class UniversalEvaluationRunner:
    """Unified evaluation runner supporting all test types.

    Features:
    - Single-level and two-level parallelism
    - Resume support
    - Incremental writes
    - Universal adapter dispatch
    - Progress tracking
    """

    async def run_evaluation(
        self,
        tasks: List[EvalTask],
        adapter: EvalAdapter,
        config: RunnerConfig,
    ) -> EvalResults:
        """Run evaluation with unified backend."""
```

**Config:**
```python
@dataclass
class RunnerConfig:
    """Configuration for unified runner."""

    # Parallelism
    max_concurrent_samples: int = 10
    max_concurrent_suites: int | None = None  # For ablation matrices

    # Resume
    resume_from: Path | None = None
    skip_completed: bool = True

    # Output
    output_file: Path
    incremental_writes: bool = True

    # Tracing
    traces_dir: Path | None = None
    per_sample_traces: bool = True

    # Scoring
    pass_threshold: float = 0.5
    timeout_seconds: float | None = None

    # Progress
    on_progress: Callable[[int, int, Result], None] | None = None
```

#### 2. Universal Adapter Interface

```python
class EvalAdapter(ABC):
    """Universal adapter interface for all test types."""

    @abstractmethod
    async def get_tasks(self) -> List[EvalTask]:
        """Load tasks for evaluation."""

    @abstractmethod
    async def run_task(
        self,
        task: EvalTask,
        agent_factory: Callable,
        context: RunContext,
    ) -> TaskResult:
        """Execute a single task."""

    @abstractmethod
    async def evaluate(
        self,
        task: EvalTask,
        result: TaskResult,
    ) -> EvalResult:
        """Evaluate task result."""
```

**Adapter Types:**

1. **CapabilityAdapter** - For eval_pipeline style tests
   - Direct method invocation
   - JSONL data files
   - Multiple scorers

2. **BenchmarkAdapter** - For benchmark evaluation
   - Uses `evaluation/adapters/` implementations
   - Single-step or multi-step environments
   - Benchmark-specific evaluation

## Implementation Plan

### Phase 1: Create Shared Backend (Week 1)

**Goal**: Extract common functionality into shared runner

**Tasks:**

1. **Create `evaluation/runner.py`**
   - [ ] Define `UniversalEvaluationRunner` class
   - [ ] Implement unified parallelism (1-level and 2-level modes)
   - [ ] Add resume support (checkpoint reading/writing)
   - [ ] Implement incremental writes
   - [ ] Add progress tracking with callbacks
   - [ ] Support timeout per sample

2. **Create `evaluation/adapters/base.py`**
   - [ ] Define `EvalAdapter` interface
   - [ ] Define universal data types (`EvalTask`, `TaskResult`, `EvalResult`)
   - [ ] Add adapter registry for discovery

3. **Implement `CapabilityAdapter`**
   - [ ] Wrap eval_pipeline's current execution model
   - [ ] Support JSONL data files
   - [ ] Support multiple scorers
   - [ ] Support multiple runs (self-consistency)

4. **Wrap existing benchmark adapters**
   - [ ] Create `BenchmarkAdapter` that wraps `evaluation/adapters/*`
   - [ ] Support single-step and multi-step environments
   - [ ] Preserve all existing benchmark functionality

**Estimated effort**: 3-4 days

### Phase 2: Integrate with eval_pipeline (Week 1-2)

**Goal**: Make eval_pipeline use shared backend while preserving API

**Tasks:**

1. **Update `eval_pipeline/evaluator.py`**
   - [ ] Use `UniversalEvaluationRunner` internally
   - [ ] Preserve existing Python API (no breaking changes)
   - [ ] Map `Evaluator.run()` to runner

2. **Add benchmark support to eval_pipeline**
   - [ ] Add `benchmark` field to test definitions
   - [ ] Support `BenchmarkAdapter` dispatch
   - [ ] Example YAML:
     ```yaml
     test_suite:
       - name: bfcl_test
         benchmark: bfcl  # Use benchmark adapter
         limit: 100
         scorers:
           - name: benchmark_evaluator
     ```

3. **Update YAML config schema**
   - [ ] Add benchmark support to config
   - [ ] Maintain backward compatibility

**Estimated effort**: 2 days

### Phase 3: Create CLI Frontend (Week 2)

**Goal**: Refactor run_ablation.py to use shared backend

**Tasks:**

1. **Create `evaluation/cli.py`**
   - [ ] Extract CLI interface from `run_ablation.py`
   - [ ] Use `UniversalEvaluationRunner` backend
   - [ ] Preserve all CLI flags and behavior
   - [ ] Add `--config` flag for YAML configs

2. **Add YAML config support to CLI**
   - [ ] Allow `--config path/to/config.yaml` as alternative to flags
   - [ ] CLI flags override YAML values
   - [ ] Example:
     ```bash
     # Old way (still works)
     python -m evaluation.cli \
       --config nemo_oo_agents \
       --benchmark bfcl \
       --limit 100

     # New way
     python -m evaluation.cli \
       --config experiments/bfcl_eval.yaml
     ```

3. **Update `run_ablation.py`**
   - [ ] Make it a thin wrapper around `evaluation.cli`
   - [ ] Preserve backward compatibility
   - [ ] Add deprecation notice

**Estimated effort**: 2 days

### Phase 4: Testing & Validation (Week 2-3)

**Goal**: Ensure both frontends work with all adapters

**Test Matrix:**

| Frontend | Adapter Type | Test Cases |
|----------|--------------|------------|
| eval_pipeline (YAML) | Capability | All existing capability tests |
| eval_pipeline (YAML) | Benchmark | BFCL, LiveCodeBench |
| eval_pipeline (Python API) | Capability | Programmatic test creation |
| eval_pipeline (Python API) | Benchmark | Programmatic benchmark runs |
| CLI | Capability | Custom tests via YAML |
| CLI | Benchmark | All 11 benchmarks |
| CLI (legacy) | Benchmark | Old run_ablation.py flags |

**Tasks:**

1. **Test eval_pipeline with benchmarks**
   - [ ] Create YAML configs for BFCL, LiveCodeBench
   - [ ] Run and verify output format
   - [ ] Verify trace files
   - [ ] Verify resume support

2. **Test CLI with YAML configs**
   - [ ] Convert existing experiments to YAML
   - [ ] Verify CLI can load YAML
   - [ ] Verify flag overrides work

3. **Regression testing**
   - [ ] All capability tests pass
   - [ ] All benchmark experiments reproduce
   - [ ] Output formats unchanged
   - [ ] Performance comparable

**Estimated effort**: 2-3 days

### Phase 5: Documentation & Migration (Week 3)

**Goal**: Document new system and migration path

**Tasks:**

1. **Update documentation**
   - [ ] Write unified evaluation guide
   - [ ] Document shared backend
   - [ ] Update YAML schema docs
   - [ ] Add CLI reference

2. **Create migration guide**
   - [ ] How to convert experiments to YAML
   - [ ] How to use new features
   - [ ] Backward compatibility notes

3. **Update examples**
   - [ ] Add benchmark examples to eval_pipeline
   - [ ] Add YAML examples for CLI
   - [ ] Add programmatic API examples

**Estimated effort**: 2 days

## Timeline

**Total: 2-3 weeks**

- Week 1: Shared backend + eval_pipeline integration (5 days)
- Week 2: CLI frontend + testing (5 days)
- Week 3: Documentation + polishing (2-3 days)

## Benefits

### For Users

✅ **Single mental model** - Same concepts across both interfaces
✅ **Choose your frontend** - YAML or CLI, same backend
✅ **Universal adapter support** - Any test type, any frontend
✅ **Best features everywhere** - Resume, timeout, multi-level parallelism
✅ **Clear migration path** - Old code keeps working

### For Maintainers

✅ **Single backend** - One place for core logic
✅ **Easier testing** - Test backend once, frontends are thin
✅ **Clear separation** - Frontend vs backend, capability vs benchmark
✅ **Reusable components** - Runner, adapters, config

## Backward Compatibility

### eval_pipeline

**Preserved:**
- All existing YAML configs work unchanged
- Python API unchanged
- Output format unchanged (.006eval.jsonl)
- Trace files unchanged

**Enhanced:**
- Can now run benchmarks via `benchmark` field
- Gains resume support
- Gains two-level parallelism (for ablation matrices)

### run_ablation.py

**Preserved:**
- All CLI flags work unchanged
- Output format unchanged
- Behavior identical

**Enhanced:**
- Can now use YAML configs via `--config`
- Gains timeout support
- Gains multiple scorers
- Gains multiple runs (self-consistency)

## Example Configs

### eval_pipeline YAML - Capability Test

```yaml
name: capability_tests
description: "Agent006 capability tests"

models:
  gpt-4o-mini:
    model_name: openai/gpt-4o-mini
    api_key_env: OPENAI_API_KEY

test_suite:
  - name: sentiment
    agent:
      module: tests.capability.agents.sentiment
      class: SentimentAgent
    method: classify
    data_file: tests/capability/data/sentiment.jsonl
    scorers:
      - name: exact_match
        class: ExactMatchScorer
```

### eval_pipeline YAML - Benchmark

```yaml
name: bfcl_eval
description: "BFCL benchmark evaluation"

models:
  gpt-4o-mini:
    model_name: openai/gpt-4o-mini
    api_key_env: OPENAI_API_KEY

test_suite:
  - name: bfcl
    agent:
      module: agents.nemo_oo_agents_tools
      class: ToolsAgent
    benchmark: bfcl  # Use benchmark adapter
    limit: 100
    scorers:
      - name: benchmark_evaluator
        class: BenchmarkEvaluatorScorer
```

### CLI - Benchmark (Old Style)

```bash
python -m evaluation.cli \
  --config nemo_oo_agents \
  --benchmark bfcl \
  --model gpt-4o-mini \
  --provider openai \
  --limit 100
```

### CLI - Benchmark (New YAML Style)

```bash
# bfcl_config.yaml
config_name: nemo_oo_agents
benchmark: bfcl
model:
  name: gpt-4o-mini
  provider: openai
limit: 100

# Run it
python -m evaluation.cli --config bfcl_config.yaml
```

### CLI - Capability Test (New)

```bash
# capability_config.yaml (same format as eval_pipeline)
name: capability_tests
test_suite:
  - name: sentiment
    agent: {...}
    data_file: data.jsonl

# Run it
python -m evaluation.cli --config capability_config.yaml
```

## Success Metrics

✅ All existing capability tests pass unchanged
✅ All existing benchmark experiments reproduce
✅ eval_pipeline can run all 11 benchmarks
✅ CLI can run capability tests via YAML
✅ Output formats compatible with trace viewer
✅ Performance unchanged (within 5%)
✅ Resume support works for both frontends
✅ Documentation complete

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking changes | High | Strict backward compatibility testing |
| Performance regression | Medium | Benchmark before/after, optimize hot paths |
| Complexity increase | Medium | Clear abstractions, good documentation |
| Migration confusion | Low | Keep old interfaces working, add deprecation warnings |

## Open Questions

1. **Should we rename `run_ablation.py` to `evaluation/cli.py`?**
   - Pro: Clearer structure
   - Con: Breaks existing scripts
   - **Recommendation**: Keep `run_ablation.py` as wrapper, add new `evaluation.cli` module

2. **Should benchmark adapters support multiple scorers?**
   - Current: Single `adapter.evaluate()` call
   - Potential: Multiple scorers like capability tests
   - **Recommendation**: Phase 2 enhancement, not critical

3. **Should we support nvidia-core-evals export?**
   - Would allow converting `.006eval.jsonl` → `results.yml`
   - Useful for NVIDIA infra integration
   - **Recommendation**: Phase 2 enhancement via export adapters

## Next Steps

1. **Review this plan** - Get team feedback
2. **Prototype shared runner** - Validate architecture (1-2 days)
3. **Begin Phase 1** - Create shared backend
4. **Iterate** - Adjust based on learnings

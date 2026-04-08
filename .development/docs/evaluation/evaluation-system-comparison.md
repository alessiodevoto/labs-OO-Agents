# Evaluation System Comparison

## Executive Summary

Three evaluation systems currently exist in the codebase with overlapping functionality:
1. **eval_pipeline** - Capability testing framework (YAML + Python API)
2. **run_ablation.py** - Benchmark evaluation script
3. **nvidia-core-evals** - NVIDIA's containerized evaluation factory

Additionally, the **evaluation/** package provides benchmark adapters and environments used by run_ablation.py.

This document analyzes each system and provides recommendations for consolidation.

---

## System 1: eval_pipeline

**Location**: `util/eval_pipeline/`

**Purpose**: Flexible evaluation framework for agent006 agents with YAML-based configuration

### Features
- **Dual interface**: Python API + CLI + YAML config
- **Test definitions**: Add tests programmatically or via YAML
- **Scorers**: Built-in ExactMatchScorer, LLMJudgeScorer, custom scorer support
- **Self-consistency**: Run each test multiple times (`runs=3`)
- **Output format**: `.006eval.jsonl` with per-test results and traces
- **Optimization support**: Experiment optimization features
- **Model abstraction**: Define models once, reference by ID
- **Incremental output**: Crash-safe JSONL writes

### Use Cases
✅ **Local and CI capability tests** - Primary use case
- Designed for testing agent006 capabilities (scale awareness, REPL, routing, etc.)
- Used by `tests/capability/` for CI runs
- Config files in `tests/capability/config*.yaml`

✅ **Prompt optimization experiments**
- Supports optimization loops with reflection prompts
- Used in `experiments/capability_eval/`

### Pros
✅ **Excellent for capability testing** - Designed for this use case
✅ **YAML configuration** - Reproducible, version-controllable test suites
✅ **Python API flexibility** - Easy to integrate into scripts
✅ **Multiple scorers** - Weighted scoring with multiple evaluators
✅ **Self-consistency** - Built-in support for multiple runs
✅ **Model abstraction** - Define once, use everywhere
✅ **Well-documented** - Comprehensive README with examples
✅ **Lightweight** - Pure Python, no Docker dependencies

### Cons
❌ **Not designed for full benchmark suites** - Focused on capability tests
❌ **No native benchmark support** - Would need adapter layer
❌ **No multi-step environments** - Single-shot evaluation only
❌ **No concurrency control** - Sequential execution
❌ **Limited to agent006** - Tightly coupled to agent006 framework

### Current Usage
- `tests/capability/` - CI capability tests
- `experiments/capability_eval/` - Prompt optimization experiments
- `.gitlab-ci.yml` - CI pipeline integration

---

## System 2: run_ablation.py + evaluation/

**Location**: `experiments/evaluation-ablations/run_ablation.py` + `evaluation/` package

**Purpose**: Benchmark evaluation with ablation studies across multiple agent configurations

### Features
- **Benchmark adapters**: 11+ benchmarks via `evaluation/adapters/`
- **Multi-step environments**: Docker-based environments for InterCode, TAU-bench, SWE-bench
- **Single-step environments**: Wrapper for BFCL, LiveCodeBench, GAIA, etc.
- **Concurrent execution**: Semaphore-based parallelism
- **Crash-safe output**: Incremental JSONL writes
- **Resume support**: Skip completed tasks, retry rate-limited/skipped
- **Per-sample tracing**: OTel tracing with .006trace.jsonl files
- **Ablation matrix**: Compare multiple agent configs across benchmarks
- **Dynamic agent loading**: Load agents from Python files (for optimization)
- **Output format**: `.006eval.json` + `.006eval.jsonl` with canonical format

### Use Cases
✅ **Local benchmark runs** - Primary use case
- Run BFCL, LiveCodeBench, BigCodeBench, etc. locally
- Compare agent configurations (agent006 vs ReAct vs baseline)
- Analyze failure modes and iterate on agent implementations

✅ **Ablation studies**
- Full matrix evaluation (configs × benchmarks)
- Measure impact of different capabilities
- Track pass rates and error categories

✅ **Optimization experiments**
- Dynamic agent loading from files
- Used by `util/e2e_optimization/` for evolutionary optimization
- Task-level filtering for targeted runs

### Pros
✅ **Comprehensive benchmark support** - 11+ benchmarks implemented
✅ **Multi-step environments** - Docker support for complex benchmarks
✅ **High parallelism** - Semaphore-based concurrency control
✅ **Crash-safe** - Resume capability, incremental writes
✅ **Flexible agent support** - Works with agent006, ReAct, baseline LLM
✅ **Rich tracing** - Per-sample OTel traces
✅ **Production-ready** - Used for real experiments
✅ **Well-maintained** - Active development, recent fixes

### Cons
❌ **Monolithic script** - 1600+ lines in single file
❌ **Not a library** - Hard to import and reuse
❌ **CLI-only** - No Python API
❌ **Duplicates eval_pipeline** - Overlapping functionality for simple cases
❌ **Complex config** - Command-line args instead of YAML
❌ **No optimization features** - Unlike eval_pipeline

### Current Usage
- `experiments/evaluation-ablations/` - Benchmark experiments
- `util/e2e_optimization/` - Optimization loop integration
- Local development and testing

---

## System 3: nvidia-core-evals

**Location**: `3p/nvidia-core-evals/`

**Purpose**: NVIDIA's standardized evaluation factory with Docker containers

### Features
- **Docker containers** - Pre-built evaluation clients
- **Standard API** - OpenAI/NIM-compatible endpoints
- **eval-factory CLI** - `ls`, `run_eval` commands
- **YAML configuration** - Standard evaluation configs
- **Framework wrappers** - LM Evaluation Harness, etc.
- **Task registry** - Pre-defined evaluation tasks (MMLU, IFEval, etc.)
- **Output format**: `results.yml` (YAML)
- **Infrastructure integration** - Designed for NVIDIA compute clusters

### Use Cases
✅ **Running benchmarks on NVIDIA infra**
- Standardized containers for NVIDIA compute environments
- Integration with NVIDIA model endpoints
- Consistent evaluation across teams

⚠️ **Local benchmark runs** - Possible but requires Docker
- Can run locally but adds Docker overhead
- Not as flexible as native Python solutions

❌ **CI capability tests** - Not designed for this
- Heavy Docker containers
- Focused on standard benchmarks (MMLU, IFEval)
- Not suitable for custom capability tests

### Pros
✅ **Standardized across NVIDIA** - Common evaluation platform
✅ **Pre-built containers** - No local setup required
✅ **Framework integration** - LM Evaluation Harness support
✅ **Infrastructure-ready** - Works on NVIDIA compute clusters
✅ **Vendor-supported** - Maintained by NVIDIA team
✅ **Task registry** - Pre-configured evaluation tasks

### Cons
❌ **Heavy Docker dependency** - Requires container runtime
❌ **Limited customization** - Designed for standard benchmarks
❌ **Not agent-focused** - Model evaluation, not agent evaluation
❌ **Different output format** - YAML instead of JSONL
❌ **External dependency** - Third-party system, limited control
❌ **Setup complexity** - Container registry access required
❌ **Not designed for capability tests** - Focused on benchmark suites
❌ **No agent006 integration** - Would require significant adapter work

### Current Usage
- **None in agent006 codebase** - Currently unused
- Available in `3p/` directory for potential future use

---

## Comparison Matrix

| Feature | eval_pipeline | run_ablation.py | nvidia-core-evals |
|---------|---------------|-----------------|-------------------|
| **Use Case Match** | | | |
| Local capability tests | ✅ Excellent | ❌ Overkill | ❌ Not suitable |
| CI capability tests | ✅ Excellent | ❌ No YAML config | ❌ Too heavy |
| Local benchmarks | ⚠️ Would need adapters | ✅ Excellent | ⚠️ Docker overhead |
| NVIDIA infra benchmarks | ❌ Not designed | ⚠️ Possible | ✅ Designed for this |
| **Architecture** | | | |
| Configuration | YAML + Python API | Command-line args | YAML + CLI |
| Concurrency | Sequential | ✅ High (semaphore) | ✅ High (parallel) |
| Output format | .006eval.jsonl | .006eval.jsonl | results.yml |
| Crash recovery | ✅ Incremental writes | ✅ Resume support | ❓ Unknown |
| **Benchmark Support** | | | |
| Custom tests | ✅ Easy (YAML) | ⚠️ Code changes | ❌ Limited |
| Standard benchmarks | ❌ No adapters | ✅ 11+ implemented | ✅ Many tasks |
| Multi-step envs | ❌ Not supported | ✅ Docker support | ✅ Via frameworks |
| **Integration** | | | |
| Python API | ✅ Yes | ❌ No (script only) | ❌ No (CLI only) |
| Agent006 | ✅ Tight integration | ✅ Supports agent006 | ❌ No integration |
| Other agents | ⚠️ Limited | ✅ ReAct, baseline | ✅ Model-agnostic |
| **Maintenance** | | | |
| Lines of code | ~2000 (package) | ~1600 (single file) | Large (3p system) |
| Documentation | ✅ Excellent | ⚠️ README only | ✅ Good (NVIDIA docs) |
| Active development | ✅ Yes | ✅ Yes | ❓ External |

---

## Recommendations

### Strategy 1: Consolidate on eval_pipeline + evaluation package (Recommended)

**Approach**: Make eval_pipeline the unified interface for all evaluation types

#### Implementation
1. **Keep eval_pipeline as the primary interface** for all evaluation
   - Current use: Capability tests
   - New use: Benchmark evaluation

2. **Enhance eval_pipeline to use evaluation/ adapters**
   - Add benchmark adapter support to eval_pipeline
   - Use `evaluation.adapters.get_adapter()` within eval_pipeline
   - Add multi-step environment support

3. **Deprecate run_ablation.py script**
   - Migrate ablation experiments to eval_pipeline YAML configs
   - Keep `evaluation/` package for adapters/environments
   - Example config:
     ```yaml
     name: ablation_bfcl
     description: "BFCL ablation study"

     models:
       gpt-4o-mini:
         model_name: openai/gpt-4o-mini
         endpoint: https://api.openai.com/v1
         api_key_env: OPENAI_API_KEY

     agent_models:
       - gpt-4o-mini

     test_suite:
       - name: bfcl_ablation
         agent:
           module: agents.agent006_tools
           class: ToolsAgent
         benchmark: bfcl  # New: use benchmark adapter
         limit: 100
         scorers:
           - name: benchmark_evaluator
             class: BenchmarkEvaluatorScorer  # New: wraps adapter.evaluate()
     ```

4. **Keep nvidia-core-evals for NVIDIA infra only**
   - Use for running standard benchmarks on NVIDIA compute clusters
   - Don't try to integrate with agent006 evaluation
   - Keep as optional tool for teams that need it

#### Pros
✅ **Single evaluation interface** - One system to learn
✅ **YAML configuration** - Reproducible for all use cases
✅ **Reuses existing code** - evaluation/ adapters already exist
✅ **Maintains capability testing** - No disruption to CI
✅ **Clear separation** - eval_pipeline for evaluation, nvidia-core-evals for infra

#### Cons
⚠️ **Migration effort** - Need to port run_ablation.py features to eval_pipeline
⚠️ **Testing required** - Ensure benchmark support works correctly

#### Estimated Effort
- 3-4 days to add benchmark support to eval_pipeline
- 1-2 days to migrate existing experiments
- 1 day for testing and documentation

---

### Strategy 2: Keep run_ablation.py, enhance modularity (Alternative)

**Approach**: Extract core logic from run_ablation.py into reusable library

#### Implementation
1. **Refactor run_ablation.py into a library**
   - Create `evaluation/runner.py` (rename existing to `evaluation/improvement_runner.py`)
   - Extract `BenchmarkRunner` class
   - Provide Python API + CLI

2. **Enhance eval_pipeline for capability tests only**
   - Keep eval_pipeline focused on simple capability tests
   - Use run_ablation.py for benchmark evaluation

3. **Document clear boundaries**
   - eval_pipeline: Capability tests, CI, optimization experiments
   - run_ablation: Benchmark evaluation, ablation studies
   - nvidia-core-evals: NVIDIA infrastructure only

#### Pros
✅ **Less disruption** - Keep existing systems mostly as-is
✅ **Faster implementation** - Refactor instead of rebuild
✅ **Preserves specialization** - Each tool optimized for its use case

#### Cons
❌ **Two evaluation systems** - Cognitive overhead for users
❌ **Code duplication** - Some features duplicated across systems
❌ **Confusion** - When to use which system?

#### Estimated Effort
- 2-3 days to extract library from run_ablation.py
- 1 day for documentation

---

### Strategy 3: Adopt nvidia-core-evals (Not Recommended)

**Approach**: Migrate all evaluation to nvidia-core-evals

#### Why Not?
❌ **Heavy Docker dependency** - Slows down development
❌ **Not designed for capability tests** - Would need significant work
❌ **No agent006 integration** - Major rewrite required
❌ **External dependency** - Less control over features
❌ **Different paradigm** - Model evaluation vs agent evaluation
❌ **CI complexity** - Docker in CI is problematic

This would only make sense if:
- All evaluation moves to NVIDIA infrastructure (not local dev)
- Focus shifts to standard benchmarks only (no capability tests)
- Agent006 is deprecated in favor of model-only evaluation

---

## Final Recommendation

**Adopt Strategy 1: Consolidate on eval_pipeline + evaluation package**

### Rationale
1. **Unified interface** - Single YAML-based system for all evaluation
2. **Best of both worlds** - Combines eval_pipeline's usability with evaluation's benchmark support
3. **Long-term maintainability** - One system to maintain and document
4. **Clear role for nvidia-core-evals** - Keep for NVIDIA infra only, don't try to replace local tools

### Implementation Plan

#### Phase 1: Add benchmark support to eval_pipeline (Week 1)
- [ ] Add `BenchmarkEvaluatorScorer` that wraps `adapter.evaluate()`
- [ ] Add `benchmark` field to test definitions (alternative to `data_file`)
- [ ] Add multi-step environment support to eval_pipeline evaluator
- [ ] Test with BFCL and LiveCodeBench

#### Phase 2: Migrate run_ablation.py experiments (Week 2)
- [ ] Convert `experiments/evaluation-ablations/` to YAML configs
- [ ] Migrate ablation matrix to multi-config YAML
- [ ] Verify output format compatibility with trace viewer
- [ ] Update documentation

#### Phase 3: Deprecate run_ablation.py (Week 3)
- [ ] Mark run_ablation.py as deprecated in README
- [ ] Add migration guide to docs
- [ ] Keep script for 2-3 months for backwards compatibility
- [ ] Remove after validation period

#### Phase 4: Documentation (Week 3-4)
- [ ] Update all docs to reference eval_pipeline
- [ ] Create benchmark evaluation guide
- [ ] Document nvidia-core-evals use case (NVIDIA infra only)
- [ ] Update CI/CD examples

### Success Metrics
✅ All capability tests continue to work
✅ All benchmark evaluations can run via eval_pipeline
✅ Single evaluation interface for all use cases
✅ Clear documentation for when to use each tool
✅ No performance regressions in CI

---

## Appendix: Key Differences

### Output Formats

**eval_pipeline**:
```jsonl
{"metadata": {...}, "results": []}
{"test_id": "test1", "passed": true, "scores": {...}, "trace_file": "..."}
{"test_id": "test2", "passed": false, "scores": {...}, "trace_file": "..."}
```

**run_ablation.py**:
```jsonl
{"_type": "metadata", "version": "1", "metadata": {...}}
{"_type": "result", "test_id": "task1", "passed": true, "scores": {...}}
{"_type": "result", "test_id": "task2", "passed": false, "scores": {...}}
{"_type": "completion", "status": "completed", "passed_count": 1}
```

**nvidia-core-evals**:
```yaml
results:
  mmlu:
    accuracy: 0.72
  ifeval:
    prompt_level_strict_acc: 0.65
```

### Configuration Styles

**eval_pipeline** (YAML):
```yaml
models:
  gpt-4:
    model_name: openai/gpt-4
    api_key_env: OPENAI_API_KEY

test_suite:
  - name: sentiment
    agent: {...}
    data_file: data.jsonl
```

**run_ablation.py** (CLI):
```bash
python run_ablation.py \
  --config agent006 \
  --benchmark bfcl \
  --model gpt-4o-mini \
  --provider openai \
  --limit 100
```

**nvidia-core-evals** (YAML + CLI):
```yaml
config:
  type: ifeval
target:
  api_endpoint:
    model_id: llama-3.1-8b
    url: https://integrate.api.nvidia.com/v1
```

```bash
eval-factory run_eval \
  --eval_type ifeval \
  --model_id my_model \
  --model_url http://localhost:8000
```

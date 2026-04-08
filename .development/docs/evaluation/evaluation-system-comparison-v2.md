# Evaluation System Comparison (Updated Analysis)

## Executive Summary

After deeper investigation, the local vs infrastructure story for nvidia-core-evals is more nuanced than initially assessed. Here's the updated comparison:

**Three evaluation systems exist:**
1. **eval_pipeline** - Capability testing framework (YAML + Python API)
2. **run_ablation.py** - Benchmark evaluation script
3. **nvidia-core-evals** - NVIDIA's evaluation factory with **smooth local-to-infra ramp**

**Key Update**: nvidia-core-evals is NOT just Docker containers. It provides a local Python package (`nemo-evaluator`) that enables local development, with the same configs working in CI and on infrastructure.

---

## System 3: nvidia-core-evals (Revised Assessment)

**Location**: `3p/nvidia-core-evals/`

**Purpose**: NVIDIA's standardized evaluation factory with local-to-infra ramp

### Architecture

**Core Components:**
1. **nemo-evaluator** - Python package (pip-installable, formerly nvidia-eval-commons/EFTK)
2. **Framework Definition Files (FDFs)** - YAML configs with Jinja templates
3. **eval-factory CLI** - Command-line tool for running evaluations
4. **Framework packages** - Individual pip wheels for each benchmark
5. **Docker containers** - Optional layer for CI/CD and infra deployment

### The Local-to-Infra Ramp (Key Finding!)

**✅ Stage 1: Local Development (No Docker Required)**
```bash
# Install nemo-evaluator package
pip install nemo-evaluator

# Navigate to a framework
cd frameworks/llm/simple-evals/src

# Initialize with EFTK
eftk init
echo "0.0dev42" > VERSION

# Install in development mode
pip install -r requirements_ef.txt
eftk prototype

# Run evaluation locally (same YAML config)
core-evals-simple-evals run_eval \
  --run_config ./tests/test_mmlu.yml \
  --output_dir ./results
```

**✅ Stage 2: CI/CD Integration (GitLab CI)**
```yaml
# .gitlab-ci.yml
include:
  - component: eval-factory-pipeline/eval-factory-pipeline-default@main
    inputs:
      container-name: my-eval
```

**✅ Stage 3: NVIDIA Infrastructure (Production)**
```bash
# Pull pre-built container
docker pull gitlab-master.nvidia.com:5005/.../lm-evaluation-harness:v1.0

# Run on NVIDIA compute clusters
docker run ... eval-factory run_eval \
  --eval_type mmlu_pro \
  --model_url https://integrate.api.nvidia.com/v1
```

**Key Point**: Same YAML config files work across all three stages!

### Features

- **nemo-evaluator package** - Core evaluation library (pip install)
- **Framework Definition Files (FDFs)** - YAML with Jinja2 templates for CLI generation
- **30+ pre-integrated frameworks** - LM Eval Harness, BFCL, LiveCodeBench, Arena-Hard, TAU-bench, etc.
- **Standard API** - Unified config format for all benchmarks
- **Configuration layers** - FDF defaults → task defaults → user config → CLI overrides
- **Self-service** - Fork existing frameworks, add custom benchmarks
- **CLI-based** - `eval-factory` and `core-evals-<framework>` commands
- **Output format**: `results.yml` (standardized YAML)

### Use Cases

✅ **Running benchmarks on NVIDIA infra** - Primary design goal
- Containers optimized for NVIDIA compute environments
- Integration with NVIDIA model endpoints (NIM, inference-api)
- Consistent evaluation across teams

✅ **Local benchmark runs** - Better than initially assessed!
- Install nemo-evaluator + framework packages via pip
- Run locally without Docker
- Same configs work locally and in production
- Good for development and testing

⚠️ **CI capability tests** - Possible but overkill
- Can use locally, but eval_pipeline is simpler for custom tests
- Better for standard benchmarks than custom agent tests
- Framework initialization overhead

❌ **Agent-focused evaluation** - Not designed for this
- Model evaluation paradigm (single request → response)
- No multi-turn agent state management
- No agent006 integration

### Pros (Updated)

✅ **Smooth local-to-infra ramp** - Same configs work everywhere (key finding!)
✅ **Local development without Docker** - Use nemo-evaluator package
✅ **30+ frameworks included** - LM Eval Harness, BFCL, LiveCodeBench, etc.
✅ **Framework reuse** - Fork existing frameworks, don't build from scratch
✅ **Standardized across NVIDIA** - Common platform for model evaluation
✅ **YAML configuration** - Readable, version-controllable
✅ **Configuration layers** - Flexible override system
✅ **Self-service model** - Teams can onboard custom benchmarks
✅ **Vendor-supported** - Active NVIDIA team maintenance

### Cons (Updated)

❌ **CLI-only, no Python API** - Can't import and use programmatically
❌ **Model-centric paradigm** - Request/response, not multi-turn agents
❌ **Output format mismatch** - YAML `results.yml` vs our `.006eval.jsonl`
❌ **Different evaluation model** - Not designed for agent interactions
❌ **Setup complexity** - Multiple packages, framework initialization
❌ **Not agent006-aware** - No agent state, strategies, tools
❌ **Limited customization** - Jinja templates, not full Python control
❌ **NVIDIA-internal tooling** - GitLab-centric, requires access
❌ **No iterative improvement** - Single-shot evaluation only

### Framework Coverage

The system includes 30+ frameworks in `frameworks/llm/`:
- `lm-evaluation-harness` - General LM evaluation
- `bfcl` - Berkeley Function Calling Leaderboard
- `livecodebench` - Competitive programming
- `arena-hard` - Difficult reasoning tasks
- `bigcode-evaluation-harness` - Code generation
- `helm` - Holistic evaluation
- `mtbench` - Multi-turn conversations
- `tau2-bench` - Tool-augmented tasks
- `simple-evals` - OpenAI's simple-evals
- And 20+ more...

### Comparison with Our Systems

| Aspect | nvidia-core-evals | run_ablation.py | eval_pipeline |
|--------|-------------------|-----------------|---------------|
| **Local development** | ✅ Yes (nemo-evaluator) | ✅ Yes | ✅ Yes |
| **Python API** | ❌ CLI only | ❌ Script only | ✅ Yes |
| **YAML config** | ✅ Yes (FDFs) | ❌ CLI args | ✅ Yes |
| **Benchmark coverage** | ✅ 30+ frameworks | ✅ 11 adapters | ❌ None (custom tests) |
| **Agent support** | ❌ Model-only | ✅ agent006, ReAct | ✅ agent006 |
| **Multi-turn** | ❌ Single request | ✅ Environments | ❌ Single call |
| **Infra integration** | ✅ Designed for it | ⚠️ Manual | ❌ Not designed |
| **Output format** | YAML | JSONL (.006eval) | JSONL (.006eval) |
| **Customization** | ⚠️ FDF templates | ✅ Full Python | ✅ Full Python |

---

## Updated Recommendation

**Strategy 1 remains the best choice, but with nuance:**

### Recommended: Consolidate on eval_pipeline + evaluation package

**Rationale:**
1. **agent006-native** - eval_pipeline and run_ablation understand agents
2. **Python API** - Programmatic control for optimization loops
3. **Multi-turn agents** - Support for stateful, iterative agent execution
4. **JSONL output** - Compatible with our trace viewer
5. **Full customization** - Not limited to Jinja templates

**When to use nvidia-core-evals:**
- ✅ Comparing agent006 against industry-standard benchmarks on NVIDIA infra
- ✅ Running leaderboard evaluations (MMLU, IFEval, etc.) for reports
- ✅ Participating in cross-team benchmark campaigns
- ❌ NOT for agent development, capability tests, or optimization experiments

### Hybrid Approach (Alternative)

**For different use cases:**

1. **Capability tests** → **eval_pipeline**
   - CI tests for agent006 capabilities
   - Prompt optimization experiments
   - Custom test suites

2. **Benchmark research** → **run_ablation.py + evaluation/**
   - BFCL, LiveCodeBench, BigCodeBench, etc.
   - Ablation studies
   - Agent architecture experiments

3. **Leaderboard runs** → **nvidia-core-evals** (optional)
   - Official NVIDIA benchmark campaigns
   - Cross-team comparison runs
   - Infrastructure-scale evaluation

### Implementation Plan (Revised)

#### Phase 1: Consolidate eval_pipeline + evaluation (Weeks 1-2)
Same as before - add benchmark support to eval_pipeline

#### Phase 2: nvidia-core-evals integration (Optional, Week 3)
If we need NVIDIA infra integration:
- [ ] Create adapter layer: `.006eval.jsonl` → `results.yml`
- [ ] Write FDF for agent006 evaluation
- [ ] Test locally with nemo-evaluator
- [ ] Deploy to NVIDIA infra for leaderboard runs

This gives us:
- **Local dev**: eval_pipeline (fast, flexible, agent-native)
- **Production benchmarks**: nvidia-core-evals (standardized, infrastructure-ready)

---

## Key Insights from Deeper Analysis

### nvidia-core-evals Local Development

**Can install and run locally:**
```bash
pip install nemo-evaluator
cd frameworks/llm/lm-evaluation-harness/src
eftk init
pip install -r requirements_ef.txt
eftk prototype  # Install in editable mode
core-evals-lm-evaluation-harness run_eval --run_config test.yml
```

**No Docker required for:**
- Framework development
- Testing configs
- Running evaluations
- Debugging issues

**Docker only needed for:**
- CI/CD builds
- Production deployment
- Containerized distribution

### Configuration System

**Four layers (very flexible):**
1. FDF framework defaults (`framework.yml` in package)
2. FDF task defaults (`evaluations:` section)
3. User config (`.yml` file passed to CLI)
4. CLI overrides (`--overrides` flag)

**Example:**
```yaml
# FDF sets: temperature=0.0000001
# User config sets: limit_samples=100
# CLI override: --overrides config.params.temperature=0.7
# Result: temperature=0.7, limit_samples=100
```

### Why It's Still Not Ideal for agent006

**Paradigm mismatch:**
- nvidia-core-evals: `config.yml` → `command` → `model_endpoint` → `results.yml`
- agent006: `test.yaml` → `Agent.run()` → `strategy.execute()` → `.006eval.jsonl`

**Key differences:**
1. **Execution model**: CLI commands vs Python agents
2. **State management**: Stateless requests vs stateful agents
3. **Multi-turn**: Not supported vs core feature
4. **Tool use**: Model function calling vs agent tool registry
5. **Strategies**: Not a concept vs central to agent006

**Bottom line**: We CAN use nvidia-core-evals locally, but it doesn't understand agents.

---

## Final Recommendation (Updated)

**Primary strategy remains Strategy 1**: Consolidate on eval_pipeline + evaluation

**But now with optional nvidia-core-evals integration:**

```
├── eval_pipeline/          # For capability tests + benchmarks
│   ├── Python API
│   ├── YAML configs
│   └── Uses evaluation/ adapters
│
├── evaluation/             # Benchmark adapters (shared)
│   ├── adapters/
│   └── environments/
│
└── nvidia-core-evals/      # Optional: NVIDIA infra integration
    └── Use only for leaderboard/comparison runs
```

**Decision tree:**
- Custom capability tests? → **eval_pipeline**
- Benchmark research/ablations? → **eval_pipeline + evaluation**
- Official NVIDIA leaderboard? → **nvidia-core-evals** (optional)

This maximizes flexibility while avoiding the complexity of maintaining nvidia-core-evals integration unless we actually need NVIDIA infrastructure access.

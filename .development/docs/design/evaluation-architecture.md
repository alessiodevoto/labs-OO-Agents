# Evaluation Architecture

**Date**: 2025-12-12
**Status**: Active

## Overview

The project has two evaluation frameworks:

1. **E2E-Optimization** (`util/e2e_optimization/`) - File-based optimization loop
2. **Prompt-Opt Runner** (`util/prompt-optimization/runner.py`) - Prompt A/B testing

## E2E-Optimization: File-Based Optimization Loop

**Purpose**: File-based evaluation framework with improvement loops and experiment tracking.

**Key Features**:
- File-based architecture (input JSONL files define test cases)
- Improvement loops with task iteration and refinement
- Experiment tracking with structured output directories
- Multi-agent support (test-level agent specification via adapters)
- Generic BenchmarkAdapter protocol

**Use for**:
- File-based evaluation workflows
- Improvement loop experiments
- Structured experiment tracking
- Capability testing with standard adapters

## Prompt-Opt Runner: Prompt Optimization Framework

**Purpose**: Specialized framework for A/B testing prompts and strategies.

**Key Features**:
- Strategy A/B testing (system vs task message modes)
- Custom test functions with per-test agent classes
- Multi-scorer support (output_correctness + method_correctness)
- Experiment tracking with named experiments
- Results visualization

**Use for**:
- Prompt optimization experiments
- Strategy comparisons
- Capability testing with detailed scorers

## When to Use Which Framework

| Use Case | Framework | Rationale |
|----------|-----------|-----------|
| File-based test definitions | E2E-Opt | Reads test cases from JSONL files |
| Improvement loop experiments | E2E-Opt | Built-in iteration and refinement |
| Structured experiment tracking | E2E-Opt | Organized output directories |
| A/B testing two prompt variants | Prompt-Opt | Strategy comparison framework |
| Comparing system vs task message modes | Prompt-Opt | Named experiment support |
| Evaluating with multiple scorers | Prompt-Opt | Multi-scorer format |
| Multi-model comparison on same tests | Prompt-Opt | Run multiple models in one command |

## How to Run Evaluations

### E2E-Optimization (File-Based)

```bash
cd /Volumes/dev/dev/agent006
source .venv/bin/activate

# Run capability tests with e2e-optimization CLI
python -m e2e_optimization.cli \
  --benchmark capability \
  --model qwen3-next-80b \
  --output-dir experiments/capability_tests
```

### Prompt-Opt Runner (A/B Testing)

```bash
cd /Volumes/dev/dev/agent006
source .venv/bin/activate
cd util/prompt-optimization

# Run all capability tests
PYTHONPATH=../../src:. python runner.py config/capabilities.yaml --models qwen3-next-80b

# Run specific test
PYTHONPATH=../../src:. python runner.py config/capabilities.yaml --models qwen3-next-80b --test sentiment_single

# Run with multiple models
PYTHONPATH=../../src:. python runner.py config/capabilities.yaml --models qwen3-next-80b,claude-sonnet-4-5

# Run experiment (defined in config)
PYTHONPATH=../../src:. python runner.py config/capabilities.yaml --experiment prompt_variant_ab
```

## Configuration

### E2E-Optimization Config
- **Test data**: `util/e2e_optimization/src/e2e_optimization/examples/*/data_*.jsonl` - Input test cases (JSONL)
- **Benchmark config**: `util/e2e_optimization/src/e2e_optimization/examples/*/config.yaml` - Per-benchmark settings
- **Models**: `util/config/models.yaml` - Centralized model configurations

### Prompt-Opt Config
- **Test suite**: `util/prompt-optimization/config/capabilities.yaml` - Test definitions with custom functions
- **Models**: `util/prompt-optimization/config/models.yaml` - Model configs with endpoints and API keys

## Output Format

Both frameworks produce `.006eval.jsonl` files with:
- Metadata line (suite info, models, timestamp)
- One result line per test
- Trace files (`.006trace.jsonl`) for debugging

### Prompt-Opt: Multi-Scorer Format

Supports multiple scorers per test (e.g., output_correctness + method_correctness):

```json
{
  "test_id": "sentiment_single",
  "passed": true,
  "scores": {
    "judge": {
      "passed": true,
      "score": 1.0,
      "reasoning": "Output correct. Method correct.",
      "metrics": {
        "result": "positive",
        "expected": "positive",
        "output_correct": true,
        "method_correct": true
      }
    }
  }
}
```

### E2E-Optimization: Single Evaluator Format

Uses a single evaluator per test:

```json
{
  "test_id": "sentiment_single",
  "passed": true,
  "scores": {
    "evaluator": {
      "passed": true,
      "score": 1.0,
      "reason": "Output matches expected value"
    }
  }
}
```

### Model Configuration Note

**Fixed 2025-12-12**: NVIDIA Nemotron Nano v3 model configuration:
- Uses `nim.aire.nvidia.com` endpoint (not integrate.api.nvidia.com)
- Model ID in config: `nano-v3`, `nemotron-nano-9b`, or `judge-nemotron-nano`
- Correct model name: `openai/nvidia/nano-v3`
- **Recommended use**: LLM-as-judge evaluation ONLY (not as agent model)

The model is now properly configured in `util/prompt-optimization/config/models.yaml`.

**Note on model behavior**: Nemotron Nano models (both v2 and v3) struggle with the REPL/code execution pattern required for agent tasks:
- Return empty responses or get stuck in recursion loops
- Cannot reliably follow the code-only output format
- **Use for judging only, not for running agents**

For agent execution, use models like:
- `qwen3-next-80b` (recommended, fast and capable)
- `claude-sonnet-4-5` (high quality)
- `nemotron-super-49b` (reasoning model)

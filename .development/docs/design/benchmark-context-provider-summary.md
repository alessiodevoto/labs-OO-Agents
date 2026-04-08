# BenchmarkContextProvider Implementation Summary

## What Was Implemented

A **benchmark-agnostic optimization system** that allows the same e2e optimizer to work with different benchmarks by injecting benchmark-specific context into the reflection prompt.

## Key Components

### 1. `BenchmarkContextProvider` (Abstract Base Class)

```python
class BenchmarkContextProvider(ABC):
    @abstractmethod
    def get_reflection_context(self, task_id: str | None = None) -> str:
        """Get markdown context for reflection prompt."""
```

### 2. Three Concrete Implementations

**CapabilityContextProvider** (default)
- Minimal context for capability tests
- Just describes task categories (scale awareness, REPL, routing, etc.)
- Used automatically if no context provider specified

**DABStepContextProvider**
- Loads solution docs from `dabstep_solutions/` directory
- Includes manual.md rules (preview or full content)
- Task-specific: loads `dabstep_{task_id}.md` when available

**TauBenchContextProvider**
- Domain API documentation
- Common failure patterns
- Multi-turn conversation guidance

### 3. Registry Pattern

```python
from e2e_optimization import get_context_provider

provider = get_context_provider("dabstep",
    solutions_dir="experiments/dabstep_solutions",
    manual_path="~/.cache/dabstep/data/context/manual.md")
```

## How It Works

### Optimizer Integration

```python
# In Optimizer.__init__
self.context_provider = context_provider or CapabilityContextProvider()

# In Optimizer._build_reflection_prompt()
benchmark_context = self.context_provider.get_reflection_context(task_id)
template.format(..., benchmark_context=benchmark_context)
```

### Prompt Template

The default template now includes:
```markdown
# Task: Improve Agent Based on Failure Analysis

You are analyzing an agent's performance to improve it.
{retry_section}
{benchmark_context}  # <-- NEW

## Performance Overview
...
```

## Usage Examples

### Capability Tests (default)

```python
from e2e_optimization import Optimizer

# Automatically uses CapabilityContextProvider
optimizer = Optimizer("experiments/capability_eval/config.yaml")
await optimizer.run_iteration()
```

### DABStep (with solution docs)

```python
from e2e_optimization import Optimizer, DABStepContextProvider
from pathlib import Path

optimizer = Optimizer(
    config_path="experiments/dabstep_eval/config.yaml",
    context_provider=DABStepContextProvider(
        solutions_dir=Path("experiments/dabstep_solutions"),
        manual_path=Path.home() / ".cache/dabstep/data/context/manual.md",
        load_full_manual=False,  # Just preview
    ),
)
await optimizer.run_iteration()
```

### TAU-bench

```python
from e2e_optimization import Optimizer, TauBenchContextProvider

optimizer = Optimizer(
    config_path="experiments/tau_bench_eval/config.yaml",
    context_provider=TauBenchContextProvider(),
)
await optimizer.run_iteration()
```

## Benefits

1. **Same optimizer for all benchmarks** - No code duplication
2. **Benchmark-specific context** - Each benchmark provides what it needs
3. **Extensible** - Easy to add new benchmarks
4. **Backward compatible** - Existing capability optimization unchanged

## Files Created/Modified

- `util/e2e_optimization/src/e2e_optimization/benchmark_context.py` (new)
- `util/e2e_optimization/src/e2e_optimization/optimizer.py` (updated)
- `util/e2e_optimization/src/e2e_optimization/__init__.py` (updated exports)

## Example Configs

- `docs/scratch/capability_optimization_example.yaml`
- `docs/scratch/dabstep_optimization_example.yaml`
- `docs/scratch/context_provider_usage_examples.py`

## Next Steps

**Phase 2**: Test with capability tests (should work unchanged)
**Phase 3**: Set up DABStep evaluation and test full pipeline

## Custom Context Providers

To add a new benchmark:

```python
from e2e_optimization import BenchmarkContextProvider

class MyBenchmarkContextProvider(BenchmarkContextProvider):
    def get_reflection_context(self, task_id: str | None = None) -> str:
        return """
## Benchmark: My Benchmark

Domain knowledge goes here...
"""

# Use it
optimizer = Optimizer(
    config_path="my_config.yaml",
    context_provider=MyBenchmarkContextProvider(),
)
```

## See Also

- [Full Design Doc](e2e-optimization-generalization-plan.md)
- [DABStep E2E Plan](dabstep_e2eopt.md)
- [Optimization Plan](../../util/e2e_optimization/OPTIMIZATION_PLAN.md)

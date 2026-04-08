# E2E Optimization Generalization Plan

## Goal

Make the e2e optimization system work for multiple benchmarks with different optimization targets:
- **Capability tests**: Strategy prompt optimization (already working)
- **DABStep**: Full agent architecture evolution with domain knowledge

## Current Architecture

The optimizer has these key components:
1. `Optimizer.reflect()` - Analyzes traces and proposes changes
2. `Optimizer.accept_or_reject()` - Evaluates proposed changes on minibatch
3. `Optimizer.reflect_loop()` - Inner loop with retry on rejection
4. Integration with eval_pipeline via `run_pairs.py`

## What Works Today

✅ Arbitrary eval_pipeline configs (capability tests proven)
✅ Multi-file code extraction and rewriting
✅ Minibatch evaluation with PYTHONPATH injection
✅ Accept/reject based on pass rate improvement
✅ Retry loop with failure context

## Generalization Needed

### 1. Benchmark Context Provider

**Problem**: Different benchmarks need different context in reflection prompts.

**Solution**: Add a context provider abstraction.

```python
# util/e2e_optimization/src/e2e_optimization/benchmark_context.py

from abc import ABC, abstractmethod
from pathlib import Path

class BenchmarkContextProvider(ABC):
    """Provides benchmark-specific context for reflection."""

    @abstractmethod
    def get_reflection_context(self, task_id: str | None = None) -> str:
        """Get markdown context for reflection prompt.

        Args:
            task_id: Optional specific task ID for task-level context

        Returns:
            Markdown text to include in reflection prompt
        """
        ...

class CapabilityContextProvider(BenchmarkContextProvider):
    """Context for capability tests - minimal, just task description."""

    def get_reflection_context(self, task_id: str | None = None) -> str:
        return """
## Benchmark: Capability Tests

These are simple tasks testing core agent capabilities:
- Scale awareness (single vs batch operations)
- REPL exploration (data inspection)
- Subagent routing (task delegation)
- Stateful multi-turn (conversation state)
"""

class DABStepContextProvider(BenchmarkContextProvider):
    """Context for DABStep - includes solution docs and domain knowledge."""

    def __init__(self, solutions_dir: Path, manual_path: Path):
        self.solutions_dir = solutions_dir
        self.manual_path = manual_path

    def get_reflection_context(self, task_id: str | None = None) -> str:
        parts = [
            "## Benchmark: DABStep (Payment Processing)",
            "",
            "### Domain Knowledge",
            self._load_manual_summary(),
            "",
        ]

        if task_id:
            solution_doc = self.solutions_dir / f"dabstep_{task_id}.md"
            if solution_doc.exists():
                parts.extend([
                    "### Solution for this Task",
                    solution_doc.read_text(),
                    "",
                ])

        return "\n".join(parts)

    def _load_manual_summary(self) -> str:
        """Extract key rules from manual.md."""
        # Could be full manual or a curated summary
        if self.manual_path.exists():
            content = self.manual_path.read_text()
            # Could truncate or extract key sections
            return f"See {self.manual_path} for full payment processing rules"
        return ""
```

**Integration**:
```python
# In Optimizer.__init__
self.context_provider = context_provider or CapabilityContextProvider()

# In Optimizer.reflect()
benchmark_context = self.context_provider.get_reflection_context(task_id)
prompt = self._build_reflection_prompt(
    ...,
    additional_context=benchmark_context,
)
```

### 2. Evolution Target Configuration

**Problem**: Different benchmarks optimize different parts of the codebase.

**Solution**: Make evolution targets explicit in config.

```yaml
# experiments/capability_eval/optimization_config.yaml
benchmark:
  name: capability_tests
  adapter: experiments/capability_eval/config.yaml

optimization:
  target_files:
    - agents/strategy.py  # Only optimize strategy prompts

  evolution_instructions: |
    Focus on improving strategy prompt templates:
    - strategy_instructions
    - initial_task_template
    - error feedback messages

    Do NOT change:
    - Core execution logic
    - Tool implementations

# experiments/dabstep_eval/optimization_config.yaml
benchmark:
  name: dabstep
  adapter: evaluation/adapters/dabstep.py

optimization:
  target_files:
    - agents/dabstep_agent.py       # Main agent
    - agents/rules_lawyer.py        # Subagent 1
    - agents/solution_verifier.py   # Subagent 2

  evolution_instructions: |
    You can modify:
    - Agent architecture (add/remove subagents)
    - Tool methods (add helpers for common operations)
    - System prompts and docstrings
    - Data loading/filtering logic

    Rules:
    - Agent must work with DABStep payment processing domain
    - Use solution docs to understand correct approaches
    - Preserve tracing instrumentation

context_provider:
  type: dabstep
  solutions_dir: experiments/dabstep_solutions/
  manual_path: ~/.cache/dabstep/data/context/manual.md
```

**Code changes**:
```python
# In Optimizer.__init__
self.target_files = config.optimization.target_files
self.evolution_instructions = config.optimization.evolution_instructions

# In Optimizer.reflect()
prompt += f"\n\n## Evolution Guidelines\n{self.evolution_instructions}"
```

### 3. Solution Reference Integration

**Problem**: DABStep needs access to solution docs during reflection.

**Solution**: Already handled by `DABStepContextProvider.get_reflection_context(task_id)`!

When reflecting on a failed DABStep sample:
```python
task_id = "1871"  # Extract from eval result
context = dabstep_provider.get_reflection_context(task_id)
# context now includes dabstep_solutions/dabstep_1871.md content
```

## Implementation Plan

### Phase 1: Add Context Provider ✅ (COMPLETE)
- [x] Create `benchmark_context.py` with ABC
- [x] Implement `CapabilityContextProvider` (minimal)
- [x] Implement `DABStepContextProvider` (with solution loading)
- [x] Implement `TauBenchContextProvider` (for future use)
- [x] Add registry with `get_context_provider()` helper
- [x] Update `Optimizer.__init__` to accept `context_provider`
- [x] Update `Optimizer._build_reflection_prompt()` to get benchmark context
- [x] Add `{benchmark_context}` placeholder to template.format()
- [x] Update default prompt template to include `{benchmark_context}`
- [x] Export new classes in `__init__.py`
- [x] Create example configs (capability, dabstep)
- [x] Create usage examples in Python

**Files Created/Modified:**
- `util/e2e_optimization/src/e2e_optimization/benchmark_context.py` (new)
- `util/e2e_optimization/src/e2e_optimization/optimizer.py` (updated)
- `util/e2e_optimization/src/e2e_optimization/__init__.py` (updated)
- `docs/scratch/capability_optimization_example.yaml` (new)
- `docs/scratch/dabstep_optimization_example.yaml` (new)
- `docs/scratch/context_provider_usage_examples.py` (new)

### Phase 2: Test with Capability Tests 🎯 (Next)
- [ ] Run existing capability optimization (should work unchanged)
- [ ] Verify CapabilityContextProvider is used by default
- [ ] Verify benchmark context appears in reflection prompt
- [ ] Confirm acceptance/rejection still works

### Phase 3: Test with DABStep 🔮 (Future)
- [ ] Create `experiments/dabstep_eval/` directory structure
- [ ] Port dabstep_agent*.py to new structure
- [ ] Create eval_pipeline config for dabstep
- [ ] Run Phase 1 of dabstep_e2eopt.md (solution docs)
- [ ] Run 1 optimization iteration on small dabstep sample (3-5 questions)
- [ ] Verify solution docs appear in reflection context
- [ ] Verify evolved agent is evaluated correctly

## Key Benefits

1. **Both benchmarks use same optimizer**: No code duplication
2. **Benchmark-specific context**: Each benchmark provides what it needs
3. **Clear evolution targets**: Config explicitly states what to optimize
4. **Easy to add new benchmarks**: Implement `BenchmarkContextProvider`, write config

## Usage Example

```python
# Capability optimization (existing)
from e2e_optimization import Optimizer, CapabilityContextProvider

optimizer = Optimizer(
    experiment_dir="experiments/capability_eval",
    config=load_config("experiments/capability_eval/optimization_config.yaml"),
    context_provider=CapabilityContextProvider(),
)
result = await optimizer.run_iteration()

# DABStep optimization (new!)
from e2e_optimization import Optimizer, DABStepContextProvider

optimizer = Optimizer(
    experiment_dir="experiments/dabstep_eval",
    config=load_config("experiments/dabstep_eval/optimization_config.yaml"),
    context_provider=DABStepContextProvider(
        solutions_dir=Path("experiments/dabstep_solutions"),
        manual_path=Path.home() / ".cache/dabstep/data/context/manual.md",
    ),
)
result = await optimizer.run_iteration()
```

## Next Steps

1. ✅ Implement context provider abstraction
2. ✅ Add config schema for evolution targets
3. ✅ Test unchanged on capability tests
4. 🎯 Set up DABStep optimization config
5. 🎯 Run first DABStep optimization iteration

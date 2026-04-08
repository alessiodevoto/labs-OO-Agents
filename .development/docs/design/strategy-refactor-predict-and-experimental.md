# Strategy Refactor: PredictStrategy and Experimental Strategies

**Date:** 2026-03-13
**Status:** Implemented (commit b89a43b7)

## Motivation

Two related problems:

1. `StructuredOutputStrategy` is a misleading name — `CodeActStrategy` also produces structured (Pydantic) output via `return_result()`. The name describes the *output type*, not the *mechanism*. The meaningful distinction is *how* the output is produced.

2. The strategies package exposed five strategies in its public API, only two of which (`CodeActStrategy` and `StructuredOutputStrategy`) are actively maintained. The remaining three (`PurePythonStrategy`, `CodeActLiteStrategy`, `ReflexionStrategy`) exist but receive no investment, misleading users into thinking they are recommended options.

## Design

### Change 1: Rename `StructuredOutputStrategy` → `PredictStrategy`

**Rationale:** The real distinction between strategies is the generation *mechanism*:
- `PredictStrategy` — single forward pass, no code execution, no iteration; directly predict the answer
- `CodeActStrategy` — iterative REPL loop with code execution via `execute_python()` + `return_result()`

`PredictStrategy` maps to established ML terminology (a single forward pass that predicts output directly) and clearly contrasts with the "Act" in `CodeActStrategy`. It describes *how* the LLM operates, not what it returns.

**Associated renames:**
- `StructuredOutputConfig` → `PredictConfig`
- `name()` property: `"STRUCTURED_OUTPUT"` → `"PREDICT"`
- OTel span attribute prefix: `structured_output.*` → `predict.*`

**Backward compatibility:**
- `StructuredOutputStrategy` kept as a `FutureWarning` alias (function-based, to avoid metaclass conflicts with Pydantic/AgentMeta)
- `StructuredOutputConfig` kept as a `FutureWarning` alias

### Change 2: Move non-maintained strategies to `strategies/experimental/`

Strategies moved to `src/agent006/strategies/experimental/`:

| Strategy | Reason |
|----------|--------|
| `PurePythonStrategy` | Not maintained; superseded by CodeAct |
| `CodeActLiteStrategy` | Not maintained; variant of CodeAct |
| `ReflexionStrategy` | Not maintained; generate→reflect→improve loop |

Strategies that stay in core (NOT moved):

| Strategy/File | Reason to keep |
|---------------|----------------|
| `CodeActStrategy` | Primary maintained strategy |
| `PredictStrategy` | Primary maintained strategy |
| `TemplateStrategy` | Internal production dependency of both primary strategies; not in public API |
| `CompositeStrategy` | Internal base class; not in public API |

### Experimental mechanics

- Experimental strategies emit `FutureWarning` on instantiation
- Removed from `agent006` top-level public API
- Still importable via `from agent006.strategies.experimental import PurePythonStrategy`
- Kept as silent aliases in `actor.py` exec_globals (no warning in generated code context)
- `ReflexionConfig` moved to `experimental/reflexion.py`; backward-compat re-export in `config/__init__.py`

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| `PredictStrategy` vs alternatives | `PredictStrategy` | Concise; ML-standard term for single forward pass; contrasts with "Act" in CodeActStrategy |
| `TemplateStrategy` placement | Stay in core, remove from public API | Is a production dependency of both primary strategies; cannot be experimental |
| `CompositeStrategy` placement | Stay in core, remove from public API | Is a base class; `DeprecationWarning` on base class instantiation would propagate to production code |
| Warning type for experimental | `FutureWarning` (not `DeprecationWarning`) | Semantics: "may change" not "going away"; avoids CI pipeline failures with `-W error::DeprecationWarning` |
| exec_globals for experimental | Keep as silent aliases | LLM-generated code would get confusing `FutureWarning` in unexpected context; silent is safer |
| `name()` property | Change to `"PREDICT"` | Consistency; stale `"STRUCTURED_OUTPUT"` in OTel spans would be misleading |
| OTel span prefix | Change `structured_output.*` → `predict.*` | Consistency with class rename |
| Test file names | Not renamed | Preserves git blame; no user-visible benefit |
| `experiments/` directory | Not updated | Frozen historical records |
| Alias implementation | Function-based (not subclass) | Metaclass conflict: Pydantic's `ModelMetaclass` and `AgentMeta` cannot be combined via subclass wrapper |

## Files Changed

### New files
- `src/agent006/strategies/predict.py` — `PredictStrategy` class
- `src/agent006/strategies/experimental/__init__.py` — experimental package with FutureWarning wrappers
- `src/agent006/strategies/experimental/pure_python.py`
- `src/agent006/strategies/experimental/codeact_lite.py`
- `src/agent006/strategies/experimental/reflexion.py`

### Modified: core strategies
- `src/agent006/strategies/structured_output.py` — thin alias with FutureWarning
- `src/agent006/strategies/__init__.py` — exports updated
- `src/agent006/__init__.py` — public API updated
- `src/agent006/config/strategy_config.py` — `PredictConfig` + aliases
- `src/agent006/config/__init__.py` — updated exports
- `src/agent006/runtime/actor.py` — exec_globals updated
- `src/agent006/strategies/codeact.py` — prompt strings + secondary injection
- `src/agent006/strategies/composite.py` — docstring references

### Modified: documentation
- `AGENTS.md`
- `docs/guides/strategies.md`
- `docs/guides/structured-output.md`
- `docs/guides/writing-generation-methods.md`
- `docs/guides/prompt-mechanics.md`

### Modified: examples
- `examples/quickstart/04_strategies.py`
- `examples/quickstart/02_structured_outputs.py`

### Modified: production agents
- `agents/dabstep-solver/dabstep_solver.py`
- `agents/librarian-agent/librarian_agent.py`
- `src/agent006_cli/tui/agent.py`
- `src/agent006/agents/summarization.py`
- `src/agent006/util/quickstart.py`

### Modified: util packages
- `util/e2e_optimization/src/e2e_optimization/scorers/llm_judge.py`
- `util/eval_pipeline/src/eval_pipeline/scoring.py`
- `util/eval_pipeline/src/eval_pipeline/cli.py` — added `"predict"` key, kept `"structured_output"` alias
- `util/prompt-optimization/runner.py`
- `util/prompt-optimization/test_agents/playground_structured_output.py`

### Modified: tests
- `tests/runtime/test_structured_output_executor.py`
- `tests/config/test_strategy_configs.py`
- `tests/integration/test_codeact_dynamic_structured_output.py`
- `tests/integration/test_codeact_nested_structured_output.py`
- `tests/capability/agents/structured_output.py`
- Various strategy and edge-case tests
- `pyproject.toml` — added `filterwarnings = ["ignore::FutureWarning:agent006.strategies"]`

## Usage After Refactor

```python
# Primary API (recommended)
from agent006 import PredictStrategy, CodeActStrategy

class MyAgent(Agent, llm=llm):
    @strategy(PredictStrategy())
    async def classify(self, text: str) -> Classification:
        """Classify {text}."""
        ...

# Backward-compat (emits FutureWarning)
from agent006 import StructuredOutputStrategy  # FutureWarning on instantiation

# Experimental (import explicitly)
from agent006.strategies.experimental import PurePythonStrategy  # FutureWarning on instantiation
```

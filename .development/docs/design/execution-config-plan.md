# Developer Configuration for @plan Methods

## Status: Implemented

**Implementation Notes (2025-12-03):**
- Only `ExecutionConfig` implemented (no `LLMConfig` - model/temperature are LLM client constructor params)
- Unified `max_retries` for both PURE_PYTHON errors and STRUCTURED_OUTPUT validation (instead of separate names)
- PURE_PYTHON now tracks iterations and errors separately - errors don't consume iteration budget
- Default `max_retries` = 5 (gives more error recovery budget)

## Problem

Configuration parameters for agent execution are buried and inaccessible to developers:

| Parameter | Location | Default | Exposed? |
|-----------|----------|---------|----------|
| `max_turns` | `pure_python.py:45` | 10 | No |
| `max_retries` | `structured_output.py:40` | 3 | No |
| `temperature` | `@agent` decorator | None | Partially |
| `max_tokens` | `@agent` decorator | None | Partially |

There is no way to configure how many iterations a PURE_PYTHON method gets, or how many validation retries a STRUCTURED_OUTPUT method attempts. Model configuration is scattered between decorator params and runtime.

## Design Decisions

- **LLM API retries**: Out of scope (LLM client's responsibility)
- **Config format**: Two dataclasses - `ExecutionConfig` and `LLMConfig`
- **Hierarchy**: Framework Default → Agent Default → @plan Override → (hidden) call override
- **Separation of concerns**:
  - `ExecutionConfig`: Execution behavior (iterations, retries, token limits)
  - `LLMConfig`: Model sampling parameters (temperature, model selection)

## Solution: `ExecutionConfig` and `LLMConfig`

### ExecutionConfig

```python
# src/nemo_oo_agents/types.py

@dataclass
class ExecutionConfig:
    """Configuration for @plan method execution behavior.

    Hierarchy (later overrides earlier):
    1. Framework defaults (this class)
    2. Agent-level: @agent(execution=...) or Agent(execution=...)
    3. Method-level: @plan(execution=...)
    4. Call-level: agent.method(_execution=...)  # Hidden, experimental
    """

    # PURE_PYTHON strategy: REPL-style iterations
    max_iterations: int = 10
    """Maximum LLM turns for multi-step code generation.
    Each iteration = one LLM call that can explore, define helpers, print debug info.
    Generation succeeds when target method is defined."""

    # STRUCTURED_OUTPUT strategy: validation retries
    max_validation_retries: int = 3
    """Maximum attempts when LLM output fails type/schema validation.
    Each retry sends validation error back to LLM for correction."""

    # Output token limit
    max_tokens: int | None = None
    """Maximum tokens in LLM response. None = use model default.
    Useful for controlling cost and response length."""
```

### LLMConfig

```python
# src/nemo_oo_agents/types.py

@dataclass
class LLMConfig:
    """Configuration for LLM model and sampling parameters.

    Hierarchy (later overrides earlier):
    1. Framework defaults (this class)
    2. Agent-level: @agent(llm=...) or Agent(llm=...)
    3. Method-level: @plan(llm=...)
    4. Call-level: agent.method(_llm=...)  # Hidden, experimental
    """

    model: str | None = None
    """Model identifier (e.g., 'gpt-4o', 'claude-3-opus').
    None = use agent's default model."""

    temperature: float | None = None
    """Sampling temperature (0.0-2.0). Higher = more creative.
    None = use model default (typically 1.0)."""

    top_p: float | None = None
    """Nucleus sampling threshold. None = use model default."""

    # Future: Add more sampling params as needed
    # frequency_penalty: float | None = None
    # presence_penalty: float | None = None
```

**Naming rationale:**
- `max_iterations` (not `max_turns`): Clearer that this is intentional multi-step exploration
- `max_validation_retries` (not `max_retries`): Explicit that this is error recovery, not exploration
- `max_tokens` in `ExecutionConfig`: Controls output behavior, not sampling
- `temperature` in `LLMConfig`: Pure sampling/model parameter

## API Changes

### @agent decorator

```python
def agent(
    *,
    execution: ExecutionConfig | None = None,  # NEW - replaces individual params
    llm: LLMConfig | None = None,              # NEW - replaces model/temperature/max_tokens
    **domain_params: Any,
) -> Callable[[type], type]:
```

**Migration**: `@agent(model="gpt-4o", temperature=0.7)` → `@agent(llm=LLMConfig(model="gpt-4o", temperature=0.7))`

### Agent.__init__

```python
def __init__(
    self,
    llm_client: Any = None,
    execution: ExecutionConfig | None = None,  # NEW
    llm: LLMConfig | None = None,              # NEW - replaces model param
    on_message: Any = None,
    on_reasoning: Any = None,
    _parent_runtime: Any = None,
):
```

### @plan decorator

```python
def plan(
    func: Callable[P, R] | None = None,
    *,
    generation_strategy: GenerationStrategy | str | None = None,
    method_lifetime: MethodLifetime | str | None = None,
    execution: ExecutionConfig | None = None,  # NEW
    llm: LLMConfig | None = None,              # NEW - per-method model override
) -> ...:
```

### Call-time override (hidden)

```python
# Hidden parameters for experimentation
result = await agent.my_method(
    arg1, arg2,
    _execution=ExecutionConfig(max_iterations=50),
    _llm=LLMConfig(temperature=0.0),  # Use deterministic sampling for this call
)
```

## Resolution Logic

```python
# In ActorRuntime._execute_with_generation()

def _resolve_execution_config(self, method) -> ExecutionConfig:
    """Resolve execution config with inheritance chain."""

    # Start with framework default
    config = ExecutionConfig()

    # Layer 2: Agent-level (from @agent decorator or __init__)
    agent_config = getattr(self.agent, '_execution_config', None)
    if agent_config:
        config = self._merge_config(config, agent_config)

    # Layer 3: Method-level (from @plan decorator)
    method_config = getattr(method, '_execution_config', None)
    if method_config:
        config = self._merge_config(config, method_config)

    return config

def _resolve_llm_config(self, method) -> LLMConfig:
    """Resolve LLM config with inheritance chain."""

    # Start with framework default
    config = LLMConfig()

    # Layer 2: Agent-level (from @agent decorator or __init__)
    agent_config = getattr(self.agent, '_llm_config', None)
    if agent_config:
        config = self._merge_config(config, agent_config)

    # Layer 3: Method-level (from @plan decorator)
    method_config = getattr(method, '_llm_config', None)
    if method_config:
        config = self._merge_config(config, method_config)

    return config
```

## Files to Modify

| File | Changes |
|------|---------|
| `src/nemo_oo_agents/types.py` | Add `ExecutionConfig` and `LLMConfig` dataclasses |
| `src/nemo_oo_agents/decorators.py` | Add `execution` and `llm` params to `@agent` and `@plan`, remove `model`/`temperature`/`max_tokens` |
| `src/nemo_oo_agents/agent.py` | Add `execution` and `llm` params to `__init__`, store resolved configs |
| `src/nemo_oo_agents/runtime/actor.py` | Add `_resolve_execution_config()` and `_resolve_llm_config()`, pass to executors, handle `_execution`/`_llm` kwargs |
| `src/nemo_oo_agents/runtime/executors/pure_python.py` | Accept configs, use `execution_config.max_iterations` and `llm_config` for generation |
| `src/nemo_oo_agents/runtime/executors/structured_output.py` | Accept configs, use `execution_config.max_validation_retries` and `llm_config` for generation |
| `src/nemo_oo_agents/runtime/executors/base.py` | Add `execution_config` and `llm_config` to `__init__` signature |

## Implementation Steps

1. **Add `ExecutionConfig` and `LLMConfig` to types.py**
   - `ExecutionConfig`: `max_iterations=10`, `max_validation_retries=3`, `max_tokens=None`
   - `LLMConfig`: `model=None`, `temperature=None`, `top_p=None`
   - Clear docstrings explaining each field

2. **Update decorators.py**
   - Add `execution: ExecutionConfig | None = None` to both `@agent` and `@plan`
   - Add `llm: LLMConfig | None = None` to both `@agent` and `@plan`
   - Remove standalone `model`, `temperature`, `max_tokens` params from `@agent`
   - Store on class/function as `_execution_config` and `_llm_config`

3. **Update agent.py**
   - Add `execution` and `llm` params to `__init__`
   - Remove standalone `model` param
   - Resolve and store as `self._execution_config` and `self._llm_config` (merging decorator + init)

4. **Update actor.py**
   - Add `_resolve_execution_config(method)` and `_resolve_llm_config(method)` helpers
   - Extract `_execution` and `_llm` from kwargs in `call_plan()`
   - Pass resolved configs to executor constructors

5. **Update executor base.py**
   - Add `execution_config: ExecutionConfig` and `llm_config: LLMConfig` to `__init__`
   - Remove individual `max_turns`/`max_retries` params

6. **Update pure_python.py**
   - Remove `max_turns` param
   - Use `self.execution_config.max_iterations`
   - Pass `self.llm_config` to LLM calls

7. **Update structured_output.py**
   - Remove `max_retries` param
   - Use `self.execution_config.max_validation_retries`
   - Pass `self.llm_config` to LLM calls

## Example Usage

```python
from nemo_oo_agents import Agent, agent, plan, ExecutionConfig, LLMConfig

# Agent with custom defaults
@agent(
    execution=ExecutionConfig(max_iterations=5),
    llm=LLMConfig(model="gpt-4o", temperature=0.7),
)
class MyAgent(Agent):

    @plan  # Uses agent defaults: 5 iterations, gpt-4o, temp=0.7
    async def simple_task(self) -> str:
        """Do something simple."""
        ...

    @plan(
        execution=ExecutionConfig(max_iterations=20),  # More iterations for complex task
        llm=LLMConfig(temperature=0.0),  # Deterministic for this method
    )
    async def complex_task(self) -> str:
        """Do something that needs exploration."""
        ...

    @plan(llm=LLMConfig(model="claude-3-opus"))  # Use different model for this method
    async def creative_task(self) -> str:
        """Do something creative."""
        ...

# Runtime override via __init__
agent = MyAgent(
    execution=ExecutionConfig(max_iterations=3),
    llm=LLMConfig(model="gpt-4o-mini"),  # Use cheaper model
)

# Hidden call-time override (experimental)
result = await agent.complex_task(
    _execution=ExecutionConfig(max_iterations=100),
    _llm=LLMConfig(temperature=1.5),  # High creativity for this call
)
```

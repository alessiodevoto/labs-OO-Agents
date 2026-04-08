# Prompt Config Propagation for Test Agents

## Problem Statement

When running capability tests with prompt variants (Phase 3), we need to ensure **all agents** in a test use the same prompt configuration - not just the top-level agent.

### Example: Router Tests

```python
@agent
class OrchestratorAgent:
    @plan
    def process(self, request: Request) -> Result:
        # Creates child agents - these need the same prompt config
        analyzer = AnalyzerAgent(llm=self._llm)
        validator = ValidatorAgent(llm=self._llm)
        # ...
```

If we're testing a "minimal prompt" variant, both `OrchestratorAgent` AND `AnalyzerAgent`/`ValidatorAgent` must use minimal prompts. Otherwise we can't isolate the effect of prompt changes.

### Current Limitation

Today, strategy configs are set at agent class definition time:

```python
@agent(strategy=PurePythonStrategy(config=PurePythonConfig(...)))
class MyAgent:
    ...
```

This doesn't support runtime injection of configs into subagents.

---

## Proposed Solution: Runtime Config Context

### Approach: Context-based Config Propagation

Use Python's `contextvars` to propagate prompt config through the call stack. Any agent created within the context automatically inherits the config.

```python
# In nemo_oo_agents/runtime/config.py
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from nemo_oo_agents.strategies.pure_python import PurePythonConfig

@dataclass
class RuntimeConfig:
    """Runtime configuration propagated through context."""
    pure_python: PurePythonConfig | None = None
    # Future: structured_output, reflexion configs

_current_config: ContextVar[RuntimeConfig | None] = ContextVar('config', default=None)

def get_current_config() -> RuntimeConfig | None:
    """Get current runtime config from context."""
    return _current_config.get()

def set_config(config: RuntimeConfig):
    """Set runtime config for current context."""
    return _current_config.set(config)

@contextmanager
def config_context(config: RuntimeConfig):
    """Context manager for scoped config propagation."""
    token = _current_config.set(config)
    try:
        yield
    finally:
        _current_config.reset(token)
```

### Usage in Test Runner

```python
from nemo_oo_agents.runtime.config import config_context, RuntimeConfig
from nemo_oo_agents.strategies.pure_python import PurePythonConfig

# Define prompt variant
minimal_config = PurePythonConfig(
    instructions="Python code only. Define {method}.",
    error_empty="Define `{method}`.",
)

# Run test with config propagated to all agents
async def run_test_with_config(test_name: str, config: RuntimeConfig):
    with config_context(config):
        # All agents created within this context get the config
        agent = create_test_agent(test_name)
        result = await agent.run(test_input)
        return result

# Example
await run_test_with_config(
    "router_analyze",
    RuntimeConfig(pure_python=minimal_config)
)
```

### Strategy Integration

Strategies check for runtime config before using their default:

```python
# In PurePythonStrategy
class PurePythonStrategy(GenerationStrategy):
    def __init__(self, config: PurePythonConfig | None = None):
        self._explicit_config = config

    @property
    def config(self) -> PurePythonConfig:
        # Priority: explicit > runtime context > default
        if self._explicit_config:
            return self._explicit_config

        runtime = get_current_config()
        if runtime and runtime.pure_python:
            return runtime.pure_python

        return PurePythonConfig()  # Default
```

---

## Alternative Approaches Considered

### Option A: Factory Functions (Rejected)

```python
def create_router_agents(config: PurePythonConfig):
    @agent(strategy=PurePythonStrategy(config=config))
    class OrchestratorAgent:
        ...
    return OrchestratorAgent
```

**Problems:**
- Requires redefining agent classes for each test run
- Breaks caching and class identity
- Verbose and error-prone

### Option B: Global Config (Rejected)

```python
nemo_oo_agents.set_global_config(minimal_config)
# All agents use this config
```

**Problems:**
- Not thread-safe for parallel test execution
- Can't run different configs concurrently
- Hidden state makes debugging hard

### Option C: Config via Agent Init (Partial)

```python
agent = MyAgent(config=minimal_config)
```

**Problems:**
- Doesn't propagate to subagents automatically
- Requires modifying all agent constructors
- Test code becomes verbose

---

## Implementation Plan

### Phase 1: Core Infrastructure

1. Add `nemo_oo_agents/runtime/config.py` with:
   - `RuntimeConfig` dataclass
   - `config_context()` context manager
   - `get_current_config()` function

2. Update `PurePythonStrategy` to check runtime config

3. Add tests for config propagation

### Phase 2: Test Runner Integration

1. Update `runner.py` to accept `--prompt-version` flag
2. Load prompt configs from YAML or Python
3. Wrap test execution in `config_context()`

### Phase 3: Viewer Integration

1. Store prompt version in test results
2. Display prompt version in viewer
3. Enable filtering/comparison by prompt version

---

## Config Definition Format

Prompt configs can be defined in YAML for easy experimentation:

```yaml
# config/prompt_variants.yaml
variants:
  original:
    # Uses all defaults
    pure_python: {}

  condensed:
    pure_python:
      instructions: |
        Output Python code. No markdown. Define target method.
        Use `...` for LLM-per-item processing.
        Print intermediate results for exploration.
      error_empty: "Empty. Define `{method}`."
      error_syntax: "Syntax error. Complete method definition."

  minimal:
    pure_python:
      instructions: "Python code only. Define {method}."
      error_empty: "Define `{method}`."
      error_syntax: "Syntax error."
```

Loader converts YAML to `RuntimeConfig`:

```python
def load_prompt_variant(name: str) -> RuntimeConfig:
    config = yaml.safe_load(open("config/prompt_variants.yaml"))
    variant = config["variants"][name]
    return RuntimeConfig(
        pure_python=PurePythonConfig(**variant.get("pure_python", {}))
    )
```

---

## Open Questions

1. **Nested contexts**: Should inner `config_context()` calls override or merge with outer?
   - **Recommendation**: Override (inner wins), matching Python's variable scoping

2. **Async safety**: Does `contextvars` work correctly with `asyncio.gather()`?
   - **Answer**: Yes, `contextvars` is designed for async and propagates correctly

3. **Subagent LLM client**: Should config propagation also include LLM client?
   - **Recommendation**: Keep separate - `llm=self._llm` pattern is explicit and clear

4. **Structured output / Reflexion**: Same pattern for other strategies?
   - **Recommendation**: Yes, add `structured_output` and `reflexion` fields to `RuntimeConfig`

---

## Files to Create/Modify

| File | Changes |
|------|---------|
| `src/nemo_oo_agents/runtime/config.py` | NEW: RuntimeConfig, config_context |
| `src/nemo_oo_agents/strategies/pure_python.py` | Check runtime config in strategy |
| `src/nemo_oo_agents/runtime/__init__.py` | Export config functions |
| `util/prompt-optimization/runner.py` | Add --prompt-version flag |
| `util/prompt-optimization/config/prompt_variants.yaml` | NEW: variant definitions |
| `tests/runtime/test_config_propagation.py` | NEW: tests for propagation |

---

## Success Criteria

1. Running `python runner.py --prompt-version minimal` applies minimal prompts to ALL agents
2. Router test subagents (AnalyzerAgent, ValidatorAgent) use the configured prompts
3. Results include prompt version metadata
4. Viewer can filter/compare results by prompt version

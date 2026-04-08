# Fix Prompt Optimization After PurePythonConfig Removal

## Problem

The `feat/prompt-optimization-ab-testing` branch has prompt optimization tools that inject custom prompts via `PurePythonConfig`. This dataclass was removed in main and replaced with `@plan(strategy=TemplateStrategy())` methods where the **docstring IS the prompt**.

## New Architecture (main branch)

```python
class PurePythonStrategy(CompositeStrategy):
    def __init__(self, *, max_iterations=10, max_retries=3, task_message_mode=False):
        # No config parameter - prompts are @plan methods

    @plan(strategy=TemplateStrategy())
    async def strategy_instructions(self, runtime: RuntimeServices) -> str:
        """## PURE_PYTHON Mode (Code Execution Loop)

        **Output Format**: Your entire response must be valid Python code...
        (docstring IS the template)"""
        ...

    @plan(strategy=TemplateStrategy())
    async def initial_task_template(self, runtime, instructions, task, method_info, current_call) -> str:
        """{instructions}

        {self.doc.show()}

        {task}
        ...
        """
        ...
```

## Solution: Subclass PurePythonStrategy

The intended design pattern is to **subclass** `PurePythonStrategy` and override the `@plan` methods with different docstrings for each prompt variant.

### Example: Prompt Variant via Subclass

```python
from nemo_oo_agents.strategies import PurePythonStrategy
from nemo_oo_agents.strategies.template import TemplateStrategy
from nemo_oo_agents.decorators import plan

class V3YoloStrategy(PurePythonStrategy):
    """Variant: Simple baseline prompt without classification step."""

    @plan(strategy=TemplateStrategy())
    async def strategy_instructions(self, runtime) -> str:
        """## PURE_PYTHON Mode (Code Execution Loop)

        **Output Format**: Your entire response must be valid Python code. No markdown, no fences.

        **Interaction Pattern**: This is a code execution loop:
        1. You output Python code (your entire response is Python)
        2. The SYSTEM executes your code and returns the output
        3. Messages labeled "[SYSTEM]" are execution results, not human requests
        4. The session ends when you define the target method

        ...(variant-specific instructions)...
        """
        ...

    @plan(strategy=TemplateStrategy())
    async def initial_task_template(self, runtime, instructions, task, method_info, current_call) -> str:
        """{instructions}

        ## Python Tools
        {self.doc.show()}

        ## Your Task
        {task}

        ## Method to Implement
        {method_info}

        ## Current Call
        {current_call}"""
        ...


class V4ClassifyStrategy(PurePythonStrategy):
    """Variant: Classification-first prompt with two reasoning steps."""

    @plan(strategy=TemplateStrategy())
    async def strategy_instructions(self, runtime) -> str:
        """## PURE_PYTHON Mode (Code Execution Loop)

        **FIRST**: Use reasoning() to classify your approach:
        - DIRECT: Single item, answer immediately without code
        - BATCH: Multiple items, write a loop
        - EXPLORE: Unknown data, use print() to inspect first

        ...(classification-specific instructions)...
        """
        ...
```

## Implementation Plan

### 1. Create Strategy Variants Module

Create `util/prompt-optimization/strategy_variants.py` containing subclasses for each A/B test variant:

```python
# strategy_variants.py

from nemo_oo_agents.strategies import PurePythonStrategy
from nemo_oo_agents.strategies.template import TemplateStrategy
from nemo_oo_agents.decorators import plan


class V3YoloStrategy(PurePythonStrategy):
    """v3_yolo: Baseline prompt - simple instructions without classification."""

    @plan(strategy=TemplateStrategy())
    async def strategy_instructions(self, runtime) -> str:
        """...(v3 docstring from capabilities.yaml)..."""
        ...

    @plan(strategy=TemplateStrategy())
    async def initial_task_template(self, runtime, instructions, task, method_info, current_call) -> str:
        """...(v3 template)..."""
        ...

    @plan(strategy=TemplateStrategy())
    async def condensed_task_template(self, runtime, task, method_info, current_call) -> str:
        """...(v3 condensed template)..."""
        ...


class V4ClassifyStrategy(PurePythonStrategy):
    """v4_classify: Classification-first prompt with approach reasoning."""
    # ... override methods with v4 docstrings


STRATEGY_VARIANTS = {
    "default": PurePythonStrategy,
    "v3_yolo": V3YoloStrategy,
    "v4_classify": V4ClassifyStrategy,
}
```

### 2. Update runner.py

Change from YAML string injection to strategy class selection:

```python
# runner.py

from strategy_variants import STRATEGY_VARIANTS

def create_strategy(variant_name: str) -> PurePythonStrategy:
    """Create strategy instance for the given variant name."""
    strategy_class = STRATEGY_VARIANTS.get(variant_name, PurePythonStrategy)
    return strategy_class(task_message_mode=True)  # A/B tests use task_message_mode
```

### 3. Simplify capabilities.yaml

Remove inline prompt strings, just reference variant names:

```yaml
# Before (old approach with PurePythonConfig)
strategy_configs:
  v3_yolo:
    name: "v3 YOLO"
    task_message_mode: true
    instructions: |
      ...(long prompt string)...
    initial_task: |
      ...(template string)...

# After (new approach with subclasses)
strategy_variants:
  v3_yolo:
    name: "v3 YOLO"
    description: "Baseline prompt - simple instructions without classification"
  v4_classify:
    name: "v4 Classify"
    description: "Classification-first prompt with approach reasoning"
```

## Benefits of Subclass Approach

1. **Type-safe**: Each variant is a proper class, IDE support works
2. **Testable**: Can unit test each variant independently
3. **Maintainable**: Prompts live in Python files, not YAML strings
4. **Debuggable**: Full stack traces, breakpoints work
5. **Composable**: Can mix and match method overrides

## Implementation Steps

1. [ ] Create `util/prompt-optimization/strategy_variants.py` with subclasses
2. [ ] Move prompt text from `capabilities.yaml` to Python docstrings
3. [ ] Update `runner.py` to select strategy class by variant name
4. [ ] Simplify `capabilities.yaml` to just variant metadata
5. [ ] Update patching logic to use strategy instances instead of config injection
6. [ ] Test capability tests with each variant

## Files to Modify

| File | Changes |
|------|---------|
| `util/prompt-optimization/strategy_variants.py` | NEW: Strategy subclasses for each A/B variant |
| `util/prompt-optimization/runner.py` | Use strategy class selection instead of config injection |
| `util/prompt-optimization/config/capabilities.yaml` | Simplify to variant metadata only |

## Migration from Old Approach

The old approach used `PurePythonConfig`:
```python
# OLD - removed in main
config = PurePythonConfig(
    task_message_mode=True,
    instructions="custom instructions...",
    initial_task="custom template...",
)
strategy = PurePythonStrategy(config=config)
```

The new approach uses subclasses:
```python
# NEW - subclass with overridden @plan methods
class CustomStrategy(PurePythonStrategy):
    @plan(strategy=TemplateStrategy())
    async def strategy_instructions(self, runtime) -> str:
        """custom instructions..."""
        ...

strategy = CustomStrategy(task_message_mode=True)
```

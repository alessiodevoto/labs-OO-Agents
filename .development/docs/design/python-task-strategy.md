# Task Message Mode for PurePythonStrategy

**Status**: IMPLEMENTED

## Overview

`task_message_mode` is a configuration option for `PurePythonStrategy` that moves all strategy instructions from the system prompt into the task message. After successful code generation, the task message is condensed to remove the instructions, reducing context pollution for subsequent calls.

## Quick Start

```python
from nemo_oo_agents import Agent, agent, plan
from nemo_oo_agents.strategies import PurePythonStrategy, PurePythonConfig

@agent(llm=llm)
class MyAgent(Agent):
    # Default: instructions in system prompt
    @plan
    async def method_a(self, x: int) -> int:
        """Add one to x."""
        ...

    # Task message mode: instructions in task, condensed after success
    @plan(strategy=PurePythonStrategy(config=PurePythonConfig(task_message_mode=True)))
    async def method_b(self, x: int) -> int:
        """Add one to x."""
        ...
```

## How It Works

### Default Mode (`task_message_mode=False`)

```
System Prompt:
├── Agent description (class docstring)
├── Strategy instructions (~90 lines)  ← persists forever
└── Available methods (self.doc())     ← persists forever

History:
├── TaskEvent: "# Task\n\n{docstring}\n\n# Method Information\n\n..."
├── AssistantEvent: <generated code>
├── FeedbackEvent / ErrorEvent
└── ...
```

### Task Message Mode (`task_message_mode=True`)

**Phase 1: Initial Request**

```
System Prompt:
└── Agent description only (minimal)

History:
├── TaskEvent (expanded):
│   ├── Strategy instructions
│   ├── {self.doc()} output
│   ├── # Task
│   ├── # Method Information
│   └── # Current Call
├── AssistantEvent: <generated code>
└── ...
```

**Phase 2: After Successful Generation**

The TaskEvent is updated in-place to remove instructions:

```
History:
├── TaskEvent (condensed):  ← updated!
│   ├── # Task
│   ├── # Method Information
│   └── # Current Call
├── AssistantEvent: <generated code>
└── ...
```

## Template System

Task messages use `runtime.expand_variables()` which supports any Python expression.

### Default Templates

```python
PurePythonConfig(
    initial_task="{instructions}\n\n{self.doc()}\n\n{task}\n\n{method_info}\n\n{current_call}",
    condensed_task="{task}\n\n{method_info}\n\n{current_call}",
)
```

### Expression Support

Templates can use any Python expression that `runtime.expand_variables()` supports:

```python
# Method calls
"{self.doc()}"

# Attribute access
"{call.docstring}"
"{config.max_iterations}"

# Any expression
"{call.method_name.upper()}"
```

This is the same system used for docstring variable expansion in agent methods.

### Pre-computed Sections

For convenience, these sections are pre-computed and available as placeholders:

| Placeholder | Expands To |
|-------------|------------|
| `{task}` | `# Task\n\n{docstring}` |
| `{method_info}` | `# Method Information\n\nasync def method(...)` |
| `{current_call}` | `# Current Call\n\nmethod(arg1, arg2)` |
| `{instructions}` | `config.instructions` |

### Custom Templates

```python
# Minimal template using expressions directly
PurePythonConfig(
    task_message_mode=True,
    initial_task="Task: {call.docstring}\n\nDefine: {method_info}",
    condensed_task="Task: {call.docstring}",
)

# Include iteration limit
PurePythonConfig(
    task_message_mode=True,
    initial_task="{instructions}\n\nMax iterations: {config.max_iterations}\n\n{task}",
)
```

## Configuration Reference

```python
@dataclass
class PurePythonConfig:
    # Execution limits
    max_iterations: int = 10
    max_retries: int = 3

    # Mode toggle
    task_message_mode: bool = False

    # Templates (only used when task_message_mode=True)
    initial_task: str = "{instructions}\n\n{self.doc()}\n\n{task}\n\n{method_info}\n\n{current_call}"
    condensed_task: str = "{task}\n\n{method_info}\n\n{current_call}"

    # Prompts
    instructions: str = "## PURE_PYTHON Mode..."
    error_empty: str = "Empty response. Define `{method}`."
    error_syntax: str = "Syntax error..."
    error_return_outside: str = "Return outside function..."
    error_method_raised: str = "Method `{method}` raised: {error}"
    feedback_not_done: str = "Define `{method}` to complete."
```

## Benefits

1. **Reduced context pollution**: Instructions removed after success
2. **Cleaner multi-turn sessions**: Subsequent calls don't see old instructions
3. **Full expression support**: Use `{self.doc()}`, `{call.docstring}`, etc.
4. **Single strategy class**: No separate `PythonTaskStrategy` needed

## History

- Originally proposed as separate `PythonTaskStrategy` class
- Consolidated into `PurePythonStrategy` with `task_message_mode=True`
- `PythonTaskStrategy` was deleted

## Test Coverage

Tests in `tests/strategies/test_python_task_strategy.py`:

- `task_message_mode=True` produces empty `strategy_prompt`
- `_build_initial_task()` includes strategy instructions and `self.doc()` output
- `_build_condensed_task()` excludes instructions
- Condensed is at least 50% shorter than initial
- Custom templates with variable expansion work
- Execute with FakeLLMClient returns correct result
- Max iterations limit is respected

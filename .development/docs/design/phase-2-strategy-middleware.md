# Strategy Middleware Design

## Problem

Adding a new generation strategy requires editing 4+ files:
- Prompt templates in `context/prompt_data/`
- Error messages in `runtime/errors/messages/`
- Executor logic in `runtime/executors/`
- Registry lists

## Solution

Inspired by [Methodic](https://gitlab-master.nvidia.com/sklingler/methodic): **one class = one strategy.** Consolidate prompts, execution logic, and error handling into a single class:

```python
@plan(strategy=PurePythonStrategy())
async def my_method(self) -> str:
    """Do something."""
```

---

## How It Works

### 1. Runtime Calls Strategy (with Hook-Based Instrumentation)

When a `@plan` method is invoked, the runtime calls hooks before/after strategy execution:

```python
class ActorRuntime:
    async def _execute_plan_method(self, method, args, kwargs):
        strategy = self.agent.get_strategy(method)
        current_call = self.build_current_call(method, args, kwargs)

        # Hooks handle instrumentation - strategy is completely agnostic
        hooks = get_instrumentation_hooks()
        ctx = hooks.before_method(self.agent, method.__name__, args, kwargs)
        try:
            result = await strategy.execute(self, current_call)
            hooks.after_method(ctx, result, None)
            return result
        except Exception as e:
            hooks.after_method(ctx, None, e)
            raise
```

Instrumentation is handled via hooks at the runtime level - strategies never see or touch tracing.

### 2. Strategy Uses Runtime Services

The strategy orchestrates execution using runtime services:

```python
class PurePythonStrategy(GenerationStrategy):
    async def execute(self, runtime, current_call: CurrentCall) -> Any:
        # Add task to history
        runtime.history.add(TaskEvent(data=ContentData(content=self.task_prompt(current_call))))

        # Generation loop (limits from self, not runtime)
        error_count = 0
        for iteration in range(self.max_iterations):
            response = await runtime.generate()
            result = await runtime.execute_code(response.content)

            # Check if target method was defined
            if current_call.method_name in result.defined_methods:
                method = result.defined_methods[current_call.method_name]
                return await method(*current_call.args, **current_call.kwargs)

            # Handle errors
            if result.error:
                error_count += 1
                if error_count >= self.max_retries:
                    raise GenerationError(f"Max retries ({self.max_retries}) exceeded")
                runtime.history.add(ErrorEvent(data=ContentData(content=self.error_prompt(result.error, result.code))))
            else:
                # Success but target not defined - add feedback for next iteration
                runtime.history.add(FeedbackEvent(data=ContentData(content=self.execution_feedback(result, current_call))))

        raise GenerationError(f"Max iterations ({self.max_iterations}) exceeded")
```

### 3. Runtime Provides Services

The runtime provides everything the strategy needs:

| Service | Description |
|---------|-------------|
| `runtime.history` | History manager with event bus (see [History as Event Bus](#history-as-event-bus)) |
| `runtime.generate()` | Build LLM input from prompt blocks, call LLM |
| `runtime.execute_code(code)` | Execute Python with namespace |
| `runtime.agent` | Agent instance (if needed) |

**Note:** `max_iterations` and `max_retries` live on the strategy, not runtime.

**Note:** Strategies do NOT have tracer access. Instrumentation is handled via hooks at the runtime level (see [phase-1-opentelemetry.md](phase-1-opentelemetry.md)). This keeps strategies tracing-agnostic.

**Separation of concerns:**
- **Strategy** defines *what* to do (prompts, loop structure, limits, namespace builtins)
- **Runtime** provides *how* to do it (LLM calls, code execution, history storage)

---

## Strategy Interface

```python
class GenerationStrategy(ABC):
    """Base class for generation strategies.

    Strategies are instances with their own config:
        @plan(strategy=PurePythonStrategy(max_iterations=20))

    This replaces the separate ExecutionConfig - strategy owns its limits.
    Prompts, builtins, and other details are internal to execute().
    """

    @property
    def name(self) -> str:
        """Strategy identifier for logging/tracing."""
        return self.__class__.__name__

    @property
    def strategy_prompt(self) -> str | None:
        """Strategy instructions available to prompt blocks."""
        return None

    @property
    def requires_lock(self) -> bool:
        """Whether this strategy needs exclusive generation access."""
        return True

    @abstractmethod
    async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
        """Run the generation loop using runtime services."""
```

**Design principles:**
- **Minimal ABC** - only `execute()` is abstract; `name`, `strategy_prompt`, `requires_lock` have defaults
- **Strategy owns config** - `max_iterations`, `max_retries` are instance attributes (set in `__init__`)
- **Prompts are internal** - task_prompt, error_prompt, etc. are implementation details inside `execute()`
- `strategy_prompt` is public (runtime uses it for system message)
- `execute(runtime, call)` receives RuntimeServices and pre-built CurrentCall

**Config on strategy vs separate ExecutionConfig:**
```python
# Before (two concepts)
@plan(strategy=ExecutionStrategy.PURE_PYTHON, execution=ExecutionConfig(max_iterations=20))

# After (one concept)
@plan(strategy=PurePythonStrategy(max_iterations=20))
```

Strategy-specific config goes on the strategy:
```python
# STRUCTURED_OUTPUT - schema comes from return type
@plan(strategy=StructuredOutputStrategy(max_retries=5))
async def my_method(self) -> MyResponseModel:
    """Returns structured output."""
```

---

## CurrentCall Dataclass

Built by runtime before calling `execute()`:

```python
@dataclass
class CurrentCall:
    id: str             # Unique call identifier (UUID)
    method_name: str    # "process_data"
    decorator: str      # "@plan" or "@plan(strategy=...)"
    signature: str      # "async def process_data(self, items: list[str]) -> int:"
    docstring: str      # Expanded task description
    call: str           # "process_data(items=['a', 'b', 'c'])"
    args: tuple
    kwargs: dict
    parent_id: str | None = None  # For nested calls
```

The `id` enables call-scoped history filtering (see below). For nested calls (a `@plan` method calling another `@plan` method), `parent_id` tracks the call tree.

---

## History as Event Bus

History is a **unified event pipeline** - all events flow through it for recording, callbacks, and telemetry.

### Single Event Model

```python
history.add(event, record=True)  # Default: record + notify + telemetry
history.add(event, record=False) # Just notify + telemetry, don't store
```

When `history.add()` is called:
1. **Telemetry** - Event sent to tracer for observability
2. **Callbacks** - Subscribers notified via `history.on()`
3. **Recording** - If `record=True`, stored in history for LLM context

### Subscribing to Events

Users subscribe to events they care about:

```python
agent = MyAgent()

# Subscribe to events
agent.history.on("message", print)                    # Print messages
agent.history.on("reasoning", my_reasoning_handler)  # Custom reasoning handler
agent.history.on("llm_response", log_to_file)        # Log all LLM responses
```

### Event Types

| Event Type | Description | Recorded by Default |
|------------|-------------|---------------------|
| `task` | Task prompt added | Yes |
| `assistant` | LLM response | Yes |
| `feedback` | Execution feedback | Yes |
| `error` | Error for retry | Yes |
| `message` | User-facing message from `message()` | Yes |
| `reasoning` | Chain-of-thought from `reasoning()` | No (ephemeral) |

### Why Unified?

- **One concept** - Events, not separate "history" and "callbacks"
- **Consistent** - All events flow through same pipeline
- **Flexible** - `record=False` for ephemeral events (reasoning, progress)
- **Observable** - Telemetry sees everything

---

## Call-Scoped History

Strategies can access either full history or just events from the current call.

### How It Works

1. **Runtime generates call ID** when building `CurrentCall`
2. **Events are tagged** with `call_id` in their metadata when added via `history.add()`
3. **Strategies filter** using `history.for_call(call_id)`

### Event Tagging

When `history.add()` is called during strategy execution, the runtime automatically injects the current call ID:

```python
Event(
    timestamp=now,
    type="task",
    data={"content": task_prompt},
    metadata={"call_id": current_call.id}  # Auto-injected by runtime
)
```

### History API

```python
class HistoryManager:
    def add(self, event: Event, *, record: bool = True) -> None:
        """Add event to the pipeline.

        Always: triggers callbacks, sends telemetry
        If record=True: stores in history for LLM context
        """

    def on(self, event_type: str, handler: Callable) -> None:
        """Subscribe to events of a specific type."""

    def recent(self, limit: int = 50) -> list[Event]:
        """Most recent events (bounded). For context blocks, debugging, etc."""

    def since(self, event_id: str) -> list[Event]:
        """Events added after the given event ID."""

    def for_call(self, call_id: str) -> list[Event]:
        """Recorded events from a specific call only."""

    def for_call_tree(self, call_id: str) -> list[Event]:
        """Recorded events from a call and all its nested calls."""
```

### Strategy Usage

```python
class MinimalContextStrategy(GenerationStrategy):
    async def execute(self, runtime, current_call: CurrentCall) -> Any:
        runtime.history.add(TaskEvent(data=ContentData(content=self.task_prompt(current_call))))

        for iteration in range(self.max_iterations):
            # generate() builds messages from full history by default
            response = await runtime.generate()

            # Strategies can query filtered views for their own logic:
            call_events = runtime.history.for_call(current_call.id)
            # ... use call_events for strategy decisions, not LLM context

            # ... rest of loop
```

This enables strategies that:
- Don't need prior conversation context
- Want isolation between calls
- Need predictable context windows

---

## Code Execution Builtins

`runtime.execute_code()` uses `make_agent_namespace()` to build the execution environment:

| Builtin | Description | Source |
|---------|-------------|--------|
| `self` | Agent instance | namespace |
| `print(...)` | Debug output (returned to LLM) | Python builtin |
| `reasoning("...")` | Chain-of-thought (ephemeral event) | strategy |
| `message("...")` | User-facing message (recorded event) | strategy |

### Namespace Assembly

```python
# util/namespace.py
def make_agent_namespace(agent, *, extra=None) -> dict:
    """Build execution namespace for generated code.

    Layering order (later overrides earlier):
    1. Agent's module imports (requests, datetime, etc.)
    2. Core (self, asyncio, __builtins__)
    3. Extra (strategy builtins, method params)
    """
    # Layer 1: Agent's module imports
    agent_module = inspect.getmodule(type(agent))
    ns = dict(agent_module.__dict__) if agent_module else {}

    # Layer 2: Core (overrides module-level names like 'self' if any)
    ns["self"] = agent
    ns["asyncio"] = asyncio
    ns["__builtins__"] = __builtins__

    # Layer 3: Extra (strategy builtins, method params)
    if extra:
        ns.update(extra)

    return ns
```

### Strategy Provides Namespace Builtins

Strategies define their own builtins that add events to history:

```python
class PurePythonStrategy(GenerationStrategy):
    def __init__(self, max_iterations=10, max_retries=3, record_reasoning=False):
        self.max_iterations = max_iterations
        self.max_retries = max_retries
        self.record_reasoning = record_reasoning

    def _make_builtins(self, runtime: RuntimeServices) -> dict[str, Any]:
        """Build strategy builtins for code execution."""
        return {
            "message": lambda text: runtime.history.add(
                MessageEvent(data=ContentData(content=text))
            ),
            "reasoning": lambda text: runtime.history.add(
                ReasoningEvent(data=ContentData(content=text)),
                record=self.record_reasoning
            ),
        }
```

**Key points:**
- **Strategy owns its builtins** - defines what `message()` and `reasoning()` do
- Builtins passed explicitly to `execute_code(builtins=...)`
- Builtins add events to history (see [History as Event Bus](#history-as-event-bus))
- `record_reasoning=True` enables capturing reasoning in history (useful for debugging/training)

---

## RuntimeServices Protocol (Complete Specification)

This is the full interface that strategies receive. Runtime implements this protocol.

```python
from typing import Protocol, Any, Callable, Literal, Annotated, Union
from pydantic import BaseModel, Field
from datetime import datetime

# === Events ===

import uuid
from datetime import datetime
from pydantic import BaseModel, Field

class ContentData(BaseModel):
    """Content wrapper for events."""
    content: str

class EventBase(BaseModel):
    """Base class for all events."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # Auto-generated UUID
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)

# Agent006-specific events

class TaskEvent(EventBase):
    """Task prompt event."""
    type: Literal["task"] = "task"
    data: ContentData

class MessageEvent(EventBase):
    """User-facing message from generated code."""
    type: Literal["message"] = "message"
    data: ContentData

class ReasoningEvent(EventBase):
    """Chain-of-thought (ephemeral by default)."""
    type: Literal["reasoning"] = "reasoning"
    data: ContentData

class ErrorEvent(EventBase):
    """Error for LLM retry."""
    type: Literal["error"] = "error"
    data: ContentData

class FeedbackEvent(EventBase):
    """Execution feedback when target not yet defined."""
    type: Literal["feedback"] = "feedback"
    data: ContentData

# Extended union for nemo_oo_agents
Event = Annotated[
    Union[
        # From context-blocks
        UserEvent, AssistantEvent, ToolCallEvent, ToolResultEvent,
        # Agent006-specific
        TaskEvent, MessageEvent, ReasoningEvent, ErrorEvent, FeedbackEvent,
    ],
    Field(discriminator="type")
]

# === Other Types ===

class TokenUsage(BaseModel):
    """Token usage statistics from LLM call."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class LLMResponse(BaseModel):
    """Response from LLM generation."""
    content: str | None = None   # Text content (code for PURE_PYTHON)
    reasoning: str | None = None # Extended thinking (if available)
    finish_reason: str | None = None  # "stop", "length", "tool_calls", etc.
    usage: TokenUsage | None = None  # Token counts

class ExecutionResult(BaseModel):
    """Result of code execution."""
    stdout: str = ""              # Captured print() output
    last_expr: Any = None         # Last expression value (IPython-like)
    error: Exception | None = None  # Execution error (if any)
    defined_methods: dict[str, Callable] = {}  # {method_name: bound_method}

    model_config = {"arbitrary_types_allowed": True}  # Allow Exception, Callable

    @property
    def success(self) -> bool:
        """True if execution completed without error."""
        return self.error is None

    def has_method(self, name: str) -> bool:
        """Check if a specific method was defined."""
        return name in self.defined_methods


class HistoryManager(Protocol):
    """Unified event pipeline: recording, callbacks, and telemetry.

    All events flow through add() which:
    1. Sends telemetry to tracer
    2. Notifies subscribers via on()
    3. Records in history (if record=True)

    Events are automatically tagged with the current call_id.
    """

    def add(self, event: Event, *, record: bool = True) -> str:
        """Add event to the pipeline.

        Args:
            event: The event to add
            record: If True, store in history for LLM context.
                    If False, just notify callbacks and telemetry.

        Returns:
            The event's unique ID (auto-generated UUID).
        """
        ...

    def on(self, event_type: str, handler: Callable[[Event], None]) -> None:
        """Subscribe to events of a specific type.

        Handler is called for every event of that type, even if record=False.
        """
        ...

    def recent(self, limit: int = 50) -> list[Event]:
        """Most recent events (bounded). For context blocks, debugging, etc."""
        ...

    def since(self, event_id: str) -> list[Event]:
        """Events added after the given event ID (uses event.id)."""
        ...

    def for_call(self, call_id: str) -> list[Event]:
        """Recorded events from a specific call only."""
        ...

    def for_call_tree(self, call_id: str) -> list[Event]:
        """Recorded events from a call and all its nested calls."""
        ...


class RuntimeServices(Protocol):
    """Services provided by runtime to strategies.

    This is what strategies receive in execute(runtime, current_call).
    ActorRuntime implements this protocol.

    Note: max_iterations/max_retries live on the strategy, not here.
    Note: No emit_message/emit_reasoning - events flow through history.
    Note: No tracer - instrumentation is handled via hooks at runtime level.
          Strategies are completely tracing-agnostic.
    """

    # === Properties ===

    @property
    def agent(self) -> Any:
        """Agent instance."""
        ...

    @property
    def history(self) -> HistoryManager:
        """History manager and event bus for this session."""
        ...

    # === LLM Generation ===

    async def generate(
        self,
        *,
        tools: list[Tool] | None = None,
        tool_choice: str = "auto",
        output_model: type[BaseModel] | None = None,
        **llm_kwargs,
    ) -> LLMResponse:
        """Build messages from context + history, call LLM.

        Extra system prompts should come from context blocks, not here.
        llm_kwargs passed through to UnifiedLLM.acall().

        The runtime handles:
        - Building system message from agent.context + strategy.strategy_prompt
        - Converting history events to messages
        - Calling LLM with tracing instrumentation
        - Adding response as AssistantEvent to history
        """
        ...

    # === Code Execution ===

    async def execute_code(
        self,
        code: str,
        *,
        builtins: dict[str, Any] | None = None,
        validate: bool = True,
    ) -> ExecutionResult:
        """Execute Python code with namespace.

        Args:
            code: Python code to execute
            builtins: Strategy builtins (message, reasoning, etc.)
            validate: Run planning language validation first

        Returns:
            ExecutionResult with stdout, return_value, error, defined_methods

        Namespace includes (via make_agent_namespace):
        - self: Agent instance
        - asyncio: For async operations
        - Agent's module imports
        - builtins: Strategy-provided functions (message, reasoning)

        Method binding:
        - Functions defined in code are extracted and bound to agent via types.MethodType
        - defined_methods contains bound methods, callable as method(*args, **kwargs)
        - Binding is released when ExecutionResult goes out of scope
        """
        ...
```

### How Strategy Uses RuntimeServices

```python
class PurePythonStrategy(GenerationStrategy):
    """PURE_PYTHON strategy with configurable limits."""

    def __init__(
        self,
        max_iterations: int = 10,
        max_retries: int = 3,
        record_reasoning: bool = False,
    ):
        self.max_iterations = max_iterations
        self.max_retries = max_retries
        self.record_reasoning = record_reasoning

    def _make_builtins(self, runtime: RuntimeServices) -> dict[str, Any]:
        """Build strategy builtins for code execution."""
        return {
            "message": lambda text: runtime.history.add(
                MessageEvent(data=ContentData(content=text))
            ),
            "reasoning": lambda text: runtime.history.add(
                ReasoningEvent(data=ContentData(content=text)),
                record=self.record_reasoning
            ),
        }

    async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
        # 1. Add task to history
        runtime.history.add(TaskEvent(data=ContentData(content=self.task_prompt(call))))

        # 2. Build builtins for code execution
        builtins = self._make_builtins(runtime)

        # 3. Generation loop
        error_count = 0
        for iteration in range(self.max_iterations):
            # Generate code (no tools for PURE_PYTHON)
            response = await runtime.generate(tools=None, tool_choice="none")

            # Execute the code with strategy builtins
            result = await runtime.execute_code(response.content or "", builtins=builtins)

            # Check for target method (bound via types.MethodType in execute_code)
            if call.method_name in result.defined_methods:
                method = result.defined_methods[call.method_name]
                # Method is already bound to agent - just call with args/kwargs
                if asyncio.iscoroutinefunction(method):
                    return await method(*call.args, **call.kwargs)
                return method(*call.args, **call.kwargs)

            # Handle errors
            if result.error:
                error_count += 1
                if error_count >= self.max_retries:
                    raise GenerationError(f"Max retries ({self.max_retries}) exceeded")
                runtime.history.add(ErrorEvent(
                    data=ContentData(content=self.error_prompt(result.error, response.content))
                ))
                continue

            # Success but no target - add feedback for next iteration
            runtime.history.add(FeedbackEvent(
                data=ContentData(content=self.execution_feedback(result, call))
            ))

        raise GenerationError(f"Max iterations ({self.max_iterations}) exceeded")
```

### RuntimeServices Implementation Location

The implementation lives in `runtime/actor.py` as part of `ActorRuntime`:

```python
# runtime/actor.py

class ActorRuntime:
    """Implements RuntimeServices protocol."""

    # Properties
    @property
    def agent(self) -> Any:
        return self._agent

    @property
    def history(self) -> HistoryManager:
        return self._history_manager

    # Note: No tracer property - instrumentation handled via hooks

    # LLM Generation
    async def generate(
        self,
        *,
        tools: list[Tool] | None = None,
        tool_choice: str = "auto",
        output_model: type[BaseModel] | None = None,
        **llm_kwargs,
    ) -> LLMResponse:
        # Build messages from prompt blocks
        messages = self._prompt_builder.build_messages(
            self._current_call.method,
            call_args=self._current_call.args,
            call_kwargs=self._current_call.kwargs,
        )

        # Call LLM (hooks handle instrumentation at actor level)
        response = await self._llm_client.acall(
            messages=messages,
            tools=tools or [],
            tool_choice=tool_choice,
            output_model=output_model,
            **llm_kwargs,
        )

        # Record in history via event bus
        self.history.add(AssistantEvent(data=ContentData(content=response.content or "")))

        return LLMResponse(
            content=response.content,
            reasoning=response.reasoning,
            finish_reason=response.finish_reason,
            usage=response.usage,
        )

    # Code Execution
    async def execute_code(
        self,
        code: str,
        *,
        builtins: dict[str, Any] | None = None,
        validate: bool = True,
    ) -> ExecutionResult:
        # Build namespace with strategy-provided builtins
        namespace = make_agent_namespace(self.agent, extra=builtins)

        # Execute code (hooks handle instrumentation at actor level)
        try:
            result = await self._executor.execute(code, namespace, validate=validate)
            return ExecutionResult(
                stdout=result.stdout,
                return_value=result.return_value,
                error=None,
                defined_methods=result.defined_methods,
            )
        except Exception as e:
            return ExecutionResult(
                stdout="",
                return_value=None,
                error=e,
                defined_methods={},
            )
```

### System Message Assembly

The runtime assembles the full system message:

```
┌─────────────────────────────────────────────────────────────┐
│                      System Message                          │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Base Context (from agent.context)                      │  │
│  │ - agent_description (docstring)                        │  │
│  │ - python_tools (self.doc())                            │  │
│  │ - state (self.state if any)                            │  │
│  └───────────────────────────────────────────────────────┘  │
│                            +                                 │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Strategy Prompt (from strategy.strategy_prompt)        │  │
│  │ - Output format instructions                           │  │
│  │ - Available builtins                                   │  │
│  │ - Interaction pattern                                  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Who assembles what:**
- **ActorRuntime**: Concatenates base context + strategy_prompt
- **Strategy**: Provides `strategy_prompt` property (static text)
- **Context blocks**: Evaluated by PromptBuilder using `make_agent_namespace()`

---

## Prompt Blocks Integration

The system message includes `strategy.strategy_prompt`. The runtime provides `strategy` in the render namespace via `extra`:

```python
# In RuntimeServices.generate()
namespace = make_agent_namespace(
    self.agent,
    extra={"strategy": self._current_strategy}  # Current strategy instance
)

# LAYOUT references strategy
LAYOUT = {
    "system_blocks": [
        {"key": "agent_description", "expr": "self.__class__.__doc__"},
        {"key": "python_tools", "expr": "self.doc()"},
        {"key": "strategy", "expr": "strategy.strategy_prompt"},
    ],
}
```

---

## Configuration

**Strategy owns its config.** No separate ExecutionConfig:

```python
# Strategy defines behavior AND limits
@plan(strategy=PurePythonStrategy(max_iterations=20, max_retries=10))

# Strategy-specific config
@plan(strategy=PurePythonStrategy(record_reasoning=True))  # Capture reasoning for debugging
```

---

## File Structure

```
src/nemo_oo_agents/
  strategies/
    __init__.py          # Exports strategy classes
    base.py              # GenerationStrategy ABC
    current_call.py      # CurrentCall dataclass
    pure_python.py       # PurePythonStrategy
    structured_output.py # StructuredOutputStrategy
```

**Files to delete after migration:**
- `src/nemo_oo_agents/runtime/executors/`
- `src/nemo_oo_agents/runtime/errors/messages/`
- `src/nemo_oo_agents/context/prompt_data/`

---

## Full Example: PurePythonStrategy

```python
class PurePythonStrategy(GenerationStrategy):
    """PURE_PYTHON strategy: LLM outputs pure Python code."""

    def __init__(
        self,
        max_iterations: int = 10,
        max_retries: int = 3,
        record_reasoning: bool = False,
    ):
        self.max_iterations = max_iterations
        self.max_retries = max_retries
        self.record_reasoning = record_reasoning

    @property
    def name(self) -> str:
        return "PURE_PYTHON"

    @property
    def strategy_prompt(self) -> str:
        return """## PURE_PYTHON Mode

**Output Format**: Your entire response must be valid Python code.

**Interaction Pattern**:
1. You output Python code
2. SYSTEM executes and returns output
3. Define the target method to complete

**Available**:
- `reasoning("...")` - Chain-of-thought
- `message("...")` - User message
- `print(...)` - Debug output
- `self` - Agent instance
"""

    def _make_builtins(self, runtime: RuntimeServices) -> dict[str, Any]:
        """Build strategy builtins for code execution."""
        return {
            "message": lambda text: runtime.history.add(
                MessageEvent(data=ContentData(content=text))
            ),
            "reasoning": lambda text: runtime.history.add(
                ReasoningEvent(data=ContentData(content=text)),
                record=self.record_reasoning
            ),
        }

    async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
        # Add task to history
        runtime.history.add(TaskEvent(data=ContentData(content=self.task_prompt(call))))

        # Build builtins
        builtins = self._make_builtins(runtime)

        # Generation loop
        error_count = 0
        for iteration in range(self.max_iterations):
            response = await runtime.generate(tools=None, tool_choice="none")
            result = await runtime.execute_code(response.content or "", builtins=builtins)

            if call.method_name in result.defined_methods:
                method = result.defined_methods[call.method_name]
                if asyncio.iscoroutinefunction(method):
                    return await method(*call.args, **call.kwargs)
                return method(*call.args, **call.kwargs)

            if result.error:
                error_count += 1
                if error_count >= self.max_retries:
                    raise GenerationError(f"Max retries ({self.max_retries}) exceeded")
                runtime.history.add(ErrorEvent(
                    data=ContentData(content=self.error_prompt(result.error, response.content))
                ))
            else:
                runtime.history.add(FeedbackEvent(
                    data=ContentData(content=self.execution_feedback(result, call))
                ))

        raise GenerationError(f"Max iterations ({self.max_iterations}) exceeded")

    def task_prompt(self, c: CurrentCall) -> str:
        return f"""# Task

{c.docstring}

## Method

```python
{c.decorator}
{c.signature}
```

## Current Call

```python
{c.call}
```"""

    def error_prompt(self, error: Exception, code: str | None = None) -> str:
        if isinstance(error, SyntaxError):
            return f"## Syntax Error\n\n{error}"
        elif isinstance(error, (NameError, TypeError, AttributeError)):
            return f"## Execution Error\n\n{error}"
        else:
            return f"## Error\n\n{error}"

    def execution_feedback(self, result: ExecutionResult, c: CurrentCall) -> str:
        parts = []
        if result.stdout:
            parts.append(f"## Output\n\n```\n{result.stdout}\n```")
        if result.defined_methods:
            parts.append(f"Methods defined: {list(result.defined_methods.keys())}")
        parts.append(f"Define `{c.method_name}` to complete the task.")
        return "\n\n".join(parts)
```

---

## Nested Execution: Composite Strategies & Subagents

Strategies may need to invoke other strategies (composite patterns) or spawn child agents (subagents). These have different execution semantics.

### RuntimeServices.execute_nested()

For composite strategies that wrap other strategies:

```python
class RuntimeServices(Protocol):
    # ... existing methods ...

    async def execute_nested(
        self,
        strategy: GenerationStrategy,
        call: CurrentCall,
    ) -> Any:
        """Execute a nested strategy within the current generation session.

        Use this for composite strategies (Reflexion, PlanExecute, etc.)
        that need to invoke other strategies.

        Lock behavior: Inherits parent's generation lock (no acquisition).
        Correlation: Events linked via parent_span_id/parent_agent_call_id.
        """
```

### Lock Behavior: Same Agent vs Subagent

| Scenario | Lock Behavior | Why |
|----------|---------------|-----|
| **Composite strategy** (same agent) | Inherits lock | Already holding it; acquiring would deadlock |
| **Nested `@plan` call** (same agent) | Inherits lock | Same as above |
| **Subagent** (different agent) | Own lock | Independent agent, can run concurrently |

**Same agent (composite/nested)**:
```python
# Already in generation session, _in_generation_session = True
# execute_nested() does NOT acquire _generation_lock
# Uses existing span_stack and agent_call_stack
```

**Different agent (subagent)**:
```python
# Normal instantiation: AnalystAgent()
# @agent decorator creates new ActorRuntime with own _generation_lock
# Trace context propagated automatically via contextvar
# Can execute concurrently with parent
```

### Implementation

```python
# runtime/actor.py

async def execute_nested(
    self,
    strategy: GenerationStrategy,
    call: CurrentCall,
) -> Any:
    """Execute nested strategy within current generation session.

    Hooks own all instrumentation - runtime just delegates to strategy.
    """
    # Instrumentation via hooks (hooks handle span creation/correlation)
    hooks = get_instrumentation_hooks()
    ctx = hooks.before_method(self.agent, f"{strategy.name}:{call.method_name}", call.args, call.kwargs)

    try:
        result = await strategy.execute(self, call)
        hooks.after_method(ctx, result, None)
        return result
    except Exception as e:
        hooks.after_method(ctx, None, e)
        raise
```

### Correlation Infrastructure

Correlation is handled entirely by hooks. The external instrumentation package (Phase 1) manages:

```python
# Hooks track correlation - runtime is agnostic
class OTelHooks:
    """External instrumentation handles all correlation."""

    def before_method(self, agent, method_name, args, kwargs) -> SpanContext:
        # Creates span, links to parent via OpenTelemetry context
        ...

    def after_method(self, ctx, result, exception) -> None:
        # Closes span, records attributes
        ...
```

**Nested calls (same agent)**:
- Hooks use OpenTelemetry context propagation
- Parent span automatically linked via context

**Subagents (different agent)**:
- `@agent` decorator propagates trace context via contextvar
- Child hooks create child spans linked to parent
- Independent `_generation_lock` allows concurrent execution

### Example: Composite Strategy

```python
class ReflexionStrategy(GenerationStrategy):
    """Generate → reflect → improve loop."""

    def __init__(self, base: GenerationStrategy = None, max_reflections: int = 3):
        self.base = base or PurePythonStrategy()
        self.max_reflections = max_reflections

    async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
        for i in range(self.max_reflections):
            # Execute base strategy (properly instrumented, shares lock)
            result = await runtime.execute_nested(self.base, call)

            # Reflect on result
            if await self._is_satisfactory(runtime, result):
                return result

            # Add reflection feedback for next iteration
            reflection = await self._generate_reflection(runtime, result)
            runtime.history.add(FeedbackEvent(data=ContentData(content=reflection)))

        return result
```

### Example: Subagent

```python
# In generated code - just normal Python instantiation
analyst = AnalystAgent()
analysis = await analyst.analyze(data)  # Own lock, can run concurrently

# @agent decorator detects creation within generation session
# and propagates trace context automatically
```

---

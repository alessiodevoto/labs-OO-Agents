# Unified Runtime Simplification Plan

**Status**: Draft
**Related**: [phase-1-opentelemetry.md](phase-1-opentelemetry.md), [phase-2-strategy-middleware.md](phase-2-strategy-middleware.md), [phase-3-context-blocks.md](phase-3-context-blocks.md)

## Overview

This plan simplifies the runtime into **three phases**:

1. **Phase 1: Hook-Based Instrumentation** - Move tracing to external package via hooks (MR !36) ✅
2. **Phase 2: Strategy Middleware & Runtime Refactor** - One class = one strategy (NBA-style) + unified namespace 🔄
3. **Phase 3: Context Blocks Extraction** - Standalone library ✅ (package complete, integration in progress)

---

## Component Interfaces

After this refactor, agent006 has **four core components** with clear, minimal interfaces:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              Component Overview                              │
│                                                                              │
│   ┌─────────────────┐      ┌─────────────────┐      ┌──────────────────┐     │
│   │  BlockRenderer  │      │ HistoryManager  │      │GenerationStrategy│     │
│   │ (context-blocks)│      │                 │      │                  │     │
│   └────────┬────────┘      └────────┬────────┘      └────────┬─────────┘     │
│            │                        │                        │               │
│            │   renders prompts      │   event pipeline       │   owns loop   │
│            │                        │                        │               │
│            └────────────────────────┴────────────────────────┘               │
│                                     │                                        │
│                                     ▼                                        │
│                          ┌─────────────────────┐                             │
│                          │   RuntimeServices   │                             │
│                          │   (ActorRuntime)    │                             │
│                          └─────────────────────┘                             │
│                                     │                                        │
│                          provides: generate(), execute_code(),               │
│                                    execute_nested(), history (ref)           │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 1. HistoryManager — Event Pipeline

**Single responsibility**: Record events, notify subscribers, provide history to LLM.

```python
class HistoryManager(Protocol):
    """Unified event pipeline: recording, callbacks, and context for LLM."""

    def add(self, event: Event, *, record: bool = True) -> str:
        """Add event to pipeline.

        Always: notify callbacks.
        If record=True: store in history for LLM context.

        Returns:
            Event ID (for later update() or get() calls).
        """

    def on(self, event_type: str, handler: Callable[[Event], None]) -> Callable[[], None]:
        """Subscribe to events of a specific type.

        Returns:
            Unsubscribe function - call to remove handler.
        """

    def get(self, event_id: str) -> Event | None:
        """Get event by ID."""

    def update(self, event_id: str, **fields) -> bool:
        """Update event fields. Does NOT re-emit to handlers.

        Returns:
            True if found and updated, False if not found.
        """

    def recent(self, limit: int = 50) -> list[Event]:
        """Most recent events (bounded). For context blocks, debugging, etc."""

    def since(self, event_id: str) -> list[Event]:
        """Events added after the given event ID."""

    def for_call(self, call_id: str) -> list[Event]:
        """Events from a specific call only."""

    def for_call_tree(self, call_id: str) -> list[Event]:
        """Events from a call and all its nested calls."""

    def clear(self) -> None:
        """Clear all events."""
```

**What it does**:
- Stores conversation history (user, assistant, tool calls, errors)
- Notifies subscribers when events occur
- Provides events for LLM context building

**What it doesn't do**:
- Format messages for providers (that's BlockRenderer)
- Know about tracing (that's hooks)
- Know about strategies

---

### 2. RuntimeServices — Services for Strategies

**Single responsibility**: Provide LLM generation and code execution to strategies.

```python
class RuntimeServices(Protocol):
    """Services provided by runtime to strategies.

    Note: No tracer - instrumentation handled via hooks at runtime level.
    Note: No execution_config - strategy owns its limits.
    """

    @property
    def agent(self) -> Agent: ...

    @property
    def history(self) -> HistoryManager:
        """Reference to event pipeline (managed by HistoryManager)."""

    async def generate(
        self,
        *,
        tools: list[Tool] | None = None,
        tool_choice: str = "auto",
        output_model: type[BaseModel] | None = None,
        **llm_kwargs,
    ) -> tuple[LLMResponse, str]:
        """Build messages from context + history, call LLM.

        Extra system prompts should come from context blocks, not here.
        llm_kwargs passed through to UnifiedLLM.acall().

        Returns:
            Tuple of (LLMResponse, event_id) where event_id can be used
            for history.update() (e.g., to strip reasoning calls).
        """

    async def execute_code(
        self,
        code: str,
        *,
        builtins: dict[str, Any] | None = None,
        validate: bool = True,
    ) -> ExecutionResult:
        """Execute Python code with namespace + strategy builtins."""

    # Future: execute_nested() for composite strategies
    # async def execute_nested(
    #     self,
    #     strategy: GenerationStrategy,
    #     call: CurrentCall,
    # ) -> Any:
    #     """Execute nested strategy within current generation session.
    #
    #     Use for composite strategies (Reflexion, PlanExecute) that wrap
    #     other strategies. Inherits parent's generation lock (no deadlock).
    #     """
```

**What it does**:
- Builds LLM messages from context blocks + history
- Calls LLM provider
- Builds execution namespace
- Executes generated Python code
- (Future) Executes nested strategies for composites like Reflexion

**What it doesn't do**:
- Own iteration limits (strategy does)
- Know about tracing (hooks do)
- Decide what prompts to use (strategy does)

---

### 3. GenerationStrategy — Execution Loop

**Single responsibility**: Orchestrate the generate→execute loop.

```python
class GenerationStrategy(ABC):
    """One class = one strategy. Owns loop logic, limits, prompts internally."""

    @property
    def name(self) -> str:
        """Strategy identifier for logging/tracing."""
        return self.__class__.__name__

    @property
    def strategy_prompt(self) -> str:
        """Strategy instructions appended to system message by runtime."""
        return ""

    @property
    def requires_lock(self) -> bool:
        """Whether this strategy needs exclusive generation access."""
        return True

    @abstractmethod
    async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
        """Run the generation loop using runtime services."""
```

**What it does**:
- Orchestrates generate→execute loop
- Owns iteration/retry limits (set in `__init__`)
- Provides `strategy_prompt` for system message (public, runtime uses it)
- Defines other prompts internally (task, error, feedback - not part of ABC)

**What it doesn't do**:
- Call LLM directly (runtime does)
- Execute code directly (runtime does)
- Manage history (adds events via `runtime.history.add()`)
- Know about tracing (completely agnostic)

---

### 4. BlockRenderer — Prompt Assembly

**Single responsibility**: Evaluate context blocks and format for LLM providers.

```python
class BlockRenderer:
    """Renders context blocks into provider-specific messages."""

    def render(
        self,
        spec: ContextSpec,
        *,
        eval: Callable[[str], Any],
        block_formatter: BlockFormatter,
        provider_formatter: ProviderFormatter,
    ) -> Any:
        """Evaluate blocks and format for provider.

        Args:
            spec: Context specification with blocks
            eval: Function to evaluate expressions (from make_agent_namespace)
            block_formatter: How to format blocks (XML, Markdown)
            provider_formatter: How to assemble for provider (OpenAI, Anthropic)
        """
```

**What it does**:
- Evaluates block expressions (`expr="self.tools"`)
- Checks visibility (`show="self.mode != 'STRUCTURED'"`)
- Formats blocks (XML tags, Markdown headers)
- Assembles provider-specific output

**What it doesn't do**:
- Know about agents or runtime
- Store state
- Know about execution or strategies

---

## Interaction Flow

```
User calls @plan method
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ ActorRuntime._execute_plan_method()                                         │
│                                                                             │
│   1. ctx = hooks.before_method(agent, method_name, args, kwargs)            │
│   2. current_call = build_current_call(method, args, kwargs)                │
│   3. result = await strategy.execute(self, current_call)    ◄────────────┐  │
│   4. hooks.after_method(ctx, result, exception)                          │  │
│                                                                          │  │
└──────────────────────────────────────────────────────────────────────────│──┘
                                                                           │
        ┌──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ PurePythonStrategy.execute(runtime, call)                                   │
│                                                                             │
│   runtime.history.add(TaskEvent(self.task_prompt(call)))                    │
│   builtins = self._make_builtins(runtime)                                   │
│                                                                             │
│   for iteration in range(self.max_iterations):                              │
│       response = await runtime.generate()           ◄── RuntimeServices     │
│       result = await runtime.execute_code(          ◄── RuntimeServices     │
│           response.content, builtins=builtins                               │
│       )                                                                     │
│                                                                             │
│       if target_defined: return result.value                                │
│       if result.error: runtime.history.add(ErrorEvent(...))                 │
│       else: runtime.history.add(FeedbackEvent(...))                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Nested Execution

Strategies may invoke other strategies (composite patterns from Methodic) or generated code may call `@plan` methods (implicit nesting).

### Lock Behavior

| Scenario | Lock Behavior | Why |
|----------|---------------|-----|
| **Composite strategy** (same agent) | Inherits lock | Already holding it; acquiring would deadlock |
| **Nested `@plan` call** (same agent) | Inherits lock | Same as above |
| **Subagent** (different agent) | Own lock | Independent agent, can run concurrently |

### How It Works

**Composite strategies** use `execute_nested()`:

```python
class ReflexionStrategy(GenerationStrategy):
    """Generate → reflect → improve loop."""

    def __init__(self, base: GenerationStrategy = None, max_reflections: int = 3):
        self.base = base or PurePythonStrategy()
        self.max_reflections = max_reflections

    async def execute(self, runtime: RuntimeServices, call: CurrentCall) -> Any:
        for i in range(self.max_reflections):
            # Nested strategy execution (shares lock, properly instrumented)
            result = await runtime.execute_nested(self.base, call)

            if await self._is_satisfactory(runtime, result):
                return result

            # Add reflection for next iteration
            reflection = await self._generate_reflection(runtime, result)
            runtime.history.add(FeedbackEvent(data=ContentData(content=reflection)))

        return result
```

**Implicit nesting** (generated code calls `@plan` method) is detected automatically via `_in_generation_session` context variable. No explicit API needed.

**Subagents** are created via normal instantiation:

```python
# In generated code - just normal Python
analyst = AnalystAgent()
analysis = await analyst.analyze(data)  # Own lock, runs concurrently
```

The `@agent` decorator detects creation within a generation session and propagates trace context automatically. No `create_child_runtime()` API needed — subagents are just agents.

### Detailed Design

- [phase-1-opentelemetry.md](phase-1-opentelemetry.md) — Hook-based instrumentation
- [phase-2-strategy-middleware.md](phase-2-strategy-middleware.md) — Strategy middleware & nested execution
- [phase-3-context-blocks.md](phase-3-context-blocks.md) — Context blocks library

---

## Comparison with Methodic

agent006's strategy middleware design draws inspiration from [Methodic](https://gitlab-master.nvidia.com/sklingler/methodic) while adapting patterns to fit agent006's architecture.

### What We're Adopting from Methodic

| Pattern | Methodic | agent006 |
|---------|----------|----------|
| **Strategy owns config** | `FunctionCalling(max_iterations=20)` | `PurePythonStrategy(max_iterations=20)` |
| **Strategy as instance** | `@strategy(Predict)` | `@plan(strategy=PurePythonStrategy())` |
| **Composite strategies** | `CompositeStrategy` base class | `execute_nested()` method |
| **Replanning with state** | `CodePlan` includes agent state in replan prompts | Strategy can include `self.doc()` in `error_prompt()` |

### Key Differences

| Aspect | Methodic | agent006 |
|--------|----------|----------|
| **Context management** | `PromptRequirements` dataclass | **Context blocks** with `show` expressions |
| **History** | `chat_history` list on strategy | `HistoryManager` event bus on runtime |
| **Instrumentation** | Direct `span.set_attribute()` in strategies | **Hook-based** - strategies are tracing-agnostic |
| **Tool management** | `refresh_tools()` method | Context blocks with `update` expressions |
| **Verbosity** | `VerbosityLevel` enum | Event subscriptions (`history.on()`) |

### Context Blocks: The New Way

Methodic uses `PromptRequirements` to declare what context a strategy needs:

```python
# Methodic approach
class Predict(Strategy):
    def get_prompt_requirements(self):
        return PromptRequirements(
            include_tools_section=False,
            include_state=True,
            truncation_detail="minimal",
        )
```

agent006 uses **context blocks** with dynamic `show` expressions:

```python
# agent006 approach - context blocks
Block(key="tools", expr="self.doc()", show="strategy.needs_tools")
Block(key="state", expr="self.state", show="True")

# Or via scoped context decorator
@context(scoped_blocks([("history", None)]))  # Hide history for this method
@plan
async def my_ahistoric_method(self):
    ...
```

**Benefits of context blocks**:
- **Dynamic** - `show` expressions evaluated per-render, not static config
- **Composable** - Blocks can reference strategy, agent state, iteration count
- **Introspectable** - Expressions are strings, visible to agents and debuggers
- **Unified** - Same system for context, history, and events

### Strategy Patterns Comparison

| Strategy Type | Methodic | agent006 Equivalent |
|---------------|----------|---------------------|
| **Direct prediction** | `Predict` | `@plan` with `max_iterations=1` |
| **Tool calling** | `FunctionCalling` | Future: `ToolCallingStrategy` |
| **Reflection loop** | `Reflexion(CompositeStrategy)` | `ReflexionStrategy` using `execute_nested()` |
| **Plan then execute** | `PlanExecute(CompositeStrategy)` | `PlanExecuteStrategy` using `execute_nested()` |
| **Adaptive routing** | `AdaptiveCoT` | Strategy with complexity check in `execute()` |
| **Code planning** | `CodePlan` | `PurePythonStrategy` (core pattern) |

### What agent006 Adds

| Feature | Description |
|---------|-------------|
| **Hook-based instrumentation** | Strategies never touch tracing - hooks handle all spans |
| **Event bus history** | Unified pipeline for recording, callbacks, and telemetry |
| **Call-scoped history** | `for_call(id)` and `for_call_tree(id)` for isolation |
| **Context blocks** | Dynamic, introspectable prompt assembly |
| **Correlation IDs** | Comprehensive linking: trace, span, agent_call, parent chains |
| **Generation lock** | Per-agent serialization with proper nested call handling |

### How to Create Methodic-Style Agents

Methodic agents are stateless—each call is independent with no shared conversation history. To achieve this in agent006:

**1. Strategy with `requires_lock = False`**

```python
class MethodicStrategy(GenerationStrategy):
    @property
    def requires_lock(self) -> bool:
        return False  # Allows concurrent calls
```

**2. Context blocks scoped to current call**

```python
Block(
    key="history",
    expr="self.history.for_call(current_call.id)",  # Only this call's events
    show="True"
)
```

This combination enables:
- **Concurrent execution** — Multiple calls run in parallel (no lock contention)
- **Isolated context** — Each call sees only its own history
- **Stateless semantics** — Calls don't affect each other

### Summary

agent006's strategy middleware takes Methodic's core insight (**strategy as instance with owned config**) and builds on it with:

1. **Context blocks** instead of `PromptRequirements` - more dynamic and introspectable
2. **Hook-based instrumentation** - strategies are completely tracing-agnostic
3. **Event bus history** - unified model for history, callbacks, and telemetry
4. **Comprehensive correlation** - full event linking for observability

The result is a cleaner separation of concerns: strategies define *what* to do, runtime provides *how*, and instrumentation happens transparently via hooks.

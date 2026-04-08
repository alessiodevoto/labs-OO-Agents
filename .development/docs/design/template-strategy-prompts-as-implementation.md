# Template Strategy: Prompts as Implementation

**Status**: Proposal
**Related**: [composite-strategies-planning-language.md](composite-strategies-planning-language.md), `PurePythonStrategy`
**Date**: 2025-12-08

## Problem

Strategy prompts are currently defined as configuration strings in dataclasses:

```python
@dataclass
class PurePythonConfig:
    """Configuration for PurePythonStrategy."""

    max_iterations: int = 10  # ← Actual configuration
    max_retries: int = 3      # ← Actual configuration

    # These look like config but are actually implementation:
    instructions: str = """## PURE_PYTHON Mode (Code Execution Loop)
**Output Format**: Your entire response must be valid Python code..."""

    error_empty: str = "Empty response. Output Python code directly. Define `{method}` to complete."
    error_syntax: str = "**Syntax error - ensure complete method definition.**"
    feedback_not_done: str = "Define `{method}` to complete the task."
```

**The problem**: Prompts are **implementation**, not configuration.

| True Configuration | Prompts (Disguised as Config) |
|-------------------|-------------------------------|
| `max_iterations = 10` | `instructions = "Output pure Python..."` |
| Can change freely | Change breaks the strategy |
| Tuning knob | **Defines what the strategy IS** |
| No coupling to code | Tightly coupled to execution logic |

If you change `instructions` to `"meow meow"`, `PurePythonStrategy` breaks. That's not configuration—that's implementation.

### Why This Matters

1. **Misleading API**: Users think they can customize prompts freely
2. **No clear override mechanism**: Subclassing requires understanding internal config structure
3. **Inconsistent with agent006**: Agent methods use docstrings for prompts; strategies use config strings
4. **Not visible in traces**: Prompt generation is invisible
5. **Testing is awkward**: Must mock config objects instead of methods

---

## Proposal: TemplateStrategy + @plan Methods

### Core Insight

Building on [MR 45 - Composite Strategies](composite-strategies-planning-language.md), strategies can have `@plan` methods that execute via an external agent.

**What if prompt templates are just `@plan` methods that use a strategy which doesn't call the LLM?**

```python
class TemplateStrategy(GenerationStrategy):
    """Strategy that formats docstring with arguments. No LLM call."""

    async def execute(self, agent: Agent, task: CurrentCall) -> str:
        template = task.docstring or ""
        # Format with call arguments
        return template.format(**task.kwargs)
```

### Developer Interface

Prompts become `@plan` methods with docstrings:

```python
class PurePythonStrategy(CompositeStrategy):
    """PURE_PYTHON strategy: LLM outputs pure Python code."""

    # === Prompt Templates (docstring = implementation) ===

    @plan(strategy=TemplateStrategy())
    async def error_empty(self, method: str) -> str:
        """Empty response. Output Python code directly. Define `{method}` to complete."""
        ...

    @plan(strategy=TemplateStrategy())
    async def error_syntax(self, method: str) -> str:
        """**Syntax error - ensure you output a complete method definition.**

You MUST define: `async def {method}(...):`"""
        ...

    @plan(strategy=TemplateStrategy())
    async def instructions(self) -> str:
        """## PURE_PYTHON Mode (Code Execution Loop)

**Output Format**: Your entire response must be valid Python code. No markdown, no fences.

**Interaction Pattern**: This is a code execution loop:
1. You output Python code (your entire response is Python)
2. The SYSTEM executes your code and returns the output
3. Messages labeled "[SYSTEM]" are execution results, not human requests
4. The session ends when you define the target method

**Available in Your Code**:
- `reasoning("...")` - Your thinking/chain-of-thought (not shown to user)
- `message("...")` - Send a message to the user
- `print(...)` - Debug output (returned to you)
- `self` - The agent instance
"""
        ...

    @plan(strategy=TemplateStrategy())
    async def feedback_not_done(self, method: str, defined_methods: list[str]) -> str:
        """Output:
```
{stdout}
```

Methods defined: {defined_methods}

Define `{method}` to complete the task."""
        ...

    # === Configuration (actual tuning knobs) ===

    def __init__(self, *, max_iterations: int = 10, max_retries: int = 3):
        self.max_iterations = max_iterations
        self.max_retries = max_retries

    # === Execution ===

    async def execute(self, agent: Agent, task: CurrentCall) -> Any:
        target_method = task.method_name

        for iteration in range(self.max_iterations):
            response, _ = await self.run(agent, task)
            code = response.content.strip()

            if not code:
                # Prompt template executes via TemplateStrategy (no LLM call)
                error_msg = await self.error_empty(agent, method=target_method)
                agent.history_manager.add(ErrorEvent(data=ContentData(content=error_msg)))
                continue

            result = await agent.runtime.execute_code(code)

            if result.error:
                if "'return' outside function" in str(result.error):
                    error_msg = await self.error_syntax(agent, method=target_method)
                else:
                    error_msg = f"Error: {result.error}"
                agent.history_manager.add(ErrorEvent(data=ContentData(content=error_msg)))
                continue

            if target_method in result.defined_methods:
                return await result.defined_methods[target_method](*task.args, **task.kwargs)

            # Not done yet
            feedback = await self.feedback_not_done(
                agent,
                method=target_method,
                defined_methods=list(result.defined_methods.keys()),
                stdout=result.stdout,
            )
            agent.history_manager.add(FeedbackEvent(data=ContentData(content=feedback)))
```

### Key Points

1. **`@plan(strategy=TemplateStrategy())`** - Declares this is a prompt template
2. **Docstring IS the template** - Consistent with agent006 philosophy
3. **Method parameters become template variables** - `{method}`, `{defined_methods}`, etc.
4. **No LLM call** - `TemplateStrategy` just formats the string
5. **Executes via agent** - Follows composite strategy pattern from MR 45
6. **Visible in traces** - Prompt generation appears in execution trace

---

## TemplateStrategy Implementation

```python
class TemplateStrategy(GenerationStrategy):
    """Strategy that formats docstring with call arguments. No LLM call.

    This is the "zero-th" strategy - pure string templating, no generation.
    Use for prompt templates that are implementation, not configuration.

    Template variables come from method parameters:
        @plan(strategy=TemplateStrategy())
        async def error_msg(self, method: str, line: int) -> str:
            '''Error in `{method}` at line {line}.'''
            ...

        # Calling: await self.error_msg(agent, method="foo", line=42)
        # Returns: "Error in `foo` at line 42."

    Advanced: Access agent state via {self.xxx} if self is in scope:
        '''Found {len(self.items)} items.'''
    """

    @property
    def name(self) -> str:
        return "TEMPLATE"

    @property
    def requires_lock(self) -> bool:
        return False  # No LLM call, no serialization needed

    @property
    def strategy_prompt(self) -> str:
        return ""  # No prompt needed - we don't call LLM

    async def execute(self, agent: Agent, task: CurrentCall) -> str:
        """Format docstring template with call arguments."""
        template = task.docstring or ""

        if not template:
            return ""

        # Build context from call arguments
        context = dict(task.kwargs)

        # Add positional args by parameter name
        if task.signature:
            import inspect
            try:
                # Get parameter names from signature string
                # task.signature is like "(self, method: str, line: int) -> str"
                sig = inspect.signature(getattr(agent, task.method_name, lambda: None))
                params = [p for p in sig.parameters if p != 'self']
                for i, name in enumerate(params):
                    if i < len(task.args):
                        context[name] = task.args[i]
            except (ValueError, TypeError):
                pass

        # Add agent reference for {self.xxx} patterns
        context['self'] = agent

        # Format template
        try:
            return template.format(**context)
        except KeyError as e:
            # Helpful error for missing template variables
            raise ValueError(
                f"Template variable {e} not found in method parameters. "
                f"Available: {list(context.keys())}"
            ) from e
```

---

## Integration with Composite Strategy Pattern

This proposal builds on [MR 45 - Composite Strategies](composite-strategies-planning-language.md). The `@plan` wrapper needs to support custom strategies:

```python
class CompositeStrategy(GenerationStrategy):
    """Base class for strategies with @plan sub-tasks."""

    @classmethod
    def _make_wrapper(cls, method):
        """Create wrapper that executes @plan method on agent."""
        @functools.wraps(method)
        async def wrapper(self, agent: Agent, *args, **kwargs):
            plan_call = CurrentCall.from_method(method, args, kwargs)

            # Support @plan(strategy=X) - key addition
            strategy = getattr(method, '_plan_strategy', None) or PurePythonStrategy()

            return await agent.runtime.execute_nested(strategy, plan_call)
        return wrapper
```

---

## Strategy Hierarchy

With this proposal, the strategy hierarchy becomes:

```
GenerationStrategy (ABC)
│
├── TemplateStrategy              # No LLM - format docstring with args
│                                 # The "zero-th" strategy
│
├── StructuredOutputStrategy      # Single LLM call → structured data
│                                 # For simple transformations
│
└── CompositeStrategy             # Has @plan sub-task methods
    │
    ├── PurePythonStrategy        # Multi-turn code generation loop
    │   └── @plan prompts use TemplateStrategy
    │
    ├── ReflexionStrategy         # Generate → reflect → improve
    │   └── @plan reflect() uses StructuredOutput
    │
    ├── PlanExecuteStrategy       # Plan → execute steps → synthesize
    │   └── @plan create_plan() uses StructuredOutput
    │
    └── EnsembleStrategy          # Multiple candidates → vote
        └── @plan select_best() uses StructuredOutput
```

**Key insight**: `TemplateStrategy` and `StructuredOutputStrategy` are the two primitive "interface strategies" that higher-level composite strategies build upon.

---

## Examples

### Customizing Prompts via Subclass

```python
class VerbosePurePython(PurePythonStrategy):
    """PurePython with more detailed error messages."""

    @plan(strategy=TemplateStrategy())
    async def error_syntax(self, method: str, error: str) -> str:
        """## Syntax Error

Your code has a syntax error:
```
{error}
```

**Common causes:**
1. Missing function definition - you output `return x` without `async def {method}(...)`
2. Incorrect indentation
3. Unclosed brackets or quotes

**Required format:**
```python
async def {method}(self, ...):
    # your implementation
    return ...
```

Please fix and try again."""
        ...
```

### Dynamic Templates with Agent State

```python
class PurePythonStrategy(CompositeStrategy):

    @plan(strategy=TemplateStrategy())
    async def context_summary(self) -> str:
        """## Current Context

Agent: {self.__class__.__name__}
Available tools: {len(self.doc._get_all_tool_names())}
History length: {len(self.history_manager.events)}
"""
        ...
```

### Reflexion Strategy with Template Prompts

```python
class ReflexionStrategy(CompositeStrategy):
    """Generate → reflect → improve loop."""

    base: GenerationStrategy
    max_reflections: int = 3

    @plan(strategy=TemplateStrategy())
    async def reflection_prompt(self, task: str, result: str) -> str:
        """Critically evaluate the result of the task execution.

**Original task:** {task}

**Result produced:**
```
{result}
```

**Evaluate:**
1. Does this result fully address the task requirements?
2. Are there any errors, inaccuracies, or inconsistencies?
3. What specific improvements could be made?

Be strict but fair. Only mark as satisfactory if the result truly meets all requirements."""
        ...

    @plan(strategy=StructuredOutputStrategy())
    async def reflect(self, prompt: str) -> Reflection:
        """Evaluate the result based on the prompt."""
        ...

    @plan(strategy=TemplateStrategy())
    async def improvement_feedback(self, issues: list[str], suggestions: list[str]) -> str:
        """Your implementation needs improvement.

**Issues identified:**
{issues_formatted}

**Suggestions:**
{suggestions_formatted}

Please create an improved implementation that addresses these issues."""
        ...

    async def execute(self, agent: Agent, task: CurrentCall) -> Any:
        for _ in range(self.max_reflections):
            result = await self.run(agent, task)

            # Build reflection prompt (TemplateStrategy - no LLM)
            prompt = await self.reflection_prompt(agent, task=task.docstring, result=str(result))

            # Reflect on result (StructuredOutputStrategy - LLM call)
            reflection = await self.reflect(agent, prompt=prompt)

            if reflection.is_satisfactory:
                return result

            # Build feedback (TemplateStrategy - no LLM)
            feedback = await self.improvement_feedback(
                agent,
                issues=reflection.issues,
                suggestions=reflection.suggestions,
            )
            agent.history_manager.add(FeedbackEvent(data=ContentData(content=feedback)))

        return result
```

---

## Trace Output

With this approach, prompt generation appears in traces:

```
agent.analyze("some data")
├── [PurePython] Starting generation
│   ├── instructions()
│   │   └── [Template] "## PURE_PYTHON Mode..."
│   ├── [LLM] Generate code
│   ├── [Execute] SyntaxError: 'return' outside function
│   ├── error_syntax(method="analyze")
│   │   └── [Template] "**Syntax error - ensure complete..."
│   ├── [LLM] Generate code (retry)
│   ├── [Execute] OK, defined: analyze
│   └── return result
```

This provides visibility into prompt construction that's currently hidden.

---

## Benefits

| Aspect | Current (Config) | Proposed (TemplateStrategy) |
|--------|-----------------|----------------------------|
| Prompt location | Dataclass field | @plan method docstring |
| Clearly implementation? | ❌ Looks like config | ✅ It's a method |
| Override mechanism | Pass config object | Subclass + override method |
| Consistency | ❌ Different from agents | ✅ Same @plan pattern |
| Trace visibility | ❌ Hidden | ✅ Appears in trace |
| IDE support | ❌ Just strings | ✅ Methods with signatures |
| Testing | Mock config | Mock/stub individual methods |
| Documentation | Config field docstrings | Method docstrings |

---

## Migration Path

### Phase 1: Add TemplateStrategy

Add `TemplateStrategy` as a new primitive strategy. No breaking changes.

```python
# agent006/strategies/template.py
class TemplateStrategy(GenerationStrategy):
    ...
```

### Phase 2: Update CompositeStrategy (MR 45)

Enable `@plan(strategy=X)` support in the wrapper.

### Phase 3: Refactor PurePythonStrategy

Convert prompt config fields to `@plan(strategy=TemplateStrategy())` methods.

```python
# Before
class PurePythonConfig:
    error_empty: str = "..."

class PurePythonStrategy:
    def __init__(self, config: PurePythonConfig | None = None):
        self.config = config or PurePythonConfig()

# After
class PurePythonStrategy(CompositeStrategy):
    @plan(strategy=TemplateStrategy())
    async def error_empty(self, method: str) -> str:
        """..."""
        ...
```

### Phase 4: Deprecate PurePythonConfig

Mark config-based prompt customization as deprecated. Guide users to subclassing.

---

## Open Questions

1. **Should TemplateStrategy support `runtime.expand_variables()`?**
   - Pro: More powerful expressions like `{len(self.items)}`
   - Con: `str.format()` is simpler and sufficient for most cases
   - Recommendation: Start with `str.format()`, add `expand_variables` mode later if needed

2. **How to handle multi-line docstrings with indentation?**
   - `inspect.getdoc()` already handles dedent
   - Should work correctly

3. **What about prompts that need agent context but aren't on CompositeStrategy?**
   - `TemplateStrategy` receives `agent` in execute, can access `agent.xxx`
   - Template can use `{self.xxx}` if we pass `self=agent` to format context

4. **Performance of async calls for string formatting?**
   - `TemplateStrategy` is fast (no I/O, no LLM)
   - async overhead is minimal
   - Benefit of consistency outweighs micro-optimization concern

---

## Conclusion

By treating prompts as `@plan` methods with `TemplateStrategy`, we:

1. **Acknowledge prompts are implementation** - They're methods, not config
2. **Unify the pattern** - Everything is `@plan` with a strategy
3. **Enable visibility** - Prompt generation appears in traces
4. **Support customization** - Subclass and override, don't configure
5. **Build on MR 45** - Extends composite strategy pattern naturally

`TemplateStrategy` becomes the foundational "zero-th" strategy—pure string templating with no LLM call—that higher-level strategies compose with.

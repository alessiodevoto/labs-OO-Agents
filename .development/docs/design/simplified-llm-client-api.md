# Simplified LLM Client API Design

## Problem

Current `_resolve_llm_client()` has 5-level resolution with context variables, decorator params, and implicit inheritance. Too much magic.

## Proposed Solution

Four explicit configuration points. No resolution logic.

---

## 1. Decorator Configuration (Required)

The `llm` argument is required on `@agent`:

```python
from unifiedllm import CompletionClient

@agent(
    llm=CompletionClient(
        model="gpt-4o",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7,
    )
)
class MyAgent(Agent):
    ...
```

Or for different providers:

```python
@agent(
    llm=CompletionClient(
        model="anthropic/claude-sonnet-4-20250514",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )
)
class ResearchAgent(Agent):
    ...
```

---

## 2. Instantiation Override

```python
# Use decorator default
agent = MyAgent()

# Override at instantiation
agent = MyAgent(
    llm=CompletionClient(
        model="gpt-4o-mini",
        api_key=os.getenv("OPENAI_API_KEY"),
    )
)
```

---

## 3. @plan Decorator Override

```python
@agent(llm=CompletionClient(model="gpt-4o", ...))
class MyAgent(Agent):

    @plan  # Uses agent's LLM
    def run(self):
        ...

    @plan(llm=CompletionClient(model="gpt-4o-mini", ...))
    def quick_task(self):
        # This method uses a cheaper model
        ...
```

---

## 4. Call-time Override

```python
agent = MyAgent()

# Use method's default LLM
result = agent.run()

# Override for this call only
result = agent.run(
    llm=CompletionClient(
        model="gpt-4o-mini",
        api_key=os.getenv("OPENAI_API_KEY"),
    )
)
```

Useful for:
- Trying a cheaper model for simple tasks
- A/B testing models
- Fallback to different provider

---

## 5. Resolution Order

```python
# In @plan execution:
effective_llm = call_llm or plan_llm or self._llm

# Three levels, all explicit.
```

---

## 6. Agent.__init__ Signature

```python
class Agent:
    def __init__(
        self,
        llm: UnifiedLLM | None = None,
        on_message: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ):
        # Simple: use provided llm, or fall back to decorator's llm
        self._llm = llm if llm is not None else self.__class__._agent_llm
```

That's it. Explicit, no magic. The decorator guarantees `_agent_llm` exists.

---

## 7. Child Agents

Each agent defines its own LLM in its decorator:

```python
@agent(llm=CompletionClient(model="gpt-4o", ...))
class ParentAgent(Agent):

    @plan
    def run(self):
        # Child uses its own decorator-defined LLM
        child = ChildAgent()

        # Or override at instantiation
        child = ChildAgent(llm=self._llm)


@agent(llm=CompletionClient(model="gpt-4o-mini", ...))
class ChildAgent(Agent):
    ...
```

No context variables. No implicit inheritance. Each agent is self-contained.

---

## 8. What Gets Deleted

| Current | Replacement |
|---------|-------------|
| `_resolve_llm_client()` (100 LOC) | 1-line fallback |
| `LLMConfig` dataclass | Direct `CompletionClient` kwargs |
| `_current_runtime_var` | Explicit `llm=self._llm` |
| `model` param on Agent | Just use `llm=` |
| `llm_config` param on Agent | Just use `llm=` |
| **`src/agent006/config.py` (entire file)** | Caller passes model/key in decorator |

### Delete `config.py` entirely

The file provides "magic" defaults that hide configuration:

```python
# config.py - DELETE THIS
def get_default_model() -> str: ...      # Magic default model
def get_default_api_base() -> str: ...   # Magic default endpoint
def get_default_api_key() -> str: ...    # Magic default key lookup
```

With the new design, there are no defaults. The `@agent` decorator requires explicit configuration. If you want a "default" for your project, define it once and reuse:

```python
# myproject/llm.py
DEFAULT_LLM = CompletionClient(
    model="gpt-4o",
    api_key=os.getenv("OPENAI_API_KEY"),
)

# Then use it
@agent(llm=DEFAULT_LLM)
class MyAgent(Agent):
    ...
```

This is explicit, discoverable, and under user control—not hidden in framework internals.

---

## 9. Tradeoffs

**Lost:**
- Implicit child inheritance (must pass `llm=self._llm`)
- Optional LLM (decorator now requires it)

**Gained:**
- Obvious behavior
- Full type safety
- No hidden resolution order
- Debuggable (you see exactly what's passed)

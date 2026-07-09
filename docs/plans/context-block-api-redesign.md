# Context Block API Redesign

**Status**: Proposal
**Author**: pfurgale
**Date**: 2026-07-01

## Problem

Context blocks come from too many sources with inconsistent APIs:
- Framework defaults (`system_prompt`, `self`, `state`)
- Strategy blocks (`strategy_prompt`, `execution_context`)
- Developer/agent-set blocks
- Skill-registered blocks

The current API exposes confusing internal concepts (`set_static` vs `set_dynamic`, `DynamicContext` vs plain strings, `ScopedContext` wrapper) and doesn't work uniformly across agent methods and standalone functions.

## Proposal

### One unified value type: `Context`

```python
from nooa import Context

context={
    "role": "You are a security expert",                       # bare str = suffix, literal
    "shell": Context(expr="doc(self.shell)", prefix=True),     # prefix, evaluated each turn
    "config": Context("fixed config text", prefix=True),       # prefix, literal
    "status": Context(expr="f'{self.done}/{self.total}'"),     # suffix, evaluated each turn
    "self": None,                                              # suppress block
}
```

### Decisions

- **Bare `str`** → suffix partition (volatile). Simple, changes often.
- **`Context(value=...|expr=..., prefix=bool)`** → explicit control over both axes.
- **`None`** → suppress block from prompt (works on protected framework blocks too).
- **`DynamicContext`** → removed. Replaced by `Context(expr="...")`.
- **`set_static()` / `set_dynamic()`** → deprecated. Will be removed after migrating all agents.

### Same dict, every entry point

```python
# 1. Class-level
class MyAgent(Agent, llm=llm, context={
    "role": Context("You are a security expert", prefix=True),
    "self": None,  # suppress doc(self) block
}):
    pass

# 2. Instance-level
agent = MyAgent(context={"focus": "performance analysis"})

# 3. @strategy decorator (agent methods)
@strategy(CodeActStrategy(), context={
    "focus": "Write comprehensive tests",
    "state": None,
})
async def write_tests(self, code: str) -> str: ...

# 4. @strategy decorator (standalone functions)
@strategy(CodeActStrategy(), context={
    "role": "expert summarizer",
    "execution_context": None,
}, llm=llm)
async def summarise(text: str) -> str:
    """Summarise {text} in one sentence."""
    ...

# 5. Scoped (with-block)
with ScopedContext(context={"urgency": "high", "state": None}):
    result = await agent.analyze(data)

# 6. Runtime (inside agent code, or constructors) — SAME TYPES
self.context["role"] = "You are an expert"
self.context["shell"] = Context(expr="doc(self.shell)", prefix=True)
self.context["status"] = Context(expr="f'{self.done}/{self.total}'")
self.context["self"] = None
```

### The `Context` class

```python
class Context:
    """Unified context block value.

    Controls both content (literal vs expression) and placement (prefix vs suffix).
    """

    def __init__(
        self,
        value: str | None = None,
        *,
        expr: str | None = None,
        prefix: bool = False,
    ):
        """
        Args:
            value: Literal text content (mutually exclusive with expr).
            expr: Python expression re-evaluated each LLM turn (mutually exclusive with value).
            prefix: If True, place in the cacheable prefix partition.
                    If False (default), place in the volatile suffix.
        """
        if value is not None and expr is not None:
            raise TypeError("Pass value or expr, not both")
        if value is None and expr is None:
            raise TypeError("Must provide value or expr")
        self.value = value
        self.expr = expr
        self.prefix = prefix
```

### Value type grid

| Value | Placement | Content | Use case |
|-------|-----------|---------|----------|
| `"text"` | suffix | Fixed literal | Temporary notes, per-turn status |
| `Context("text", prefix=True)` | prefix | Fixed literal | Agent identity, stable config |
| `Context(expr="self.x()")` | suffix | Re-evaluated | Dynamic status, progress |
| `Context(expr="doc(self.shell)", prefix=True)` | prefix | Re-evaluated | Tool docs (stable across turns) |
| `None` | — | Suppress | Remove framework/strategy blocks |

### Prefix vs suffix

- **Prefix** = cacheable across LLM turns. Forms a stable shared prefix that providers can cache. Use for blocks stable across multiple turns.
- **Suffix** = volatile. Changes between turns. Default.

### Runtime API

```python
# Dict assignment — the ONLY interface:
self.context["key"] = "literal text"                            # suffix, literal
self.context["key"] = Context(expr="self.status()")             # suffix, evaluated
self.context["key"] = Context(expr="doc(self.shell)", prefix=True)  # prefix, evaluated
self.context["key"] = Context("stable text", prefix=True)       # prefix, literal
self.context["key"] = None                                      # suppress

# Suppress = None, restore = assign a value. No separate enable/disable.
self.context["self"] = None                                     # suppress framework block
self.context["self"] = Context(expr="doc(type(self))", prefix=True)  # restore it

# Introspection:
self.context.all_keys()                # discover all block keys
del self.context["key"]                # remove user block entirely
```

### Deprecated (will migrate, then remove)

**Why:** `set_static()` and `set_dynamic()` conflate two independent concepts into one method name:

1. **Content type** — is the value a fixed literal or a re-evaluated expression?
2. **Placement** — does the block live in the cacheable prefix or the volatile suffix?

The names suggest a single axis ("static" vs "dynamic") but the methods actually control *both* axes simultaneously, and not in the way the names imply:

| Method | Content | Placement | Confusing? |
|--------|---------|-----------|-----------|
| `set_static("k", "v")` | literal | prefix | Sounds right… |
| `set_static("k", expr="e")` | **expression** | prefix | "Static" that changes every turn?! |
| `set_dynamic("k", "e")` | expression | suffix | OK but only because both happen to be "dynamic" |
| `self.context["k"] = "v"` | literal | suffix | Why suffix? It's a fixed string! |

The new `Context(value|expr, prefix=bool)` makes both axes explicit and independent:

```python
Context("text", prefix=True)       # literal + prefix — clear
Context(expr="self.x()", prefix=True)  # expression + prefix — clear
Context(expr="self.x()")           # expression + suffix — clear
"text"                             # literal + suffix — shorthand
```

**Deprecated methods:**

```python
# DEPRECATED — use Context("value", prefix=True) instead
self.context.set_static("key", "value")

# DEPRECATED — use Context(expr="self.x()", prefix=True) instead
self.context.set_static("key", expr="self.x()")

# DEPRECATED — use Context(expr="self.x()") or bare str instead
self.context.set_dynamic("key", "self.x()")

# DEPRECATED — use self.context["key"] = None instead
self.context.disable("key1", "key2")

# DEPRECATED — use self.context["key"] = Context(...) instead
self.context.enable("key1")
```

### Well-known block keys

| Key | Source | Description |
|-----|--------|-------------|
| `system_prompt` | Framework | Agent class docstring |
| `self` | Framework | `doc(type(self))` introspection |
| `state` | Framework | Current instance field values |
| `strategy_prompt` | Strategy | Strategy instructions |
| `execution_context` | Strategy (CodeAct) | Available imports and types |

### Migration path

1. Implement `Context` class alongside existing `DynamicContext`
2. Make `__setitem__` accept `Context`, `None`, and bare `str`
3. Add `context=` kwarg to `@strategy()` decorator (direct, without ScopedContext wrapper)
4. Deprecate `DynamicContext`, `set_static()`, `set_dynamic()`
5. Migrate all agents to new API
6. Remove deprecated APIs

### Backwards compatibility (during migration)

- `DynamicContext("expr")` treated as `Context(expr="expr")` internally
- `set_static()` / `set_dynamic()` emit deprecation warning, continue working
- `ScopedContext(context={...})` still works (needed when combining with `events=`)
- Existing `context={"k": None}` semantics unchanged

## Non-goals

- Changing how strategies define their blocks internally (`get_block_overrides`)
- Changing the event filtering API (`EventQuery`)
- Exposing the context builder pipeline to end users

## Open questions

1. Should `Context` be the public name, or something shorter/different? (`Block`?)
2. Should we provide a `SUPPRESS = None` constant for readability?
3. Timeline for deprecation removal?

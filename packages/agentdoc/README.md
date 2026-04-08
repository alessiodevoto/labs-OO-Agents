# agentdoc

Python introspection utilities for AI agents. Generates token-efficient, structured documentation of Python objects at runtime — suitable for injecting into prompts.

## Installation

```bash
uv pip install agentdoc
```

## Mental model

1. **`spec()`** — specify how the type renders: field descriptions, visibility rules, rendering hints.
2. **`doc()`** — get the documentation: the API contract, ready to inject into a prompt.

```python
from agentdoc import spec, hidden, doc, pformat
from typing import Annotated

class SearchAgent:
    """Agent that searches a document index."""
    index_path: Annotated[str, "Path to search index"] = "data/index.json"
    max_results: int = 10
    api_key: Annotated[str, hidden] = ""          # excluded from documentation
    label: Annotated[str, spec(description="Display name")] = "default"

    def search(self, query: str) -> list[str]:
        """Search the index for {query}."""
        ...

    def rerank(self, results: list[str], query: str) -> list[str]:
        """Rerank {results} by relevance to {query}."""
        ...

agent = SearchAgent()
agent.index_path = "data/prod.json"
agent.max_results = 5
```

```python
>>> print(doc(SearchAgent))
class SearchAgent:
    """Agent that searches a document index."""

    index_path: str = 'data/index.json'  # Path to search index
    max_results: int = 10

    def rerank(self, results: list[str], query: str) -> list[str]:
        """Rerank {results} by relevance to {query}."""
    def search(self, query: str) -> list[str]:
        """Search the index for {query}."""

>>> print(pformat(agent))
SearchAgent(index_path='data/prod.json', max_results=5)

>>> print(doc(agent))
class SearchAgent:
    """Agent that searches a document index."""

    index_path: str = 'data/prod.json'
    max_results: int = 5

    def rerank(self, results: list[str], query: str) -> list[str]:
        """Rerank {results} by relevance to {query}."""
    def search(self, query: str) -> list[str]:
        """Search the index for {query}."""
```

`api_key` is absent from both outputs because it is annotated with `hidden`.

## Core API

### `doc(*objs, concise=False, inline_depth=None) -> str`

Render the API contract of a type, function, module, or instance.

```python
doc(MyAgent)               # class with fields + methods
doc(my_agent)              # same, resolved from an instance
doc(some_function)         # function signature + docstring
doc(my_module)             # module-level symbols
doc(MyAgent, concise=True) # first-line docstrings only
doc(ClassA, ClassB, fn)    # multiple objects, deduplicated referenced types
```

`inline_depth` controls how many levels of referenced types are expanded inline (default `1` when `concise=False`, `0` when `concise=True`).

**Inherited fields:** `doc()` walks the full MRO so parent-class fields appear before child-class fields. Fields hidden in a parent remain hidden unless the child re-declares them without `hidden`.

**Docstring `{param}` placeholders:** Curly-brace placeholders in method docstrings are an nemo_oo_agents runtime convention — the framework substitutes actual argument values when calling generation methods. `doc()` shows the raw template string, which is correct: it displays the prompt pattern as-is for documentation consumers to see the API contract.

### `pformat(obj, ...) -> str` and `pprint(obj, ...)`

Drop-in replacements for `rich.pretty.pformat()` and `rich.pretty.pprint()` — same signature, no Rich dependency required. For user-defined instances, hidden fields are automatically excluded.

```python
from agentdoc import pformat, pprint

pformat(my_agent)                        # formatted string
pformat(my_agent, max_length=5)          # truncate lists to 5 items
pformat(my_agent, max_string=50)         # truncate strings to 50 chars
pformat(my_agent, max_depth=3)           # limit nesting depth
pprint(my_agent)                         # print to stdout

# Rich compatibility: console and indent_guides are accepted but ignored
pformat(obj, console=console, indent_guides=True)
```

The `console` and `indent_guides` parameters are accepted for API compatibility with Rich but have no effect.

### `spec()` — specify documentation metadata

Three forms, one function. Which to use:

| Scenario | Form | Example |
|----------|------|---------|
| Field in a type you own | Annotated marker | `x: Annotated[int, spec(description="…")] = 0` |
| Method/class you own | Decorator | `@spec(hidden=True)` |
| Type you don't own | Imperative | `spec(ThirdPartyClass, "field", hidden=True)` |

Quick rule: use `@hidden` for simple method hiding; use `@spec(hidden=True)` when you also need to attach a `description` or `expand` hint in the same call.

```python
# 1. Annotated marker — inline with the field annotation
class Config:
    host: Annotated[str, spec(description="DB hostname")] = "localhost"
    port: Annotated[int, spec(description="DB port")] = 5432

# 2. Decorator — on a method or class you own
@spec(hidden=True)
def _internal_helper(self): ...

@spec(expand=False)       # collapse sub-type to a one-liner in doc()
class HeavyTool: ...

@spec(hidden=False)       # opt a dunder or _-prefixed method INTO doc() output
def __init__(self, x: float = 0.0) -> None: ...

# 3. Imperative — for types you don't own
spec(ThirdPartyClass, "password", hidden=True)        # hide a field
spec(ThirdPartyClass, "method_name", hidden=True)     # hide a method
spec(ThirdPartyClass, "batch_size", description="Records per batch")
```

`Annotated[T, "plain string"]` and `spec(description="plain string")` are equivalent; both render as `# plain string` in `doc()` output.

**Method parameter descriptions:** `Annotated` descriptions on function parameters are automatically extracted and appended as an `Args:` section in the rendered docstring if none exists:

```python
def search(self, query: Annotated[str, "The search query"]) -> list[str]:
    """Search the index."""
    ...

# doc() renders:
#   def search(self, query: str) -> list[str]:
#       """Search the index.
#
#       Args:
#           query: The search query
#       """
```

**Field type inline comments:** When a field has no explicit description but its type has a docstring, `doc()` uses the first line of the type's docstring as an inline comment:

```python
class VectorDB:
    """Semantic vector store for embedding search."""
    ...

class Agent:
    db: VectorDB  # no description

# doc(Agent) renders:
#   db: VectorDB  # Semantic vector store for embedding search.
```

### `hidden` — visibility control

```python
# Field: exclude from doc() and pformat()
api_key: Annotated[str, hidden] = ""

# Method: exclude from doc()
@hidden
def _rebuild_index(self) -> None: ...

# Context manager: hide names defined in the block
with hidden:
    import secrets
```

**Unhiding parent fields** — use `spec()`, not re-declaration:

```python
class BaseAgent:
    context: Annotated[ContextApi, hidden, nosnapshot]
```

**Instance opt-in** — `doc(self)` sees it; `doc(MyAgent)` does not. Ideal for agent subclasses where the LLM is the consumer of `doc(self)`:

```python
class MyAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        spec(self, "context", hidden=False)  # per-instance, does not touch the annotation
```

**Class opt-in** — both `doc(MyAgent)` and `doc(instance)` see it:

```python
class MyAgent(BaseAgent):
    pass

spec(MyAgent, "context", hidden=False)  # after class definition; does not touch the annotation
```

> **Warning:** Re-declaring the field in the subclass body (`context: ContextApi = None`) silently drops all other parent annotations — `nosnapshot`, descriptions, `expand` hints. Use `spec()` instead.

## Annotations as inline comments

`Annotated[T, "description"]` and `spec(description=...)` render as `# comments` on the field line in `doc()` output, and as `Args:` sections in function documentation.

```python
class Embedding:
    vector: Annotated[list[float], "Raw embedding values"]
    model: Annotated[str, "Model that produced this embedding"] = "text-embedding-3-small"
    dims: Annotated[int, "Embedding dimensionality"] = 1536

# doc(Embedding) →
# class Embedding:
#     vector: list[float]  # Raw embedding values
#     model: str = 'text-embedding-3-small'  # Model that produced this embedding
#     dims: int = 1536  # Embedding dimensionality
```

## Pydantic interop

`Field(repr=False)` is treated as `hidden=True` — the field is excluded from `doc()` and `pformat()`:

```python
class User(BaseModel):
    username: str
    secret_key: str = Field(default="xyz", repr=False)  # hidden

doc(User)       # secret_key absent
pformat(User()) # secret_key absent
```

## `spec.define_doc()` — specify a custom TypeInfo extractor

For third-party types (or any type with complex internal state), specify exactly how `doc()` represents it.

The `TypeInfo` and `FieldInfo` types are importable from `agentdoc.ext`.

```python
from agentdoc.ext import TypeInfo, FieldInfo

# Specify the API contract for doc() (TypeInfo extractor)
@spec.define_doc(httpx.Client)
def _(cls_or_instance) -> TypeInfo | tuple[TypeInfo, dict]:
    info = TypeInfo(
        name="Client",
        base=None,
        fields=[FieldInfo(name="base_url", type="str")],
        methods=[],
        docstring="An HTTP client.",
    )
    if isinstance(cls_or_instance, type):
        return info
    return info, {"base_url": cls_or_instance.base_url}
```

Extractors are inherited by subclasses (MRO order); a subclass-specific definition takes precedence.

## Known limitation: `__init__`-only fields

`doc()` reads class-level annotations to discover fields. Fields assigned only in `__init__` with no class-level annotation are invisible to `doc()` (but are visible to `pformat()` via `__dict__`).

```python
class Bad:
    def __init__(self, host: str):
        self.host = host          # doc() cannot see this

class Good:
    host: str                     # class-level annotation — doc() sees it
    def __init__(self, host: str):
        self.host = host
```

Workaround: add a class-level annotation (with or without a default).

## Known Limitations

- **Circular references:** `pformat()` handles circular containers gracefully via `max_depth`, but deeply nested circular structures may still be slow to format.
- **Properties that raise:** `pformat()` silently skips instance fields backed by properties that raise exceptions on access.
- **`__slots__` without annotations:** `doc()` cannot show field types for `__slots__` classes without type annotations — `pformat()` can access their values via `__dict__` or `__slots__`.
- **Lazy module attributes:** Modules that define `__getattr__` for lazy loading will not have those lazily-loaded names included in `doc()` output.
- **Callable instance attributes:** Instance attributes that are functions/callables (e.g., `self.callback = fn`) are excluded from `pformat()` output.

## Design

- **Strings, not objects** — return values are ready for direct prompt injection
- **Visible by default, hide explicitly** — mirrors Python's `_` convention
- **`doc()` = contract, `pformat()` = state** — two orthogonal views
- **Zero dependencies** — stdlib only

### Why `show()` was removed

Early versions had a three-function model: `spec()` → `doc()` → `show()`, where `show(instance)` produced a flat multiline `field = value` dump of the current instance state. It was removed because it didn't carry its weight:

- **`doc(instance)` already does this.** Pass an instance to `doc()` and you get the full type structure with current values substituted in place of defaults. That covers the "what does this object hold right now" question with richer context (types, docstrings, method signatures).
- **`pformat(instance)` covers the compact case.** For a quick inline repr, `pformat()` produces `ClassName(field=value, ...)` and already respects `hidden` annotations. It's a Rich-compatible drop-in that LLMs already know from training data.
- **Redundancy creates confusion.** Having three ways to inspect an object forced users to ask "should I use `doc(agent)`, `show(agent)`, or `pformat(agent)`?" Removing `show()` leaves a clear choice: `doc()` for the API contract (including current values when passed an instance), `pformat()` for a compact value repr.
- **API surface matters for LLMs.** Every extra symbol in `doc(agentdoc)` is a symbol an agent-generated snippet might misuse. Fewer names means fewer opportunities for the LLM to reach for the wrong tool.

## Deferred / not yet implemented

- **`Environment` / `spec.capture()`** — snapshot a module's visible globals for agent context. Deferred; not currently used by nemo_oo_agents.

## License

MIT

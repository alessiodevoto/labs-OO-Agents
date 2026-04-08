# agentdoc Design Document

## Problem Statement

Python lacks a standard way to get a readable string representation of a **type's structure** (fields, methods, signatures). The `help()` function blocks on stdin, making it unsuitable for LLM agents. Additionally, `rich.pprint()` provides excellent value formatting but introduces a heavy dependency.

**agentdoc** provides lightweight pretty-printing with smart truncation and type/callable/module introspection optimized for LLM consumption.

## Quick Reference

| Function | Purpose | Use When |
|----------|---------|----------|
| `pformat(obj)` | Universal formatter → string | Need formatted string for any object |
| `doc(obj)` | Documentation view | "What can this object do?" (API/contract) |
| `pprint(obj)` | State/value view | "What values does this object have?" (debugging) |

```python
from agentdoc import doc, pprint, pformat

doc(MyClass)              # Type structure with methods and docstrings
doc(instance)             # Same as doc(type(instance)) - shows API
pprint(instance)          # Current field values: MyClass(field=value, ...)
pformat(data, max_length=10)  # Truncated repr for large data
```

**Key distinction**: `doc()` shows what an object *can do*, `pprint()` shows what it *currently contains*.

## Core API

### pformat(obj, ...) → str

Universal formatter. Returns a string representation with configurable truncation and conciseness.

```python
def pformat(
    obj,
    *,
    max_length: int | None = None,    # Max container elements
    max_string: int | None = None,    # Max string chars
    max_depth: int | None = None,     # Max nesting depth
    concise: bool = False,            # First-line docstrings only
    instance_mode: str = "repr",      # "repr" (values) or "type" (structure)
) -> str
```

**Behavior by input type:**

| Input | Output |
|-------|--------|
| Regular value (list, dict, str, ...) | Truncated repr |
| `type` (class) | Class syntax with fields, methods, docstrings |
| Function/method | Signature + docstring + return type |
| Module | Module docstring + public functions |
| Instance | Depends on `instance_mode` parameter |
| `TypeInfo`, `CallableInfo`, `ModuleInfo` | Formatted directly |

### doc(obj, concise=False) → str

Documentation view showing type structure. For instances, shows the **type's API** (not current values).

```python
def doc(obj, concise: bool = False) -> str:
    """Generate documentation for any object."""
    return pformat(obj, concise=concise, max_length=50, max_string=500, instance_mode="type")
```

### pprint(obj, ...) → None

Prints repr-style representation showing current state/values.

```python
def pprint(obj, max_length=None, max_string=None, max_depth=None, concise=False):
    """Pretty-print object with truncation."""
    print(pformat(obj, instance_mode="repr", ...))
```

### Conciseness Parameter

| Component | `concise=False` (default) | `concise=True` |
|-----------|---------------------------|----------------|
| Docstrings | Full text | First line only |
| Signatures | Full (always) | Full (always) |
| Fields | All with descriptions | All with descriptions |
| Referenced Types | Shown | Hidden |

### Referenced Types

When documenting a class, function, or method, `doc()` automatically discovers and includes all custom types used in the interface (parameters, return types, field types). This enables self-contained documentation for progressive disclosure.

```python
doc(DatabaseTool)  # Shows DatabaseTool AND QueryRequest, QueryResult
```

**Behavior:**
- **Full mode** (`concise=False`): Appends "## Referenced Types" section with each type shown concisely
- **Concise mode** (`concise=True`): Omits referenced types (prevents recursion)
- **Smart filtering**: Only custom types (Pydantic, dataclasses, etc.), excludes builtins
- **Deduplication**: Each type appears once even if used multiple times

## Output Examples

### Type Documentation

```python
# doc(User) or doc(User, concise=False)
class User(BaseModel):
    """
    A user in the system.

    Users can be created, saved, and greeted. Each user has
    a unique email address.
    """

    name: str  # User's full name
    email: str
    age: int = 0

    def greet(self, greeting: str = 'Hello') -> str:
        """
        Greet the user with a custom message.

        Args:
            greeting: The greeting prefix to use

        Returns:
            A personalized greeting string
        """
    async def save(self, path: str | None = None) -> bool:
        """Save the user to persistent storage."""
```

```python
# doc(User, concise=True) - first-line docstrings only
class User(BaseModel):
    """A user in the system."""

    name: str  # User's full name
    email: str
    age: int = 0

    def greet(self, greeting: str = 'Hello') -> str:
        """Greet the user with a custom message."""
    async def save(self, path: str | None = None) -> bool:
        """Save the user to persistent storage."""
```

### Instance State

```python
# pprint(user_instance)
User(name='Alice', email='a@b.com', age=28)
```

### Functions

```python
# doc(fetch_data)
async def fetch_data(url: str, timeout: float = 30.0) -> Response:
    """
    Fetch data from a remote URL.

    Args:
        url: The URL to fetch from
        timeout: Request timeout in seconds

    Returns:
        Response object with status and data
    """

## Referenced Types
class Response:
    """Response object with status and data."""

```

### Modules

```python
# doc(mymodule)
# mymodule

"""
Utilities for data processing.
"""

def load_csv(path: str) -> DataFrame:
    """Load a CSV file into a DataFrame."""

def save_json(data: dict, path: str) -> None:
    """Save a dictionary as JSON."""

```

## Golden Path: Recommended Patterns

These patterns maximize documentation clarity for agent code.

### 1. Use Annotated for Inline Documentation

String metadata in `Annotated` types is automatically extracted into docstrings:

```python
def insert(
    self,
    table: Annotated[str, "Target table name"],
    data: Annotated[dict, "Row data to insert"]
) -> Annotated[int, "ID of inserted row"]:
    """Insert a row into the database."""
    ...
```

Renders as:

```python
def insert(self, table: str, data: dict) -> int:
    """
    Insert a row into the database.

    Args:
        table: Target table name
        data: Row data to insert

    Returns:
        ID of inserted row
    """
```

### 2. Pydantic Models for Structured I/O

```python
class QueryRequest(BaseModel):
    query: Annotated[str, Field(description="SQL query to execute")]
    limit: Annotated[int, Field(ge=1, le=1000, description="Max rows")] = 100
    timeout: Annotated[float, Field(gt=0, description="Timeout in seconds")] = 30.0
```

### 3. Namespaced Tools with Typed State

Group related operations with visible state for debugging:

```python
class DatabaseTool:
    """Database operations with query tracking."""

    connection_string: str
    query_count: int = 0
    last_query: str | None = None

    def query(self, sql: Annotated[str, "SQL query"]) -> QueryResult:
        """Execute a database query."""
```

The `doc(DatabaseTool)` output includes referenced types automatically:

```python
class DatabaseTool:
    """Database operations with query tracking."""

    connection_string: str
    query_count: int = 0
    last_query: str | None = None

    def query(self, sql: str) -> QueryResult:
        """
        Execute a database query.

        Args:
            sql: SQL query
        """

## Referenced Types
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]
    count: int
```

Benefits:
- `doc(db_tool)` shows all related operations with referenced types
- `pprint(db_tool)` shows current state: `DatabaseTool(connection_string='...', query_count=5, last_query='SELECT ...')`

### 4. Progressive Disclosure

Structure enables natural drill-down with complete context at each level:

```python
doc(agent)              # Overview: namespaces and methods
doc(agent.db)           # All database operations + referenced types (QueryResult)
doc(agent.db.query)     # Specific method details + referenced types
```

Example of `doc(agent.db.query)`:

```python
def DatabaseTool.query(self, sql: str) -> QueryResult:
    """
    Execute a database query.

    Args:
        sql: SQL query
    """

## Referenced Types
class QueryResult(BaseModel):
    """Result of a database query."""

    rows: list[dict]
    count: int
```

Referenced types are automatically included, so each level provides self-contained documentation without additional queries.

### 5. Agent Structure Example

Definition:

```python
class DataProcessingAgent(Agent):
    """Agent for data processing with database and cache."""

    processed_requests: int = 0
    db: DatabaseTool
    cache: CacheTool

    async def fetch_user(
        self,
        user_id: Annotated[int, "User ID to fetch"]
    ) -> Annotated[UserProfile, "User data"]:
        """Fetch user data with caching."""
```

Output of `doc(DataProcessingAgent)`:

```python
class DataProcessingAgent:
    """Agent for data processing with database and cache."""

    processed_requests: int = 0
    db: DatabaseTool = DatabaseTool()
    cache: CacheTool = CacheTool()

    async def fetch_user(self, user_id: int) -> UserProfile:
        """
        Fetch user data with caching.

        Args:
            user_id: User ID to fetch

        Returns:
            User data
        """

## Referenced Types
class UserProfile(BaseModel):
    """User profile information."""

    id: int
    username: str
    email: str
```

**Key principles:**
- Type everything (state, parameters, returns)
- Use `Annotated` for inline descriptions
- Namespace related operations into tool classes
- Expose debugging state with type hints

---

## Data Model & Architecture

*This section is for implementers building or extending agentdoc.*

### Info Types

All introspection uses composable data structures:

```python
@dataclass
class FieldInfo:
    """Information about a class field/attribute."""
    name: str
    type: str               # e.g., "str", "list[int]"
    default: Any            # Default value, or ... if required
    description: str | None

@dataclass
class CallableInfo:
    """Information about a function or method."""
    name: str
    signature: str          # "(param: type, ...)" - parameters only
    return_type: str
    docstring: str | None
    is_async: bool

@dataclass
class TypeInfo:
    """Information about a class/type."""
    name: str
    base: str | None        # "BaseModel", "@dataclass", "Enum", etc.
    fields: list[FieldInfo]
    methods: list[CallableInfo]
    docstring: str | None

@dataclass
class ModuleInfo:
    """Information about a module."""
    name: str
    docstring: str | None
    functions: list[CallableInfo]
```

`CallableInfo` composes naturally into both `TypeInfo.methods` and `ModuleInfo.functions`.

### Extraction Flow

```python
def extract_info(obj) -> TypeInfo | CallableInfo | ModuleInfo:
    """Extract structured info from any Python object."""
    if inspect.ismodule(obj):
        return _extract_module_info(obj)
    elif inspect.isfunction(obj) or inspect.ismethod(obj):
        return _extract_callable_info(obj)
    elif isinstance(obj, type):
        return _extract_type_info(obj)
    else:
        return _extract_type_info(type(obj))
```

`_extract_type_info(cls)` handles: Pydantic models, dataclasses, NamedTuple, TypedDict, attrs, Enum, and plain classes.

### Formatting Pipeline

```
pformat(obj, ...)
       │
       ├── Info object? ──────────────────┐
       │   (TypeInfo, CallableInfo, etc.) │
       │                                  │
       ├── Python type/callable/module? ──┼──► extract_info(obj)
       │                                  │         │
       │                                  ▼         ▼
       │                          _format_type() / _format_callable() / _format_module()
       │
       └── Regular value? ────────────────► _format_value() (truncation)
```

## Extension Points

### Extraction Precedence

When extracting type information, agentdoc checks these sources in order:

1. **Registry** (`@register_type_info_extractor`) - Override types you don't control
2. **Protocol** (`__type_info__`, `__instance_values__`) - Class controls its own representation
3. **Automatic** - Introspection fallback (Pydantic, dataclass, attrs, etc.)

This ordering allows library authors to override even types that implement protocols.

### Protocol-Based Customization

For classes you control, implement these methods directly:

```python
class MyClass:
    @classmethod
    def __type_info__(cls) -> TypeInfo:
        """Override type extraction."""
        return TypeInfo(
            name="MyClass",
            base="CustomFramework",
            fields=[FieldInfo("id", "int", ..., "Unique identifier")],
            methods=[...],
            docstring="A custom framework class."
        )

    def __instance_values__(self) -> dict[str, Any]:
        """Override instance value extraction."""
        return {"id": self.id}  # Hide internal fields
```

### Registry-Based Customization

For third-party types you don't control:

```python
from agentdoc import register_type_info_extractor

@register_type_info_extractor(ThirdPartyClass)
def extract_third_party(obj) -> TypeInfo | tuple[TypeInfo, dict]:
    """Return TypeInfo for type, or (TypeInfo, values) for instance."""
    if isinstance(obj, type):
        return TypeInfo(...)
    else:
        return (TypeInfo(...), {"field": obj.field})
```

### Controlling Visibility

Filter out methods/fields by not including them in your `__type_info__` implementation:

```python
class MyAgent(Agent):
    # Framework attributes to hide
    _FRAMEWORK_ATTRS = {"runtime", "_llm", "context_spec"}

    @classmethod
    def __type_info__(cls) -> TypeInfo:
        info = extract_type_info(cls, _skip_protocol=True)  # Avoid recursion
        info.fields = [f for f in info.fields if f.name not in cls._FRAMEWORK_ATTRS]
        return info
```

---

## Design Rationale

### Why not Rich?

Rich is excellent but heavy. agentdoc provides a lightweight subset suitable for core library use.

### Why unified Info types?

1. **Composable**: `CallableInfo` reused across types and modules
2. **Single extraction point**: `extract_info()` handles everything
3. **Testable**: Extract and format independently

### Why external `pformat()` instead of methods?

Matches Python conventions (`pprint.pformat()`, `json.dumps()`, `repr()`). Info types stay as pure data containers.

### Why `concise` boolean?

Simpler than string enums, no typo risk, intuitive meaning.

### Why separate truncation from conciseness?

- **Truncation**: Data size ("show 50 of 10,000 items")
- **Conciseness**: Verbosity ("first-line docstrings only")

---

## Usage in nemo_oo_agents

agentdoc functions are injected into the LLM's execution namespace:

```python
exec_globals.update({
    "pprint": pprint,
    "doc": doc,
    "help": doc,  # Shadow built-in to prevent stdin blocking
})
```

LLM-generated code can then use `pprint(data)`, `doc(self)`, `doc(MyClass, concise=True)`, etc.

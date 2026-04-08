# Event Rendering Simplification: Remove RenderSpec

**Date:** 2026-01-28
**Status:** Proposal

## Problem

Current event rendering is verbose and indirect:

1. Each event defines a `render_spec()` method returning `RenderSpec(tag, attrs, content, role)`
2. Formatters iterate over `attrs` and `content` lists to pull values
3. Two concepts (attrs vs content) that don't add clarity
4. Logic for "show this field" scattered between field definition and `render_spec()`

## Proposed Solution

Use Pydantic's built-in conventions:
- **Private fields** (`_field`) are excluded from repr/serialization
- **Render the whole type** with `pformat(event)` - gives you type name + public fields
- **Class attributes** (`_tag`, `_role`) for metadata

The key insight: Pydantic's repr already does what we want. Private fields are excluded, public fields are shown with their values, and the class name is included.

### Before

```python
class ExecutePythonEvent(EventBase):
    type: Literal["execute_python"] = "execute_python"
    tool_call_id: str
    execution_count: int
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    value: Any = None
    explicit_return: bool = False
    status: Literal["complete", "error"]

    def render_spec(self) -> RenderSpec:
        content_fields = []
        if self.stdout:
            content_fields.append("stdout")
        if self.stderr:
            content_fields.append("stderr")
        if self.error:
            content_fields.append("error")
        if self.value is not None:
            content_fields.append("return_" if self.explicit_return else "value")
        if not content_fields:
            content_fields = ["stdout"]
        return RenderSpec(
            tag="execute_python",
            attrs=["tool_call_id", "execution_count", "status"],
            content=content_fields,
            role=Role.USER,
        )
```

### After

```python
class ExecutePythonEvent(EventBase):
    _tag = "execute_python"
    _role = Role.USER

    # Public fields - rendered (in display order)
    tool_call_id: str
    status: Literal["complete", "error"]
    execution_count: int
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    value: Any = None

    # Private fields - hidden from rendering
    _explicit_return: bool = PrivateAttr(default=False)
```

### Rendering

```python
from pprint import pformat

def render_event(event: EventBase, index: int) -> str:
    """Render event as tagged Pydantic repr."""
    tag = getattr(event, "_tag", event.__class__.__name__.lower())

    # pformat renders the Pydantic type with class name + public fields
    # Private fields are automatically excluded by Pydantic
    body = pformat(event)

    return f'<{tag} expr="self.history[{index}]">\n{body}\n</{tag}>'
```

That's it. Pydantic's `__repr__` already:
- Includes the class name (`ExecutePythonEvent(...)`)
- Shows public fields with values
- Excludes private fields (`_field`)
- Handles nested types correctly

### Output Format

```xml
<execute_python expr="self.history[5]">
ExecutePythonEvent(
  tool_call_id="call_abc123",
  status="complete",
  execution_count=3,
  stdout="""Hello world
Done processing""",
  value=42
)
</execute_python>
```

## Migration

### Changes to `context_blocks/models.py`

1. **Remove** `RenderSpec` class entirely
2. **Add** to `EventBase`:
   - `_tag: ClassVar[str]` - XML tag name
   - `_role: ClassVar[Role]` - message role (USER/ASSISTANT/TOOL)

### Changes to `agent006/events.py`

The `type` field is removed entirely - `_tag` serves as both the discriminator and the XML tag name.

```python
class TaskEvent(EventBase):
    """Task prompt event - added at start of generation."""
    _tag = "task"
    _role = Role.USER

    prompt: str | list


class MessageEvent(EventBase):
    """User-facing message from generated code via message()."""
    _tag = "message"
    _role = Role.ASSISTANT

    content: str | list


class ReasoningEvent(EventBase):
    """Chain-of-thought from generated code via reasoning()."""
    _tag = "reasoning"
    _role = Role.ASSISTANT

    content: str | list


class ErrorEvent(EventBase):
    """Error for LLM retry."""
    _tag = "error"
    _role = Role.USER

    content: str | list


class FeedbackEvent(EventBase):
    """Execution feedback when target method not yet defined."""
    _tag = "feedback"
    _role = Role.USER

    content: str | list


class AssistantEvent(EventBase):
    """LLM response (code for PURE_PYTHON, JSON for STRUCTURED_OUTPUT)."""
    _tag = "assistant_message"
    _role = Role.ASSISTANT

    content: str | list


class ExecutePythonEvent(EventBase):
    """Output from execute_python."""
    _tag = "execute_python"
    _role = Role.USER

    tool_call_id: str
    status: Literal["complete", "error"]
    execution_count: int
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    value: Any = None

    _explicit_return: bool = PrivateAttr(default=False)
```

### Changes to Formatters

Replace:
```python
spec = event.render_spec()
for attr in spec.attrs:
    value = getattr(event, attr)
```

With:
```python
tag = event._tag
role = event._role
for name, value in event.model_dump().items():
    if value not in (None, "", []):
        # render field
```

## Edge Cases

### 1. Explicit Return vs Expression Value

Current logic distinguishes `return x` from expression `x`. With `pformat()`:

- `value` is always shown when non-None
- `_explicit_return` is private (LLM doesn't see it)
- If needed, code can check `event._explicit_return` to distinguish

The LLM sees `value=42` regardless of whether it came from `return 42` or just `42`.

### 2. Empty Fields

Current: Conditionally include fields only if non-empty.

With `pformat()`: Empty strings still appear as `stdout=""`.

Options:
- **A)** Accept it - LLM can see fields exist even when empty
- **B)** Use `model_dump(exclude_defaults=True)` + custom formatter
- **C)** Override `__repr__` to skip empties

Recommendation: **A** - keep it simple. Showing `stdout=""` is harmless and the simplicity of pure `pformat()` is worth it.

### 3. Discriminator Field

The `type` field is removed. Use `_tag` as the discriminator:

```python
Event = Annotated[
    TaskEvent | MessageEvent | ...,
    Field(discriminator="_tag"),
]
```

Since `_tag` is a class variable (not an instance field), we may need to verify Pydantic supports this for discriminated unions. Alternative: keep a literal `_tag` field with `PrivateAttr`.

## Benefits

1. **Single source of truth** - field visibility is where field is defined (private = hidden)
2. **Minimal code** - renderer is literally `pformat(event)` wrapped in a tag
3. **Familiar pattern** - uses standard Pydantic private/public convention
4. **Debuggable** - `print(event)` shows exactly what LLM sees
5. **No abstraction** - no `RenderSpec`, no `attrs` vs `content`, no `render_spec()` methods
6. **Type name included** - LLM sees `ExecutePythonEvent(...)` not just field values

## Drawbacks

1. **Less flexible** - can't dynamically change visibility per-render
2. **Discriminator change** - need to verify Pydantic supports class var discriminators

## Implementation Steps

1. [ ] Update `EventBase` in context-blocks with `_tag`, `_role` class vars
2. [ ] Remove `RenderSpec` class
3. [ ] Remove `render_spec()` method from `EventBase`
4. [ ] Migrate each event class in `events.py`:
   - Add `_tag`, `_role`
   - Convert hidden fields to `PrivateAttr`
   - Add properties for programmatic access where needed
5. [ ] Update formatters to use new pattern
6. [ ] Update discriminated union to use `_tag`
7. [ ] Remove `type` field from all events
8. [ ] Update tests

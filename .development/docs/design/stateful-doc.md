# Proposal: Stateful self.doc()

## Problem

Currently `self.doc()` is stateless - each call requires passing parameters to control what's expanded:

```python
self.doc(expand=[self.items])  # Must pass expand every time
```

This means:
1. The LLM must remember and re-specify what to expand each time
2. There's no persistent "documentation view state"
3. Instructions are generic and don't reflect current state

## Proposed Solution

Make `self.doc` a stateful object with methods:
- `self.doc()` / `self.doc.show()` - render current state
- `self.doc.expand(...)` - expand items and return updated doc
- `self.doc.collapse(...)` - collapse items and return updated doc
- `self.doc.set(...)` - change detail levels

## Before: Current Output

```
# Self Documentation

## Methods
- async add_item(name: str) -> bool
    Add an item to inventory.
- get_total() -> int
    Get total item count.

## Variables
- self.cache = dict[2 items]
- self.items = list[3 items]

---
To see more detail: `self.doc(expand=[self.cache, re])`
To reconfigure: `context.update_block("python_tools", expr="self.doc(expand=[...])")`
```

## After: Proposed Output

```
# Self Documentation

## Current State
Expanded: (none)
Detail: methods=summary, variables=summary

## Methods
- async add_item(name: str) -> bool
    Add an item to inventory.
- get_total() -> int
    Get total item count.

## Variables
- self.cache = dict[2 items]  [self.doc.expand(self.cache)]
- self.items = list[3 items]  [self.doc.expand(self.items)]

---
## Modify Documentation
- Expand: `self.doc.expand(self.items)`
- Collapse: `self.doc.collapse(self.items)`
- Expand all: `self.doc.expand_all()`
- Collapse all: `self.doc.collapse_all()`
- Set detail: `self.doc.set(methods="full")` or `self.doc.set(variables="full")`
```

## After Expanding `self.items`

After calling `self.doc.expand(self.items)`:

```
# Self Documentation

## Current State
Expanded: items
Detail: methods=summary, variables=summary

## Methods
- async add_item(name: str) -> bool
    Add an item to inventory.
- get_total() -> int
    Get total item count.

## Variables
- self.cache = dict[2 items]  [self.doc.expand(self.cache)]
- self.items = ['apple', 'banana', 'cherry']  [self.doc.collapse(self.items)]

---
## Modify Documentation
- Expand: `self.doc.expand(self.cache)`
- Collapse: `self.doc.collapse(self.items)`
- Expand all: `self.doc.expand_all()`
- Collapse all: `self.doc.collapse_all()`
- Set detail: `self.doc.set(methods="full")` or `self.doc.set(variables="full")`
```

## API Design

### Doc Object

`self.doc` is a `Doc` object with state and methods:

```python
class Doc:
    """Stateful documentation viewer for an agent."""

    def __init__(self, agent: Agent):
        self._agent = agent
        self._expanded: set[str] = set()
        self._methods: Literal["summary", "full"] = "summary"
        self._variables: Literal["summary", "full"] = "summary"

    def __call__(self, **kwargs) -> str:
        """Shorthand for show(). Enables self.doc() syntax."""
        return self.show(**kwargs)

    def show(self, include_instructions: bool = True) -> str:
        """Render documentation with current state."""
        ...

    def expand(self, *items: Any) -> str:
        """Expand items and return updated doc."""
        for item in items:
            name = self._resolve_name(item)
            self._expanded.add(name)
        return self.show()

    def collapse(self, *items: Any) -> str:
        """Collapse items and return updated doc."""
        for item in items:
            name = self._resolve_name(item)
            self._expanded.discard(name)
        return self.show()

    def expand_all(self) -> str:
        """Expand all variables."""
        # Add all variable names to expanded set
        return self.show()

    def collapse_all(self) -> str:
        """Collapse all."""
        self._expanded.clear()
        return self.show()

    def set(self, methods: str = None, variables: str = None) -> str:
        """Set detail levels."""
        if methods:
            self._methods = methods
        if variables:
            self._variables = variables
        return self.show()
```

### Agent Integration

```python
class Agent:
    def __init__(self, ...):
        ...
        self.doc = Doc(self)
```

### Usage

```python
# Show current state
self.doc()
self.doc.show()

# Expand/collapse (returns updated doc)
self.doc.expand(self.items)
self.doc.expand(self.items, self.cache)  # Multiple
self.doc.expand("items")                  # By name
self.doc.collapse(self.items)
self.doc.collapse_all()

# Change detail level
self.doc.set(methods="full")
self.doc.set(variables="full")
```

### Backward Compatibility

The `__call__` method preserves `self.doc()` syntax:

```python
self.doc()                             # Works (calls show())
self.doc(include_instructions=False)   # Works (passes to show())
```

## Benefits

1. **LLM-friendly**: Progressive exploration without tracking state
2. **Self-documenting**: Output shows current state and how to modify it
3. **Ergonomic**: Single calls instead of rebuilding expand lists
4. **Discoverable**: Inline hints like `[self.doc.expand(self.items)]`
5. **Clean namespacing**: All doc methods on `self.doc`, not polluting Agent

## Implementation Notes

1. State lives on the `Doc` object (`self.doc`)
2. Each mutation method returns updated doc (no separate render call)
3. State persists across LLM turns within a session
4. State resets when agent is re-instantiated

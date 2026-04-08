# Design: History & Context Management System

**Date**: 2026-01-23
**Status**: Draft - For Review

---

## Executive Summary

The current history system has three distinct problems that require three separate solutions:

### Problem 1: Rendering is Broken

History messages render with invalid Python expressions:
```xml
<execute_python expr="self.history.events[159].content">  <!-- BROKEN -->
```
Events now expose flat fields (`content`, `arguments`, `tool_call_id`), and formatters should reference `self.history[n]`. Hardcoded event internals in formatters create tight coupling that breaks when the event shape changes.

**Solution**: Events specify what to render via `RenderSpec`. Formatters follow the spec.

### Problem 2: No Persistent Storage

History lives only in memory. Crash = lost context. No way to resume sessions or switch storage backends. In memory grows without bound.

**Solution**: Pluggable backends (InMemory, SQLite) behind a `HistoryBackend` protocol.

### Problem 3: No Context Budget Management

Long sessions exhaust the context window. No way to:
- Summarize old events to save space
- Truncate history while preserving critical events (task, recent)
- Allocate context between system prompt context-blocks and history

**Solution**: History management policies that operate on **views** of the event stream, with soft-delete (archive) and replacement tracking.

---

## Architecture: Three Orthogonal Concerns

In this proposal, none of these subsystems have to know about each other. Each problem maps to an independent component:

```
┌─────────────────────────────────────────────────────────────────────┐
│                            Agent                                    │
│                                                                     │
│   ┌─────────────────┐   ┌──────────────────┐   ┌─────────────────┐  │
│   │  context-blocks │   │     History      │   │     History     │  │
│   │   (rendering)   │   │   Management     │   │     Backend     │  │
│   │                 │   │   (policies)     │   │    (storage)    │  │
│   │  • RenderSpec   │   │  • Summarization │   │  • InMemory     │  │
│   │  • Formatters   │   │  • Truncation    │   │  • SQLite       │  │
│   │  • XML/Markdown │   │  • Budgeting     │   │  • (future DB)  │  │
│   └────────┬────────┘   └────────┬─────────┘   └────────┬────────┘  │
│            │                     │                      │           │
│            │ render(events)      │ manage(budget)       │ store()   │
│            ▼                     ▼                      ▼           │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                  HistoryManager (facade)                    │   │
│   │                                                             │   │
│   │  • __getitem__(n) → position in active view                 │   │
│   │  • get(id) → UUID lookup (all events)                       │   │
│   │  • add(event) → append to history                           │   │
│   │  • archive_range() / replace_range() → view ops             │   │
│   └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Separation of Concerns

| Component | Responsibility | Doesn't Care About |
|-----------|---------------|-------------------|
| **context-blocks** | Render events → XML/Markdown | Storage, management policies |
| **History Management** | Decide what to keep/summarize, budgets | How events are rendered or stored |
| **History Backend** | Store/retrieve events | Rendering or management logic |

### Coupling Points (Minimal)

1. **Event types**: New events (SummaryEvent, TruncationEvent) implement `render_spec()` interface
2. **Token counting**: Shared utility used by both rendering (for display) and management (for budgets)

### Independent Development

- **Phase 1-3** (rendering): Ship without touching management or backends
- **Phase 4** (backends): Add without changing rendering or management
- **Phase 5-6** (management): Add without redesigning rendering

---

## Proposed Design

### Core Principle: Events Own Their Rendering

Instead of formatters knowing event internals, events define a `RenderSpec` that tells formatters how to render them:

```python
@dataclass
class RenderSpec:
    tag: str                      # XML tag name
    attrs: list[str]              # Field names to render as XML attributes
    content: list[str]            # Field names to render as content (inline or nested)

class EventBase:
    def render_spec(self) -> RenderSpec:
        """Describe structure. Formatter handles expr generation."""
        return RenderSpec(
            tag=self.type,
            attrs=[],
            content=["content"],
        )
```

**Flat event structure**: Fields live directly on the event, not nested in `.data`:
```python
class ExecutePythonEvent(EventBase):
    type: Literal["execute_python"] = "execute_python"
    stdout: str          # NOT data.stdout
    stderr: str
    status: str
```

**Key design**: Events describe *what* they have, not *how* to reference it. The formatter constructs the `expr` attribute using position.

### Position-Based Access

Introduce `self.history[n]` syntax for compact, valid exprs:

```python
class HistoryManager:
    def __getitem__(self, key: int) -> EventBase:
        return self._events[key]  # Position in current view
```

- `self.history[3]` → 3rd event in current view (for exprs)
- `self.history.get("uuid")` → stable ID lookup (for programmatic access)

Position is a **render-time concept**, not stored on events. This supports future history manipulation (summarization, truncation) where positions change.

### Event Schema

Events have four key columns/attributes:

| Column | Type | Purpose | Assigned | Survives Archive |
|--------|------|---------|----------|------------------|
| `id` | UUID | Stable identity | At creation | Yes |
| `seq` | Integer | Storage ordering (NOT view order) | At insert (auto-increment) | Yes (gaps OK) |
| `timestamp` | datetime | When event occurred | At creation | Yes |
| `type` + fields | str + varies | Event discriminator + flat fields | At creation | Yes |
| `status` | Enum | Active/archived | Default 'active' | N/A |

```python
class EventBase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    seq: int = Field(default=-1)  # Assigned by backend on insert
    timestamp: datetime = Field(default_factory=datetime.now)
    status: Literal["active", "archived"] = "active"
    # type and data defined by subclasses
```

**Three distinct concepts:**

1. **`id` (UUID)** - Stable identity for lookups: `history.get("abc-123")`
2. **`seq` (integer)** - Storage order, survives deletions (gaps allowed): `ORDER BY seq`
3. **`position` (integer)** - Render-time index into active view: `self.history[3]`

Example with summarization:
```
Before:
  seq=1: TaskEvent       (active)     → position=0, self.history[0]
  seq=2: AssistantEvent  (active)     → position=1, self.history[1]
  seq=3: ToolCallEvent   (active)     → position=2, self.history[2]
  seq=4: ToolResultEvent (active)     → position=3, self.history[3]
  seq=5: AssistantEvent  (active)     → position=4, self.history[4]

After replace_range(summary, start_seq=2, end_seq=4):
  seq=1: TaskEvent       (active)     → position=0, self.history[0]
  seq=2: AssistantEvent  (archived)
  seq=3: ToolCallEvent   (archived)
  seq=4: ToolResultEvent (archived)
  seq=5: AssistantEvent  (active)     → position=2, self.history[2]
  seq=6: SummaryEvent    (active)     → position=1, self.history[1]  ← replaces seq 2-4
```

The summary appears at the position of the events it replaced (position=1), preserving conversation flow. It stores `replaces_start_seq=2` for view ordering and audit trails.

### Structured Event Rendering

Events render differently based on complexity:

**Single-field events** (task, user, error) - inline content, expr points to field:
```python
# RenderSpec for TaskEvent
RenderSpec(
    tag="task",
    attrs=[],
    content=["content"],  # Single content field = inline
)
```
```xml
<task expr="self.history[0].content" timestamp="2026-01-23T10:00:00" tokens="52" pct="0.3%">
Your task is to analyze the sales data...
</task>
```
→ `self.history[0].content` returns the string directly

**Multi-field events** (execute_python) - nested elements, expr points to parent:
```python
# RenderSpec for ExecutePythonEvent
RenderSpec(
    tag="execute_python",
    attrs=["tool_call_id"],  # Field names → XML attributes
    content=["status", "stdout", "stderr"],  # Field names → nested elements
)
```
```xml
<execute_python expr="self.history[3]" tool_call_id="tooluse_..." timestamp="..." tokens="245" pct="1.2%">
  <status>completed</status>
  <stdout>output text here</stdout>
</execute_python>
```
→ `self.history[3].status`, `self.history[3].stdout`

**Key invariant**: `expr` always evaluates to the content inside the tags.

Benefits:
- Simple events stay simple (no unnecessary nesting)
- Complex events are self-documenting (tag name = field name)
- Token efficient
- Consistent contract: expr = content

### HistoryManager (Facade)

The public API used by rendering, policies, and agent code:

```python
class HistoryManager:
    """Facade over storage backend. Provides view operations."""

    # === Read (active view) ===
    def __getitem__(self, position: int) -> EventBase:
        """Access by position in active view (for exprs)."""

    def active_events(self) -> list[EventBase]:
        """Return active events in view order.

        Safe to call even with DB backend - active view is bounded
        by context budget (policies keep it small enough to render).
        """

    def __len__(self) -> int:
        """Count of active events."""

    # === Read (all events) ===
    def get(self, event_id: str) -> EventBase | None:
        """Lookup by UUID (includes archived)."""

    # === Write ===
    def add(self, event: EventBase) -> int:
        """Append event, return seq."""

    # === View operations (range-based) ===
    def archive_range(self, start_seq: int, end_seq: int) -> int:
        """Archive contiguous range. Returns count."""

    def replace_range(self, new_event: EventBase, start_seq: int, end_seq: int) -> int:
        """Replace contiguous range with new event. Returns new seq."""

    # === Queries ===
    def get_replaced_by(self, event_id: str) -> list[EventBase]:
        """What events did this summary/truncation replace?"""
```

### History Backends

Storage layer behind the facade:

```python
class HistoryBackend(Protocol):
    # === Insert ===
    def append(self, event: EventBase) -> int:
        """Insert event, assign seq, return seq."""

    # === Lookup ===
    def get(self, event_id: str) -> EventBase | None:
        """Lookup by UUID (all events, including archived)."""

    def get_by_seq(self, seq: int) -> EventBase | None:
        """Lookup by sequence number."""

    # === Range operations (contiguous by seq) ===
    def archive_range(self, start_seq: int, end_seq: int) -> int:
        """Archive events where start_seq <= seq <= end_seq.
        Returns count archived."""

    def replace_range(self, new_event: EventBase, start_seq: int, end_seq: int) -> int:
        """Replace contiguous range with new event.
        1. Archive events in range
        2. Insert new_event
        3. Record replacements in junction table
        Returns new event's seq."""

    # === List ===
    def list_active(self, limit: int | None = None) -> list[EventBase]:
        """List active events ordered by seq."""

    def list_all(self, limit: int | None = None) -> list[EventBase]:
        """List all events (including archived) ordered by seq."""

    # === Queries ===
    def get_replaced_by(self, event_id: str) -> list[EventBase]:
        """What events did this summary/truncation replace?"""

    def count_active(self) -> int
    def count_all(self) -> int
    def clear(self) -> None
```

**Why ranges?** A summary/truncation event represents "what happened between seq X and seq Y" - that's inherently contiguous. You can't summarize scattered events with a single marker.

| Backend | Use Case | Persistence |
|---------|----------|-------------|
| **InMemoryBackend** | Default, fast, testing | None |
| **FilesystemBackend** | Simple persistence, human-readable | JSONL file |
| **SQLiteBackend** | Long sessions, crash recovery, queries | File-based |

### Archive Constraints

**Problem**: Not all contiguous ranges are valid for archiving. Consider tool calls:

```
seq=1: UserEvent("analyze this data")
seq=2: AssistantEvent("I'll analyze it")
seq=3: ToolCallEvent(id="tc_123", name="execute_python", args={...})
seq=4: ToolResultEvent(tool_call_id="tc_123", content="Done")
seq=5: AssistantEvent("Analysis complete")
```

If we archive `seq=3` (the tool call) but not `seq=4` (its result), we create an orphaned result that references a non-existent call. The LLM would see a tool result without the tool call that produced it - this violates the conversation structure that LLMs expect.

**Solution**: Pluggable constraints that validate archive operations:

```python
class ArchiveConstraint(Protocol):
    """Validates that an archive range is semantically valid."""

    def validate(self, events: list[EventBase], start_seq: int, end_seq: int) -> bool:
        """Return True if archiving this range is valid."""
        ...

    def find_valid_boundary(self, events: list[EventBase], target_seq: int) -> int:
        """Find nearest valid boundary at or after target_seq.

        Used by policies to find valid archive points when they
        want to archive "approximately up to here."
        """
        ...
```

**Why pluggable?** We don't know all the rules yet. Different event types may have different constraints. New constraints can be added without modifying the core system.

**Built-in constraint: Tool Call Pairing**

```python
class ToolCallPairingConstraint:
    """Tool calls and their results must be archived together."""

    def validate(self, events: list[EventBase], start_seq: int, end_seq: int) -> bool:
        # Build set of tool_call IDs in range
        call_ids = {e.id for e in events if e.type == "tool_call"
                    and start_seq <= e.seq <= end_seq}
        result_ids = {e.tool_call_id for e in events if e.type == "tool_result"
                      and start_seq <= e.seq <= end_seq}

        # Every call in range must have its result in range (or result doesn't exist)
        for e in events:
            if e.type == "tool_call" and start_seq <= e.seq <= end_seq:
                # Check if result exists outside range
                for r in events:
                    if r.type == "tool_result" and r.tool_call_id == e.id:
                        if not (start_seq <= r.seq <= end_seq):
                            return False  # Result exists but outside range

            if e.type == "tool_result" and start_seq <= e.seq <= end_seq:
                # Check if call exists outside range
                if e.tool_call_id not in call_ids:
                    for c in events:
                        if c.type == "tool_call" and c.id == e.tool_call_id:
                            if not (start_seq <= c.seq <= end_seq):
                                return False  # Call exists but outside range

        return True

    def find_valid_boundary(self, events: list[EventBase], target_seq: int) -> int:
        """Extend boundary to include any pending tool calls/results."""
        boundary = target_seq
        # If there's a tool call at boundary, include its result
        # If there's a tool result at boundary, include its call
        # ... implementation details ...
        return boundary
```

**HistoryManager uses constraints**:

```python
class HistoryManager:
    def __init__(self, backend: HistoryBackend,
                 constraints: list[ArchiveConstraint] | None = None):
        self._backend = backend
        self._constraints = constraints or [ToolCallPairingConstraint()]

    def archive_range(self, start_seq: int, end_seq: int) -> int:
        events = self.list_all()  # Need all events to check references
        for constraint in self._constraints:
            if not constraint.validate(events, start_seq, end_seq):
                raise InvalidArchiveRange(
                    f"Range [{start_seq}, {end_seq}] violates {constraint.__class__.__name__}"
                )
        return self._backend.archive_range(start_seq, end_seq)
```

**Future constraints** (examples of rules we might discover):
- Agent call pairing (nested agent calls must include their completions)
- Transaction boundaries (don't split related operations)
- Checkpoint events (some events mark "safe" archive points)

### History Management Policies

Policies operate **only via the public HistoryManager API** - no special access to internals:

```python
class HistoryPolicy(Protocol):
    """Uses only public HistoryManager API."""

    def manage(self, history: HistoryManager, budget_tokens: int) -> None:
        # Available operations:
        # - history.list() → active events ordered by seq
        # - history[n] → position-based access
        # - history.get(id) → UUID lookup
        # - history.archive_range(start_seq, end_seq) → soft-delete range
        # - history.replace_range(event, start_seq, end_seq) → replace range
        # - history.count() → active event count
        ...
```

This ensures policies are:
- Decoupled from backend implementation
- Testable in isolation
- Potentially a separate package

**Preserved events** (never removed/summarized):
- `TaskEvent` - the original task prompt
- Prefill events - input inspection
- Most recent N events (configurable)

#### Summarization Policy

Replaces old events with an LLM-generated summary:

```python
class SummarizationPolicy:
    def manage(self, history: HistoryManager, budget_tokens: int) -> None:
        events = history.list()
        # 1. Identify contiguous range to summarize (oldest, non-preserved)
        start_seq, end_seq = self._find_summarizable_range(events, budget_tokens)
        # 2. Call LLM to generate summary of events in range
        summary_text = self._summarize(events, start_seq, end_seq)
        # 3. Replace range with SummaryEvent
        summary = SummaryEvent(summary_text=summary_text, original_count=end_seq - start_seq + 1)
        history.replace_range(summary, start_seq, end_seq)
```

#### Truncation Policy

Removes old events with a marker:

```python
class TruncationPolicy:
    def manage(self, history: HistoryManager, budget_tokens: int) -> None:
        events = history.list()
        # 1. Identify contiguous range to truncate (oldest, non-preserved)
        start_seq, end_seq = self._find_truncatable_range(events, budget_tokens)
        # 2. Replace range with TruncationEvent
        marker = TruncationEvent(removed_count=end_seq - start_seq + 1)
        history.replace_range(marker, start_seq, end_seq)
```

#### New Event Types (flat structure)

```python
class SummaryEvent(EventBase):
    """Replaces summarized events."""
    type: Literal["summary"] = "summary"
    summary_text: str
    original_count: int

class TruncationEvent(EventBase):
    """Marker for truncated events."""
    type: Literal["truncation"] = "truncation"
    removed_count: int
```

### Context Budgeting

Explicit allocation of context window:

```python
@dataclass
class ContextBudget:
    context_blocks_pct: float = 0.3   # 30% for system prompt
    history_pct: float = 0.6          # 60% for conversation
    reserve_pct: float = 0.1          # 10% for response
```

**Enforcement**:
- History: Policy auto-manages within budget before each `generate()` call
- Context blocks: Throw `ContextBudgetExceeded` if block would exceed budget

**Visibility**: All rendered content shows token usage:
```xml
<persona expr="self.persona" tokens="1200" pct="6%">
```

---

## Implementation Phases

### Phase 1: Fix Expr Bug + Add `__getitem__`
- Add `HistoryManager.__getitem__`
- Fix expr paths in formatters
- **Deliverable**: `self.history[n].content` works

### Phase 2: RenderSpec System
- Add `RenderSpec` dataclass
- Events implement `render_spec()`
- Formatters use render_spec to generate structured output
- **Deliverable**: Structured XML with child elements (tag name = field name)

### Phase 3: Flatten Event Structure
- Move fields from nested `data` to directly on event
- Remove `data` wrapper models
- **Deliverable**: `self.history[n].stdout` works (no `.data` indirection)

### Phase 4: History Backend Abstraction
- Define `HistoryBackend` protocol
- Implement `InMemoryBackend`
- Implement `SQLiteBackend`
- **Deliverable**: Pluggable storage

### Phase 5: History Management Policies
- Define policy protocol
- Implement `SummarizationPolicy`
- Implement `TruncationPolicy`
- Add `SummaryEvent`, `TruncationEvent`
- **Deliverable**: Auto-managed history

### Phase 6: Context Budgeting
- Add `ContextBudget` class
- Enforce block budgets
- Add token/pct to rendered output
- **Deliverable**: Full budget system

### Phase 7: Cleanup
- Remove `seq_id` from EventBase
- Remove deprecated code

---

## Implementation Plan (Phases 1-3)

# History Context Management System (Phases 1-3) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver phases 1–3 of history rendering: valid exprs, RenderSpec-driven rendering, and flattened event fields.

**Architecture:** Add `HistoryManager.__getitem__` for position-based access, fix formatter expr paths, then introduce `RenderSpec` to decouple render structure from formatter internals. Finally, flatten event models (remove `.data` wrappers) and update rendering + tests to use direct fields.

**Tech Stack:** Python 3.12, Pydantic, `context_blocks`, `agent006` runtime, pytest

---

### Task 0: Prep worktree and baseline

**Files:**
- Modify: none
- Test: `packages/context-blocks/tests/test_formatters.py`, `tests/test_history_manager.py`

**Step 1: Create a worktree**
Run: `git worktree add ../agent006-history-phases-1-3 -b history-phases-1-3`

**Step 2: Activate venv**
Run: `source .venv/bin/activate`

**Step 3: Run baseline tests**
Run: `pytest packages/context-blocks/tests/test_formatters.py tests/test_history_manager.py -v`
Expected: PASS (or note any pre-existing failures)

---

### Task 1: Phase 1 — Add `HistoryManager.__getitem__`

**Files:**
- Modify: `src/agent006/runtime/history.py`
- Test: `tests/test_history_manager.py`

**Step 1: Write the failing test**
Add to `tests/test_history_manager.py`:
```python
def test_getitem_by_position():
    hm = HistoryManager()
    hm.add(TaskEvent(content="First"))
    hm.add(TaskEvent(content="Second"))

    assert hm[0].content == "First"
    assert hm[1].content == "Second"
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_history_manager.py::test_getitem_by_position -v`
Expected: FAIL with `TypeError: 'HistoryManager' object is not subscriptable`

**Step 3: Write minimal implementation**
Update `src/agent006/runtime/history.py`:
```python
def __getitem__(self, key: int) -> EventBase:
    return self._events[key]
```

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_history_manager.py::test_getitem_by_position -v`
Expected: PASS

**Step 5: Commit**
```bash
git add tests/test_history_manager.py src/agent006/runtime/history.py
git commit -m "feat: add HistoryManager indexing"
```

---

### Task 2: Phase 1 — Fix formatter expr paths

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/formatter.py`
- Test: `packages/context-blocks/tests/test_formatters.py`

**Step 1: Update failing tests (expr paths)**
Update expected exprs in `packages/context-blocks/tests/test_formatters.py`:
```python
assert '<user_message expr="self.history[5].content">' in result
assert '<assistant_message expr="self.history[10].content">' in result
assert '<tool_call expr="self.history[15].arguments"' in result
assert '<tool_result expr="self.history[16].content">' in result
```

**Step 2: Run test to verify it fails**
Run: `pytest packages/context-blocks/tests/test_formatters.py::TestXMLBlockFormatterEventFormatting -v`
Expected: FAIL with mismatched expr strings

**Step 3: Update formatter exprs**
Update `XMLBlockFormatter`/`MarkdownBlockFormatter` in `packages/context-blocks/src/context_blocks/formatter.py`:
```python
expr = f"self.history[{seq_id}].content"
# tool_call
expr = f"self.history[{seq_id}].arguments"
# tool_result
expr = f"self.history[{seq_id}].content"
```

**Step 4: Run test to verify it passes**
Run: `pytest packages/context-blocks/tests/test_formatters.py::TestXMLBlockFormatterEventFormatting -v`
Expected: PASS

**Step 5: Commit**
```bash
git add packages/context-blocks/src/context_blocks/formatter.py \
  packages/context-blocks/tests/test_formatters.py
git commit -m "fix: align formatter exprs with data fields"
```

---

### Task 3: Phase 2 — Add `RenderSpec` model + tests

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/models.py`
- Modify: `packages/context-blocks/src/context_blocks/__init__.py`
- Create: `packages/context-blocks/tests/test_render_spec.py`

**Step 1: Write failing tests**
Create `packages/context-blocks/tests/test_render_spec.py`:
```python
from context_blocks.models import RenderSpec

def test_render_spec_fields():
    spec = RenderSpec(tag="task", attrs=[], content=["content"])
    assert spec.tag == "task"
    assert spec.attrs == []
    assert spec.content == ["content"]
```

**Step 2: Run test to verify it fails**
Run: `pytest packages/context-blocks/tests/test_render_spec.py -v`
Expected: FAIL with `ImportError: cannot import name 'RenderSpec'`

**Step 3: Write minimal implementation**
Add to `packages/context-blocks/src/context_blocks/models.py`:
```python
from dataclasses import dataclass

@dataclass
class RenderSpec:
    tag: str
    attrs: list[str]
    content: list[str]
```
Export from `packages/context-blocks/src/context_blocks/__init__.py`.

**Step 4: Run test to verify it passes**
Run: `pytest packages/context-blocks/tests/test_render_spec.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add packages/context-blocks/src/context_blocks/models.py \
  packages/context-blocks/src/context_blocks/__init__.py \
  packages/context-blocks/tests/test_render_spec.py
git commit -m "feat: add RenderSpec model"
```

---

### Task 4: Phase 2 — Add `render_spec()` to context-blocks events

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/events.py`
- Test: `packages/context-blocks/tests/test_events.py`

**Step 1: Write failing tests**
Add to `packages/context-blocks/tests/test_events.py`:
```python
from context_blocks.events import UserEvent

def test_default_render_spec():
    event = UserEvent(content="hi")
    spec = event.render_spec()
    assert spec.tag == "user"
    assert spec.attrs == []
    assert spec.content == ["content"]
```

**Step 2: Run test to verify it fails**
Run: `pytest packages/context-blocks/tests/test_events.py::test_default_render_spec -v`
Expected: FAIL with `AttributeError: 'UserEvent' object has no attribute 'render_spec'`

**Step 3: Write minimal implementation**
Update `packages/context-blocks/src/context_blocks/events.py`:
```python
from context_blocks.models import RenderSpec

class EventBase(BaseModel):
    ...
    def render_spec(self) -> RenderSpec:
        return RenderSpec(tag=self.type, attrs=[], content=["content"])
```
Override in `ToolCallEvent`/`ToolResultEvent` as needed:
```python
class ToolCallEvent(EventBase):
    def render_spec(self) -> RenderSpec:
        return RenderSpec(tag="tool_call", attrs=["name"], content=["arguments"])
```

**Step 4: Run test to verify it passes**
Run: `pytest packages/context-blocks/tests/test_events.py::test_default_render_spec -v`
Expected: PASS

**Step 5: Commit**
```bash
git add packages/context-blocks/src/context_blocks/events.py \
  packages/context-blocks/tests/test_events.py
git commit -m "feat: add render_spec to context-blocks events"
```

---

### Task 5: Phase 2 — Use `RenderSpec` in formatters

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/formatter.py`
- Test: `packages/context-blocks/tests/test_formatters.py`

**Step 1: Update tests for RenderSpec-based output**
Update `packages/context-blocks/tests/test_formatters.py` to expect:
```python
assert '<task expr="self.history[0].content">' in result
```
Add a multi-field case (e.g., `ToolCallEvent`) asserting nested content if `content` has multiple fields.

**Step 2: Run test to verify it fails**
Run: `pytest packages/context-blocks/tests/test_formatters.py::TestXMLBlockFormatterEventFormatting -v`
Expected: FAIL with mismatched structure

**Step 3: Implement RenderSpec-driven formatting**
Update `XMLBlockFormatter.format_event` (and markdown equivalent) to:
```python
spec = event.render_spec()
expr = (
    f"self.history[{seq_id}].{spec.content[0]}"
    if len(spec.content) == 1
    else f"self.history[{seq_id}]"
)
# Build attrs from spec.attrs (support dotted paths for Phase 2)
# Build content: inline for single field, nested tags for multi-field
```

**Step 4: Run test to verify it passes**
Run: `pytest packages/context-blocks/tests/test_formatters.py::TestXMLBlockFormatterEventFormatting -v`
Expected: PASS

**Step 5: Commit**
```bash
git add packages/context-blocks/src/context_blocks/formatter.py \
  packages/context-blocks/tests/test_formatters.py
git commit -m "feat: render events via RenderSpec"
```

---

### Task 6: Phase 2 — Add `render_spec()` to agent006 events

**Files:**
- Modify: `src/agent006/events.py`
- Test: `tests/test_events.py`

**Step 1: Write failing tests**
Add to `tests/test_events.py`:
```python
from agent006.events import TaskEvent

def test_agent_event_render_spec():
    event = TaskEvent(content="do it")
    spec = event.render_spec()
    assert spec.tag == "task"
    assert spec.content == ["content"]
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_events.py::test_agent_event_render_spec -v`
Expected: FAIL with `AttributeError: 'TaskEvent' object has no attribute 'render_spec'`

**Step 3: Write minimal implementation**
In `src/agent006/events.py`, implement `render_spec()` for each event type, mirroring context-blocks:
```python
def render_spec(self) -> RenderSpec:
    return RenderSpec(tag=self.type, attrs=[], content=["content"])
```
Add custom specs for `ExecutePythonEvent` (include `tool_call_id` as attr and fields like `status`, `content`).

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_events.py::test_agent_event_render_spec -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/agent006/events.py tests/test_events.py
git commit -m "feat: add render_spec to agent006 events"
```

---

### Task 7: Phase 3 — Flatten context-blocks events (remove `.data`)

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/events.py`
- Modify: `packages/context-blocks/src/context_blocks/renderer.py`
- Modify: `packages/context-blocks/src/context_blocks/formatter.py`
- Modify: `packages/context-blocks/src/context_blocks/__init__.py`
- Test: `packages/context-blocks/tests/test_events.py`, `packages/context-blocks/tests/test_formatters.py`

**Step 1: Update tests to use flat fields**
Change `ContentData(content="x")` → `UserEvent(content="x")` and update assertions to `event.content`.

**Step 2: Run tests to verify they fail**
Run: `pytest packages/context-blocks/tests/test_events.py packages/context-blocks/tests/test_formatters.py -v`
Expected: FAIL with missing `data` attribute

**Step 3: Flatten event models**
Update `packages/context-blocks/src/context_blocks/events.py`:
```python
class UserEvent(EventBase):
    type: Literal["user"] = "user"
    content: str | list
```
Remove `ContentData`, `ToolCallData`, `ToolResultData`, and update usages accordingly.
Update `packages/context-blocks/src/context_blocks/__init__.py` exports to remove data models.
Update `render_spec()` to use direct fields (e.g., `content=["content"]`, `attrs=["name"]`).

**Step 4: Update renderer/formatter for flat fields**
Replace `event.data.content` with `event.content`, and adjust tool call/result handling.

**Step 5: Run tests to verify they pass**
Run: `pytest packages/context-blocks/tests/test_events.py packages/context-blocks/tests/test_formatters.py -v`
Expected: PASS

**Step 6: Commit**
```bash
git add packages/context-blocks/src/context_blocks/events.py \
  packages/context-blocks/src/context_blocks/renderer.py \
  packages/context-blocks/src/context_blocks/formatter.py \
  packages/context-blocks/tests/test_events.py \
  packages/context-blocks/tests/test_formatters.py
git commit -m "refactor: flatten context-blocks event fields"
```

---

### Task 8: Phase 3 — Flatten agent006 events + history usage

**Files:**
- Modify: `src/agent006/events.py`
- Modify: `src/agent006/runtime/history.py`
- Modify: `src/agent006/runtime/out_accessor.py`
- Test: `tests/test_history_manager.py`, `tests/test_events.py`, `tests/runtime/test_out_accessor.py`

**Step 1: Update tests to use flat fields**
Replace `.data.content` with `.content`, and update event construction to pass `content=` directly.

**Step 2: Run tests to verify they fail**
Run: `pytest tests/test_history_manager.py tests/test_events.py tests/runtime/test_out_accessor.py -v`
Expected: FAIL with `AttributeError: '...Event' object has no attribute 'data'`

**Step 3: Flatten agent006 event models**
Update `src/agent006/events.py` to mirror context-blocks (flat fields).
Update `render_spec()` to use direct fields (no `data.` prefixes).

**Step 4: Update HistoryManager and helpers**
Update history search and recent logic in `src/agent006/runtime/history.py` to use flat fields
(e.g., `event.content`, `event.tool_call_id`, `event.arguments`).

**Step 5: Run tests to verify they pass**
Run: `pytest tests/test_history_manager.py tests/test_events.py tests/runtime/test_out_accessor.py -v`
Expected: PASS

**Step 6: Commit**
```bash
git add src/agent006/events.py src/agent006/runtime/history.py \
  src/agent006/runtime/out_accessor.py tests/test_history_manager.py \
  tests/test_events.py tests/runtime/test_out_accessor.py
git commit -m "refactor: flatten agent006 event fields"
```

---

### Task 9: Phase 3 — Update remaining references and run focused suite

**Files:**
- Modify: `tests/` (any remaining `.data.` references)
- Modify: `src/agent006/` and `packages/context-blocks/` usages

**Step 1: Update remaining references**
Search for `.data.` in affected packages and update to flat fields.

**Step 2: Run focused test suite**
Run: `pytest tests/runtime tests/strategies tests/integration -v`
Expected: PASS

**Step 3: Commit**
```bash
git add src/agent006 packages/context-blocks tests
git commit -m "chore: update remaining event field references"
```

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Event identity | UUID (`id`) | Stable across archiving, globally unique |
| Event ordering | Integer (`seq`) | Fast ORDER BY, gaps OK after archive |
| Event structure | Flat (no `.data` nesting) | Simpler API: `event.stdout` not `event.data.stdout` |
| Expr access | `position` (render-time) | Index into active view, not stored |
| Archive vs delete | Soft delete (`status`) | Full audit trail, replacement tracking |
| View operations | Range-based (`start_seq`, `end_seq`) | Summary/truncation represents contiguous history |
| Child element exprs | None (tag name = field name) | Obvious, token efficient, no redundancy |
| Token counting | chars/4 initially | Fast, no deps. Pluggable for accuracy later. |
| Policy triggers | Auto before generate() + manual | Flexible, predictable |
| Policy API surface | Public HistoryManager API only | Decoupled, testable, backend-agnostic |
| Preserved events | Task + Prefill + Recent N | Critical context must survive |
| Archive constraints | Pluggable protocol | Rules not yet known; tool pairing is one example |

---

## Open Questions

1. **Summarization LLM**: Use same model as agent, or cheaper model for summaries?
2. **Summary granularity**: Summarize N events at a time, or batch all eligible?
3. **Backend switching**: Allow switching backends mid-session?

### Position Stability After Summarization

**The Problem**: When events are summarized/truncated, LLM-generated code that references old positions may break.

```
Before summarization:
  position 0: TaskEvent
  position 1: AssistantEvent
  position 2: ToolCallEvent      ← LLM writes: result = history[2].arguments
  position 3: ToolResultEvent
  position 4: AssistantEvent

After summarizing positions 1-3:
  position 0: TaskEvent
  position 1: SummaryEvent       ← history[2] is now this (doesn't have .arguments!)
  position 2: AssistantEvent     ← or this?
```

If code references `history[2].arguments` and then summarization happens, the reference breaks.

**Option A: Multiple Positions → Same Summary**

Make `history[1]`, `history[2]`, `history[3]` all return the SummaryEvent that replaced them:

```python
history[0]  # → TaskEvent
history[1]  # → SummaryEvent
history[2]  # → SummaryEvent (same object)
history[3]  # → SummaryEvent (same object)
history[4]  # → AssistantEvent
```

*Problems*:
- `len(history)` is misleading (reports 5, but only 3 distinct events)
- Iteration visits the same SummaryEvent multiple times
- Field access still fails: `history[2].arguments` errors (SummaryEvent has no `.arguments`)
- The underlying problem (code referencing non-existent fields) isn't solved

**Option B: Use `seq` for Stable Access**

Remove position-based indexing entirely. Make `history[n]` mean "event at seq=n":

```python
history[2]      # → Always returns event at seq=2 (even if archived)
history.get(id) # → UUID lookup
```

No separate `history.seq()` method needed - `__getitem__` *is* the seq access.

*Problems*:
- Summary event gets assigned seq=6 (next in sequence), but needs to appear at position 1 in the rendered view
- View ordering becomes: `ORDER BY COALESCE(replaces_start_seq, seq)` - complex
- Rendered exprs would show non-consecutive seqs: `history[1]`, `history[6]`, `history[5]`
- LLMs may be confused by gaps and out-of-order seq numbers
- No way to iterate "current view" in order (need `history.active_events()` for that)

**Option C: Accept Position Drift**

Accept that positions shift after summarization. Code that ran before summarization worked at that time; after summarization, the conversation has moved on.

*Problems*:
- Old code references in conversation history become misleading
- Debugging becomes harder (can't trust position numbers in logs)

**To Resolve**: Need to decide whether stable references matter enough to add complexity. May depend on how often LLM-generated code references historical positions vs just using them inline.

---

## Alternatives Considered

### A. Just Fix the Bug
Change `.data.content` to `.content` in formatters.

**Rejected**: Keeps tight coupling, doesn't address deeper issues.

### B. Remove Exprs Entirely
Just render content without expr metadata.

**Rejected**: Loses the "how to reference this" feature that LLMs find useful.

### C. String Templates in Events
Events return `"self.history[{seq_id}].content"` template.

**Rejected**: Awkward API, still requires seq_id.

---

## Migration Path

- Phase 1-3 can be done incrementally, each phase is independently valuable
- Phases 4-6 require more planning but can wait until needed
- Existing code continues to work throughout migration
- No breaking changes to public API

---

## Success Criteria

1. All exprs are valid, executable Python
2. Long sessions don't exhaust context window
3. History survives crashes (with SQLite backend)
4. Budget allocation is visible and enforced
5. Tests pass at each phase

---

## Appendix: Token Estimation

Using chars/4 approximation:
- 128K context window ≈ 512K characters
- 30% for blocks = 153K chars
- 60% for history = 307K chars
- 10% reserve = 51K chars

A typical event (500 chars) uses ~0.1% of history budget. With summarization, sessions can run indefinitely.

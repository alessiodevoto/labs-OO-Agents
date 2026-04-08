# History Context Phases 1-3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement phases 1–3 for history rendering: valid exprs, RenderSpec-driven rendering, and flattened event fields.

**Architecture:** Add position-based indexing to `HistoryManager`, repair formatter expr paths, introduce `RenderSpec` to separate event structure from formatting, and flatten event fields across `context_blocks` and `nemo_oo_agents`.

**Tech Stack:** Python 3.12, Pydantic, `context_blocks`, `nemo_oo_agents` runtime, pytest

---

### Task 0: Create isolated worktree and baseline

**Files:**
- Modify: none
- Test: `packages/context-blocks/tests/test_formatters.py`, `tests/test_history_manager.py`

**Step 1: Create a worktree**
Run: `git worktree add ../nemo_oo_agents-history-phases-1-3 -b history-phases-1-3`

**Step 2: Activate venv**
Run: `source .venv/bin/activate`

**Step 3: Run baseline tests**
Run: `pytest packages/context-blocks/tests/test_formatters.py tests/test_history_manager.py -v`
Expected: PASS (or note pre-existing failures)

---

### Task 1: Phase 1 — Add `HistoryManager.__getitem__`

**Files:**
- Modify: `src/nemo_oo_agents/runtime/history.py`
- Test: `tests/test_history_manager.py`

**Step 1: Write the failing test**
Add:
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
Add:
```python
def __getitem__(self, key: int) -> EventBase:
    return self._events[key]
```

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_history_manager.py::test_getitem_by_position -v`
Expected: PASS

**Step 5: Commit**
```bash
git add tests/test_history_manager.py src/nemo_oo_agents/runtime/history.py
git commit -m "feat: add HistoryManager indexing"
```

---

### Task 2: Phase 1 — Fix formatter expr paths

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/formatter.py`
- Test: `packages/context-blocks/tests/test_formatters.py`

**Step 1: Update failing tests**
Change expected exprs to use flat fields:
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
Update exprs in `XMLBlockFormatter`/`MarkdownBlockFormatter`:
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
Create:
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
Add:
```python
from dataclasses import dataclass

@dataclass
class RenderSpec:
    tag: str
    attrs: list[str]
    content: list[str]
```
Export from `context_blocks/__init__.py`.

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
Add:
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
Add to `EventBase`:
```python
def render_spec(self) -> RenderSpec:
    return RenderSpec(tag=self.type, attrs=[], content=["content"])
```
Override for tool events:
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

**Step 1: Update tests for RenderSpec output**
Add an event with multiple content fields and assert nested tags.

**Step 2: Run test to verify it fails**
Run: `pytest packages/context-blocks/tests/test_formatters.py::TestXMLBlockFormatterEventFormatting -v`
Expected: FAIL with mismatched structure

**Step 3: Implement RenderSpec-based formatting**
Update `format_event` to:
```python
spec = event.render_spec()
expr = (
    f"self.history[{seq_id}].{spec.content[0]}"
    if len(spec.content) == 1
    else f"self.history[{seq_id}]"
)
# Build attrs from spec.attrs (support dotted paths for Phase 2)
# Inline content for single field, nested tags for multi-field
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

### Task 6: Phase 2 — Add `render_spec()` to nemo_oo_agents events

**Files:**
- Modify: `src/nemo_oo_agents/events.py`
- Test: `tests/test_events.py`

**Step 1: Write failing tests**
Add:
```python
from nemo_oo_agents.events import TaskEvent

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
Implement `render_spec()` on nemo_oo_agents events mirroring context-blocks.

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_events.py::test_agent_event_render_spec -v`
Expected: PASS

**Step 5: Commit**
```bash
git add src/nemo_oo_agents/events.py tests/test_events.py
git commit -m "feat: add render_spec to nemo_oo_agents events"
```

---

### Task 7: Phase 3 — Flatten context-blocks events (remove `.data`)

**Files:**
- Modify: `packages/context-blocks/src/context_blocks/events.py`
- Modify: `packages/context-blocks/src/context_blocks/renderer.py`
- Modify: `packages/context-blocks/src/context_blocks/formatter.py`
- Modify: `packages/context-blocks/src/context_blocks/__init__.py`
- Test: `packages/context-blocks/tests/test_events.py`, `packages/context-blocks/tests/test_formatters.py`

**Step 1: Update tests for flat fields**
Change `UserEvent(content="...")` usage and update assertions.

**Step 2: Run tests to verify they fail**
Run: `pytest packages/context-blocks/tests/test_events.py packages/context-blocks/tests/test_formatters.py -v`
Expected: FAIL with missing `data` attribute

**Step 3: Flatten event models**
Replace data models with direct fields:
```python
class UserEvent(EventBase):
    type: Literal["user"] = "user"
    content: str | list
```
Remove `ContentData`, `ToolCallData`, `ToolResultData`, update exports and usages.

**Step 4: Update renderer/formatter**
Replace `event.data.content` with `event.content`, update tool call/result access.

**Step 5: Run tests to verify they pass**
Run: `pytest packages/context-blocks/tests/test_events.py packages/context-blocks/tests/test_formatters.py -v`
Expected: PASS

**Step 6: Commit**
```bash
git add packages/context-blocks/src/context_blocks/events.py \
  packages/context-blocks/src/context_blocks/renderer.py \
  packages/context-blocks/src/context_blocks/formatter.py \
  packages/context-blocks/src/context_blocks/__init__.py \
  packages/context-blocks/tests/test_events.py \
  packages/context-blocks/tests/test_formatters.py
git commit -m "refactor: flatten context-blocks event fields"
```

---

### Task 8: Phase 3 — Flatten nemo_oo_agents events + history usage

**Files:**
- Modify: `src/nemo_oo_agents/events.py`
- Modify: `src/nemo_oo_agents/runtime/history.py`
- Modify: `src/nemo_oo_agents/runtime/out_accessor.py`
- Test: `tests/test_history_manager.py`, `tests/test_events.py`, `tests/runtime/test_out_accessor.py`

**Step 1: Update tests for flat fields**
Replace `.data.content` with `.content` and update event construction to pass direct fields.

**Step 2: Run tests to verify they fail**
Run: `pytest tests/test_history_manager.py tests/test_events.py tests/runtime/test_out_accessor.py -v`
Expected: FAIL with missing `data` attribute

**Step 3: Flatten nemo_oo_agents event models**
Mirror context-blocks flat fields and update `render_spec()` to use direct fields.

**Step 4: Update HistoryManager helpers**
Update `recent()` and search logic to use flat fields (`event.content`, `event.arguments`, `event.tool_call_id`).

**Step 5: Run tests to verify they pass**
Run: `pytest tests/test_history_manager.py tests/test_events.py tests/runtime/test_out_accessor.py -v`
Expected: PASS

**Step 6: Commit**
```bash
git add src/nemo_oo_agents/events.py src/nemo_oo_agents/runtime/history.py \
  src/nemo_oo_agents/runtime/out_accessor.py tests/test_history_manager.py \
  tests/test_events.py tests/runtime/test_out_accessor.py
git commit -m "refactor: flatten nemo_oo_agents event fields"
```

---

### Task 9: Phase 3 — Update remaining references and run focused suite

**Files:**
- Modify: `tests/` (remaining `.data.` references)
- Modify: `src/nemo_oo_agents/` and `packages/context-blocks/` usages

**Step 1: Update remaining references**
Search for `.data.` in both packages and update to flat fields.

**Step 2: Run focused suite**
Run: `pytest tests/runtime tests/strategies tests/integration -v`
Expected: PASS

**Step 3: Commit**
```bash
git add src/nemo_oo_agents packages/context-blocks tests
git commit -m "chore: update remaining event field references"
```

---

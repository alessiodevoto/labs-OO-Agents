# Unified Visibility Model Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace all visibility mechanisms (underscore convention, `_FRAMEWORK_ATTRS`, `_VisibleToAgent`) with a unified `visible`/`hidden` API.

**Architecture:** `hidden` is a sentinel object that works as both a decorator (`@hidden`) and annotation marker (`Annotated[T, hidden]`). `visible` is a context manager for module-scope opt-in. Detection helpers (`is_hidden_method`, `is_hidden_field`) centralize all visibility checks. `__type_info__()` and `__instance_values__()` switch from underscore/hardcoded filtering to using these helpers.

**Tech Stack:** Python 3.12+, typing (Annotated, get_type_hints), agentdoc protocol, pytest

**Design doc:** `docs/plans/2026-03-10-visible-hidden-design.md`

---

### Task 1: Add `hidden` sentinel and detection helpers

**Files:**
- Modify: `src/agent006/visibility.py`
- Test: `tests/test_visibility.py`

**Step 1: Write failing tests for `hidden` as decorator**

Add to `tests/test_visibility.py`:

```python
from typing import Annotated


def test_hidden_decorator_marks_method():
    """@hidden sets _agent006_hidden on the function."""
    from agent006.visibility import hidden

    @hidden
    def my_method():
        pass

    assert my_method._agent006_hidden is True


def test_hidden_decorator_preserves_function():
    """@hidden returns the original function, not a wrapper."""
    from agent006.visibility import hidden

    def my_method():
        return 42

    result = hidden(my_method)
    assert result is my_method
    assert result() == 42
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_visibility.py::test_hidden_decorator_marks_method tests/test_visibility.py::test_hidden_decorator_preserves_function -v`
Expected: FAIL with `ImportError: cannot import name 'hidden'`

**Step 3: Implement `hidden` sentinel**

In `src/agent006/visibility.py`, add after the existing imports:

```python
class _Hidden:
    """Marker for hiding methods and fields from the LLM.

    Works in two roles:
    1. Decorator: @hidden on methods
    2. Annotation marker: Annotated[T, hidden] on fields
    """

    def __call__(self, func):
        """Use as @hidden decorator on methods."""
        func._agent006_hidden = True
        return func

    def __repr__(self):
        return "hidden"

hidden = _Hidden()
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_visibility.py::test_hidden_decorator_marks_method tests/test_visibility.py::test_hidden_decorator_preserves_function -v`
Expected: PASS

**Step 5: Write failing tests for detection helpers**

Add to `tests/test_visibility.py`:

```python
def test_is_hidden_method_true():
    from agent006.visibility import hidden, is_hidden_method

    @hidden
    def secret():
        pass

    assert is_hidden_method(secret) is True


def test_is_hidden_method_false():
    from agent006.visibility import is_hidden_method

    def public():
        pass

    assert is_hidden_method(public) is False


def test_is_hidden_field_with_annotated():
    from agent006.visibility import hidden, is_hidden_field

    class MyClass:
        secret: Annotated[str, hidden] = ""
        public: str = ""

    assert is_hidden_field(MyClass, "secret") is True
    assert is_hidden_field(MyClass, "public") is False


def test_is_hidden_field_missing_name():
    from agent006.visibility import is_hidden_field

    class MyClass:
        public: str = ""

    assert is_hidden_field(MyClass, "nonexistent") is False


def test_is_hidden_field_subclass_override():
    """Subclass re-declaring without hidden unhides the field."""
    from agent006.visibility import hidden, is_hidden_field

    class Base:
        secret: Annotated[str, hidden] = ""

    class Child(Base):
        secret: str = ""  # unhides

    assert is_hidden_field(Base, "secret") is True
    assert is_hidden_field(Child, "secret") is False
```

**Step 6: Run tests to verify they fail**

Run: `pytest tests/test_visibility.py -k "is_hidden" -v`
Expected: FAIL with `ImportError: cannot import name 'is_hidden_method'`

**Step 7: Implement detection helpers**

In `src/agent006/visibility.py`, add after the `hidden` singleton:

```python
import typing


def is_hidden_method(func) -> bool:
    """Check if a method is marked with @hidden."""
    return getattr(func, "_agent006_hidden", False) is True


def is_hidden_field(cls: type, name: str) -> bool:
    """Check if a field is annotated with Annotated[T, hidden].

    Checks the most-derived class first (MRO order), so a subclass
    can unhide a parent's hidden field by re-declaring without hidden.
    """
    # Walk MRO to find the most-derived annotation for this name
    for klass in cls.__mro__:
        annotations = klass.__dict__.get("__annotations__", {})
        if name in annotations:
            hint = annotations[name]
            # Check if it's Annotated with hidden in metadata
            origin = typing.get_origin(hint)
            if origin is Annotated:
                args = typing.get_args(hint)
                return any(arg is hidden for arg in args[1:])
            # Found annotation without hidden — not hidden
            return False
    return False
```

**Step 8: Run tests to verify they pass**

Run: `pytest tests/test_visibility.py -v`
Expected: ALL PASS

**Step 9: Commit**

```bash
git add tests/test_visibility.py src/agent006/visibility.py
git commit -m "feat: add hidden sentinel and is_hidden_method/is_hidden_field helpers"
```

---

### Task 2: Rename `_VisibleToAgent` → `_Visible`, `visible_to_agent` → `visible`

**Files:**
- Modify: `src/agent006/visibility.py`
- Modify: `tests/test_visibility.py`

**Step 1: Update tests to use new names**

In `tests/test_visibility.py`, update all three existing tests:
- Change `from agent006.visibility import _VisibleToAgent` → `from agent006.visibility import _Visible`
- Change all `_VisibleToAgent()` → `_Visible()`
- Change `vta` variable name → `vis` (cosmetic, optional)

Also add a test for the `visible` singleton name:

```python
def test_visible_singleton_exists():
    from agent006.visibility import visible

    assert isinstance(visible, _Visible)
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_visibility.py -v`
Expected: FAIL with `ImportError: cannot import name '_Visible'`

**Step 3: Rename in visibility.py**

In `src/agent006/visibility.py`:
- Rename class `_VisibleToAgent` → `_Visible`
- Rename singleton `visible_to_agent` → `visible`
- Update docstrings to reference `visible` instead of `visible_to_agent`

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_visibility.py -v`
Expected: ALL PASS

**Step 5: Update all references across codebase**

Search for `visible_to_agent` and `_VisibleToAgent` across the codebase and update:
- `src/agent006/__init__.py` (if already exported)
- Any docs referencing the old names
- `docs/plans/2026-03-09-visible-to-agent-impl.md` (update references)

Run: `grep -r "visible_to_agent\|_VisibleToAgent" src/ tests/ --include="*.py" -l`

**Step 6: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add -A
git commit -m "refactor: rename _VisibleToAgent/visible_to_agent to _Visible/visible"
```

---

### Task 3: Export `visible` and `hidden` from `agent006.__init__`

**Files:**
- Modify: `src/agent006/__init__.py`
- Test: `tests/test_visibility.py`

**Step 1: Write failing test**

Add to `tests/test_visibility.py`:

```python
def test_visible_and_hidden_importable_from_agent006():
    from agent006 import hidden, visible

    assert callable(hidden)
    assert hasattr(visible, "__enter__")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_visibility.py::test_visible_and_hidden_importable_from_agent006 -v`
Expected: FAIL with `ImportError`

**Step 3: Add exports to `__init__.py`**

In `src/agent006/__init__.py`, add after the `no_trace` import (line 23):

```python
from agent006.visibility import hidden, visible  # noqa: E402
```

Add to `__all__` list:

```python
"hidden",
"visible",
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_visibility.py::test_visible_and_hidden_importable_from_agent006 -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agent006/__init__.py tests/test_visibility.py
git commit -m "feat: export visible and hidden from agent006"
```

---

### Task 4: Switch `__type_info__()` from underscore filtering to `@hidden`

**Files:**
- Modify: `src/agent006/agent.py:374-405`
- Test: `tests/utils/test_doc_utility.py`

**Step 1: Write failing tests**

Add to `tests/utils/test_doc_utility.py`:

```python
def test_hidden_method_excluded_from_type_info():
    """@hidden methods should not appear in __type_info__()."""
    from unittest.mock import MagicMock

    from agent006 import Agent, hidden

    llm = MagicMock()
    llm.model = "test"

    class TestAgent(Agent, llm=llm):
        async def public_method(self):
            """Public."""
            ...

        @hidden
        async def secret_method(self):
            """Hidden."""
            ...

    info = TestAgent.__type_info__()
    method_names = [m.name for m in info.methods]
    assert "public_method" in method_names
    assert "secret_method" not in method_names


def test_underscore_method_visible_without_hidden():
    """_private methods should now be VISIBLE (no underscore convention)."""
    from unittest.mock import MagicMock

    from agent006 import Agent

    llm = MagicMock()
    llm.model = "test"

    class TestAgent(Agent, llm=llm):
        async def _private_method(self):
            """Was hidden by convention, now visible."""
            ...

    info = TestAgent.__type_info__()
    method_names = [m.name for m in info.methods]
    assert "_private_method" in method_names
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/utils/test_doc_utility.py::test_hidden_method_excluded_from_type_info tests/utils/test_doc_utility.py::test_underscore_method_visible_without_hidden -v`
Expected: FAIL — `secret_method` is visible (no `@hidden` support yet), `_private_method` is filtered out (underscore convention still active)

**Step 3: Update `__type_info__()` in `agent.py`**

In `src/agent006/agent.py`, modify `__type_info__()` (lines 374-405):

Replace:
```python
# Filter out private methods
filtered_methods = [m for m in base_info.methods if not m.name.startswith("_")]
```

With:
```python
from agent006.visibility import is_hidden_method

# Filter out @hidden methods
filtered_methods = [
    m for m in base_info.methods
    if not is_hidden_method(getattr(cls, m.name, None))
]
```

Replace:
```python
# Filter out framework fields and private fields
filtered_fields = [
    f
    for f in base_info.fields
    if f.name not in cls._FRAMEWORK_ATTRS and not f.name.startswith("_")
]
```

With:
```python
from agent006.visibility import is_hidden_field

# Filter out @hidden fields
filtered_fields = [
    f
    for f in base_info.fields
    if not is_hidden_field(cls, f.name)
]
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/utils/test_doc_utility.py::test_hidden_method_excluded_from_type_info tests/utils/test_doc_utility.py::test_underscore_method_visible_without_hidden -v`
Expected: PASS

**Step 5: Fix existing tests that relied on underscore filtering**

Run: `pytest tests/utils/test_doc_utility.py -v`

Tests that assert `_private` methods are hidden will now fail. Update them to use `@hidden` instead. Key tests to update:
- `test_runtime_hidden_in_doc` (line ~304)
- `test_event_manager_hidden_in_doc` (line ~315)
- `test_type_info_hides_true_internals` (line ~417)

These tests check that `runtime`, `event_manager` etc. are hidden — they will pass once Task 5 (framework attrs migration) is done. For now, mark them with `@pytest.mark.xfail(reason="Waiting for _FRAMEWORK_ATTRS migration")` or fix them in Task 5.

**Step 6: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: Some failures related to framework attrs (fixed in Task 5)

**Step 7: Commit**

```bash
git add src/agent006/agent.py tests/utils/test_doc_utility.py
git commit -m "feat: switch __type_info__ from underscore filtering to @hidden"
```

---

### Task 5: Switch `__instance_values__()` from underscore filtering to `hidden`

**Files:**
- Modify: `src/agent006/agent.py:407-462`
- Test: `tests/utils/test_doc_utility.py`

**Step 1: Write failing test**

Add to `tests/utils/test_doc_utility.py`:

```python
def test_hidden_field_excluded_from_instance_values():
    """Annotated[T, hidden] fields should not appear in __instance_values__()."""
    from typing import Annotated
    from unittest.mock import MagicMock

    from agent006 import Agent, hidden

    llm = MagicMock()
    llm.model = "test"

    class TestAgent(Agent, llm=llm):
        public_val: str = "visible"
        secret_val: Annotated[str, hidden] = "hidden"

    agent = TestAgent()
    agent.public_val = "visible"
    agent.secret_val = "hidden"
    values = agent.__instance_values__()
    assert "public_val" in values
    assert "secret_val" not in values


def test_underscore_field_visible_without_hidden():
    """_private fields should now be VISIBLE (no underscore convention)."""
    from unittest.mock import MagicMock

    from agent006 import Agent

    llm = MagicMock()
    llm.model = "test"

    class TestAgent(Agent, llm=llm):
        _custom: str = "was private"

    agent = TestAgent()
    values = agent.__instance_values__()
    assert "_custom" in values
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_doc_utility.py::test_hidden_field_excluded_from_instance_values tests/utils/test_doc_utility.py::test_underscore_field_visible_without_hidden -v`
Expected: FAIL

**Step 3: Update `__instance_values__()` in `agent.py`**

In `src/agent006/agent.py`, modify `__instance_values__()` (lines 407-462):

Replace the `excluded = self._FRAMEWORK_ATTRS` and underscore checks.

For instance attributes section (~lines 425-433), replace:
```python
if name.startswith("_") or name in excluded:
    continue
```

With:
```python
from agent006.visibility import is_hidden_field
if is_hidden_field(type(self), name):
    continue
```

For class-level attributes section (~lines 437-460), replace:
```python
if name in values or name.startswith("_") or name in excluded:
    continue
```

With:
```python
if name in values or is_hidden_field(type(self), name):
    continue
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/utils/test_doc_utility.py::test_hidden_field_excluded_from_instance_values tests/utils/test_doc_utility.py::test_underscore_field_visible_without_hidden -v`
Expected: PASS

**Step 5: Run full doc utility tests and fix regressions**

Run: `pytest tests/utils/test_doc_utility.py -v`
Fix any tests that relied on underscore or `_FRAMEWORK_ATTRS` filtering.

**Step 6: Commit**

```bash
git add src/agent006/agent.py tests/utils/test_doc_utility.py
git commit -m "feat: switch __instance_values__ from underscore filtering to hidden annotation"
```

---

### Task 6: Migrate `_FRAMEWORK_ATTRS` to `Annotated[T, hidden]`

**Files:**
- Modify: `src/agent006/agent.py:66-82` (class declaration), `143-215` (init), `367-372` (delete `_FRAMEWORK_ATTRS`)
- Test: `tests/utils/test_doc_utility.py`

**Step 1: Write failing test**

Add to `tests/utils/test_doc_utility.py`:

```python
def test_framework_attrs_hidden_via_annotation():
    """runtime, event_manager, event_query, render_config should be hidden via Annotated[T, hidden]."""
    from unittest.mock import MagicMock

    from agent006 import Agent, hidden
    from agent006.visibility import is_hidden_field

    assert is_hidden_field(Agent, "runtime") is True
    assert is_hidden_field(Agent, "event_manager") is True
    assert is_hidden_field(Agent, "event_query") is True
    assert is_hidden_field(Agent, "render_config") is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_doc_utility.py::test_framework_attrs_hidden_via_annotation -v`
Expected: FAIL — no `Annotated[T, hidden]` annotations on Agent class yet

**Step 3: Add annotations to Agent class**

In `src/agent006/agent.py`, add to the Agent class body (around line 83, after the docstring):

```python
from __future__ import annotations  # at top of file if not already there
from typing import TYPE_CHECKING, Annotated, Any, NamedTuple

from agent006.visibility import hidden

# ... in the Agent class body:
class Agent(metaclass=AgentMeta):
    # ... docstring ...

    # Framework attributes — hidden from LLM via annotation
    runtime: Annotated[Any, hidden]
    event_manager: Annotated[Any, hidden]
    event_query: Annotated[Any, hidden]
    render_config: Annotated[Any, hidden]
```

Note: Use `Any` for the type to avoid circular imports. The actual types are only available under `TYPE_CHECKING`.

**Step 4: Delete `_FRAMEWORK_ATTRS`**

Remove lines 364-372 (the `_FRAMEWORK_ATTRS` set and its comment). Remove all references to `_FRAMEWORK_ATTRS` in the file (the `excluded = self._FRAMEWORK_ATTRS` line in `__instance_values__` should already be gone from Task 5).

**Step 5: Run test to verify it passes**

Run: `pytest tests/utils/test_doc_utility.py::test_framework_attrs_hidden_via_annotation -v`
Expected: PASS

**Step 6: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: ALL PASS (or only pre-existing failures unrelated to visibility)

**Step 7: Commit**

```bash
git add src/agent006/agent.py tests/utils/test_doc_utility.py
git commit -m "refactor: replace _FRAMEWORK_ATTRS with Annotated[T, hidden] on Agent"
```

---

### Task 7: Filter `exec_globals` by `_agent006_visible_names`

**Files:**
- Modify: `src/agent006/runtime/actor.py:648-649`
- Modify: `src/agent006/strategies/generated_code.py:45-46`
- Test: `tests/test_visibility.py`

**Step 1: Write failing test**

Add to `tests/test_visibility.py`:

```python
def test_filter_exec_globals_by_visible_names():
    """Only _agent006_visible_names should survive module dict filtering."""
    from agent006.visibility import filter_module_globals

    import types

    mod = types.ModuleType("test_mod")
    mod.public_import = "json"
    mod.visible_const = 42
    mod.private_import = "os"
    mod._agent006_visible_names = {"visible_const"}

    filtered = filter_module_globals(mod)
    assert "visible_const" in filtered
    assert "public_import" not in filtered
    assert "private_import" not in filtered


def test_filter_exec_globals_no_visible_names_passes_all():
    """If _agent006_visible_names is not set, pass through all names (backwards compat)."""
    from agent006.visibility import filter_module_globals

    import types

    mod = types.ModuleType("test_mod")
    mod.some_import = "json"
    mod.some_const = 42

    filtered = filter_module_globals(mod)
    assert "some_import" in filtered
    assert "some_const" in filtered
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_visibility.py -k "filter_exec_globals" -v`
Expected: FAIL with `ImportError: cannot import name 'filter_module_globals'`

**Step 3: Implement `filter_module_globals`**

In `src/agent006/visibility.py`, add:

```python
def filter_module_globals(module: types.ModuleType) -> dict[str, Any]:
    """Filter module dict to only visible names.

    If the module has _agent006_visible_names (set by `with visible:` blocks),
    only those names are included. Otherwise, all names pass through (backwards compat).

    Returns:
        Filtered dict of module globals.
    """
    visible_names = getattr(module, "_agent006_visible_names", None)
    if visible_names is None:
        return dict(module.__dict__)
    return {k: v for k, v in module.__dict__.items() if k in visible_names}
```

Add `from typing import Any` to the imports at the top of the file.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_visibility.py -k "filter_exec_globals" -v`
Expected: PASS

**Step 5: Wire into `actor.py`**

In `src/agent006/runtime/actor.py`, replace lines 648-649:

```python
agent_module = inspect.getmodule(type(self.agent))
exec_globals = dict(agent_module.__dict__) if agent_module else {}
```

With:

```python
from agent006.visibility import filter_module_globals

agent_module = inspect.getmodule(type(self.agent))
exec_globals = filter_module_globals(agent_module) if agent_module else {}
```

**Step 6: Wire into `generated_code.py`**

In `src/agent006/strategies/generated_code.py`, replace lines 45-46:

```python
agent_module = inspect.getmodule(type(agent))
namespace: dict[str, Any] = dict(agent_module.__dict__) if agent_module else {}
```

With:

```python
from agent006.visibility import filter_module_globals

agent_module = inspect.getmodule(type(agent))
namespace: dict[str, Any] = filter_module_globals(agent_module) if agent_module else {}
```

**Step 7: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: ALL PASS

**Step 8: Commit**

```bash
git add src/agent006/visibility.py src/agent006/runtime/actor.py src/agent006/strategies/generated_code.py tests/test_visibility.py
git commit -m "feat: filter exec_globals by _agent006_visible_names from visible blocks"
```

---

### Task 8: Update `_extract_module_context` in codeact.py

**Files:**
- Modify: `src/agent006/strategies/codeact.py:1696-1750`
- Test: existing codeact tests

**Step 1: Update `_extract_module_context` to use `filter_module_globals`**

In `src/agent006/strategies/codeact.py`, modify `_extract_module_context()` (lines 1696-1750).

Replace the two loops that iterate `agent_module.__dict__` with underscore checks:

```python
# Line 1714-1717: replace
for name, obj in agent_module.__dict__.items():
    if name.startswith("_"):
        continue
```

With:

```python
from agent006.visibility import filter_module_globals

filtered = filter_module_globals(agent_module)
for name, obj in filtered.items():
```

Do the same for the second loop (lines 1733-1735):

```python
# Replace
for name, obj in agent_module.__dict__.items():
    if name.startswith("_"):
        continue
```

With:

```python
for name, obj in filtered.items():
```

Note: `filtered` is already computed above, reuse it.

**Step 2: Run codeact tests**

Run: `pytest tests/strategies/test_codeact*.py -x -q`
Expected: ALL PASS

**Step 3: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add src/agent006/strategies/codeact.py
git commit -m "refactor: use filter_module_globals in _extract_module_context"
```

---

### Task 9: Fix all remaining tests that rely on underscore convention

**Files:**
- Modify: various test files
- Check: `tests/utils/test_doc_utility.py`, any integration tests

**Step 1: Find all tests that assume underscore = hidden**

Run: `grep -rn "startswith.*_\|_private\|hidden.*underscore\|private.*method" tests/ --include="*.py" -l`

Review each file for tests that assert `_private` methods/fields are hidden from doc(self).

**Step 2: Update each test**

For each test that creates an agent with `_private` methods and asserts they're hidden:
- Add `@hidden` decorator to the method
- Or update the assertion to expect the method IS visible

**Step 3: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add -A
git commit -m "test: update all tests from underscore convention to @hidden"
```

---

### Task 10: Update AGENTS.md documentation

**Files:**
- Modify: `AGENTS.md`

**Step 1: Update visibility documentation**

In `AGENTS.md`, update the "Method Design" section:

Replace the `_private methods are invisible` bullet with:

```markdown
- **`@hidden` methods/fields are invisible to the LLM.** Not in `doc(self)`, not in exec_globals. Use `Annotated[T, hidden]` for fields. Underscore prefix has no visibility meaning.
- **Module-level names are hidden by default.** Use `with visible:` to opt-in specific imports and constants.
```

**Step 2: Add visibility section**

Add a new "Visibility" section after "Method Design":

```markdown
### Visibility

Two concepts control what the LLM can see: `visible` and `hidden`.

| Scope | Default | Opt-in | Opt-out |
|-------|---------|--------|---------|
| Module level | HIDDEN | `with visible:` block | (already hidden) |
| Class methods | VISIBLE | (already visible) | `@hidden` |
| Class fields | VISIBLE | (already visible) | `Annotated[T, hidden]` |

```python
from agent006 import Agent, hidden, visible

with visible:
    from pathlib import Path
    API_BASE = "https://api.example.com"

class MyAgent(Agent, llm=llm):
    model: str = "gpt-4"
    api_key: Annotated[str, hidden] = ""

    def search(self, query: str) -> list[str]:
        ...

    @hidden
    def rebuild_index(self):
        pass
```

To unhide a parent's hidden field, re-declare in the subclass without `hidden`:

```python
class MyAgent(Agent, llm=llm):
    context: Context  # unhides Agent's Annotated[Context, hidden]
```
```

**Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update AGENTS.md with unified visible/hidden visibility model"
```

---

### Task 11: Final verification

**Step 1: Run full test suite**

Run: `pytest tests/ -q`
Expected: ALL PASS

**Step 2: Run ruff lint**

Run: `ruff check src/agent006/visibility.py src/agent006/agent.py src/agent006/runtime/actor.py src/agent006/strategies/generated_code.py src/agent006/strategies/codeact.py`
Expected: No errors

**Step 3: Run ruff format**

Run: `ruff format --check src/agent006/ tests/`
Expected: No formatting issues

**Step 4: Verify exports**

Run: `python -c "from agent006 import visible, hidden; print(visible, hidden)"`
Expected: prints the singleton objects

**Step 5: Commit any remaining fixes**

```bash
git add -A
git commit -m "chore: final cleanup for visible/hidden visibility model"
```

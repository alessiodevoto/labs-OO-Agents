# `visible_to_agent` — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `visible_to_agent` context manager that controls which module-level names are visible to LLM-generated code in exec_globals, replacing the current "everything leaks" default.

**Architecture:** `_VisibleToAgent` context manager uses module dict diff to track names defined inside its block, stores them as `_nooa_visible_names` on the module. `execute_code` in `actor.py` reads this set to build exec_globals from only visible names instead of the full module dict. Validates conflicts with `blocked_modules` (error) and `RESTRICTED_MODULES` (warning).

**Tech Stack:** Python `sys._getframe`, `types.ModuleType`, pytest, warnings module

**Design doc:** `docs/2026-03-09-visible-to-agent-design.md`

---

### Task 1: Create `_VisibleToAgent` context manager with dict diff

**Files:**
- Create: `src/nooa/visibility.py`
- Test: `tests/test_visibility.py`

**Step 1: Write the failing test**

```python
# tests/test_visibility.py
import types
import sys


def test_visible_to_agent_captures_new_names():
    """Names defined inside the block are recorded on the module."""
    from nooa.visibility import _VisibleToAgent

    # Create a fake module to test against
    mod = types.ModuleType("fake_module")
    mod.existing = "already here"
    sys.modules["fake_module"] = mod

    vta = _VisibleToAgent()
    # Simulate entering from fake_module's context
    vta._enter_for_module(mod)
    mod.new_name = "added inside block"
    mod.another = 42
    vta.__exit__(None, None, None)

    assert "new_name" in mod._nooa_visible_names
    assert "another" in mod._nooa_visible_names
    assert "existing" not in mod._nooa_visible_names

    # Cleanup
    del sys.modules["fake_module"]


def test_visible_to_agent_multiple_blocks_are_additive():
    """Second block adds to the set, doesn't replace."""
    from nooa.visibility import _VisibleToAgent

    mod = types.ModuleType("fake_module2")
    sys.modules["fake_module2"] = mod

    vta = _VisibleToAgent()

    vta._enter_for_module(mod)
    mod.first = 1
    vta.__exit__(None, None, None)

    vta._enter_for_module(mod)
    mod.second = 2
    vta.__exit__(None, None, None)

    assert "first" in mod._nooa_visible_names
    assert "second" in mod._nooa_visible_names

    del sys.modules["fake_module2"]


def test_visible_to_agent_empty_block():
    """Empty block records nothing."""
    from nooa.visibility import _VisibleToAgent

    mod = types.ModuleType("fake_module3")
    sys.modules["fake_module3"] = mod

    vta = _VisibleToAgent()
    vta._enter_for_module(mod)
    vta.__exit__(None, None, None)

    assert mod._nooa_visible_names == set()

    del sys.modules["fake_module3"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_visibility.py -v`
Expected: FAIL with `ImportError` — `nooa.visibility` doesn't exist

**Step 3: Write the implementation**

```python
# src/nooa/visibility.py
"""Context manager for controlling agent namespace visibility.

Tracks which module-level names are visible to LLM-generated code.
Names defined inside a `with visible_to_agent:` block are recorded
on the module as `_nooa_visible_names` for consumption by
exec_globals construction in actor.py.
"""

from __future__ import annotations

import sys
import types


class _VisibleToAgent:
    """Context manager that tracks names defined inside its block.

    Uses module dict diff: snapshots keys on __enter__, diffs on __exit__.
    Records new names on the module as `_nooa_visible_names`.

    Usage at module level::

        import nooa

        with nooa.visible_to_agent:
            import json
            import pandas as pd
            THRESHOLD = 0.5
    """

    def __enter__(self):
        frame = sys._getframe(1)
        module_name = frame.f_globals.get("__name__")
        module = sys.modules.get(module_name) if module_name else None
        if module is None:
            raise RuntimeError(
                "visible_to_agent must be used at module level, "
                "could not determine calling module."
            )
        return self._enter_for_module(module)

    def _enter_for_module(self, module: types.ModuleType):
        """Enter the block for a specific module (also used by tests)."""
        self._module = module
        self._snapshot = set(module.__dict__.keys())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        new_names = set(self._module.__dict__.keys()) - self._snapshot
        existing = getattr(self._module, "_nooa_visible_names", set())
        self._module._nooa_visible_names = existing | new_names
        self._module = None
        self._snapshot = None
        return False


# Singleton — reused across all `with` blocks in all modules.
visible_to_agent = _VisibleToAgent()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_visibility.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/nooa/visibility.py tests/test_visibility.py
git commit -m "feat: add _VisibleToAgent context manager with dict diff"
```

---

### Task 2: Add blocked_modules conflict validation (startup error)

**Files:**
- Modify: `src/nooa/visibility.py:43-52` (the `__exit__` method)
- Test: `tests/test_visibility.py`

**Step 1: Write the failing test**

```python
# Add to tests/test_visibility.py
import subprocess

import pytest


def test_blocked_module_in_visible_raises_error():
    """Importing a blocked module inside visible_to_agent raises ConfigurationError."""
    from nooa.visibility import _VisibleToAgent

    mod = types.ModuleType("fake_blocked")
    sys.modules["fake_blocked"] = mod

    vta = _VisibleToAgent()
    vta._enter_for_module(mod)
    mod.subprocess = subprocess  # blocked module

    with pytest.raises(Exception, match="blocked_modules"):
        vta.__exit__(None, None, None)

    del sys.modules["fake_blocked"]


def test_function_from_blocked_module_in_visible_raises_error():
    """Importing a function from a blocked module raises ConfigurationError."""
    from nooa.visibility import _VisibleToAgent

    mod = types.ModuleType("fake_blocked2")
    sys.modules["fake_blocked2"] = mod

    vta = _VisibleToAgent()
    vta._enter_for_module(mod)
    mod.run = subprocess.run  # function from blocked module

    with pytest.raises(Exception, match="blocked_modules"):
        vta.__exit__(None, None, None)

    del sys.modules["fake_blocked2"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_visibility.py::test_blocked_module_in_visible_raises_error -v`
Expected: FAIL — no error raised, test's `pytest.raises` fails

**Step 3: Write the implementation**

Add validation to `__exit__` in `src/nooa/visibility.py`. Add a helper `_get_module_name`:

```python
def _get_module_name(obj) -> str | None:
    """Get the module name of an object."""
    if isinstance(obj, types.ModuleType):
        return obj.__name__
    return getattr(obj, "__module__", None)
```

Update `__exit__` to call a validation method:

```python
def __exit__(self, exc_type, exc_val, exc_tb):
    if exc_type is not None:
        # Don't validate if the block raised — let the original error propagate
        self._module = None
        self._snapshot = None
        return False

    new_names = set(self._module.__dict__.keys()) - self._snapshot
    existing = getattr(self._module, "_nooa_visible_names", set())
    self._module._nooa_visible_names = existing | new_names

    self._validate_new_names(new_names)

    self._module = None
    self._snapshot = None
    return False

def _validate_new_names(self, names: set[str]) -> None:
    from nooa.runtime.restrictions import (
        DEFAULT_BLOCKED_MODULES,
        is_from_blocked_module,
    )

    for name in names:
        obj = self._module.__dict__.get(name)
        if obj is None:
            continue
        if is_from_blocked_module(obj, DEFAULT_BLOCKED_MODULES):
            module_name = _get_module_name(obj) or "unknown"
            raise ConfigurationError(
                f"'{name}' (from {module_name}) is in blocked_modules but was "
                f"declared in visible_to_agent. Import it outside the block "
                f"(developer-only) or remove it from blocked_modules via "
                f"CodeActConfig."
            )
```

Add `ConfigurationError` to `src/nooa/errors/__init__.py`:

```python
class ConfigurationError(NemoOOAgentsError):
    """Agent configuration is invalid.

    Raised at import time when visible_to_agent contains conflicting entries
    (e.g. a module that is also in blocked_modules).
    """
    pass
```

Import in visibility.py:

```python
from nooa.errors import ConfigurationError
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_visibility.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/nooa/visibility.py src/nooa/errors/__init__.py tests/test_visibility.py
git commit -m "feat: validate blocked_modules conflicts in visible_to_agent"
```

---

### Task 3: Add RESTRICTED_MODULES warning

**Files:**
- Modify: `src/nooa/visibility.py` (the `_validate_new_names` method)
- Test: `tests/test_visibility.py`

**Step 1: Write the failing test**

```python
# Add to tests/test_visibility.py
import warnings


def test_restricted_module_in_visible_warns():
    """Importing a restricted (but not blocked) module logs a warning."""
    import os

    from nooa.visibility import _VisibleToAgent

    mod = types.ModuleType("fake_restricted")
    sys.modules["fake_restricted"] = mod

    vta = _VisibleToAgent()
    vta._enter_for_module(mod)
    mod.os = os  # restricted but not blocked

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        vta.__exit__(None, None, None)

    assert len(w) == 1
    assert "restricted" in str(w[0].message).lower()
    assert "os" in str(w[0].message)

    del sys.modules["fake_restricted"]


def test_non_restricted_module_no_warning():
    """Non-restricted modules produce no warning."""
    import json

    from nooa.visibility import _VisibleToAgent

    mod = types.ModuleType("fake_clean")
    sys.modules["fake_clean"] = mod

    vta = _VisibleToAgent()
    vta._enter_for_module(mod)
    mod.json = json

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        vta.__exit__(None, None, None)

    assert len(w) == 0

    del sys.modules["fake_clean"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_visibility.py::test_restricted_module_in_visible_warns -v`
Expected: FAIL — no warning emitted

**Step 3: Write the implementation**

Add to `_validate_new_names` in `src/nooa/visibility.py`, after the blocked check:

```python
import warnings

from nooa.runtime.restrictions import (
    DEFAULT_BLOCKED_MODULES,
    RESTRICTED_MODULES,
    is_from_blocked_module,
    match_blocked_module,
)

# ... inside _validate_new_names, after the blocked check loop:
for name in names:
    obj = self._module.__dict__.get(name)
    if obj is None:
        continue

    # Blocked module conflict → error (already done above)
    if is_from_blocked_module(obj, DEFAULT_BLOCKED_MODULES):
        module_name = _get_module_name(obj) or "unknown"
        raise ConfigurationError(...)

    # Restricted module → warning
    module_name = _get_module_name(obj)
    if module_name and match_blocked_module(module_name, RESTRICTED_MODULES):
        warnings.warn(
            f"'{name}' (from {module_name}) is a restricted module and has "
            f"been made visible to agent code via visible_to_agent. "
            f"Specific blocking calls are still enforced by "
            f"BlockingCallValidator.",
            stacklevel=4,
        )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_visibility.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/nooa/visibility.py tests/test_visibility.py
git commit -m "feat: warn when restricted modules are made visible to agent"
```

---

### Task 4: Export `visible_to_agent` from `nooa.__init__`

**Files:**
- Modify: `src/nooa/__init__.py:12-71`
- Test: `tests/test_visibility.py`

**Step 1: Write the failing test**

```python
# Add to tests/test_visibility.py
def test_importable_from_nooa():
    """visible_to_agent is importable from the top-level package."""
    from nooa import visible_to_agent as vta
    from nooa.visibility import _VisibleToAgent

    assert isinstance(vta, _VisibleToAgent)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_visibility.py::test_importable_from_nooa -v`
Expected: FAIL — `ImportError: cannot import name 'visible_to_agent'`

**Step 3: Write the implementation**

In `src/nooa/__init__.py`, add:

```python
from nooa.visibility import visible_to_agent  # noqa: E402
```

And add to `__all__`:

```python
"visible_to_agent",
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_visibility.py::test_importable_from_nooa -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/nooa/__init__.py tests/test_visibility.py
git commit -m "feat: export visible_to_agent from nooa package"
```

---

### Task 5: Wire exec_globals construction to use `_nooa_visible_names`

**Files:**
- Modify: `src/nooa/runtime/actor.py:647-649`
- Test: `tests/runtime/test_exec_globals_visibility.py`

**Step 1: Write the failing test**

```python
# tests/runtime/test_exec_globals_visibility.py
"""Tests for exec_globals construction respecting visible_to_agent."""
import sys
import types

from nooa.runtime.actor import _build_module_exec_globals


def test_no_visible_names_returns_empty():
    """Module with no _nooa_visible_names → empty dict."""
    mod = types.ModuleType("mod_no_vta")
    mod.json = __import__("json")
    mod.os = __import__("os")
    sys.modules["mod_no_vta"] = mod

    result = _build_module_exec_globals(mod)
    assert result == {}

    del sys.modules["mod_no_vta"]


def test_visible_names_filters_module_dict():
    """Only names in _nooa_visible_names are included."""
    mod = types.ModuleType("mod_with_vta")
    mod.json = __import__("json")
    mod.os = __import__("os")
    mod.SECRET = "hidden"
    mod._nooa_visible_names = {"json"}
    sys.modules["mod_with_vta"] = mod

    result = _build_module_exec_globals(mod)
    assert "json" in result
    assert "os" not in result
    assert "SECRET" not in result

    del sys.modules["mod_with_vta"]


def test_visible_names_includes_non_module_objects():
    """Constants and functions declared in visible_to_agent are included."""
    mod = types.ModuleType("mod_with_consts")
    mod.THRESHOLD = 0.5
    mod.json = __import__("json")
    mod._private = "hidden"
    mod._nooa_visible_names = {"THRESHOLD", "json"}
    sys.modules["mod_with_consts"] = mod

    result = _build_module_exec_globals(mod)
    assert result["THRESHOLD"] == 0.5
    assert "json" in result
    assert "_private" not in result

    del sys.modules["mod_with_consts"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/runtime/test_exec_globals_visibility.py -v`
Expected: FAIL — `_build_module_exec_globals` doesn't exist

**Step 3: Write the implementation**

Extract the module-dict-to-exec_globals logic into a helper in `src/nooa/runtime/actor.py`:

```python
def _build_module_exec_globals(agent_module: types.ModuleType | None) -> dict[str, Any]:
    """Build exec_globals from the agent module, respecting visible_to_agent.

    If the module has `_nooa_visible_names` (set by visible_to_agent),
    only those names are included. Otherwise returns empty dict (strict default).
    """
    if agent_module is None:
        return {}

    visible_names = getattr(agent_module, "_nooa_visible_names", None)

    if visible_names is not None:
        return {
            name: agent_module.__dict__[name]
            for name in visible_names
            if name in agent_module.__dict__
        }

    # No visible_to_agent block: strict default — empty module dict
    return {}
```

Then in `execute_code` (line 648-649), replace:

```python
# Before:
agent_module = inspect.getmodule(type(self.agent))
exec_globals = dict(agent_module.__dict__) if agent_module else {}

# After:
agent_module = inspect.getmodule(type(self.agent))
exec_globals = _build_module_exec_globals(agent_module)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/runtime/test_exec_globals_visibility.py -v`
Expected: PASS

**Step 5: Run existing tests to check for regressions**

Run: `uv run pytest tests/runtime/test_exec_globals_stripping.py tests/runtime/test_code_validator.py tests/runtime/test_blocking_call_validator.py tests/config/test_blocked_config.py -v`
Expected: All PASS (stripping tests use `_strip_blocked_modules` directly, not the full exec_globals path)

**Step 6: Commit**

```bash
git add src/nooa/runtime/actor.py tests/runtime/test_exec_globals_visibility.py
git commit -m "feat: wire exec_globals to respect visible_to_agent allowlist"
```

---

### Task 6: Update existing examples and tests that rely on module dict leaking

**Files:**
- Modify: various test files and examples that create agents and expect module imports to be visible
- No new test file

This task is exploratory. After wiring the strict default, run the full test suite to find failures:

**Step 1: Run the full test suite**

Run: `uv run pytest tests/ -x --tb=short 2>&1 | head -80`

**Step 2: For each failure, determine the fix**

Two categories of fixes:
1. **Test creates an agent and expects module imports in exec_globals** → add `_nooa_visible_names` to the test module, or use `visible_to_agent` in the test setup.
2. **Test directly calls `execute_code` and provides its own exec_globals** → no change needed (these bypass module dict entirely).

**Step 3: Apply fixes and re-run until green**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add -u
git commit -m "fix: update tests for strict visible_to_agent default"
```

---

### Task 7: Integration test — full round-trip with visible_to_agent

**Files:**
- Test: `tests/test_visibility.py` (add integration test)

**Step 1: Write the integration test**

```python
# Add to tests/test_visibility.py
def test_end_to_end_visible_to_agent():
    """Integration: visible_to_agent controls what exec_globals contains."""
    import json
    import os

    from nooa.runtime.actor import _build_module_exec_globals, _strip_blocked_modules
    from nooa.runtime.restrictions import DEFAULT_BLOCKED_MODULES
    from nooa.visibility import _VisibleToAgent

    # Simulate a module with mixed imports
    mod = types.ModuleType("fake_e2e")
    mod.json = json
    mod.os = os
    mod._internal = "should not leak"
    sys.modules["fake_e2e"] = mod

    # Use visible_to_agent to declare json as visible
    vta = _VisibleToAgent()
    vta._enter_for_module(mod)
    # Simulate: only json was imported inside the block
    mod.__dict__.setdefault("json", json)  # already there, but the snapshot diff will catch re-assignments
    # Actually, we need to add a NEW name to trigger diff.
    # Let's set it up properly:
    del sys.modules["fake_e2e"]

    mod2 = types.ModuleType("fake_e2e2")
    sys.modules["fake_e2e2"] = mod2

    vta2 = _VisibleToAgent()
    vta2._enter_for_module(mod2)
    mod2.json = json
    mod2.THRESHOLD = 0.5
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        vta2.__exit__(None, None, None)

    # Now add things outside the block (developer-only)
    mod2.os = os
    mod2._internal = "hidden"

    # Build exec_globals
    result = _build_module_exec_globals(mod2)
    assert "json" in result
    assert "THRESHOLD" in result
    assert "os" not in result
    assert "_internal" not in result

    # Stripping still works as safety net
    result = _strip_blocked_modules(result, DEFAULT_BLOCKED_MODULES)
    assert "json" in result  # not blocked

    del sys.modules["fake_e2e2"]
```

**Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_visibility.py::test_end_to_end_visible_to_agent -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_visibility.py
git commit -m "test: add integration test for visible_to_agent round-trip"
```

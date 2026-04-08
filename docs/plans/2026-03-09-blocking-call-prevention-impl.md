# Blocking Call Prevention — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent LLM-generated code from blocking the event loop by stripping blocked modules from exec_globals and replacing string-based AST validation with runtime-aware name resolution.

**Architecture:** Unified restrictions file defines defaults. `CodeActConfig` exposes `blocked_modules`/`blocked_calls` as configurable fields. exec_globals stripping removes blocked modules before code execution. `BlockingCallValidator` resolves AST names against exec_globals for partially-blocked modules. Replaces `AsyncSafetyValidator` entirely.

**Tech Stack:** Python AST, Pydantic config, pytest

**Design doc:** `docs/2026-03-09-blocking-call-prevention-design.md`

---

### Task 1: Create restrictions.py with default blocked modules/calls

**Files:**
- Create: `src/agent006/runtime/restrictions.py`
- Test: `tests/runtime/test_restrictions.py`

**Step 1: Write the failing test**

```python
# tests/runtime/test_restrictions.py
from agent006.runtime.restrictions import (
    DEFAULT_BLOCKED_CALLS,
    DEFAULT_BLOCKED_MODULES,
    RESTRICTED_MODULES,
)


def test_blocked_modules_is_frozenset():
    assert isinstance(DEFAULT_BLOCKED_MODULES, frozenset)
    assert "subprocess" in DEFAULT_BLOCKED_MODULES
    assert "socket" in DEFAULT_BLOCKED_MODULES


def test_blocked_calls_has_expected_modules():
    assert "time" in DEFAULT_BLOCKED_CALLS
    assert "sleep" in DEFAULT_BLOCKED_CALLS["time"]
    assert "os" in DEFAULT_BLOCKED_CALLS
    assert "system" in DEFAULT_BLOCKED_CALLS["os"]
    assert "asyncio" in DEFAULT_BLOCKED_CALLS
    assert "run" in DEFAULT_BLOCKED_CALLS["asyncio"]


def test_blocked_modules_subset_of_restricted():
    assert DEFAULT_BLOCKED_MODULES.issubset(RESTRICTED_MODULES)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/runtime/test_restrictions.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError`

**Step 3: Write the implementation**

```python
# src/agent006/runtime/restrictions.py
"""Unified module restrictions for agent code execution.

Single source of truth for blocked modules, blocked calls, and restricted modules.
Consumed by CodeActConfig (defaults), exec_globals stripping, and BlockingCallValidator.
"""

from __future__ import annotations

# Fully blocked — stripped from exec_globals, block the event loop.
# These modules have no legitimate async use in CodeAct.
DEFAULT_BLOCKED_MODULES: frozenset[str] = frozenset({
    "subprocess",
    "socket",
    "http.client",
    "urllib.request",
    "ftplib",
    "smtplib",
    "imaplib",
    "poplib",
    "telnetlib",
    "xmlrpc.client",
    "select",
    "signal",
})

# Specific calls blocked on otherwise-allowed modules.
# Keys are module names, values are frozensets of blocked function/method names.
# Dotted names (e.g. "Thread.join") match class-qualified instance method calls.
# Plain names (e.g. "sleep") match module-level function calls.
DEFAULT_BLOCKED_CALLS: dict[str, frozenset[str]] = {
    "time": frozenset({"sleep"}),
    "os": frozenset({"system", "popen", "wait", "waitpid", "waitid"}),
    "threading": frozenset({"Thread.join", "Lock.acquire", "Event.wait", "Condition.wait"}),
    "multiprocessing": frozenset({"Process.join", "Queue.get", "Queue.put"}),
    "asyncio": frozenset({"run", "run_coroutine_threadsafe"}),
}

# Restricted — require explicit declaration (allow_imports or normal import).
# Superset of DEFAULT_BLOCKED_MODULES.
RESTRICTED_MODULES: frozenset[str] = frozenset({
    # Blocked modules (all restricted too)
    *DEFAULT_BLOCKED_MODULES,
    # External resource access
    "os",
    "shutil",
    "pathlib",
    "tempfile",
    "glob",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "http",
    # Database
    "sqlite3",
    "psycopg2",
    "pymongo",
    # LLM SDKs
    "openai",
    "anthropic",
    "litellm",
    # System
    "sys",
    "ctypes",
    "multiprocessing",
    "threading",
})
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/runtime/test_restrictions.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agent006/runtime/restrictions.py tests/runtime/test_restrictions.py
git commit -m "feat: add unified restrictions.py with blocked modules/calls"
```

---

### Task 2: Add blocked_modules and blocked_calls to CodeActConfig

**Files:**
- Modify: `src/agent006/config/strategy_config.py:6-26`
- Test: `tests/config/test_strategy_config.py` (add tests)

**Step 1: Write the failing test**

```python
# Add to existing test file or create tests/config/test_blocked_config.py
from agent006.config.strategy_config import CodeActConfig
from agent006.runtime.restrictions import DEFAULT_BLOCKED_CALLS, DEFAULT_BLOCKED_MODULES


def test_codeact_config_has_blocked_modules():
    config = CodeActConfig()
    assert config.blocked_modules == DEFAULT_BLOCKED_MODULES


def test_codeact_config_has_blocked_calls():
    config = CodeActConfig()
    assert config.blocked_calls == DEFAULT_BLOCKED_CALLS


def test_codeact_config_blocked_modules_override():
    custom = DEFAULT_BLOCKED_MODULES - {"subprocess"}
    config = CodeActConfig(blocked_modules=custom)
    assert "subprocess" not in config.blocked_modules
    assert "socket" in config.blocked_modules


def test_codeact_config_merge_with_blocked():
    base = CodeActConfig()
    override = CodeActConfig(blocked_modules=frozenset({"subprocess"}))
    merged = base.merge_with(override)
    assert merged.blocked_modules == frozenset({"subprocess"})
    # Non-overridden fields keep defaults
    assert merged.max_iterations == 50
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_blocked_config.py -v`
Expected: FAIL — `blocked_modules` attribute doesn't exist

**Step 3: Write the implementation**

In `src/agent006/config/strategy_config.py`, add two fields to `CodeActConfig`:

```python
from agent006.runtime.restrictions import DEFAULT_BLOCKED_CALLS, DEFAULT_BLOCKED_MODULES

class CodeActConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_iterations: int = 50
    max_retries: int = 3
    cell_timeout: float = 600.0
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tool_calls: int | None = None
    blocked_modules: frozenset[str] = DEFAULT_BLOCKED_MODULES
    blocked_calls: dict[str, frozenset[str]] = DEFAULT_BLOCKED_CALLS
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_blocked_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agent006/config/strategy_config.py tests/config/test_blocked_config.py
git commit -m "feat: add blocked_modules/blocked_calls to CodeActConfig"
```

---

### Task 3: Add exec_globals field to ValidationContext

**Files:**
- Modify: `src/agent006/runtime/code_validator.py:62-71`
- Modify: `src/agent006/runtime/actor.py:709-718`

**Step 1: Write the failing test**

```python
# tests/runtime/test_validation_context.py
from agent006.runtime.code_validator import ValidationContext


def test_validation_context_has_exec_globals():
    ctx = ValidationContext()
    assert ctx.exec_globals == {}


def test_validation_context_accepts_exec_globals():
    globs = {"foo": 42}
    ctx = ValidationContext(exec_globals=globs)
    assert ctx.exec_globals is globs
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/runtime/test_validation_context.py -v`
Expected: FAIL — `exec_globals` not a field on ValidationContext

**Step 3: Write the implementation**

In `src/agent006/runtime/code_validator.py`, add field to `ValidationContext` (around line 71):

```python
@dataclass
class ValidationContext:
    """Shared context for all validators."""

    code: str = ""
    agent_class: type | None = None
    available_names: set[str] = field(default_factory=set)
    importable_modules: set[str] = field(default_factory=set)
    forbidden_self_calls: set[str] = field(default_factory=set)
    execution_count: int = 1
    agent: Any = None
    exec_globals: dict[str, Any] = field(default_factory=dict)
```

In `src/agent006/runtime/actor.py`, pass exec_globals when constructing ValidationContext (around line 709):

```python
context = ValidationContext(
    code=code,
    agent_class=type(self.agent),
    available_names=set(exec_globals.keys()),
    importable_modules=importable_modules,
    forbidden_self_calls=forbidden_self_calls,
    execution_count=execution_count,
    agent=self.agent,
    exec_globals=exec_globals,
)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/runtime/test_validation_context.py -v`
Expected: PASS

**Step 5: Run existing tests to check no regressions**

Run: `uv run pytest tests/runtime/test_code_validator.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/agent006/runtime/code_validator.py src/agent006/runtime/actor.py tests/runtime/test_validation_context.py
git commit -m "feat: add exec_globals to ValidationContext"
```

---

### Task 4: Implement BlockingCallValidator

**Files:**
- Modify: `src/agent006/runtime/code_validator.py` — add new class, replace AsyncSafetyValidator
- Test: `tests/runtime/test_blocking_call_validator.py`

This is the largest task. Split into sub-steps.

**Step 1: Write failing tests for module resolution**

```python
# tests/runtime/test_blocking_call_validator.py
import ast
import subprocess
import time
import types

import pytest

from agent006.runtime.code_validator import (
    BlockingCallValidator,
    ValidationContext,
    ValidationError,
    UnifiedCodeValidator,
)
from agent006.runtime.restrictions import DEFAULT_BLOCKED_CALLS, DEFAULT_BLOCKED_MODULES


def make_context(**kwargs):
    """Helper to build a ValidationContext with exec_globals."""
    return ValidationContext(**kwargs)


class TestFullyBlockedModules:
    """Tests for modules in DEFAULT_BLOCKED_MODULES."""

    def test_reject_subprocess_run(self):
        code = "subprocess.run(['ls'])"
        ctx = make_context(exec_globals={"subprocess": subprocess})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) > 0
        assert "subprocess" in issues[0].message

    def test_reject_subprocess_aliased(self):
        code = "sp.run(['ls'])"
        ctx = make_context(exec_globals={"sp": subprocess})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) > 0

    def test_reject_subprocess_function_directly_imported(self):
        code = "run(['ls'])"
        ctx = make_context(exec_globals={"run": subprocess.run})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) > 0

    def test_reject_socket_connect(self):
        import socket
        code = "sock.connect(('localhost', 80))"
        ctx = make_context(exec_globals={"sock": socket})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) > 0


class TestPartiallyBlockedCalls:
    """Tests for specific calls on allowed modules."""

    def test_reject_time_sleep(self):
        code = "time.sleep(5)"
        ctx = make_context(exec_globals={"time": time})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) > 0
        assert "sleep" in issues[0].message

    def test_allow_time_time(self):
        code = "time.time()"
        ctx = make_context(exec_globals={"time": time})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) == 0

    def test_allow_time_monotonic(self):
        code = "time.monotonic()"
        ctx = make_context(exec_globals={"time": time})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) == 0

    def test_reject_os_system(self):
        import os
        code = "os.system('ls')"
        ctx = make_context(exec_globals={"os": os})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) > 0

    def test_allow_os_path_join(self):
        import os
        code = "os.path.join('a', 'b')"
        ctx = make_context(exec_globals={"os": os})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) == 0

    def test_reject_asyncio_run(self):
        import asyncio
        code = "asyncio.run(coro())"
        ctx = make_context(exec_globals={"asyncio": asyncio})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) > 0


class TestLocalVariableTracking:
    """Tests for instance methods on locally-created objects."""

    def test_reject_thread_join(self):
        import threading
        code = "t = threading.Thread(target=fn)\nt.join()"
        ctx = make_context(exec_globals={"threading": threading})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) > 0
        assert "join" in issues[0].message

    def test_reject_lock_acquire(self):
        import threading
        code = "lock = threading.Lock()\nlock.acquire()"
        ctx = make_context(exec_globals={"threading": threading})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) > 0

    def test_allow_str_join(self):
        code = "','.join(['a', 'b'])"
        ctx = make_context(exec_globals={})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) == 0


class TestAllowedPatterns:
    """Patterns that should NOT be blocked."""

    def test_allow_asyncio_sleep(self):
        import asyncio
        code = "await asyncio.sleep(1)"
        ctx = make_context(exec_globals={"asyncio": asyncio})
        validator = BlockingCallValidator()
        tree = ast.parse(code, mode="eval")  # won't work, need async wrapper
        # Wrap in async function for valid AST
        wrapped = "async def _():\n    await asyncio.sleep(1)"
        issues = validator.validate(ast.parse(wrapped), ctx)
        assert len(issues) == 0

    def test_allow_asyncio_gather(self):
        import asyncio
        code = "async def _():\n    await asyncio.gather(a(), b())"
        ctx = make_context(exec_globals={"asyncio": asyncio})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) == 0

    def test_allow_json_loads(self):
        import json
        code = "json.loads(data)"
        ctx = make_context(exec_globals={"json": json})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) == 0

    def test_allow_unresolvable_call(self):
        code = "unknown_func()"
        ctx = make_context(exec_globals={})
        validator = BlockingCallValidator()
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) == 0


class TestConfigOverride:
    """Tests that config-provided blocked sets are respected."""

    def test_custom_blocked_modules(self):
        code = "subprocess.run(['ls'])"
        # Not blocked if removed from blocked_modules
        ctx = make_context(
            exec_globals={"subprocess": subprocess},
        )
        validator = BlockingCallValidator(
            blocked_modules=frozenset(),
            blocked_calls={},
        )
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) == 0

    def test_custom_blocked_calls(self):
        code = "time.sleep(5)"
        ctx = make_context(exec_globals={"time": time})
        # Remove sleep from blocked calls
        validator = BlockingCallValidator(
            blocked_modules=DEFAULT_BLOCKED_MODULES,
            blocked_calls={},
        )
        issues = validator.validate(ast.parse(code), ctx)
        assert len(issues) == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/runtime/test_blocking_call_validator.py -v`
Expected: FAIL — `BlockingCallValidator` doesn't exist

**Step 3: Implement BlockingCallValidator**

In `src/agent006/runtime/code_validator.py`, add the new class. Replace `AsyncSafetyValidator` in `UnifiedCodeValidator.__init__`.

The implementation needs:

1. **`_resolve_module(node, exec_globals)`** — resolve AST name to module name via exec_globals lookup.

2. **`_BlockingCallVisitor`** — AST visitor that:
   - For each `Call` node, resolves the call target's module.
   - Checks against `blocked_modules` (fully blocked).
   - Checks against `blocked_calls` (partially blocked — specific function name).
   - Tracks local variables assigned from constructors on blocked-call modules (for `Thread.join` etc.).
   - When a method call on a tracked local var matches a dotted entry in `blocked_calls`, rejects it.

3. **Constructor accepts `blocked_modules` and `blocked_calls`** so config can override defaults.

Key implementation details:

```python
class BlockingCallValidator:
    """Validates code for blocking calls that would freeze the event loop.

    Resolves AST names against exec_globals to determine module of origin.
    Replaces AsyncSafetyValidator with runtime-aware name resolution instead
    of string matching.
    """

    def __init__(
        self,
        blocked_modules: frozenset[str] | None = None,
        blocked_calls: dict[str, frozenset[str]] | None = None,
    ):
        from agent006.runtime.restrictions import DEFAULT_BLOCKED_CALLS, DEFAULT_BLOCKED_MODULES

        self.blocked_modules = blocked_modules if blocked_modules is not None else DEFAULT_BLOCKED_MODULES
        self.blocked_calls = blocked_calls if blocked_calls is not None else DEFAULT_BLOCKED_CALLS

    def validate(self, tree: ast.AST, context: ValidationContext) -> list[ValidationIssue]:
        visitor = _BlockingCallVisitor(
            exec_globals=context.exec_globals,
            blocked_modules=self.blocked_modules,
            blocked_calls=self.blocked_calls,
        )
        visitor.visit(tree)
        return visitor.issues
```

For `_BlockingCallVisitor`, the core logic in `visit_Call`:

```python
def visit_Call(self, node: ast.Call) -> Any:
    # Get the function being called and its receiver
    if isinstance(node.func, ast.Attribute):
        # e.g., subprocess.run(), time.sleep(), t.join()
        module_name = self._resolve_module(node.func.value)
        call_name = node.func.attr

        if module_name:
            # Check fully blocked modules
            if module_name in self.blocked_modules:
                self._add_issue(node, module_name, call_name)
                return self.generic_visit(node)

            # Check partially blocked calls
            if module_name in self.blocked_calls:
                if call_name in self.blocked_calls[module_name]:
                    self._add_issue(node, module_name, call_name)
                    return self.generic_visit(node)

        # Check local variable tracking (for Thread.join, Lock.acquire etc.)
        if isinstance(node.func.value, ast.Name):
            var_name = node.func.value.id
            if var_name in self.tracked_locals:
                module_name, class_name = self.tracked_locals[var_name]
                dotted = f"{class_name}.{call_name}"
                if module_name in self.blocked_calls and dotted in self.blocked_calls[module_name]:
                    self._add_issue(node, module_name, dotted)

    elif isinstance(node.func, ast.Name):
        # e.g., run(['ls']) where run = subprocess.run
        obj = self.exec_globals.get(node.func.id)
        if obj is not None:
            obj_module = getattr(obj, "__module__", None)
            if obj_module and obj_module in self.blocked_modules:
                self._add_issue(node, obj_module, node.func.id)
            elif obj_module and obj_module in self.blocked_calls:
                fn_name = getattr(obj, "__name__", node.func.id)
                if fn_name in self.blocked_calls[obj_module]:
                    self._add_issue(node, obj_module, fn_name)

    self.generic_visit(node)
```

For local variable tracking in `visit_Assign`:

```python
def visit_Assign(self, node: ast.Assign) -> Any:
    # Track: t = threading.Thread(...) → tracked_locals["t"] = ("threading", "Thread")
    if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
        module_name = self._resolve_module(node.value.func.value)
        if module_name and module_name in self.blocked_calls:
            class_name = node.value.func.attr
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.tracked_locals[target.id] = (module_name, class_name)
    self.generic_visit(node)
```

**Step 4: Replace AsyncSafetyValidator in UnifiedCodeValidator**

In `UnifiedCodeValidator.__init__` (line 1208), change:

```python
# Before:
AsyncSafetyValidator(),
# After:
BlockingCallValidator(),
```

The `BlockingCallValidator` needs to accept config from the strategy. Add an optional parameter to `UnifiedCodeValidator.__init__`:

```python
def __init__(self, blocked_modules=None, blocked_calls=None):
    self.validators = [
        SecurityValidator(),
        BlockingCallValidator(blocked_modules=blocked_modules, blocked_calls=blocked_calls),
        ClassAssignmentValidator(),
    ]
```

And in `actor.py` where `UnifiedCodeValidator()` is instantiated (line 720), pass the config:

```python
validator = UnifiedCodeValidator(
    blocked_modules=config.blocked_modules,
    blocked_calls=config.blocked_calls,
)
```

This requires getting `config` from the strategy at that point. Check how `cell_timeout` is accessed — it's available via `self.config` on the strategy, which is passed to execute_code. The exact threading of config through to the validator instantiation needs to follow the existing pattern for `cell_timeout`.

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/runtime/test_blocking_call_validator.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/agent006/runtime/code_validator.py tests/runtime/test_blocking_call_validator.py
git commit -m "feat: add BlockingCallValidator with runtime-aware name resolution"
```

---

### Task 5: Wire config through to validator in actor.py

**Files:**
- Modify: `src/agent006/runtime/actor.py:720-721`
- Modify: `src/agent006/strategies/codeact.py` (where execute_code is called)

**Step 1: Trace how cell_timeout flows from config to execute_code**

Read `src/agent006/strategies/codeact.py:1666` to understand how `self.config.cell_timeout` is passed. Follow the same pattern for `blocked_modules`/`blocked_calls`.

The strategy calls `execute_code()` and passes `timeout=self.config.cell_timeout`. The validator is instantiated inside `execute_code()`. We need to either:
- Pass `blocked_modules`/`blocked_calls` as parameters to `execute_code()`
- Or have `execute_code()` receive the full `CodeActConfig`

Look at the `execute_code` signature to decide. The simpler approach: add `blocked_modules` and `blocked_calls` as optional parameters to `execute_code()`, defaulting to the restrictions.py defaults.

**Step 2: Write a test**

```python
# tests/runtime/test_config_wiring.py
# Integration test: verify that CodeActConfig.blocked_modules is respected end-to-end
import subprocess

import pytest

from agent006.runtime.actor import ActorRuntime
from agent006.runtime.code_validator import UnifiedCodeValidator, ValidationContext, ValidationError


def test_validator_uses_config_blocked_modules():
    """Validator with empty blocked_modules allows subprocess."""
    validator = UnifiedCodeValidator(blocked_modules=frozenset(), blocked_calls={})
    ctx = ValidationContext(exec_globals={"subprocess": subprocess})
    # Should NOT raise — subprocess is not blocked
    validator.validate("subprocess.run(['ls'])", ctx)


def test_validator_uses_default_blocked_modules():
    """Validator with defaults blocks subprocess."""
    validator = UnifiedCodeValidator()
    ctx = ValidationContext(exec_globals={"subprocess": subprocess})
    with pytest.raises(ValidationError):
        validator.validate("subprocess.run(['ls'])", ctx)
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/runtime/test_config_wiring.py -v`
Expected: FAIL until wiring is complete

**Step 4: Implement the wiring**

In `actor.py`, modify the `execute_code` method to accept and pass through blocked config:

```python
# In execute_code(), around line 720
validator = UnifiedCodeValidator(
    blocked_modules=blocked_modules,
    blocked_calls=blocked_calls,
)
```

In `codeact.py`, where `execute_code` is called, pass config values:

```python
result = await runtime.execute_code(
    code,
    ...,
    timeout=self.config.cell_timeout,
    blocked_modules=self.config.blocked_modules,
    blocked_calls=self.config.blocked_calls,
)
```

**Step 5: Run tests**

Run: `uv run pytest tests/runtime/test_config_wiring.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/agent006/runtime/actor.py src/agent006/strategies/codeact.py tests/runtime/test_config_wiring.py
git commit -m "feat: wire blocked_modules/blocked_calls config through to validator"
```

---

### Task 6: Implement exec_globals stripping

**Files:**
- Modify: `src/agent006/runtime/actor.py:628-670` (exec_globals construction)
- Test: `tests/runtime/test_exec_globals_stripping.py`

**Step 1: Write the failing test**

```python
# tests/runtime/test_exec_globals_stripping.py
import subprocess
import time
import types


def strip_blocked_from_exec_globals(
    exec_globals: dict,
    blocked_modules: frozenset[str],
) -> dict:
    """Import the actual function from actor.py once implemented."""
    from agent006.runtime.actor import _strip_blocked_modules
    return _strip_blocked_modules(exec_globals, blocked_modules)


class TestExecGlobalsStripping:
    def test_strips_blocked_module(self):
        globs = {"subprocess": subprocess, "json": __import__("json")}
        result = strip_blocked_from_exec_globals(globs, frozenset({"subprocess"}))
        assert "subprocess" not in result
        assert "json" in result

    def test_strips_aliased_module(self):
        globs = {"sp": subprocess}
        result = strip_blocked_from_exec_globals(globs, frozenset({"subprocess"}))
        assert "sp" not in result

    def test_strips_function_from_blocked_module(self):
        globs = {"run": subprocess.run}
        result = strip_blocked_from_exec_globals(globs, frozenset({"subprocess"}))
        assert "run" not in result

    def test_keeps_non_blocked_module(self):
        globs = {"time": time}
        result = strip_blocked_from_exec_globals(globs, frozenset({"subprocess"}))
        assert "time" in result

    def test_keeps_non_module_objects(self):
        globs = {"x": 42, "name": "hello"}
        result = strip_blocked_from_exec_globals(globs, frozenset({"subprocess"}))
        assert globs == result

    def test_empty_blocked_set_strips_nothing(self):
        globs = {"subprocess": subprocess}
        result = strip_blocked_from_exec_globals(globs, frozenset())
        assert "subprocess" in result
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/runtime/test_exec_globals_stripping.py -v`
Expected: FAIL — `_strip_blocked_modules` doesn't exist

**Step 3: Implement stripping**

In `src/agent006/runtime/actor.py`, add a function and call it during exec_globals construction:

```python
def _strip_blocked_modules(
    exec_globals: dict[str, Any],
    blocked_modules: frozenset[str],
) -> dict[str, Any]:
    """Remove blocked modules and their members from exec_globals."""
    if not blocked_modules:
        return exec_globals
    result = {}
    for name, obj in exec_globals.items():
        module_name = None
        if isinstance(obj, types.ModuleType):
            module_name = obj.__name__
        elif hasattr(obj, "__module__"):
            module_name = getattr(obj, "__module__", None)
        if module_name and module_name in blocked_modules:
            continue
        result[name] = obj
    return result
```

Call it in `execute_code()` after building exec_globals, before validation:

```python
exec_globals = _strip_blocked_modules(exec_globals, blocked_modules)
```

**Step 4: Run tests**

Run: `uv run pytest tests/runtime/test_exec_globals_stripping.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agent006/runtime/actor.py tests/runtime/test_exec_globals_stripping.py
git commit -m "feat: strip blocked modules from exec_globals before code execution"
```

---

### Task 7: Remove AsyncSafetyValidator and update existing tests

**Files:**
- Modify: `src/agent006/runtime/code_validator.py` — remove `AsyncSafetyValidator` and `_AsyncSafetyVisitor`
- Modify: `tests/runtime/test_code_validator.py:563-875` — rewrite `TestAsyncSafetyValidator`
- Modify: `tests/runtime/test_async_deadlock_prevention.py` — update integration tests

**Step 1: Remove AsyncSafetyValidator class**

Delete `AsyncSafetyValidator` (line 338-354) and `_AsyncSafetyVisitor` (lines 357-725) from `code_validator.py`.

**Step 2: Update test_code_validator.py**

Replace `TestAsyncSafetyValidator` (lines 563-875) with tests that verify the same scenarios pass/fail under `BlockingCallValidator`. The key scenarios to preserve:

Rejection tests — must still be rejected:
- `asyncio.run()` and aliases
- `loop.run_until_complete()` and chained
- `loop.run_forever()`
- `run_coroutine_threadsafe()`
- `Future.result()` on submit (note: this may now be runtime-patch only)
- `time.sleep()` and aliases
- `Thread.join()`, `Process.join()`

Allowance tests — must still be allowed:
- `await coroutine()`
- `asyncio.gather()`, `asyncio.create_task()`, `asyncio.sleep()`
- `asyncio.wait()`, `asyncio.wait_for()`
- `asyncio.to_thread()`, `asyncio.wrap_future()`
- `str.join()` (no false positive)

Update each test to pass `exec_globals` with the relevant modules.

**Step 3: Update test_async_deadlock_prevention.py**

These are integration tests that run code through an actual agent. They should still pass since the behavior is the same — just the validator internals changed. Run them and fix any breakage.

**Step 4: Run full test suite**

Run: `uv run pytest tests/runtime/test_code_validator.py tests/runtime/test_async_deadlock_prevention.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/agent006/runtime/code_validator.py tests/runtime/test_code_validator.py tests/runtime/test_async_deadlock_prevention.py
git commit -m "refactor: remove AsyncSafetyValidator, replace with BlockingCallValidator"
```

---

### Task 8: Run full test suite and fix regressions

**Files:**
- Any files that need fixing based on test failures

**Step 1: Run the full test suite**

Run: `uv run pytest tests/ -x --timeout=60 -q`
Expected: All PASS

**Step 2: Fix any failures**

Common issues to watch for:
- Tests that import `AsyncSafetyValidator` directly
- Tests that check for E2XX error codes (now E3XX)
- Tests that rely on specific error message wording
- Integration tests that execute code and expect specific validation behavior

**Step 3: Commit fixes**

```bash
git add -u
git commit -m "fix: resolve test regressions from validator replacement"
```

---

### Task 9: Update exports and documentation

**Files:**
- Modify: `src/agent006/runtime/__init__.py` (if restrictions.py needs exporting)
- Modify: `AGENTS.md` or other docs (if BlockingCallValidator needs documenting)

**Step 1: Export restrictions constants if needed**

Check if any external code needs to import `DEFAULT_BLOCKED_MODULES` etc. At minimum, `CodeActConfig` already imports from `restrictions.py`, so no extra exports needed unless agents need to reference the defaults directly.

**Step 2: Commit**

```bash
git add -u
git commit -m "docs: update exports for blocking call prevention"
```

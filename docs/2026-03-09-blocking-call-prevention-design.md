# Blocking Call Prevention in CodeAct — Design

**Date:** 2026-03-09
**Issue:** [#116](https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/issues/116) — `execute_code()` blocks event loop
**Related:** [!327](https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/merge_requests/327) — restricted imports

## Problem

LLM-generated code in CodeAct can call synchronous blocking functions (e.g. `subprocess.run()`) that freeze the event loop. `asyncio.wait_for` cannot cancel a blocking syscall, so `cell_timeout` is ineffective. The current `AsyncSafetyValidator` catches some patterns (asyncio misuse, `time.sleep()`, `Thread.join()`) via string matching and alias tracking, but misses entire categories of blocking calls — most critically `subprocess.*`, which is the real-world cause of #116.

## Approach

Two complementary mechanisms:

1. **Control what's visible to the agent** via `allow_imports` and configurable `blocked_modules`.
2. **Runtime-aware AST validation** that resolves names against `exec_globals` instead of string matching, catching calls on partially-blocked modules and edge cases.

## Design

### Unified Restrictions File

`src/nemo_oo_agents/runtime/restrictions.py` — single source of truth for all module restrictions, consumed by both the startup import checker (!327) and the runtime validator.

```python
# Default blocked modules — blocks event loop, stripped from exec_globals
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

# Default blocked calls — specific calls blocked on otherwise-allowed modules
DEFAULT_BLOCKED_CALLS: dict[str, frozenset[str]] = {
    "time": frozenset({"sleep"}),
    "os": frozenset({"system", "popen", "wait", "waitpid", "waitid"}),
    "threading": frozenset({"Thread.join", "Lock.acquire", "Event.wait", "Condition.wait"}),
    "multiprocessing": frozenset({"Process.join", "Queue.get", "Queue.put"}),
    "asyncio": frozenset({"run", "run_coroutine_threadsafe"}),
}

# Restricted — require explicit declaration via allow_imports or normal import
RESTRICTED_MODULES: frozenset[str] = frozenset({
    # ... the ~70 modules from !327, superset of DEFAULT_BLOCKED_MODULES
})
```

### Configuration via CodeActConfig

The blocked modules and calls are configurable per-agent/per-method, following the existing Pydantic config pattern with `merge_with()` cascading:

```python
class CodeActConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_iterations: int = 50
    max_retries: int = 3
    cell_timeout: float = 600.0
    # New:
    blocked_modules: frozenset[str] = DEFAULT_BLOCKED_MODULES
    blocked_calls: dict[str, frozenset[str]] = DEFAULT_BLOCKED_CALLS
```

Override per-method when needed:

```python
@strategy(CodeActStrategy(config=CodeActConfig(
    blocked_modules=DEFAULT_BLOCKED_MODULES - {"subprocess"},
)))
async def needs_subprocess(self) -> str:
    """..."""
    ...
```

**`blocked_modules` drives both visibility and validation.** A module in `blocked_modules` is stripped from exec_globals AND rejected by the validator. Removing a module from `blocked_modules` allows it through both layers — provided it's also in `allow_imports`. This ensures one knob controls both mechanisms.

### Import Visibility: `allow_imports`

Single context manager controls what the agent can see. Normal imports are developer-only:

```python
with nemo_oo_agents.allow_imports:
    import json        # Agent can use
    import os          # Agent can use (specific calls still blocked via blocked_calls)
    import requests    # Agent can use

import subprocess      # Developer only — not visible to agent
```

Rules:
- Imports inside `allow_imports` are candidates for exec_globals.
- `blocked_modules` removes from that candidate set. So if `subprocess` is in both `allow_imports` and `blocked_modules`, it's stripped — and the developer gets a startup error: *"subprocess is in blocked_modules — import it outside allow_imports or remove it from blocked_modules in CodeActConfig."*
- Normal imports (outside `allow_imports`) are never in exec_globals. Developer methods can use them freely since they run in their own module scope.
- Restricted modules (!327) still require being imported somewhere — `allow_imports` or normal import. The restriction check validates intentionality.

**Visibility flow:**

```
Developer imports module
  → Is it inside allow_imports?
    → No: developer-only, not in exec_globals
    → Yes: candidate for exec_globals
      → Is it in blocked_modules (from config)?
        → Yes: stripped from exec_globals, startup error
        → No: available to agent in exec_globals
```

### exec_globals Construction

In `actor.py`, after building `exec_globals` from the agent module dict, strip modules that aren't in `allow_imports` or are in `blocked_modules`:

```python
allowed = agent_class._allow_imports          # set of module names from allow_imports block
blocked = strategy.config.blocked_modules     # from CodeActConfig

for name, obj in list(exec_globals.items()):
    module_name = None
    if isinstance(obj, types.ModuleType):
        module_name = obj.__name__
    elif hasattr(obj, "__module__"):
        module_name = obj.__module__

    if module_name is None:
        continue

    # Strip if not in allow_imports OR if in blocked_modules
    if module_name not in allowed or module_name in blocked:
        del exec_globals[name]
```

### BlockingCallValidator (replaces AsyncSafetyValidator)

Second layer — catches calls on partially-blocked modules and edge cases where a blocked module somehow enters scope.

**Core mechanism:** resolve AST names against `exec_globals` to determine module of origin.

```python
def _resolve_module(self, node: ast.expr, exec_globals: dict) -> str | None:
    """Resolve an AST expression to its module name via exec_globals."""
    if isinstance(node, ast.Name):
        obj = exec_globals.get(node.id)
        if isinstance(obj, types.ModuleType):
            return obj.__name__
        return getattr(obj, "__module__", None)
    if isinstance(node, ast.Attribute):
        return self._resolve_module(node.value, exec_globals)
    return None
```

**Validation logic:**

For every `Call` node in the AST:
1. Resolve the call target's module via `exec_globals`.
2. If module is in `blocked_modules` (from config) — reject.
3. If module is in `blocked_calls` (from config) and the specific function/method is in the blocked set — reject.
4. If unresolvable — fall through to local variable tracking (see below).
5. Otherwise — allow.

**Local variable tracking for instance methods:**

The exec_globals resolver can't handle locally-created objects: `t = threading.Thread(); t.join()`. The variable `t` doesn't exist in exec_globals at validation time. To avoid regressing from the existing `AsyncSafetyValidator`, the `BlockingCallValidator` retains lightweight AST tracking for this pattern class:

- Track assignments where the RHS is a constructor call resolvable to a blocked module (e.g., `threading.Thread()` resolves `threading` via exec_globals → tracked).
- When `.join()`, `.acquire()`, `.wait()`, etc. are called on tracked variables, check against `blocked_calls`.

This is a narrow supplement to the exec_globals resolver, not a parallel system. It only activates when the resolver returns `None` for a call target and the call's method name appears in a `blocked_calls` value.

**Dotted entries in `blocked_calls`:**

Entries like `"Thread.join"` in `blocked_calls["threading"]` are matched as follows:
- The AST node `t.join()` has `attr="join"`.
- `t` is tracked as originating from `Thread()` (class name recorded at assignment).
- Match is: `f"{tracked_class}.{attr}"` → `"Thread.join"` → found in `blocked_calls["threading"]`.

For module-level functions like `os.system()`:
- The AST node `os.system()` has `attr="system"`.
- `os` resolves directly via exec_globals.
- Match is: `attr` → `"system"` → found in `blocked_calls["os"]`.

**ValidationContext** gets one new field:

```python
@dataclass
class ValidationContext:
    # ... existing fields ...
    exec_globals: dict[str, Any] = field(default_factory=dict)
```

### Patterns Caught

| Pattern | How it's caught |
|---|---|
| `subprocess.run(["ls"])` | Not in exec_globals (NameError). Validator as backup. |
| `sp.run(["ls"])` (aliased) | Same — alias points to same module object, stripped. |
| `from subprocess import run; run(["ls"])` | `run` stripped (its `__module__` is `subprocess`). |
| `time.sleep(5)` | `time` in exec_globals. Validator: `"sleep"` in `blocked_calls["time"]`. |
| `os.system("ls")` | Validator: `"system"` in `blocked_calls["os"]`. |
| `asyncio.run(coro())` | Validator: `"run"` in `blocked_calls["asyncio"]`. |
| `os.path.join(a, b)` | Validator: `"path.join"` not in `blocked_calls["os"]`. Allowed. |
| `t = threading.Thread(); t.join()` | Local variable tracking: `t` tracked as `Thread`, `"Thread.join"` in `blocked_calls["threading"]`. |
| `lock = threading.Lock(); lock.acquire()` | Local variable tracking: `lock` tracked as `Lock`, `"Lock.acquire"` in `blocked_calls["threading"]`. |

### Unresolvable Patterns (Accepted Gaps)

| Pattern | Why |
|---|---|
| `getattr(x, "run")(...)` | Dynamic attribute access — not resolvable from AST. |
| `globals()["subprocess"]` | Dynamic namespace access. |
| `mod = __import__("subprocess"); mod.run()` | Dynamic import + use in same cell — `mod` not in exec_globals at validation time. |

These require active evasion, not normal LLM coding patterns.

### Runtime Patches (async_safety.py)

Stay as-is. They catch cross-cell `Future.result()` / `Future.exception()` / `concurrent.futures.wait()` / `concurrent.futures.as_completed()` patterns via monkey-patching. These are complementary to AST validation and don't need changes.

### Error Messages

Generic format:
```text
{module}.{call}() blocks the event loop and is not allowed in agent code.
Use an async alternative or an appropriate agent tool.
```

Fix hints only for obvious 1:1 replacements:
- `time.sleep()` → `await asyncio.sleep()`
- `asyncio.run()` → `await coro()`

Exception type: `ValidationError` (unchanged).

New error code series: E3XX.

### What's Removed

- `AsyncSafetyValidator` class
- `_AsyncSafetyVisitor` class and all tracking machinery
- Error codes E201–E207
- Tests rewritten to cover same scenarios under new validator

### Relationship to !327

| Concern | When | Mechanism |
|---|---|---|
| Developer forgot to declare import | Agent startup | !327's restricted imports check |
| Module in `allow_imports` but in `blocked_modules` | Agent startup | Throw: "remove from allow_imports or unblock in config" |
| Module not in `allow_imports` | Code execution | Not in exec_globals (developer-only) |
| Module in `allow_imports` but in `blocked_modules` (via config) | Code execution | Stripped from exec_globals + BlockingCallValidator |
| Specific call blocked on allowed module | Code execution | BlockingCallValidator checks `blocked_calls` |
| Instance method on locally-created object | Code execution | Local variable tracking in BlockingCallValidator |
| Cross-cell Future.result() | Code execution | Runtime patches (async_safety.py) |

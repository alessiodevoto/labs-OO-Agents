# Async Deadlock Patterns in Agent-Generated Code

## The Problem

Agent-generated code runs **inside** an async context (`async def __repl_wrapper__`). The event loop runs on MainThread. If that thread blocks, the event loop stops - deadlock.

## Observed in Production

**Model:** qwen3-80b (observed 3x, Jan 2026)

**Tests affected:** `needle_in_haystack`, `employee_lookup`

**Generated code:**
```python
async def find_negative_sentiment():
    result = await self.call_agent(self.data1, self.data2, self.data3)
    return_result(result)

# THIS LINE DEADLOCKS
asyncio.run_coroutine_threadsafe(find_negative_sentiment(), asyncio.get_event_loop()).result()
```

## Why It Happens

Agent sees "asyncio is imported" and thinks it needs `asyncio.run()` to execute async code.

Reality: **You're already inside `async def`. Just `await` directly.**

## Deadlock Patterns

| Pattern | Why | Fix |
|---------|-----|-----|
| `asyncio.run()` | Can't nest event loops | Block at validation |
| `run_until_complete()` | Can't nest event loops | Block at validation |
| `run_forever()` | Would block event loop | Block at validation |
| `run_coroutine_threadsafe()` | Cross-thread primitive used same-thread | Block at validation |
| `future.result()` | Blocks event loop thread | AST detection + runtime patch |
| `future.exception()` | Blocks event loop thread | Runtime patch |
| `concurrent.futures.wait()` | Blocks event loop thread | Runtime patch |
| `concurrent.futures.as_completed()` | Blocks event loop thread | Runtime patch |

## The Fix: Defense in Depth

### Layer 1: Block Nested Event Loop Patterns

These patterns fail at runtime with "cannot run nested event loop". We block them at validation time with helpful error messages that guide the model to use `await` directly:

```python
# BLOCKED with clear error message:
asyncio.run(my_func())           # "use 'await my_func()' instead"
loop.run_until_complete(coro())  # "use 'await coro()' instead"
loop.run_forever()               # "would block the event loop"
```

### Layer 2: Block `run_coroutine_threadsafe`

This pattern is **never correct** inside an async context. It's a cross-thread primitive being used same-thread. Detected via AST to provide accurate line numbers:

```python
# BLOCKED with clear error message:
asyncio.run_coroutine_threadsafe(coro(), loop)  # "use 'await' directly"
run_coroutine_threadsafe(coro(), loop)          # also caught if imported directly
```

### Layer 3: AST Detection for Future.result()

Regex for `.result()` has false positives. Use AST to detect actual Future usage:

**Patterns detected:**
- Chained: `executor.submit(work).result()`
- Variable: `f = submit(...); f.result()`
- Attribute: `self.future = submit(...); self.future.result()`

**Limitation:** AST analysis is per-code-block. It can't track Futures created in previous `execute_code()` calls. That's why we need Layer 4.

### Layer 4: Runtime Patches (Safety Net)

Patch `concurrent.futures` blocking methods to detect when called from the event loop thread:

```python
import asyncio
import concurrent.futures
from threading import current_thread

def _is_event_loop_thread() -> bool:
    """Check if current thread is running the event loop."""
    try:
        loop = asyncio.get_running_loop()
        if hasattr(loop, "_thread_id"):
            return loop._thread_id == current_thread().ident
        return True
    except RuntimeError:
        return False

def _make_safe_future_method(original_method, method_name: str):
    """Wrap a Future method to prevent deadlock in async context."""
    def safe_method(self, timeout=None):
        if _is_event_loop_thread():
            raise RuntimeError(
                f"Can't call Future.{method_name}() from async context - "
                "this would deadlock. Use 'await asyncio.wrap_future(future)' instead."
            )
        return original_method(self, timeout)
    return safe_method

# Apply patches at import time
concurrent.futures.Future.result = _make_safe_future_method(
    concurrent.futures.Future.result, "result"
)
concurrent.futures.Future.exception = _make_safe_future_method(
    concurrent.futures.Future.exception, "exception"
)
# Similar patches for wait() and as_completed()
```

**What this catches:**
- Futures created in previous code blocks: `self.task = submit(...)` then later `self.task.result()`
- Futures created by developer code but accessed by agent
- Any blocking call from the event loop thread, regardless of who created the Future

## Edge Cases

### Cross-Block Future Access

```python
# Turn 1: Agent creates future
self.background_task = executor.submit(long_running_work)

# Turn 2 (later): Agent tries to get result
result = self.background_task.result()  # BLOCKED by runtime patch
```

AST analysis alone can't catch this - the Future was created in a different code block. The runtime patch catches it.

### Mixed Developer and Agent Code

```python
class MyAgent(Agent):
    def __init__(self):
        # Developer creates a Future
        self.data_loader = executor.submit(load_data)

    # Agent generates this method
    async def process(self):
        data = self.data_loader.result()  # BLOCKED - would deadlock
```

The runtime patch catches this too. Even though the developer created the Future, calling `.result()` from async context would deadlock.

**Developer's fix:**
```python
class MyAgent(Agent):
    def __init__(self):
        self.data_loader = executor.submit(load_data)

    async def get_data(self):
        # Expose async-safe accessor
        return await asyncio.wrap_future(self.data_loader)
```

### Non-Future .result() Methods

```python
class QueryResult:
    def result(self):
        return self._data

obj = QueryResult()
obj.result()  # ALLOWED - not a concurrent.futures.Future
```

The runtime patch only affects `concurrent.futures.Future.result()`, not arbitrary `.result()` methods on other classes.

### Sync Code Called from Async

```python
def sync_helper():  # Developer's sync function
    future = executor.submit(work)
    return future.result()  # Would work in sync context

async def agent_method():
    return sync_helper()  # BLOCKED - sync_helper runs on event loop thread
```

This is **correct behavior**. Even though `sync_helper` was designed for sync contexts, calling it from async context would deadlock. The developer should make it async-aware:

```python
async def async_helper():
    future = executor.submit(work)
    return await asyncio.wrap_future(future)
```

## System Prompt Addition

Tell agents upfront to prevent bad patterns:

```text
You are executing inside `async def`. The event loop is already running.
Use `await` directly. Never use:
- asyncio.run_coroutine_threadsafe()
- future.result() or future.exception()
- concurrent.futures.wait() or as_completed()

For sync work in a thread pool, use:
    result = await loop.run_in_executor(executor, sync_function)
```

## Correct Patterns

```python
# BAD - deadlocks
asyncio.run_coroutine_threadsafe(my_func(), loop).result()

# BAD - deadlocks
future = executor.submit(work)
result = future.result()

# BAD - deadlocks
self.task = executor.submit(work)
# ... later ...
result = self.task.result()

# GOOD - direct await
result = await my_func()

# GOOD - wrap concurrent.futures.Future
future = executor.submit(work)
result = await asyncio.wrap_future(future)

# GOOD - run_in_executor (preferred for sync work)
result = await loop.run_in_executor(executor, sync_function)

# GOOD - gather instead of wait()
results = await asyncio.gather(coro1(), coro2())
```

## Summary

| Layer | What it catches | How |
|-------|-----------------|-----|
| AST check | `asyncio.run()`, `run_until_complete()`, `run_forever()` | Block + clear error |
| AST check | `run_coroutine_threadsafe()` | Block + clear error |
| AST check | `future.result()` in same block | Block + clear error |
| Runtime patch | ALL blocking calls from event loop thread | Block + clear error |
| System prompt | All patterns | Prevent generation |

The runtime patch is the **safety net** - it catches everything the static analysis misses, including cross-block futures and mixed developer/agent code.

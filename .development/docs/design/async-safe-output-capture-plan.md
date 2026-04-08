# Async-Safe stdout/stderr Capture Plan

## Problem Statement

The current execution system has two issues:

1. **Only captures `print()` calls** - Direct `sys.stdout.write()`, `sys.stderr.write()`, `warnings.warn()`, and logging to stderr are lost
2. **Partial output on error is not shown to LLM** - When code fails, any stdout/stderr before the error is not included in the feedback

The current implementation uses a contextvar-based print replacement for async safety:

```python
# Task-local stdout buffer for async-safe capture
_stdout_buffer_var: contextvars.ContextVar[io.StringIO | None] = contextvars.ContextVar(
    "stdout_buffer", default=None
)

def _make_task_local_print():
    def task_local_print(*args, sep=" ", end="\n", file=None, flush=False):
        buffer = _stdout_buffer_var.get()
        if buffer is not None and file is None:
            _original_print(*args, sep=sep, end=end, file=buffer, flush=flush)
        else:
            _original_print(*args, sep=sep, end=end, file=file, flush=flush)
    return task_local_print
```

This approach:
- ✅ Is async-safe (each task gets its own buffer via contextvar)
- ❌ Only intercepts `print()` calls
- ❌ Misses `sys.stdout.write()`, `sys.stderr.write()`, warnings, logging

## Requirements

1. **Capture all output**: stdout AND stderr, including direct `.write()` calls
2. **Async-safe**: Multiple code executions can run in parallel without output mixing
3. **Include partial output on error**: Show stdout/stderr that occurred before an exception
4. **Consistent naming**: Use "Stdout:" and "Stderr:" (or combined "Output:") in LLM feedback

## Solution Options

### Option A: Subprocess Isolation (Recommended)

Run each code block in a subprocess. This provides complete isolation:

```python
import subprocess
import sys

async def execute_code_isolated(code: str, namespace: dict) -> ExecutionResult:
    # Serialize namespace to subprocess
    # Run code in subprocess
    # Capture stdout/stderr from subprocess pipes
    result = subprocess.run(
        [sys.executable, "-c", wrapped_code],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return ExecutionResult(
        stdout=result.stdout,
        stderr=result.stderr,
        error=... if result.returncode != 0 else None,
    )
```

**Pros:**
- Complete output isolation (stdout + stderr)
- True parallel safety - no shared state
- Can set resource limits (memory, CPU)
- Clean process boundary

**Cons:**
- Serialization overhead for namespace (pickle/dill)
- Can't share live objects (agent instances, tools) without IPC
- Higher latency per execution
- Complex to pass back defined methods/locals

**Verdict:** Good for sandboxed/untrusted code, but too heavy for our REPL use case where we need live `self` access.

---

### Option B: Thread + Real Stream Redirection

Run each execution in a thread with actual sys.stdout/stderr redirection:

```python
import threading
import sys
import io

_thread_stdout = threading.local()
_thread_stderr = threading.local()

class ThreadLocalStream:
    def __init__(self, original, attr_name):
        self._original = original
        self._attr_name = attr_name

    def write(self, data):
        buffer = getattr(_thread_stdout, self._attr_name, None)
        if buffer is not None:
            buffer.write(data)
        else:
            self._original.write(data)

    def flush(self):
        buffer = getattr(_thread_stdout, self._attr_name, None)
        if buffer is not None:
            buffer.flush()
        self._original.flush()

# Install once at module load
sys.stdout = ThreadLocalStream(sys.stdout, 'stdout_buffer')
sys.stderr = ThreadLocalStream(sys.stderr, 'stderr_buffer')
```

**Pros:**
- Captures ALL output (print, write, warnings, logging)
- Thread-local storage provides isolation
- No serialization overhead
- Live object access works

**Cons:**
- Asyncio tasks aren't threads - need to run exec() in thread pool
- Adds threading complexity
- Global sys.stdout/stderr replacement affects entire process

**Verdict:** Could work but mixing asyncio + threads adds complexity.

---

### Option C: Contextvar-based Stream Wrapper (Extend Current Approach)

Extend the current contextvar pattern to wrap sys.stdout and sys.stderr:

```python
import sys
import io
import contextvars

_stdout_buffer_var: contextvars.ContextVar[io.StringIO | None] = contextvars.ContextVar(
    "stdout_buffer", default=None
)
_stderr_buffer_var: contextvars.ContextVar[io.StringIO | None] = contextvars.ContextVar(
    "stderr_buffer", default=None
)

class ContextVarStream:
    """Stream wrapper that redirects to contextvar buffer when set."""

    def __init__(self, original: io.TextIOBase, buffer_var: contextvars.ContextVar):
        self._original = original
        self._buffer_var = buffer_var
        # Preserve original stream attributes
        self.encoding = getattr(original, 'encoding', 'utf-8')
        self.errors = getattr(original, 'errors', 'strict')

    def write(self, data: str) -> int:
        buffer = self._buffer_var.get()
        if buffer is not None:
            return buffer.write(data)
        return self._original.write(data)

    def flush(self) -> None:
        buffer = self._buffer_var.get()
        if buffer is not None:
            buffer.flush()
        self._original.flush()

    def fileno(self) -> int:
        return self._original.fileno()

    def isatty(self) -> bool:
        return self._original.isatty()

    # ... other stream methods delegate to _original

# Install once at module load (or in runtime init)
_original_stdout = sys.stdout
_original_stderr = sys.stderr
sys.stdout = ContextVarStream(_original_stdout, _stdout_buffer_var)
sys.stderr = ContextVarStream(_original_stderr, _stderr_buffer_var)
```

**Usage in execute_code:**
```python
async def execute_code(...) -> ExecutionResult:
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    stdout_token = _stdout_buffer_var.set(stdout_buffer)
    stderr_token = _stderr_buffer_var.set(stderr_buffer)

    try:
        # ... execute code ...
        return ExecutionResult(
            stdout=stdout_buffer.getvalue(),
            stderr=stderr_buffer.getvalue(),
            error=None,
            ...
        )
    except Exception as e:
        return ExecutionResult(
            stdout=stdout_buffer.getvalue(),
            stderr=stderr_buffer.getvalue(),
            error=e,
            ...
        )
    finally:
        _stdout_buffer_var.reset(stdout_token)
        _stderr_buffer_var.reset(stderr_token)
```

**Pros:**
- Captures ALL output (print, write, warnings, logging)
- Contextvar provides async-task isolation
- Minimal overhead
- Live object access works
- Extends existing pattern naturally

**Cons:**
- Global sys.stdout/stderr replacement (one-time at init)
- Need to handle all stream methods (fileno, isatty, etc.)
- Some edge cases (subprocess spawn inherits real fd, not wrapper)

**Verdict:** Best balance of completeness and simplicity. Recommended.

---

## Recommended Implementation: Option C

### Changes Required

#### 1. `src/nemo_oo_agents/runtime/actor.py`

- Add `_stderr_buffer_var` contextvar
- Create `ContextVarStream` class that wraps streams
- Install wrappers on `sys.stdout` and `sys.stderr` at module load
- Update `execute_code()` to set both buffers
- Keep task-local print as optimization (avoids wrapper overhead for print())

#### 2. `src/nemo_oo_agents/events.py`

- Add `stderr: str = ""` field to `ExecutionResult`

#### 3. `src/nemo_oo_agents/strategies/codeact.py`

- Update `_format_tool_result()` to include stderr
- Include partial stdout/stderr on error
- Use consistent naming: "Stdout:" and "Stderr:" (or combined)

#### 4. `src/nemo_oo_agents/strategies/pure_python.py`

- Update `_send_execution_error()` to include partial stdout/stderr
- Update `_send_continuation_feedback()` with stderr if present
- Use consistent naming

### Output Format Decision

Options for presenting output to LLM:

**A. Separate sections:**
```
Stdout:
<stdout content>

Stderr:
<stderr content>
```

**B. Combined with labels:**
```
Output:
[stdout] line 1
[stderr] warning message
[stdout] line 2
```

**C. Combined unlabeled (simple):**
```
Output:
<stdout + stderr interleaved by time>
```

**Recommendation:** Option A (separate sections) - clearest for LLM to understand, and we can't reliably interleave by time anyway since we capture to separate buffers.

### Edge Cases to Handle

1. **Nested async tasks** - Each task gets its own contextvar copy, so nested `await` calls are safe
2. **Subprocess calls in code** - `subprocess.run()` writes to real fd, not our wrapper. Document this limitation or capture subprocess output explicitly.
3. **Multiprocessing** - Similar to subprocess, child processes won't use our wrappers
4. **C extensions writing to fd 1/2** - Can't intercept. Document limitation.
5. **warnings.warn()** - Uses `sys.stderr`, so will be captured ✓
6. **logging** - If handler writes to `sys.stderr`, will be captured ✓

### Testing Plan

1. Test `print()` capture (existing)
2. Test `sys.stdout.write()` capture (new)
3. Test `sys.stderr.write()` capture (new)
4. Test `warnings.warn()` capture (new)
5. Test partial output on error - stdout before exception
6. Test partial output on error - stderr before exception
7. Test parallel execution isolation - two concurrent tasks don't mix output
8. Test stream methods work (flush, encoding, etc.)

## Implementation Estimate

- **actor.py changes:** ~50-80 lines for ContextVarStream class + integration
- **events.py changes:** ~2 lines (add stderr field)
- **codeact.py changes:** ~10 lines (update formatting)
- **pure_python.py changes:** ~15 lines (update error/feedback formatting)
- **Tests:** ~100 lines for new test cases

Total: ~200 lines of changes, medium complexity.

# How to Sandbox Your Application

CodeAct runs LLM-generated cells **in the agent's own process, on the agent's own
event loop**. That is fast and the default, but it has two failure modes for
untrusted code:

1. **No enforceable resource bound.** `CodeActConfig.cell_timeout` is applied with
   `asyncio.wait_for`, which can only cancel a coroutine that yields back to the
   loop. A CPU-bound cell (`while True`, a BFS, an `itertools` sweep) never yields,
   so the timeout never fires and the cell runs unbounded (a single cell has run
   **2345 s under a 60 s cap** and returned `success=True`). There is no memory or
   CPU bound at all.
2. **No isolation.** A cell can read any file the process can (`open("/etc/shadow")`,
   your API keys), write anywhere, and open arbitrary network sockets.

Both stem from the same root: **untrusted code shares the trusted process.** The
sandbox backend runs each cell in a **separate worker process** that the parent can
(a) hard-kill and (b) lock down with OS-enforced guards *before the cell runs*.

This guide shows how to turn it on and what changes when you do. It is **opt-in**:
the default `execution_backend="inprocess"` is unchanged. Linux-only (it probes the
host and fails closed elsewhere).

## Turning it on

Set `execution_backend="sandbox"` on the strategy config and describe the guardrails
in a `SandboxConfig`:

```python
from nooa import strategy
from nooa.strategies import CodeActStrategy
from nooa.config.strategy_config import CodeActConfig
from nooa.runtime.sandbox.config import SandboxConfig

@strategy(CodeActStrategy(config=CodeActConfig(
    cell_timeout=60.0,                 # becomes a HARD bound under the sandbox
    execution_backend="sandbox",
    sandbox=SandboxConfig(
        workspace="/work/arc_runs",    # the one dir cells may read+write
        network=False,                 # block the internet
        max_memory_mb=4096,
        max_cpu_seconds=120,
    ),
)))
async def solve(self): ...
```

Nothing else about your agent changes: the `execute_python` middleware pipeline and
the `before/after_code_execution` hooks still fire, and events/tracing stay on the
parent. Only the cell body moves into the worker.

## What it enforces

Four independently-toggleable guardrails, each enforced by the **kernel** in the
child, installed *after fork and before any cell bytecode runs*:

| Guardrail | Mechanism | Verified behaviour (kernel 6.8, Landlock ABI 4) |
|---|---|---|
| **Timeout** | parent `wait_for(recv, cell_timeout + grace)` → `SIGTERM`→`SIGKILL` → restart worker | `while True` killed at the deadline; parent survives; next cell runs |
| **CPU cap** | `RLIMIT_CPU` (`soft==hard`) | spin loop → `SIGXCPU`/`SIGKILL` near the cap |
| **Memory cap** | `RLIMIT_AS` (`soft==hard`) + parent RSS poll | `bytearray(1 GiB)` under a 256 MiB cap → clean `MemoryError` |
| **Filesystem** | Landlock path-beneath rules, default-deny | read/write outside the allow-list → `PermissionError`; inside → OK |
| **Network** | seccomp-BPF: `socket(AF_INET/AF_INET6)` → `EACCES` | inet socket/connect → `PermissionError`; `AF_UNIX` still works |

The timeout guardrail has **no field of its own** — it reuses `cell_timeout` and
turns that (previously advisory) value into a hard bound
(`hard_deadline = cell_timeout + sandbox.timeout_grace_s`).

Why these and not namespaces: unprivileged network/mount namespaces (`unshare`) are
blocked by many container seccomp policies, so they aren't a reliable primitive.
seccomp + Landlock + rlimits are **unprivileged, self-imposed, and irrevocable**, and
work inside containers. Each is installed by the child on itself with raw `ctypes`
syscalls — **no new dependencies**. Once installed, arbitrary Python in the cell —
even `import ctypes` — cannot lift them (`no_new_privs` + seccomp, Landlock
`restrict_self`, `setrlimit` with `soft==hard`).

**Fail-closed.** At executor start the sandbox probes what the host can enforce; if
`require=True` (the default) and a requested guard is unenforceable, it raises
`SandboxUnavailable` instead of running the cell unguarded.

## Configuration reference

```python
from typing import Literal
from pydantic import BaseModel

FileAccess = Literal["read", "read_write"]

class FileRule(BaseModel):            # frozen
    path: str
    access: FileAccess = "read"

class SandboxConfig(BaseModel):       # frozen
    # guardrail 3: filesystem
    filesystem: bool = True                       # enforce Landlock at all
    workspace: str | None = None                  # a dir cells get read+write to
    allow: tuple[FileRule, ...] = ()              # extra explicit allow rules
    system_paths: bool = True                     # auto-allow read of interpreter/libs
    # guardrail 4: network
    network: bool = False                         # False → internet blocked
    # guardrail 2: memory + cpu
    max_memory_mb: int = 0                         # 0 = disabled
    max_cpu_seconds: int = 0                       # 0 = disabled
    rss_poll_s: float = 0.25                       # parent RSS watchdog interval
    # guardrail 1: timeout — reuses CodeActConfig.cell_timeout; this is only the grace
    timeout_grace_s: float = 2.0
    # brokered self.* calls run parent-side while the worker idles; their time must
    # NOT consume the cell deadline. They get their own bound instead (0 = unbounded).
    broker_timeout_s: float = 300.0
    # process / recovery
    start_method: Literal["fork"] = "fork"         # fork only (worker inherits the live agent)
    recovery: Literal["restart_empty", "disabled"] = "restart_empty"
    require: bool = True                            # fail-closed if a guard is unenforceable
    context_block: bool = True                     # inject the constraints context block

class CodeActConfig(BaseModel):
    ...
    cell_timeout: float | None = None
    execution_backend: Literal["inprocess", "sandbox"] = "inprocess"
    sandbox: SandboxConfig = SandboxConfig()
```

## The context block

When `context_block` is on, the strategy injects one block each turn describing the
**active** constraints (a disabled guard is omitted), wrapped in a
`<sandbox>…</sandbox>` envelope, so the model adapts:

```
<sandbox>
Your code runs in an isolated process with enforced limits:
- Wall-clock: each cell is hard-killed 60s after it starts. Keep cells short; return partial results.
- CPU: 120 CPU-seconds per cell. Memory: 4096 MB per cell (allocation past this raises MemoryError).
- Filesystem: writable path(s): /work/arc_runs. Reads/writes elsewhere raise PermissionError.
- Network: disabled. Opening a socket to the internet raises PermissionError; do not attempt downloads.
- Values returned from a cell must be picklable (numbers, str, list, dict, ndarray). Keep live
  objects in the namespace and return a summary instead.
</sandbox>
```

## What changes for a cell

Because `self` in a cell is a proxy over the parent's live agent and the worker owns
the REPL namespace, a few in-process behaviours change under the sandbox — all
surfaced to the model in the `<sandbox>` block:

- **`self.<attr>` returns a fresh copy**, so in-place mutation (`self.items.append(x)`)
  mutates a throwaway and is not persisted. Call a method (`self.record(x)`) or
  reassign (`self.items = self.items + [x]`, which brokers a `__setattr__` to the
  parent). Attribute *assignment* and container ops on a non-picklable live attribute
  (`self.context["k"] = v`, `self.memory.remember(x)`) are brokered; in-place mutation
  of a *fetched copy* is not.
- **Mutating non-`self` module-level state fails loud.** Module globals a cell reaches
  are frozen read-only views; assigning through them raises `SandboxStateError` rather
  than silently diverging (the change would die with the worker and never reach the
  parent).
- **`return_result` must pass the value**, not a variable-name string — the parent has
  no view of the worker's cell locals to resolve a bare identifier.
- **`Out[n]` history and caller-seeded `session_locals` are not present** in the worker
  namespace (they live on the parent); recompute rather than reference `Out[-1]`.
- **A hard timeout / OOM kill resets the worker's namespace** (variables/functions from
  earlier cells are gone). The synthesized error says so; `recovery="restart_empty"`
  (default) restarts fresh, `recovery="disabled"` stops the backend.

Not supported: `deny` sub-trees (allowlist-only filesystem) and the `spawn` start
method (`fork` only, since the worker inherits the live agent).

## How it works

```
Parent (agent event loop)                     Child worker (one long-lived process)
--------------------------                    -------------------------------------
CodeActStrategy.execute()
  creates SandboxedExecutor (lazy, 1st cell)
  fork worker ─────────────────────────────►  install_guards(spec):   [runs ONCE, before any cell]
                                                 setrlimit(AS, CPU)
                                                 landlock_restrict_self(file rules)
                                                 seccomp(no AF_INET)     ← irrevocable from here on
                                                 build persistent namespace; self→ParentAgentProxy
  runtime.execute_code(code, sandbox=exec)
    → executor.run_cell(code, timeout) ──────►  run_cell_source(code, ns)
       await wait_for(recv, timeout+grace)          exec cell; capture stdout/return/signal
         self.method(x) ◄──── RPC {tool_call} ──    self.method(x) ──► RPC to the parent's live agent
       ◄── {ok, result DTO, stdout, …}           ◄── IPC-safe DTO (picklable subset)
    on timeout: terminate→kill→restart(recovery); synth ExecutionResult(error=CellTimeoutError)
```

**Key inversion:** in the child, `self` is a `ParentAgentProxy`. Every `self.tool(...)`
RPCs to the parent's **live** agent, so agent state stays authoritative and the child
never needs network or the LLM (which seccomp blocks anyway). Cell-local state
(functions defined in cell 1, used in cell 3) lives in the child and never crosses the
boundary — only explicitly returned / snapshotted picklable values do.

### Module map

| File | Role |
|---|---|
| `runtime/sandbox/config.py` | `SandboxConfig`, `FileRule`, enums (pydantic, frozen) |
| `runtime/sandbox/capabilities.py` | probe what the host can enforce; power fail-closed + test skips |
| `runtime/sandbox/guards.py` | `ctypes` primitives: `apply_rlimits`, `apply_landlock`, `apply_seccomp_no_inet`, `install_guards` |
| `runtime/sandbox/cell_core.py` | `run_cell_source(...)` — wrapper/compile/exec/capture core |
| `runtime/sandbox/worker.py` | child entrypoint: `install_guards`, op loop, `ParentAgentProxy` |
| `runtime/sandbox/executor.py` | parent `SandboxedExecutor`: fork/restart, hard-timeout, tool broker, RSS watchdog, IPC |
| `runtime/sandbox/readonly.py` | `SandboxStateError`, read-only module-state views |
| `runtime/sandbox/serialization.py` | IPC-safe result DTO, error surrogate, picklability checks |
| `runtime/sandbox/context_block.py` | render active constraints into the agent-facing block |
| `runtime/sandbox/errors.py` | `SandboxError`, `SandboxUnavailable`, `CellTimeoutError`, `CellMemoryError`, `CellSerializationError`, `WorkerDiedError` |

## The serialization boundary

`ExecutionResult` has non-picklable fields; the IPC contract is:

- `defined_methods`, `captured_locals` — **stay in the child** (the persistent
  namespace). The parent gets empty dicts; the values remain reachable by name in
  later cells.
- `returned_value` — if picklable, crosses; else replaced with a
  `CellSerializationError` carrying a clear "return a summary instead" message.
- `error` — reduced to a picklable surrogate (type name, message, formatted
  traceback) and re-raised as a lightweight exception on the parent, with
  `wrapper_line_offset` preserved so tracebacks point at the right cell line.
- `signal` (`return_result`) — marshaled as a picklable record and re-raised as the
  signal on the parent.
- `images` (`show()`) — already dicts, picklable.
- Tool-call args/return values must be picklable; a clear `CellSerializationError` is
  raised otherwise.

## Why leakage can't happen

1. **Ordering.** `install_guards()` runs as the first thing in the worker, before the
   op loop, before any cell — there is no window in which cell code runs unguarded.
2. **Irrevocability.** seccomp (with `no_new_privs`) and Landlock (`restrict_self`)
   cannot be relaxed by the restricted process; `setrlimit` with `soft==hard` cannot
   be raised. Arbitrary cell code cannot remove a guard.
3. **Fail-closed.** With `require=True`, an unenforceable requested guard raises
   `SandboxUnavailable` rather than running the cell unguarded.
4. **Backstop.** If the child ignores an in-child interrupt or wedges in a C call, the
   parent's `wait_for` + `SIGKILL` terminates it regardless.

Each link is covered by paired tests under `tests/runtime/sandbox/`: a leak test shows
the exploit succeeding *without* the guard, and a paired test shows the *same* exploit
raising under it. The default `inprocess` suite stays green — the delegation branch is
only taken when the sandbox is enabled.

## Composability

The per-cell sandbox composes with a whole-process OS sandbox (e.g. a uid-drop or
namespace wrapper around the agent process): the worker is just another process inside
that outer boundary. The two are orthogonal — the per-cell sandbox contains *cell
actions*; a whole-process sandbox contains the *agent process itself*.

# Proposal: Agent State Serialization

## Motivation

NeMo OO Agents agents are stateful Python objects. Over a multi-turn conversation an agent accumulates event history, context blocks that shape the system prompt, LLM-generated methods bound to `self`, and instance attributes set during code execution. Today, all of this lives in-process memory. If the process ends — whether intentionally (stateless HTTP server returning a response) or unintentionally (crash, timeout, OOM) — the agent's state is gone.

We propose a unified persistence strategy: a single **`StorageManager`** interface that centralizes all agent storage — event streaming, snapshots, and restoration. Users pass one object to the agent and get full persistence. Internally, events are still streamed (append-only, immutable, unbounded) while context/methods/attributes are snapshotted (mutable, small, checkpointed on demand), but the user doesn't need to think about this split.

## What We Persist vs. What We Don't

```
┌─────────────────────────────────────────────────────────┐
│                    Agent State                          │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  PERSISTED (via StorageManager)                         │
│  ┌───────────────────────────────────────────────┐      │
│  │ 1. Events         — Full conversation history │      │
│  │ 2. Context Blocks  — System prompt state      │      │
│  │ 3. Defined Methods — LLM-generated code       │      │
│  │ 4. Instance Attrs  — Runtime-set self.x = ... │      │
│  └───────────────────────────────────────────────┘      │
│                                                         │
│  NOT PERSISTED                                          │
│  ┌───────────────────────────────────────────────┐      │
│  │ 5. Session locals  — REPL vars (per-call)     │      │
│  │ 6. Event handlers  — Runtime callbacks         │      │
│  │ 7. LLM client      — Constructor config        │      │
│  │ 8. Agent identity   — UUID, regenerated        │      │
│  │ 9. Runtime internals — Locks, gen context      │      │
│  └───────────────────────────────────────────────┘      │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

The intent here is provide enough persistence support for users of NeMo OO Agents to serialize and load agents. We want to maintain the design of NeMo OO Agents as a library, i.e., we want to leave control of where to store agent info, how often to checkpoint, etc. to users where possible.  We want how we serialize and load objects to look Pythonic, so that this can fit easily into a larger strategy of how to store/move/load overall application state between users.

### Why persist 1–4?

These are the things the LLM depends on to continue a conversation coherently:

1. **Events** give the LLM its conversation history — what was said, what code ran, what results came back. Without events, the LLM starts from scratch.

2. **Context blocks** shape the system prompt. When agent code does `self.context["plan"] = "Step 1: ..."`, that block appears in every subsequent LLM call. Losing it means the LLM loses its working notes.

3. **Defined methods** are the LLM's reusable tools. When it writes `def analyze(self, data):` with a `@strategy` decorator, that method becomes callable in future turns. Losing it means the LLM must regenerate the implementation.

4. **Instance attributes** are the LLM's scratch space on `self`. When it writes `self.results = [...]`, that data is available in later method calls. Losing it means the LLM loses its accumulated work products.

### Why not persist 5–9?

5. **Session locals** (`x = 42` from one code cell used as `x + 1` in the next) live within a single generation session — the strategy's `while not session.is_exhausted()` loop. When the method call completes, the session object is garbage collected and locals vanish. Since the expected restore boundary is between method calls, session locals are already empty. The PythonOutput events capture a text summary of captured variables (e.g., "Variables now in scope: x (int), df (DataFrame)"), giving the LLM context to reconstruct if needed.

6. **Event handlers** are runtime callbacks (`event_manager.on("message", fn)`). They're set up by application code during initialization, not by the LLM. The application re-registers them when it creates the restored agent.

7. **LLM client, config, formatters** are constructor arguments. The caller provides them again when constructing the restored agent — possibly with different settings (new API key, different model, etc.).

8. **Agent identity** is a UUID regenerated on each `__init__`. The restored agent is conceptually the same conversation but a new runtime instance.

9. **Runtime internals** (generation lock, execution namespace, in-flight context) are inherently transient.

## Serialization options + suggestion

The big challenge in serializing NeMo OO Agents agents is the mixture of metaprogramming and traditional programming. State is a mixture of objects implemented by developers (EventManager, ContextManager) and objects generated by the LLM at runtime.

There's a few methods of serialization that could work:

- *Swappable backends.* This is what EventManager already implements. An InMemoryBackend already encapsulates Events. We can supply new implementations to connect to Redis, SQL, etc. Users then specify which backends to use. This is effectively a dependency injection based approach.
- *Pickle-ish.*  Serialize agents as a single blob of data, and then users are responsible for storing/retrieving as needed. This is what https://gitlab-master.nvidia.com/interactive-agents/nooa/-/merge_requests/367 implements, a single Pydantic created JSON can be generated at will. This is a manual checkpoint/deserialization approach.
- *Agent as Pydantic model.* Make the Agent class itself a Pydantic `BaseModel`, getting serialization for free via `model_dump()` / `model_validate()`. This doesn't work in practice: Agent uses a custom metaclass (`AgentMeta`) that conflicts with Pydantic's `ModelMetaclass`, the constructor holds non-serializable runtime objects (`UnifiedLLM` client, `ActorRuntime` with circular references), and LLM-defined methods and attributes are dynamically bound callables that aren't Pydantic fields.

Our proposal: a single `StorageManager` interface that unifies event streaming and state snapshotting behind one object. Internally, the implementation decides how to persist each kind of state — streaming events to a database, writing snapshots to files or Redis, etc. — but the user sees one interface.

### StorageManager: one interface for all persistence

`StorageManager` is passed to the agent at construction time. It owns the `EventBackend` (for streaming events) and provides `save_snapshot()` / `load_snapshot()` (for everything else). The agent delegates all persistence through this single object.

```python
from nooa import StorageManager

storage = PostgresStorageManager(connection_string="postgresql://...")
agent = MyAgent(storage=storage)
```

On save:

```python
snapshot_id = agent.save()  # delegates to storage.save_snapshot()
```

On restore:

```python
agent = MyAgent.load(snapshot_id, storage=storage)
# Events come from storage's event backend, rest from the snapshot
```

Constructor args (`llm=`, `context=`, etc.) still work alongside `load()` — constructor-provided values take precedence over restored state. This means you can restore a conversation but swap the model:

```python
agent = MyAgent.load(snapshot_id, storage=storage, llm=new_model)
```

## How Users Use This

Users pass a `StorageManager` to the agent. Events stream automatically. Snapshots are created explicitly via `agent.save()`. Restoration is done via `MyAgent.load()`.

### Stateless HTTP server (AgentHub pattern)

Each request uses the same storage to stream events and load/save snapshots:

```python
from nooa import Agent
from nooa_persistence import PostgresStorageManager

storage = PostgresStorageManager("postgresql://...")

@app.post("/chat/{session_id}")
async def chat(session_id: str, message: str):
    # Load agent — events and snapshot both come from storage
    agent = MyAgent.load(session_id, storage=storage)

    # Process the new message — agent has full context
    response = await agent.chat(message)

    # Save updated snapshot
    agent.save()

    return {"response": response}
```

### Periodic checkpointing in a long-running process

Events stream to storage automatically. Snapshot periodically so context/methods/attrs aren't lost on crash:

```python
storage = PostgresStorageManager("postgresql://...")
agent = MyAgent(storage=storage)

for i, task in enumerate(tasks):
    result = await agent.process(task)

    # Snapshot every 10 tasks (events are already persisted)
    if i % 10 == 0:
        agent.save()
```

### Session handoff between workers

Both workers use the same storage. Only the snapshot ID needs to be transferred:

```python
# Worker A: snapshot and publish the ID
snapshot_id = agent.save()
await queue.publish("handoff", snapshot_id)

# Worker B: pick up snapshot ID, restore from shared storage
snapshot_id = await queue.consume("handoff")
agent = MyAgent.load(snapshot_id, storage=shared_storage)
await agent.continue_work()
```

### In-memory usage (default, no persistence)

When no `StorageManager` is provided, agents work exactly as they do today — everything lives in-process memory, nothing is persisted:

```python
agent = MyAgent(llm=llm)
# Works exactly as before — no persistence, no behavioral change
```

### What we're NOT providing

- **No automatic snapshotting.** There's no `agent.autosave(path)` or `@on_turn_end` persistence trigger. Users call `agent.save()` explicitly when they want a snapshot. Events stream automatically, but the snapshot is manual.

- **No concurrency control.** If two processes connect to the same storage and diverge, we don't detect or merge conflicts. That's the application's responsibility.

- **No built-in persistent StorageManagers (yet).** We provide the `StorageManager` protocol and the `InMemoryStorageManager` default. Persistent implementations (Postgres, SQLite, file-based) are a separate package concern. We may ship reference implementations later.

## StorageManager Interface

The `StorageManager` takes the agent itself for snapshotting — it reads the agent's internals directly and decides how to serialize them. This gives implementations full flexibility: JSON to a file, rows in Postgres, protobuf, etc. The agent doesn't know or care how its state is serialized.

```python
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from nooa import Agent

@runtime_checkable
class StorageManager(Protocol):
    """Unified storage interface for agent persistence.

    Implementations centralize all agent storage:
    - Event streaming (via the event_backend property)
    - Snapshot creation and loading

    The StorageManager is the single object users pass to agents
    for full persistence support.

    Snapshot methods receive/return the agent directly. The
    implementation is responsible for reading agent internals
    (context blocks, method registry, attributes, event manager
    metadata) and serializing them however it sees fit.
    """

    @property
    def event_backend(self) -> EventBackend:
        """The EventBackend used for streaming events.

        EventManager uses this for its storage layer. Events are
        streamed here on every add() call — append-only, immutable.
        """
        ...

    def save_snapshot(self, agent: Agent) -> str:
        """Save a snapshot of agent state.

        The implementation reads the agent's internals directly:
        - agent.context._raw_items() — context blocks (static + dynamic)
        - agent._defined_methods_registry — LLM-generated method sources
        - agent.__dict__ — instance attributes (filtered by transient/framework)
        - agent.event_manager._next_tag_num — tag counter for event continuity

        The implementation decides how to serialize these (JSON, DB rows, etc.)
        and where to store them.

        Args:
            agent: The agent to snapshot.

        Returns:
            A snapshot_id that can be used to load this snapshot later.

        Raises:
            SerializationError: If a value can't be serialized by this
                implementation (e.g., non-JSON-serializable attribute
                that isn't marked transient).
        """
        ...

    def restore_snapshot(self, snapshot_id: str, agent: Agent) -> None:
        """Restore agent state from a previously saved snapshot.

        The implementation reads the snapshot from storage and writes
        it back to the agent's internals (context blocks, method registry,
        attributes, event manager metadata).

        Symmetric counterpart to save_snapshot: save reads agent → writes
        storage, restore reads storage → writes agent.

        Called by Agent.load() after constructing a fresh agent.

        Args:
            snapshot_id: The ID returned by save_snapshot().
            agent: The freshly constructed agent to restore into.

        Raises:
            SnapshotNotFoundError: If snapshot_id is not found.
        """
        ...
```

### What the StorageManager reads from the agent

The agent exposes its internals for the StorageManager to read. These are not new APIs — they're existing internal attributes that the StorageManager accesses directly:

| Agent internal | What it provides | Type |
|----------------|-----------------|------|
| `agent.context._raw_items()` | Context blocks (static values + DynamicContext markers) | `dict[str, Any \| DynamicContext]` |
| `agent._defined_methods_registry` | LLM-generated method source code | `dict[str, str]` |
| `agent.__dict__` | Instance attributes (StorageManager filters by transient/framework/callable) | `dict[str, Any]` |
| `agent.event_manager._next_tag_num` | Tag counter for event continuity | `int` |
| `agent._get_transient_attrs()` | Set of attribute names annotated `transient` | `set[str]` |

This is intentionally not a formal interface — the StorageManager reads the agent's guts. If we need to tighten this boundary later, we can introduce an extraction protocol then.

### InMemoryStorageManager (default)

The default implementation keeps everything in memory — same behavior as today. It also serves as the reference implementation for the JSON serialization logic:

```python
class InMemoryStorageManager:
    """In-memory StorageManager — no persistence, same as current behavior.

    Also serves as the reference implementation for JSON-based
    snapshot serialization.
    """

    def __init__(self):
        self._backend = InMemoryBackend()
        self._snapshots: dict[str, dict] = {}

    @property
    def event_backend(self) -> EventBackend:
        return self._backend

    def save_snapshot(self, agent: Agent) -> str:
        """Read agent internals, serialize to JSON, store in memory."""
        snapshot = self._extract_snapshot(agent)
        snapshot_id = f"{agent._agent_id}:{uuid4()}"
        self._snapshots[snapshot_id] = snapshot
        return snapshot_id

    def restore_snapshot(self, snapshot_id: str, agent: Agent) -> None:
        """Restore agent state from a stored snapshot."""
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            raise SnapshotNotFoundError(f"Snapshot '{snapshot_id}' not found")
        self._apply_snapshot(snapshot, agent)

    def _extract_snapshot(self, agent: Agent) -> dict:
        """Read agent internals and produce a JSON-serializable dict."""
        # Context blocks
        context_blocks = {}
        for key, value in agent.context._raw_items():
            if isinstance(value, DynamicContext):
                context_blocks[key] = {"type": "dynamic", "expr": value.expr}
            else:
                _assert_json_safe(key, value)
                context_blocks[key] = {"type": "static", "value": value}

        # LLM-generated methods
        methods = dict(agent._defined_methods_registry)

        # Instance attributes
        transient_attrs = agent._get_transient_attrs()
        attributes = {}
        for k, v in agent.__dict__.items():
            if k.startswith('_') or k in FRAMEWORK_ATTRS or callable(v):
                continue
            if k in transient_attrs:
                continue
            _assert_json_safe(k, v)
            attributes[k] = v

        return {
            "version": 1,
            "event_manager": {
                "next_tag_num": agent.event_manager._next_tag_num,
            },
            "context": {"blocks": context_blocks},
            "methods": {"sources": methods},
            "attributes": {"values": attributes},
        }

    def _apply_snapshot(self, snapshot: dict, agent: Agent) -> None:
        """Write snapshot data back to agent internals."""
        # Restore context blocks
        for key, block in snapshot["context"]["blocks"].items():
            if block["type"] == "dynamic":
                agent.context.set_dynamic(key, block["expr"])
            else:
                agent.context[key] = block["value"]

        # Restore LLM-generated methods (recompile from source)
        for name, source in snapshot["methods"]["sources"].items():
            agent._restore_method(name, source)

        # Restore instance attributes
        for key, value in snapshot["attributes"]["values"].items():
            setattr(agent, key, value)

        # Restore event manager metadata
        agent.event_manager._next_tag_num = snapshot["event_manager"]["next_tag_num"]
```

### Relationship to EventBackend

`StorageManager` owns an `EventBackend` — it doesn't replace it. `EventManager` still talks to `EventBackend` for event storage. The `StorageManager` provides the backend instance and adds snapshot capabilities:

```
┌─────────────────────────────────────────────────────────┐
│                       Agent                              │
│                                                          │
│  ┌──────────────┐    ┌──────────────────────────────┐   │
│  │ EventManager  │───▶│ StorageManager               │   │
│  │ (pipeline,    │    │                              │   │
│  │  handlers,    │    │  ┌────────────────────────┐  │   │
│  │  queries)     │    │  │ .event_backend         │  │   │
│  └──────────────┘    │  │ (EventBackend protocol) │  │   │
│                       │  └────────────────────────┘  │   │
│  agent.save()  ──────▶│  .save_snapshot(agent)       │   │
│  Agent.load()  ──────▶│  .restore_snapshot(id, agent)│   │
│                       │                              │   │
│                       │  InMemoryStorageManager       │   │
│                       │  PostgresStorageManager       │   │
│                       │  FileStorageManager           │   │
│                       └──────────────────────────────┘   │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

This preserves the existing `EventBackend` protocol — existing backends continue to work. `StorageManager` is additive: it wraps `EventBackend` and adds snapshot capabilities.

### Evolution path

`StorageManager` is the extension point. Future implementations can choose different strategies:

- **`InMemoryStorageManager`** — Everything in-process. No persistence. Default.
- **`FileStorageManager`** — Events in memory, snapshots to JSON files. Good for debugging/local dev.
- **`PostgresStorageManager`** — Events streamed to Postgres, snapshots in a `snapshots` table. Production-grade.
- **`FullStreamStorageManager`** — Future: stream *everything* (events + context mutations + attribute writes) to a backend, no manual snapshots needed. The interface stays the same — `save_snapshot()` becomes a no-op or returns "latest".

Because the StorageManager gets the full agent, a future streaming implementation could hook into mutation points (context writes, method definitions, attribute sets) by instrumenting the agent at construction time — without changing the StorageManager protocol.

## Snapshot Format

The snapshot format is internal to each `StorageManager` implementation. Since `restore_snapshot()` writes directly to the agent (rather than returning a dict for `Agent.load()` to interpret), implementations are free to use whatever representation they want — JSON dicts, DB rows, protobuf, etc.

The `InMemoryStorageManager` reference implementation uses this JSON dict format:

```python
{
    "version": 1,
    "event_manager": {
        "next_tag_num": 42
    },
    "context": {
        "blocks": {
            "notes":  {"type": "static",  "value": "Here are my notes..."},
            "status": {"type": "dynamic", "expr": "self.format_status()"},
            "plan":   {"type": "static",  "value": ["Step 1: fetch data", "Step 2: analyze"]}
        }
    },
    "methods": {
        "sources": {
            "solve":  "async def solve(self, data: list) -> dict:\n    ...",
            "helper": "def helper(self, x: int) -> int:\n    return x * 2"
        }
    },
    "attributes": {
        "values": {
            "results": [1, 2, 3],
            "memory":  ["processed task A", "processed task B"]
        }
    }
}
```

Note: there are no `__unserializable__` markers. Every value in the JSON snapshot is fully serializable. Non-serializable values cause `save_snapshot()` to raise `SerializationError` — the developer must either make the value serializable or annotate the attribute as `transient`.

The top-level `"version"` field enables future schema evolution. Other StorageManager implementations may use a completely different format internally.

## Agent API

### `agent.save() -> str`

Creates a snapshot via the `StorageManager`. Returns a `snapshot_id`.

```python
snapshot_id = agent.save()
# snapshot_id is opaque — could be a UUID, a file path, a DB key, etc.
```

Internally:
1. Calls `self._storage.save_snapshot(self)` — the StorageManager reads the agent's internals and serializes them
2. Returns the `snapshot_id` from the storage manager

The StorageManager raises `SerializationError` if any value can't be serialized (see fail-fast semantics below). The agent itself has no serialization logic.

### `Agent.load(snapshot_id, storage, **kwargs) -> Agent`

Class method that restores an agent from a snapshot.

```python
agent = MyAgent.load(snapshot_id, storage=storage, llm=new_model)
```

Internally:
1. Constructs a new agent via `cls(storage=storage, **kwargs)` — normal constructor, so LLM, truncation, etc. are resolved as usual
2. Calls `storage.restore_snapshot(snapshot_id, agent)` — the StorageManager reads its stored data and writes it back to the agent's internals
3. Returns the restored agent

The StorageManager raises `SnapshotNotFoundError` if the snapshot_id is not found.

### `storage=` constructor parameter

```python
agent = MyAgent(storage=my_storage, llm=llm)
```

When provided:
- `EventManager` is initialized with `storage.event_backend` instead of a fresh `InMemoryBackend`
- `agent.save()` delegates to this storage manager
- `agent._storage` holds the reference

When omitted:
- Behavior is identical to today — `InMemoryBackend` is used, `agent.save()` raises an error (no storage configured)

## Design Details

### Serialization approach

Serialization logic lives in the `StorageManager`, not the Agent. Different implementations can serialize differently — JSON, Postgres rows, protobuf, etc. The Agent just exposes its internals for the StorageManager to read.

For events (streamed to the backend), we lean on **Pydantic**. All 13 event types are already Pydantic `BaseModel` subclasses, so persistent backend implementations can use Pydantic's `TypeAdapter` with a discriminated union (keyed on `event_type`) to serialize and deserialize them. This gives type-safe round-tripping, automatic handling of nested models (e.g., `ToolResult` inside `ToolCallEvent`), and validation on deserialization — all without writing custom serialization logic per event type.

For snapshot values (context blocks, instance attributes), `InMemoryStorageManager` (and other JSON-based implementations) use **fail-fast JSON serialization**:

```python
try:
    json.dumps(value)
except (TypeError, ValueError):
    raise SerializationError(
        f"Cannot snapshot attribute '{key}': type {type(value).__name__} "
        f"is not JSON-serializable. Annotate as `transient` to skip, "
        f"or ensure the value is JSON-safe before calling save()."
    )
```

No pickle, no custom codecs, no silent fallbacks. If `save_snapshot()` encounters a value it can't serialize, it raises immediately. We prefer failing loudly over producing a snapshot that restores to a broken agent the LLM can't recover from.

Developers can explicitly opt out of serialization for specific attributes using the `transient` annotation (see Instance Attributes below). Transient-annotated attributes are silently skipped during snapshot and restored as `None`.

### Events (streamed via StorageManager.event_backend)

Events are the conversation backbone — what the LLM said, what code ran, what results came back. Events are streamed to the `EventBackend` provided by the `StorageManager`.

`EventManager` delegates all storage to an `EventBackend` (protocol). The manager owns behavior (emission, tagging, queries, collapse); the backend owns storage. `StorageManager` provides the backend instance — replacing `InMemoryBackend` with a persistent implementation gives durable events with no changes to EventManager.

**What we capture:**
- All 13 event types (Task, Message, Reasoning, Error, Feedback, LLMOutput, PythonOutput, Summary, BeforeTurn, AfterTurn, UserEvent, AssistantEvent, ToolCallEvent)
- Event tags, IDs, timestamps, metadata
- Active vs. archived status (which events the LLM currently sees)
- Tag counter (`_next_tag_num`) so new events get correct sequential tags — stored in the snapshot
- Summary/collapse structure (which events are collapsed under a Summary)
- Insertion order (critical — this is the conversation sequence)

**What we don't capture:**
- `PythonOutput.value` when it holds a non-serializable Python object (DataFrame, model instance, etc.). The value is replaced with a `TransientValue` sentinel carrying the original type and repr. The `stdout`, `stderr`, `execution_count`, and `captured_locals` summary string are all preserved, so the LLM still sees what the code *printed* and what variables were defined — just not the live Python object.
- Event handler subscriptions (`event_manager.on("message", fn)`). These are runtime callbacks registered by application code, not conversation state.

**Impact on `Out[n]`:** The `OutAccessor` (Jupyter-style `Out[n]` access) would need to be updated to recognize `TransientValue` sentinels. With sentinels, `Out[n]` indices stay stable (no silent index shifts), and the LLM sees a descriptive repr instead of a missing entry. JSON-serializable return values (ints, strings, dicts, lists) are unaffected.

**How it works:**
`EventManager` delegates all storage to its `EventBackend`. On every `add()` call, the event is passed to the backend's `store()` method. Persistent backends serialize each event using Pydantic's `TypeAdapter` — `.model_dump(mode="json")` to serialize, `.validate_python()` to deserialize — with the `event_type` field as the discriminator so the correct model class is selected automatically. The backend also tracks tags, active/archived status, and insertion order. `EventManager` owns the tag counter (`_next_tag_num`), which is included in the snapshot so it's restored correctly.

### Context Blocks (snapshotted, fail-fast)

Context blocks appear in the LLM's system prompt. They're how agents manage what information the LLM sees across turns — working notes, plans, status summaries. Context blocks are captured as part of the snapshot.

Although `ContextManager.__setitem__` accepts `Any`, we expect context blocks to be generally serializable: strings, lists, dicts, numbers. They're rendered into the system prompt as text (strings pass through; non-strings get `agentdoc_pformat()`), so putting a non-serializable object like a DB connection in a context block is a code smell — it wouldn't render usefully either. We lean into this: **`save_snapshot()` will raise `SerializationError` if a context block value isn't JSON-serializable.** No silent fallbacks, no markers.

The `StorageManager` reads context blocks via `agent.context._raw_items()` and serializes each block based on its type:

| Block type | Serialized as | Restored as |
|-----------|--------------|-------------|
| Static (JSON-safe value) | `{"type": "static", "value": <actual_value>}` | Original value |
| Dynamic | `{"type": "dynamic", "expr": "<expression>"}` | `DynamicContext(expr)` via `set_dynamic()` |

Dynamic blocks are fully recoverable — they only store an expression string. On the next LLM turn, `_prepare_context()` evaluates the expression and populates the cache as usual.

On restore, the `StorageManager` writes context blocks back to the agent's `ContextManager` directly (via `__setitem__` and `set_dynamic`). Constructor-provided context is applied first (during normal construction), then the snapshot overwrites on top — so snapshot state wins over constructor defaults, but class-level framework blocks are protected.

**What we capture:**
- Static block keys and values (`self.context["plan"] = "Step 1: ..."`)
- Dynamic block keys and expressions (`self.context.set_dynamic("status", "self.format_status()")`)

**What we don't capture:**
- `_dynamic_cache` — the resolved values of dynamic expressions. These are re-evaluated on the next `_prepare_context()` call, so they don't need persisting.
- `_protected_keys` — the set of framework-reserved block names (e.g., `system_prompt`, `self`). These are derived from `FRAMEWORK_BLOCKS` at construction time, not runtime state.

### Defined Methods

When the LLM generates a method implementation (`def solve(self, data):`), it gets AST-parsed, compiled, and bound to the agent via `types.MethodType`. These methods are callable in future turns, and the LLM's system prompt advertises them (via `agentdoc`). After restore, they'd be gone.

**What we capture:**
- Method source code (the Python text of the `def` statement)
- Method name

**What we don't capture:**
- The compiled function object itself — we recompile from source on restore
- Closure-captured variables from the session that defined the method — if the method body references a local variable from the same code cell (not `self`, not a parameter), that variable won't exist after restore and the method may raise `NameError` when called
- Decorator side effects — if a decorator modified external state during the original definition, that side effect isn't replayed (though `@strategy` is stateless and works fine)

**How it works:**
The source code is already tracked at runtime on each function as `func._generated_source`. We propose adding a `_defined_methods_registry: dict[str, str]` on the Agent that collects `{method_name: source_code}` as methods are bound. This registry is populated at the two places where methods get attached to agents:

1. `HelperMethodManager.apply()` — pre-execution binding of helper methods
2. `ActorRuntime.execute_code()` — post-execution binding from exec_globals

On restore:
```
For each (method_name, source) in saved methods:
    1. Build execution namespace (same as ExecutionNamespaceBuilder.build())
    2. exec(compile(source, f"<restored:{method_name}>", "exec"), namespace)
    3. func = namespace[method_name]
    4. bound = types.MethodType(func, agent)
    5. setattr(agent, method_name, bound)
```

This reuses the same compilation path as normal execution, so decorators (`@strategy`), type annotations, and async functions are handled consistently.

### Instance Attributes (snapshotted, fail-fast + `transient`)

The LLM can set attributes directly on the agent instance (`self.results = [1, 2, 3]`). Developers also set attributes in `__init__`. Both are included in the snapshot by default.

**The rule is simple: every public attribute on `self` is snapshotted unless annotated `transient`.** If a value isn't JSON-serializable and isn't marked transient, `save()` raises `SerializationError`. This is intentional — we prefer a loud failure over silently producing a snapshot that restores to a broken agent.

#### The `transient` annotation

Developers use a type annotation to opt out of snapshotting for attributes that can't or shouldn't be serialized:

```python
from nooa import transient

class MyAgent(Agent):
    db_conn: transient
    cache: transient

    def __init__(self, ...):
        super().__init__(...)
        self.db_conn = get_db_connection()  # skipped on snapshot, restored as None
        self.cache = {}                      # skipped on snapshot, restored as None
        self.memory = []                     # NOT transient — snapshotted normally
```

`transient` is a type annotation marker, not a wrapper — the value itself is a plain Python object. `_export_state()` inspects the class annotations and skips any attribute annotated `transient`.

On export, transient attributes are recorded with type info but no value:

```python
{"__transient__": True, "type": "Connection", "repr": "Connection(host='db.example.com')"}
```

On restore, transient attributes are set to a `TransientValue` sentinel instead of `None`:

```python
class TransientValue:
    """Sentinel for attributes that were not persisted."""
    def __init__(self, type_name: str, repr_str: str):
        self.type_name = type_name
        self.repr_str = repr_str

    def __repr__(self):
        return f"<TransientValue: was {self.type_name} ({self.repr_str})>"

    def __bool__(self):
        return False  # falsy, like None
```

This plays nicely with `pprint()`, `doc()`, and debugging — you can see what was there before. The constructor is expected to re-initialize transient attributes with real values (e.g., `self.db_conn = get_db_connection()`), replacing the sentinel.

#### What we capture

- All public attributes on `self` (not starting with `_`)
- JSON-serializable values — strings, numbers, lists, dicts, booleans, None
- Both constructor-set and LLM-set attributes (no baseline-diff — everything is included)

#### What we don't capture

- Attributes annotated `transient` — explicitly skipped
- Callable attributes (bound methods, lambdas) — covered by the defined methods mechanism
- Framework-internal attributes (anything starting with `_`)

#### How it works

On save, the `StorageManager` iterates `agent.__dict__` and serializes every public, non-callable, non-framework attribute. See the `InMemoryStorageManager._extract_snapshot()` reference implementation for the full logic.

On restore, the `StorageManager` applies snapshot values via `setattr` on the agent. Since `restore_snapshot()` runs after the constructor, snapshot values overwrite constructor defaults — so if `__init__` creates `self.memory = []` and the snapshot has `self.memory = [1, 2, 3]`, the restored agent gets `[1, 2, 3]`.

### Code Execution State (Not Persisted)

For completeness: the REPL execution state is intentionally not persisted.

**What exists at runtime (within a generation session):**
- `session_locals` — variables from prior code cells (`x = 42` available as `x + 1` next turn)
- `exec_globals` — the full execution namespace including imports, builtins, session vars
- `Out[n]` accessor — Jupyter-style access to previous cell outputs
- Stdout/stderr capture buffers

**Why we don't persist it:**
Session locals live inside a `GenerationSession` object that's created at the start of each method call and garbage collected when the method returns. At the restore boundary (between method calls), session locals are already empty — there's nothing to save.

The PythonOutput events do capture a text summary of variables (e.g., `"Variables now in scope: x (int), df (DataFrame)"`) and the stdout/stderr of each cell. This gives the LLM enough context to understand what happened, even though the live Python objects aren't available.

If mid-session checkpointing becomes a requirement (e.g., crash recovery during a long-running generation loop), it would be a future extension requiring serialization of arbitrary Python objects — a fundamentally harder problem.

## Corner Cases and Limitations

### 1. Crash during code execution (non-atomic side effects)

If the agent crashes mid-execution, events streamed to a persistent backend survive — but the snapshot reflects the state at the last `save()` call. Context blocks, methods, and attributes modified since that snapshot are lost.

Additionally, some external side effects (API calls, database writes, messages sent) from partially-completed execution have already happened in the outside world. On restore, the LLM sees the full event history (from the backend) but may re-attempt operations, causing duplicates. This is the classic "at-least-once" problem. We don't solve it — the application layer must handle idempotency for external side effects if crash recovery is a requirement.

```
Timeline:
    [save()] → LLM writes code → API call 1 (succeeds) → API call 2 (crash!)
                                   ^^^^^^^^^^^^^^^^^^^^^^^^
                                   Events are in the backend, but context/methods/attrs
                                   revert to the last snapshot.
                                   External side effects may be duplicated on retry.
```

### 2. Non-serializable values raise on save

`save()` raises `SerializationError` if any context block or instance attribute value isn't JSON-serializable (unless the attribute is annotated `transient`). This is by design — we prefer a loud failure at snapshot time over a silent broken restore later.

The developer's fix is either:
- Make the value serializable (e.g., store a summary string instead of a DataFrame)
- Annotate the attribute as `transient` to skip it explicitly
- Use `self.context` for text-based state and keep non-serializable objects as `transient` instance attributes

### 3. Methods with closure captures

If an LLM-generated method closes over variables from the session that defined it (e.g., references a local variable from the same code cell), those variables won't exist after restore. The method source is restored and recompiled, but calling it may raise `NameError`.

In practice, most LLM-generated methods use `self` and parameters rather than closure captures, so this is uncommon.

### 4. Transient attributes restore as `TransientValue`

Attributes annotated `transient` are skipped on snapshot and restored as a `TransientValue` sentinel that carries the original type name and repr. This is better than `None` — it's falsy, but `pprint()` and `doc()` show what was there before, and `isinstance` checks won't accidentally match.

The constructor is expected to re-initialize transient attributes with real values (e.g., `self.db_conn = get_db_connection()`). If it doesn't, code accessing the attribute will get a `TransientValue` instead of the expected type — which will likely fail loudly on first use, making the issue obvious.

### 5. REPL variables within a generation session

Variables defined in one code cell and used in the next (`x = 42` then `x + 1`) only live within a single method call's generation loop. They're already gone between method calls, so they're not persisted. If the user needs mid-session checkpointing (e.g., for crash recovery during a long-running generation), that's a future extension not covered by this proposal.

### 6. No schema migration

The `"version": 1` field reserves space for future migration logic, but we don't implement any. If the event types or serialization format change in a future release, old snapshots may fail to deserialize. Users should treat snapshots as tied to the framework version that produced them.

### 7. Dynamic context expressions referencing unavailable state

A dynamic block like `self.context.set_dynamic("status", "self.compute_status()")` stores the expression string. On restore, the expression is preserved. But if `compute_status()` was an LLM-defined method that failed to restore (e.g., due to closure captures), evaluating the expression on the next turn will raise an error. The `_prepare_context()` error handling will catch this, but the block's resolved value will be missing from the system prompt.

### 8. save() without a StorageManager

If `agent.save()` is called on an agent constructed without a `StorageManager`, it raises `StorageNotConfiguredError`. This makes the error obvious — you can't persist state without telling the agent where to put it.

## AgentSnapshot Intermediate Representation

**Status: Implemented** (MR !467)

The snapshot serialization uses a two-layer design that separates *state extraction* from *format serialization*:

```
Agent ←→ AgentSnapshot ←→ dict (JSON)
         (dataclass)       (or protobuf, DB rows, etc.)
```

### Layer 1: `storage/snapshot.py` — Format-agnostic dataclasses

Three dataclasses capture the serializable agent state:

```python
@dataclass
class ContextBlockEntry:
    key: str
    type: str          # "static" or "dynamic"
    value: Any = None  # present when type == "static"
    expr: str | None = None  # present when type == "dynamic"

@dataclass
class EventManagerState:
    next_tag_num: int = 1

@dataclass
class AgentSnapshot:
    version: int = SNAPSHOT_VERSION
    context: list[ContextBlockEntry]
    event_manager: EventManagerState
    methods: dict[str, str]       # method_name → source code
    attributes: dict[str, Any]    # public, non-framework, JSON-safe attrs
```

`AgentSnapshot` provides two methods:
- **`AgentSnapshot.from_agent(agent)`** — Extracts state from an agent. Iterates context blocks, reads `_next_tag_num`, copies `_defined_methods_registry`, and collects public non-framework non-callable attributes. Raises `SerializationError` on non-JSON-serializable values.
- **`snapshot.restore(agent)`** — Writes state back into an agent. Restores context blocks, tag counter, recompiles methods from source via `ExecutionNamespaceBuilder`, and sets attributes. Raises `SerializationError` on version mismatch.

Framework attributes are skipped via a `_FRAMEWORK_ATTRS` frozenset (`runtime`, `event_manager`, `event_query`, `render_config`, `context`, `events`) plus the `startswith("_")` check for private attrs.

### Layer 2: `storage/json_snapshot.py` — JSON serialization

Four functions that convert between `AgentSnapshot` and JSON-serializable dicts:

- **`snapshot_to_dict(snapshot)`** — Converts an `AgentSnapshot` to a dict.
- **`snapshot_from_dict(data)`** — Constructs an `AgentSnapshot` from a dict. Validates version.
- **`snapshot_to_json(agent)`** — Convenience: `snapshot_to_dict(AgentSnapshot.from_agent(agent))`.
- **`snapshot_from_json(data, agent)`** — Convenience: `snapshot_from_dict(data).restore(agent)`.

### Why the split?

The `AgentSnapshot` dataclass is the contract between extraction and serialization. This means:
- **Other formats reuse extraction logic.** A future `ProtobufStorageManager` calls `AgentSnapshot.from_agent()` and serializes the dataclass its own way — no JSON involved.
- **Type safety.** The implicit dict schema (`snapshot["event_manager"]["next_tag_num"]`) becomes typed fields checked by the IDE and pyright.
- **Single validation point.** Version checks, structure validation, and error handling live on the dataclass, not scattered across format-specific code.

### `_defined_methods_registry`

A `dict[str, str]` on the Agent mapping method name → source code. Populated at the three runtime sites where LLM-generated methods are bound:

1. `ActorRuntime.execute_code()` — methods defined in LLM code cells
2. `HelperMethodManager.apply()` — helper methods installed by strategies
3. `_create_plan_method()` — plan step methods (only concrete bodies, not ellipsis-body methods that still need generation)

On restore, `AgentSnapshot.restore()` recompiles each source string via `ExecutionNamespaceBuilder.build()` + `exec(compile(...))` + `types.MethodType`, mirroring the normal runtime compilation path.

## Proposed File Changes

| File | Change | Status |
|------|--------|--------|
| `agent.py` | Add `storage=` param, `_defined_methods_registry` annotation + init | ✅ Done |
| `storage/snapshot.py` | New: `AgentSnapshot`, `ContextBlockEntry`, `EventManagerState` dataclasses with `from_agent()` / `restore()` | ✅ Done |
| `storage/json_snapshot.py` | New: `snapshot_to_dict` / `snapshot_from_dict` + convenience `snapshot_to_json` / `snapshot_from_json` | ✅ Done |
| `storage/__init__.py` | Export `AgentSnapshot`, `snapshot_to_json`, `snapshot_from_json` | ✅ Done |
| `storage/manager.py` | `StorageManager` protocol | ✅ Done (prior PR) |
| `storage/in_memory.py` | `InMemoryStorageManager` default implementation | ✅ Done (prior PR) |
| `errors/storage.py` | `SerializationError`, `SnapshotNotFoundError`, `StorageNotConfiguredError` | ✅ Done (prior PR) |
| `strategies/generated_code.py` | Record source in `_defined_methods_registry` in `HelperMethodManager.apply()` | ✅ Done |
| `runtime/actor.py` | Record source in `_defined_methods_registry` in `execute_code()` and `_create_plan_method()` | ✅ Done |
| `tests/runtime/test_snapshot.py` | 15 tests: roundtrip, dataclass layer, version checks, error cases | ✅ Done |
| `agent.py` | Add `save()`, `load()` classmethod | Pending |
| `serialization.py` | New: `TransientValue` sentinel, `transient` marker | Pending |
| `events.py` | Update `PythonOutput.value` serialization to use `TransientValue` sentinel | Pending |
| `out_accessor.py` | Update to recognize `TransientValue` (keep `Out[n]` indices stable) | Pending |
| `storage/in_memory.py` | Wire `AgentSnapshot` into `save_snapshot()` / `restore_snapshot()` | Pending |

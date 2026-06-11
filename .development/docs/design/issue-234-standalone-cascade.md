# Issue 234 — ATIF capture of standalone generation functions

## Problem

A standalone `@strategy` generation function (module-level, no `self`) was captured
in its caller's ATIF trajectory only when called from inside an open generation
turn. Called from a **pure-Python orchestrator** (an agent method with a real body,
not `...`), no turn is open, so the standalone's trajectory was silently dropped —
no `subagent_trajectories[]` entry and no file of its own. The root cause: a
pure-Python orchestrator emits no events, and ATIF is a pure event subscriber.

## Solution

### 1. Generic agent-call lifecycle events

The agent method wrapper emits `BeforeAgentCall` / `AfterAgentCall`
(`Role.RUNTIME_EVENT`, so never recorded in the event store or LLM context) for
every agent-level method call — generation methods, pure-Python orchestrators, and
sync helpers. They are the pub-sub complement to the existing `agent_call`
middleware and are symmetric with `BeforeTurn` / `AfterTurn` for generation turns.
The core runtime stays agnostic of any subscriber.

Each event carries `is_top_level` — true iff no agent was active (in
`_parent_agent_var`) before this call, i.e. the outermost agent in the current
async context.

### 2. ATIF binds the cascade off those events

`_atif_exporter_var` always points at the exporter that should capture work running
right now. On each agent run's outermost `BeforeAgentCall` (the var holds something
other than this exporter), `AtifExporter` binds itself for the call's duration; the
matching `AfterAgentCall` releases it (paired by `call_id`; same-agent nested method
calls are no-ops). What the var held at bind time decides the run's role:

- **nothing** → top-level run: owns its own trajectory file.
- **another exporter** (the parent) → nested run: embeds into the parent (see §3)
  and suppresses its own file. It still binds the var to itself so its own
  children nest under it — recursion follows the agent-delegation tree.

The event fires on the agent's own `EventManager`, so each exporter only ever binds
itself, and binding happens at call time, never at construction — preserving
isolation when independent agents share one async context
(`tests/atif/test_enable_atif_isolation.py`). `atif_scope` keeps its install-time
binding; `enable_atif` relies on this run binding. (`on_before_turn` keeps a
per-turn binding as a fallback for turns not preceded by an agent-call event, e.g.
exporters driven directly in tests.)

### 3. Embedded + referenced delegations

Delegated work is embedded as a `subagent_trajectories[]` entry **and** referenced
from a parent observation (ATIF v1.7's intended shape), never orphaned:

- **Standalone `@strategy` functions** (no own exporter): the parent captures each
  call via a fresh child exporter and lifts it on completion.
- **Sub-`Agent` instances** (own exporter): the sub-agent accumulates ONE trajectory
  (an OO agent shares event history across calls), embedded under the parent and
  upserted by `trajectory_id`.

The reference is attached to the enclosing tool call's observation when the
delegation happened inside a generation turn; otherwise (pure-Python orchestrator) a
deterministic-dispatch step (`source="agent"`, `llm_call_count=0`) is synthesized
carrying the `subagent_trajectory_ref`. Delegations before the first `SystemPrompt`
are buffered (like `Task` events) and flushed after the system step, preserving
order. A sub-agent handoff ref additionally records
`extra.subagent_step_range = [start, end]` — which steps of the (accumulating)
sub-trajectory that handoff produced — so a reused sub-agent reads as one
sub-trajectory with one ordered ref per round.

`asyncio.gather` fan-out produces one dispatch step + one embedded sub-trajectory
per concurrent delegation.

### 4. Pure-Python orchestrator finalization

A top-level orchestrator with no generation method of its own never fires
`SystemPrompt` or a final `AfterTurn`, so its `AfterAgentCall` flushes any buffered
dispatch steps and finalizes the trajectory (`_finalize_trajectory` is idempotent
via `_finalized`).

## Scope

A `subagent_trajectories[]` entry is created at an agent-identity boundary
(standalone function or distinct sub-`Agent`). Same-agent method recursion
(PM→GM→GM→…) flattens into one trajectory's step sequence (B-flatten); nesting depth
tracks agent-delegation boundaries, not method-call depth.

## Tests

- `tests/runtime/test_agent_call_events.py` — the generic events.
- `tests/atif/test_standalone_entrypoint_cascade.py` — standalone capture from
  inside a turn, first/after from a pure-Python orchestrator, crash release,
  concurrent runs, sync top-level arm/release, referenced-dispatch ordering, no-leak.
- `tests/atif/test_multi_agent_embedding.py` — orchestrator embedding sub-agents +
  standalones in one trajectory, reused-sub-agent accumulation with per-handoff
  step-ranges, and pure-orchestrator finalization.
- `tests/atif/test_enable_atif_isolation.py` — cross-agent isolation.

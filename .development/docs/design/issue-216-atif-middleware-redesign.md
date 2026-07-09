# ATIF export — event-driven, OTel-decoupled

## Problem

OpenInference / litellm instrumentation stamps both ids surfaced by
OpenAI's Responses API onto LLM span attributes — the function-call id
(`fc_*`) and the runtime call id (`call_*`). `unifiedllm`
(`unifiedllm.py:1963`), the CodeAct strategy, and the `code_execution`
TOOL span all key on `call_*`. The previous ATIF exporter read back
from those attributes and picked `fc_*` for `tool_calls[].tool_call_id`
while observations remained keyed by `call_*`. Pairing failed silently.

The runtime owns the canonical event stream; the trajectory is built
from that stream directly, not reverse-engineered from OTLP attributes.

## Architecture

The exporter is a read-only subscriber on each `Agent`'s
`EventManager`. One wildcard subscription routes every event through a
type-keyed dispatcher:

```python
em.on("*", exporter._dispatch_event)
```

```python
_HANDLER_DISPATCH = {
    "Task":          on_task,
    "BeforeTurn":    on_before_turn,
    "SystemPrompt":  on_system_prompt,
    "LLMComplete":   on_llm_complete,
    "LLMOutput":     on_llm_output,
    "Reasoning":     on_reasoning,
    "ToolCallEvent": on_tool_call_event,
    "PythonOutput":  on_python_output,
    "AfterTurn":     on_after_turn,
    "Error":         on_error,
    "Notification":  on_notification,
    "Summary":       on_summary,
}
```

Unknown event types fall through to `_record_generic_event(event)`,
which renders by `event._role` (USER/TOOL → user step, ASSISTANT →
agent step with `llm_call_count=0`, RUNTIME_EVENT/METADATA → skipped).
This keeps custom `EventBase` subclasses visible without an explicit
allow-list edit.

No `intercept(...)` middleware. No global state.

### Events

The exporter consumes the framework's existing event stream plus two
events introduced for it:

- `LLMComplete` — fires once per `runtime.generate()` after
  `LLMResponse` is built; carries `model_name`, token counts, cost,
  structured `tool_calls[]` (canonical `call_*` ids), `reasoning_content`,
  `dynamic_context` (see below), `generation_id`. `Role.RUNTIME_EVENT`,
  `record=False` (not in LLM-visible history).
- `SystemPrompt` — fires from `runtime/actor.py` right after
  `_build_messages` at every LLM call site (initial + retry branches).
  Carries the rendered `messages[0].content` (static SYSTEM-role
  blocks) and the surrounding `generation_id`. `Role.RUNTIME_EVENT`,
  `record=False`.

All other events (`Task`, `BeforeTurn`, `AfterTurn`, `LLMOutput`,
`ToolCallEvent`, `PythonOutput`, `Reasoning`, `Summary`, `Error`,
`Notification`) are pre-existing.

### Activation

```python
# Low-level.
uninstall = install_atif(agent.event_manager, path=..., session_id=...,
                         agent_name=..., agent_version=...)
try:    await agent.run()
finally: uninstall()

# Async context manager.
async with atif_scope(agent, path=..., session_id="...",
                      agent_name=..., agent_version=...):
    await agent.run()

# Zero-config global (every Agent constructed after this call gets
# `logs/atif/<AgentClass>/<session_id>.json`).
enable_atif(output_dir="logs/atif")
```

`install_atif` (and `atif_scope`) also set
`_atif_exporter_var: ContextVar[AtifExporter | None]` so standalone
generation functions (`create_standalone_wrapper` in `standalone.py`)
attach their child `EventManager`s to the same exporter — see Case C.

### Why `LLMComplete` and not `intercept("llm_call")`

`intercept` is the wrap-and-possibly-transform API; the exporter does
neither. `on(...)` callbacks fire after the operation completes and
match the read-only contract. `LLMComplete` makes the metadata that
`LLMResponse` already carries (`usage`, `cost`, structured
`tool_calls`) a first-class event available to any future consumer.

## Event → ATIF mapping

| Event | ATIF target |
|---|---|
| `BeforeTurn` (`turn_number==1`, `parent_generation_id is None`) | Open `Trajectory`; set `agent.{name,version}`, `session_id`, `trajectory_id`, `agent.tool_definitions = agentdoc.doc(self)`. Push `_atif_exporter_var` for run-scoped cascade. |
| `BeforeTurn` (any) | Open pending step in `pending: dict[generation_id, PendingStep]`. |
| `Task` | `StepObject{source:"user", message:prompt}`. Buffered if `SystemPrompt` hasn't fired yet (see "System prompt + dynamic context"). |
| `SystemPrompt` (first) | `StepObject{source:"system", message:content, step_id=1}`. Flush any buffered Tasks. |
| `SystemPrompt` (drift) | If content differs, stash for the next agent step's `extra.system_prompt_changed=True` + `extra.system_prompt`. Identical content is a no-op. |
| `LLMComplete` | Fill pending step (matched by `generation_id`): `model_name`, `metrics`, `tool_calls[]` (LLM-issued, `call_*` ids), `reasoning_content`, `extra.dynamic_context`, `llm_call_count=1`. |
| `LLMOutput` | Pending step `message`. |
| `Reasoning` | Append to pending step `reasoning_content`. |
| `ToolCallEvent` (LLM-issued id) | Augment existing `tool_calls[i].extra`; on result, append to `observation.results[]` with `source_call_id=tool_call_id`. |
| `ToolCallEvent` (new id) | Append to `tool_calls[]` with `extra.synthetic=True`, `extra.synthetic_type=<kind>`. Covers CodeAct's inline `return_result` and PredictStrategy's `_replace_with_tool_call`. |
| `PythonOutput` | Source of observation content for `execute_python` (`stdout`, `stderr`, returned value). Preferred over `ToolCallEvent.result.content`. |
| `AfterTurn` | Close pending step; assign sequential `step_id`; atomic write. |
| `AfterTurn` (`is_final`, `parent_generation_id is None`) | Compute `final_metrics`; release `_atif_exporter_var`; atomic write. |
| `Error` | `StepObject{source:"user", message:content, extra.event_kind:"error"}`. |
| `Notification` | `StepObject{source:"user", message:description, extra.source}`. |
| `Summary` | `StepObject{source:"system", message:"Context compaction performed", observation.results=[{content:summary_text}], extra.context_management={type:"compaction", boundary:"replace"}}`. Mark all prior `is_copied_context=True`. |
| Custom `EventBase` | `_record_generic_event` (role-based, see Architecture). |

### `step_id`

Sequential from 1, assigned at `AfterTurn` (and at each side-channel
event that emits its own step). Ordering = completion order = on-disk
write order.

### `tool_calls` source of truth

`LLMComplete` writes the LLM's tool calls; `ToolCallEvent` augments
them or appends framework-emitted ones. Matching is by
`tool_call_id`. LLM-issued ids are canonical `call_*`; synthetic ids
are `<kind>_<hex8>` (e.g. `codeact_inline_a1b2c3d4`). The joinability
invariant (§II) — `tool_calls[i].tool_call_id ⇔
observation.results[j].source_call_id` — holds by construction.

## System prompt + dynamic context

CachedBlockFormatter renders the messages list as:

```
[ system: static blocks (doc(self), strategy prompt, static context blocks),
  …event history…,
  user: <context>…dynamic SYSTEM blocks…</context> ]
```

The static prefix is stable across LLM calls (for prompt caching); the
trailing `<context>…</context>` envelope is re-rendered every call
from current agent state.

The exporter records both:

- **System step (`step_id=1`)**: the first `SystemPrompt` emits a
  `source:"system"` step with the rendered static content.
  Render-at-call-time means `SystemPrompt` fires AFTER `Task`; the
  exporter buffers Tasks until the first `SystemPrompt` arrives, then
  flushes them as `step_id=2..N`. `close()` and `finalize_on_exception`
  flush orphan-buffered Tasks (synthetic tests, crashed runs before
  the first LLM call).
- **Drift**: a later `SystemPrompt` with content hashing different
  from the first annotates the next agent step with
  `extra.system_prompt_changed=True` and `extra.system_prompt=<new>`.
  Static blocks are supposed to be stable (`metadata.static=True`);
  drift signals that something mutated them mid-run.
- **Per-turn dynamic context**: `LLMComplete.dynamic_context` carries
  the trailing `<context>…</context>` envelope extracted from
  `messages[-1].content`. Stored on the closing agent step's
  `extra.dynamic_context`. Each agent step thus records exactly what
  the LLM saw on that call without persisting the full per-turn
  messages list.

## Concurrency and nesting

The exporter holds `pending: dict[generation_id, PendingStep]`. Every
`BeforeTurn` opens an entry; every subsequent event (`LLMComplete`,
`LLMOutput`, `ToolCallEvent`, etc.) routes to the entry matching its
`generation_id`. This one keying scheme covers all four cases below.

| Case | Pattern | Where work lives | Mechanism |
|---|---|---|---|
| A — `PredictStrategy` alone | One LLM call, no tools | Parent `steps[]` (one step) | N/A |
| B — Same-agent nested generation | `self.classify(x)` called from inside `self.run`'s `execute_python` | **Flattened** into the SAME trajectory's `steps[]`; nesting recoverable via `extra.generation_id` / `extra.parent_generation_id` | Same `EventManager` |
| C — Standalone generation function | `@strategy(...)` function outside any `Agent` class; `create_standalone_wrapper` mints a fresh `Agent` + `EventManager` per call | Child `Trajectory` under root `subagent_trajectories[]`; `observation.results[].subagent_trajectory_ref` on the enclosing tool call | `_atif_exporter_var` ContextVar cascade → `_attach_child(child_event_manager)` |
| D — `asyncio.gather(...)` | N concurrent calls | D.1 (same-agent): flattens like B. D.2 (standalone): one child per call, all linked under the enclosing observation. D.3 (top-level harness): one root trajectory per call. | Pending-step dict keyed by `generation_id` |

B is flattened because nested same-agent calls share the EventManager
and read as one linear conversation. C embeds because the child has a
genuinely distinct EventManager and logical identity.

## Crash safety

Atomic write (`<path>.tmp` → `os.replace`) on every `AfterTurn` and on
the activation layer's exception path (which calls
`finalize_on_exception`). Crashed trajectories carry:

```json
{
  "extra": {
    "crashed": true,
    "exception_type": "TimeoutError",
    "exception_message": "..."
  }
  // final_metrics omitted
}
```

Bounded loss: `len(steps) ∈ {N, N−1}` where N is the number of
fully-completed turns at crash time. Detection key is
`extra.crashed`; the absence of `final_metrics` is not a reliable
signal on its own (consumers may produce trajectories without
aggregate metrics).

## Validation

Three layers, all in pytest:

1. **Schema** — `src/nooa/atif/schema.py` Pydantic models
   mirroring ATIF v1.7 §II. Every produced trajectory round-trips
   through `Trajectory.model_validate(...)`.
2. **Normative rules** (`tests/atif/normative.py`) — MUST/MUST-NOT
   rules that aren't pure-schema: sequential `step_id`; joinability;
   `trajectory_id` uniqueness within `subagent_trajectories[]`;
   `is_copied_context` propagation across compaction boundaries;
   `llm_call_count=0 ⇒ metrics absent`; deterministic-dispatch
   semantics; ISO 8601 timestamps; `message` field present;
   `final_metrics.total_*` sum across steps; `subagent_trajectory_ref`
   resolvable.
3. **Structural scenarios** (`tests/atif/test_structural_scenarios.py`)
   — representative end-to-end runs (real runtime via `FakeLLMClient`,
   or synthetic events for crash/multimodal/compaction) asserting the
   exporter's structural contract per scenario: step sources/ordering,
   tool_call↔observation joinability, subagent embedding + ref
   resolution, `is_copied_context` propagation, crash markers, metrics
   aggregation. Each trajectory also passes layers 1 and 2.

   Scenarios: single-turn CodeAct, multi-turn-with-error, PredictStrategy
   isolated, CodeAct→Predict flattened (Case B), standalone-generation
   subagent (Case C), `asyncio.gather` standalones (Case D.2), multimodal
   `Task.images` → `ContentPart[]`, compaction boundary, inline
   `return_result` synthetic tool call, nested subagent, crashed mid-turn.

   Asserting structure (rather than byte-pinned snapshots) keeps these
   tests focused on the exporter's behavior — they don't churn on
   framework system-prompt wording or unrelated module-symbol changes.

## File layout

```
src/nooa/atif/
  __init__.py     # re-exports install_atif, atif_scope, enable_atif, Trajectory
  schema.py       # Pydantic v1.7 models
  exporter.py     # AtifExporter state machine
  install.py      # install_atif, atif_scope, enable_atif

tests/atif/
  normative.py                     # assert_atif_normative
  test_schema_validation.py
  test_normative_rules.py
  test_exporter_state_machine.py   # synthetic-event-driven
  test_end_to_end_codeact.py       # hermetic agent + atif_scope
  test_phase4_nesting.py           # Cases B/C/D + crash + multimodal
  test_custom_event_types.py       # wildcard dispatch + role fallback
  test_enable_atif_isolation.py    # zero-config activation
  test_structural_scenarios.py     # end-to-end scenarios, asserted by structure
```

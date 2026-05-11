# Truncation 3.0 — L4: Event Eviction in Context-Window Assembly

**Branch:** `feat/l4-eviction-default-budget-split` (based on `feat/l4-eviction-with-markers`)
**Status:** Implemented (includes default 50/50 context-vs-events budgeting)

## Problem

When the rendered event timeline exceeds the LLM's context budget, the framework
must fit within budget. Prior to L4, boundary handling lacked a consistent
marker family and structured budgeting — no preservation guarantees, no
auto-budget derivation, and unclear over-budget signaling.

## Design

### Eviction strategy: oldest-first with preservation anchors

`_apply_event_total_limit` drops the oldest events first (they're least relevant
to the current decision), but two classes of events are **never evicted**:

| Anchor | Rule | Rationale |
|--------|------|-----------|
| **Recent-N** | Last `min_preserved_events` (default 5) events always survive | The model needs its recent reasoning chain to make coherent next decisions |
| **Latest-Task** | The most recent `Task` event always survives | The original instruction is what the agent is executing — losing it leaves the model goalless |

Events matching either anchor are skipped during eviction. If the budget still
can't be met after exhausting all evictable events, the preserved set stays
intact (graceful degradation over data loss).

### Default unconfigured budget split

When the agent author leaves both token budgets unset (`max_context_tokens=None`
and `max_event_tokens=None`), and the LLM client exposes `context_window`, L4
applies a default split:

```
context_limit = context_window // 2
reserve_tokens = max(response_reserve_tokens, max_output_tokens + 0.05 * context_window)
base_event_budget = context_window - reserve_tokens
applied_event_budget = max(0, base_event_budget - measured_context_tokens)
```

Where `measured_context_tokens` is the **true post-truncation context-block
size** (after context-block eviction, not a static estimate).

Definitions:

- `response_reserve_tokens` (RRT): fixed minimum output buffer used during
  input budgeting/truncation. It guarantees the model has reply headroom even
  when `max_output_tokens` is not provided.
- `max_output_tokens`: per-call generation cap passed to the model.
  In unconfigured default budgeting, this can raise reserve via:
  `max(response_reserve_tokens, max_output_tokens + 0.05 * context_window)`.

This gives two guarantees by default:

1. Context blocks can consume at most half the window
2. Event eviction accounts for real context usage before deciding what to drop

`response_reserve_tokens` defaults to 4,096 and acts as the floor reserve.
When `max_output_tokens` is provided on the call, reserve grows to include
that output budget plus a 5% context-window margin.

If either budget is explicitly configured by the author, that explicit config
wins and the default split is not applied.

### Boundary handling marker: `Summary` via collapse()

When the budget boundary is hit, runtime archives old events with
`event_manager.collapse(...)` and emits a `Summary` marker with text like:

```text
hit context window limit: before=131,200/126,000 events_tokens,
target_after<=100,800, archived~42
```

This gives one mental model:

- boundary hit => collapse
- no events lost (events are archived behind Summary)
- future turns render the Summary marker family directly

Implementation details:

- collapse uses hysteresis (~25% extra archival) to avoid re-collapsing every turn
- Task events whose `metadata.call_id` is currently on `_agent_call_stack` are excluded
- Summary text carries before/after budget numbers for debugging

### Context-block boundary behavior: in-place `EVICTED` labels

When context blocks exceed `max_context_tokens`, blocks are **not removed** from
the rendered system prompt. Instead, selected blocks are kept in-place and their
content is replaced with:

```
EVICTED: over context budget (block_tokens=...)
```

This keeps keys/structure stable for the LLM while making eviction explicit.

Selection policy:
- evict `self.context` user blocks first (from newest backward),
- then evict remaining system blocks (newest backward),
- stop once total context-block tokens fit the budget.

Terminology is unified with `context.status`: over-budget context blocks are
reported as **EVICTED** (e.g., `3 EVICTED`).

### Exact `context.status()` text (current)

Event/context internal rendering policy: only **L2** (stdout/stderr capture) uses `TruncatingStringIO` truncation markers. Event internals and context-block internals use structural pformat bounds and must not emit `<truncated-output>`.

`context.status()` is rendered from `ContextWindowStats.format()` and uses this
exact wording:

```text
Context usage: {total_tokens:,} / {max_total:,} tokens ({pct:.1f}%)
  Context blocks: {context_blocks_tokens:,} / {max_context_tokens:,} tokens ({pct:.1f}%) — {context_blocks_count} blocks — {context_blocks_dropped} EVICTED
  Events:         {events_tokens:,} / {max_event_tokens:,} tokens ({pct:.1f}%) — {events_count} events — {events_dropped} dropped
Context is nearly full. Context blocks over budget are labeled EVICTED. Use self.context (ContextApi) to summarize or remove blocks, and self.events (EventsApi) to summarize or manage event history.
```

Notes:
- If a limit is unset, that line falls back to `... tokens` without `/ limit`.
- The warning line appears when blocks/events were dropped, or utilization is hot.


## Configuration

Two new fields on `TruncationConfig`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `min_preserved_events` | `int` | `5` | Floor on recent events that can never be evicted |
| `response_reserve_tokens` | `int` | `4096` | Tokens reserved for LLM response when auto-deriving event budget. Set to 0 to disable auto-derivation. |

## Interaction with existing layers

| Layer | Relationship to L4 |
|-------|-------------------|
| **L1** (agent `pprint(...)`) | Independent — controls how individual values render |
| **L2** (I/O capture `<truncated>`) | Independent — limits stdout/stderr before it becomes an event |
| **L3** (block render budgets) | L4 operates *after* L3 — individual blocks are already size-bounded when L4 sees them |
| **L4** (this layer) | Operates on the *assembled* event list, after all per-block truncation |

## Invariants

1. The rendered prompt always contains at least `min_preserved_events` events
   (or all events if fewer exist)
2. The most recent `Task` event is never evicted (the agent always knows its goal)
3. Evicted events remain queryable via `self.events[tag]`
4. Boundary hits archive history via `collapse()` + Summary text (`hit context window limit ...`)
5. Task events with active stack `call_id` are excluded from collapse ranges
6. Over-budget context blocks remain visible and are labeled `EVICTED`
7. In unconfigured mode, context budget defaults to `context_window // 2`
8. In unconfigured mode, event budget is reduced by measured context size before eviction
9. In unconfigured mode, reserve is `max(response_reserve_tokens, max_output_tokens + 5% window)`
10. Default split fires only when no explicit budget is configured AND the LLM
   reports its context window

## Open questions / future work

- **Summary quality policy**: boundary hits now collapse with a fixed
  summary template. A future enhancement could generate richer summaries
  (task-aware, tool-aware) while keeping deterministic token bounds.
- **Priority-based eviction**: some event types (e.g. `Error`) might be worth
  preserving longer than others. Currently all non-anchored events have equal
  eviction priority (oldest-first).
- **Structured-overhead-aware split**: default split now accounts for measured
  content tokens, but provider-side wrappers (JSON/tool envelopes, XML tags)
  still create additional overhead handled by the structured safety net. A
  future refinement could bake that overhead directly into pre-render budgets.

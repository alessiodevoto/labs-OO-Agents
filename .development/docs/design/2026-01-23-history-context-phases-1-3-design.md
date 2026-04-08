# History Context Phases 1-3 Design

**Goal:** Deliver phases 1–3 of history rendering: valid exprs, RenderSpec-driven rendering, and flattened event fields without touching history management/backends.

**Scope:** Only rendering and event model shape changes across `packages/context-blocks/` and `src/nemo_oo_agents/`.

## Architecture Overview

Phase 1 repairs expr correctness and adds `HistoryManager.__getitem__` so exprs can use position-based access (`self.history[n]`). Phase 2 decouples formatters from event internals by introducing a `RenderSpec` and `render_spec()` on events. Phase 3 flattens event fields so content, tool attributes, and execution outputs live directly on the event (no `.data` wrapper).

```
HistoryManager -> events list (position-based)
        |
        v
BlockRenderer -> BlockFormatter (RenderSpec)
        |
        v
ProviderFormatter -> provider messages
```

## Key Design Choices

- **Position-based exprs:** Exprs reference the current render view (`self.history[n]`), not stored on events. This keeps future history management compatible with rendering.
- **RenderSpec contract:** Events declare their tag, attribute fields, and content fields; formatters map those into markup without knowing event shapes.
- **Flat events:** Flattening reduces indirection (`event.content` instead of `event.data.content`) and aligns with the new RenderSpec structure.

## Data Flow

1. `HistoryManager` exposes events via list access, enabling exprs like `self.history[2].content`.
2. `BlockRenderer` formats events using `RenderSpec`, generating inline or nested markup.
3. Provider formatters consume the formatted events without needing event internals.

## Testing Strategy

- Add/adjust unit tests in `context-blocks` to assert RenderSpec behaviors and expr outputs.
- Update `nemo_oo_agents` tests to reflect flattened event fields.
- Focused suites for renderer/formatter and history APIs before widening to integration tests.

## Diagram (Event Rendering)

```
Event -> render_spec() -> RenderSpec(tag, attrs, content)
     -> BlockFormatter builds:
        - expr: self.history[n] or self.history[n].field
        - attrs: attributes on tag
        - content: inline or nested tags
```

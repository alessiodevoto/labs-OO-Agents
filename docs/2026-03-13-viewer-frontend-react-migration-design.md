# Viewer Frontend Migration: Vanilla JS → React

**Date:** 2026-03-13
**Scope:** `packages/nemo-oo-agents-viewer/frontend/`

## Problem

The package viewer frontend is built with vanilla JS, plain HTML, and raw CSS — no framework, no build step, no type safety. This worked for rapid prototyping but creates increasing friction:

- **No component reuse.** The trace list and eval experiment summary both render paginated trace tables, but share no code. DOM construction is duplicated across ~80 files.
- **Plugin system is stringly-typed.** 36 event-type renderers register by string key and return raw DOM nodes. Adding a new plugin means copy-pasting boilerplate with no type checking.
- **State management is ad-hoc.** A mix of global `window.*` variables, `localStorage`, `sessionStorage`, and URL hash/query params. `sessionStorage` workarounds (`traceListState`, `lastViewedTrace`) exist solely to compensate for full-page navigations destroying DOM state.
- **No hot reload during development.** Every change requires a manual browser refresh.
- **Dead code from legacy viewers.** Langfuse upload UI (`/api/sessions/upload`), eval-set management (`/api/eval-set/*`) — the backend endpoints don't exist in the package viewer, but the frontend code remains.

## Goals

1. Migrate to a modern SPA stack while preserving all functional features.
2. Keep the same FastAPI backend — only change how it serves the frontend.
3. Maintain the `nooa start-dev` / `nooa viewers start` workflow unchanged.
4. Enable component reuse (e.g. a shared trace table used in both `/traces` and eval summary).
5. Remove dead code (Langfuse upload, eval-set management).

## Non-Goals

- Redesigning the backend API.
- Adding new features beyond what exists today.
- Supporting SSR or static site generation.

## Stack

| Layer | Choice | License | Rationale |
|-------|--------|---------|-----------|
| Build | Vite 8 | MIT | Rolldown-based, fastest available bundler. Dev server with HMR and API proxy. |
| UI | React 19 + TypeScript | MIT | Component model, type safety, large ecosystem. |
| Routing | React Router v7 (declarative/SPA mode) | MIT | Battle-tested, browser-based routing. Replaces hash routing in eval viewer. |
| Styling | Tailwind CSS 4.2 | MIT | Utility-first, no custom CSS file proliferation. |
| Syntax highlighting | highlight.js | BSD-3-Clause | Already in use (currently via CDN), moves to npm dependency. |
| State management | React APIs (useState, useReducer, useContext) | — | Sufficient for this app's complexity. No external library needed. |
| Package manager | npm | — | — |

## Project Layout

New source lives in `packages/nemo-oo-agents-viewer/frontend-react/`. The existing `frontend/` directory is left untouched until migration is complete and verified.

```
packages/nemo-oo-agents-viewer/frontend-react/
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/
    main.tsx                  # React root + router mount
    App.tsx                   # Top-level layout + route definitions
    api/                      # Typed API client functions
      traces.ts
      eval.ts
      annotations.ts
      playground.ts
    hooks/
      useKeyboardNav.ts
      useLocalStorage.ts
      useUrlState.ts          # Sync filter/view state with URL search params
    context/
      TraceViewerContext.tsx   # Trace detail state (events, filters, expansion)
      EvalContext.tsx          # Eval experiment/test state
    pages/                    # Route-level components
      TraceList.tsx
      TraceDetail.tsx
      EvalExperimentList.tsx
      EvalExperimentSummary.tsx
      EvalTestDetail.tsx
      EvalPlayground.tsx
    components/
      TraceTable.tsx           # Shared paginated trace/session table
      trace/
        EventList.tsx
        EventItem.tsx
        Timeline.tsx
        FilterSidebar.tsx
        TracePlayground.tsx
      eval/
        ExperimentCard.tsx
        TestTable.tsx
        EmbeddedTraceViewer.tsx
        LiveIndicator.tsx
      annotations/
        AnnotationForm.tsx
        AnnotationIndicator.tsx
        TagInput.tsx
      plugins/                # Event type renderers as React components
        registry.ts
        PluginWrapper.tsx
        ...grouped by category...
      shared/
        CodeBox.tsx
        CopyButton.tsx
        KeyboardShortcutsHelp.tsx
```

## Routes

All browser-based routing (no hash routing). React Router v7 in declarative/SPA mode.

| Path | Page | Notes |
|------|------|-------|
| `/` | — | Redirect to `/eval` |
| `/traces` | TraceList | Paginated table (50/page), search, delete |
| `/traces/view` | TraceDetail | `?session_id=...&event=N&filters=...&timeline=...&embed=true` |
| `/eval` | EvalExperimentList | Paginated list (50/page), search, live status |
| `/eval/experiment/:id` | EvalExperimentSummary | Stats, filterable test table (reuses TraceTable) |
| `/eval/experiment/:id/test/:testId` | EvalTestDetail | I/O, scorers, embedded trace viewer |
| `/eval/experiment/:id/test/:testId/playground` | EvalPlayground | `?turn=N` |

The trace viewer keeps `session_id` as a query param (not a path segment) because session IDs can contain arbitrary characters.

## Shared TraceTable Component

Both `/traces` and the eval experiment summary display paginated lists of trace sessions. Today these are completely separate implementations. The migration introduces a single `TraceTable` component used in both contexts:

- **Common columns:** name/session ID, span count, modified timestamp, size
- **Eval-specific columns:** pass/fail, score, model (added via props)
- **Actions:** delete (trace list only), click-to-navigate (both)
- **Pagination:** 50 items per page, prev/next controls. Configurable in the future.

## State and Persistence

| State | Storage | Rationale |
|-------|---------|-----------|
| Sidebar collapsed | `localStorage` | User preference, persists across sessions |
| Timeline expanded height | `localStorage` | User preference |
| Trace view filters | URL search params (base64 JSON) | Shareable/bookmarkable URLs |
| Eval filters (model, status, keyword) | URL search params | Shareable URLs (currently in `localStorage`, moving to URL) |
| Trace list pagination/scroll | React state | SPA keeps component state alive across navigation |
| Last viewed trace highlight | React state | No `sessionStorage` needed in SPA |
| Eval pagination state | React state / context | No `sessionStorage` needed |

### Removed

| Key | Was | Why removed |
|-----|-----|-------------|
| `uploadedTraces` | `localStorage` | Langfuse upload is dead code — backend has no `/api/sessions/upload` |
| `traceListState` | `sessionStorage` | Workaround for full-page navigation; unnecessary in SPA |
| `lastViewedTrace` | `sessionStorage` | Same — React state handles this |
| `evalViewer_paginationState` | `sessionStorage` | Same — React context handles this |
| `eval-filters-{experimentId}` | `sessionStorage` | Replaced by URL params |

## Plugin System

The current system: 36 vanilla JS classes extending `BasePlugin`, registered by event-type string in a `PluginRegistry`. Each returns a raw DOM element.

The React system: same registry concept, but plugins are React components.

```typescript
type ViewState = 'collapsed' | 'concise' | 'expanded';

interface PluginProps {
  event: SpanEvent;
  viewState: ViewState;
  searchQuery?: string;
}

type PluginComponent = React.FC<PluginProps>;

// Registry maps event type patterns to components
const registry = new Map<string, PluginComponent>();
```

Plugins are grouped by similarity to reduce duplication:

- **Lifecycle** (started/finished pairs): `agent_call`, `execution`, `generation`, `plan`, `signal` — common wrapper component
- **LLM**: `llm_call`, `chat_completion`, `llm_call_start`, `span.llm_call`, etc.
- **Messages**: `user_message`, `agent_message`, `agent_reasoning`
- **Code**: `code_execution`, `line_exec`, `repl`
- **Spans**: generic fallback, `method`, `generation`, `tool_execution`, `eval`, `tau_bench_step`

## Features In Scope

1. **Trace list** — paginated table (50/page), search, delete
2. **Trace detail** — event list with span hierarchy, 3-state expand/collapse, text search with highlighting
3. **Timeline** — dual canvas (overview + detail), zoom, wiper, span bars, resizable
4. **Filter sidebar** — event type checkboxes, text search, span/agent/LLM/execution ID dropdowns
5. **Plugin system** — all 36 event-type renderers, converted to React components
6. **Annotations** — score, label, comment, tags; CRUD via `/api/annotations`; quick +/- feedback
7. **Playground** — trace and eval; model selector, temperature, inference, diff view
8. **Eval viewer** — experiment list, summary (with shared TraceTable), test detail with embedded trace viewer
9. **Live updater** — polling for experiment status with Page Visibility API pause
10. **Keyboard shortcuts** — all existing shortcuts preserved (j/k navigation, expand/collapse, etc.)
11. **URL state** — shareable URLs for trace view state and eval filters
12. **Embed mode** — `?embed=true` hides toolbar/timeline for embedding in other views

## Features Dropped

| Feature | Reason |
|---------|--------|
| Langfuse upload | Backend endpoint (`/api/sessions/upload`) does not exist in package viewer |
| Eval-set management | Backend endpoints (`/api/eval-set/*`) do not exist in package viewer |
| Saved filter configurations | Low usage; can be added later if needed |

## Backend Changes

Minimal. The FastAPI app in `main.py` needs two changes:

**1. Production serving:** Mount Vite's build output (`frontend-react/dist/`) as static files and add a catch-all route serving `index.html` for client-side routing.

**2. Remove old routes:** The per-page `FileResponse` routes (`serve_root`, `serve_trace_list`, `serve_trace_viewer`, `serve_eval`) and the `StaticFilesNoCacheJS` class are replaced by the catch-all + Vite's asset fingerprinting.

**`__init__.py` change:** Update `FRONTEND_DIR` resolution to point at `frontend-react/dist/` (build output) instead of `frontend/`.

All API routes (`/api/*`, `/v1/traces`) remain unchanged.

### CLI Impact

`nooa start-dev` and `nooa viewers start` continue to work identically — they import `nooa_viewer.main:app` and run it with uvicorn. The only difference is which static files the app serves.

During development, the Vite dev server runs separately on its own port and proxies API requests to the FastAPI backend:

```
Browser → localhost:5173 (Vite dev server, HMR)
              ↓ proxy /api/*, /v1/*
         localhost:5001 (FastAPI backend)
```

## Migration Phases

Each phase produces a working, testable application.

### Phase 1: Scaffold + Core Read Path

- Initialize Vite 8 + React 19 + React Router v7 + Tailwind 4.2 project
- Configure Vite proxy to FastAPI backend
- Implement `TraceList` page with `TraceTable` component (paginated, searchable)
- Implement `TraceDetail` page with event list, hierarchy, expand/collapse
- Port 4-5 core plugins: span fallback, `llm_call`, `user_message`, `code_execution`, `method`
- Verify end-to-end data flow from OTLP store through API to rendered UI

### Phase 2: Full Trace Viewer

- Port all 36 trace plugins to React components
- Port timeline canvas (overview + detail, zoom, wiper, span bars, resize)
- Port filter sidebar with URL state synchronization
- Port annotations (form, indicators, tag input, quick feedback, CRUD)
- Port trace viewer keyboard shortcuts

### Phase 3: Eval Viewer

- Implement experiment list with `TraceTable`, search, pagination, live status
- Implement experiment summary with stats and filterable test table
- Implement test detail with I/O, scorers, embedded trace viewer, raw JSON
- Port eval keyboard shortcuts and live updater with visibility API pause

### Phase 4: Playground + Polish

- Implement playground for both trace and eval contexts
- Implement embed mode (`?embed=true`)
- Add keyboard shortcuts help overlay
- Final styling pass and accessibility review
- Update backend `main.py` with catch-all route for production serving
- Update `__init__.py` to resolve `FRONTEND_DIR` to build output

## End-to-End Testing

### Framework

**Playwright** (MIT license). Industry standard for E2E testing of React+Vite apps. TypeScript-first, cross-browser, fast parallel execution.

### Test Data

Tests run against a real FastAPI backend seeded with fixture data. A test fixture script ingests a small set of OTLP traces into a temporary SQLite database via `POST /v1/traces` before the test suite runs. The fixtures should cover:

- At least 2 experiments, one with multiple test sessions
- Sessions with diverse span types to exercise multiple plugins (LLM calls, code execution, tool calls, user/agent messages, errors)
- At least one session with eval metadata (pass/fail, scores)

The fixture OTLP payloads live in `frontend-react/e2e/fixtures/` as JSON files. A `globalSetup` script starts the FastAPI backend with `TRACE_STORE_DB` pointing to a temp file, seeds it, and tears it down after the suite.

### Test Scenarios

**Happy path — eval-to-trace flow:**

```
1. Navigate to /eval
2. Assert: experiment list loads, at least 2 experiments visible
3. Click an experiment
4. Assert: experiment summary page loads with stats and test table
5. Assert: test table shows all sessions for that experiment
6. Click a trace/test row
7. Assert: trace detail loads, event list renders with correct span count
8. Assert: multiple plugin types render (check for LLM call, code execution,
   user message sections — at least 3 distinct plugin types visible)
9. Expand a collapsed event
10. Assert: expanded content appears (code box, message content, etc.)
```

**Happy path — trace list flow:**

```
1. Navigate to /traces
2. Assert: trace table loads with pagination controls
3. Assert: page shows up to 50 traces
4. Click a trace
5. Assert: trace detail page loads with events
6. Navigate back
7. Assert: trace list page is restored (SPA navigation, no full reload)
```

**Keyboard navigation:**

```
1. Navigate to /eval, focus the experiment list
2. Press j/↓ — assert next experiment is selected
3. Press k/↑ — assert previous experiment is selected
4. Press Enter — assert experiment summary opens
5. Press h/Backspace — assert navigates back to list
6. Navigate to trace detail
7. Press j/k — assert event selection moves
8. Press →/Enter — assert event expands
9. Press ←/h — assert event collapses
10. Press / — assert search input is focused
11. Press ? — assert help overlay appears
12. Press Esc — assert help overlay closes
```

### Running Tests

```bash
cd packages/nemo-oo-agents-viewer/frontend-react
npx playwright test              # headless
npx playwright test --ui         # interactive UI mode
npx playwright test --headed     # visible browser
```

### CI Considerations

Playwright tests run in CI against the built production bundle (`npm run build` then serve via FastAPI). This validates both the build output and the backend integration. Tests should complete in under 60 seconds for the initial suite.

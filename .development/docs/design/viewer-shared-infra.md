# Viewer Shared Infrastructure Plan

## Overview

The e2e-optimization viewer and prompt-optimization viewer have significant overlap. This document proposes sharing infrastructure to reduce duplication and improve consistency.

## Current State

| Component | prompt-optimization | e2e-optimization |
|-----------|---------------------|------------------|
| Port | 5003 | 5005 |
| Views | List → Summary → Detail | List → Summary (partial) |
| Test table | Full filtering, navigation | Basic table |
| Keyboard nav | Yes (j/k, arrows, etc.) | No |
| Live updates | Yes | No |
| Trace viewer | Yes (port 5001 links) | Partial (fixed) |

## Components to Share

### 1. Frontend JS Libraries (High Value)

Move to `viewer_utils/frontend/js/`:

| File | Purpose | Effort |
|------|---------|--------|
| `router.js` | URL routing, history management | Medium |
| `views.js` | View manager pattern | High (needs generalization) |
| `filter-manager.js` | Filter state, controls binding | Low |
| `keyboard-nav.js` | Keyboard shortcuts | Low |
| `live-updater.js` | Auto-refresh for live runs | Low |

**Strategy**: Create base classes that viewers can extend/configure.

### 2. Frontend CSS (Already Started)

Already sharing base styles via `viewer_utils/frontend/`:
- `base.css` - Core layout, typography
- `plugins.css` - LLM input/output rendering

Add shared component styles:
- `components/tables.css` - Filterable test tables
- `components/cards.css` - Experiment/run cards
- `components/badges.css` - Status badges (passed/failed/etc.)

### 3. Backend Patterns

Already sharing via `viewer_utils`:
- `StaticFilesNoCacheJS` - No-cache for JS files
- `setup_cors_middleware` - CORS configuration
- Path utilities (`get_traces_dir`, `get_results_dir`)

Could add:
- Base `ViewerApp` class with common endpoints
- Trace file loading utility
- Common Pydantic models (status badges, etc.)

### 4. Trace Viewer Integration

Both viewers need to link to the trace viewer (port 5001). Share:
- Trace URL builder function
- Common link rendering

## Implementation Plan

### Phase 1: Quick Wins (Current)
1. [x] Fix trace URLs in e2e viewer to use trace viewer
2. [ ] Share trace URL builder in `viewer_utils/frontend/js/trace-utils.js`

### Phase 2: Shared JS Libraries
1. [ ] Move `filter-manager.js` to viewer_utils (most standalone)
2. [ ] Move `keyboard-nav.js` to viewer_utils
3. [ ] Abstract `router.js` into shared base

### Phase 3: View Pattern Generalization
1. [ ] Extract `BaseViewManager` class
2. [ ] Create `TestTableRenderer` component
3. [ ] Create `TestDetailRenderer` component

### Phase 4: Unified Results Viewing
1. [ ] e2e viewer uses prompt-optimization's test table pattern
2. [ ] Both viewers can drill into individual tests
3. [ ] Both link to trace viewer with consistent UX

## Data Model Mapping

```
prompt-optimization          e2e-optimization
----------------            ----------------
experiment_id        →      run_id
test_id              →      task_id
test.eval.passed     →      task.success
test.eval.score      →      task.score
test.trace_file      →      task.trace_path
```

The mapping is straightforward - just different naming conventions.

## Questions

1. Should e2e viewer fully adopt prompt-optimization's view pattern?
   - Pro: Maximum code reuse, consistent UX
   - Con: More upfront work

2. Should we merge the viewers into one multi-purpose viewer?
   - Pro: Single codebase, unified experience
   - Con: Complexity, different purposes

## Recommendation

**Short term**: Fix trace URLs, share small utilities
**Medium term**: Extract and share filter-manager and keyboard-nav
**Long term**: Consider unified viewer or full view pattern sharing

The e2e viewer is simpler by design (focused on optimization runs), so we shouldn't over-engineer. Start with trace URL fix and see what feels most valuable.

---

## Full Implementation Plan: Shared Test/Task Viewing

### Goal

Have e2e evaluation results use the prompt-optimization's proven test table/detail pattern, giving users the same polished experience for viewing evaluation task results.

### Architecture

```
viewer_utils/frontend/
├── js/
│   ├── shared-router.js        # URL routing (from prompt-opt router.js)
│   ├── shared-filter-manager.js # Filter state (from prompt-opt filter-manager.js)
│   ├── shared-keyboard-nav.js   # Keyboard shortcuts
│   ├── shared-views-base.js     # BaseViewManager with extension points
│   └── trace-utils.js           # Trace viewer URL builder
├── css/
│   ├── base.css                 # Already exists
│   ├── tables.css               # Test/task table styles
│   └── badges.css               # Status badges
└── plugins/                     # Already exists (LLM input/output)
```

### Step-by-Step Implementation

#### Step 1: Extract Shared JS to viewer_utils

**Files to create in `viewer_utils/frontend/js/`:**

1. **`trace-utils.js`** - Trace viewer URL builder
   ```javascript
   function buildTraceViewerUrl(tracePath, port = 5001) {
       const sessionId = extractSessionId(tracePath);
       return `http://${window.location.hostname}:${port}/?session_id=${encodeURIComponent(sessionId)}`;
   }
   ```

2. **`shared-filter-manager.js`** - Copy from prompt-opt with minor cleanup
   - Filter state management
   - Control binding
   - URL parameter sync

3. **`shared-keyboard-nav.js`** - Copy from prompt-opt
   - j/k navigation
   - Arrow keys
   - Enter to select
   - Esc to close

4. **`shared-router.js`** - Simplified router
   - Hash-based routing
   - History management
   - Route listeners

#### Step 2: Create Shared CSS Components

**Files to create in `viewer_utils/frontend/css/`:**

1. **`tables.css`** - Filterable tables
   - `.tests-table` styles
   - Filter row in header
   - Row hover/selection
   - Status cell styling

2. **`badges.css`** - Status badges
   - `.status-badge.passed/.failed/.error`
   - `.positive/.negative` value colors

#### Step 3: Create BaseViewManager

**`viewer_utils/frontend/js/shared-views-base.js`:**

```javascript
class BaseViewManager {
    constructor(router, filterManager) {
        this.router = router;
        this.filterManager = filterManager;
    }

    // Override in subclass
    async fetchItems() { throw new Error('Not implemented'); }

    // Override in subclass
    getItemId(item) { throw new Error('Not implemented'); }

    // Shared table rendering
    renderTable(items, columns, options = {}) {
        // Generic table with filter header
    }

    // Shared detail navigation
    renderDetailNav(currentIndex, totalItems) {
        // Prev/next buttons, position indicator
    }

    // Trace link helper
    renderTraceLink(tracePath) {
        return buildTraceViewerUrl(tracePath);
    }
}
```

#### Step 4: Update e2e Viewer to Use Shared Components

**Changes to `e2e_optimization/viewer/frontend/`:**

1. **`index.html`** - Add shared script imports
   ```html
   <script src="/shared/js/trace-utils.js"></script>
   <script src="/shared/js/shared-filter-manager.js"></script>
   <script src="/shared/js/shared-keyboard-nav.js"></script>
   ```

2. **`js/main.js`** - Refactor to use shared components
   - Use `buildTraceViewerUrl()` for trace links
   - Use `SharedFilterManager` for task filtering
   - Add keyboard navigation

3. **Add task detail view** - Click task row to see full details
   - Input data
   - Expected output
   - Actual output
   - Error message (if failed)
   - All iteration traces

#### Step 5: Backend API Updates

**Add to `e2e_optimization/viewer/backend/main.py`:**

```python
@app.get("/api/evaluations/{run_id}/tasks/{task_id}")
async def get_task_detail(run_id: str, task_id: str):
    """Get full details for a single task including all iterations."""
    # Return full task data with all iteration traces
```

### Data Flow

```
e2e evaluation result
        │
        ▼
┌───────────────────────┐
│  /api/evaluations     │  List view - run selector
└───────────────────────┘
        │
        ▼
┌───────────────────────┐
│  /api/evaluations/:id │  Summary view - metrics + task table
└───────────────────────┘
        │
        ▼
┌───────────────────────┐
│  /api/.../tasks/:tid  │  Detail view - single task with traces
└───────────────────────┘
        │
        ▼
┌───────────────────────┐
│  Trace Viewer :5001   │  LLM call inspection
└───────────────────────┘
```

### Migration Strategy

1. **Keep current e2e viewer working** throughout migration
2. **Add shared components incrementally** - don't break existing functionality
3. **Feature flag** if needed: `?v2=true` to test new view system
4. **Once stable**, remove old code paths

### Estimated Effort

| Step | Effort | Risk |
|------|--------|------|
| 1. Extract shared JS | 2-3 hours | Low |
| 2. Shared CSS | 1 hour | Low |
| 3. BaseViewManager | 2-3 hours | Medium |
| 4. e2e viewer update | 3-4 hours | Medium |
| 5. Backend API | 1 hour | Low |
| **Total** | **~10 hours** | **Medium** |

### Success Criteria

- [ ] e2e evaluation tasks display in filterable table
- [ ] Click task row → detail view with input/output/traces
- [ ] Keyboard navigation works (j/k, arrows)
- [ ] Trace links open in trace viewer
- [ ] Both viewers share same filter-manager code
- [ ] Both viewers share same keyboard-nav code

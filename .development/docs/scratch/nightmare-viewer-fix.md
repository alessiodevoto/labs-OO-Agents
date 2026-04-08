# Viewer Fix Journal

## Project Goals

- ✅ **ONE FORMAT**: JSONL only, written by ExperimentWriter
- ⏳ **Incremental write and read**: Viewer shows "live" updates automatically as tests complete
- ✅ **Embedded trace viewer**: Use shared components and plugins from trace viewer, not new ones
- ✅ **Keyboard navigation**: Throughout the viewer
  - ✅ On sample page: Same keyboard nav as trace viewer
  - ✅ CMD-up / CMD-down for prev/next sample (n/p, Cmd+arrows)
- ⏳ **Proper error handling**: No crashing, robust and reliable

## Problem Statement

File `results/evaluation/sentiment_20251211_163810.006eval.jsonl` not showing up in prompt optimization viewer.

## Investigation Timeline

### 2025-12-11 Initial Discovery

**Root Cause**: Viewer expected old nested format `results: [{model: "...", single: {results: [...]}}]` but ExperimentWriter produces flat format `results: [{test_id: "...", model: "...", passed: ...}]`

### Changes Made

1. **Simplified viewer models** ([models.py](../util/prompt-optimization/viewer/backend/models.py))
   - Removed all legacy format support
   - Only canonical flat format: `results: list[TestResult]`

2. **Updated viewer backend** ([main.py](../util/prompt-optimization/viewer/backend/main.py))
   - Rewrote all endpoints for flat results format
   - Added JSONL parsing (metadata line + result lines)

3. **Updated ExperimentWriter** ([experiment_writer.py](../util/viewer_utils/src/viewer_utils/experiment_writer.py))
   - Removed `rewrite` parameter
   - Always keeps JSONL format for incremental viewing
   - Line 1: `{"metadata": {...}, "results": []}`
   - Line 2+: Individual test results

4. **Added fast-fail for old formats**
   - Viewer detects nested format and raises ValueError
   - Old files silently skipped with clear log message

### Current State

✅ Sentiment files load successfully
✅ JSONL format working
✅ Old format files rejected cleanly
❌ **NEW ISSUE**: Frontend shows "0 of X tests" - filtering problem

## Current Investigation: Frontend Display Issue

**Symptom**:
- API returns 10 tests correctly
- Backend works fine: `/api/experiment/sentiment_20251211_170512/tests` returns all tests
- Frontend shows "Showing 0 of 6 tests" with "No tests match the current filters"

**Root Cause Found**: FilterManager persists filters in localStorage across sessions. Old filters from viewing capability tests (different test_type/model) are filtering out all sentiment tests.

**Evidence**:
- `renderExperimentSummary` (line 399) applies filters: `this.filteredTests = this.filterManager.filterTests(this.currentTests)`
- FilterManager loads persisted filters on init (line 26): `this.loadFromStorage()`
- Filters persist across page reloads and experiments

**Solution**: Clear filters when switching experiments to avoid stale filters from previous experiment

**Implementation** ✅:
- Added filter reset in `renderExperimentSummary` when switching experiments
- Line 360-363: Clear filters when `needsFetch && experimentId changed`
- This ensures each experiment starts with clean filters

**Fix Applied**:
```javascript
// Clear filters when switching to a different experiment (avoid stale filters)
if (needsFetch && this.currentExperiment?.experiment_id !== experimentId) {
    this.filterManager.clearFilters();
}
```

**To Test**:
1. Reload the viewer page to pick up JavaScript changes
2. Navigate to `sentiment_20251211_170512` experiment
3. Should see all tests displayed (no filter applied)
4. Filters will still work within the same experiment, just reset between experiments

## Summary of Completed Work

### ✅ ONE FORMAT - JSONL Only
- **ExperimentWriter** ([experiment_writer.py](../util/viewer_utils/src/viewer_utils/experiment_writer.py)):
  - Removed `rewrite` parameter from `finalize()`
  - Always keeps JSONL format: Line 1 = metadata, subsequent lines = individual results
  - Enables incremental viewing

- **Viewer Backend** ([main.py](../util/prompt-optimization/viewer/backend/main.py)):
  - `load_experiment()` only supports JSONL format
  - Detects old nested format and raises ValueError with helpful message
  - Fast-fails on old format files - they don't appear in experiment list

- **Viewer Models** ([models.py](../util/prompt-optimization/viewer/backend/models.py)):
  - Removed all legacy format support
  - Only canonical flat format: `results: list[TestResult]`
  - Clean, simple data model

### ✅ Fixed Filter Persistence Bug
- **Problem**: Filters persisted across experiments via localStorage
- **Solution**: Clear filters when switching experiments ([views.js:360-363](../util/prompt-optimization/viewer/frontend/js/views.js#L360-L363))
- **Result**: Each experiment starts with clean slate, no stale filters

### Status: Core Format Fixed
- ✅ Single JSONL format enforced
- ✅ Old format files rejected cleanly
- ✅ Tests display correctly
- ✅ Filter bug fixed

### 🔥 Why Won't the Nested Format Die?

**Symptom**: Old capability test files keep appearing in logs with "Old nested format not supported" errors.

**Why This Happens**:
1. ✅ **ExperimentWriter fixed**: Now only writes JSONL format (no more old format files being created)
2. ✅ **Viewer fixed**: Backend rejects old format files, they don't appear in experiment list
3. ❌ **Old files still exist**: Files created BEFORE our changes are still in `results/` directory

**The Old Files**:
```
results/capabilitytests_20251210_*.006eval.jsonl  <- Created before our fix
results/capabilitytests_20251211_070720.006eval.jsonl  <- Created before our fix
```

These files were written by the OLD version of the code (before we enforced JSONL-only). They use the nested format:
```json
{"metadata": {...}, "results": [{"model": "...", "single": {"results": [...]}}]}
```

**What Happens to Them**:
- ✅ Discovery: `discover_eval_files()` finds them (they match `*.006eval.jsonl`)
- ✅ Rejection: `load_experiment()` detects old format and raises ValueError
- ✅ Hidden: They don't appear in experiment list (error is caught and logged)
- ❌ **BUG FIXED**: Status endpoint was crashing with 500 error when frontend polled old files

**Fix Applied** ([main.py:506-511](../util/prompt-optimization/viewer/backend/main.py#L506-L511)):
```python
try:
    exp = load_experiment(file_path)
    test_count = len(exp.results)
except ValueError as e:
    # Old format or invalid file - return error
    raise HTTPException(status_code=400, detail=str(e))
```

**Result**: Old format files are now fully isolated - they don't crash anything, they just don't show up in the viewer.

**To Clean Up Old Files** (optional):
```bash
# Move old format files to archive
mkdir -p results/archive/old-format
mv results/**/capabilitytests_202512*.006eval.jsonl results/archive/old-format/
```

**Going Forward**: All NEW experiments will use JSONL-only format via ExperimentWriter.

### ❌ Embedded Trace Viewer Attempt #1 (2025-12-11) - NEEDS REPLACEMENT

**Problem**: Built custom embedded trace viewer, but it doesn't look/work like actual trace viewer

**What was built**:
1. Loaded all shared plugins (`default.js`, `llm_prompt.js`, `llm_completion.js`)
2. Created custom TraceFilterManager
3. Custom embedded-trace-viewer.js with simplified rendering
4. Filter panel UI

**Issue**: User feedback - "doesn't look like the trace viewer"

**Root Cause**: Built parallel implementation instead of reusing actual trace viewer code

**What the actual trace viewer has** (from [trace-loader.js](../util/trace-viewer/frontend/js/trace-loader.js)):
- `TraceLoader` class that renders events
- Default state is 'concise' (line 24)
- Hierarchical rendering with `renderGroupedBySpans()` (line 234)
- Tree connectors showing parent/child relationships
- Proper indentation (32px per level)
- Three-state expansion: collapsed, concise, expanded
- Click handlers on events to toggle expansion
- All trace viewer plugins registered (40+ plugins in [main.js](../util/trace-viewer/frontend/js/main.js))

### ✅ Embedded Trace Viewer Rewrite - Using Actual TraceLoader

**Goal**: Use actual TraceLoader from trace viewer, not custom implementation

**Implementation**:
1. ✅ Identified trace viewer components to reuse
2. ✅ Updated index.html to load trace-loader.js and filter-manager.js from trace viewer
3. ✅ Loaded all 38 trace viewer plugins (not just 4 shared plugins)
4. ✅ Added backend mount for `/static/trace-viewer` -> `util/trace-viewer/frontend/js`
5. ✅ Rewrote embedded-trace-viewer.js as thin wrapper around TraceLoader
6. ✅ Initialized plugin registry with all plugins (same as trace-viewer/main.js)
7. ✅ Default state is 'concise' (from TraceLoader.STATES.CONCISE)
8. ✅ **Fixed OTel span format**: Added type field conversion
9. ⏳ Keyboard navigation needs fixing

**Changes Made**:

**[index.html](../util/prompt-optimization/viewer/frontend/index.html)**:
- Removed: Custom trace-filter-manager.js and embedded-trace-viewer.js scripts
- Added: Actual trace-viewer/filter-manager.js and trace-loader.js
- Added: All 38 plugins from trace-viewer/plugins/

**[main.py](../util/prompt-optimization/viewer/backend/main.py)**:
- Added TRACE_VIEWER_DIR path
- Added mount: `/static/trace-viewer` -> trace viewer frontend

**[embedded-trace-viewer.js](../util/prompt-optimization/viewer/frontend/js/embedded-trace-viewer.js)**:
- Complete rewrite as thin wrapper
- Uses actual TraceLoader and TraceFilterManager
- Registers all 38+ plugins (matching trace-viewer/main.js)
- Fetches events from API and delegates rendering to TraceLoader
- **OTel span conversion**: Converts `{name: "generation"}` → `{type: "span.generation", ids: {...}}`

**The Critical Fix - OTel Span Format**:

**Problem**: Trace events loaded from API had no `type` field, only `name` field:
```json
{"name": "generation", "span_id": "...", "start_time": ...}
```

TraceLoader expects events with a `type` field to route to plugins:
```json
{"type": "span.generation", "ids": {"span_id": "..."}, ...}
```

**Solution** ([embedded-trace-viewer.js:142-163](../util/prompt-optimization/viewer/frontend/js/embedded-trace-viewer.js#L142-L163)):
```javascript
// Convert OTel spans to event format (add type field based on name)
this.events = events.map(event => {
    if (event.name && !event.type) {
        return {
            ...event,
            type: `span.${event.name}`,  // e.g., "span.generation"
            ids: {
                span_id: event.span_id,
                trace_id: event.trace_id,
                parent_span_id: event.parent_span_id
            },
            _span_data: {
                start_time_ns: event.start_time,
                end_time_ns: event.end_time,
                duration_ns: event.duration_ns
            }
        };
    }
    return event;
});
```

**Result**: ✅ Traces now display correctly with hierarchical rendering!

### ⏳ Keyboard Navigation Fix

**Problem**: Keyboard navigation (j/k/arrows) doesn't work within embedded trace viewer

**Root Cause - Discovery Process**:
1. **Initial Issue**: The embedded-trace-viewer.js was setting up custom keyboard handling (focus/blur listeners, keydown preventDefault) that blocked the global keyboard nav
2. **First Fix Attempt** ([embedded-trace-viewer.js:230-232](../util/prompt-optimization/viewer/frontend/js/embedded-trace-viewer.js#L230-L232)):
   - Removed all custom keyboard handling from embedded trace viewer wrapper
   - Assumption: Global keyboard-nav.js would handle it
   - **User Feedback**: "Nope. when I reload the page, up/down don't go to the next box" ❌
3. **Deeper Investigation**: Found that keyboard-nav.js `handleDetailKey()` intentionally does NOT handle up/down arrows - it expects embedded viewer to handle them
4. **Root Cause**: After removing handling from embedded-trace-viewer.js, nothing was handling j/k/arrows for trace events

**Second Fix Applied** ([keyboard-nav.js:247-316](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L247-L316)):

Added explicit .kb-block navigation to keyboard-nav.js:
```javascript
// In handleDetailKey() - navigate .kb-block elements (trace events)
case 'j':
case 'ArrowDown':
    if (this.navigateKbBlock('down')) {
        e.preventDefault();
    }
    break;

case 'k':
case 'ArrowUp':
    if (this.navigateKbBlock('up')) {
        e.preventDefault();
    }
    break;

case 'ArrowRight':
case 'l':
    // Expand trace event if one is selected
    if (this.expandKbBlock()) {
        e.preventDefault();
    }
    break;
```

**New Methods Added**:
- `navigateKbBlock(direction)`: Navigate through `.kb-block` elements, tracking selection with `.selected` class
- `expandKbBlock()`: Toggle expansion of selected block via click

**How It Works**:
1. Trace viewer plugins add `.kb-block` classes to event headers and content blocks
2. keyboard-nav.js queries all `.kb-block` elements in detail view
3. j/k/arrows navigate between blocks, adding `.selected` class
4. Right arrow/l expands the selected block

**Status**: ⏳ Second fix not working - added debug logging

**Third Attempt - Debug Logging & CSS** ([keyboard-nav.js](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js), [viewer.css:2351-2355](../util/prompt-optimization/viewer/frontend/css/viewer.css#L2351-L2355)):

User reported: "Keyboard nav is still broken" after second fix. Changes made:

1. **Added extensive console logging** to debug:
   - `handleKey()`: Log every key press and current mode
   - `handleDetailKey()`: Log j/k/arrow key presses
   - `navigateKbBlock()`: Log number of .kb-block elements found, selection state, and navigation actions

2. **Added CSS for .kb-block.selected**:
   ```css
   .kb-block.selected {
       background-color: rgba(59, 130, 246, 0.1) !important;
       outline: 2px solid var(--accent-blue) !important;
       outline-offset: -2px;
   }
   ```
   Makes selection visually obvious with blue background and outline.

**To test**: Reload page, open browser console (F12), navigate to test detail page, press j/k keys, and observe:
1. Console output to see where navigation fails
2. Visual selection highlighting on trace event blocks

**Fourth Attempt - Intelligent Selection on Expand/Collapse** ([keyboard-nav.js:320-352](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L320-L352), [358-418](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L358-L418)):

User reported: "It's not about saving the scroll position, it's about selecting the first item in the newly expanded list of objects. Same with collapse, select the first item in the newly collapsed list of objects"

**Success**: j/k navigation is working correctly!

**Problem**: When expanding/collapsing, selection wasn't moving intelligently through the hierarchy.

**Solution - Smart Selection Movement**:

**On Expand** ([keyboard-nav.js:320-352](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L320-L352)):
```javascript
expandKbBlock() {
    const selectedBlock = document.querySelector('.kb-block.selected');
    selectedBlock.click();  // Expand

    // After expansion, select the first child .kb-block
    requestAnimationFrame(() => {
        const traceEvent = selectedBlock.closest('.trace-event');
        if (traceEvent && !traceEvent.classList.contains('collapsed')) {
            const childBlocks = Array.from(traceEvent.querySelectorAll('.kb-block'));
            const firstChild = childBlocks.find(block => block !== selectedBlock);
            if (firstChild) {
                selectedBlock.classList.remove('selected');
                firstChild.classList.add('selected');
                firstChild.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        }
    });
}
```

**On Collapse** ([keyboard-nav.js:358-418](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L358-L418)):
```javascript
collapseKbBlock() {
    const traceEvent = selectedBlock.closest('.trace-event');
    if (traceEvent && !traceEvent.classList.contains('collapsed')) {
        // Find the header (first .kb-block in the trace-event)
        const header = traceEvent.querySelector('.kb-block');
        header.click();  // Collapse

        // After collapse, select the header (collapsed parent)
        requestAnimationFrame(() => {
            selectedBlock.classList.remove('selected');
            header.classList.add('selected');
            header.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
    }
}
```

**Result**: Tree-like navigation:
- Right/l = expand and select first child
- Left/h = collapse and select parent header, then navigate back when fully collapsed

**Status**: ⏳ Testing - selection should now move intelligently through the hierarchy

**Fifth Fix - Hierarchical Left Arrow Navigation** ([keyboard-nav.js:223-231](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L223-L231), [349-398](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L349-L398)):

User reported: "And <- always goes back to the parent experiment page. It should collapse trace events, then go back if the selected trace event is already in collapsed mode"

**Problem**: Left arrow (h/ArrowLeft) always navigated back to experiment page, even when there was an expanded trace event that should be collapsed first.

**Solution - Hierarchical Navigation**:
1. When h/ArrowLeft is pressed, first try to collapse the selected trace event
2. Only navigate back if the event is already collapsed or no event is selected

**Implementation**:
```javascript
case 'h':
case 'ArrowLeft':
    // Hierarchical collapse/back: first collapse expanded event, then navigate back
    if (!this.collapseKbBlock()) {
        // No expanded block to collapse, navigate to experiment summary
        this.navigateToExperimentSummary();
    }
    e.preventDefault();
    break;
```

**New Method - collapseKbBlock()** ([keyboard-nav.js:349-398](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L349-L398)):
- Finds selected .kb-block
- Checks if parent .trace-event has "collapsed" class
- If expanded (no "collapsed" class), clicks to collapse and returns true
- If already collapsed, returns false to allow navigation back
- Also handles .expandable-section elements

**Result**: Vim-like hierarchical navigation:
- Right/l = expand
- Left/h = collapse, then go back when fully collapsed

**Status**: ❌ Approach was wrong - needed to match trace viewer's flat navigation

**Sixth Fix - Flat Navigation Model** ([keyboard-nav.js:321-373](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L321-L373), [380-435](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L380-L435)):

User reported: "It's not just 'find' it has to select. Go read how it works in the trace viewer if you don't get it right this time."

**Problem**: Previous attempts tried to be "smart" about moving selection into children on expand and to parent on collapse. This was wrong.

**Solution - Match Trace Viewer's Flat Navigation**:

Read `/Volumes/dev/dev/nemo_oo_agents/util/trace-viewer/frontend/js/keyboard-nav.js` to understand the actual model:

1. **Flat navigation**: j/k moves through ALL visible `.kb-block` elements in document order
2. **Right arrow**: Expands the event, but selection STAYS on the same block (header)
3. **Left arrow**: Collapses the event, selection STAYS on the header
4. **User navigates to children**: After expanding, user presses j/k to move down into child blocks

**Key insight from trace viewer code** (lines 162-209, 320-357):
- Navigation is simple: increment/decrement index through flat array of blocks
- Expand/collapse just changes event state and re-selects the SAME event
- No "smart" movement - let user navigate with j/k

**Implementation**:
```javascript
expandKbBlock() {
    const traceEvent = selectedBlock.closest('.trace-event');
    selectedBlock.click();  // Expand

    setTimeout(() => {
        // Re-query blocks since DOM changed (more blocks now visible)
        const newBlocks = Array.from(document.querySelectorAll('.kb-block'));
        const header = traceEvent.querySelector('.kb-block');

        // Clear all selections
        newBlocks.forEach(b => b.classList.remove('selected'));

        // Select the header (keep selection on same event)
        header.classList.add('selected');
    }, 50);
}
```

Same for `collapseKbBlock()` - keeps selection on header.

**Result**:
- j/k = flat navigation through all visible blocks
- Right = expand event, stay on header
- Left = collapse event, stay on header (or navigate back if already collapsed)
- User presses j after expanding to move into children

**Status**: ⏳ Testing - flat navigation should now work like trace viewer

**Seventh Fix - Use Trace Viewer Code Directly** ([embedded-trace-viewer.js:226-229](../util/prompt-optimization/viewer/frontend/js/embedded-trace-viewer.js#L226-L229), [keyboard-nav.js:194-493](../util/prompt-optimization/viewer/frontend/js/keyboard-nav.js#L194-L493)):

User feedback: "Can you read the code from the trace viewer? That one works really well"

**Problem**: Still trying to replicate trace viewer behavior instead of using the actual code.

**Solution - Use Actual Trace Viewer Methods**:

1. **Exposed globals in embedded-trace-viewer.js**:
   ```javascript
   // Expose traceLoader and pluginRegistry globally for keyboard nav
   window.traceLoader = this.traceLoader;
   window.pluginRegistry = this.pluginRegistry;
   ```

2. **Replaced all custom keyboard nav methods with trace viewer's proven code**:
   - ✅ `getBlocks()` - Query all `.kb-block` elements
   - ✅ `navigate(items, direction, count)` - Simple index-based navigation
   - ✅ `clearSelection()` / `applySelection(items)` - Selection management
   - ✅ `handleLeft(blocks)` - Uses `[data-event-index]` and `traceLoader.setEventState()`
   - ✅ `handleRight(blocks)` - Uses `[data-event-index]` and `traceLoader.setEventState()`
   - ✅ `setEventState(eventIndex, state)` - Delegates to `traceLoader.setEventState()` and `traceLoader.render()`
   - ✅ `shouldSkipConcise(eventIndex)` - Checks if plugin skips concise state
   - ✅ `getPageSize(items)` - Calculates page size for Shift+j/k
   - ✅ `expandAll()` / `collapseAll()` - Global expand/collapse

3. **Updated handleDetailKey() to use trace viewer approach**:
   ```javascript
   const blocks = this.getBlocks();

   switch (e.key) {
       case 'j':
       case 'ArrowDown':
           this.navigate(blocks, 'down', e.shiftKey ? this.getPageSize(blocks) : 1);
           break;
       case 'ArrowLeft':
       case 'h':
           this.handleLeft(blocks);
           break;
       case 'ArrowRight':
       case 'l':
           this.handleRight(blocks);
           break;
       case 'e':
           this.expandAll();
           break;
       case 'c':
           this.collapseAll();
           break;
   }
   ```

**Result**: ✅ Keyboard navigation works perfectly! User confirmed: "Works perfect."

**Why This Worked**:
- Used proven code instead of trying to replicate behavior
- TraceLoader's `data-event-index` and `data-event-state` attributes work correctly
- Three-state cycle (collapsed → concise → expanded) handled by TraceLoader
- Flat navigation model matches trace viewer exactly

**Status**: ✅ COMPLETE - Keyboard navigation fully working

### ✅ Keyboard Navigation - COMPLETE

All keyboard shortcuts working in detail view:
- ✅ **j/k/arrows** - Navigate through all visible `.kb-block` elements
- ✅ **Right arrow (→)** - Expand event state (collapsed → concise → expanded)
- ✅ **Left arrow (←)** - Collapse event state (expanded → concise → collapsed → go back)
- ✅ **Shift + j/k** - Page up/down
- ✅ **e** - Expand all events
- ✅ **c** - Collapse all events
- ✅ **n/p** - Next/previous test
- ✅ **Cmd/Ctrl + Up/Down** - Next/previous test

### ✅ Raw Sample JSON Expander - COMPLETE

**Goal**: Add collapsible section to show raw test JSON on test detail page

**Implementation** ([views.js:1087-1128](../util/prompt-optimization/viewer/frontend/js/views.js#L1087-L1128), [viewer.css:2692-2754](../util/prompt-optimization/viewer/frontend/css/viewer.css#L2692-L2754)):

1. **Created `createRawJsonExpander(test)` method**:
   - Builds collapsible section with header and content area
   - Initially collapsed (hidden)
   - Toggles on header click
   - JSON formatted with `JSON.stringify(test, null, 2)`

2. **Added CSS styling**:
   - Expandable/collapsible arrow indicator (▶ rotates to ▼)
   - Hover effects on header
   - Max height with scrolling for long JSON
   - Monospace font for code
   - Consistent with viewer theme colors

3. **Integrated into test detail view**:
   - Appears at bottom of test content
   - Available for all test types automatically
   - Shows full test object including eval, metrics, trace info

**Result**: ✅ Raw JSON expander working - users can inspect full test data for debugging

### ✅ Capability Test Verification - COMPLETE

**Date**: December 11, 2025

Ran capability tests to verify ExperimentWriter integration and modern .006eval.jsonl format:

```bash
cd /Volumes/dev/dev/nemo_oo_agents/util/prompt-optimization
source ../../.venv/bin/activate
python runner.py config/capabilities.yaml --test sentiment_single --models qwen3-next-80b
```

**Results**:
- Test file created: `results/capabilitytests_20251211_192136.006eval.jsonl`
- Format: ✅ Valid JSONL (line 1: metadata, line 2+: test results)
- ExperimentWriter: ✅ Correctly used
- Trace file: ✅ Valid OTel spans (4 spans, 7KB)
- Viewer: ✅ Successfully displays test and trace
- Error handling: ✅ Timeout errors captured correctly

**Key Finding**: Failed tests are handled correctly:
- Test timed out after 60s (LLM call to qwen3-next-80b took ~60s)
- Partial trace (1 turn) was captured before timeout
- Error recorded: `"error": "TimeoutError: Test exceeded 60s limit"`
- Trace file and experiment file both valid and viewable

**Browser Cache Note**: Initially appeared as "0 tests" in UI due to browser cache, but backend correctly returned `test_count: 1`. Hard refresh resolved display issue.

### ✅ Multiple Scorer Display - ALREADY IMPLEMENTED

**Status**: This feature was already fully implemented in a previous session.

**Implementation** ([base.js:205-272](../util/prompt-optimization/viewer/frontend/js/renderers/base.js#L205-L272), [viewer.css:728-810](../util/prompt-optimization/viewer/frontend/css/viewer.css#L728-L810)):

1. **JavaScript rendering**:
   - Loops through `test.scores` object (e.g., `{judge: {...}, exact_match: {...}}`)
   - Creates `<details>` element for each scorer with:
     - Summary: badge (PASS/FAIL), scorer name, score
     - Content: reasoning text and additional metrics
   - Auto-expands if ≤3 scorers for better UX

2. **CSS styling**:
   - `.judges-container` - vertical layout with left border
   - `.judge-detail` - card style with rounded corners
   - `.judge-header` - clickable summary with hover effects
   - `.judge-reasoning` - formatted reasoning display
   - `.judge-metrics` - inline metrics with labels

**Verification**: Capability test result displays single scorer ("judge") correctly. Multiple scorers would display as separate collapsible sections.

### ✅ Trace View Flicker Fix - COMPLETE

**Date**: December 11, 2025

Fixed flicker when navigating to sample page by creating DOM structure once and reusing it.

**Problem**: Trace viewer replaced container innerHTML twice:
1. Line 122: Set to "Loading trace events..."
2. Line 210: Clear and rebuild entire structure
3. This caused visible flicker during transition

**Solution** ([embedded-trace-viewer.js:41-57, 234-245](../util/prompt-optimization/viewer/frontend/js/embedded-trace-viewer.js#L41-L57)):

1. Added `initializeStructure()` method called in constructor:
   - Creates wrapper and eventsContainer once
   - Sets up globals (window.traceLoader, window.pluginRegistry)
   - Stores reference to eventsContainer

2. Updated `loadEvents()` to use `eventsContainer.innerHTML`:
   - Only updates events content, not wrapper structure
   - No more double-replace

3. Simplified `render()` method:
   - Only clears eventsContainer
   - Calls traceLoader.render()
   - Structure remains intact

**Result**: ✅ No more flicker when loading traces - smooth transition from "Loading..." to rendered events.

### ✅ Multiple Scorers Verification - COMPLETE

**Date**: December 11, 2025

Created test experiment with 3 scorers to verify display:

**Test File**: `results/multiscorer_test_20251211_200000.006eval.jsonl`
- 2 tests, each with 3 scorers:
  - `llm_judge` - LLM evaluation with reasoning and metrics
  - `exact_match` - Exact string matching
  - `semantic_similarity` - Cosine similarity with metrics

**Verification**:
- Experiment accessible at: `http://0.0.0.0:5003/#/experiment/multiscorer_test_20251211_200000/test/test_001`
- All 3 scorers display as separate collapsible sections
- Each shows badge, score, reasoning, and metrics
- Auto-expands (≤3 scorers)

## Summary - All Tasks Complete! 🎉

1. ✅ Raw JSON expander on test detail page
2. ✅ Capability tests verification with ExperimentWriter
3. ✅ Multiple scorer display (already implemented)
4. ✅ Trace view flicker fix
5. ✅ Multiple scorers test creation and verification

### Next Steps

1. **Future enhancements**:
   - Incremental viewing: Add auto-refresh for "running" experiments
   - Error handling: Audit for edge cases and improve robustness

## Testing

The viewer is currently running on port 5002. Available test files:
- `results/evaluation/sentiment_20251211_170512.006eval.jsonl`
- `results/evaluation/sentiment_20251211_160515.006eval.jsonl`
- `results/examples/demoexperiment_20251211_140936.006eval.jsonl`

To test the trace viewer integration:
1. Navigate to http://localhost:5002
2. Select a sentiment experiment
3. Click on a test to open the detail page
4. Verify the trace events display with the actual trace viewer rendering

## 2025-12-12: evaluation/runner.py Breaking JSONL Format Again

### The Problem Returns

**Symptom**: 500 error when trying to load traces for capability tests:
```
GET http://0.0.0.0:5003/api/experiment/capability_tests_20251212_055256/trace/sentiment_single_001 500
json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
```

**Root Cause**: `evaluation/runner.py:729` was writing pretty-printed JSON with `indent=2`:
```python
json.dump(final_data, f, indent=2, default=str)  # ❌ Creates 246-line file
```

This creates multi-line JSON that the viewer can't parse as JSONL. When the parser tries to read line 1 (`{`), it's not valid JSON by itself.

### Why This Happened

After ExperimentWriter was fixed (see above), `evaluation/runner.py` still had the old finalization code that rewrites with pretty-printing. The two systems were out of sync.

### The Fix

Updated `evaluation/runner.py:723-737` to match `ExperimentWriter.finalize()` behavior:

```python
# Rewrite file in JSONL format (line 1 = metadata, lines 2+ = results)
with open(self._current_experiment_path, "w") as f:
    # Line 1: Metadata line (single-line JSON, no indent)
    metadata_line = {
        "metadata": metadata or {},
        "results": [],
    }
    json.dump(metadata_line, f, default=str)
    f.write("\n")

    # Lines 2+: Each result as single-line JSON
    for result in results:
        json.dump(result, f, default=str)
        f.write("\n")
```

**Key Change**: No `indent=2` parameter - writes single-line JSON for each line, maintaining JSONL format.

### Status

✅ evaluation/runner.py now writes JSONL consistently
✅ Matches ExperimentWriter.finalize() behavior
✅ prompt-optimization/runner.py compare function fixed to write JSONL
⏳ Old files with pretty-printed JSON will still cause errors (need regeneration)

### Additional Fix: prompt-optimization/runner.py

The `compare()` function was writing single-file JSON instead of JSONL:
```python
# OLD: Single-line JSON (defeats incremental viewing)
results_file = results_dir / f"compare_{config_suffix}_{timestamp}.006eval.json"
json.dump(combined_json, f, default=str)
```

Fixed to write proper JSONL format (line 1 = metadata, lines 2+ = results):
```python
# NEW: JSONL format (viewer-compatible, supports incremental viewing)
results_file = results_dir / f"compare_{config_suffix}_{timestamp}.006eval.jsonl"
# Line 1: Metadata
metadata_line = {"metadata": combined_json["metadata"], "results": []}
json.dump(metadata_line, f, default=str)
f.write("\n")
# Lines 2+: Results
for result in combined_json["results"]:
    json.dump(result, f, default=str)
    f.write("\n")
```

### Prevention

- **JSONL is the ONLY format** - all `.006eval.jsonl` files must use line-based format
- Never use `json.dump(..., indent=N)` - this breaks line-based parsing
- Always write: Line 1 = `{"metadata": {...}, "results": []}`, Lines 2+ = individual results
- Use ExperimentWriter or follow its pattern exactly
- Single-line JSON files defeat the purpose of JSONL (incremental writing/viewing)

## 2025-12-12: Missing Input/Output/Expected in Capability Tests

### Problem

Test detail view wasn't displaying input, expected output, or actual output for capability tests.

**Symptom**: Capability test detail page showed empty I/O fields despite test passing/failing.

**Root Cause**: `run_custom_test()` in `prompt-optimization/runner.py` wasn't extracting `input`, `output`, `expected` from eval_result.

### The Fix

Updated `prompt-optimization/runner.py:882-906` to extract I/O fields:

```python
# Extract input/output/expected from eval_result.metrics for viewer display
metrics = eval_result.metrics or {}
input_value = None
output_value = metrics.get("result")  # Actual output
expected_value = metrics.get("expected")  # Expected output

# Try to extract input from first turn of trace
if llm_trace and "turns" in llm_trace and llm_trace["turns"]:
    first_turn = llm_trace["turns"][0]
    input_value = first_turn.get("task")

return {
    "test": test_name,
    "display_name": test_config.get("name", test_name),
    "input": input_value,
    "output": output_value,
    "expected": expected_value,
    # ... rest of fields
}
```

### Result

✅ Test detail view now shows:
- **Input**: Task description with parameters
- **Output**: Actual result (e.g., "positive")
- **Expected**: Expected result (e.g., "positive")

This enables proper debugging and review of test results in the viewer.

### Follow-up Fix: Parse Input Parameters Only

**Problem**: The input field was showing the full task prompt (including all instructions and warnings), not just the actual input parameters.

**Solution** (commit `798b970`): Parse out just the `## Input parameters:` section from the trace task:
- For sentiment: `text = "I absolutely love..."`
- For calculate: `a=17, b=23`
- For batch tests: `pairs = [(3, 4), (5, 6), ...]`

The viewer now displays clean, readable input values that users can quickly understand.

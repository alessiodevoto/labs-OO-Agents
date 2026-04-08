# Viewer Improvements Log

## 2025-12-11: Compact Scorer Display Implementation

### Summary
Implemented a compact, single-line display for multiple scorers in test detail views with progressive disclosure for metadata.

### Changes Made

#### 1. Compact Display Layout
**Files**: `base.js`, `viewer.css`

- Replaced collapsible scorer sections with one-line display
- Layout order: badge → name → reason → score (rightmost)
- Badge shows pass/fail (✓/✗)
- Score is always right-aligned and fixed width
- Reasoning text shows inline when collapsed, hidden when expanded

#### 2. Data Source Flexibility
**File**: `base.js:205-217`

- Collect scorers from both `test.scores` AND `test.metrics.scorers`
- Handle output from both `test.output` AND `test.metrics.actual`
- Supports multiple test result formats

#### 3. Progressive Disclosure
**File**: `base.js:233-308`

Scorers with metadata (reasoning, weight, metrics) use `<details>/<summary>`:
- **Collapsed state**: Shows badge, name, reason (inline, truncated with ellipsis), score
- **Expanded state**: Shows full details including reasoning, weight, and all metrics
- Scorers without metadata remain simple one-line displays

#### 4. Keyboard Navigation Integration
**Files**: `base.js`, `keyboard-nav.js:362-373`

- Added `.kb-block` class to all scorer elements
- Expandable scorers marked with `data-kb-expandable="true"`
- Navigate with j/k keys
- Expand/collapse with Enter/→ keys
- **Collapse-first behavior**: Left arrow (←/h/Backspace) collapses expanded scorers before navigating back
- Detects `<details>` elements using `block.tagName === 'DETAILS'` and checks `block.open` state

#### 5. CSS Styling
**File**: `viewer.css:728-842`

Key styles:
- `.judges-container-compact`: Vertical stack with 2px gap
- `.judge-row-compact`: Flexbox row with 8px gaps
- `.judge-name-compact`: Fixed width, flex-shrink: 0
- `.judge-reason-inline`: Flexible width (flex: 1), truncates with ellipsis
- `.judge-score-compact`: Fixed width, right-aligned, flex-shrink: 0, margin-left: 8px
- Keyboard selection highlighting with `.keyboard-selected` class
- Auto-hide inline reason when details expanded

### Visual Design

```
Collapsed scorer:
[✓] llm_judge  The response correctly addresses... 0.90
└─────┬─────┘ └──────────────┬────────────────┘ └─┬─┘
    badge              reason (truncated)      score

Expanded scorer:
[✗] exact_match  Output does not match... 0.00
│
│   Output does not match expected result.
│
│   tokens: 75
│   latency_ms: 1234
```

### Rationale

1. **Vertical Space Efficiency**: One-line display allows viewing many scorers without scrolling
2. **Information Priority**: Most important info (pass/fail, name, score) always visible
3. **Progressive Disclosure**: Details available on-demand without cluttering the view
4. **Keyboard-First**: Full keyboard navigation support for power users
5. **Consistency**: Same layout whether scorer has metadata or not

### Testing Notes

- Tested with tests having multiple scorers (llm_judge, exact_match, semantic_similarity)
- Verified inline reason display and ellipsis truncation
- Confirmed keyboard navigation and collapse-first behavior
- Checked both expandable and non-expandable scorers

## 2025-12-11: Flicker Fix and Keyboard Navigation Improvements

### Trace View Flicker Fix
**Files**: `embedded-trace-viewer.js:41-62, 138-170`, `base.js:38-57`, `views.js:958-973`

**Problem**: Trace viewer caused flicker with multiple loading states and double-fetching of trace data

**Root Cause**:
1. views.js fetched trace events and attached to `test.otel_events`
2. EmbeddedTraceViewer ignored preloaded events and fetched again
3. Multiple loading states: "Loading test..." → "Loading trace events..." → events

**Solution** (three-part fix):
1. **In `embedded-trace-viewer.js`**:
   - Accept `preloadedEvents` option in constructor
   - Only show "Loading..." if events need to be fetched
   - Skip API fetch if preloaded events available
2. **In `base.js`**:
   - Pass `test.otel_events` to viewer as `preloadedEvents`
   - Remove `setTimeout` and create viewer synchronously
3. **Result**:
   - No double-fetch of trace data
   - No loading message when events already loaded
   - Trace viewer renders immediately with data

### Keyboard Navigation Fix
**File**: `keyboard-nav.js:345-376`

**Problem**: Pressing left arrow on expanded scorer details navigated to parent page instead of collapsing

**Solution**:
- Moved expandable check BEFORE eventContainer check
- Now scorers collapse first, then navigation happens
- Logic order: expandable check → trace event check → navigate back

### Testing
- Verified scorer collapse-first behavior works
- Confirmed no visible flicker when loading trace view
- Tested with both scorer details and trace events

## 2025-12-11: Score Alignment and Copy/Paste Fix

### Score Right-Alignment Fix
**File**: `base.js:259-265, 326-329`

**Problem**: When scorers had no reasoning text, the score wasn't right-aligned because there was no flex: 1 spacer element

**Solution**:
- Always add reason span (even if empty) for expandable scorers
- Add spacer element for non-expandable scorers
- Ensures consistent flex layout: badge → name → spacer (flex: 1) → score (fixed right)

### OS Shortcuts Fix (Cmd+C Copy)
**File**: `keyboard-nav.js:35-40`

**Problem**: Keyboard navigation was intercepting standard OS shortcuts like Cmd+C (copy), preventing text copying

**Solution**:
- Added early return for Cmd/Ctrl key combinations (except navigation keys)
- Allows browser to handle copy/paste/cut/undo/redo/select-all
- Preserves our Cmd+Up/Down navigation for samples
- Check happens before any keyboard navigation handling

**Implementation**:
```javascript
if ((e.metaKey || e.ctrlKey) && !['ArrowUp', 'ArrowDown', 'j', 'k'].includes(e.key)) {
    return;  // Let browser handle standard OS shortcuts
}
```

## 2025-12-11: Navigation Bug Fix

### clearKbSelection Method Name Error
**File**: `keyboard-nav.js:92`

**Problem**: When navigating from sample view to experiment view, got error: `this.clearKbSelection is not a function`

**Solution**:
- Fixed method name typo: `clearKbSelection()` → `clearSelection()`
- The method was correctly defined as `clearSelection()` but was being called with the wrong name

## 2025-12-11: Commits and Status

### Commits Made
**Branch**: `feat/e2e-optimization-loop`

1. **Commit 56ada8a**: "feat(viewer): compact scorer display with keyboard navigation"
   - Compact one-line scorer display with progressive disclosure
   - Keyboard navigation with expand/collapse support
   - OS shortcuts fix (Cmd+C, Cmd+V work correctly)
   - Score alignment fix with flex layout
   - Navigation bug fix (clearKbSelection → clearSelection)

2. **Commit c623e4a**: "feat: integrate trace viewer, add e2e optimization examples, and update documentation"
   - Trace viewer integration with backend API
   - E2E optimization examples (capability, compute, sentiment)
   - Comprehensive documentation (8 new docs files)
   - ExperimentWriter for programmatic result generation
   - Linting fixes with documented noqa tags

### Current Status

**Working Features:**
- ✅ Compact scorer display (one line per scorer)
- ✅ Progressive disclosure with `<details>/<summary>`
- ✅ Keyboard navigation (j/k, expand/collapse)
- ✅ OS shortcuts (Cmd+C, Cmd+V, etc.)
- ✅ Trace viewer integration
- ✅ All changes committed to branch

**Known Issues:**
- ⚠️ **Trace viewer flicker**: Still exists (see note below)
- The flicker fix documented above (lines 88-127) was attempted but reverted
- The attempt broke the trace viewer completely due to DOM timing issues
- Current implementation uses `setTimeout(0)` which causes flicker but works reliably

**Flicker Fix Note:**
The documentation for "Flicker Fix and Keyboard Navigation Improvements" (lines 88-127) describes an aspirational solution that was implemented but then reverted. The issue is that:
1. Removing `setTimeout` causes TraceLoader to fail because `#trace-events` isn't in the DOM yet
2. Preloading events creates complex timing issues with TraceLoader's internal state
3. The working version with `setTimeout` remains in place, accepting the minor flicker as a trade-off for reliability

## 2025-12-11: Flicker Fix - Simple Solution

### Double-Fetch Elimination
**File**: `views.js:958-973`
**Commit**: 89f632d

**Problem**:
- views.js was fetching trace events and attaching to `test.otel_events`
- EmbeddedTraceViewer was then fetching the same events again
- This caused a visible flicker: "Loading test..." → "Loading trace events..." → events displayed

**Solution**:
Simply removed the trace fetch from views.js entirely. Let EmbeddedTraceViewer handle all trace loading.

**Code Removed**:
```javascript
// Fetch trace events for this test (from trace file)
let traceEvents = [];
try {
    const traceResponse = await fetch(`/api/experiment/${experimentId}/trace/${encodeURIComponent(testId)}`);
    if (traceResponse.ok) {
        const traceData = await traceResponse.json();
        traceEvents = traceData.events || [];
    }
} catch (e) {
    console.warn('Failed to load trace events:', e);
}

// Attach trace events to test for renderer to use
if (traceEvents.length > 0) {
    test.otel_events = traceEvents;
}
```

**Result**:
- ✅ Single fetch of trace data
- ✅ No "Loading trace events..." flicker
- ✅ Simpler code (one fewer responsibility in views.js)
- ✅ Still uses `setTimeout(0)` for DOM timing safety, but no double-fetch

**Why This Works Better Than Preloading**:
The complex preloading approach (lines 88-127 above) tried to share data between views.js and EmbeddedTraceViewer. This is simpler: just let EmbeddedTraceViewer own the entire trace loading responsibility.

### Future Improvements

- Consider adding hover tooltips for truncated reasoning text
- Explore color coding for score ranges (red/yellow/green)
- Could further reduce flicker by using `requestAnimationFrame` instead of `setTimeout` for DOM attachment

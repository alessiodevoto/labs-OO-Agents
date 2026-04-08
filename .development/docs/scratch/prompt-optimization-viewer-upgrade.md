# Agent006 Evaluation Viewer Upgrade Plan

## Overview

Upgrade the prompt-optimization viewer to become the **Agent006 Evaluation Viewer** with improvements based on the trace-viewer's design patterns.

## Goals

1. **Rename**: Change displayed name to "Agent006 Evaluation Viewer"
2. **Keyboard Navigation**: Bring trace-viewer's plugin-based keyboard navigation structure
3. **Bulletproof Experiment Detection**: Fix failure modes in running experiment detection
4. **Embedded Trace View**: Remove separate "turns" view, embed trace events directly in sample view
5. **Smooth Transitions**: Eliminate flashing when switching views

## Runners to Support

| Runner | Location | Output Pattern | Status Field |
|--------|----------|----------------|--------------|
| prompt-optimization | `util/prompt-optimization/runner.py` | `*.006eval.jsonl` | `metadata.status` |
| evaluation runner | `evaluation/runner.py` | `*.006eval.jsonl` | `metadata.status` |
| e2e file-based optimizer | `util/e2e_optimization/lib/experiment_format.py` | `*.006eval.jsonl` | `metadata.status` |

**All runners must write the same format** with `"status": "running"` or `"status": "completed"`.

### Standardization Required: `evaluation/runner.py`

Currently `evaluation/runner.py` outputs:
- Individual results: `{task_id}.006eval.json` (no status)
- Reports: `*_report.json` (no status)

**Changes needed:**
1. Output single `.006eval.jsonl` file per benchmark run (not per-task)
2. Add `metadata.status` field (`"running"` during execution, `"completed"` at end)
3. Write incrementally so viewer can show progress

---

## 1. Rename to Agent006 Evaluation Viewer

### Files to Update

| File | Change |
|------|--------|
| `viewer/frontend/index.html` | Change `<title>` and `<h1>` |
| `viewer/backend/main.py` | Update any title references |

---

## 2. Bulletproof Experiment Detection

### Design Goals

1. **Debuggable without frontend**: CLI tools to inspect experiment status
2. **Support both runners**: Same detection logic works for both
3. **Clear failure states**: Distinguish stale, corrupt, running, completed

### Experiment States

```
┌──────────────┐
│   running    │ ─── file actively being written (mtime < 60s ago)
└──────┬───────┘
       │
       ├─── runner finishes normally ──► completed
       │
       ├─── runner crashes ──► stale (detected by mtime > 60s + status=running)
       │
       └─── JSON write interrupted ──► corrupt (JSON parse fails)
```

### State Detection Logic

```python
def get_experiment_state(file_path: Path) -> dict:
    """Determine experiment state from file.

    Returns:
        {
            "state": "running" | "completed" | "stale" | "corrupt" | "not_found",
            "status_in_file": str | None,  # What the file says
            "mtime": float | None,         # Last modified timestamp
            "age_seconds": float | None,   # How old is the file
            "reason": str | None,          # Why we classified it this way
        }
    """
    if not file_path.exists():
        return {"state": "not_found", "reason": "File does not exist"}

    mtime = file_path.stat().st_mtime
    age_seconds = time.time() - mtime

    # Try to parse JSON
    try:
        with open(file_path) as f:
            data = json.load(f)
        status_in_file = data.get("metadata", {}).get("status", "unknown")
    except json.JSONDecodeError as e:
        return {
            "state": "corrupt",
            "mtime": mtime,
            "age_seconds": age_seconds,
            "reason": f"JSON parse error: {e}",
        }

    # Determine state
    STALE_THRESHOLD = 60  # seconds

    if status_in_file == "completed":
        return {
            "state": "completed",
            "status_in_file": status_in_file,
            "mtime": mtime,
            "age_seconds": age_seconds,
        }

    if status_in_file == "running":
        if age_seconds > STALE_THRESHOLD:
            return {
                "state": "stale",
                "status_in_file": status_in_file,
                "mtime": mtime,
                "age_seconds": age_seconds,
                "reason": f"File says 'running' but hasn't been updated in {age_seconds:.0f}s",
            }
        return {
            "state": "running",
            "status_in_file": status_in_file,
            "mtime": mtime,
            "age_seconds": age_seconds,
        }

    # Unknown status
    return {
        "state": "unknown",
        "status_in_file": status_in_file,
        "mtime": mtime,
        "age_seconds": age_seconds,
        "reason": f"Unexpected status value: {status_in_file}",
    }
```

### CLI Debug Tool

Create `util/prompt-optimization/viewer/debug_experiments.py`:

```python
#!/usr/bin/env python3
"""Debug tool for experiment status detection.

Usage:
    # Check all experiments
    python debug_experiments.py

    # Check specific experiment
    python debug_experiments.py --file results/sentiment_20241210_123456.006eval.jsonl

    # Watch mode (poll every 2s)
    python debug_experiments.py --watch

    # Simulate runner crash (for testing stale detection)
    python debug_experiments.py --simulate-crash results/test.006eval.jsonl
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

STALE_THRESHOLD = 60  # seconds

def get_experiment_state(file_path: Path) -> dict:
    """Get experiment state - same logic as backend."""
    if not file_path.exists():
        return {"state": "not_found", "path": str(file_path)}

    stat = file_path.stat()
    mtime = stat.st_mtime
    age_seconds = time.time() - mtime

    try:
        with open(file_path) as f:
            data = json.load(f)
        status = data.get("metadata", {}).get("status", "unknown")
        test_count = len(data.get("results", []))
    except json.JSONDecodeError as e:
        return {
            "state": "corrupt",
            "path": str(file_path),
            "age_seconds": round(age_seconds, 1),
            "error": str(e),
        }

    # Determine state
    if status == "completed":
        state = "completed"
    elif status == "running" and age_seconds > STALE_THRESHOLD:
        state = "stale"
    elif status == "running":
        state = "running"
    else:
        state = "unknown"

    return {
        "state": state,
        "path": str(file_path),
        "status_in_file": status,
        "age_seconds": round(age_seconds, 1),
        "test_count": test_count,
        "mtime": datetime.fromtimestamp(mtime).isoformat(),
    }

def print_state(state: dict):
    """Pretty-print experiment state."""
    icons = {
        "running": "🟢",
        "completed": "✅",
        "stale": "💀",
        "corrupt": "❌",
        "not_found": "❓",
        "unknown": "⚠️",
    }
    icon = icons.get(state["state"], "?")

    print(f"{icon} {state['state'].upper():10} {state.get('path', 'N/A')}")
    if state.get("status_in_file"):
        print(f"   File says: {state['status_in_file']}")
    if state.get("age_seconds") is not None:
        print(f"   Age: {state['age_seconds']}s")
    if state.get("test_count") is not None:
        print(f"   Tests: {state['test_count']}")
    if state.get("error"):
        print(f"   Error: {state['error']}")
    print()

def find_experiments(results_dir: Path) -> list[Path]:
    """Find all .006eval.jsonl files."""
    return sorted(results_dir.glob("*.006eval.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

def simulate_crash(file_path: Path):
    """Create a file that looks like a crashed runner."""
    data = {
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "status": "running",  # Never completed
            "suite_name": "simulated_crash_test",
        },
        "results": [],
    }
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(data, f)
    print(f"Created simulated crash file: {file_path}")
    print(f"Wait {STALE_THRESHOLD}s, then run debug_experiments.py to see 'stale' detection")

def main():
    parser = argparse.ArgumentParser(description="Debug experiment status detection")
    parser.add_argument("--file", type=Path, help="Check specific file")
    parser.add_argument("--watch", action="store_true", help="Poll every 2s")
    parser.add_argument("--simulate-crash", type=Path, metavar="FILE", help="Create crashed experiment file")
    parser.add_argument("--results-dir", type=Path, default=Path("results"), help="Results directory")
    args = parser.parse_args()

    if args.simulate_crash:
        simulate_crash(args.simulate_crash)
        return

    while True:
        print(f"\n{'='*60}")
        print(f"Experiment Status @ {datetime.now().isoformat()}")
        print(f"{'='*60}\n")

        if args.file:
            state = get_experiment_state(args.file)
            print_state(state)
        else:
            experiments = find_experiments(args.results_dir)
            if not experiments:
                print(f"No .006eval.jsonl files in {args.results_dir}")
            for exp_path in experiments[:10]:  # Show last 10
                state = get_experiment_state(exp_path)
                print_state(state)

        if not args.watch:
            break
        time.sleep(2)

if __name__ == "__main__":
    main()
```

### Backend API Changes

**File: `viewer/backend/main.py`**

```python
@app.get("/api/experiments/{exp_id}/health")
async def experiment_health(exp_id: str):
    """Get experiment health status.

    Returns same structure as CLI debug tool for consistency.
    """
    file_path = get_experiment_path(exp_id)
    return get_experiment_state(file_path)  # Reuse same logic

@app.get("/api/health")
async def system_health():
    """Overall system health check."""
    results_dir = get_results_dir()
    experiments = list(results_dir.glob("*.006eval.jsonl"))

    states = [get_experiment_state(p) for p in experiments[-10:]]
    running = sum(1 for s in states if s["state"] == "running")
    stale = sum(1 for s in states if s["state"] == "stale")

    return {
        "status": "healthy",
        "experiment_count": len(experiments),
        "running": running,
        "stale": stale,
        "stale_threshold_seconds": STALE_THRESHOLD,
    }
```

### Frontend Changes

**File: `viewer/frontend/js/live-updater.js`**

```javascript
class LiveUpdater {
    constructor() {
        this.pollInterval = 2000;  // 2 seconds
        this.staleThreshold = 60;  // Must match backend
    }

    async checkHealth(experimentId) {
        const response = await fetch(`/api/experiments/${experimentId}/health`);
        const health = await response.json();

        this.updateStatusIndicator(health.state);

        // Only continue polling if running
        return health.state === 'running';
    }

    updateStatusIndicator(state) {
        const indicator = document.getElementById('status-indicator');
        const text = document.getElementById('status-text');

        const stateConfig = {
            'running': { class: 'running', text: 'Running...', pulse: true },
            'completed': { class: 'completed', text: 'Complete', pulse: false },
            'stale': { class: 'stale', text: 'Stale (runner crashed?)', pulse: false },
            'corrupt': { class: 'error', text: 'Corrupt file', pulse: false },
        };

        const config = stateConfig[state] || { class: 'unknown', text: state, pulse: false };

        indicator.className = `status-indicator ${config.class}`;
        text.textContent = config.text;
        indicator.querySelector('.pulse').style.display = config.pulse ? 'block' : 'none';
    }
}
```

### Testing the Detection System

Test all three runners with the debug tool:

```bash
# ============================================
# Test 1: prompt-optimization runner
# ============================================
cd util/prompt-optimization
python runner.py config/sentiment.yaml --models qwen3-coder-480b &
RUNNER_PID=$!

# Watch in another terminal
python viewer/debug_experiments.py --watch --results-dir results

# Kill mid-run to test stale detection
kill $RUNNER_PID
# Wait 60s, should show "STALE"

# ============================================
# Test 2: evaluation runner (e2e_optimization evaluate)
# ============================================
cd /path/to/project
python -m e2e_optimization.cli evaluate --example sentiment &
RUNNER_PID=$!

# Watch
python util/prompt-optimization/viewer/debug_experiments.py --watch --results-dir results/evaluation

# Kill mid-run
kill $RUNNER_PID
# Wait 60s, should show "STALE"

# ============================================
# Test 3: Corrupt file detection
# ============================================
echo "invalid json{" > results/corrupt_test.006eval.jsonl
python viewer/debug_experiments.py --file results/corrupt_test.006eval.jsonl
# Should show "CORRUPT"

# Cleanup
rm results/corrupt_test.006eval.jsonl

# ============================================
# Test 4: Simulate crash (quick test)
# ============================================
python viewer/debug_experiments.py --simulate-crash results/crash_test.006eval.jsonl
# Wait 60s
python viewer/debug_experiments.py --file results/crash_test.006eval.jsonl
# Should show "STALE"
```

---

## 3. Keyboard Navigation Structure

### Current State (prompt-optimization)
- Basic keyboard-nav.js with direct key handlers
- No plugin registry for extensibility
- Navigation tied to specific view implementations

### Target State (from trace-viewer)
- Plugin-based keyboard handler registration
- Context-aware shortcuts (different per view)
- Navigation abstracted through a registry

### Changes Required

**File: `viewer/frontend/js/keyboard-nav.js`**

Refactor to use a more structured approach:

```javascript
// Registry of handlers per context
const keyboardHandlers = {
    'experiment-list': { ... },
    'summary': { ... },
    'sample': { ... }
};

// Active context tracking
let activeContext = 'experiment-list';

// Unified key handler
function handleKeyPress(e) {
    const handlers = keyboardHandlers[activeContext];
    if (handlers && handlers[e.key]) {
        handlers[e.key](e);
    }
}
```

---

## 4. Embed Trace View into Sample View

### Current Flow

```
Summary View → Sample View → Turns View (separate)
                    ↓
              Click "View Trace"
                    ↓
              Turns View (full page)
```

### Target Flow

```
Summary View → Sample View (with embedded trace events)
                    ↓
              Trace events inline
              Filter panel embedded
              No timeline (optional)
```

### Navigation Changes

| Current Key | Current Action | New Key | New Action |
|-------------|----------------|---------|------------|
| `j`/`↓` | Next turn (in turns view) | `j`/`↓` | Next trace event (in sample view) |
| `k`/`↑` | Prev turn | `k`/`↑` | Prev trace event |
| - | - | `Cmd+↓` | Next sample |
| - | - | `Cmd+↑` | Prev sample |

### Changes Required

**File: `viewer/frontend/js/renderers/base.js`**

Add embedded trace view rendering:

```javascript
renderEmbeddedTraceView(test, context) {
    const container = document.createElement('div');
    container.className = 'embedded-trace-view';

    // 1. Filter panel (from shared-filter-manager.js)
    const filterPanel = this.createTraceFilterPanel(test);
    container.appendChild(filterPanel);

    // 2. Trace events list (reuse trace-viewer's event rendering)
    const eventsList = this.createTraceEventsList(test.otel_events);
    container.appendChild(eventsList);

    return container;
}
```

**File: `viewer/frontend/js/keyboard-nav.js`**

Add Cmd+Arrow handlers for sample navigation:

```javascript
// In sample view context
handleKey(e) {
    // Cmd+Up/Down for sample navigation
    if (e.metaKey || e.ctrlKey) {
        if (e.key === 'ArrowUp') {
            e.preventDefault();
            this.prevSample();
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            this.nextSample();
        }
        return;
    }

    // Regular j/k for trace event navigation
    if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault();
        this.nextTraceEvent();
    } else if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault();
        this.prevTraceEvent();
    }
}
```

**File: `viewer/frontend/index.html`**

Remove turns view, update help:

```html
<!-- Remove: View 4: Turns View -->
<!-- Was: <div id="turns-view" class="view hidden"> ... </div> -->

<!-- Update help overlay -->
<dt><kbd>Cmd+↓</kbd></dt>
<dd>Next sample</dd>
<dt><kbd>Cmd+↑</kbd></dt>
<dd>Previous sample</dd>
<dt><kbd>j</kbd>/<kbd>↓</kbd></dt>
<dd>Next trace event</dd>
<dt><kbd>k</kbd>/<kbd>↑</kbd></dt>
<dd>Previous trace event</dd>
```

---

## 5. Smooth View Transitions

### Current Problem

Views flash when transitioning because:
1. Old view is hidden immediately
2. New view content is loaded async
3. "Loading..." flash visible during fetch

### Solution: Pre-render and Crossfade

**File: `viewer/frontend/css/main.css`**

```css
.view {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    opacity: 0;
    visibility: hidden;
    transition: opacity 150ms ease-in-out, visibility 0s linear 150ms;
}

.view.active {
    opacity: 1;
    visibility: visible;
    transition: opacity 150ms ease-in-out, visibility 0s;
}

/* Pre-render container - off-screen but rendered */
.view.prerender {
    visibility: visible;
    opacity: 0;
    pointer-events: none;
}
```

**File: `viewer/frontend/js/views.js`**

```javascript
async transitionToView(viewId, dataLoader) {
    const currentView = document.querySelector('.view.active');
    const newView = document.getElementById(viewId);

    // 1. Start loading data while current view still visible
    const loadingPromise = dataLoader();

    // 2. Pre-render new view (invisible but laid out)
    newView.classList.add('prerender');

    // 3. Wait for data
    await loadingPromise;

    // 4. Populate new view content (still invisible)
    // ... (view-specific population code)

    // 5. Start crossfade
    currentView.classList.remove('active');
    newView.classList.remove('prerender');
    newView.classList.add('active');

    // 6. After transition, fully hide old view
    setTimeout(() => {
        currentView.classList.add('hidden');
    }, 150);
}
```

---

## Implementation Order

1. **Phase 1: Rename + Smooth Transitions** (low risk, immediate UX improvement)
   - Update title/heading to "Agent006 Evaluation Viewer"
   - Add CSS transitions
   - Refactor view switching to use pre-render pattern

2. **Phase 2: Experiment Detection** (runners + backend + CLI + frontend)
   - **Standardize `evaluation/runner.py`**: output `.006eval.jsonl` with `metadata.status`
   - Create `debug_experiments.py` CLI tool
   - Add `/api/experiments/{id}/health` endpoint
   - Add `/api/health` system endpoint
   - Update live-updater to use health checks
   - Add stale/corrupt state handling in UI
   - **Test all 3 runners** with debug tool

3. **Phase 3: Keyboard Navigation** (refactor)
   - Create context-based handler registry
   - Migrate existing handlers
   - Add Cmd+Arrow sample navigation

4. **Phase 4: Embedded Trace View** (largest change)
   - Add embedded trace rendering to base renderer
   - Wire up event navigation
   - Remove turns view
   - Update help overlay

---

## Files to Modify

| File | Changes |
|------|---------|
| `evaluation/runner.py` | **Standardize output**: `.006eval.jsonl` with `metadata.status` |
| `viewer/frontend/index.html` | Rename title, remove turns view, update help |
| `viewer/frontend/css/main.css` | Add transition classes, status indicator styles |
| `viewer/frontend/js/views.js` | Pre-render pattern |
| `viewer/backend/main.py` | Health endpoints |
| `viewer/debug_experiments.py` | **NEW** - CLI debug tool |
| `viewer/frontend/js/live-updater.js` | Health check integration |
| `viewer/frontend/js/keyboard-nav.js` | Context registry, Cmd+Arrow |
| `viewer/frontend/js/renderers/base.js` | Embedded trace rendering |

## Dependencies

- Shared plugins from `viewer_utils/frontend/plugins/`
- `SharedFilterManager` from `viewer_utils/frontend/js/shared-filter-manager.js`
- `trace-utils.js` for trace viewer URL building

---

## Testing

### 1. Rename
- Open viewer, verify title shows "Agent006 Evaluation Viewer"

### 2. Experiment Detection (use CLI tool)
```bash
# Test normal flow
python runner.py config/sentiment.yaml &
python viewer/debug_experiments.py --watch
# Should show "RUNNING"

# Test stale detection
kill $!  # Kill runner
# Wait 60s, should show "STALE"

# Test corrupt detection
echo "bad{json" > results/test.006eval.jsonl
python viewer/debug_experiments.py --file results/test.006eval.jsonl
# Should show "CORRUPT"
```

### 3. Smooth Transitions
- Visual inspection, no flash when navigating between views

### 4. Keyboard Navigation
- j/k moves through trace events in sample view
- Cmd+Up/Down switches samples

### 5. Embedded Trace View
- All trace events visible in sample view
- Filter panel works
- No separate turns view link

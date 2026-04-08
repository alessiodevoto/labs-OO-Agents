# Prompt Optimization Viewer - Status Indicator Fix

**Date:** 2025-12-11
**Issue:** Experiment shows as "running" in list but no live indicator appears

## Problem Description

After completing a test run, experiments were showing inconsistent status:
- **Experiment list**: Shows correct status from metadata ("completed") ✓
- **Live indicator**: Not appearing even for running experiments ✗

The root cause was **two different status detection mechanisms** in the backend that were inconsistent with each other.

## Root Cause Analysis

### Backend Status Detection (Before Fix)

1. **`/api/experiments` endpoint** (line 427-434 in `main.py`):
   - Reads `status` field from `exp.metadata.status`
   - Applies staleness detection (60 second threshold) for "running" status
   - **Result:** Correctly shows "completed" for finished experiments

2. **`/api/experiment/{id}/status` endpoint** (line 503-506):
   - **Completely ignored metadata status field**
   - Only checked file modification time (< 5 seconds = running)
   - **Result:** Always shows `running=false` after 5 seconds, even if metadata says "running"

### Live Updater Logic

The `LiveUpdater` class polls `/api/experiment/{id}/status` every 2 seconds to:
- Update the live indicator badge
- Detect when new tests are added
- Trigger view refreshes

Because the status endpoint ignored metadata, the live indicator would:
- Disappear 5 seconds after last file write (even if experiment still running)
- Never appear for completed experiments (correct behavior)

## The Fix

### Backend Changes (`viewer/backend/main.py`)

Modified `/api/experiment/{id}/status` to check metadata status first:

```python
# Check status from metadata first (authoritative)
status = getattr(exp.metadata, "status", None)

if status == "completed":
    # Experiment explicitly marked as completed
    is_running = False
elif status == "running":
    # Check staleness: if file hasn't been modified in 60 seconds, likely stale
    seconds_since_modified = time.time() - stat.st_mtime
    is_running = seconds_since_modified < 60
else:
    # Fallback: Check if file was modified recently (< 5 seconds ago)
    is_running = (time.time() - stat.st_mtime) < 5
```

**Key improvements:**
1. **Respects metadata status**: If experiment metadata says "completed", immediately return `running=false`
2. **Staleness detection**: For "running" experiments, check if file was modified in last 60 seconds (matches `/api/experiments` behavior)
3. **Fallback logic**: For experiments without status metadata, use file modification time (backward compatibility)

### Frontend Changes (`viewer/frontend/js/views.js`)

Fixed experiment list caching issue (separate bug):
- **Before:** Experiment list was cached in memory, new experiments wouldn't appear
- **After:** Always fetch fresh data when viewing experiment list

## Testing

After the fix:

```bash
# Experiment list endpoint
curl http://localhost:5003/api/experiments
# Shows: "status": "completed" ✓

# Status endpoint
curl http://localhost:5003/api/experiment/capabilitytests_20251211_095556/status
# Shows: "running": false ✓
```

## Status Flow

### For Running Experiments
1. Runner writes result file with `"status": "running"` in metadata
2. `/api/experiments` reads status → shows "● LIVE" badge
3. `/api/experiment/{id}/status` checks metadata → returns `running=true`
4. Live updater shows pulsing indicator and polls every 2 seconds
5. Runner updates file with new tests → status endpoint detects changes
6. View auto-refreshes with new test results

### For Completed Experiments
1. Runner writes final result with `"status": "completed"` in metadata
2. `/api/experiments` reads status → removes live badge
3. `/api/experiment/{id}/status` checks metadata → returns `running=false`
4. Live updater hides indicator and stops polling
5. View shows final results

### For Stale Experiments (Crashed/Killed)
1. Experiment still has `"status": "running"` in metadata (never finished)
2. `/api/experiments` checks staleness → changes status to "stale" if file not modified in 60s
3. `/api/experiment/{id}/status` applies same logic → returns `running=false` after 60s
4. View shows "● STALE" badge instead of "● LIVE"

## Files Modified

1. **`util/prompt-optimization/viewer/backend/main.py`**
   - Updated `/api/experiment/{id}/status` endpoint (lines 486-520)
   - Added metadata status checking with staleness detection

2. **`util/prompt-optimization/viewer/frontend/js/views.js`**
   - Removed experiment list caching (lines 171-193)
   - Always fetch fresh data to show new experiments

## Impact

- ✅ Live indicator now correctly appears/disappears based on actual experiment status
- ✅ Completed experiments show as "completed" everywhere (no confusion)
- ✅ Stale experiments (crashed processes) detected after 60 seconds
- ✅ New experiments appear immediately in list view (no page reload needed)
- ✅ Live updater works reliably for long-running experiments

## Related Components

- **Backend:** `util/prompt-optimization/viewer/backend/main.py`
- **Frontend:** `util/prompt-optimization/viewer/frontend/js/live-updater.js`
- **Runner:** `util/prompt-optimization/runner.py` (writes status metadata)
- **Models:** `util/prompt-optimization/viewer/backend/models.py` (ExperimentStatus)

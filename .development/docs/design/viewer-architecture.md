# Agent006 Evaluation Viewer - Architecture & Design

## System Overview

```
┌─────────────┐
│   Runners   │  (evaluation, prompt-opt, e2e-opt)
└──────┬──────┘
       │ writes .006eval.{json,jsonl}
       ▼
┌─────────────┐
│ File System │  (results/*)
└──────┬──────┘
       │ reads files
       ▼
┌─────────────┐
│   Backend   │  (FastAPI + Pydantic models)
└──────┬──────┘
       │ HTTP API
       ▼
┌─────────────┐
│  Frontend   │  (Vanilla JS SPA)
└─────────────┘
```

## Design Principles

1. **Single Responsibility**: Each layer has one job
2. **Fail Fast**: Detect and log errors at the earliest layer
3. **Observable**: Every layer must be debuggable via backend endpoints
4. **Format Agnostic**: Normalize data early, endpoints work uniformly

## Layer 1: Runners → Files

### Responsibilities
- Execute agent tests
- Write results incrementally (.jsonl during execution)
- Finalize results (.json when complete)
- Write metadata (timestamp, status, models, etc.)

### File Format

**Standard Format** (`.006eval.jsonl` - see `docs/evaluation-file-format.md`)

JSONL format with:
- Line 1: Metadata + empty results array
- Lines 2+: Individual test results

```json
// Line 1
{"metadata": {"timestamp": "...", "status": "running|completed", "config": {...}}, "results": []}

// Lines 2+
{"test_id": "test_001", "model": "gpt-4o-mini", "variant": "v1", "scores": {...}, ...}
{"test_id": "test_002", "model": "claude-4", "variant": "v1", "scores": {...}, ...}
```

**Key Features**:
- Supports multiple models per experiment
- Supports multiple variants (prompt versions)
- Supports multiple judges (scorers) per test via `scores` dict
- Flat structure - easy to parse and aggregate

### Error Handling
- Runners log to console (captured by user)
- Write status="error" to metadata on failure
- Backend should never crash on malformed files

## Layer 2: Files → Backend (Data Loading)

### Responsibilities
- Discover experiment files recursively (`.006eval.jsonl` and `.006eval.json`)
- Parse standard format (JSONL with incremental writes, or JSON when finalized)
- Handle corrupted/incomplete files gracefully
- Log all parsing errors

### Data Model

```python
class ExperimentData:
    """Parsed experiment data."""
    metadata: dict          # From line 1
    tests: list[dict]       # Flat list of test results

    @property
    def models(self) -> list[str]:
        """Unique models in this experiment."""
        return list(set(t["model"] for t in self.tests))

    @property
    def variants(self) -> list[str]:
        """Unique variants in this experiment."""
        return list(set(t.get("variant", "default") for t in self.tests))

def load_experiment(path: Path) -> ExperimentData:
    """Parse standard format experiment file."""
    with open(path) as f:
        content = f.read().strip()

    # Try single JSON (finalized)
    try:
        data = json.loads(content)
        return ExperimentData(
            metadata=data["metadata"],
            tests=data["results"]
        )
    except json.JSONDecodeError:
        # Fall back to JSONL (incremental)
        lines = [json.loads(l) for l in content.split('\n') if l.strip()]
        metadata = lines[0]
        tests = lines[1:] if len(lines) > 1 else []
        return ExperimentData(
            metadata=metadata.get("metadata", {}),
            tests=tests
        )
```

### Debugging Endpoints
- `/api/debug/files` - List all discovered files with parse status
- `/api/debug/logs?level=ERROR` - View parsing errors
- `/api/debug/experiment/{id}/raw` - View raw file contents

## Layer 3: Backend API Endpoints

### Responsibilities
- Serve normalized experiment data
- Calculate aggregated statistics
- Provide health/status information
- Log all errors with full stack traces

### Endpoint Categories

**1. Data Endpoints** (work on normalized data)
- `/api/experiments` - List all experiments
- `/api/experiment/{id}` - Get full experiment
- `/api/experiment/{id}/tests` - Get test list
- `/api/experiment/{id}/summary` - Get aggregated stats

**2. Debug Endpoints** (for LLM debugging)
- `/api/health` - System health check
- `/api/debug/logs` - Recent error logs
- `/api/debug/files` - File discovery status
- `/api/debug/experiment/{id}/raw` - Raw file content
- `/api/debug/experiment/{id}/health` - Single experiment health

**3. Trace Endpoints**
- `/api/experiment/{id}/trace/{test_id}` - Get trace for test

### Error Handling Strategy

```python
@app.get("/api/endpoint")
async def endpoint():
    try:
        # Business logic here
        data = load_experiment(path)
        return process(data)
    except HTTPException:
        raise  # Re-raise HTTP errors
    except Exception as e:
        # Log to debug buffer
        log_debug("ERROR", f"Endpoint failed: {e}",
                 endpoint="/api/endpoint",
                 traceback=traceback.format_exc())
        # Return 500 with details
        raise HTTPException(500, detail=str(e))
```

### Testing Strategy (LLM-Friendly)

1. **Test file discovery**: `curl /api/debug/files`
2. **Test file parsing**: `curl /api/debug/experiment/{id}/raw`
3. **Test normalization**: `curl /api/experiment/{id}`
4. **Test summary**: `curl /api/experiment/{id}/summary`
5. **Check for errors**: `curl /api/debug/logs?level=ERROR`

**Key Insight**: If steps 1-4 work via curl, frontend MUST work.

## Shared Utilities (util/viewer_utils)

### Purpose
Common code shared between viewers (prompt-optimization viewer, trace viewer, etc.)

### Contents
- `paths.py` - Project root detection, results directory resolution
- `static_files.py` - No-cache static file serving for development
- `cors.py` - CORS middleware setup
- `frontend/` - Shared frontend components (CSS, JS utilities)

### Usage Pattern

**Backend**:
```python
from viewer_utils import (
    get_project_root,
    get_results_dir,
    get_shared_frontend_dir,
    StaticFilesNoCacheJS,
    setup_cors_middleware
)

# Mount shared frontend assets
SHARED_DIR = get_shared_frontend_dir()
app.mount("/static/shared", StaticFilesNoCacheJS(directory=str(SHARED_DIR)))
```

**Frontend**:
```html
<link rel="stylesheet" href="/static/shared/css/common.css">
<script src="/static/shared/js/utils.js"></script>
```

### Trace Viewer Embedding

The trace viewer will be embedded into experiment views:

```
┌──────────────────────────────────┐
│  Experiment Summary View         │
│  ┌────────────────────────────┐ │
│  │ Test List                   │ │
│  │ - test_001 [passed]         │ │
│  │ - test_002 [failed] ◀────   │ │
│  │ - test_003 [passed]        │ │
│  └────────────────────────────┘ │
│                                  │
│  ┌────────────────────────────┐ │
│  │ Embedded Trace View         │ │
│  │ (loaded from viewer_utils)  │ │
│  │                             │ │
│  │ LLM calls, tool uses, etc.  │ │
│  └────────────────────────────┘ │
└──────────────────────────────────┘
```

**Implementation**:
- Trace viewer JS/CSS live in `util/viewer_utils/src/viewer_utils/frontend/trace/`
- Mounted at `/static/shared/trace/`
- Experiment viewer includes trace viewer component
- Single backend endpoint: `/api/experiment/{id}/trace/{test_id}`

## Layer 4: Frontend

### Responsibilities
- Poll backend for updates
- Display experiment list
- Display experiment details
- Navigate between views
- Show loading/error states
- **Embed trace viewer** for test inspection

### Error Handling
- Display HTTP error details from backend
- Show "Failed to load" with retry button
- Log frontend errors to console

### Design Principle
**Frontend should be a thin view layer**. All logic in backend.

### Component Reuse
- Common CSS/JS from `viewer_utils`
- Trace viewer embedded as component
- Consistent look & feel across viewers

## Implementation Plan

### Phase 1: Migrate to Standard Format (Current Priority)
1. ✅ Define standard format spec (`docs/evaluation-file-format.md`)
2. Update evaluation/runner.py to output standard format
3. Update prompt-optimization runner to output standard format
4. Simplify backend to parse only standard format
5. Remove format-specific code (FlatExperimentResult, etc.)
6. Test all endpoints work via curl

### Phase 2: Comprehensive Error Logging
1. Add error logging to all endpoints
2. Test error scenarios (missing files, corrupt data, etc.)
3. Verify errors appear in `/api/debug/logs`

### Phase 3: Frontend Polish
1. Add error displays
2. Add loading states
3. Test incremental updates

## Success Criteria

1. **LLM can debug without browser**:
   - All data accessible via `/api/debug/*` endpoints
   - All errors logged and queryable
   - Raw file contents viewable

2. **Backend works independently**:
   - All endpoints testable with curl
   - Consistent responses for both formats
   - Graceful error handling

3. **Frontend is simple**:
   - Thin view layer
   - All logic in backend
   - Clear error messages from backend

## Current Status

- ✅ Dual format support exists but incomplete
- ✅ Debug logs endpoint added
- ⚠️  Normalization layer needed
- ⚠️  Some endpoints still format-aware (summary, etc.)
- ⚠️  Error handling inconsistent across endpoints

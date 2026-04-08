# Tracing with Annotations - Design Document

## Motivation

agent006 currently has OpenTelemetry-based tracing that writes spans to local JSONL files. While this works well for individual development, three key capabilities are missing:

1. **Remote/Centralized Storage**: Teams need to share traces across developers and environments. Currently, traces are only stored locally.

2. **Annotations**: There's no way to attach user feedback or evaluations to traces. When reviewing agent behavior, users can't mark outputs as correct/incorrect, add comments, or score quality.

3. **Reproducibility Metadata**: Traces don't capture the code version (git commit) or environment that produced them, making it hard to reproduce issues or understand what changed.

These gaps limit agent006's usefulness for:
- Team collaboration on agent development
- Building evaluation datasets from production traces
- Debugging issues reported by users
- Iterative improvement based on human feedback
- Reproducing behavior from specific code versions

## Goals

After implementing this design:

1. **Zero-config local development** remains unchanged - `enable_tracing()` just works
2. **Optional Langfuse integration** for teams needing centralized traces and annotations
3. **Unified annotation API** works identically regardless of storage backend
4. **Keyboard-first annotation UX** - annotate spans without leaving the navigation flow
5. **Automatic reproducibility metadata** - every trace captures git commit, dirty state, and environment info

## Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────┐
│                        agent006 Runtime                          │
│                                                                  │
│   enable_tracing()          enable_tracing_langfuse()           │
│         │                            │                           │
│         ▼                            ▼                           │
│   Local JSONL Files           Langfuse Server                   │
│   + Sidecar Annotations       (self-hosted or cloud)            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Trace Viewer Backend                         │
│                                                                  │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │              TraceProvider Interface                     │   │
│   │                                                          │   │
│   │   list_traces()    get_trace()    get_annotations()     │   │
│   │   create_annotation()    delete_annotation()             │   │
│   └─────────────────────────────────────────────────────────┘   │
│              │                              │                    │
│              ▼                              ▼                    │
│       LocalProvider                  LangfuseProvider           │
│    (JSONL + sidecars)              (Langfuse Python SDK)        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Trace Viewer Frontend                         │
│                                                                  │
│   • Keyboard navigation (↑/↓ between spans)                     │
│   • Press "A" to annotate current span                          │
│   • Inline annotation indicators on annotated spans             │
│   • Annotation form appears as overlay/modal                    │
└─────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

**1. Provider Abstraction in Trace Viewer Backend**

The trace-viewer FastAPI backend gains a `TraceProvider` interface. The frontend always talks to our API, never directly to Langfuse. This:
- Keeps the frontend simple (no Langfuse SDK in JS)
- Allows switching backends via config, not code changes
- Provides a single API for both traces and annotations

**2. Langfuse as the Remote Backend**

We chose [Langfuse](https://github.com/langfuse/langfuse) (MIT licensed, 19k+ GitHub stars) over alternatives because:
- Native OpenTelemetry support
- Built-in annotation/scoring API
- Simple self-hosting (`docker compose up`)
- Active community, YC-backed
- MIT license (vs Phoenix's Elastic License v2)

**3. Sidecar Files for Local Annotations**

Local annotations are stored alongside traces:
```
traces/
  20251215_140000.006trace.jsonl           # Trace spans
  20251215_140000.006trace.annotations.jsonl  # Annotations for this trace
```

This keeps the local flow simple - no database required.

**4. Automatic Trace Metadata**

Every trace automatically captures environment metadata for reproducibility:

| Attribute | Description | Example |
|-----------|-------------|---------|
| `git.commit` | Short commit hash | `a1b2c3d` |
| `git.commit_full` | Full commit hash | `a1b2c3d4e5f6...` |
| `git.dirty` | Uncommitted changes exist | `true` / `false` |
| `git.branch` | Current branch name | `main`, `feature/foo` |
| `agent006.version` | Framework version | `0.1.0` |
| `python.version` | Python version | `3.12.0` |
| `hostname` | Machine hostname | `dev-laptop` |

**Implementation:**

```python
def _get_git_metadata() -> dict[str, str]:
    """Capture git state at trace initialization."""
    import subprocess

    try:
        # Get commit hash
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5
        ).decode().strip()

        # Check for uncommitted changes
        dirty = subprocess.call(
            ["git", "diff", "--quiet"],
            stderr=subprocess.DEVNULL,
            timeout=5
        ) != 0

        # Get branch name
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5
        ).decode().strip()

        return {
            "git.commit": commit[:7],
            "git.commit_full": commit,
            "git.dirty": str(dirty).lower(),
            "git.branch": branch,
        }
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {}  # Not in a git repo or git command timed out
```

This metadata is attached as **OTel Resource Attributes**, meaning it appears on every span in the trace. In the trace viewer:

```
┌─────────────────────────────────────────────────────────────────┐
│  Trace: 20251215_140000                                         │
│  Commit: a1b2c3d (main, dirty)                                  │
│  ──────────────────────────────────────────────────────────────│
```

## Data Model

### Annotation Schema

The schema uses agent006 terminology while maintaining compatibility with Langfuse's score API.

```python
class Annotation(BaseModel):
    """User annotation on a trace span.

    Compatible with Langfuse scores API:
    - span_id maps to Langfuse observationId
    - score/label maps to Langfuse value/stringValue
    - source maps to Langfuse source field
    """

    # Identity
    id: str = Field(default_factory=lambda: str(uuid4()))

    # Link to span
    trace_id: str                              # Required: which trace
    span_id: str | None = None                 # Optional: specific span (None = whole trace)

    # Annotation content
    name: str                                  # Category name, e.g., "quality", "correctness"
    score: float | None = None                 # Numeric value (e.g., 0.0-1.0, 1-5)
    label: str | None = None                   # Categorical value (e.g., "good", "bad", "neutral")
    comment: str | None = None                 # Free-form text feedback

    # Metadata
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    author_id: str | None = None               # Who created it (user ID or name)
    source: Literal["human", "llm", "code"] = "human"  # How it was created
```

**Field Mapping to Langfuse:**

| agent006 Field | Langfuse Field | Notes |
|----------------|----------------|-------|
| `trace_id` | `traceId` | Direct mapping |
| `span_id` | `observationId` | Langfuse term for sub-trace spans |
| `name` | `name` | Score config name |
| `score` | `value` | Numeric scores |
| `label` | `stringValue` | Categorical scores |
| `comment` | `comment` | Direct mapping |
| `author_id` | `authorUserId` | Direct mapping |
| `source` | `source` | "human" → "ANNOTATION", "code" → "API", "llm" → "EVAL" |

**Annotation Types:**

```python
# Quick feedback (thumbs up/down)
Annotation(trace_id="...", span_id="...", name="feedback", label="positive")

# Quality score
Annotation(trace_id="...", span_id="...", name="quality", score=0.8)

# Detailed comment
Annotation(trace_id="...", span_id="...", name="review", label="incorrect",
           comment="The agent hallucinated the API response")

# Correctness flag
Annotation(trace_id="...", span_id="...", name="correctness", label="correct")
```

## API Endpoints

New endpoints for the trace-viewer backend:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/traces/{trace_id}/annotations` | List all annotations for a trace |
| GET | `/api/spans/{span_id}/annotations` | List annotations for a specific span |
| POST | `/api/annotations` | Create annotation |
| PATCH | `/api/annotations/{annotation_id}` | Update annotation |
| DELETE | `/api/annotations/{annotation_id}` | Delete annotation |

**Create Annotation Request:**
```json
{
  "trace_id": "abc123",
  "span_id": "def456",
  "name": "quality",
  "score": 0.8,
  "comment": "Good response but slightly verbose"
}
```

## Frontend: Inline Annotation UX

The trace viewer is optimized for keyboard navigation. Annotations integrate into this flow without requiring a separate view.

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate between spans |
| `A` | Open annotation form for current span |
| `Escape` | Close annotation form |
| `Enter` | Submit annotation (when form is open) |
| `1-5` | Quick score (when form is open) |
| `+` / `-` | Quick thumbs up/down |

### Annotation Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│  Span: plan.process_request                           [●] 0.8   │
│  ────────────────────────────────────────────────────────────── │
│  Duration: 1.2s                                                 │
│  Agent: MyAgent                                                 │
│  ...                                                            │
└─────────────────────────────────────────────────────────────────┘
     │
     │ User presses "A"
     ▼
┌─────────────────────────────────────────────────────────────────┐
│  Span: plan.process_request                           [●] 0.8   │
│  ────────────────────────────────────────────────────────────── │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  📝 Annotate Span                              [Esc] ✕  │   │
│  │                                                          │   │
│  │  Quick:  👍 (+)  👎 (-)                                 │   │
│  │                                                          │   │
│  │  Score:  ○ 1  ○ 2  ○ 3  ● 4  ○ 5                       │   │
│  │                                                          │   │
│  │  Label:  [Dropdown: correct/incorrect/partial]          │   │
│  │                                                          │   │
│  │  Comment: [                                    ]         │   │
│  │                                                          │   │
│  │                              [Cancel] [Save (Enter)]    │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ...                                                            │
└─────────────────────────────────────────────────────────────────┘
```

### Annotation Indicators

Annotated spans show visual indicators:

```
┌─────────────────────────────────────────────────────────────────┐
│  [●] plan.process_request                    ⭐ 4/5  💬 1       │
│      └── Span has score (4/5) and 1 comment                     │
│                                                                  │
│  [ ] generation                                                  │
│      └── No annotations                                          │
│                                                                  │
│  [●] code_execution                          👎                  │
│      └── Marked as negative feedback                             │
└─────────────────────────────────────────────────────────────────┘
```

**Indicator Legend:**
- `⭐ N/5` - Score annotation
- `👍` / `👎` - Quick feedback
- `💬 N` - Has N comments
- `●` in bracket - Has any annotation

## Usage

### Local Development (Default)

```python
from openinference_instrumentation_agent006 import enable_tracing

# Zero config - traces go to ./traces/
enable_tracing()
```

Annotations are created in the trace-viewer UI and stored in sidecar files.

### Team/Production (Langfuse)

```bash
# Option 1: Self-hosted Langfuse
docker compose up -d  # From langfuse repo

# Option 2: Langfuse Cloud
# Sign up at langfuse.com

# Configure
export LANGFUSE_HOST=http://localhost:3000  # or https://cloud.langfuse.com
export LANGFUSE_PUBLIC_KEY=pk-...
export LANGFUSE_SECRET_KEY=sk-...
```

```python
from openinference_instrumentation_agent006 import enable_tracing_langfuse

enable_tracing_langfuse(project_name="my-agent")
```

Configure trace-viewer to read from Langfuse:
```json
{
  "provider": "langfuse",
  "langfuse": {
    "host": "http://localhost:3000"
  }
}
```

## Implementation Plan

| Phase | Task | Files |
|-------|------|-------|
| 1 | Add git/env metadata capture to tracing | `packages/openinference-instrumentation-agent006/` |
| 2 | Add Annotation model | `util/trace-viewer/backend/models.py` |
| 3 | Create TraceProvider interface + LocalProvider | `util/trace-viewer/backend/providers.py` |
| 4 | Add annotation API endpoints | `util/trace-viewer/backend/main.py` |
| 5 | Implement LangfuseProvider | `util/trace-viewer/backend/providers.py` |
| 6 | Add `enable_tracing_langfuse()` | `packages/openinference-instrumentation-agent006/` |
| 7 | Frontend: display trace metadata (commit, dirty) | `util/trace-viewer/frontend/js/` |
| 8 | Frontend: keyboard shortcut + annotation form | `util/trace-viewer/frontend/js/` |
| 9 | Frontend: annotation indicators on spans | `util/trace-viewer/frontend/js/`, `css/` |
| 10 | Example + documentation | `examples/advanced/tracing_otlp.py` |

## Target Annotations: Annotating Specific Parts of Spans

### Motivation

When reviewing traces, users often want to provide feedback on specific parts of a span rather than the span as a whole:

- **LLM completions**: Annotate prompt quality separately from output quality
- **Code execution**: Comment on the code vs the result
- **Tool calls**: Rate the input formatting vs the response handling
- **Multi-step operations**: Provide feedback on individual steps

**Example:** An LLM span with a good prompt but hallucinated output should allow:
- Positive feedback on the prompt: "Clear and specific"
- Negative feedback on the output: "Contains factual errors"

Without target support, the user must choose between:
1. Annotating the whole span (loses granularity)
2. Not annotating at all (loses feedback)

### Design Goals

1. **Backward compatible**: Existing annotations without targets continue to work
2. **Langfuse compatible**: Design works with Langfuse's annotation model
3. **Intuitive UX**: Users can easily select and annotate specific parts
4. **Flexible**: Works across different span types (LLM, tool, code, etc.)

### Data Model: Adding `target` Field

Extend the Annotation model with an optional `target` field:

```python
class Annotation(BaseModel):
    """User annotation on a trace span.

    Compatible with Langfuse scores API:
    - span_id maps to Langfuse observationId
    - score/label maps to Langfuse value/stringValue
    - source maps to Langfuse source field
    - target is stored in comment prefix or metadata for Langfuse
    """

    # Identity
    id: str = Field(default_factory=lambda: str(uuid4()))

    # Link to span
    trace_id: str                              # Required: which trace
    span_id: str | None = None                 # Optional: specific span (None = whole trace)

    # NEW: Annotation target (part of span)
    target: str | None = None                  # Optional: which part (e.g., "prompt", "output", "code", "result")

    # Annotation content
    name: str                                  # Category name, e.g., "quality", "correctness"
    score: float | None = None                 # Numeric value (e.g., 0.0-1.0, 1-5)
    label: str | None = None                   # Categorical value (e.g., "good", "bad", "neutral")
    comment: str | None = None                 # Free-form text feedback

    # Metadata
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    author_id: str | None = None               # Who created it (user ID or name)
    source: Literal["human", "llm", "code"] = "human"  # How it was created
```

**Target Examples:**
- `null` - Annotation applies to entire span (default, backward compatible)
- `"prompt"` - Annotation applies to LLM prompt
- `"output"` - Annotation applies to LLM output
- `"code"` - Annotation applies to code in code execution span
- `"result"` - Annotation applies to execution result
- `"input"` - Annotation applies to tool input
- `"response"` - Annotation applies to tool response

**Target Semantics:**
- Target strings are **semantic labels**, not DOM selectors
- Plugins define which targets they support
- Targets are optional - most spans won't use them

### Langfuse Compatibility Strategy

**Challenge:** Langfuse's score/annotation model does not have a native `target` field. Scores are attached to entire observations (spans), not parts of observations.

**Solution Options:**

#### Option 1: Store in Comment (Simplest)
When syncing to Langfuse, prefix the comment with `[target]`:

```python
# agent006 storage
Annotation(span_id="abc123", target="prompt", comment="Clear and specific")

# When syncing to Langfuse
langfuse.create_score(
    observation_id="abc123",
    comment="[prompt] Clear and specific"  # Target in comment prefix
)
```

**Pros:** Simple, no data loss
**Cons:** Parsing required to extract target

#### Option 2: Store in Metadata (Clean)
Use Langfuse's `metadata` field (available as of 2025):

```python
langfuse.create_score(
    observation_id="abc123",
    comment="Clear and specific",
    metadata={"agent006_target": "prompt"}  # Target in metadata
)
```

**Pros:** Clean separation, structured data
**Cons:** Requires metadata support

#### Option 3: Hierarchical Observations (Future)
Create sub-observations for each target:

```
span: llm_completion
  ├── observation: prompt (with score)
  └── observation: output (with score)
```

**Pros:** Native Langfuse model, best compatibility
**Cons:** Requires changing how we structure traces, complex migration

**Recommendation:** Use **Option 2 (metadata)** for LangfuseProvider. This is clean, structured, and aligns with Langfuse's extensibility model. For LocalProvider, store target directly in the annotation JSONL.

### Frontend Implementation

#### 1. Marking Annotatable Targets in Plugins

Plugins mark which parts of their rendered content can be annotated independently:

```javascript
// In llm_completion plugin renderExpanded()
function renderExpanded(span, container) {
    // Render prompt section
    const promptDiv = document.createElement('div');
    promptDiv.className = 'kb-block';  // Keyboard navigable
    promptDiv.dataset.annotationTarget = 'prompt';  // Mark as annotatable target
    promptDiv.innerHTML = `
        <div class="section-header">Prompt</div>
        <pre>${escapeHtml(span.attributes.prompt)}</pre>
    `;
    container.appendChild(promptDiv);

    // Render output section
    const outputDiv = document.createElement('div');
    outputDiv.className = 'kb-block';  // Keyboard navigable
    outputDiv.dataset.annotationTarget = 'output';  // Mark as annotatable target
    outputDiv.innerHTML = `
        <div class="section-header">Output</div>
        <pre>${escapeHtml(span.attributes.output)}</pre>
    `;
    container.appendChild(outputDiv);
}
```

#### 2. Detecting Target on Annotation

When user presses `A` or `+/-`, detect which target is selected:

```javascript
class AnnotationManager {
    showAnnotationForm(spanElement) {
        // Walk up to find span_id
        let element = spanElement;
        while (element && !element.dataset.spanId) {
            element = element.parentElement;
        }

        const spanId = element.dataset.spanId;

        // NEW: Detect if a target is selected
        const target = this.detectTarget(spanElement, element);

        // Store for submission
        this.currentSpanId = spanId;
        this.currentSpanElement = element;
        this.currentTarget = target;  // NEW

        // Show in UI
        const targetInfo = target
            ? `<span class="target-badge">${target}</span>`
            : 'entire span';
        document.getElementById('annotation-target-info').innerHTML =
            `Annotating: ${targetInfo}`;
    }

    detectTarget(selectedElement, spanElement) {
        // Check if selected element has target
        if (selectedElement.dataset.annotationTarget) {
            return selectedElement.dataset.annotationTarget;
        }

        // Walk up to span boundary looking for target
        let current = selectedElement;
        while (current && current !== spanElement) {
            if (current.dataset.annotationTarget) {
                return current.dataset.annotationTarget;
            }
            current = current.parentElement;
        }

        return null;  // No target = annotating entire span
    }

    async submitAnnotation() {
        const annotation = {
            trace_id: this.currentFilePath,
            span_id: this.currentSpanId,
            target: this.currentTarget,  // NEW: include target
            name: 'manual',
            score: score,
            label: label,
            comment: comment,
            source: 'human'
        };

        await fetch('/api/annotations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(annotation)
        });
    }
}
```

#### 3. Displaying Target-Specific Indicators

Annotations with targets should display indicators on the target element, not just the span:

```javascript
class AnnotationManager {
    updateAllIndicators() {
        // Group annotations by (span_id, target)
        const annotationsBySpan = new Map(); // span_id -> Map(target -> [annotations])

        for (const [spanId, annotations] of this.annotations.get(this.currentTraceId) || []) {
            const byTarget = new Map();
            for (const ann of annotations) {
                const target = ann.target || null;
                if (!byTarget.has(target)) byTarget.set(target, []);
                byTarget.get(target).push(ann);
            }
            annotationsBySpan.set(spanId, byTarget);
        }

        // Apply indicators
        for (const [spanId, byTarget] of annotationsBySpan) {
            const spanElement = document.querySelector(`[data-span-id="${spanId}"]`);
            if (!spanElement) continue;

            // Apply span-level annotations (target=null)
            if (byTarget.has(null)) {
                this.updateIndicator(spanElement, byTarget.get(null));
            }

            // Apply target-specific annotations
            for (const [target, annotations] of byTarget) {
                if (target === null) continue;  // Already handled

                const targetElement = spanElement.querySelector(
                    `[data-annotation-target="${target}"]`
                );
                if (targetElement) {
                    this.updateIndicator(targetElement, annotations);
                }
            }
        }
    }
}
```

#### 4. Visual Design

When a span has target annotations, show indicators on both the span and the targets:

```
┌─────────────────────────────────────────────────────────────┐
│ ⭐ 4.2  LLM Completion                                      │  ← Span-level: average score
├─────────────────────────────────────────────────────────────┤
│ 👍  Prompt:                                                 │  ← Target: positive feedback
│     "Explain quantum computing in simple terms..."          │
├─────────────────────────────────────────────────────────────┤
│ 👎 💬 2  Output:                                            │  ← Target: negative + 2 comments
│     "Quantum computing uses quantum bits called qubits..."  │
└─────────────────────────────────────────────────────────────┘
```

### Backend Implementation

#### 1. Update Annotation Model

Add `target` field to the Annotation model (already shown above).

#### 2. LocalProvider Changes

No changes needed - just store/retrieve the `target` field:

```python
class LocalProvider:
    async def create_annotation(self, annotation: Annotation) -> Annotation:
        # Simply serialize annotation including target field
        annotation_file = self._get_annotation_file(annotation.trace_id)
        with open(annotation_file, 'a') as f:
            f.write(annotation.model_dump_json() + '\n')
        return annotation
```

#### 3. LangfuseProvider Changes

When syncing to Langfuse, store target in metadata:

```python
class LangfuseProvider:
    async def create_annotation(self, annotation: Annotation) -> Annotation:
        # Build metadata with target
        metadata = {}
        if annotation.target:
            metadata['agent006_target'] = annotation.target

        # Create score in Langfuse
        self.client.create_score(
            trace_id=annotation.trace_id,
            observation_id=annotation.span_id,
            name=annotation.name,
            value=annotation.score,
            string_value=annotation.label,
            comment=annotation.comment,
            metadata=metadata if metadata else None,
            source=self._map_source(annotation.source)
        )

        return annotation

    async def get_annotations(self, trace_id: str) -> list[Annotation]:
        # Fetch scores from Langfuse
        scores = self.client.get_scores(trace_id=trace_id)

        annotations = []
        for score in scores:
            # Extract target from metadata
            target = score.metadata.get('agent006_target') if score.metadata else None

            annotations.append(Annotation(
                id=score.id,
                trace_id=score.trace_id,
                span_id=score.observation_id,
                target=target,  # Restore from metadata
                name=score.name,
                score=score.value,
                label=score.string_value,
                comment=score.comment,
                source=self._map_source_back(score.source)
            ))

        return annotations
```

### Plugin Guidelines

Plugins should follow these conventions when adding target support:

#### 1. Target Naming

Use semantic names that describe **what** the content is:

✅ Good:
- `prompt`, `output` (LLM)
- `code`, `result` (code execution)
- `input`, `response` (tool calls)
- `query`, `results` (database)

❌ Bad:
- `section1`, `section2` (not semantic)
- `top`, `bottom` (positional)
- `first`, `second` (ordinal)

#### 2. Target Markup

Mark targets with both `data-annotation-target` and `kb-block` class:

```javascript
const targetDiv = document.createElement('div');
targetDiv.className = 'kb-block';  // Makes it keyboard-navigable
targetDiv.dataset.annotationTarget = 'prompt';  // Makes it annotatable
```

#### 3. Target Scope

- Keep targets **coarse-grained** - major sections, not individual lines
- Typical span should have 0-4 targets
- Don't mark everything - only parts users would want to annotate separately

#### 4. Example Plugins

**LLM Completion Plugin:**
- Targets: `prompt`, `output`
- Optional: `system_message` if shown separately

**Code Execution Plugin:**
- Targets: `code`, `result`
- Optional: `stderr` if shown separately

**Tool Call Plugin:**
- Targets: `input`, `response`

**Basic Span (no targets):**
- No targets needed - annotate entire span

### Migration and Backward Compatibility

#### Phase 1: Add Target Field (Backward Compatible)

1. Add `target: str | None = None` to Annotation model
2. Update LocalProvider to store/load target (automatic)
3. Update LangfuseProvider to use metadata for target
4. Update frontend to detect and submit target
5. **Result:** All existing annotations have `target=null`, continue working

#### Phase 2: Update Key Plugins

1. Add target markup to 2-3 high-value plugins:
   - LLM completion plugin (most important)
   - Code execution plugin
   - Tool call plugin
2. Test annotation workflow with targets
3. **Result:** Users can annotate LLM prompts/outputs separately

#### Phase 3: Comprehensive Plugin Support

1. Add target support to remaining plugins
2. Add visual highlighting on hover over annotatable targets
3. Add bulk annotation features (annotate all prompts in trace)

### Example Usage

```python
# User reviews an LLM trace
# Navigates to LLM completion span
# Presses 'j' to enter the span (expanded view)
# Navigates with 'j'/'k' to prompt section
# Presses '+' → Quick positive feedback on prompt
# Navigates to output section
# Presses 'a' → Opens annotation form
# Types: "Contains factual errors about quantum mechanics"
# Presses Ctrl+Enter → Saves annotation

# Result: Two annotations created
Annotation(
    span_id="abc123",
    target="prompt",
    name="feedback",
    label="positive",
    source="human"
)

Annotation(
    span_id="abc123",
    target="output",
    name="manual",
    label="negative",
    comment="Contains factual errors about quantum mechanics",
    source="human"
)
```

### Implementation Checklist

**Backend:**
- [ ] Add `target: str | None = None` to Annotation model
- [ ] Update LangfuseProvider to store/retrieve target via metadata
- [ ] Add target field to API request/response schemas

**Frontend:**
- [ ] Update `showAnnotationForm()` to detect target
- [ ] Update `submitAnnotation()` to include target
- [ ] Update `quickFeedback()` to detect target
- [ ] Update `updateAllIndicators()` to group by (span_id, target)
- [ ] Add target badge/label to annotation form UI
- [ ] Update CSS for target-specific indicators

**Plugins:**
- [ ] Update LLM completion plugin with prompt/output targets
- [ ] Update code execution plugin with code/result targets
- [ ] Update tool call plugin with input/response targets
- [ ] Document target naming conventions

**Testing:**
- [ ] Test annotation without target (backward compatibility)
- [ ] Test annotation with target (new functionality)
- [ ] Test Langfuse sync with target in metadata
- [ ] Test indicator display on targets vs spans
- [ ] Test keyboard navigation to targets

**Documentation:**
- [ ] Update annotation API docs with target field
- [ ] Add plugin developer guide for targets
- [ ] Add user guide for target annotations

## References

- [Langfuse GitHub](https://github.com/langfuse/langfuse) - MIT licensed LLM observability platform
- [Langfuse Scores API](https://langfuse.com/docs/scores/annotation) - Their annotation/scoring API
- [Langfuse API Reference](https://api.reference.langfuse.com/) - Interactive API documentation
- [Langfuse Annotation Queues](https://langfuse.com/changelog/2025-03-13-public-api-annotation-queues) - Public API for annotation workflows
- [OpenTelemetry Spans](https://opentelemetry.io/docs/concepts/signals/traces/) - OTel trace concepts

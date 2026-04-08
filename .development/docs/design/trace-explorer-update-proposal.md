# Trace Explorer Update Proposal

**Date:** Mon Jan 27 2026
**Based on:** [trace-explorer-test-drive-feedback.md](trace-explorer-test-drive-feedback.md)

## Summary

The feedback identified several improvements needed for effective regression RCA. This proposal outlines implementation for each, prioritized by impact.

---

## High Priority

### 1. Trace Diff/Comparison Feature

**Request:** Compare two traces side-by-side to identify divergence points.

**API Design:**

```python
# Python API
diff_result = TraceExplorer.diff(trace1, trace2)
# or
diff_result = trace1.compare(trace2)

# Returns structured diff:
# - Session count differences
# - Call graph differences
# - First point of divergence
# - Prompt differences at matched turns
```

**CLI Design:**

```bash
trace-explorer trace1.jsonl --diff trace2.jsonl
trace-explorer trace1.jsonl --diff trace2.jsonl --session abc123  # Compare specific session
```

**Implementation Plan:**

```python
@dataclass
class TraceDiff:
    """Result of comparing two traces."""
    trace1_path: str
    trace2_path: str
    session_count_diff: tuple[int, int]  # (trace1_count, trace2_count)
    call_graph_diff: str  # Side-by-side ASCII comparison
    first_divergence: DivergencePoint | None
    prompt_diffs: list[PromptDiff]  # Matched turns with different prompts

@dataclass
class DivergencePoint:
    """First point where traces differ."""
    session_id: tuple[str, str]  # (trace1_session, trace2_session)
    turn_index: int
    reason: str  # "different_output", "different_tool_calls", "missing_turn"
    context: str  # Formatted diff at this point

@dataclass
class PromptDiff:
    """A prompt that differs between traces."""
    session_id: tuple[str, str]
    turn_index: int
    diff_type: str  # "expression_path", "content", "missing_section"
    trace1_value: str
    trace2_value: str

class TraceExplorer:
    @classmethod
    def diff(cls, trace1: "TraceExplorer", trace2: "TraceExplorer") -> str:
        """Compare two traces and show differences.

        Compares:
        - Session count and structure
        - Call graph (agents called, order)
        - Turn-by-turn prompt content
        - LLM output divergence

        Returns formatted diff report.
        """
        ...

    def compare(self, other: "TraceExplorer") -> str:
        """Convenience method - calls TraceExplorer.diff(self, other)."""
        return TraceExplorer.diff(self, other)
```

**Output Format:**

```
# Trace Comparison

## Summary
| Metric | Trace 1 | Trace 2 |
|--------|---------|---------|
| File | router_mr.jsonl | router_main.jsonl |
| Sessions | 1 | 2 |
| Total Turns | 5 | 4 |
| Status | FAILED | PASSED |

## Call Graph Comparison

Trace 1:                          Trace 2:
├─ RouterTestWrapper.process      ├─ RouterTestWrapper.process
│  └─ (5t, 1200ms) [ERR]         │  ├─ (3t, 800ms) [OK]
                                  │  └─ Validator.validate
                                  │     └─ (1t, 400ms) [OK]

## First Divergence

Session: abc123 (trace1) vs def456 (trace2)
Turn: 2

Trace 1 prompt contains:
  <task expr="self.history[0].prompt">

Trace 2 prompt contains:
  <task expr="self.history.events[0].content">

## Next Steps
- Use `get_turn('abc123', 2)` on trace1 to see full context
- Use `get_turn('def456', 2)` on trace2 to compare
```

---

### 2. Document Eval Context Loading

**Request:** Clarify how to load traces that have evaluation results.

**Solution:** Add documentation section and example.

The `get_eval_context()` method already exists but traces need to include eval result spans. Document the expected span format:

```python
# Eval result is extracted from spans with:
# - name: "evaluation" or "benchmark.evaluation"
# - attributes.eval.result (JSON with pass/fail, score, expected, actual)
# - attributes.eval.benchmark (benchmark name)
# - attributes.eval.task_id (task identifier)

# Example: Creating a trace with eval results
from opentelemetry import trace as otel_trace

tracer = otel_trace.get_tracer("evaluation")
with tracer.start_span("evaluation") as span:
    span.set_attribute("eval.result", json.dumps({
        "passed": False,
        "score": 0.0,
        "expected": "validator_called",
        "actual": "empty_result",
        "scorer_reasoning": "Agent did not call Validator agent"
    }))
    span.set_attribute("eval.benchmark", "capability_eval")
    span.set_attribute("eval.task_id", "router_validate_002")
```

**Add to help text:**

```python
### 5. get_eval_context()
See evaluation inputs, expected outputs, and scorer results.
- Requires trace to have an 'evaluation' span with eval.result attribute
- Use this to understand why a trace failed evaluation

**Note:** If this returns "No evaluation result", the trace file doesn't
include eval metadata. Ensure your evaluation runner adds spans with:
- `eval.result` (JSON: {passed, score, expected, actual, scorer_reasoning})
- `eval.benchmark` (benchmark name)
- `eval.task_id` (task identifier)
```

---

## Medium Priority

### 3. Raw Span Access

**Request:** Access raw span data for debugging trace structure.

**API Design:**

```python
class TraceExplorer:
    def get_raw_span(self, span_id: str) -> str:
        """Get raw span data as formatted JSON.

        Args:
            span_id: Full span_id or 6-char prefix

        Returns:
            JSON-formatted span with all attributes
        """
        ...

    def get_raw_spans(self, session_id: str) -> str:
        """Get all raw spans for a session.

        Returns spans in chronological order with their relationships.
        """
        ...
```

**CLI:**

```bash
trace-explorer trace.jsonl --raw abc123          # Raw span by ID
trace-explorer trace.jsonl --session abc --raw   # All raw spans for session
```

**Implementation:**

```python
def get_raw_span(self, span_id: str) -> str:
    """Get raw span data as formatted JSON."""
    # Find span by full or partial ID
    matched_span = None
    for sid, span in self._span_index.items():
        if sid == span_id or sid.startswith(span_id):
            matched_span = span
            break

    if not matched_span:
        return f"No span found matching '{span_id}'"

    # Format as indented JSON
    return json.dumps(matched_span, indent=2, default=str)
```

---

### 4. Batch Processing / Directory Loading

**Request:** Analyze multiple traces for aggregate statistics.

**API Design:**

```python
class TraceExplorer:
    @classmethod
    def from_directory(
        cls,
        path: str | Path,
        pattern: str = "*.jsonl"
    ) -> "TraceCollection":
        """Load all traces from a directory.

        Returns a TraceCollection for batch analysis.
        """
        ...

@dataclass
class TraceCollection:
    """Collection of traces for batch analysis."""
    traces: list[TraceExplorer]

    def filter_by_status(self, status: str) -> "TraceCollection":
        """Filter to traces with given status (OK/ERROR)."""
        ...

    def filter_by_error_type(self, error_type: str) -> "TraceCollection":
        """Filter to traces containing specific error type."""
        ...

    def get_summary(self) -> str:
        """Get aggregate statistics across all traces."""
        ...

    def get_error_distribution(self) -> dict[str, int]:
        """Count occurrences of each error type."""
        ...

    def export_regression_list(self, output_path: str) -> None:
        """Export list of failed traces to file."""
        ...
```

**CLI:**

```bash
trace-explorer --dir /path/to/traces/              # Summary of all traces
trace-explorer --dir /path/to/traces/ --errors     # All errors across traces
trace-explorer --dir /path/to/traces/ --filter-status ERROR  # Only failures
```

---

## Low Priority

### 5. Quiet Flag for Warning Suppression

**Request:** Reduce noise from parser warnings during RCA.

**Implementation:**

```python
# Add module-level flag
_quiet_mode = False

def set_quiet_mode(quiet: bool) -> None:
    """Enable/disable warning suppression."""
    global _quiet_mode
    _quiet_mode = quiet

# Update warning sites
def _load_spans(trace_path: str | Path) -> list[dict[str, Any]]:
    ...
    if not _quiet_mode and parse_errors <= 3:
        print(f"Warning: Parse error at line {line_num}: {e}", file=sys.stderr)
    ...

# Also use warnings.filterwarnings for UserWarning suppression
```

**CLI:**

```bash
trace-explorer trace.jsonl --quiet    # Suppress all warnings
trace-explorer trace.jsonl -q         # Short form
```

---

### 6. Timeline Navigation (Expose Existing Method)

**Current state:** `_get_timeline()` exists but is private.

**Proposal:** Make it public and add CLI support.

```python
def get_timeline(self, max_events: int = 50) -> str:
    """Get chronological timeline of events.

    Shows spans in order with timestamps and durations.
    Useful for understanding execution flow in long traces.
    """
    return self._get_timeline(max_events)

def find_first_error(self) -> str:
    """Navigate to the first error in the trace.

    Returns formatted output for the turn where the first error occurred,
    along with navigation hints.
    """
    result = self._find_first_error()
    if not result:
        return "No errors found in trace."
    session, turn_idx, turn = result
    return f"First error at session {session.session_id}, turn {turn_idx}\n\n" + \
           self.get_turn(session.session_id, turn_idx)
```

**CLI:**

```bash
trace-explorer trace.jsonl --timeline              # Show timeline
trace-explorer trace.jsonl --first-error           # Jump to first error
```

---

## Implementation Order

1. **Phase 1 (High Impact):**
   - [ ] `--quiet` flag (quick win, reduces noise)
   - [ ] `get_raw_span()` method (debugging aid)
   - [ ] Document eval context format (update docstrings)

2. **Phase 2 (Core Feature):**
   - [ ] `diff()` / `compare()` methods
   - [ ] `--diff` CLI flag
   - [ ] Divergence detection logic

3. **Phase 3 (Power Features):**
   - [ ] `from_directory()` / `TraceCollection`
   - [ ] Expose `get_timeline()` and `find_first_error()`

---

## Testing Plan

Each feature needs:
1. Unit tests with fixture traces
2. Integration test with real traces from `util/trace-viewer/traces/`
3. CLI test verifying correct output

Test files to create:
- `tests/test_diff.py` - Trace comparison tests
- `tests/test_batch.py` - Directory loading tests
- `tests/test_raw_span.py` - Raw span access tests

# Trace Analyzer: Annotation & Storage Design

## Overview

This document describes how we annotate traces and store them as an eval set for the Trace Analyzer Agent. The goal is to enable:

1. Quick annotation during debugging sessions
2. Building a curated eval set from annotated traces
3. Evaluating the Trace Analyzer against human annotations

## Analysis Pipeline

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  .006trace.jsonl │────▶│   Mechanical    │────▶│   LLM Trace     │
│  (raw trace)    │     │   Analysis      │     │   Analyzer      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                      │                        │
        │                      ▼                        ▼
        │               ┌─────────────────────────────────────┐
        │               │  .006trace.annotations.jsonl        │
        │               │  (eval + mechanical + LLM + human)  │
        │               └─────────────────────────────────────┘
        │                      ▲           ▲            │
        │                      │           │            │
        ▼                      │           │            ▼
┌─────────────────┐            │           │       ┌─────────┐
│  Trace Viewer   │────────────┘           │       │Langfuse │
│  (human annot)  │────────────────────────│──────▶│ (sync)  │
└─────────────────┘                        │       └─────────┘
                                           │
┌─────────────────┐                        │
│  .006eval.jsonl │────────────────────────┘
│  (eval result)  │
└─────────────────┘
```

**Flow:**
1. **Eval result** (if available) from `.006eval.jsonl` is written as the first annotation (`source: "eval"`)
2. **Mechanical analysis** runs as a pre-pass (deterministic, fast)
3. **LLM analyzer** runs with mechanical findings (and eval context if available)
4. **Trace viewer** allows humans to browse traces and add annotations
5. **All sources** write to annotation sidecar with different `source` values:
   - `source: "eval"` - from eval pipeline result (when available)
   - `source: "mechanical"` - from pre-pass checks
   - `source: "llm"` - from LLM analyzer
   - `source: "human"` - from trace viewer UI
6. **Trace viewer uploads** both trace and annotations to Langfuse (via "Upload" button)

**Important**: Eval context is optional. The analyzer works on any trace:
- **With eval context**: Traces from eval runs have input/expected/output, helping the analyzer understand what went wrong
- **Without eval context**: Traces from manual debugging sessions still get mechanical analysis and LLM diagnosis based on observable behavior (errors, loops, tool misuse)

## Mechanical Analysis (Pre-pass)

Deterministic checks specific to agent006 that don't require LLM judgment. Designed as a **pluggable system** so checks can be easily added/removed.

### Pluggable Architecture

```python
# e2e_optimization/mechanical_checks/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

@dataclass
class MechanicalFinding:
    """Result of a mechanical check."""
    check_id: str                    # e.g., "execution_error"
    severity: Literal["ERROR", "WARNING", "INFO"]
    session_id: str
    span_id: str                     # Always set - identifies the specific span with the issue
    message: str                     # Human-readable description
    details: dict | None = None      # Extra structured data (code snippets, etc.)

class MechanicalCheck(ABC):
    """Base class for mechanical checks. Implement to add new checks."""

    @property
    @abstractmethod
    def check_id(self) -> str:
        """Unique identifier for this check."""
        ...

    @property
    @abstractmethod
    def severity(self) -> Literal["ERROR", "WARNING", "INFO"]:
        """Default severity level."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description of what this check detects."""
        return ""

    @abstractmethod
    def run(self, trace: "TraceExplorer") -> list[MechanicalFinding]:
        """Run the check and return any findings."""
        ...
```

### Registry Pattern

```python
# e2e_optimization/mechanical_checks/__init__.py

from .base import MechanicalCheck, MechanicalFinding
from .checks import ExecutionErrorCheck, MaxIterationsCheck

# Start minimal - add checks as we find traces that need them
DEFAULT_CHECKS: list[type[MechanicalCheck]] = [
    ExecutionErrorCheck,   # Code raised an exception
    MaxIterationsCheck,    # Ran out of turns
]

def run_all_checks(
    trace: "TraceExplorer",
    checks: list[type[MechanicalCheck]] | None = None,
) -> list[MechanicalFinding]:
    """Run all registered checks on a trace."""
    checks = checks or DEFAULT_CHECKS
    findings = []
    for check_cls in checks:
        check = check_cls()
        findings.extend(check.run(trace))
    return findings
```

### File Structure

```
evaluation/
├── mechanical_checks/
│   ├── __init__.py    # Registry and run_all_checks()
│   ├── base.py        # MechanicalCheck ABC, MechanicalFinding
│   └── checks.py      # All check implementations (start simple, split later if needed)
```

### Example Check Implementation

```python
# e2e_optimization/mechanical_checks/codeact_checks.py

class NoToolCallsCheck(MechanicalCheck):
    """Detect CodeAct rounds where no tool/method was called."""

    @property
    def check_id(self) -> str:
        return "no_tool_calls"

    @property
    def severity(self) -> Literal["ERROR", "WARNING", "INFO"]:
        return "ERROR"

    @property
    def description(self) -> str:
        return "CodeAct round with no tool/method calls"

    def run(self, trace: TraceExplorer) -> list[MechanicalFinding]:
        findings = []
        for session in trace._all_sessions:
            for i, turn in enumerate(session.turns):
                if isinstance(turn, ExecutionTurn):
                    if not self._has_tool_call(turn.code):
                        findings.append(MechanicalFinding(
                            check_id=self.check_id,
                            severity=self.severity,
                            session_id=session.session_id,
                            span_id=turn.span_id,
                            message="Execution turn with no tool/method calls",
                            details={"code": turn.code[:200]},
                        ))
        return findings

    def _has_tool_call(self, code: str) -> bool:
        # Look for self.xxx() patterns
        import re
        return bool(re.search(r'self\.\w+\s*\(', code or ""))
```

### Built-in Checks

**Initial checks** (start with these):

| Check ID | Severity | Description |
|----------|----------|-------------|
| `execution_error` | ERROR | Code execution raised an exception |
| `max_iterations` | ERROR | Ran out of turns |

**Future checks** (add as we encounter traces that need them):

| Check ID | Severity | Description |
|----------|----------|-------------|
| `syntax_error` | ERROR | LLM generated invalid Python |
| `timeout` | ERROR | Execution exceeded time limit |
| `empty_response` | ERROR | LLM returned empty/null |
| `no_tool_calls` | ERROR | CodeAct round with no tool/method calls |
| `repeated_code` | WARNING | Same code pattern repeated (potential loop) |
| `subagent_failure_ignored` | WARNING | Child agent failed but parent continued |

### Output Format

Mechanical findings are converted to annotations with `source: "mechanical"`:

```json
{
  "id": "mech-001",
  "session_id": "abc123",
  "span_id": "def456",
  "source": "mechanical",
  "name": "mechanical_check",
  "label": "execution_error",
  "comment": "NameError: name 'undefined_var' is not defined",
  "metadata": {
    "check_id": "execution_error",
    "severity": "ERROR",
    "details": {"code_snippet": "result = undefined_var + 1"}
  }
}
```

### Eval Result Annotation

When an eval result exists, it's written as the first annotation with `source: "eval"`:

```json
{
  "id": "eval-001",
  "session_id": "abc123",
  "span_id": "root-span-id",
  "source": "eval",
  "name": "eval_result",
  "label": "FAIL",
  "score": 0.0,
  "comment": "Expected 10, got 1",
  "metadata": {
    "benchmark": "tau_bench",
    "task_id": "retail_easy_001",
    "input": "How many blue t-shirts are in stock?",
    "expected": "10",
    "output": "1",
    "scores": {"exact_match": 0.0}
  }
}
```

### Feeding to LLM Analyzer

The LLM analyzer receives mechanical findings as context:

```python
# In TraceExplorer or passed to analyzer
def get_mechanical_findings(self) -> str:
    """Get mechanical analysis results for this trace."""
    findings = load_mechanical_annotations(self.trace_file)
    if not findings:
        return "No mechanical issues detected."

    lines = [f"Found {len(findings)} mechanical issue(s):"]
    for f in findings:
        lines.append(f"- [{f.severity}] {f.check_id}: {f.message}")
        lines.append(f"  Span: {f.span_id}")
    return "\n".join(lines)
```

### Adding Custom Checks

To add a new check:

1. Create a new class extending `MechanicalCheck`
2. Implement `check_id`, `severity`, and `run()`
3. Add to `DEFAULT_CHECKS` or pass explicitly to `run_all_checks()`

```python
# e2e_optimization/mechanical_checks/custom/my_check.py

class MyCustomCheck(MechanicalCheck):
    @property
    def check_id(self) -> str:
        return "my_custom_check"

    @property
    def severity(self) -> Literal["ERROR", "WARNING", "INFO"]:
        return "WARNING"

    def run(self, trace: TraceExplorer) -> list[MechanicalFinding]:
        # Your custom logic here
        ...
```

### Developing New Mechanical Checks

**Workflow**: Start by finding a trace that exhibits the pattern you want to detect.

```
1. Find a trace with the issue
   └── Browse traces in trace viewer or eval results
   └── Identify a specific failure pattern (e.g., "agent keeps retrying same failed call")

2. Copy trace to test fixtures
   └── cp path/to/trace.006trace.jsonl e2e_optimization/mechanical_checks/fixtures/

3. Write the check with a test
   └── Create check class in e2e_optimization/mechanical_checks/checks.py
   └── Write test that loads fixture and verifies detection

4. Add to DEFAULT_CHECKS when ready
   └── Start disabled, enable after validation on more traces
```

**Example test structure**:

```python
# e2e_optimization/mechanical_checks/tests/test_checks.py

def test_repeated_failure_check():
    """Test that RepeatedFailureCheck detects retry loops."""
    trace = TraceExplorer.from_file("fixtures/retry_loop_trace.006trace.jsonl")
    check = RepeatedFailureCheck()
    findings = check.run(trace)

    assert len(findings) == 1
    assert findings[0].check_id == "repeated_failure"
    assert "retry" in findings[0].message.lower()
```

**Tip**: Use `/new-mechanical-check` skill to scaffold a new check from a trace file.

## Storage Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          LANGFUSE                                │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │   Traces    │───▶│   Scores    │    │   Eval Set Registry │  │
│  │  (spans)    │    │(annotations)│    │   (JSON in repo)    │  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │ Trace Analyzer  │
                    │   Eval Runner   │
                    └─────────────────┘
```

### Why Langfuse?

- **Portable**: Eval set doesn't depend on local file paths
- **Collaborative**: Multiple people can annotate
- **Existing integration**: Trace viewer already supports LangfuseProvider
- **Rich UI**: Can browse traces and annotations in Langfuse dashboard

### Local Development

For local iteration, traces can still be stored as `.006trace.jsonl` files with `.006trace.annotations.jsonl` sidecars. The eval set registry supports both:

```json
{
  "entries": [
    {"langfuse_trace_id": "abc123", "source": "tau_bench"},
    {"local_trace": "traces/debug_session.006trace.jsonl", "source": "manual"}
  ]
}
```

## Annotation Schema

Extend the existing annotation schema with failure analysis fields:

```python
class TraceAnnotation:
    # === Identity (existing) ===
    id: str                          # UUID
    session_id: str                  # Trace/session identifier
    span_id: str | None              # Specific span (None = whole trace)

    # === Failure Analysis (new) ===
    tags: list[str]                  # Freeform tags (emergent taxonomy)
    comment: str | None              # Free-form explanation of what went wrong

    # === General Feedback (existing) ===
    score: float | None              # 0.0-1.0 or 1-5
    label: str | None                # "correct", "incorrect", etc.

    # === Metadata (existing, extended) ===
    created_at: str                  # ISO8601 timestamp
    author_id: str | None            # Who created this
    source: Literal["human", "llm", "mechanical"]  # Extended with "mechanical"

    # === Additional metadata (new) ===
    metadata: dict | None            # Flexible extra data (e.g., mechanical check details)
```

**Note**: We use freeform `tags` instead of a fixed `failure_mode` enum. Categories emerge from tag analysis over time (see "Failure Taxonomy: Emergent Approach" below).

### Source Values

| Source | Description |
|--------|-------------|
| `eval` | Eval pipeline result (input, expected, output, scores) |
| `human` | Manual annotation during debugging |
| `mechanical` | Deterministic pre-pass checks |
| `llm` | LLM Trace Analyzer output |

### Failure Taxonomy: Emergent Approach

**Key insight from research**: Don't start with fixed categories. Treat the taxonomy as code that evolves.

```
Phase 1: Freeform Tags
    ↓
Phase 2: Observe patterns, measure inter-annotator agreement
    ↓
Phase 3: Consolidate frequent tags into categories
    ↓
Phase 4: Keep iterating (categories aren't fixed forever)
```

#### Phase 1: Start with Tags

Initially, `tags` is a freeform list. Annotators use whatever terms feel natural, with **autocomplete from existing tags** to encourage consistency:

```json
{
  "tags": ["wrong-api-call", "counting-error", "ignored-result"],
  "comment": "Agent called search_products but didn't sum the results"
}
```

**Tag autocomplete**: When adding tags, the UI shows existing tags sorted by frequency. This naturally converges on consistent terminology without enforcing a rigid schema upfront.

#### Phase 2: Analyze Tag Patterns

Periodically review tag frequency and clustering:

```python
# Example analysis script
from collections import Counter

tags = load_all_annotation_tags()
print(Counter(tags).most_common(20))

# Output:
# [('execution_error', 45), ('wrong_tool', 32), ('counting_error', 28), ...]
```

Measure inter-annotator agreement. If two people tag the same trace differently:
- Low agreement → refine tag definitions with examples
- High agreement → candidate for promotion to category

#### Phase 3: Consolidate into Categories

When patterns stabilize, promote frequent tags to categorical values:

```python
# Emerges from tag analysis, not predetermined
FAILURE_CATEGORIES = {
    "tool_error": ["wrong_tool", "wrong_args", "tool_misuse"],
    "reasoning_error": ["counting_error", "logic_error", "hallucination"],
    "context_error": ["missing_info", "ignored_context", "overflow"],
    # ... grows over time
}
```

#### Reference: Known Taxonomies

For inspiration (not prescription), these taxonomies exist in the literature:

| Source | Categories |
|--------|------------|
| **MAST** (Multi-Agent) | Specification, Inter-Agent, Verification failures |
| **GAIA** | Planning, Reasoning, Tool Execution errors |
| **SWE-bench** | Patch Generation, Test Reproduction, Spec Ambiguity |
| **WebArena** | Grounding, Infeasibility Detection, Repeated Actions |
| **Microsoft** | Security vs Safety, Novel vs Inherited failures |

See `docs/eval-research/gemini.md` Section 7.1 for a unified taxonomy proposal.

### Langfuse Score Mapping

Annotations map to Langfuse scores using metadata fields:

```python
# Creating a Langfuse score from annotation
langfuse.score(
    trace_id=annotation.session_id,
    observation_id=annotation.span_id,  # Optional
    name=annotation.name or "trace_analysis",
    value=annotation.score,
    comment=annotation.comment,
    metadata={
        "agent006_source": annotation.source,
        "agent006_failure_mode": annotation.failure_mode,
        "agent006_failure_detail": annotation.failure_detail,
        "agent006_label": annotation.label,
    }
)
```

## Eval Set Registry

A JSON file in the repo that curates traces for evaluation:

**Location**: `evaluation/trace_analyzer_eval_set.json`

```json
{
  "name": "trace-analyzer-eval-v1",
  "description": "Curated traces for evaluating the trace analyzer agent",
  "version": "1.0.0",
  "created_at": "2026-01-07T10:00:00Z",
  "updated_at": "2026-01-07T10:00:00Z",

  "entries": [
    {
      "id": "entry-001",
      "langfuse_trace_id": "abc123-def456",
      "source": "tau_bench",
      "tier": "stable",
      "benchmark": "tau_bench",
      "task_id": "retail_easy_001",
      "expected_tags": ["wrong-api-call", "counting-error"],
      "notes": "Agent called wrong API endpoint, didn't aggregate results"
    },
    {
      "id": "entry-002",
      "langfuse_trace_id": "xyz789-uvw012",
      "source": "manual",
      "tier": "frontier",
      "expected_tags": ["unauthorized-action", "missing-confirmation"],
      "notes": "Agent tried to delete user data without confirmation"
    }
  ],

  "stats": {
    "total_entries": 2,
    "by_tier": {
      "stable": 1,
      "frontier": 1,
      "horizon": 0
    },
    "by_tag": {
      "wrong-api-call": 1,
      "counting-error": 1,
      "unauthorized-action": 1,
      "missing-confirmation": 1
    },
    "by_source": {
      "tau_bench": 1,
      "manual": 1
    }
  }
}
```

### Entry Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier for this entry |
| `langfuse_trace_id` | Yes | Langfuse trace ID |
| `source` | Yes | Where trace came from: `tau_bench`, `bfcl`, `manual`, etc. |
| `tier` | Yes | Difficulty tier: `stable`, `frontier`, or `horizon` |
| `benchmark` | No | Benchmark name if from eval runner |
| `task_id` | No | Task ID within benchmark |
| `expected_tags` | No | Ground truth tags for evaluation (what human annotated) |
| `notes` | No | Human notes about this trace |

### Trace Tiers

Traces are classified into three tiers based on expected analyzer capability:

| Tier | Description | Expected Pass Rate |
|------|-------------|-------------------|
| `stable` | Reliably diagnosed - regression if failing | ≥95% |
| `frontier` | At the edge of capability | ~60-80% |
| `horizon` | Aspirational - cannot yet handle | N/A |

**Usage:**
- **Stable**: Well-understood failure patterns the analyzer reliably diagnoses. Watch for regressions.
- **Frontier**: Harder cases where we're actively improving. Track trend over time.
- **Horizon**: Cases we can't handle yet (e.g., subtle reasoning errors, multi-agent coordination failures). Track for future capability.

**Tracking Progress:**
```python
# Report pass rates by tier
for tier in ["stable", "frontier", "horizon"]:
    tier_results = [r for r in results if r.tier == tier]
    if tier_results:
        passed = sum(r.passed for r in tier_results)
        print(f"{tier}: {passed}/{len(tier_results)} ({100*passed/len(tier_results):.0f}%)")
```

## Trace Context (Domain Knowledge)

The LLM analyzer needs context to understand traces. "expected 10, got 1" is meaningless without knowing what the task was about.

### Context Sources

```
┌─────────────────────────────────────────────────────────────────┐
│                    Context for Analyzer                         │
├─────────────────────────────────────────────────────────────────┤
│  1. Eval Result (automatic)                                     │
│     - input: what the agent was asked to do                     │
│     - expected: correct answer/behavior                         │
│     - output: what agent actually produced                      │
│     - scores: why scorer said it failed                         │
│                                                                 │
│  2. Benchmark Context Template (per-benchmark)                  │
│     - Domain explanation (retail API, code repos, etc.)         │
│     - Common failure patterns for this benchmark                │
│     - How to interpret expected/actual values                   │
│                                                                 │
│  3. Task-Specific Context (optional, per-entry)                 │
│     - Extra notes for unusual/tricky cases                      │
└─────────────────────────────────────────────────────────────────┘
```

### Benchmark Context Templates

**Location**: `evaluation/benchmark_contexts/`

```
evaluation/
├── benchmark_contexts/
│   ├── tau_bench.md
│   ├── bfcl.md
│   ├── swe_bench.md
│   └── intercode.md
```

**Example**: `evaluation/benchmark_contexts/tau_bench.md`

```markdown
# TAU-bench Context for Trace Analyzer

## Domain
TAU-bench evaluates tool-use agents in simulated domains:
- **retail**: Customer service for an online store (orders, products, users)
- **airline**: Flight booking and customer support

## API Structure
The agent interacts with mock APIs via function calls:
- `get_user_details(user_id)` - Get customer info
- `search_products(query)` - Search product catalog
- `get_order_details(order_id)` - Get order info
- etc.

## Expected Output Format
- Usually a specific value (count, ID, status)
- Example: "expected 10, got 1" means the correct answer was 10 items/SKUs/etc.

## Common Failure Patterns
1. **Counting errors**: Agent doesn't aggregate results correctly
2. **Wrong API**: Uses get_order when should use search_products
3. **Missing confirmation**: Modifies data without user confirmation
4. **Partial results**: Returns first result instead of all matches
```

### Loading Context

```python
class TraceExplorer:
    def __init__(
        self,
        sessions: list[AgentSession],
        trace_file: str,
        eval_result: dict | None = None,
        benchmark_context: str | None = None,  # NEW
    ):
        self.eval_result = eval_result
        self.benchmark_context = benchmark_context

    def get_full_context(self) -> str:
        """Get all available context for the analyzer.

        Works with or without eval context:
        - With eval: includes benchmark context, task input/expected/output
        - Without eval: relies on mechanical findings and trace content alone
        """
        parts = []

        # 1. Benchmark context template (optional)
        if self.benchmark_context:
            parts.append("## Benchmark Context")
            parts.append(self.benchmark_context)

        # 2. Eval result context (optional - not present for manual debugging traces)
        if self.eval_result:
            parts.append("## Task Details")
            parts.append(self.get_eval_context())

        # 3. Mechanical findings (always available)
        findings = self.get_mechanical_findings()
        if findings:
            parts.append("## Mechanical Analysis Findings")
            parts.append(findings)

        # Note: If no context is available, the analyzer still works by
        # examining the trace content directly (turns, errors, tool calls)
        return "\n\n".join(parts)
```

### Analysis Without Eval Context

When analyzing traces from manual debugging sessions (no `.006eval.jsonl`), the analyzer:

1. **Still runs mechanical checks** - Detects errors, max iterations, syntax issues
2. **Examines trace content directly** - Tool calls, responses, execution patterns
3. **Identifies observable issues** - Loops, exceptions, empty responses, repeated failures
4. **Cannot assess correctness** - Without expected output, can't determine if the final answer was wrong

This is valuable for:
- Debugging crashes and errors during development
- Identifying inefficient agent behavior (unnecessary loops, redundant calls)
- Finding issues that don't require knowing the "right answer"

### Per-Entry Context (Optional)

For unusual cases, add context directly in the eval set registry:

```json
{
  "langfuse_trace_id": "abc123",
  "source": "tau_bench",
  "task_context": "Customer asked about blue t-shirts. Correct answer requires counting DISTINCT product IDs, not total search results."
}
```

## Annotation Workflow

### Workflow 1: From Eval Runner Results (Primary)

The main workflow for building the eval set:

```
1. Run eval
   python run_ablation.py --benchmark tau_bench --limit 50

2. Review results
   - Check .006eval.jsonl for failures
   - Identify 1-2 interesting traces to annotate

3. Open trace in viewer
   - Browse to the trace file
   - Press 'A' to open annotation form

4. Annotate the trace
   - Add freeform tags (comma-separated, autocomplete from existing)
   - Add comment explaining what went wrong
   - Click "Save" → writes annotation to local sidecar

5. Upload to Langfuse (optional)
   - Click "Upload" button → uploads trace + annotations to Langfuse

6. Add to eval set
   - Click "+ Eval Set" button
   - Select tier (stable/frontier/horizon)
   - Enter source (tau_bench, bfcl, manual, etc.)
   → Updates evaluation/trace_analyzer_eval_set.json
```

**Key insight**: You don't batch-upload all traces. You selectively annotate and upload individual interesting traces as you find them.

### Workflow 2: During Debugging (Ad-hoc)

For traces encountered during development/debugging:

```
1. Open trace viewer, browse to interesting trace
2. Press 'A' to open annotation form
3. Add tags + comment
4. Save → Upload → + Eval Set (same as above)
```

### Workflow 3: Quick CLI Annotation

For annotating without opening the viewer:

```bash
# Run mechanical checks on a trace
python -m e2e_optimization.annotate_trace run-checks \
  --trace results/traces/task_001.006trace.jsonl

# Add human annotation
python -m e2e_optimization.annotate_trace annotate \
  --trace results/traces/task_001.006trace.jsonl \
  --tags "wrong-api-call,counting-error" \
  --comment "Called get_order with wrong customer_id format" \
  --label FAIL

# Add to eval set
python -m e2e_optimization.manage_eval_set add \
  --trace results/traces/task_001.006trace.jsonl \
  --source tau_bench \
  --tier frontier \
  --tags "wrong-api-call,counting-error"

# View eval set stats
python -m e2e_optimization.manage_eval_set stats
```

## Evaluation Process

**Goal**: The trace analyzer feeds into e2e optimization. The real question is: **"Does this diagnosis help improve the agent?"**

We use an LLM judge (not tag F1) because:
- Semantic similarity matters ("wrong-api-call" ≈ "tool-misuse")
- Human annotations vary in terminology
- The goal is actionable diagnosis, not exact tag matching

### LLM Judge Scorer

```python
from eval_pipeline import LLMJudgeScorer

diagnosis_judge = LLMJudgeScorer(
    name="diagnosis_quality",
    prompt="""You are evaluating a trace analyzer's diagnosis of an agent failure.

## Human Annotation (ground truth)
Tags: {expected_tags}
Comment: {expected_comment}

## Analyzer's Diagnosis
{analyzer_output}

## Evaluation Criteria
1. **Issue Identification**: Does the diagnosis identify the same core issue(s) as the human?
   (Exact tag match not required - semantic equivalence counts)
2. **Actionability**: Would this diagnosis help someone fix the agent?
3. **Accuracy**: Is the diagnosis factually correct about what happened?

Rate the diagnosis:
- PASS: Captures the essential issue and is actionable
- PARTIAL: Identifies related issues but misses key point, or correct but not actionable
- FAIL: Misses the issue entirely or is misleading

Output JSON: {"verdict": "PASS|PARTIAL|FAIL", "reason": "..."}
""",
    pass_threshold=0.5,  # PASS=1.0, PARTIAL=0.5, FAIL=0.0
)
```

### Running the Eval

```python
from eval_pipeline import Evaluator, LLMJudgeScorer
from e2e_optimization.analyzer_agent import TraceAnalyzerAgent

# Load eval set
eval_set = load_eval_set("evaluation/trace_analyzer_eval_set.json")

# Build test data - include human annotation for judge context
test_data = [
    {
        "kwargs": {"trace_id": entry.langfuse_trace_id},
        "expected": {
            "tags": entry.expected_tags,
            "comment": entry.notes,
        },
    }
    for entry in eval_set.entries
]

evaluator = Evaluator(
    models={"analyzer": get_analyzer_llm()},
    output_dir="results/trace_analyzer_eval",
    name="trace_analyzer",
)

evaluator.add_test(
    name="trace_diagnosis",
    agent_class=TraceAnalyzerAgent,
    method="analyze",
    data=test_data,
    scorers=[diagnosis_judge],
)

results = await evaluator.run()
print(results.summary())
```

### Success Criteria

The trace analyzer succeeds if its diagnosis would help e2e optimization:

1. **Issue Identification** - Correctly identifies what went wrong (semantic match, not exact tags)
2. **Actionability** - Diagnosis points to something that can be fixed (prompt, tools, context)
3. **Accuracy** - Diagnosis is factually correct about what happened in the trace

**Note**: We accept "PARTIAL" as passing (score 0.5) because even partial diagnoses can inform optimization.

## Implementation Phases

### Phase 1: Mechanical Analysis Framework ✅
- [x] Create `e2e_optimization/mechanical_checks/` package structure
- [x] Implement `MechanicalCheck` ABC and `MechanicalFinding` dataclass
- [x] Implement initial checks: `execution_error`, `max_iterations`
- [x] Add `get_mechanical_findings()` to TraceExplorer
- [x] Write findings to annotation sidecar with `source: "mechanical"`
- [x] Add more checks incrementally as we find traces that need them

### Phase 2: Annotation Schema & Storage ✅
- [x] Extend annotation model with `tags` (list), `metadata` fields
- [x] Add `source: "mechanical"` support to annotation schema
- [x] Update Langfuse score mapping in LangfuseProvider
- [x] Create eval set registry schema and loader
- [x] Add tag frequency analysis tooling

### Phase 3: Context System (~70%)
- [ ] Create `evaluation/benchmark_contexts/` directory
- [ ] Write context templates for tau_bench, bfcl, intercode
- [x] Add `benchmark_context` parameter to TraceExplorer
- [x] Implement `get_full_context()` method
- [ ] Add `task_context` field to eval set registry entries

### Phase 4: Annotation Workflow ✅
- [x] Update trace viewer UI with tags input (autocomplete)
- [x] Add "Add to eval set" action in trace viewer
- [x] Create `manage_eval_set.py` CLI tool
- [x] Create `annotate_trace.py` CLI tool
- Note: Batch upload CLI not needed - trace viewer "Upload" button handles per-trace uploads

### Phase 5: Evaluation via eval_pipeline
- [ ] Configure `LLMJudgeScorer` with diagnosis quality prompt
- [ ] Create eval script using `Evaluator.add_test()` API
- [ ] Run via `evaluator.run()`
- [ ] Add tag consolidation tooling (for taxonomy evolution)

## Decisions

1. **Langfuse project setup**: Use existing project. We're in prototype/research phase.

2. **Annotation conflicts**: Use most recent annotation. No consensus required.

3. **Eval set versioning**: Defer. Git handles file versioning for now.

4. **Trace retention**: Defer. Not a concern in prototype phase.

# Eval Spans in Traces Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bake evaluation results into trace files as OpenTelemetry spans so the trace viewer can render them with the EvalPlugin.

**Architecture:** After scoring completes in the eval pipeline, create an OTel span named `eval` with attributes for test_id, pass/fail, scores, and per-scorer details. This span is written to the same trace file as the agent execution, enabling the trace viewer to display evaluation results inline.

**Tech Stack:** OpenTelemetry Python SDK, existing `openinference_instrumentation_nemo_oo_agents` package

---

## Background

The trace viewer has an `EvalPlugin` (`util/trace-viewer/frontend/js/plugins/eval.js`) that renders spans with `eval.*` attributes. The plugin expects:
- Span name: `eval` (triggers via `span.eval` pattern)
- Attributes: `eval.test_id`, `eval.passed`, `eval.weighted_score`, `eval.model`, `eval.agent_class`, `eval.method`
- Per-scorer: `eval.scorer.{name}.score`, `eval.scorer.{name}.passed`, `eval.scorer.{name}.reasoning`

Currently, the eval pipeline writes results to `.006eval.jsonl` files but never creates the OTel span.

---

### Task 1: Create Eval Span Writer Function

**Files:**
- Create: `util/eval_pipeline/src/eval_pipeline/trace_eval_span.py`
- Test: `util/eval_pipeline/tests/test_trace_eval_span.py`

**Step 1: Write the failing test for span creation**

```python
# util/eval_pipeline/tests/test_trace_eval_span.py
"""Tests for eval span creation in traces."""

import json
from pathlib import Path

import pytest

from eval_pipeline.eval_types import ScoreDetail
from eval_pipeline.trace_eval_span import write_eval_span_to_trace


class TestWriteEvalSpanToTrace:
    """Test eval span writing to trace files."""

    def test_writes_span_with_eval_attributes(self, tmp_path: Path):
        """Eval span has correct name and attributes."""
        trace_file = tmp_path / "test.006trace.jsonl"
        trace_file.write_text("")  # Create empty trace file

        write_eval_span_to_trace(
            trace_file=trace_file,
            test_id="test_001_gpt4_run1",
            passed=True,
            weighted_score=0.85,
            model="gpt-4",
            agent_class="TestAgent",
            method="run",
            scores={
                "exact_match": ScoreDetail(
                    score=1.0,
                    passed=True,
                    reasoning="Output matches expected",
                ),
                "llm_judge": ScoreDetail(
                    score=0.7,
                    passed=True,
                    reasoning="Good quality response",
                ),
            },
        )

        # Read the span from trace file
        content = trace_file.read_text()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 1, f"Expected 1 span, got {len(lines)}"

        span = json.loads(lines[0])

        # Verify span name
        assert span["name"] == "eval"

        # Verify core attributes
        attrs = span["attributes"]
        assert attrs["eval.test_id"] == "test_001_gpt4_run1"
        assert attrs["eval.passed"] is True
        assert attrs["eval.weighted_score"] == 0.85
        assert attrs["eval.model"] == "gpt-4"
        assert attrs["eval.agent_class"] == "TestAgent"
        assert attrs["eval.method"] == "run"

        # Verify scorer attributes
        assert attrs["eval.scorer.exact_match.score"] == 1.0
        assert attrs["eval.scorer.exact_match.passed"] is True
        assert attrs["eval.scorer.exact_match.reasoning"] == "Output matches expected"
        assert attrs["eval.scorer.llm_judge.score"] == 0.7
        assert attrs["eval.scorer.llm_judge.passed"] is True
        assert attrs["eval.scorer.llm_judge.reasoning"] == "Good quality response"

    def test_handles_missing_reasoning(self, tmp_path: Path):
        """Scorer without reasoning still creates attributes."""
        trace_file = tmp_path / "test.006trace.jsonl"
        trace_file.write_text("")

        write_eval_span_to_trace(
            trace_file=trace_file,
            test_id="test_002",
            passed=False,
            weighted_score=0.3,
            model="gpt-3.5",
            agent_class="Agent",
            method="execute",
            scores={
                "basic": ScoreDetail(score=0.3, passed=False, reasoning=None),
            },
        )

        span = json.loads(trace_file.read_text().strip())
        attrs = span["attributes"]

        assert attrs["eval.scorer.basic.score"] == 0.3
        assert attrs["eval.scorer.basic.passed"] is False
        assert "eval.scorer.basic.reasoning" not in attrs

    def test_does_nothing_if_trace_file_is_none(self, tmp_path: Path):
        """No error if trace_file is None."""
        # Should not raise
        write_eval_span_to_trace(
            trace_file=None,
            test_id="test_003",
            passed=True,
            weighted_score=1.0,
            model="gpt-4",
            agent_class="Agent",
            method="run",
            scores={},
        )

    def test_does_nothing_if_trace_file_does_not_exist(self, tmp_path: Path):
        """No error if trace file doesn't exist."""
        trace_file = tmp_path / "nonexistent.006trace.jsonl"

        # Should not raise
        write_eval_span_to_trace(
            trace_file=trace_file,
            test_id="test_004",
            passed=True,
            weighted_score=1.0,
            model="gpt-4",
            agent_class="Agent",
            method="run",
            scores={},
        )
```

**Step 2: Run test to verify it fails**

Run: `cd util/eval_pipeline && python -m pytest tests/test_trace_eval_span.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval_pipeline.trace_eval_span'`

**Step 3: Write minimal implementation**

```python
# util/eval_pipeline/src/eval_pipeline/trace_eval_span.py
"""Write evaluation results as spans to trace files.

This module creates OpenTelemetry-compatible spans containing eval results
that can be rendered by the trace viewer's EvalPlugin.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eval_pipeline.eval_types import ScoreDetail


def write_eval_span_to_trace(
    trace_file: Path | None,
    test_id: str,
    passed: bool,
    weighted_score: float,
    model: str,
    agent_class: str,
    method: str,
    scores: dict[str, ScoreDetail],
    duration_ns: int | None = None,
) -> None:
    """Write an eval span to the trace file.

    Creates a span with name 'eval' and attributes that the trace viewer's
    EvalPlugin expects. The span is appended to the existing trace file.

    Args:
        trace_file: Path to trace file. No-op if None or doesn't exist.
        test_id: Unique test identifier (e.g., "test_001_gpt4_run1")
        passed: Overall pass/fail status
        weighted_score: Weighted score across all scorers (0.0-1.0)
        model: Model identifier
        agent_class: Agent class name
        method: Method name that was evaluated
        scores: Dict of scorer name -> ScoreDetail with per-scorer results
        duration_ns: Optional duration in nanoseconds (for timing display)
    """
    if trace_file is None or not trace_file.exists():
        return

    # Build span attributes
    attributes: dict[str, str | float | bool] = {
        "eval.test_id": test_id,
        "eval.passed": passed,
        "eval.weighted_score": weighted_score,
        "eval.model": model,
        "eval.agent_class": agent_class,
        "eval.method": method,
    }

    # Add per-scorer attributes
    for scorer_name, detail in scores.items():
        prefix = f"eval.scorer.{scorer_name}"
        attributes[f"{prefix}.score"] = detail.score
        attributes[f"{prefix}.passed"] = detail.passed
        if detail.reasoning:
            attributes[f"{prefix}.reasoning"] = detail.reasoning

    # Add duration if provided
    if duration_ns is not None:
        attributes["duration_ns"] = duration_ns

    # Build span structure (OTel-compatible JSONL format)
    now_ns = time.time_ns()
    span = {
        "span_id": uuid.uuid4().hex[:16],
        "trace_id": uuid.uuid4().hex,
        "parent_span_id": None,
        "name": "eval",
        "start_time": now_ns,
        "end_time": now_ns,
        "duration_ns": duration_ns or 0,
        "attributes": attributes,
        "events": [],
        "status": {
            "status_code": "OK" if passed else "ERROR",
            "description": None,
        },
        "resource": {"attributes": {}},
    }

    # Append to trace file
    with open(trace_file, "a") as f:
        f.write(json.dumps(span, default=str) + "\n")
```

**Step 4: Run test to verify it passes**

Run: `cd util/eval_pipeline && python -m pytest tests/test_trace_eval_span.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add util/eval_pipeline/src/eval_pipeline/trace_eval_span.py util/eval_pipeline/tests/test_trace_eval_span.py
git commit -m "feat(eval-pipeline): add trace_eval_span module for writing eval spans to traces"
```

---

### Task 2: Integrate Eval Span Writing into Pipeline

**Files:**
- Modify: `util/eval_pipeline/src/eval_pipeline/pipeline.py:285-320`
- Test: `util/eval_pipeline/tests/test_pipeline_eval_span.py`

**Step 1: Write the failing integration test**

```python
# util/eval_pipeline/tests/test_pipeline_eval_span.py
"""Tests for eval span integration in pipeline."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from eval_pipeline.eval_types import ScoreDetail
from eval_pipeline.pipeline import process_sample
from eval_pipeline.config import PipelineConfig


class TestProcessSampleEvalSpan:
    """Test that process_sample writes eval span to trace."""

    @pytest.fixture
    def mock_sample(self):
        """Create a mock sample for testing."""
        sample = MagicMock()
        sample.task.id = "test_001"
        sample.task.run_id = 1
        sample.model = "test-model"
        sample.agent_class = "TestAgent"
        sample.method = "run"
        sample.display_name = "Test"
        sample.tier = "stable"
        sample.scorers = []

        # Mock agent factory
        mock_agent = MagicMock()
        sample.agent_factory = MagicMock(return_value=mock_agent)

        return sample

    @pytest.fixture
    def mock_writer(self):
        """Create a mock writer."""
        writer = MagicMock()
        writer.append_result = MagicMock()
        return writer

    @pytest.mark.asyncio
    async def test_process_sample_writes_eval_span(
        self, tmp_path: Path, mock_sample, mock_writer, monkeypatch
    ):
        """process_sample writes eval span to trace file."""
        trace_file = tmp_path / "test.006trace.jsonl"
        trace_file.write_text("")  # Create empty trace file

        # Mock execute_task to return a result
        mock_result = MagicMock()
        mock_result.error = None

        async def mock_execute_task(**kwargs):
            return mock_result

        monkeypatch.setattr(
            "eval_pipeline.pipeline.execute_task", mock_execute_task
        )

        # Mock build_scoring_context
        mock_ctx = MagicMock()
        mock_ctx.input = "test input"
        mock_ctx.actual = "test output"
        mock_ctx.expected = "expected"
        mock_ctx.error = None
        mock_ctx.input_tokens = 10
        mock_ctx.output_tokens = 20
        mock_ctx.total_tokens = 30

        monkeypatch.setattr(
            "eval_pipeline.pipeline.build_scoring_context",
            lambda _: mock_ctx,
        )

        # Mock score_task to return scores
        async def mock_score_task(ctx, scorers):
            return {
                "test_scorer": {
                    "score": 0.9,
                    "reasoning": "Test reasoning",
                }
            }

        monkeypatch.setattr(
            "eval_pipeline.pipeline.score_task", mock_score_task
        )

        # Mock tracing exports (no-op)
        monkeypatch.setattr(
            "eval_pipeline.pipeline.get_current_exporter",
            lambda: None,
        )

        config = PipelineConfig(pass_threshold=0.5)

        # Run the pipeline
        result = await process_sample(
            sample=mock_sample,
            trace_file=trace_file,
            config=config,
            writer=mock_writer,
        )

        # Verify trace file has eval span
        content = trace_file.read_text()
        lines = [line for line in content.strip().split("\n") if line]

        # Find the eval span
        eval_spans = [
            json.loads(line) for line in lines
            if json.loads(line).get("name") == "eval"
        ]

        assert len(eval_spans) == 1, f"Expected 1 eval span, found {len(eval_spans)}"

        span = eval_spans[0]
        attrs = span["attributes"]

        assert "eval.test_id" in attrs
        assert attrs["eval.passed"] is True
        assert attrs["eval.weighted_score"] == 0.9
        assert attrs["eval.model"] == "test-model"
        assert attrs["eval.agent_class"] == "TestAgent"
        assert attrs["eval.method"] == "run"
        assert attrs["eval.scorer.test_scorer.score"] == 0.9
```

**Step 2: Run test to verify it fails**

Run: `cd util/eval_pipeline && python -m pytest tests/test_pipeline_eval_span.py -v`
Expected: FAIL with assertion error (no eval span in trace)

**Step 3: Modify pipeline.py to call write_eval_span_to_trace**

Add import at top of file (around line 25):
```python
from eval_pipeline.trace_eval_span import write_eval_span_to_trace
```

Insert the following after line 268 (after `typed_scores` is built) and before line 270 (before "Create identity fields"):

```python
    # Stage 4: Write eval span to trace (for trace viewer rendering)
    write_eval_span_to_trace(
        trace_file=trace_file,
        test_id=f"{sample.task.id}_{sample.model.split('/')[-1] if sample.model else 'default'}_run{getattr(sample.task, 'run_id', 1)}",
        passed=weighted_score >= config.pass_threshold and result.error is None,
        weighted_score=weighted_score,
        model=sample.model or "unknown",
        agent_class=sample.agent_class,
        method=sample.method,
        scores=typed_scores,
    )
```

**Step 4: Run test to verify it passes**

Run: `cd util/eval_pipeline && python -m pytest tests/test_pipeline_eval_span.py -v`
Expected: PASS

**Step 5: Run existing pipeline tests to check for regressions**

Run: `cd util/eval_pipeline && python -m pytest tests/test_pipeline.py -v`
Expected: All existing tests PASS

**Step 6: Commit**

```bash
git add util/eval_pipeline/src/eval_pipeline/pipeline.py util/eval_pipeline/tests/test_pipeline_eval_span.py
git commit -m "feat(eval-pipeline): integrate eval span writing into process_sample"
```

---

### Task 3: Export trace_eval_span in Package __init__

**Files:**
- Modify: `util/eval_pipeline/src/eval_pipeline/__init__.py`

**Step 1: Verify current exports**

Read the current `__init__.py` to find the correct location for the new export.

**Step 2: Add export**

Add to the imports section:
```python
from eval_pipeline.trace_eval_span import write_eval_span_to_trace
```

Add to `__all__` list:
```python
"write_eval_span_to_trace",
```

**Step 3: Run import test**

Run: `cd util/eval_pipeline && python -c "from eval_pipeline import write_eval_span_to_trace; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add util/eval_pipeline/src/eval_pipeline/__init__.py
git commit -m "feat(eval-pipeline): export write_eval_span_to_trace in package"
```

---

### Task 4: End-to-End Verification

**Step 1: Run full test suite**

Run: `cd util/eval_pipeline && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 2: Manual verification with real eval run**

Run a small eval to verify the span appears in traces:

```bash
cd util/eval_pipeline
python -m eval_pipeline.cli run \
    --test-dir ../../tests/capabilities/stable \
    --pattern "test_simple*.py" \
    --limit 1 \
    --output-dir /tmp/eval_test
```

Then inspect the trace file:
```bash
cat /tmp/eval_test/*.006trace.jsonl | grep '"name": "eval"' | head -1 | python -m json.tool
```

Expected: JSON output showing eval span with attributes.

**Step 3: Commit final state**

```bash
git status
# Verify all changes are committed
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Create eval span writer | `trace_eval_span.py`, test |
| 2 | Integrate into pipeline | `pipeline.py`, test |
| 3 | Export in package | `__init__.py` |
| 4 | E2E verification | Manual test |

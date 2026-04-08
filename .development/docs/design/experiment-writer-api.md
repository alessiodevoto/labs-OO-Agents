# ExperimentWriter API

**Location**: `util/viewer_utils/src/viewer_utils/experiment_writer.py`

## Purpose

Centralized utility for all evaluation runners to write `.006eval.jsonl` files in a consistent format compatible with the Agent006 Evaluation Viewer.

## Why Use ExperimentWriter?

**Before**: Each runner implemented its own file writing logic:
- `evaluation/runner.py`: Wrapped results in `{"result": ...}`, causing parsing bugs
- `prompt-optimization/runner.py`: Rewrote entire file each update (inefficient)
- Different formats made debugging difficult

**After**: Single source of truth for file format:
- ✅ Consistent format across all runners
- ✅ Crash-safe incremental writes (JSONL during execution)
- ✅ Proper `status` field for live updates
- ✅ Auto-finalization with metadata

## File Format

### During Execution (JSONL)

```jsonl
{"metadata": {"timestamp": "...", "status": "running", ...}, "results": []}
{"test_id": "test_001", "passed": true, ...}
{"test_id": "test_002", "passed": false, ...}
```

- Line 1: Metadata container with `status="running"`
- Lines 2+: Individual test results (unwrapped)
- File grows incrementally as tests complete
- Safe if process crashes mid-run

### After Finalization (JSON)

```json
{
  "metadata": {
    "timestamp": "2025-12-11T14:00:00",
    "status": "completed",
    "completed_at": "2025-12-11T14:05:00",
    "duration_seconds": 300,
    ...
  },
  "results": [
    {"test_id": "test_001", "passed": true, ...},
    {"test_id": "test_002", "passed": false, ...}
  ]
}
```

- Single JSON object with all results
- `status="completed"` with completion metadata
- Pretty-printed for human readability

## API Usage

### Basic Example

```python
from viewer_utils import ExperimentWriter

# Create writer
writer = ExperimentWriter(
    output_dir="results",
    experiment_name="sentiment_classifier"
)

# Start experiment
file_path = writer.start(metadata={
    "suite_name": "sentiment",
    "models": ["gpt-4o-mini"],
    "config": {"temperature": 0.7}
})
print(f"Writing to: {file_path}")

# Run tests and append results
for test_case in test_cases:
    result = run_test(test_case)

    # Format result (see schema below)
    test_result = {
        "test_id": test_case.id,
        "test": "sentiment",
        "display_name": test_case.description,
        "model": "gpt-4o-mini",
        "test_type": "sentiment",
        "passed": result.success,
        "eval": {
            "passed": result.success,
            "score": result.score,
            "metrics": {
                "exact_match": result.exact_match,
                "llm_judge_score": result.llm_score,
            },
            "reasoning": result.reasoning
        },
        "input": test_case.input,
        "expected": test_case.expected
    }

    writer.append_result(test_result)

# Finalize when done
writer.finalize(final_metadata={
    "duration_seconds": elapsed_time,
    "aggregate_metrics": {
        "pass_rate": pass_count / total_count,
        "avg_score": sum(scores) / len(scores)
    }
})
```

### Context Manager (Auto-Finalization)

```python
from viewer_utils import ExperimentWriter

with ExperimentWriter("results", "my_experiment") as writer:
    writer.start(metadata={"suite_name": "my_suite"})

    for test in tests:
        result = run_test(test)
        writer.append_result(result)

    # Finalize() called automatically on exit
```

### Crash Simulation (Testing)

```python
writer = ExperimentWriter("results", "crash_test")
writer.start()
writer.append_result(some_result)
# Don't call finalize() - simulates crash
# File stays with status="running"
# Viewer will mark as STALE after 60 seconds
```

## Test Result Schema

Each test result passed to `append_result()` must follow this schema:

```python
{
    # Required fields
    "test_id": str,          # Unique test identifier
    "test": str,             # Test suite name
    "display_name": str,     # Human-readable description
    "model": str,            # Model identifier
    "test_type": str,        # Test type (for renderer selection)
    "passed": bool,          # Overall pass/fail

    # Evaluation results (required)
    "eval": {
        "passed": bool,      # Pass/fail
        "score": float,      # 0.0 to 1.0
        "metrics": dict,     # Multiple scorer outputs (see below)
        "reasoning": str     # Optional explanation
    },

    # Optional fields
    "input": dict,           # Test input data
    "expected": Any,         # Expected output
    "trace_file": str,       # Path to trace file
    "error": str,            # Error message if failed
    "iterations": list,      # For iterative improvements
}
```

### Multiple Scorers

The `eval.metrics` field supports multiple scorers:

```python
"eval": {
    "score": 0.85,  # Overall score
    "metrics": {
        "exact_match": True,           # Scorer 1
        "llm_judge_score": 0.9,        # Scorer 2
        "code_quality": "acceptable",  # Scorer 3
        "tokens_used": 1250            # Scorer 4
    }
}
```

The viewer displays all metrics as key-value pairs in the UI.

## Methods

### `__init__(output_dir, experiment_name, timestamp=None)`

Initialize experiment writer.

**Args:**
- `output_dir`: Directory to write results
- `experiment_name`: Name for filename (auto-sanitized)
- `timestamp`: Optional timestamp (defaults to now)

**Returns:** ExperimentWriter instance

**Example:**
```python
writer = ExperimentWriter(
    output_dir="results/evaluation",
    experiment_name="sentiment_classification",
    timestamp=datetime(2025, 12, 11, 14, 0, 0)
)
# Creates: results/evaluation/sentimentclassification_20251211_140000.006eval.jsonl
```

### `start(metadata=None) -> Path`

Initialize the experiment file with metadata line.

**Args:**
- `metadata`: Optional metadata dict (merged with defaults)

**Default metadata:**
```python
{
    "timestamp": "2025-12-11T14:00:00",
    "suite_name": experiment_name,
    "status": "running"
}
```

**Returns:** Path to created file

**Raises:** RuntimeError if already started

### `append_result(result: dict) -> None`

Append a test result to the file.

**Args:**
- `result`: Test result dictionary (see schema above)

**Behavior:**
- Writes result as single JSONL line
- Appends to file (doesn't rewrite)
- Crash-safe: each append is atomic

**Raises:**
- RuntimeError if not started or already finalized

### `finalize(final_metadata=None, rewrite=True) -> None`

Mark experiment as completed.

**Args:**
- `final_metadata`: Additional metadata (duration, aggregate metrics, etc.)
- `rewrite`: If True (default), rewrite file as single JSON object

**Behavior:**
- Updates `status="completed"`
- Adds `completed_at` timestamp
- Merges `final_metadata` into metadata
- Rewrites file as pretty-printed JSON (if `rewrite=True`)

**Example:**
```python
writer.finalize(final_metadata={
    "duration_seconds": 123.4,
    "aggregate_metrics": {
        "total_tests": 10,
        "passed": 8,
        "pass_rate": 0.8
    }
})
```

## Integration with Existing Runners

### evaluation/runner.py

Replace custom file writing logic with:

```python
from viewer_utils import ExperimentWriter

class BenchmarkRunner:
    async def run_benchmark(self, name, adapter, task_limit):
        writer = ExperimentWriter(
            output_dir=self.config.results_dir,
            experiment_name=name
        )

        writer.start(metadata={
            "suite_name": name,
            "models": [self.config.model],
            "pass_at_k": self.config.pass_at_k
        })

        for task in tasks:
            result = await self.run_task(task)
            writer.append_result(format_result(result))

        writer.finalize(final_metadata={
            "duration_seconds": elapsed,
            "aggregate_metrics": metrics
        })
```

### prompt-optimization/runner.py

Replace `write_incremental_results()` with:

```python
from viewer_utils import ExperimentWriter

# Setup
writer = ExperimentWriter(results_dir, suite_name)
writer.start(metadata={
    "suite_name": config["name"],
    "models": model_ids,
    "strategy_mode": strategy_config_name
})

# In test loop
writer.append_result(test_result)

# At end
writer.finalize()
```

## File Discovery

The viewer auto-discovers experiment files using these patterns:
- `**/*.006eval.json` - Finalized experiments
- `**/*.006eval.jsonl` - Running or finalized experiments

Files are auto-detected from project root, excluding:
- `.venv/`, `node_modules/`, `.git/`
- Any directory in `.gitignore`

## Testing

Example script: `util/viewer_utils/examples/experiment_writer_example.py`

```bash
# Basic usage
python util/viewer_utils/examples/experiment_writer_example.py --example basic

# Context manager
python util/viewer_utils/examples/experiment_writer_example.py --example context

# Crash simulation (for testing stale detection)
python util/viewer_utils/examples/experiment_writer_example.py --example crash
```

Debug experiments:

```bash
# Check specific file
python util/prompt-optimization/viewer/debug_experiments.py --file results/myexp.006eval.jsonl

# Watch all experiments
python util/prompt-optimization/viewer/debug_experiments.py --watch --results-dir results
```

## Troubleshooting

### Results don't appear incrementally in viewer

**Cause:** Backend polling might be disabled or file parsing failed

**Solution:**
1. Check backend console for errors
2. Verify file format with debug tool:
   ```bash
   python viewer/debug_experiments.py --file path/to/experiment.006eval.jsonl
   ```
3. Ensure live-updater polls every 2 seconds (check browser console)

### Viewer shows experiment as CORRUPT

**Cause:** Invalid JSON in file

**Solution:**
1. Check file manually: `cat experiment.006eval.jsonl`
2. Verify each line is valid JSON: `python -m json.tool < file.jsonl`
3. Check for Python exceptions during write

### Viewer shows experiment as STALE

**Cause:** File has `status="running"` but hasn't been modified in 60+ seconds

**Expected:** This means the runner crashed without calling `finalize()`

**Solution:**
- For production: Fix the crash (check logs)
- For testing: This is correct behavior!

## Related Documentation

- [Prompt Optimization Viewer Upgrade Plan](prompt-optimization-viewer-upgrade.md)
- [Viewer Backend API](../util/prompt-optimization/viewer/backend/README.md)
- [Experiment Detection System](../util/prompt-optimization/viewer/debug_experiments.py)

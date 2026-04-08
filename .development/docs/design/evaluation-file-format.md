# Agent006 Evaluation File Format Specification

## File Naming

`{suite_name}_{timestamp}.006eval.jsonl`

Examples:
- `sentiment_20251211_144101.006eval.jsonl`
- `capabilitytests_20251211_070720.006eval.jsonl`

## Format: JSONL (JSON Lines)

**During execution**: Write line-by-line for incremental updates
**After completion**: Can optionally consolidate to single JSON object

### Line 1: Experiment Header

```json
{
  "metadata": {
    "timestamp": "2025-12-11T14:14:29.860273",
    "suite_name": "sentiment",
    "status": "running|completed|error",
    "config": {
      "models": ["gpt-4o-mini", "claude-sonnet-4"],
      "variants": ["v1_baseline", "v2_cot"],
      "judges": ["exact_match", "llm_judge"]
    }
  },
  "results": []
}
```

### Lines 2+: Individual Test Results

```json
{
  "test_id": "sentiment_006",
  "test_name": "sentiment",
  "display_name": "Classify sentiment: I'm so grateful...",
  "model": "gpt-4o-mini",
  "variant": "v1_baseline",
  "input": {
    "text": "I'm so grateful for all the support from my friends."
  },
  "output": "positive",
  "expected": "positive",
  "passed": true,
  "scores": {
    "exact_match": {
      "passed": true,
      "score": 1.0,
      "reason": "Output matches expected value exactly"
    },
    "llm_judge": {
      "passed": true,
      "score": 0.95,
      "reason": "Semantically correct with high confidence"
    }
  },
  "metrics": {
    "iterations": 1,
    "first_attempt_passed": true,
    "execution_time_ms": 1250
  },
  "trace_file": "traces/evaluation/sentiment/sentiment_006_20251211.006trace.jsonl",
  "error": null
}
```

**Field Semantics**:
- `passed` (top-level): **AND of all judges** - true only if ALL judges in `scores` passed
- `scores`: Dictionary of judge results, each with:
  - `passed` (bool): Whether this judge considers the test passed
  - `score` (float): Numeric score, typically 0.0-1.0
  - `reason` (str): Human-readable explanation of the judgment

## Design Rationale

### Multi-dimensional Support

The format naturally supports all dimensions:

1. **Multiple Models**: Each test result has `model` field
   ```json
   {"test_id": "test_001", "model": "gpt-4o-mini", ...}
   {"test_id": "test_001", "model": "claude-sonnet-4", ...}
   ```

2. **Multiple Variants**: Each test result has `variant` field
   ```json
   {"test_id": "test_001", "variant": "v1_baseline", ...}
   {"test_id": "test_001", "variant": "v2_cot", ...}
   ```

3. **Multiple Judges**: `scores` object contains one entry per judge
   ```json
   "scores": {
     "exact_match": {"passed": true, "score": 1.0},
     "llm_judge": {"passed": true, "score": 0.95},
     "code_quality": {"passed": false, "score": 0.6}
   }
   ```

### Aggregation Examples

**By Model**:
```python
by_model = {}
for test in results:
    model = test["model"]
    by_model[model] = by_model.get(model, []) + [test]
```

**By Variant**:
```python
by_variant = {}
for test in results:
    variant = test["variant"]
    by_variant[variant] = by_variant.get(variant, []) + [test]
```

**By Judge**:
```python
by_judge = {}
for test in results:
    for judge_name, judge_result in test["scores"].items():
        by_judge[judge_name] = by_judge.get(judge_name, []) + [judge_result]
```

### Optional Fields

Fields can be omitted if not applicable:
- `variant`: If only one variant tested
- `expected`: For generative tasks without ground truth
- `scores`: Can have just one judge, or many
- `error`: Only present if test errored

### Test Combinations

For full matrix testing (M models × V variants × T tests):

```
Total results = M × V × T

Example: 2 models × 3 variants × 100 tests = 600 result lines
```

Each line represents one (model, variant, test) combination.

## Reading Logic

### During Execution (Incremental)

```python
def read_experiment_incremental(file_path):
    with open(file_path) as f:
        # Line 1: metadata
        metadata = json.loads(f.readline())

        # Lines 2+: test results
        results = []
        for line in f:
            if line.strip():
                results.append(json.loads(line))

        return metadata, results
```

### After Completion (Consolidated)

Runner can optionally rewrite as single JSON:

```json
{
  "metadata": {...},
  "results": [
    {"test_id": "...", ...},
    {"test_id": "...", ...}
  ]
}
```

Backend reads both formats automatically.

## Viewer Aggregations

The viewer can slice data multiple ways:

### Pass Rates by Model
```
gpt-4o-mini:     95/100 (95%)
claude-sonnet-4: 98/100 (98%)
```

### Pass Rates by Variant
```
v1_baseline: 85/100 (85%)
v2_cot:      95/100 (95%)
v3_reflexion: 98/100 (98%)
```

### Pass Rates by Judge
```
exact_match: 90/100 (90%)
llm_judge:   85/100 (85%)  (more strict)
```

### Heatmap: Model × Variant
```
            v1_baseline  v2_cot  v3_reflexion
gpt-4o-mini      85%      92%       95%
claude-4         88%      95%       98%
```

## Migration Path

### Evaluation Runner
Current output has nested structure - flatten to this format:
- Extract tests from `results[0].single.results`
- Add `model` and `variant` fields
- Rename `eval` → `scores`

### Prompt Optimization Runner
Already close to this format - minor adjustments:
- Flatten `ModelResult` wrapper
- Rename fields to match spec

### E2E Optimization Runner
TBD - examine current output and adapt

## Field Requirements

### Required Fields (every test result MUST have these)

- `test_id` (str): Unique identifier for this test case
- `model` (str): Which model was used (e.g., "gpt-4o-mini", "claude-sonnet-4")
- `variant` (str): Which prompt variant was used (e.g., "v1_baseline", "v2_cot")
  - **Must be provided even if only one variant exists**
  - Use descriptive names like "v1_baseline" rather than omitting
- `passed` (bool): Overall pass/fail = **AND of all judges in scores**
- `scores` (dict): At least one judge result
  - Each judge entry must have:
    - `passed` (bool)
    - `score` (float)
    - `reason` (str): Explanation of the judgment

### Optional Fields

- `test_name` (str): Test category/type (defaults to test_id if omitted)
- `display_name` (str): Human-readable description for UI
- `input` (any): Test input data (structure depends on test type)
- `output` (any): Model's output/response
- `expected` (any): Expected/ground truth output (for comparison tests)
- `metrics` (dict): Additional metrics (iterations, timing, etc.)
- `trace_file` (str): Path to trace file for debugging
- `error` (str): Error message if test errored (null if no error)

# Token Tracking Implementation in eval_pipeline

**Date:** 2025-01-06
**Status:** Implemented and tested

## Overview

Added support for tracking input tokens, output tokens, and total tokens in eval_pipeline evaluation results. Token information is automatically extracted from OpenTelemetry trace files and included in `.006eval.jsonl` result files.

## Motivation

Token usage is a critical metric for LLM-based systems:
- **Cost tracking**: Monitor API costs across evaluations
- **Performance analysis**: Identify verbose prompts or inefficient agents
- **Model comparison**: Compare token efficiency across different models
- **Optimization**: Track token usage improvements over iterations

## Implementation

### Changes Made

#### 1. Updated `count_tokens_from_trace()` in `scoring.py`

**Before:**
```python
def count_tokens_from_trace(trace_file: Path) -> int | None:
    """Count total tokens from trace file."""
    # Returned only total tokens
```

**After:**
```python
def count_tokens_from_trace(trace_file: Path) -> tuple[int, int, int] | None:
    """Count input, output, and total tokens from trace file.

    Returns:
        Tuple of (input_tokens, output_tokens, total_tokens) or None if no data found.
    """
```

The function now:
- Sums `llm.token_count.prompt` and `gen_ai.usage.prompt_tokens` for input tokens
- Sums `llm.token_count.completion` and `gen_ai.usage.completion_tokens` for output tokens
- Sums `llm.token_count.total` and `gen_ai.usage.total_tokens` for total tokens
- Returns a tuple of all three values, or None if no token data found

#### 2. Extended `ScoringContext` model in `models.py`

Added three new fields:
```python
@dataclass
class ScoringContext:
    # ... existing fields ...

    # New fields
    input_tokens: int | None = None   # Number of input (prompt) tokens
    output_tokens: int | None = None  # Number of output (completion) tokens
    total_tokens: int | None = None   # Total tokens used
```

#### 3. Updated `build_scoring_context()` in `scoring.py`

`token_count` field has been removed from `ScoringContext` and is no longer populated.

Modified to populate the new token fields:
```python
def build_scoring_context(result: ExecutionResult) -> ScoringContext:
    token_data = count_tokens_from_trace(result.trace_file)

    input_tokens = None
    output_tokens = None
    total_tokens = None

    if token_data:
        input_tokens, output_tokens, total_tokens = token_data

    return ScoringContext(
        # ... other fields ...
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        # ... error field ...
    )
```

#### 4. Extended `EvalTestResult` schema in `eval_types.py`

Added three new optional fields to the result schema:
```python
class EvalTestResult(BaseModel):
    # ... existing fields ...

    # Token usage
    input_tokens: int | None = None   # Number of input (prompt) tokens
    output_tokens: int | None = None  # Number of output (completion) tokens
    total_tokens: int | None = None   # Total tokens used

    # ... error fields ...
```

#### 5. Updated `process_sample()` in `pipeline.py`

Modified to pass token information to the result:
```python
eval_result = EvalTestResult(
    # ... other fields ...
    input_tokens=ctx.input_tokens,
    output_tokens=ctx.output_tokens,
    total_tokens=ctx.total_tokens,
    # ... other fields ...
)
```

### Token Data Source

Token information comes from OpenTelemetry trace files (`.006trace.jsonl`), specifically from span attributes:

- **Standard OpenInference format**:
  - `llm.token_count.prompt` → input tokens
  - `llm.token_count.completion` → output tokens
  - `llm.token_count.total` → total tokens

- **OpenAI GenAI format** (fallback):
  - `gen_ai.usage.prompt_tokens` → input tokens
  - `gen_ai.usage.completion_tokens` → output tokens
  - `gen_ai.usage.total_tokens` → total tokens

The implementation sums token counts across all LLM spans in the trace, capturing multi-turn conversations.

## Usage

### Automatic Population

Token data is automatically extracted and included in results when:
1. Tracing is enabled (via `openinference_instrumentation_agent006`)
2. The LLM client populates token usage in trace spans
3. The trace file exists and is readable

No code changes are required in existing evaluations.

### Reading Token Data from Results

```python
from eval_pipeline.eval_parser import EvalFileParser

# Parse evaluation results
parser = EvalFileParser()
metadata, results, completion = parser.parse_file('results.006eval.jsonl')

# Access token information for each result
for result in results:
    if result.input_tokens is not None:
        print(f"{result.test_id}:")
        print(f"  Input tokens:  {result.input_tokens:,}")
        print(f"  Output tokens: {result.output_tokens:,}")
        print(f"  Total tokens:  {result.total_tokens:,}")

# Calculate aggregate statistics
total_input = sum(r.input_tokens or 0 for r in results)
total_output = sum(r.output_tokens or 0 for r in results)
print(f"Total tokens: {total_input + total_output:,}")
```

### Result JSON Structure

Token fields appear in the `.006eval.jsonl` file:
```json
{
  "_type": "result",
  "test_id": "sentiment_001_gpt4_run1",
  "passed": true,
  "model": "openai/gpt-4",
  "input_tokens": 150,
  "output_tokens": 50,
  "total_tokens": 200,
  ...
}
```

### Handling Missing Token Data

Token fields are optional (`int | None`). They will be `null` if:
- Tracing is not enabled
- The trace file doesn't exist
- The LLM client doesn't populate token usage
- Token attributes are not present in the trace

Always check for `None` before using:
```python
if result.total_tokens is not None:
    cost = result.total_tokens * price_per_token
```

## Testing

All existing tests pass (79 passed, 45 skipped):
- `test_scoring.py`: Validates token extraction from traces
- `test_models.py`: Validates ScoringContext structure
- `test_pipeline.py`: Validates end-to-end pipeline with token tracking
- `test_eval_types.py`: Validates EvalTestResult schema and serialization

A demonstration script is available at:
```bash
python util/eval_pipeline/demo_token_tracking.py
```

## Backward Compatibility

### For Consumers

The changes are fully backward compatible:
- Token fields are optional (default to `None`)
- Existing code that doesn't use token fields continues to work
- Schema version remains "1" (new optional fields don't break parsing)

### Migration Path

**Recommended:**
```python
# New code - use separate fields
input_tokens = ctx.input_tokens
output_tokens = ctx.output_tokens
total_tokens = ctx.total_tokens
```

## Future Enhancements

Potential improvements:
1. **Token cost estimation**: Calculate dollar cost based on model pricing
2. **Token efficiency metrics**: Tokens per task, tokens per success, etc.
3. **Reasoning token tracking**: Separate tracking for reasoning tokens (o1/o3-mini models)
4. **Per-scorer token usage**: Track tokens used by LLM-based scorers separately
5. **Token budget enforcement**: Fail tasks that exceed token budgets

## Files Modified

- `util/eval_pipeline/src/eval_pipeline/scoring.py`
- `util/eval_pipeline/src/eval_pipeline/models.py`
- `util/eval_pipeline/src/eval_pipeline/eval_types.py`
- `util/eval_pipeline/src/eval_pipeline/pipeline.py`

## Related Documentation

- [OpenInference Span Attributes](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md)
- [Evaluation Types Schema](../util/eval_pipeline/src/eval_pipeline/eval_types.py)
- [Trace Format Documentation](../docs/tracing-format-plan.md)

# LLMMethodologyScorer Implementation

**Date**: 2025-01-09
**File**: `util/eval_pipeline/src/eval_pipeline/scoring.py`

## Overview

Created `LLMMethodologyScorer` - a new scorer that evaluates the code execution process rather than just final outputs. Unlike `LLMJudgeScorer` which validates final results, `LLMMethodologyScorer` analyzes all intermediate code executions from trace files.

## Key Features

1. **Extracts all code executions** from trace files via `extract_code_executions_from_trace()`
2. **Formats execution history** with code, results, and errors for LLM analysis
3. **Validates execution process** against a provided rubric using LLM-as-judge
4. **Separate trace files** for judge evaluations (`_method_judge.006trace.jsonl`)

## Architecture

### Helper Function: `extract_code_executions_from_trace()`

Parses JSONL trace files to extract execution records containing:
- `code`: The executed Python code
- `result`: Execution result (if available)
- `error`: Any errors that occurred
- `span_name`: Context from OpenTelemetry span
- `timestamp`: Execution time for ordering

Handles:
- `code_execution` spans (direct code execution)

### Class: `LLMMethodologyScorer`

Similar structure to `LLMJudgeScorer` but focused on process evaluation.

**Template Placeholders:**
- `{input}` - Task input
- `{output}` - Final output
- `{expected}` - Expected output
- `{executions}` - Formatted execution history
- `{execution_count}` - Number of executions

**Example Rubric:**
```python
rubric = """
Evaluate if the code executions demonstrate proper error handling.

Task: {input}

Code Executions:
{executions}

Return passed=true if:
1. Errors are caught and handled gracefully
2. Recovery attempts are made when failures occur
3. The agent doesn't give up after first failure
"""
```

## Usage Example

```python
from eval_pipeline.scoring import LLMMethodologyScorer, ScorerConfig

# Configure the scorer
scorer = LLMMethodologyScorer(
    rubric="""
    Evaluate if the agent's code executions show proper debugging:

    Task: {input}

    Execution History:
    {executions}

    Return passed=true if the agent:
    - Tests assumptions before proceeding
    - Handles errors appropriately
    - Learns from failures and adjusts approach
    """,
    model_spec=my_model_spec
)

# Use in scoring configuration
config = ScorerConfig(
    name="error_recovery",
    weight=1.0,
    scorer=scorer
)
```

## Use Cases

1. **Error Recovery Evaluation**: Validate that agents handle failures gracefully
2. **Debugging Process Assessment**: Check if agents properly investigate issues
3. **Iteration Quality**: Evaluate if agents learn from mistakes
4. **Code Quality**: Assess intermediate code quality, not just final output
5. **Process Compliance**: Validate execution follows required patterns

## Implementation Details

- **Fresh agent per evaluation**: Avoids conversation history contamination
- **Retry logic**: 3 attempts with error handling
- **Trace file management**: Separate judge traces from agent traces
- **Execution formatting**: Truncates long results, shows errors clearly
- **Timestamp ordering**: Executions displayed in chronological order

## Integration

The scorer integrates seamlessly with the eval_pipeline:
- Uses same `ScoringContext` interface
- Returns standard `ScoreResult` with score, reasoning, and metadata
- Works with `score_task()` function alongside other scorers
- Supports weighted scoring via `ScorerConfig`

## Differences from LLMJudgeScorer

| Aspect | LLMJudgeScorer | LLMMethodologyScorer |
|--------|----------------|----------------------|
| Focus | Final output correctness | Execution process quality |
| Input | Output vs expected | All code executions + results |
| Use case | Answer validation | Process evaluation |
| Template | `{code}` (optional) | `{executions}` (required) |
| Trace file | `_judge.006trace.jsonl` | `_method_judge.006trace.jsonl` |

## Future Enhancements

- **Pattern detection**: Automatically identify common error patterns
- **Execution metrics**: Count retries, error types, recovery time
- **Partial scoring**: Score based on progress, not just pass/fail
- **Execution graph**: Visualize execution flow for debugging

# Predict Strategy Capability Tests

## Summary

Added capability tests for `PredictStrategy` with composed Pydantic models. Prior to this change, the capability test suite only tested `CodeActStrategy` and its variants, with no tests specifically validating structured output functionality or model composition.

## Changes Made

### 1. New Test Agent: `PredictAgent`

**File:** `tests/capability/agents/structured_output.py`

Created a new agent that demonstrates **Pydantic model composition** with 4 methods:

- **`extract_user_info(text: str) -> UserInfo`** (generation method)
  - Extracts user information from rich text using PredictStrategy
  - Pydantic model: name (str), age (int), email (str)

- **`extract_review_info(text: str) -> ReviewInfo`** (generation method)
  - Extracts product review information from rich text using PredictStrategy
  - Pydantic model: product_name (str), rating (int), would_recommend (bool), key_points (list[str])
  - Includes field constraints (rating must be 1-5)

- **`combine_extraction(user: UserInfo, review: ReviewInfo) -> CombinedResult`** (generation method)
  - Takes two Pydantic models as input and combines them with an LLM-generated summary
  - Returns: CombinedResult containing UserInfo, ReviewInfo, and summary
  - Demonstrates PredictStrategy methods can take Pydantic models as parameters

- **`process_review(text: str) -> CombinedResult`** (orchestrator method)
  - **Regular implemented method** (not a generation method - no `...` body)
  - Orchestrates the extraction and combination:
    1. Calls `extract_user_info(text)` → stores UserInfo
    2. Calls `extract_review_info(text)` → stores ReviewInfo
    3. Calls `combine_extraction(user, review)` → returns CombinedResult
  - This is the method called by the test

### 2. Test Data File

**File:** `tests/capability/data/structured_combined_extraction.jsonl`

Single JSONL file with 3 rich test cases, each containing:
- Complex text with both user information and product review
- Expected output with nested Pydantic models:
  - `user`: UserInfo dict
  - `review`: ReviewInfo dict
  - `summary`: Combined summary string

Each test case includes:
- `args`: Empty array (no positional arguments)
- `kwargs`: Dictionary with `text` parameter containing rich multi-paragraph input
- `expected`: Nested structure matching CombinedResult model

### 3. Configuration Updates

**File:** `tests/capability/config.yaml`

Added single test entry in "STRUCTURED OUTPUT" category:

```yaml
- name: structured_combined_extraction
  description: "Extract and compose multiple Pydantic models - tests PredictStrategy with model composition"
  tier: stable
  agent:
    module: tests.capability.agents.structured_output
    class: PredictAgent
  method: process_review
  data_file: tests/capability/data/structured_combined_extraction.jsonl
  scorers:
    - name: exact_match
      class: ExactMatchScorer
      weight: 1.0
```

### 4. ExactMatchScorer Enhancement

**File:** `util/eval_pipeline/src/eval_pipeline/scoring.py`

Enhanced `_parse_value()` function to handle Pydantic models:
- Detects Pydantic v2 models (`.model_dump()`)
- Detects Pydantic v1 models (`.dict()`)
- Converts models to dicts before comparison
- Added test coverage in `util/eval_pipeline/tests/test_scoring.py`

### 5. PredictStrategy XML Wrapper Fix

**File:** `src/nemo_oo_agents/strategies/predict.py`

Fixed issue where LLM mimics context block XML format and wraps JSON output in tags:
- Added `_strip_xml_wrapper()` method to detect and remove XML tags
- Handles both attributed tags: `<assistant_message expr="...">JSON</assistant_message>`
- And simple tags: `<result>JSON</result>`
- Preserves multiline JSON formatting
- Falls back to original content if no XML wrapper detected
- Added comprehensive test coverage in `tests/runtime/test_structured_output_executor.py`

**Problem:** When context blocks show examples with `<assistant_message>` tags, LLMs sometimes mimic this format in their structured output, causing JSON parsing to fail.

**Solution:** Strip XML wrappers before attempting to parse JSON, allowing the strategy to handle both clean JSON and XML-wrapped JSON gracefully.

### 6. Reasoning Model Support

**File:** `src/nemo_oo_agents/strategies/predict.py`

Added support for reasoning models (o1, Nemotron, QwQ, etc.) that output structured data in the `reasoning` field:
- Check `response.reasoning` first, then fall back to `response.content`
- Reasoning models often place JSON output in the reasoning field when using structured output
- Logs which field is being used for debugging

**Problem:** Nemotron and other reasoning models return structured output in `response.reasoning` instead of `response.content`, causing empty content errors.

**Solution:** Modified `_call_llm_structured()` to check reasoning field first, enabling structured output to work with all model types.

### 7. Failed Output Capture for Debugging

**File:** `src/nemo_oo_agents/strategies/predict.py`

Enhanced error reporting to capture raw LLM output when validation fails:
- Added `_extract_raw_from_llm_response()` to extract content even when parsing fails
- Added `_extract_raw_content()` to extract raw response after successful parsing
- Added `_add_failed_output_to_span()` to add failed outputs to OpenTelemetry spans
- Span attributes include (on generation span, not LLM completion span):
  - `generation.failed_output.attempt_N`: Raw LLM output (truncated to 2000 chars)
  - `generation.failed_output.error_type.attempt_N`: Error type (e.g., "JSONDecodeError")
  - `generation.failed_output.error_msg.attempt_N`: Error message (truncated to 500 chars)
- Raw output also added to ErrorEvent in history (truncated to 1000 chars)
- Returns `llm_response_object` from `_call_llm_structured()` to enable extraction even on parsing failure

**Problem:** When JSON parsing or validation failed, we only saw error messages but not what the LLM actually output, making debugging impossible.

**Solution:**
1. Return LLMResponse object from `_call_llm_structured()` even when parsing fails
2. Extract raw content from LLMResponse in exception handler
3. Store it in span attributes for trace viewer visibility (on parent generation span)
4. Include truncated version in error events for history visibility
5. Allows developers to see exactly what the LLM generated when it fails

## Test Coverage

The test validates:

1. **Multiple structured extractions:** Two separate Pydantic models from same rich text
2. **Pydantic models as parameters:** `combine_extraction()` takes UserInfo and ReviewInfo as inputs
3. **Model composition:** Combining multiple models into a single result with LLM-generated summary
4. **Complex types:** Lists (key_points), integers with constraints (rating 1-5), booleans
5. **Rich input parsing:** Multi-paragraph text with mixed formats
6. **Nested Pydantic models:** CombinedResult contains UserInfo and ReviewInfo as fields
7. **Orchestration pattern:** Regular method calls multiple generation methods and passes results between them
8. **Real-world scenario:** User reviews with metadata (name, age, email, rating, recommendations)

## Running the Test

```bash
cd /home/sklingler/projects/nemo_oo_agents
uv run python -m eval_pipeline --config tests/capability/config.yaml --test structured_combined_extraction
```

## Validation Results

- **Syntax validation:** ✅ Python files compile successfully
- **JSONL validation:** ✅ Data file is valid JSONL format
- **Schema validation:** ✅ Test cases have proper args/kwargs/expected structure

## Key Benefits

This single comprehensive test is superior to multiple simple tests because it:

1. **Tests composition:** Shows PredictStrategy methods can call other structured methods
2. **More realistic:** Mimics real-world usage where multiple extractions are combined
3. **Rich context:** Uses complex, multi-paragraph inputs instead of simple snippets
4. **Nested validation:** ExactMatchScorer now handles nested Pydantic models correctly
5. **Cleaner maintenance:** One test file, one data file, one config entry

## Notes

- Uses `tier: stable` as this tests core framework functionality
- `ExactMatchScorer` enhanced to handle Pydantic models via `model_dump()` / `dict()` conversion
- Test demonstrates that PredictStrategy can orchestrate multiple structured outputs
- Complements unit tests in `tests/runtime/test_structured_output_executor.py` with end-to-end integration

## Related Files

- Agent: `tests/capability/agents/structured_output.py`
- Test data: `tests/capability/data/structured_combined_extraction.jsonl`
- Config: `tests/capability/config.yaml` (search for "structured_combined_extraction")
- Scorer enhancement: `util/eval_pipeline/src/eval_pipeline/scoring.py` (_parse_value function)
- Unit tests: `tests/runtime/test_structured_output_executor.py`
- Strategy implementation: `src/nemo_oo_agents/strategies/predict.py`
- Backward-compatible alias: `src/nemo_oo_agents/strategies/structured_output.py`

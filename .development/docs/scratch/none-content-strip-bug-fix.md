# Fix: Intermittent 'NoneType' object has no attribute 'strip' in check evaluation

**Date**: 2026-03-04

## Summary

Verified and fixed the bug where use-case checks (e.g. `summary_format_check`) intermittently failed with `'NoneType' object has no attribute 'strip'` when the LLM API returned `None` for `message.content` (e.g. tool-call-only or empty completion).

## Root cause (verified)

1. **unifiedllm** (`packages/unifiedllm/src/unifiedllm/unifiedllm.py`): In the tool_calls branch, `assistant_message["content"]` was set to `raw_response.choices[0].message.content` without a fallback. When the API returns `None`, that value was stored and could propagate to consumers.
2. **agent006** (`src/agent006/strategies/structured_output.py`): `_strip_xml_wrapper(content)` did `content.strip()` without handling `None`, so any code path that passed `None` (e.g. from `assistant_message["content"]` or future callers) raised `AttributeError`.

## Changes made

- **unifiedllm**: Added `or ""` when setting `assistant_message["content"]` in both sync and async tool_calls branches (lines 792 and 903).
- **agent006**: In `_strip_xml_wrapper`, use `(content or "").strip()` and type hint `content: str | None`.
- **Tests**:
  - `tests/runtime/test_structured_output_executor.py`: `TestStripXMLWrapper::test_strip_xml_handles_none_content` — asserts `_strip_xml_wrapper(None)` returns `""` and does not raise.
  - `packages/unifiedllm/tests/test_empty_content_retry.py`: `test_tool_calls_with_none_content_stores_empty_string_in_assistant_message` — asserts that when the API returns tool_calls with `message.content is None`, `response.assistant_message["content"] == ""` and `response.content == ""`.

## References

- Teammate bug report (coding agent analysis)
- Existing safe pattern in same file: `text_content = raw_response.choices[0].message.content or ""` at lines 765, 806, 874, 917

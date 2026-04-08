# Capability Stable Tier Drop: Trace Format Mismatch (Fixed)

**Date:** 2026-03-13
**Context:** After tracing refactor, stable tier pass rate dropped from ~95% to 70.8% in CI capability runs.

## Root cause

The **trace file format** written by the instrumentation no longer matched what the **eval_pipeline scoring** code expected.

- **File exporter (current):** `OtlpJsonFileExporter` writes **OTLP JSON** (TracesData) per line:
  `{"resourceSpans":[{"resource":{...},"scopeSpans":[{"spans":[...]}]}]}`
  Each line is a full OTLP envelope, not a single span.

- **Scoring code (before fix):** Assumed **one flat span per line**:
  `{"name": "code_execution", "attributes": {"code": "..."}, ...}`
  It did `span = json.loads(line)` then `span.get("name")`, `span.get("attributes")`.
  For OTLP lines there is no top-level `"name"`, so every line was effectively ignored.

**Effect:**
- `extract_code_from_trace()` always returned `None` for real CI trace files.
- `count_tokens_from_trace()` and `extract_code_executions_from_trace()` saw no spans.
- Any scorer that depends on code (LLMJudgeScorer with `skip_prefill`, CodeExecutionScorer, token counts) got empty or wrong data, so many stable-tier tests that rely on code-based evaluation failed.

## Fix

**Location:** `util/eval_pipeline/src/eval_pipeline/scoring.py`

1. **`_iter_spans_from_trace_file(trace_file)`**
   Yields flat span dicts (`name`, `attributes`, `start_time_unix_nano`) from a trace file.
   - **OTLP format:** If a line has `resourceSpans`, walk `resourceSpans` → `scopeSpans` → `spans`, convert each OTLP span (and attributes) to the flat shape.
   - **Legacy format:** If a line has no `resourceSpans`, treat it as one flat span (with optional conversion of list-format attributes).

2. **Use the iterator everywhere trace files are read:**
   - `extract_code_from_trace()`
   - `count_tokens_from_trace()`
   - `extract_code_executions_from_trace()`

Attribute conversion for OTLP reuses `_otlp_attrs_to_dict` from `eval_pipeline.otlp_io` (OTLP `[{key, value: {stringValue|intValue|...}}]` → flat `dict`).

## Verification

- On a real CI trace file (`results/ci/capability_.../traces/...jsonl`), `_iter_spans_from_trace_file` now yields dozens of spans and `extract_code_from_trace()` returns the executed code (e.g. 2345 chars for an OrderTestWrapper run).
- All 76 existing scoring tests pass (flat-span format still supported).
- New test: `TestExtractCodeFromTrace::test_extract_code_from_otlp_trace_file` ensures OTLP TracesData lines are parsed and code is extracted.

## Recommendation

Re-run the capability suite (e.g. same config as CI) and confirm stable tier returns to ~95%. No config or exporter change required; only the scoring reader was updated to accept OTLP trace file format.

## Follow-up: Legacy format removed

Trace file scoring now supports **only OTLP** (one TracesData per line). Legacy “one flat span per line” format was removed:

- **`write_eval_span_to_trace`** appends one OTLP TracesData line (same shape as the file exporter) so the trace file is all-OTLP.
- **`_iter_spans_from_trace_file`** only parses lines with `resourceSpans`; non-OTLP lines are skipped.
- Unit tests in `test_scoring.py` and `test_trace_eval_span.py` use OTLP fixtures (helper `_otlp_line` / `_read_first_span_from_otlp_file`).

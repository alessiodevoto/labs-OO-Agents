# Viewer: Trace Import

**Date:** 2026-03-19

## Problem

The viewer only ingests traces via the OTLP HTTP endpoint (`POST /v1/traces`). Some teams export traces to the filesystem instead and need a way to get them into the viewer. The Agentic Agent Development (AAD) team is one such client: they produce `.jsonl` trace files from Cursor and Claude Code session converters.

## Approach

We initially explored a UI-only approach with file/directory pickers in the browser. We decided instead to implement a CLI command, which is more versatile and easier to include in automation scripts. A UI import button can be added later if needed.

## Design

### CLI command

```
agent006 import-traces <file_or_directory> [--endpoint URL] [--batch-id ID]
```

- **file_or_directory**: a single `.jsonl` file or a directory (recursively finds all `.jsonl` files)
- **--endpoint**: the viewer API URL (default: `http://localhost:5001`)
- **--batch-id**: optional label for this import batch (default: auto-generated as `import_YYYYMMDD_HHMMSS_<hex6>`)

### batch_id

Each import gets a `batch_id` injected as a resource attribute on every span before posting. This enables:
- **Filtering**: the viewer's trace table shows `batch_id` as a filterable column, so users can view just the traces they imported
- **Cleanup**: `DELETE /api/traces?batch_id=X` removes all traces from a bad import in one call
- **Automation**: scripts can set `--batch-id` explicitly to group related imports

### How it works

1. Collect all `.jsonl` files from the path argument
2. For each file:
   - Derive `session.id` from the filename (basename without extension) if not already present in the resource attributes
   - Inject `batch_id` into resource attributes
   - POST each line to `{endpoint}/v1/traces`
3. Print summary and viewer URL with batch_id filter

Example output:
```
Importing 10 trace files...
  10 imported, 0 skipped
View at: http://localhost:5001/traces?batch_id=import_20260319_174500
```

### File format support

We support OTLP JSON format (lines with `{"resourceSpans": [...]}`). This is the format produced by the `OtlpJsonFileExporter` and by `write_eval_span_to_trace`.

Legacy snake_case format (older traces with flat `span_id`, `trace_id`, `name` keys) is not supported in this initial version. If users need to import legacy traces, a separate converter can be built later.

### Backend changes

The CLI reuses the existing `POST /v1/traces` endpoint for ingestion — no new ingest endpoints needed.

One new endpoint is added for cleanup: `DELETE /api/traces?batch_id=X` removes all traces from a specific import batch. This is a local-only, single-user dev tool with no authentication — the delete endpoint follows the same pattern as the existing `DELETE /api/traces/{session_id}`.

## Out of scope

- Legacy trace format conversion (can be added later)
- UI upload button (can be added later on top of the same backend)
- Metadata standardization across trace producers (separate problem)

# Design: Render Markdown Tables as Slack File Snippets

## Problem

Markdown tables emitted by agents (`| col | col |`) are posted as raw pipe-separated
text in Slack. Without monospace font, column alignment is unreadable.

## Solution

Intercept markdown tables in the bridge's text output path and upload them as Slack
file snippets (`files.getUploadURLExternal` + `files.completeUploadExternal`). Slack
renders these as collapsible inline cards with monospace font. Fall back to a
`tabulate`-formatted code block if the upload fails.

## Files Changed

- `util/slack-bridge/slack_formatter.py` — add `extract_tables()` and `format_table_as_code_block()`
- `util/slack-bridge/bridge.py` — add `upload_table_snippet()`, modify `stop_stream()`
- `util/slack-bridge/tests/test_slack_formatter.py` — new tests for extraction/formatting

## Implementation Details

### 1. `extract_tables(text)` in `slack_formatter.py`

Splits a text string into an ordered list of typed segments:
- `{"type": "text", "content": str}` — plain text
- `{"type": "table", "headers": list[str], "rows": list[list[str]], "raw": str}` — parsed table

**Detection algorithm** (line-by-line):
- A separator line matches `^\s*\|[-:|\s]+\|\s*$`
- When a separator is found and the preceding line starts with `|`, we have a table
- The line immediately before the separator is the header row
- Lines after the separator that start with `|` are data rows
- All other lines are text

**Cell parsing**: split on `|`, strip whitespace, drop leading/trailing empty strings.

### 2. `format_table_as_code_block(headers, rows)` in `slack_formatter.py`

Fallback formatter using `tabulate` with `rounded_outline` style, wrapped in triple backticks.
Falls back to raw wrap if `tabulate` is not installed.

### 3. `upload_table_snippet(content, channel, thread_ts, client, initial_comment="")` in `bridge.py`

Async function, returns `bool` (never raises):

1. `client.files_getUploadURLExternal(filename="table.txt", length=len(content.encode()))`
2. `httpx.AsyncClient().post(upload_url, content=content.encode())`
3. `client.files_completeUploadExternal(files=[{"id": file_id}], channel_id=channel, thread_ts=thread_ts, initial_comment=initial_comment)`

### 4. Wire-up in `stop_stream()` in `bridge.py`

In the `elif stream_failed and stream_text_acc:` branch, replace the single
`post_message()` call with segment-aware dispatch:
- text segments → `post_message`
- table segments → `upload_table_snippet` (fallback: `format_table_as_code_block` → `post_message`)

## Tests

1. `test_extract_tables_no_table`
2. `test_extract_tables_table_only`
3. `test_extract_tables_mixed`
4. `test_extract_tables_multiline_text_around_table`
5. `test_format_table_as_code_block`

## Edge Cases

- Table with no data rows: headers populated, rows=[]
- Pipe chars not in tables (e.g. shell commands): won't match without separator line
- Multiple tables: each gets its own upload
- Upload failure: falls back to code block silently

## Plan Updates (post review)

### Streaming path clarification
`stream_failed = True` is hardcoded (bridge always uses `post_message` for consistent
agent identity, as the comment says). The `stream.append()` path is never taken.
`stop_stream()`'s `elif stream_failed and stream_text_acc:` branch is always the
code path for text output. No separate handling needed.

### `_collapsible()` is not called on text events in bridge.py
`text` events hit `continue` before reaching `formatter.format(event)`, so
`_collapsible()` is never called on agent text. `stream_text_acc` is raw text.

### Separator regex — stricter
Use `^\s*\|[\s\-:|]+\|\s*$` AND require at least one `-` char in the line to
distinguish actual separators from pipe-only rows. Implementation: check
`re.match(r'^\s*\|[-:|\s]+\|\s*$', line) and '-' in line`.

### Additional tests
- `test_upload_table_snippet_success` — mock client, verifies 3-step API call sequence
- `test_upload_table_snippet_failure` — mock client raises, returns False without raising

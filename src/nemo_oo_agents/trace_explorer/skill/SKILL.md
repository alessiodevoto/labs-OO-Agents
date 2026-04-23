---
name: trace-explorer
description: >-
  Explore and debug agent execution traces. Use when the user asks to analyze
  a trace, debug an agent run, investigate errors in a trace, or when they
  paste a trace-explorer prompt from the viewer UI.
---

# Trace Explorer

Explore agent execution traces using the `trace-explorer` CLI. Traces can be loaded from local JSONL files or from a running viewer API.

## Setup

Run commands with `uv run` so the correct environment is used automatically:

```bash
uv run trace-explorer --help
```

If `trace-explorer` is already on your PATH (e.g. installed as a package), omit `uv run`.

## Exploration Strategy

Use progressive disclosure — start broad, then drill into specifics:

1. **Overview first** — understand the call graph, session count, pass/fail status
2. **Errors** — check for failures and error patterns
3. **Drill into sessions** — inspect specific agent sessions
4. **Drill into turns** — look at individual LLM calls and code executions
5. **Search** — find patterns across the entire trace

## Commands

### From a local file
```bash
trace-explorer trace.jsonl                         # overview
trace-explorer trace.jsonl --errors                # all errors
trace-explorer trace.jsonl -s <session_id>         # session detail
trace-explorer trace.jsonl -s <session_id> -t <N>  # specific turn
trace-explorer trace.jsonl --search "pattern"      # search
trace-explorer trace.jsonl --first-error           # jump to first error
trace-explorer trace.jsonl --timeline              # chronological timeline
trace-explorer trace.jsonl --json                  # structured JSON output
```

### From the viewer API
```bash
trace-explorer --viewer <URL> --session-id <ID>                  # overview
trace-explorer --viewer <URL> --session-id <ID> --errors         # all errors
trace-explorer --viewer <URL> --session-id <ID> -s <SID>         # session detail
trace-explorer --viewer <URL> --session-id <ID> -s <SID> -t <N>  # specific turn
trace-explorer --viewer <URL> --session-id <ID> --span-id <ID>   # jump to span
trace-explorer --viewer <URL> --session-id <ID> --search "pat"   # search
```

### Experiment-level analysis
```bash
trace-explorer --viewer <URL> --experiment <ID>                    # summary with pass/fail rates
trace-explorer --viewer <URL> --experiment <ID> --errors           # Python exceptions across all failed sessions
trace-explorer --viewer <URL> --experiment <ID> --failures         # eval failure reasons (wrong answer, wrong schema)
trace-explorer --viewer <URL> --experiment <ID> --search "pattern" # search across all sessions
trace-explorer --viewer <URL> --experiment <ID> --json             # structured JSON with all session IDs
```

Use `--errors` for crash/exception failures, `--failures` for wrong-answer failures that don't throw exceptions.

For comprehensive per-session analysis beyond error aggregation, fetch the full list and drill into each:
```bash
# Step 1: get all session IDs
trace-explorer --viewer <URL> --experiment <ID> --json

# Step 2: for each session_id in the output, load its trace
trace-explorer --viewer <URL> --session-id <session_id>
trace-explorer --viewer <URL> --session-id <session_id> --errors
```

Use `--json` output in step 1 to get a machine-readable list of all tests (passed and failed)
with their `session_id` fields, then iterate to summarize patterns across the experiment.

## Tips

- Session IDs can be abbreviated to 6 characters (e.g., `e15ed8` instead of the full ID)
- Use `--json` for structured output when you need to process the data programmatically
- Use `-v` (verbose) to see full details instead of concise summaries
- Use `-q` (quiet) to suppress parser warnings
- The overview output includes navigation hints showing what to explore next

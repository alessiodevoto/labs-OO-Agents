# Issue 168 — Flush in-flight journal POSTs on JournalExporter shutdown

Source: https://gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents/-/issues/168

## Problem

Short-lived processes (e.g. an LLM-generated `main.py` invoked once per
sample inside `sentiment_accuracy` evals) exit before the journal
callback's daemon-thread POSTs to `/v1/journal/calls` and
`/v1/journal/blocks` complete. Result: spans arrive at the viewer with
no message content because the call/block records never landed.

## Root cause (after re-investigation)

The OTel SDK's `TracerProvider.__init__` already registers an atexit
hook that fans out to every span processor's `shutdown()`, which in turn
calls the exporter's `shutdown()`. So OTel handles the
`BatchSpanProcessor.drain` half automatically — no work needed there.

What's broken is one specific spot: `JournalExporter.shutdown()` in
`src/nemo_oo_agents/tracing/_journal_exporter.py`. The method removes
the destination from the shared litellm callback and calls
`self._span_exporter.shutdown()`, but it never calls `flush_pending()`,
which is the only thing that joins the daemon `_post_json` worker
threads. `force_flush` does call it; `shutdown` does not.

So the bug is:

`atexit` (registered by OTel) → `TracerProvider.shutdown` →
`BatchSpanProcessor.shutdown` → `JournalExporter.shutdown` →
**(missing flush)** → returns → process exits → daemon threads die
mid-POST.

## Fix

One line in `JournalExporter.shutdown()` — call `flush_pending(timeout=30.0)`
before the destination cleanup and span-exporter shutdown.

## Scope

| File | Change |
|------|--------|
| `src/nemo_oo_agents/tracing/_journal_exporter.py` | Call `flush_pending(timeout=30.0)` at the top of `shutdown()`. |
| `tests/tracing/test_journal_exporter_shutdown_flushes.py` | New regression test: dispatch a slow journal POST, call `JournalExporter.shutdown()` from a side thread, assert it does not return until the POST completes. |

## What we explicitly did *not* do

- **Did not register a new atexit hook in `enable_tracing`.** OTel already
  does this; adding our own would have been redundant with OTel's own
  `TracerProvider.shutdown` registration (verified by tracing
  `atexit.register` calls during a test run).
- **Did not add a `NEMO_OO_OTLP_EXPORTER` env var.** Users who want the
  full-message OTLP path can already pass
  `enable_tracing(exporters=[exporters.local_otlp()])` explicitly. A
  new env var would just duplicate that knob.
- **Did not change `force_flush`.** It already calls `flush_pending`;
  the fix only makes `shutdown` consistent with it.

## Acceptance criteria

1. `JournalExporter.shutdown()` joins the journal callback's
   daemon-thread POSTs before returning.
2. New regression test passes; existing tests continue to pass.
3. Lint is clean.

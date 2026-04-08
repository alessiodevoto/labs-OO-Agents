"""JournalExporter: spans (sans messages) + content-addressed message sideband.

Combines two responsibilities into a single exporter:

1. Exports spans to the local viewer with ``llm.input_messages.*`` /
   ``llm.output_messages.*`` attributes **stripped** — the viewer reconstructs
   full messages from the journal instead.
2. Installs a litellm ``CustomLogger`` callback that intercepts message lists
   *before* each LLM call and posts only new (delta) messages to the viewer's
   journal endpoints, reducing per-call storage from O(N) to O(delta).

Usage::

    enable_tracing(exporters=[exporters.journal()])
"""

from __future__ import annotations

from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class JournalExporter(SpanExporter):
    """SpanExporter: stripped OTLP spans + content-addressed message journal.

    Delegates span export to an internal :class:`OtlpJsonHttpExporter` with
    ``strip_llm_messages=True`` so message attributes are omitted from the
    wire payload.  The litellm callback handles message delivery separately.
    """

    def __init__(self, base_url: str) -> None:
        from openinference_instrumentation_agent006._litellm_journal import (
            MessageJournalCallback,
        )
        from openinference_instrumentation_agent006._otlp_http_exporter import (
            OtlpJsonHttpExporter,
        )

        self._base_url = base_url.rstrip("/")
        self._callback = MessageJournalCallback(self._base_url)
        self._span_exporter = OtlpJsonHttpExporter(
            endpoint=f"{self._base_url}/v1/traces",
            strip_llm_messages=True,
        )
        self._install()

    def _install(self) -> None:
        """Register the callback in litellm.callbacks (idempotent)."""
        try:
            import litellm

            from openinference_instrumentation_agent006._litellm_journal import (
                MessageJournalCallback,
            )

            already = any(
                isinstance(cb, MessageJournalCallback) and cb._base_url == self._base_url for cb in litellm.callbacks
            )
            if not already:
                litellm.callbacks.append(self._callback)
        except ImportError:
            pass  # litellm not installed; journal callback is a no-op

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export spans with LLM message attributes stripped."""
        return self._span_exporter.export(spans)

    def shutdown(self) -> None:
        """Remove the litellm callback and shut down the span exporter."""
        try:
            import litellm

            litellm.callbacks = [cb for cb in litellm.callbacks if cb is not self._callback]
        except (ImportError, AttributeError):
            pass
        self._span_exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._span_exporter.force_flush(timeout_millis)

    def describe(self) -> str:
        return f"journal:{self._base_url}"

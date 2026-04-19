# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lightweight OTLP JSON HTTP span exporter.

Sends spans to an OTLP HTTP endpoint as JSON (not protobuf),
matching the ExportTraceServiceRequest schema.
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from openinference_instrumentation_nemo_oo_agents._otlp_serialize import build_resource_spans

log = logging.getLogger(__name__)


class OtlpJsonHttpExporter(SpanExporter):
    """Exports spans as OTLP JSON over HTTP POST.

    Used with ``BatchSpanProcessor`` so exports are batched and non-blocking.
    Tracks dropped spans and prints a loud summary on ``shutdown()`` so
    trace loss is never silent.
    """

    # Attribute key prefixes for LLM messages (OpenInference convention).
    _LLM_MESSAGE_PREFIXES = ("llm.input_messages.", "llm.output_messages.")

    def __init__(
        self,
        endpoint: str = "http://localhost:5001/v1/traces",
        strip_llm_messages: bool = False,
    ):
        self._endpoint = endpoint
        self._strip_llm_messages = strip_llm_messages
        self._dropped_spans = 0
        self._failed_batches = 0
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS

        # Group spans by session.id before sending. BatchSpanProcessor may batch
        # spans from multiple concurrent sessions into one export() call. Sending
        # all spans under the first session's id would mis-route the rest.
        #
        # Do NOT use get_session() here — export() runs in the BatchSpanProcessor
        # worker thread whose ContextVar context is a frozen copy from thread-start
        # time, *before* set_session(session_id) is called in eval tasks.
        # session.id is stamped per-span by SessionSpanProcessor in the event loop
        # thread, so read it from span.attributes instead.
        from collections import defaultdict
        by_session: dict[str | None, list] = defaultdict(list)
        for span in spans:
            sid = str(span.attributes["session.id"]) if span.attributes and span.attributes.get("session.id") else None
            by_session[sid].append(span)

        exclude = self._LLM_MESSAGE_PREFIXES if self._strip_llm_messages else ()
        overall = SpanExportResult.SUCCESS
        for session_id, session_spans in by_session.items():
            override = {"session.id": session_id} if session_id else None
            resource_spans = build_resource_spans(session_spans, resource_attrs_override=override, exclude_attr_prefixes=exclude)
            result = self._send_payload(session_spans, {"resourceSpans": resource_spans})
            if result != SpanExportResult.SUCCESS:
                overall = result
        return overall

    def _send_payload(self, spans: Sequence[ReadableSpan], payload: dict) -> SpanExportResult:
        try:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            req = urllib.request.Request(
                self._endpoint,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status < 300:
                    return SpanExportResult.SUCCESS
                self._record_failure(len(spans), f"HTTP {resp.status}")
                return SpanExportResult.FAILURE
        except urllib.error.HTTPError as e:
            self._record_failure(
                len(spans),
                f"{e} — ensure the viewer is running (`nemo oo start-dev`) and nothing else is bound to this port.",
            )
            return SpanExportResult.FAILURE
        except Exception as e:
            self._record_failure(len(spans), str(e))
            return SpanExportResult.FAILURE

    def _record_failure(self, span_count: int, reason: str) -> None:
        with self._lock:
            self._dropped_spans += span_count
            self._failed_batches += 1
        log.error(
            "DROP: failed to export %d span(s) to %s: %s",
            span_count,
            self._endpoint,
            reason,
        )

    def describe(self) -> str:
        """Human-readable description of this exporter."""
        return f"otlp:{self._endpoint}"

    def shutdown(self) -> None:
        with self._lock:
            dropped = self._dropped_spans
            batches = self._failed_batches
        if dropped:
            log.warning(
                "%d span(s) across %d failed batch(es) were NOT delivered to %s. "
                "Traces may be incomplete. "
                "Consider --no-live-trace (JSONL export) to avoid this under parallel load.",
                dropped,
                batches,
                self._endpoint,
            )

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

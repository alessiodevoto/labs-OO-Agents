# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lightweight SpanProcessor that stamps session.id on every span.

Must be added to TracerProvider **before** export processors so the attribute
is present when exporters see the span.

Reads the session id from the OpenTelemetry context — the same source
:mod:`openinference_instrumentation_nemo_oo_agents._session` writes
via :func:`set_session`. Needed for spans created by the plain SDK
tracer (framework hooks) because those don't go through
OpenInference's ``OITracer`` and therefore don't auto-receive the
attribute from ``get_attributes_from_context()`` at creation.
"""

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from nemo_oo_agents.tracing._session import get_session


class SessionSpanProcessor(SpanProcessor):
    """Stamps ``session.id`` on every span at ``on_start``."""

    def on_start(self, span, parent_context: Context | None = None) -> None:
        session_id = get_session()
        if session_id:
            span.set_attribute("session.id", session_id)

    def on_end(self, span: ReadableSpan) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

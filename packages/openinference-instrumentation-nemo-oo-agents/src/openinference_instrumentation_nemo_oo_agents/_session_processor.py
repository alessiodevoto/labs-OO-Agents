# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lightweight SpanProcessor that stamps session.id on every span.

Must be added to TracerProvider **before** export processors so the attribute
is present when exporters see the span.
"""

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from openinference_instrumentation_nemo_oo_agents._session import _current_session


class SessionSpanProcessor(SpanProcessor):
    """Reads the session ContextVar at span-start and sets ``session.id``."""

    def on_start(self, span, parent_context: Context | None = None) -> None:
        session_id = _current_session.get(None)
        if session_id:
            span.set_attribute("session.id", session_id)

    def on_end(self, span: ReadableSpan) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

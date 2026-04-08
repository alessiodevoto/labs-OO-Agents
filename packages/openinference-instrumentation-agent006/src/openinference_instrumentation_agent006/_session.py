"""ContextVar-based session management for exporter-agnostic session routing.

All exporters see the same session.id via the SessionSpanProcessor, which reads
the ContextVar and stamps it on every span as a span attribute.
"""

from contextvars import ContextVar

_current_session: ContextVar[str | None] = ContextVar("agent006_session", default=None)


def set_session(session_id: str | None) -> None:
    """Set the session ID for the current async context.

    All spans created in this context will carry ``session.id`` as a span attribute.
    Every exporter sees the correct value:

    - **JSONL exporter**: routes spans to ``{trace_dir}/{session_id}.jsonl``
    - **OTLP exporters**: ``session.id`` is a span attribute -- collectors group correctly
    - **Console exporter**: prints ``session.id`` in the span output
    """
    _current_session.set(session_id)


def get_session() -> str | None:
    """Get the current session ID for this async context."""
    return _current_session.get()

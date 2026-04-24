# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lightweight SpanProcessor that stamps session.id on every span.

Must be added to TracerProvider **before** export processors so the attribute
is present when exporters see the span.

Reads the session id from the OpenTelemetry context — the same source
:mod:`nemo_oo_agents.tracing._session` writes
via :func:`set_session`. Needed for spans created by the plain SDK
tracer (framework hooks) because those don't go through
OpenInference's ``OITracer`` and therefore don't auto-receive the
attribute from ``get_attributes_from_context()`` at creation.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

from openinference.semconv.trace import SpanAttributes
from opentelemetry import context as otel_context
from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from nemo_oo_agents.tracing._session import get_session

# Diagnostic log path. Env override so tests/CI can redirect, off via
# NEMO_SESSION_DEBUG_LOG= (empty) or `none`. Default is on — we want the log
# to fill the moment the user hits the bug in the TUI.
_DEBUG_LOG_ENV = "NEMO_SESSION_DEBUG_LOG"
_DEFAULT_DEBUG_LOG = "/tmp/nemo_oo_agents_session_debug.log"


def _debug_log_path() -> Path | None:
    raw = os.environ.get(_DEBUG_LOG_ENV, _DEFAULT_DEBUG_LOG)
    if not raw or raw.lower() == "none":
        return None
    return Path(raw)


_DEBUG_LOCK = threading.Lock()

# Span names we log on every on_start (not only when session is missing).
# Useful for isolating "processor not attached to this span's provider"
# vs "on_start fires but session is stripped downstream" — if an orphan
# span's trace_id is absent from the log, the processor never saw it.
_ALWAYS_LOG_SPAN_NAMES = frozenset({"acompletion"})


def _append_debug(line: str) -> None:
    """Best-effort append a line to the session-debug log.

    Any I/O failure is silently swallowed — this is diagnostic code, it
    must not disrupt tracing or span creation even if the filesystem is
    unhappy (which is how the original diag log got quarantined).
    """
    p = _debug_log_path()
    if p is None:
        return
    try:
        with _DEBUG_LOCK:
            with p.open("a", encoding="utf-8") as f:
                f.write(line)
                if not line.endswith("\n"):
                    f.write("\n")
    except Exception:
        pass


def _current_task_label() -> str:
    """Return an identifier for the asyncio task the span was created in.

    Returns ``thread:<name>`` when called outside any running event loop
    (e.g. the BatchSpanProcessor export thread, though on_start shouldn't
    fire there). Inside a task, returns ``task:<name>:<id>``.
    """
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return f"thread:{threading.current_thread().name}"
    if task is None:
        return f"thread:{threading.current_thread().name}"
    return f"task:{task.get_name()}:{id(task):x}"


def _span_session_attr(span) -> tuple[bool, str | None]:
    """Read ``session.id`` directly off the span's attribute dict.

    Returns ``(present, value)``. ``present`` distinguishes the three
    cases that matter for diagnosis:

    - ``(False, None)``  — attribute not set at all (no start-span path
      stamped it). This is the normal case for framework spans before
      on_start runs.
    - ``(True, "")``     — attribute is explicitly the empty string.
      This is the smoking gun for the OITracer path reading an empty
      string out of the OTel context via ``get_attributes_from_context``.
    - ``(True, "real")`` — already correctly stamped (OITracer path
      with a non-empty ``session.id`` in context, or a prior processor).
    """
    attrs = getattr(span, "attributes", None) or {}
    if SpanAttributes.SESSION_ID not in attrs:
        return False, None
    return True, str(attrs.get(SpanAttributes.SESSION_ID))


class SessionSpanProcessor(SpanProcessor):
    """Stamps ``session.id`` on every span at ``on_start``.

    Also emits a diagnostic line to the debug log for *any* span whose
    ``session.id`` is missing at ``on_start`` time. This is how we hunt
    down the TUI's "unknown_*" orphan spans — the log captures task
    identity, OTel context value, and existing span attrs so we can
    distinguish:

    - OITracer stamped ``session.id=""`` from an empty-string context.
    - OITracer left ``session.id`` unset because context had no value.
    - on_start ran in a task whose context differs from the calling
      task's (e.g. a detached/fire-and-forget task that snapshotted
      the context *before* ``set_session()`` ran).

    Disable the log with ``NEMO_SESSION_DEBUG_LOG=none``.
    """

    # Class-level so we fire the "processor loaded" marker exactly once
    # per process, regardless of how many provider re-configurations
    # happen.
    _alive_marker_written: bool = False
    _alive_marker_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._ensure_alive_marker()

    def _ensure_alive_marker(self) -> None:
        """Write a one-shot line proving this processor is loaded.

        Lets the user see at a glance whether the live TUI is running
        our patched processor (marker present) or a stale install
        (log file absent / empty).
        """
        if SessionSpanProcessor._alive_marker_written:
            return
        with SessionSpanProcessor._alive_marker_lock:
            if SessionSpanProcessor._alive_marker_written:
                return
            SessionSpanProcessor._alive_marker_written = True
        _append_debug(
            f"ts={time.time():.3f} event=processor_loaded pid={os.getpid()} module={__name__}"
        )

    def on_start(self, span, parent_context: Context | None = None) -> None:
        session_id = get_session()
        name = getattr(span, "name", "") or "?"

        if session_id:
            span.set_attribute(SpanAttributes.SESSION_ID, session_id)

        # --- Diagnostic: stamp state directly onto the span ------------
        # Goes onto *every* acompletion span and every missing-session
        # span, regardless of where it ends up attributed. Because the
        # attributes ride with the span through the OTLP exporter to the
        # viewer, we can read them back via ``/api/trace?session_id=…``
        # without needing access to the process's file system. If an
        # orphan span lacks these attributes entirely, its TracerProvider
        # does not have this processor attached — that tells us the bug
        # is LiteLLMInstrumentor pointing at a different provider, not a
        # race on the session ContextVar.
        should_stamp = (not session_id) or (name in _ALWAYS_LOG_SPAN_NAMES)
        if should_stamp:
            present, value = _span_session_attr(span)
            raw_ctx_value = otel_context.get_value(SpanAttributes.SESSION_ID)
            span.set_attribute("nemo_debug.on_start_fired", True)
            span.set_attribute("nemo_debug.on_start_pid", os.getpid())
            span.set_attribute(
                "nemo_debug.on_start_get_session",
                "" if session_id is None else session_id,
            )
            span.set_attribute(
                "nemo_debug.on_start_ctx_session_id_raw",
                repr(raw_ctx_value),
            )
            span.set_attribute(
                "nemo_debug.on_start_span_attr_present",
                present,
            )
            span.set_attribute(
                "nemo_debug.on_start_span_attr_value",
                "" if value is None else value,
            )
            span.set_attribute(
                "nemo_debug.on_start_task_label",
                _current_task_label(),
            )

        # Also mirror to the file log as a backup so the user can tail
        # it if they prefer.
        if not should_stamp:
            return
        try:
            ctx = span.get_span_context()
            trace_id = f"{ctx.trace_id:032x}" if ctx else "?"
            span_id = f"{ctx.span_id:016x}" if ctx else "?"
        except Exception:
            trace_id = span_id = "?"
        _append_debug(
            " ".join(
                (
                    f"ts={time.time():.3f}",
                    f"pid={os.getpid()}",
                    f"name={name}",
                    f"trace={trace_id}",
                    f"span={span_id}",
                    f"{_current_task_label()}",
                    f"get_session={session_id!r}",
                    f"ctx_session_id={otel_context.get_value(SpanAttributes.SESSION_ID)!r}",
                )
            )
        )

    def on_end(self, span: ReadableSpan) -> None:
        """Log the final attribute state for ``acompletion`` spans.

        We can't mutate a ReadableSpan, but we can *read* its final
        attribute map and write to the debug log. Together with the
        on_start log line, this bookends the span's lifetime: if
        ``session.id`` is present at on_start and absent here, the strip
        happened during the span's body (most likely in
        ``OITracer.start_as_current_span``'s post-on_start
        ``openinference_span.set_attributes(context_attributes)`` pass
        when the context changed). If it's present at on_end but absent
        in the exporter payload, the bug is downstream of the span
        processors entirely.
        """
        name = getattr(span, "name", "") or "?"
        if name not in _ALWAYS_LOG_SPAN_NAMES:
            return
        attrs = span.attributes or {}
        sid = attrs.get(SpanAttributes.SESSION_ID)
        try:
            ctx = span.get_span_context()
            trace_id = f"{ctx.trace_id:032x}" if ctx else "?"
            span_id = f"{ctx.span_id:016x}" if ctx else "?"
        except Exception:
            trace_id = span_id = "?"
        _append_debug(
            " ".join(
                (
                    f"ts={time.time():.3f}",
                    f"pid={os.getpid()}",
                    "event=on_end",
                    f"name={name}",
                    f"trace={trace_id}",
                    f"span={span_id}",
                    f"session_id_on_span={sid!r}",
                    f"attr_count={len(attrs)}",
                    # The debug stamps we added at on_start — if the
                    # exported span is missing these too (verified via
                    # viewer API), that means the span we see *here* at
                    # on_end is still carrying them while export drops
                    # them.
                    f"nemo_debug_on_start_get_session="
                    f"{attrs.get('nemo_debug.on_start_get_session')!r}",
                    f"nemo_debug_on_start_ctx_session_id_raw="
                    f"{attrs.get('nemo_debug.on_start_ctx_session_id_raw')!r}",
                )
            )
        )

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

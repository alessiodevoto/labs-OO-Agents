"""LiteLLM CustomLogger that maintains a content-addressed message journal.

Intercepts the message list *before* each LLM call and posts only messages
not yet seen in this session to ``POST /v1/journal/messages``.  After each
successful call, posts a call record referencing all messages by hash to
``POST /v1/journal/calls``.

This reduces per-call data transmission from O(N) to O(delta) — in a
100-turn agentic loop only the 1–3 new messages per turn are transmitted,
not the full accumulated context window.

Usage (handled automatically by ``enable_tracing()``)::

    import litellm
    from openinference_instrumentation_nemo_oo_agents._litellm_journal import (
        MessageJournalCallback,
    )
    litellm.callbacks.append(MessageJournalCallback("http://localhost:5001"))
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from litellm.integrations.custom_logger import CustomLogger
from opentelemetry import trace as otel_trace

from openinference_instrumentation_nemo_oo_agents._session import get_session

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_msg(msg: dict) -> str:
    """Content-address a message dict.

    Uses canonical JSON (sorted keys, no whitespace) so the hash is stable
    regardless of dict insertion order.  Returns ``"sha256:<hex>"``.
    """
    canonical = json.dumps(msg, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _msg_to_dict(msg: Any) -> dict:
    """Normalise a litellm message to a plain JSON-serialisable dict."""
    if msg is None:
        return {}
    if isinstance(msg, dict):
        return msg
    if hasattr(msg, "model_dump"):
        return msg.model_dump(exclude_unset=True)
    try:
        return dict(msg)
    except TypeError:
        return {"raw": str(msg)}


def _extract_output_msgs(response_obj: Any) -> list[dict]:
    """Pull assistant messages out of a litellm completion response."""
    msgs = []
    try:
        for choice in response_obj.choices:
            msgs.append(_msg_to_dict(choice.message))
    except Exception as exc:
        log.debug("Failed to extract output messages: %s", exc)
    return msgs


_LARGE_PAYLOAD_BYTES = 512 * 1024  # 512 KB — warn when payloads get this big


_POST_RETRIES = 3
_POST_RETRY_DELAYS = (1.0, 3.0, 5.0)


def _post_json(url: str, payload: Any, *, session_id: str = "", timeout: float = 15.0) -> None:
    """Fire-and-forget JSON POST with retries — dispatched to a daemon thread."""

    def _send() -> None:
        tag = f" [session={session_id}]" if session_id else ""
        n_items = len(payload) if isinstance(payload, list) else 1
        size_kb = 0.0
        try:
            data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        except (TypeError, ValueError) as exc:
            log.warning("POST %s: JSON serialization failed: %s%s", url, exc, tag)
            return
        size_kb = len(data) / 1024
        if len(data) > _LARGE_PAYLOAD_BYTES:
            log.warning(
                "POST %s: large payload %.0f KB (%d item(s))%s",
                url,
                size_kb,
                n_items,
                tag,
            )
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.monotonic()
        for attempt in range(_POST_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    if resp.status < 300:
                        if elapsed_ms > 2000:
                            log.info(
                                "POST %s OK but slow: %.0fms (attempt %d)%s",
                                url,
                                elapsed_ms,
                                attempt + 1,
                                tag,
                            )
                        return
                    log.debug("POST %s returned HTTP %s%s", url, resp.status, tag)
            except Exception as exc:
                elapsed_ms = (time.monotonic() - t0) * 1000
                if attempt < _POST_RETRIES - 1:
                    log.info(
                        "POST %s attempt %d/%d failed after %.0fms: %s%s — retrying in %.0fs",
                        url,
                        attempt + 1,
                        _POST_RETRIES,
                        elapsed_ms,
                        exc,
                        tag,
                        _POST_RETRY_DELAYS[attempt],
                    )
                    time.sleep(_POST_RETRY_DELAYS[attempt])
                else:
                    log.warning(
                        "POST %s failed after %d attempts (%.0fms total): %s (%.0f KB, %d item(s))%s",
                        url,
                        _POST_RETRIES,
                        elapsed_ms,
                        exc,
                        size_kb,
                        n_items,
                        tag,
                    )

    threading.Thread(target=_send, daemon=True).start()


def _to_ts(t: Any) -> float:
    """Convert a datetime or numeric value to a Unix timestamp float."""
    if hasattr(t, "timestamp"):
        return t.timestamp()
    try:
        return float(t)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------


class MessageJournalCallback(CustomLogger):
    """LiteLLM callback that streams messages to a content-addressed journal.

    Maintains a per-session in-memory set of already-sent hashes so that
    each unique message is transmitted at most once per session.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        base = base_url.rstrip("/")
        self._messages_url = base + "/v1/journal/messages"
        self._calls_url = base + "/v1/journal/calls"
        self._base_url = base
        # session_id -> set of hashes already posted.
        # NOTE: grows unboundedly in long-running servers with many sessions.
        # Each session holds O(unique_messages) hash strings (~80 bytes each).
        # Acceptable for typical eval workloads (<1000 sessions); for persistent
        # servers consider wrapping in an LRU-bounded structure.
        self._sent: dict[str, set[str]] = {}
        # litellm_call_id -> (input_hash_list, span_id | None).
        # Entries are removed in log_success_event / log_failure_event.
        # Entries for calls where neither fires (cancelled tasks, internal
        # litellm crashes) will leak until the callback object is GC'd.
        self._call_inputs: dict[str, tuple[list[str], str | None]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _session(self) -> str:
        return get_session() or "unknown"

    def _send_new_messages(self, session_id: str, messages: list[dict]) -> list[str]:
        """Hash all messages, POST the new ones, return ordered hash list.

        If the first message is a system message and context block strings are
        available via the sideband ContextVar, each block is content-addressed
        individually.  The system message entry in the returned hash list is a
        compound hash ``{"role": "system", "_blocks": [h1, h2, ...]}`` whose
        constituent block entries (``{"_block": rendered_str}``) are stored
        separately.  This reduces storage from O(N × |system|) to
        O(N × |delta|) for sessions with growing context blocks.
        """
        from openinference_instrumentation_nemo_oo_agents._context_sideband import (
            get_context_blocks,
            set_context_blocks,
        )

        # Build (hash, entry) pairs; may expand system message into block entries
        entries: list[tuple[str, dict]] = []  # goes into hashes / input_hashes
        extras: list[tuple[str, dict]] = []  # block sub-entries for compound msg

        for i, m in enumerate(messages):
            if i == 0 and m.get("role") == "system":
                block_strings = get_context_blocks()
                if block_strings:
                    set_context_blocks([])  # consume the sideband
                    block_hashes = [_hash_msg({"_block": s}) for s in block_strings]
                    compound: dict = {"role": "system", "_blocks": block_hashes}
                    entries.append((_hash_msg(compound), compound))
                    extras = [(bh, {"_block": bs}) for bh, bs in zip(block_hashes, block_strings, strict=True)]
                    continue
            entries.append((_hash_msg(m), m))

        hashes = [h for h, _ in entries]

        with self._lock:
            known = self._sent.setdefault(session_id, set())
            new: list[dict] = []
            # Block sub-entries first so they exist before the compound entry
            for h, m in extras:
                if h not in known:
                    known.add(h)
                    new.append({"h": h, "msg": m})
            for h, m in entries:
                if h not in known:
                    known.add(h)
                    new.append({"h": h, "msg": m})

        if new:
            _post_json(self._messages_url, new, session_id=session_id)
        return hashes

    # ------------------------------------------------------------------
    # Sync hooks
    # ------------------------------------------------------------------

    @staticmethod
    def _current_span_id() -> str | None:
        """Return the active OTel span_id as a 16-char hex string, or None."""
        try:
            ctx = otel_trace.get_current_span().get_span_context()
            if ctx.is_valid:
                return format(ctx.span_id, "016x")
        except Exception as exc:
            log.debug("Failed to read current span_id: %s", exc)
        return None

    def log_pre_api_call(self, model: str, messages: list, kwargs: dict) -> None:
        session_id = self._session()
        call_id = kwargs.get("litellm_call_id", "")
        dicts = [_msg_to_dict(m) for m in messages]
        hashes = self._send_new_messages(session_id, dicts)
        span_id = self._current_span_id()
        with self._lock:
            self._call_inputs[call_id] = (hashes, span_id)

    def log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        session_id = self._session()
        call_id = kwargs.get("litellm_call_id", "")
        with self._lock:
            stored = self._call_inputs.pop(call_id, None)
        if stored is None:
            log.warning(
                "[MessageJournal] log_success_event fired for call_id=%r with no prior "
                "log_pre_api_call — input_hashes will be empty (retry or out-of-order event?)",
                call_id,
            )
            input_hashes, span_id = [], None
        else:
            input_hashes, span_id = stored

        output_msgs = _extract_output_msgs(response_obj)
        output_hashes = self._send_new_messages(session_id, output_msgs)

        usage = getattr(response_obj, "usage", None)
        tokens: dict[str, int] | None = None
        if usage:
            tokens = {
                "prompt": getattr(usage, "prompt_tokens", 0) or 0,
                "completion": getattr(usage, "completion_tokens", 0) or 0,
            }
            details = getattr(usage, "prompt_tokens_details", None)
            if details:
                cached = getattr(details, "cached_tokens", 0) or 0
                if cached:
                    tokens["cached"] = cached

        record: dict = {
            "call_id": call_id,
            "session_id": session_id,
            "model": kwargs.get("model", ""),
            "ts_start": _to_ts(start_time),
            "ts_end": _to_ts(end_time),
            "input_hashes": input_hashes,
            "output_hashes": output_hashes,
            "tokens": tokens,
        }
        if span_id:
            record["span_id"] = span_id
        _post_json(self._calls_url, record, session_id=session_id)

    def log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        call_id = kwargs.get("litellm_call_id", "")
        with self._lock:
            self._call_inputs.pop(call_id, None)

    # ------------------------------------------------------------------
    # Async hooks — delegate to sync; ContextVar propagates correctly
    # ------------------------------------------------------------------

    async def async_log_pre_api_call(self, model: str, messages: list, kwargs: dict) -> None:
        self.log_pre_api_call(model, messages, kwargs)

    async def async_log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        self.log_failure_event(kwargs, response_obj, start_time, end_time)

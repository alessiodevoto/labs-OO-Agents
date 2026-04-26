# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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
    from nemo_oo_agents.tracing._litellm_journal import (
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
from collections.abc import Callable
from typing import Any

from litellm.integrations.custom_logger import CustomLogger
from opentelemetry import trace as otel_trace

from nemo_oo_agents.tracing._session import get_session

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


def _skeleton_dict_message(msg: dict, blocks: dict[str, str]) -> dict:
    """Content-address ``msg["content"]`` and each tool_call's ``arguments``.

    Mirrors what :func:`nemo_oo_agents.runtime.actor._build_journal_payload`
    does for ``RenderedMessage`` inputs, but takes the dict shape returned
    by :func:`_extract_output_msgs` (and anything else that lands already
    as an OpenAI-shape message dict).

    Populates *blocks* with ``hash -> content`` for every large field so
    the caller can feed those to :meth:`MessageJournalCallback._send_new_blocks`
    for per-session dedup.

    Fields transformed:

    * ``content`` (string) → replaced with ``parts=[{"block_hash": …}]``.
    * ``tool_calls[i].function.arguments`` (string) → replaced with
      ``arguments_hash`` under the same ``function`` object.
    * ``images`` (list[str]) → replaced with ``image_hashes``
      (list[str]), one hash per image.

    Unchanged fields (``role``, ``tool_call_id``, ``type``, ``name``,
    message-level extras) are carried through untouched.
    """
    entry: dict = {k: v for k, v in msg.items() if k not in ("content", "tool_calls", "images")}

    content = msg.get("content")
    if content is not None:
        content_s = str(content)
        h = _hash_str(content_s)
        blocks[h] = content_s
        entry["parts"] = [{"block_hash": h}]

    tcs = msg.get("tool_calls")
    if tcs:
        new_tcs: list[dict] = []
        for tc in tcs:
            new_tc = dict(tc)
            fn = dict(new_tc.get("function") or {})
            args = fn.get("arguments")
            if args is not None:
                args_s = str(args)
                ah = _hash_str(args_s)
                blocks[ah] = args_s
                fn.pop("arguments", None)
                fn["arguments_hash"] = ah
            new_tc["function"] = fn
            new_tcs.append(new_tc)
        entry["tool_calls"] = new_tcs

    images = msg.get("images")
    if images:
        image_hashes: list[str] = []
        for img in images:
            # Canonical JSON so dict-shape images hash stably regardless
            # of key order. Non-dict shapes (already a string URL, etc.)
            # just serialize to themselves.
            img_s = (
                json.dumps(img, sort_keys=True, separators=(",", ":"))
                if not isinstance(img, str)
                else img
            )
            h = _hash_str(img_s)
            blocks[h] = img_s
            image_hashes.append(h)
        entry["image_hashes"] = image_hashes

    return entry


def _hash_str(s: str) -> str:
    """SHA-256 a string the same way ``actor._build_journal_payload`` does."""
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


_LARGE_PAYLOAD_BYTES = 512 * 1024  # 512 KB — warn when payloads get this big


_POST_RETRIES = 3
_POST_RETRY_DELAYS = (1.0, 3.0, 5.0)


def _post_json(
    url: str,
    payload: Any,
    *,
    session_id: str = "",
    timeout: float = 15.0,
    on_success: Callable[[], None] | None = None,
) -> None:
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
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
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
                        if on_success is not None:
                            on_success()
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
    """LiteLLM callback that streams skeletons + content-addressed blocks.

    Per LLM call:

    1. ``log_pre_api_call`` consumes the journal sideband (populated by
       the nemo_oo_agents actor's ``_build_journal_payload``) and POSTs
       any newly-seen blocks (by hash) to ``/v1/journal/blocks``. The
       skeleton — the wire message list with block refs replaced by
       hashes — is held until the call completes.
    2. ``log_success_event`` POSTs the full call record
       (``{call_id, session_id, skeleton, output_messages, span_id, tokens}``)
       to ``/v1/journal/calls``. Output messages (assistant replies)
       are included inline — they're small and one-shot per call.

    Per-session in-memory hash sets avoid retransmitting blocks that
    were already sent for that session.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        base = base_url.rstrip("/")
        self._blocks_url = base + "/v1/journal/blocks"
        self._calls_url = base + "/v1/journal/calls"
        self._base_url = base
        # Track block hashes for the current session only.
        # When the session changes the old set is dropped, so memory
        # stays bounded regardless of how many sessions the process sees.
        self._sent_session: str = ""
        self._sent_hashes: set[str] = set()
        # litellm_call_id -> (input_skeleton, span_id | None). Entries
        # are removed in log_success_event / log_failure_event.
        self._call_inputs: dict[str, tuple[list[dict], str | None]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _session(self) -> str:
        return get_session() or "unknown"

    def _send_new_blocks(self, session_id: str, blocks: dict[str, str]) -> None:
        """POST any blocks this session hasn't seen yet.

        Only the current session's hashes are tracked.  When the session
        changes the old set is dropped, keeping memory bounded.  Hashes
        are recorded *after* the POST succeeds so a failed fire-and-forget
        attempt will be retried on the next call.
        """
        if not blocks:
            return
        with self._lock:
            if session_id != self._sent_session:
                self._sent_session = session_id
                self._sent_hashes = set()
            new_entries = [
                {"hash": h, "content": c} for h, c in blocks.items() if h not in self._sent_hashes
            ]
        if new_entries:
            new_hashes = frozenset(blocks.keys())

            def _on_success() -> None:
                with self._lock:
                    # Replace with current payload's hashes — old hashes
                    # from collapsed/archived events are forgotten.
                    self._sent_hashes.clear()
                    self._sent_hashes.update(new_hashes)

            _post_json(self._blocks_url, new_entries, session_id=session_id, on_success=_on_success)
        else:
            # No new blocks to POST, but still prune the tracking set
            # to only the current payload's hashes.
            with self._lock:
                self._sent_hashes.clear()
                self._sent_hashes.update(blocks.keys())

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
        from nemo_oo_agents.tracing._context_sideband import (
            get_journal_payload,
            set_journal_payload,
        )

        session_id = self._session()
        call_id = kwargs.get("litellm_call_id", "")

        payload = get_journal_payload()
        if payload is not None:
            set_journal_payload(None)  # consume
            self._send_new_blocks(session_id, payload.blocks)
            input_skeleton = payload.skeleton
        else:
            # No sideband — publish the raw messages as the skeleton
            # with no block refs. The viewer just uses their content
            # as-is, matching what the wire shows.
            input_skeleton = [_msg_to_dict(m) for m in messages]

        span_id = self._current_span_id()
        with self._lock:
            self._call_inputs[call_id] = (input_skeleton, span_id)

    def log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        session_id = self._session()
        call_id = kwargs.get("litellm_call_id", "")
        with self._lock:
            stored = self._call_inputs.pop(call_id, None)
        if stored is None:
            log.warning(
                "[MessageJournal] log_success_event fired for call_id=%r with no prior "
                "log_pre_api_call — input_skeleton will be empty (retry or out-of-order event?)",
                call_id,
            )
            input_skeleton, span_id = [], None
        else:
            input_skeleton, span_id = stored

        raw_output = _extract_output_msgs(response_obj)
        # Content-address the output messages too. The assistant's reply
        # itself doesn't dedup across *different* calls, but a single
        # reply can already be hundreds of KB when it embeds a large
        # code block in ``tool_call.arguments`` — routing that body
        # through the blocks sideband keeps the /v1/journal/calls record
        # small and re-uses any hash that overlaps with messages the
        # agent will echo back on subsequent turns.
        output_blocks: dict[str, str] = {}
        output_messages = [_skeleton_dict_message(m, output_blocks) for m in raw_output]
        if output_blocks:
            self._send_new_blocks(session_id, output_blocks)

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
            "input_skeleton": input_skeleton,
            "output_messages": output_messages,
            "tokens": tokens,
        }
        if span_id:
            record["span_id"] = span_id
        _post_json(self._calls_url, record, session_id=session_id)

    def log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        call_id = kwargs.get("litellm_call_id", "")
        with self._lock:
            self._call_inputs.pop(call_id, None)

    # ------------------------------------------------------------------
    # Async hooks — delegate to sync; ContextVar propagates correctly
    # ------------------------------------------------------------------

    async def async_log_pre_api_call(self, model: str, messages: list, kwargs: dict) -> None:
        self.log_pre_api_call(model, messages, kwargs)

    async def async_log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.log_failure_event(kwargs, response_obj, start_time, end_time)

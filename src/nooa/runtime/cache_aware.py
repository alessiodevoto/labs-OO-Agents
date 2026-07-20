# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cache-aware manager: prompt-cache observability and prefix-stability enforcement.

Provider-side prompt caching only pays when consecutive requests in a
conversation share a byte-identical message prefix. NOOA's renderer already
produces a cache-friendly shape (static system prefix → append-only events →
volatile trailing ``<context>``), but nothing *verifies* it: a context block
that silently mutates mid-prefix, or an event that is rewritten between
turns, voids the cache for everything after it with no signal anywhere.

:class:`CacheAwareManager` closes that gap. Per conversation (keyed like
``prompt_cache_key``: one key per (agent, strategy)) it:

1. **Tracks cache effectiveness** from provider usage: prompt tokens, cached
   tokens, and the *ceiling* — what an ideal append-only prefix would have
   cached (the previous request's prompt, aligned down to the cache
   granularity). ``report()`` summarizes; per-call efficiency lands in logs.
2. **Detects prefix breaks** by hashing every outgoing message and diffing
   against the previous request in the same conversation. A divergence
   earlier than the previous request's volatile tail is a cache breaker; the
   manager names the message index — and, for the system message, the exact
   context-block key(s) that mutated (via ``RenderedMessage.parts``).
3. **Demotes churning static blocks** (opt-in callback): when the same
   static-partition block mutates ``demote_after`` times, ``on_demote(key)``
   fires so the owner can move the block to the dynamic partition — same
   content, volatile placement, stable prefix from then on. Data-preserving
   by construction: content never changes, only placement.
4. **Context trail** (``stabilize_messages``): rewrites each outgoing message
   list into a strict byte-extension of the previous request. The stock
   renderer REPLACES the trailing ``<context>`` envelope every turn; on
   gpt-5.5-class serving stacks the provider cache only checkpoints at the
   END of a completed request, so that swap makes every follow-up request
   diverge mid-entry and fall back to a shallow (~instructions-sized)
   checkpoint — measured 6% hit rate vs a 92% ceiling. The trail keeps each
   emitted ``<context>`` snapshot pinned at its historical position and
   appends the new one, so requests stay append-only and the full prefix
   stays matchable. No data is lost — the model sees every snapshot, the
   last one current. Disable with ``NOOA_CONTEXT_TRAIL=0``.

Diagnostics add zero provider traffic; the trail adds only the retained
snapshot tokens, which are themselves cached after first use. Disable
diagnostics with ``NOOA_CACHE_DIAG=0``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nooa.context_blocks.models import RenderedMessage

logger = logging.getLogger("nooa.cache_aware")

#: OpenAI prompt-cache granularity (tokens); cache hits come in multiples.
CACHE_GRANULARITY = 128


def _hash_text(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()


def _message_fingerprint(msg: RenderedMessage) -> str:
    """Stable content hash of one outgoing message (wire-relevant fields only)."""
    tc = msg.tool_call
    return _hash_text(
        msg.role.value,
        msg.content or "",
        f"{tc.id}|{tc.name}|{json.dumps(tc.arguments, sort_keys=True, default=str)}" if tc else "",
        msg.tool_call_id or "",
        json.dumps(msg.images, sort_keys=True, default=str) if msg.images else "",
        json.dumps(msg.provider_items, sort_keys=True, default=str) if msg.provider_items else "",
    )


def _block_hashes(msg: RenderedMessage) -> dict[str, str]:
    """Per-block content hashes for a parts-carrying message (system / <context>)."""
    from nooa.context_blocks.models import BlockPart

    out: dict[str, str] = {}
    for part in msg.parts or []:
        if isinstance(part, BlockPart):
            out[part.key] = _hash_text(part.content)
    return out


@dataclass
class PrefixReport:
    """Outcome of comparing one request against the previous one in its conversation."""

    key: str
    prev_len: int
    cur_len: int
    lcp: int  # messages in the longest common prefix
    #: first mutated message index when the break is BEFORE the expected
    #: volatile tail; None for clean (append-only) transitions.
    breaker_index: int | None = None
    breaker_kind: str | None = None  # "system_blocks" | "event" | "history_rewrite"
    mutated_blocks: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.breaker_index is None


@dataclass
class _TrailEntry:
    """One retained ``<context>`` snapshot, pinned after core message *anchor*."""

    anchor: int  # number of core messages that preceded it at emission time
    message: RenderedMessage
    fingerprint: str


@dataclass
class _ConversationState:
    fingerprints: list[str] = field(default_factory=list)
    system_blocks: dict[str, str] = field(default_factory=dict)
    requests: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    ceiling_tokens: int = 0
    last_prompt_tokens: int | None = None
    # context-trail state
    trail: list[_TrailEntry] = field(default_factory=list)
    core_fingerprints: list[str] = field(default_factory=list)


def _is_context_envelope(msg: RenderedMessage) -> bool:
    """The CachedBlockFormatter's trailing dynamic-blocks envelope.

    Identified structurally: a USER message whose first part is the
    formatter's ``CONTEXT_ENVELOPE_OPEN`` TextPart (event messages carry a
    BlockPart first, so they can't collide). The delimiter is imported from
    the formatter so detection stays in lockstep with what it emits.
    """
    from nooa.context_blocks.models import Role, TextPart
    from nooa.context_blocks.renderers.cached import CONTEXT_ENVELOPE_OPEN

    return (
        msg.role == Role.USER
        and bool(msg.parts)
        and isinstance(msg.parts[0], TextPart)
        and msg.parts[0].text == CONTEXT_ENVELOPE_OPEN
    )


class CacheAwareManager:
    """Per-agent prompt-cache tracker. See module docstring."""

    def __init__(
        self,
        *,
        granularity: int = CACHE_GRANULARITY,
        demote_after: int = 3,
        on_demote: Callable[[str], bool] | None = None,
        enabled: bool | None = None,
        context_trail: bool | None = None,
    ) -> None:
        if enabled is None:
            enabled = os.environ.get("NOOA_CACHE_DIAG", "1") != "0"
        if context_trail is None:
            context_trail = os.environ.get("NOOA_CONTEXT_TRAIL", "1") != "0"
        self.enabled = enabled
        self.context_trail = context_trail
        self.granularity = granularity
        self.demote_after = demote_after
        self.on_demote = on_demote
        self._conversations: dict[str, _ConversationState] = {}
        self._block_churn: dict[str, int] = {}
        self._demoted: set[str] = set()

    # -------------------------------------------------------- context trail
    def stabilize_messages(
        self, key: str, messages: list[RenderedMessage]
    ) -> list[RenderedMessage]:
        """Rewrite *messages* so consecutive requests are byte-append-only.

        The renderer replaces the trailing ``<context>`` envelope every turn;
        this pins each emitted snapshot at its historical position and appends
        the new one only when its content changed. When the core history
        (everything except the envelope) does not extend the previous
        request's core — a summarizer collapse or rollback — the trail is
        reset and the list passes through unchanged (deliberate pay-once
        cache miss).

        Returns the input list unchanged when the trail is disabled or there
        is nothing to do.
        """
        if not self.context_trail or not messages:
            return messages

        state = self._conversations.setdefault(key, _ConversationState())

        core = messages
        envelope: RenderedMessage | None = None
        if _is_context_envelope(messages[-1]):
            core = messages[:-1]
            envelope = messages[-1]

        core_fps = [_message_fingerprint(m) for m in core]

        # Core must extend the previous core; otherwise history was rewritten.
        prev = state.core_fingerprints
        extends = len(core_fps) >= len(prev) and core_fps[: len(prev)] == prev
        if not extends:
            if prev:
                logger.info("[cache-aware] %s: core history rewritten — context trail reset", key)
            state.trail = []

        if envelope is not None:
            env_fp = _message_fingerprint(envelope)
            if not state.trail or state.trail[-1].fingerprint != env_fp:
                state.trail.append(
                    _TrailEntry(anchor=len(core), message=envelope, fingerprint=env_fp)
                )

        state.core_fingerprints = core_fps

        if not state.trail:
            return messages

        # Weave trail snapshots back in at their historical anchors.
        out: list[RenderedMessage] = []
        ti = 0
        for i, msg in enumerate(core):
            while ti < len(state.trail) and state.trail[ti].anchor <= i:
                out.append(state.trail[ti].message)
                ti += 1
            out.append(msg)
        while ti < len(state.trail):
            out.append(state.trail[ti].message)
            ti += 1
        # Preserve object identity when weaving reproduced the input verbatim.
        # First turn (and any turn with no historical snapshot woven into the
        # middle): the only trail entry is the current envelope anchored at the
        # end, so `out` re-creates `messages` element-for-element. Returning the
        # original list lets the caller's `is` identity check skip a redundant
        # provider re-format. Pure optimization — same content either way.
        if len(out) == len(messages) and all(a is b for a, b in zip(out, messages, strict=True)):
            return messages
        return out

    # ------------------------------------------------------------- requests
    def observe_request(self, key: str, messages: list[RenderedMessage]) -> PrefixReport | None:
        """Diff this request's messages against the previous request for *key*.

        Returns a :class:`PrefixReport` (None when disabled or first request).
        Logs a structured warning for genuine prefix breaks.
        """
        if not self.enabled:
            return None
        state = self._conversations.setdefault(key, _ConversationState())
        cur = [_message_fingerprint(m) for m in messages]
        cur_blocks = _block_hashes(messages[0]) if messages else {}
        prev, prev_blocks = state.fingerprints, state.system_blocks
        state.fingerprints, state.system_blocks = cur, cur_blocks
        state.requests += 1
        if not prev:
            return PrefixReport(key=key, prev_len=0, cur_len=len(cur), lcp=0)

        lcp = 0
        for a, b in zip(prev, cur, strict=False):
            if a != b:
                break
            lcp += 1

        report = PrefixReport(key=key, prev_len=len(prev), cur_len=len(cur), lcp=lcp)

        # Expected volatile tail: the previous request's LAST message (the
        # trailing <context> envelope) is replaced each turn. Divergence at
        # prev_len - 1 or later is the designed pattern; anything earlier
        # broke bytes the provider had already cached.
        if lcp >= len(prev) - 1:
            return report

        report.breaker_index = lcp
        if len(cur) < len(prev) - 1:
            # History got shorter: summarization / collapse. Deliberate,
            # pay-once rewrite — report as info, not a warning. Checked FIRST:
            # a collapse can also rewrite the system message, and attributing
            # that to block churn would wrongly count toward demotion.
            report.breaker_kind = "history_rewrite"
        elif lcp == 0 and prev_blocks and cur_blocks:
            report.breaker_kind = "system_blocks"
            report.mutated_blocks = sorted(
                k
                for k in prev_blocks.keys() | cur_blocks.keys()
                if prev_blocks.get(k) != cur_blocks.get(k)
            )
            self._note_block_churn(report.mutated_blocks)
        else:
            report.breaker_kind = "event"

        if report.breaker_kind == "history_rewrite":
            logger.info(
                "[cache-aware] %s: history rewritten (%d -> %d messages) — "
                "expected one-off cache miss (summarization/collapse)",
                key,
                len(prev),
                len(cur),
            )
        else:
            logger.warning(
                "[cache-aware] %s: prompt prefix broken at message %d/%d (%s)%s — "
                "provider cache misses everything after this point",
                key,
                lcp,
                len(prev),
                report.breaker_kind,
                f" mutated blocks: {report.mutated_blocks}" if report.mutated_blocks else "",
            )
        return report

    def _note_block_churn(self, keys: list[str]) -> None:
        for k in keys:
            self._block_churn[k] = self._block_churn.get(k, 0) + 1
            if (
                self.on_demote is not None
                and k not in self._demoted
                and self._block_churn[k] >= self.demote_after
            ):
                self._demoted.add(k)
                try:
                    moved = self.on_demote(k)
                except Exception:
                    logger.exception("[cache-aware] demote hook failed for block %r", k)
                    continue
                if moved:
                    logger.warning(
                        "[cache-aware] context block %r mutated %d times inside the "
                        "static prefix — demoted to the dynamic partition (same "
                        "content, volatile placement) to stabilize the cacheable prefix",
                        k,
                        self._block_churn[k],
                    )

    # ---------------------------------------------------------------- usage
    def observe_usage(self, key: str, prompt_tokens: int, cached_tokens: int) -> None:
        """Fold provider-reported usage of one call into the conversation stats."""
        if not self.enabled or prompt_tokens <= 0:
            return
        state = self._conversations.setdefault(key, _ConversationState())
        state.prompt_tokens += prompt_tokens
        state.cached_tokens += cached_tokens
        if state.last_prompt_tokens is not None:
            ceiling = min(state.last_prompt_tokens, prompt_tokens)
            state.ceiling_tokens += (ceiling // self.granularity) * self.granularity
        state.last_prompt_tokens = prompt_tokens

    # --------------------------------------------------------------- report
    def report(self) -> dict[str, dict[str, int | float]]:
        """Per-conversation totals: hit rate and share of the achievable ceiling."""
        out: dict[str, dict[str, int | float]] = {}
        for key, s in self._conversations.items():
            out[key] = {
                "requests": s.requests,
                "prompt_tokens": s.prompt_tokens,
                "cached_tokens": s.cached_tokens,
                "ceiling_tokens": s.ceiling_tokens,
                "hit_rate": (s.cached_tokens / s.prompt_tokens) if s.prompt_tokens else 0.0,
                "ceiling_capture": (
                    (s.cached_tokens / s.ceiling_tokens) if s.ceiling_tokens else 0.0
                ),
            }
        return out

    def log_summary(self) -> None:
        for key, r in self.report().items():
            logger.info(
                "[cache-aware] %s: %d requests, %s prompt tokens, %s cached "
                "(hit %.1f%%, %.0f%% of achievable ceiling)",
                key,
                r["requests"],
                f"{r['prompt_tokens']:,}",
                f"{r['cached_tokens']:,}",
                100 * r["hit_rate"],
                100 * r["ceiling_capture"],
            )

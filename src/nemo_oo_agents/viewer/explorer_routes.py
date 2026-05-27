# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Trace explorer API routes — server-side execution of TraceExplorer methods.

Thin-client path: instead of downloading all spans and parsing client-side,
the client hits these endpoints and the server runs TraceExplorer logic
against spans loaded directly from the DB.
"""

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import otlp_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/explorer", tags=["explorer"])

# ---------------------------------------------------------------------------
# LRU Cache for TraceExplorer instances
# ---------------------------------------------------------------------------

_CACHE_MAX_SIZE = 16
_explorer_cache: OrderedDict[str, Any] = OrderedDict()
_cache_lock = threading.Lock()


def _get_cached_explorer(session_id: str):
    """Return a cached TraceExplorer or None."""
    with _cache_lock:
        if session_id in _explorer_cache:
            _explorer_cache.move_to_end(session_id)
            return _explorer_cache[session_id]
    return None


def _put_cached_explorer(session_id: str, explorer):
    """Store a TraceExplorer in the cache, evicting LRU if full."""
    with _cache_lock:
        if session_id in _explorer_cache:
            _explorer_cache.move_to_end(session_id)
        else:
            _explorer_cache[session_id] = explorer
            _explorer_cache.move_to_end(session_id)
            while len(_explorer_cache) > _CACHE_MAX_SIZE:
                _explorer_cache.popitem(last=False)


def clear_explorer_cache():
    """Clear the explorer cache."""
    with _cache_lock:
        _explorer_cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_explorer(session_id: str):
    """Load spans from DB and build a TraceExplorer, with LRU caching.

    Returns a cached instance if available; otherwise builds fresh and caches.
    """
    cached = _get_cached_explorer(session_id)
    if cached is not None:
        return cached

    from nemo_oo_agents.trace_explorer.explorer import (
        TraceExplorer,
        _normalize_otlp_span,
        _parse_trace_from_spans,
        set_quiet_mode,
    )

    if not otlp_store.session_exists(session_id):
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    otlp_spans = otlp_store.get_session_spans(session_id, augment=True)
    if not otlp_spans:
        raise HTTPException(status_code=404, detail=f"No spans found for session: {session_id}")

    # Normalize from OTLP wire format to internal flat-attribute format
    raw_spans = [_normalize_otlp_span(s) for s in otlp_spans]

    set_quiet_mode(True)
    sessions = _parse_trace_from_spans(raw_spans)
    eval_result = TraceExplorer._extract_eval_from_spans(raw_spans)

    explorer = TraceExplorer(
        sessions=sessions,
        trace_file=f"viewer://{session_id}",
        eval_result=eval_result,
        raw_spans=raw_spans,
        viewer_url=None,
    )

    _put_cached_explorer(session_id, explorer)
    return explorer


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ExplorerTextResponse(BaseModel):
    """Text response from an explorer method."""

    result: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/summary")
async def get_summary(
    session_id: str = Query(..., description="Viewer session ID"),
) -> dict[str, Any]:
    """Lightweight session summary using direct DB queries (no TraceExplorer build)."""
    summary = await asyncio.to_thread(otlp_store.get_session_summary, session_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return summary


@router.get("/agent-spans")
async def get_agent_spans(
    session_id: str = Query(..., description="Viewer session ID"),
) -> dict[str, Any]:
    """Return only AGENT spans for a session (lightweight tree structure)."""
    spans = await asyncio.to_thread(otlp_store.get_agent_spans, session_id)
    return {"spans": spans, "count": len(spans)}


@router.get("/error-spans")
async def get_error_spans(
    session_id: str = Query(..., description="Viewer session ID"),
) -> dict[str, Any]:
    """Return only error spans for a session (direct DB query)."""
    spans = await asyncio.to_thread(otlp_store.get_error_spans, session_id)
    return {"spans": spans, "count": len(spans)}


@router.get("/descendant-spans")
async def get_descendant_spans(
    session_id: str = Query(..., description="Viewer session ID"),
    span_id: str = Query(..., description="Root span ID to get descendants of"),
) -> dict[str, Any]:
    """Return a span subtree (span + all descendants). For targeted loading."""
    spans = await asyncio.to_thread(otlp_store.get_descendant_spans, session_id, span_id)
    if not spans:
        raise HTTPException(status_code=404, detail=f"Span not found: {span_id}")
    return {"spans": spans, "count": len(spans)}


@router.get("/overview")
async def get_overview(
    session_id: str = Query(..., description="Viewer session ID"),
    concise: bool = Query(True, description="Compact view"),
) -> ExplorerTextResponse:
    """Run get_overview() server-side and return the formatted string."""
    explorer = await asyncio.to_thread(_build_explorer, session_id)
    result = await explorer.get_overview(concise=concise)
    return ExplorerTextResponse(result=result)


@router.get("/session")
async def get_session(
    session_id: str = Query(..., description="Viewer session ID"),
    target_session_id: str = Query(..., description="6-char session ID to inspect"),
    concise: bool = Query(False, description="Truncate long content"),
) -> ExplorerTextResponse:
    """Run get_session() server-side for a specific session."""
    explorer = await asyncio.to_thread(_build_explorer, session_id)
    result = await explorer.get_session(target_session_id, concise=concise)
    return ExplorerTextResponse(result=result)


@router.get("/session-list")
async def get_session_list(
    session_id: str = Query(..., description="Viewer session ID"),
) -> dict[str, Any]:
    """Return structured session list."""
    explorer = await asyncio.to_thread(_build_explorer, session_id)
    summaries = await explorer.get_session_list()
    return {"sessions": [s.model_dump() if hasattr(s, "model_dump") else vars(s) for s in summaries]}


@router.get("/turn")
async def get_turn(
    session_id: str = Query(..., description="Viewer session ID"),
    target_session_id: str = Query(..., description="6-char session ID"),
    turn_index: int = Query(..., description="Turn index"),
) -> ExplorerTextResponse:
    """Run get_turn() server-side."""
    explorer = await asyncio.to_thread(_build_explorer, session_id)
    result = await explorer.get_turn(target_session_id, turn_index)
    return ExplorerTextResponse(result=result)


@router.get("/errors")
async def get_errors(
    session_id: str = Query(..., description="Viewer session ID"),
) -> ExplorerTextResponse:
    """Run get_errors() server-side."""
    explorer = await asyncio.to_thread(_build_explorer, session_id)
    result = await explorer.get_errors()
    return ExplorerTextResponse(result=result)


@router.get("/search")
async def search(
    session_id: str = Query(..., description="Viewer session ID"),
    pattern: str = Query(..., description="Search pattern"),
) -> ExplorerTextResponse:
    """Run search() server-side."""
    explorer = await asyncio.to_thread(_build_explorer, session_id)
    result = await explorer.search(pattern)
    return ExplorerTextResponse(result=result)


@router.get("/timeline")
async def get_timeline(
    session_id: str = Query(..., description="Viewer session ID"),
    max_events: int = Query(50, description="Max timeline events"),
) -> ExplorerTextResponse:
    """Run get_timeline() server-side."""
    explorer = await asyncio.to_thread(_build_explorer, session_id)
    result = await explorer.get_timeline(max_events=max_events)
    return ExplorerTextResponse(result=result)


@router.get("/first-error")
async def find_first_error(
    session_id: str = Query(..., description="Viewer session ID"),
) -> ExplorerTextResponse:
    """Run find_first_error() server-side."""
    explorer = await asyncio.to_thread(_build_explorer, session_id)
    result = await explorer.find_first_error()
    return ExplorerTextResponse(result=result)


@router.get("/eval-context")
async def get_eval_context(
    session_id: str = Query(..., description="Viewer session ID"),
    concise: bool = Query(True, description="Compact view"),
) -> ExplorerTextResponse:
    """Run get_eval_context() server-side."""
    explorer = await asyncio.to_thread(_build_explorer, session_id)
    result = await explorer.get_eval_context(concise=concise)
    return ExplorerTextResponse(result=result)

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


def _build_explorer_for_span(session_id: str, span_id: str):
    """Build a mini-TraceExplorer from just the subtree under span_id.

    Uses get_descendant_spans() to load only the relevant spans,
    then parses just that subset into a TraceExplorer. Much faster
    for large traces when drilling into a specific session.
    """
    from nemo_oo_agents.trace_explorer.explorer import (
        TraceExplorer,
        _normalize_otlp_span,
        _parse_trace_from_spans,
        set_quiet_mode,
    )

    otlp_spans = otlp_store.get_descendant_spans(session_id, span_id)
    if not otlp_spans:
        raise HTTPException(status_code=404, detail=f"Span not found: {span_id}")

    raw_spans = [_normalize_otlp_span(s) for s in otlp_spans]
    set_quiet_mode(True)
    sessions = _parse_trace_from_spans(raw_spans)

    return TraceExplorer(
        sessions=sessions,
        trace_file=f"viewer://{session_id}",
        eval_result=None,
        raw_spans=raw_spans,
        viewer_url=None,
    )


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


@router.get("/search-fast")
async def search_fast(
    session_id: str = Query(..., description="Viewer session ID"),
    query: str = Query(..., description="FTS5 search query"),
    limit: int = Query(100, description="Max results"),
) -> dict[str, Any]:
    """Fast full-text search using FTS5 index (no TraceExplorer build).

    Supports FTS5 query syntax: simple words, "quoted phrases", AND/OR/NOT.
    Returns matching spans with snippets showing context around matches.
    """
    results = await asyncio.to_thread(otlp_store.search_spans_fts, session_id, query, limit)
    return {"matches": results, "count": len(results), "query": query}


@router.get("/overview-fast")
async def get_overview_fast(
    session_id: str = Query(..., description="Viewer session ID"),
) -> dict[str, Any]:
    """Lightweight overview using only AGENT spans (no full tree build).

    Returns the call graph structure with agent names, methods, durations,
    and error status — but no turn content or message details. Much faster
    than get_overview() for large traces.
    """
    from nemo_oo_agents.trace_explorer.explorer import _normalize_otlp_span, _otlp_attrs_to_dict

    otlp_spans = await asyncio.to_thread(otlp_store.get_agent_spans, session_id)
    if not otlp_spans:
        # Fall back to checking if session exists
        exists = await asyncio.to_thread(otlp_store.session_exists, session_id)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return {"sessions": [], "session_id": session_id}

    # Build lightweight call graph from AGENT spans
    sessions = []
    for span in otlp_spans:
        attrs = span.get("attributes", [])
        if isinstance(attrs, list):
            attr_dict = {a["key"]: a.get("value", {}).get("stringValue", "") for a in attrs}
        else:
            attr_dict = attrs

        start_ns = int(span.get("startTimeUnixNano", 0))
        end_ns = int(span.get("endTimeUnixNano", 0))
        duration_ms = (end_ns - start_ns) / 1_000_000 if end_ns > start_ns else 0.0

        status_code = span.get("status", {}).get("code", 1)
        status = "ERROR" if status_code == 2 else "OK"

        sessions.append({
            "span_id": span.get("spanId", ""),
            "session_id": span.get("spanId", "")[:6],
            "parent_span_id": span.get("parentSpanId"),
            "agent_name": attr_dict.get("agent.name", span.get("name", "").split(".")[0]),
            "method_name": attr_dict.get("agent.method", span.get("name", "").split(".")[-1]),
            "duration_ms": round(duration_ms, 1),
            "status": status,
            "status_message": span.get("status", {}).get("message"),
        })

    return {"sessions": sessions, "session_id": session_id}


@router.get("/session-fast")
async def get_session_fast(
    session_id: str = Query(..., description="Viewer session ID"),
    target_session_id: str = Query(..., description="6-char session ID to inspect"),
    span_id: str = Query(..., description="Span ID of the target agent session"),
    concise: bool = Query(False, description="Truncate long content"),
) -> ExplorerTextResponse:
    """Load only the subtree for a specific session and run get_session().

    Uses get_descendant_spans() to avoid loading the full trace — much faster
    for large traces when you already know the span_id of the agent session.
    """
    explorer = await asyncio.to_thread(_build_explorer_for_span, session_id, span_id)
    result = await explorer.get_session(target_session_id, concise=concise)
    return ExplorerTextResponse(result=result)


@router.get("/turn-fast")
async def get_turn_fast(
    session_id: str = Query(..., description="Viewer session ID"),
    target_session_id: str = Query(..., description="6-char session ID"),
    span_id: str = Query(..., description="Span ID of the target agent session"),
    turn_index: int = Query(..., description="Turn index"),
) -> ExplorerTextResponse:
    """Load only the subtree for a specific session and run get_turn().

    Uses get_descendant_spans() to avoid loading the full trace.
    """
    explorer = await asyncio.to_thread(_build_explorer_for_span, session_id, span_id)
    result = await explorer.get_turn(target_session_id, turn_index)
    return ExplorerTextResponse(result=result)


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

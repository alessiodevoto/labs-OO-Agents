# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Trace explorer API routes — server-side execution of TraceExplorer methods.

Thin-client path: instead of downloading all spans and parsing client-side,
the client hits these endpoints and the server runs TraceExplorer logic
against spans loaded directly from the DB.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import otlp_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/explorer", tags=["explorer"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_explorer(session_id: str):
    """Load spans from DB and build a TraceExplorer for the given session.

    Imports are deferred to avoid circular imports at module level.
    """
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

    return TraceExplorer(
        sessions=sessions,
        trace_file=f"viewer://{session_id}",
        eval_result=eval_result,
        raw_spans=raw_spans,
        viewer_url=None,
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ExplorerTextResponse(BaseModel):
    """Text response from an explorer method."""

    result: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


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

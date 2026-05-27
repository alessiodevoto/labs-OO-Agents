# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Thin client for TraceExplorer — delegates analysis to the viewer server.

Instead of downloading all spans and parsing locally, this client calls
server-side endpoints that run TraceExplorer logic against the DB directly.
Much faster for large traces.

Usage:
    from nemo_oo_agents.trace_explorer.client import TraceExplorerClient

    client = TraceExplorerClient("http://localhost:5001", "my-session-id")
    print(await client.get_overview())
    print(await client.get_session("abc123"))
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import httpx


class TraceExplorerClient:
    """Thin client that calls server-side TraceExplorer endpoints.

    Has the same public async API as TraceExplorer but executes all
    analysis server-side, avoiding the need to download and parse
    all spans locally.
    """

    def __init__(self, base_url: str, session_id: str, *, timeout: float = 60.0):
        """Initialize the thin client.

        Args:
            base_url: Viewer base URL (e.g. "http://localhost:5001").
            session_id: The viewer session ID to explore.
            timeout: HTTP request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._session_id = session_id
        self._timeout = timeout

    @property
    def session_id(self) -> str:
        """The viewer session ID being explored."""
        return self._session_id

    @property
    def base_url(self) -> str:
        """The viewer base URL."""
        return self._base_url

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a GET request to an explorer endpoint."""
        url = f"{self._base_url}/api/explorer/{endpoint}"
        all_params = {"session_id": self._session_id}
        if params:
            all_params.update(params)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.get(url, params=all_params)
                if resp.status_code == 404:
                    raise ValueError(f"Session not found: {self._session_id}")
                resp.raise_for_status()
                return resp.json()
            except httpx.ConnectError as e:
                raise ConnectionError(
                    f"Cannot reach viewer at {self._base_url}: {e}"
                ) from e
            except httpx.HTTPStatusError as e:
                raise ValueError(
                    f"Explorer API returned HTTP {e.response.status_code}: "
                    f"{e.response.text[:200]}"
                ) from e

    async def _get_text(self, endpoint: str, params: dict[str, Any] | None = None) -> str:
        """Make a GET request and return the 'result' text field."""
        data = await self._get(endpoint, params)
        return data.get("result", "")

    # =========================================================================
    # Public API — mirrors TraceExplorer
    # =========================================================================

    async def get_overview(self, *, concise: bool = True) -> str:
        """Get trace overview with call graph.

        Args:
            concise: If True, compact view. If False, full details.
        """
        return await self._get_text("overview", {"concise": concise})

    async def get_session(self, session_id: str, *, concise: bool = False) -> str:
        """Get detailed execution info about a specific session.

        Args:
            session_id: The 6-character session ID to inspect.
            concise: If True, truncate long content.
        """
        return await self._get_text("session", {
            "target_session_id": session_id,
            "concise": concise,
        })

    async def get_session_list(self) -> list[dict[str, Any]]:
        """Return structured session list."""
        data = await self._get("session-list")
        return data.get("sessions", [])

    async def get_turn(self, session_id: str, turn_index: int) -> str:
        """Get full details for a specific turn.

        Args:
            session_id: The 6-character session ID.
            turn_index: Turn index (0-based).
        """
        return await self._get_text("turn", {
            "target_session_id": session_id,
            "turn_index": turn_index,
        })

    async def get_errors(self) -> str:
        """List all errors in the trace with context."""
        return await self._get_text("errors")

    async def search(self, pattern: str) -> str:
        """Search for a pattern across all trace content.

        Args:
            pattern: Text pattern to search for.
        """
        return await self._get_text("search", {"pattern": pattern})

    async def get_timeline(self, max_events: int = 50) -> str:
        """Get chronological timeline of events.

        Args:
            max_events: Maximum number of events to return.
        """
        return await self._get_text("timeline", {"max_events": max_events})

    async def find_first_error(self) -> str:
        """Jump directly to the first error in the trace."""
        return await self._get_text("first-error")

    async def get_eval_context(self, concise: bool = True) -> str:
        """Get evaluation context (inputs, expected outputs, scores).

        Args:
            concise: If True, compact view.
        """
        return await self._get_text("eval-context", {"concise": concise})

    # =========================================================================
    # Lightweight endpoints (no full TraceExplorer build needed)
    # =========================================================================

    async def get_summary(self) -> dict[str, Any]:
        """Get lightweight session summary (span count, duration, error count).

        Uses direct DB queries — much faster than get_overview() for large traces.
        """
        return await self._get("summary")

    async def get_agent_spans(self) -> dict[str, Any]:
        """Get only AGENT spans for the session (tree structure without content)."""
        return await self._get("agent-spans")

    async def get_error_spans(self) -> dict[str, Any]:
        """Get only error spans for the session (direct DB query)."""
        return await self._get("error-spans")

    async def get_session_fast(self, session_id: str, span_id: str, *, concise: bool = False) -> str:
        """Get session details using only the subtree under span_id.

        Much faster than get_session() for large traces — only loads the
        spans belonging to that specific agent session.

        Args:
            session_id: The 6-char session ID to inspect.
            span_id: The full span_id of the target agent session.
            concise: If True, truncate long content.
        """
        return await self._get_text("session-fast", {
            "target_session_id": session_id,
            "span_id": span_id,
            "concise": concise,
        })

    async def get_turn_fast(self, session_id: str, span_id: str, turn_index: int) -> str:
        """Get turn details using only the subtree under span_id.

        Much faster than get_turn() for large traces.

        Args:
            session_id: The 6-char session ID.
            span_id: The full span_id of the target agent session.
            turn_index: Turn index (0-based).
        """
        return await self._get_text("turn-fast", {
            "target_session_id": session_id,
            "span_id": span_id,
            "turn_index": turn_index,
        })

    async def get_descendant_spans(self, span_id: str) -> dict[str, Any]:
        """Get a span and all its descendants (targeted subtree loading).

        Args:
            span_id: The root span ID to get descendants of.
        """
        return await self._get("descendant-spans", {"span_id": span_id})

    async def help(self) -> str:
        """Return usage guide for the TraceExplorer API."""
        return """# TraceExplorerClient (thin-client mode)

This client delegates all analysis to the viewer server, avoiding
the need to download and parse all spans locally.

## Core Methods

- get_overview(concise=True) — Call graph with inputs/outputs/errors
- get_session(session_id, concise=False) — Detailed turn-by-turn execution
- get_session_list() — List of all sessions as structured dicts
- get_turn(session_id, turn_index) — Full LLM context at a specific turn
- get_errors() — All errors with context
- search(pattern) — Find pattern across all trace content
- get_timeline(max_events=50) — Chronological event timeline
- find_first_error() — Jump to first error
- get_eval_context(concise=True) — Evaluation inputs/outputs/scores

## Navigation Pattern

1. Start with get_overview() to see the big picture
2. Find a session of interest in the call graph
3. Use get_session(id) to see turn-by-turn execution
4. Use get_turn(id, n) to see exact LLM context at turn n
5. Use get_errors() to see all errors at once
"""

    def __repr__(self) -> str:
        return f"TraceExplorerClient(base_url={self._base_url!r}, session_id={self._session_id!r})"

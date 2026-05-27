# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for trace explorer thin-client path (explorer_routes + client)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from nemo_oo_agents.trace_explorer.client import TraceExplorerClient


# =============================================================================
# Fixtures
# =============================================================================


def _make_otlp_spans() -> list[dict]:
    """Create minimal OTLP-format spans as returned by otlp_store.get_session_spans."""
    agent_span = {
        "traceId": "trace001",
        "spanId": "aabbccdd11223344",
        "name": "TestAgent.handle",
        "kind": 1,
        "startTimeUnixNano": "1000000000",
        "endTimeUnixNano": "2000000000",
        "attributes": [
            {"key": "openinference.span.kind", "value": {"stringValue": "AGENT"}},
            {"key": "agent.name", "value": {"stringValue": "TestAgent"}},
            {"key": "agent.method", "value": {"stringValue": "handle"}},
            {"key": "agent.call_id", "value": {"stringValue": "call_001"}},
        ],
        "status": {"code": 1},
        "events": [],
        "_resource": {},
    }
    return [agent_span]


@pytest.fixture
def app():
    """Create a test FastAPI app with explorer routes."""
    from fastapi import FastAPI

    from nemo_oo_agents.viewer.explorer_routes import router

    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def mock_otlp_store():
    """Mock otlp_store functions used by explorer_routes."""
    with patch("nemo_oo_agents.viewer.explorer_routes.otlp_store") as mock_store:
        mock_store.session_exists.return_value = True
        mock_store.get_session_spans.return_value = _make_otlp_spans()
        yield mock_store


# =============================================================================
# Server-side route tests
# =============================================================================


@pytest.mark.asyncio
async def test_overview_endpoint(app, mock_otlp_store):
    """Test /api/explorer/overview returns a text result."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/explorer/overview",
            params={"session_id": "test-session"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data
    assert isinstance(data["result"], str)
    assert len(data["result"]) > 0


@pytest.mark.asyncio
async def test_overview_endpoint_session_not_found(app, mock_otlp_store):
    """Test 404 when session doesn't exist."""
    mock_otlp_store.session_exists.return_value = False
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/explorer/overview",
            params={"session_id": "nonexistent"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_errors_endpoint(app, mock_otlp_store):
    """Test /api/explorer/errors endpoint."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/explorer/errors",
            params={"session_id": "test-session"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data


@pytest.mark.asyncio
async def test_search_endpoint(app, mock_otlp_store):
    """Test /api/explorer/search endpoint."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/explorer/search",
            params={"session_id": "test-session", "pattern": "TestAgent"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data


@pytest.mark.asyncio
async def test_session_list_endpoint(app, mock_otlp_store):
    """Test /api/explorer/session-list endpoint."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/explorer/session-list",
            params={"session_id": "test-session"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert isinstance(data["sessions"], list)


@pytest.mark.asyncio
async def test_turn_endpoint(app, mock_otlp_store):
    """Test /api/explorer/turn endpoint."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/explorer/turn",
            params={
                "session_id": "test-session",
                "target_session_id": "aabbcc",
                "turn_index": 0,
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data


@pytest.mark.asyncio
async def test_timeline_endpoint(app, mock_otlp_store):
    """Test /api/explorer/timeline endpoint."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/explorer/timeline",
            params={"session_id": "test-session"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data


@pytest.mark.asyncio
async def test_first_error_endpoint(app, mock_otlp_store):
    """Test /api/explorer/first-error endpoint."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/explorer/first-error",
            params={"session_id": "test-session"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data


@pytest.mark.asyncio
async def test_eval_context_endpoint(app, mock_otlp_store):
    """Test /api/explorer/eval-context endpoint."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/explorer/eval-context",
            params={"session_id": "test-session"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data


# =============================================================================
# Client tests
# =============================================================================


@pytest.mark.asyncio
async def test_client_connection_error():
    """Test that client raises ConnectionError when server is unreachable."""
    client = TraceExplorerClient("http://localhost:19999", "test-session", timeout=2.0)
    with pytest.raises((ConnectionError, ValueError)):
        await client.get_overview()


@pytest.mark.asyncio
async def test_client_help():
    """Test that help() returns usage text without network calls."""
    client = TraceExplorerClient("http://localhost:5001", "test-session")
    result = await client.help()
    assert "get_overview" in result
    assert "get_session" in result


@pytest.mark.asyncio
async def test_client_repr():
    """Test client repr."""
    client = TraceExplorerClient("http://localhost:5001", "my-session")
    assert "localhost:5001" in repr(client)
    assert "my-session" in repr(client)

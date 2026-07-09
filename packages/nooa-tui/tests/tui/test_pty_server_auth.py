# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Session-token authentication tests for the PTY web terminal server.

The server exposes a full interactive shell, so every endpoint must reject
requests without the per-session token: HTTP endpoints return 403 and
WebSocket connections are accepted and immediately closed with code 4403 before any PTY is spawned.
"""

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
pytest.importorskip("ptyprocess", reason="web extra not installed")

from nooa_cli.web.pty_server import _TOKEN_COOKIE, create_pty_app
from starlette.testclient import TestClient

TOKEN = "test-token-abc123"


def _client(auth_token: str | None = TOKEN) -> TestClient:
    # tui_argv is never spawned in these tests: unauthenticated /ws/pty is
    # rejected before the PTY starts, and authenticated tests use /ws/rich.
    app, _kill_all = create_pty_app(tui_argv=["/bin/true"], auth_token=auth_token)
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET / (the xterm.js page)
# ---------------------------------------------------------------------------


def test_index_without_token_is_403():
    resp = _client().get("/")
    assert resp.status_code == 403
    assert "xterm" not in resp.text


def test_index_with_wrong_token_is_403():
    resp = _client().get("/", params={"token": "wrong"})
    assert resp.status_code == 403


def test_index_with_token_is_200_and_sets_cookie():
    resp = _client().get("/", params={"token": TOKEN})
    assert resp.status_code == 200
    assert "xterm" in resp.text
    assert resp.cookies.get(_TOKEN_COOKIE) == TOKEN


def test_index_cookie_allows_reload_without_query_param():
    client = _client()
    assert client.get("/", params={"token": TOKEN}).status_code == 200
    # TestClient persists cookies; a reload without ?token= must still work.
    assert client.get("/").status_code == 200


def test_index_without_auth_token_serves_page():
    """auth_token=None (--no-auth) disables the check entirely."""
    resp = _client(auth_token=None).get("/")
    assert resp.status_code == 200
    assert "xterm" in resp.text


def test_index_injects_token_for_ws_query_param():
    """The served page carries the token so WS URLs don't rely on the
    host-scoped cookie (multi-instance port-collision fix)."""
    resp = _client().get("/", params={"token": TOKEN})
    assert f'window.__NEMO_TERM_TOKEN = "{TOKEN}"' in resp.text


def test_non_ascii_token_fails_closed_not_500():
    """compare_digest(str, str) is ASCII-only; auth compares bytes so
    non-ASCII input fails closed (403 / WS reject) rather than 500."""
    client = _client()
    assert client.get("/", params={"token": "é"}).status_code == 403
    assert client.post("/rich", params={"token": "é"}, json={"kind": "x"}).status_code == 403


def test_openapi_schema_not_exposed():
    """openapi_url=None — no unauthenticated schema disclosure."""
    assert _client().get("/openapi.json").status_code == 404


# ---------------------------------------------------------------------------
# POST /rich
# ---------------------------------------------------------------------------


def test_rich_post_without_token_is_403():
    resp = _client().post("/rich", json={"kind": "markdown", "text": "hi"})
    assert resp.status_code == 403


def test_rich_post_with_token_query_param_is_ok():
    resp = _client().post("/rich", params={"token": TOKEN}, json={"kind": "markdown", "text": "x"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# WebSockets
# ---------------------------------------------------------------------------


def _assert_ws_rejected(client: TestClient, path: str):
    # The server accepts then immediately closes so real browsers observe 4403
    # rather than HTTP-level handshake failure/1006. Starlette TestClient returns
    # the close ASGI event from receive() rather than raising on context entry.
    with client.websocket_connect(path) as ws:
        message = ws.receive()
    assert message == {"type": "websocket.close", "code": 4403, "reason": ""}


@pytest.mark.parametrize("path", ["/ws/pty", "/ws/rich"])
def test_websocket_without_token_rejected(path):
    _assert_ws_rejected(_client(), path)


@pytest.mark.parametrize("path", ["/ws/pty", "/ws/rich"])
def test_websocket_with_wrong_token_rejected(path):
    _assert_ws_rejected(_client(), f"{path}?token=wrong")


def test_rich_websocket_with_token_query_param_accepted():
    client = _client()
    # Seed one history payload so an authenticated connect provably works.
    client.post("/rich", params={"token": TOKEN}, json={"kind": "markdown", "text": "replay"})
    with client.websocket_connect(f"/ws/rich?token={TOKEN}") as ws:
        assert ws.receive_json() == {"kind": "markdown", "text": "replay"}


def test_rich_websocket_with_cookie_accepted():
    client = _client()
    # Visiting the page sets the token cookie, which the WS handshake reuses
    # (this mirrors how the browser JS connects — no token plumbing in JS).
    assert client.get("/", params={"token": TOKEN}).status_code == 200
    client.post("/rich", params={"token": TOKEN}, json={"kind": "markdown", "text": "via-cookie"})
    with client.websocket_connect("/ws/rich") as ws:
        assert ws.receive_json() == {"kind": "markdown", "text": "via-cookie"}

# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for RFC 9728 OAuth authorization-server discovery in mcp/oauth.py."""

import httpx
import pytest

from nemo_oo_agents.mcp import oauth


def _client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, follow_redirects=True)


@pytest.mark.asyncio
async def test_discovery_follows_www_authenticate_resource_metadata():
    """The 401 challenge's resource_metadata pointer drives discovery.

    Mirrors MaaS: the metadata path is NOT a suffix of the server URL, so the
    only way to find it is the WWW-Authenticate header.
    """
    server_url = "https://maas.prd.example.com/maas/jira/mcp"
    metadata_url = "https://maas.prd.example.com/.well-known/oauth-protected-resource/maas/jira/mcp"
    auth_server = "https://maas.prd.example.com/maas/auth/jira-callback"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url == server_url:
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        f'Bearer error="invalid_token", resource_metadata="{metadata_url}"'
                    )
                },
            )
        if url == metadata_url:
            return httpx.Response(200, json={"authorization_servers": [auth_server]})
        # Server-URL-relative well-known probes 404 (MaaS shape).
        return httpx.Response(404)

    async with _client(handler) as client:
        servers = await oauth._discover_authorization_servers(client, server_url)

    assert servers == [auth_server]


@pytest.mark.asyncio
async def test_discovery_falls_back_to_well_known_paths():
    """Servers that expose metadata at the conventional path still work."""
    server_url = "https://example.com/mcp"
    auth_server = "https://example.com/auth"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url == server_url:
            return httpx.Response(401)  # no WWW-Authenticate header
        if url == "https://example.com/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"authorization_servers": [auth_server]})
        return httpx.Response(404)

    async with _client(handler) as client:
        servers = await oauth._discover_authorization_servers(client, server_url)

    assert servers == [auth_server]


@pytest.mark.asyncio
async def test_discovery_returns_empty_when_nothing_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with _client(handler) as client:
        servers = await oauth._discover_authorization_servers(client, "https://x.example/mcp")

    assert servers == []


@pytest.mark.asyncio
async def test_fetch_authorization_server_metadata():
    auth_server = "https://example.com/auth"
    meta = {
        "authorization_endpoint": f"{auth_server}/authorize",
        "token_endpoint": f"{auth_server}/token",
        "registration_endpoint": f"{auth_server}/register",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == f"{auth_server}/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=meta)
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await oauth._fetch_authorization_server_metadata(client, auth_server)

    assert result == meta


@pytest.mark.asyncio
async def test_resource_metadata_pointer_parses_header():
    server_url = "https://x.example/mcp"
    pointer = "https://x.example/.well-known/oauth-protected-resource/mcp"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, headers={"www-authenticate": f'Bearer resource_metadata="{pointer}"'}
        )

    async with _client(handler) as client:
        result = await oauth._resource_metadata_pointer(client, server_url)

    assert result == pointer


@pytest.mark.asyncio
async def test_resource_metadata_pointer_ignored_on_non_401():
    """A 200 response carrying a stray WWW-Authenticate header is not trusted."""
    pointer = "https://x.example/.well-known/oauth-protected-resource/mcp"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"www-authenticate": f'Bearer resource_metadata="{pointer}"'}
        )

    async with _client(handler) as client:
        result = await oauth._resource_metadata_pointer(client, "https://x.example/mcp")

    assert result is None

# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# OAuth 2.0 implementation following RFC 8252 "OAuth 2.0 for Native Apps":
#   https://datatracker.ietf.org/doc/html/rfc8252
#
# Key RFC 8252 practices applied here:
#   §4   — Use the system browser (webbrowser.open), not an embedded webview
#   §7.3 — Use loopback redirect URIs (127.0.0.1) with a dynamic OS-assigned
#           port (bind to port 0) so no fixed port needs to be pre-registered
#           and port conflicts are impossible
#   §8   — PKCE (RFC 7636, S256 method) is required for all native clients
import asyncio
import base64
import contextlib
import hashlib
import html
import logging
import secrets
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

logger = logging.getLogger(__name__)


@dataclass
class OAuthConfig:
    """Configuration for OAuth 2.0 flow.

    Attributes:
        authorization_endpoint: OAuth authorization URL
        token_endpoint: OAuth token exchange URL
        client_id: OAuth client ID
        redirect_uri: Redirect URI for OAuth callback
        scope: Optional OAuth scopes
        client_secret: Optional client secret (for dynamic registration)
    """

    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    scope: str | None = None
    client_secret: str | None = None
    timeout: float = 300.0  # 5 minutes


@dataclass
class OAuthToken:
    """OAuth access token with metadata.

    Attributes:
        access_token: The access token
        token_type: Token type (typically "Bearer")
        expires_in: Token expiration time in seconds (if provided)
        refresh_token: Refresh token (if provided)
    """

    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    refresh_token: str | None = None


def _html_page(title: str, body: str) -> str:
    """Return a minimal styled HTML page for the OAuth callback browser tab."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      display: flex; align-items: center; justify-content: center;
      height: 100vh; margin: 0; background: #f5f5f5;
    }}
    .card {{
      background: #fff; border-radius: 12px; padding: 40px 48px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.10); text-align: center; max-width: 420px;
    }}
    h1 {{ margin-top: 0; font-size: 1.5em; }}
    p {{ margin: 1em 0; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    {body}
  </div>
</body>
</html>"""


class OAuthHandler:
    """Handles OAuth 2.0 PKCE flow for MCP authentication."""

    def __init__(self, config: OAuthConfig):
        """Initialize OAuth handler.

        Args:
            config: OAuth configuration
        """
        self.config = config
        self._code_verifier: str | None = None
        self._code_challenge: str | None = None
        # Set by _capture_code_via_local_server after dynamic port assignment;
        # used by exchange_code_for_token to send the exact redirect_uri the
        # authorization server received (RFC 8252 §4.1 requirement).
        self._actual_redirect_uri: str | None = None

    def _generate_pkce_pair(self) -> tuple[str, str]:
        """Generate PKCE code verifier and challenge.

        Returns:
            Tuple of (code_verifier, code_challenge)
        """
        # Generate code verifier (43-128 characters, URL-safe)
        code_verifier = (
            base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8").rstrip("=")
        )

        # Generate code challenge (SHA256 hash of verifier)
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("utf-8")).digest())
            .decode("utf-8")
            .rstrip("=")
        )

        return code_verifier, code_challenge

    def _build_authorization_url(self, redirect_uri: str | None = None) -> str:
        """Build OAuth authorization URL with PKCE parameters.

        Args:
            redirect_uri: Override redirect URI (e.g. after dynamic port selection).
                          Defaults to self.config.redirect_uri.

        Returns:
            Authorization URL
        """
        self._code_verifier, self._code_challenge = self._generate_pkce_pair()

        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri or self.config.redirect_uri,
            "code_challenge": self._code_challenge,
            "code_challenge_method": "S256",
        }

        if self.config.scope:
            params["scope"] = self.config.scope

        query_string = urlencode(params)
        return f"{self.config.authorization_endpoint}?{query_string}"

    async def authorize(self, open_browser: bool = True) -> str:
        """Perform OAuth authorization flow.

        Implements RFC 8252 §7.3: binds a temporary local HTTP server on an
        OS-assigned port (port 0) so the callback is captured automatically
        without any copy-paste.  Falls back to terminal input if the local
        server cannot be started.

        Args:
            open_browser: Whether to automatically open browser

        Returns:
            Authorization code

        Raises:
            RuntimeError: If authorization fails
        """
        try:
            code = await self._capture_code_via_local_server(open_browser)
        except Exception as e:
            logger.warning(f"Local callback server failed ({e}), falling back to manual input")
            # Fall back: build auth URL with the static config redirect_uri
            auth_url = self._build_authorization_url()
            logger.info(f"Please visit: {auth_url}")
            logger.info("After authorizing, copy the 'code' parameter from the redirect URL.")
            code = (await asyncio.to_thread(input, "Enter authorization code: ")).strip()

        if not code:
            raise RuntimeError("Authorization code not provided")

        return code

    async def _capture_code_via_local_server(self, open_browser: bool) -> str:
        """Start a temporary HTTP server on a dynamic OS-assigned port (RFC 8252 §7.3).

        Args:
            open_browser: Whether to automatically open the browser

        Returns:
            Authorization code extracted from the callback request

        Raises:
            RuntimeError: If the server times out or receives an error callback
        """
        parsed = urlparse(self.config.redirect_uri)
        host = parsed.hostname or "127.0.0.1"
        callback_path = parsed.path or "/callback"
        scheme = parsed.scheme or "http"
        # Use port from redirect_uri if specified, otherwise bind to port 0 for dynamic assignment
        requested_port = parsed.port if parsed.port is not None and parsed.port != 0 else 0

        received_code: list[str] = []
        error_info: list[str] = []
        done = asyncio.Event()
        loop = asyncio.get_running_loop()

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass  # Silence request logs

            def do_GET(self) -> None:
                req_parsed = urlparse(self.path)
                if req_parsed.path != callback_path:
                    self.send_response(404)
                    self.end_headers()
                    return

                params = parse_qs(req_parsed.query)
                if "error" in params:
                    error_info.append(params["error"][0])
                    body = _html_page(
                        "Authorization Failed",
                        f"<p style='color:red'>Error: {html.escape(params['error'][0])}</p>"
                        "<p>You can close this tab.</p>",
                    )
                elif "code" in params:
                    received_code.append(params["code"][0])
                    body = _html_page(
                        "Authorization Successful",
                        "<p style='color:green'>Success</p>"
                        "<p>You can close this tab and return to the application.</p>",
                    )
                else:
                    body = _html_page(
                        "Unexpected Response", "<p>No code received. You can close this tab.</p>"
                    )

                encoded = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                # Set the asyncio.Event from the thread using thread-safe call
                loop.call_soon_threadsafe(done.set)

        # Bind to requested port (0 means OS picks a free port, RFC 8252 §7.3)
        server = HTTPServer((host, requested_port), CallbackHandler)
        actual_port = server.server_address[1]
        server.timeout = 1.0  # Wake up every second to check for cancellation

        # Build the redirect URI and auth URL now that we know the real port
        actual_redirect_uri = f"{scheme}://{host}:{actual_port}{callback_path}"
        self._actual_redirect_uri = actual_redirect_uri
        auth_url = self._build_authorization_url(redirect_uri=actual_redirect_uri)

        logger.info(f"OAuth callback server listening on {actual_redirect_uri}")

        def serve() -> None:
            while not done.is_set():
                server.handle_request()
            server.server_close()

        # Run the blocking server loop in a thread (asyncio.to_thread is for one-shot
        # functions, but this is a long-running loop that needs to run until done)
        thread = Thread(target=serve, daemon=True)
        thread.start()

        if open_browser:
            try:
                webbrowser.open(auth_url)
                logger.info("Opened browser for authorization")
            except Exception as e:
                logger.warning(f"Failed to open browser: {e}")
                logger.info(f"Please visit: {auth_url}")
        else:
            logger.info(f"Please visit: {auth_url}")

        with contextlib.suppress(asyncio.TimeoutError):
            # Timeout is handled below by checking if received_code is empty
            await asyncio.wait_for(done.wait(), timeout=self.config.timeout)

        thread.join(timeout=2)

        if error_info:
            raise RuntimeError(f"OAuth authorization error: {error_info[0]}")
        if not received_code:
            raise RuntimeError(
                f"OAuth callback timed out — no authorization code received within {self.config.timeout} seconds"
            )

        logger.info("Authorization code captured automatically from callback")
        return received_code[0]

    async def exchange_code_for_token(self, code: str) -> OAuthToken:
        """Exchange authorization code for access token.

        Args:
            code: Authorization code from OAuth callback

        Returns:
            OAuth token

        Raises:
            RuntimeError: If token exchange fails
        """
        if not self._code_verifier:
            raise RuntimeError("Code verifier not set. Call authorize() first.")

        async with httpx.AsyncClient() as client:
            # Use the redirect_uri that was actually sent to the authorization
            # server (may differ from config due to dynamic port selection).
            redirect_uri_used = self._actual_redirect_uri or self.config.redirect_uri
            normalized_redirect_uri = redirect_uri_used.rstrip("/")

            data = {
                "grant_type": "authorization_code",
                "code": code.strip(),
                "redirect_uri": normalized_redirect_uri,
                "client_id": self.config.client_id,
                "code_verifier": self._code_verifier,
            }

            if self.config.client_secret:
                data["client_secret"] = self.config.client_secret

            try:
                response = await client.post(self.config.token_endpoint, data=data)
                response.raise_for_status()
                token_data = response.json()

                logger.info("Token exchange successful!")

                return OAuthToken(
                    access_token=token_data["access_token"],
                    token_type=token_data.get("token_type", "Bearer"),
                    expires_in=token_data.get("expires_in"),
                    refresh_token=token_data.get("refresh_token"),
                )
            except httpx.HTTPStatusError as e:
                error_detail = e.response.text if e.response else str(e)
                error_json = None
                try:
                    if e.response:
                        error_json = e.response.json()
                except Exception:
                    pass

                logger.error(
                    f"Token exchange failed: {e.response.status_code}, Request data: {data}, Response: {error_detail}",
                )
                if error_json:
                    logger.error(f"Error details: {error_json}")

                # Provide helpful error message for common issues
                error_msg = f"Failed to exchange authorization code for token (HTTP {e.response.status_code})"
                if error_json and error_json.get("error") == "invalid_grant":
                    error_msg += (
                        "\nThe authorization code may have expired or already been used. "
                        "Authorization codes are typically valid for only a few minutes. "
                        "Please try the OAuth flow again to get a fresh code."
                    )
                else:
                    error_msg += f": {error_detail}"

                raise RuntimeError(error_msg) from e

    async def complete_flow(self, open_browser: bool = True) -> OAuthToken:
        """Complete full OAuth flow: authorize and exchange code for token.

        Args:
            open_browser: Whether to automatically open browser

        Returns:
            OAuth token

        Raises:
            RuntimeError: If OAuth flow fails
        """
        try:
            logger.info("Starting OAuth authorization...")
            code = await self.authorize(open_browser=open_browser)
            logger.info(f"Authorization code received (length: {len(code)})")
            logger.info("Exchanging authorization code for token...")
            token = await self.exchange_code_for_token(code)
            logger.info("OAuth flow completed successfully")
            return token
        except Exception as e:
            logger.error(f"OAuth flow failed: {e}")
            raise


def _get_redirect_uri(redirect_uri: str) -> str:
    """Get redirect URI, handling port 0 by returning it as-is.

    Args:
        redirect_uri: Redirect URI (may have port 0 for dynamic assignment)

    Returns:
        Redirect URI unchanged (port 0 is handled by the actual server binding)
    """
    # If port is specified and not 0, use it as-is
    parsed = urlparse(redirect_uri)
    if parsed.port is not None and parsed.port != 0:
        return redirect_uri
    # For port 0, return as-is - the actual server will bind and use that port
    # Registration should happen after server binding or be skipped for port 0
    return redirect_uri


async def handle_mcp_oauth(
    server_url: str,
    redirect_uri: str = "http://127.0.0.1:0/callback",
    client_id: str | None = None,
    scope: str | None = None,
    open_browser: bool = True,
) -> OAuthToken:
    """Handle OAuth flow for MCP server.

    Args:
        server_url: MCP server URL
        redirect_uri: OAuth redirect URI
        client_id: OAuth client ID (if not provided, may be discovered from server)
        scope: OAuth scopes
        open_browser: Whether to automatically open browser

    Returns:
        OAuthToken object containing the access token and metadata

    Raises:
        RuntimeError: If OAuth flow fails or endpoints cannot be discovered
    """

    well_known_paths = [
        ".well-known/oauth-authorization-server",
        ".well-known/oauth-protected-resource",  # MCP spec
    ]
    auth_endpoint = None
    token_endpoint = None
    registration_endpoint = None
    discovered_client_id = client_id

    async with httpx.AsyncClient() as client:
        # First, try discovery from the MCP server URL
        for path in well_known_paths:
            try:
                url = f"{server_url}/{path}"
                response = await client.get(url, timeout=5.0)
                if response.status_code == 200:
                    data = response.json()
                    authorization_servers = data.get("authorization_servers") or []
                    if authorization_servers:
                        auth_server = authorization_servers[0]
                        auth_endpoint = auth_server + "/authorize" if auth_server else None
                        token_endpoint = auth_server + "/token" if auth_server else None
                        registration_endpoint = auth_server + "/register" if auth_server else None
                    discovered_client_id = (
                        discovered_client_id or data.get("client_id") or data.get("clientId")
                    )
                    break
            except Exception:
                continue

    client_secret = None
    if not discovered_client_id and registration_endpoint:
        # For dynamic registration with port 0, we need to bind the server first
        # to get the actual port. However, to avoid TOCTOU race, we'll skip
        # registration if port is 0 and let the server handle it, or register
        # after the server is bound in _capture_code_via_local_server.
        # For now, skip registration if port is 0 to avoid the race condition.
        parsed = urlparse(redirect_uri)
        if parsed.port == 0:
            logger.warning(
                "Skipping dynamic client registration: port 0 requires server binding first. "
                "The server will bind to a dynamic port during authorization."
            )
        else:
            registration_redirect_uri = _get_redirect_uri(redirect_uri)
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        registration_endpoint,
                        json={"redirect_uris": [registration_redirect_uri]},
                        timeout=10.0,
                    )
                    if response.status_code == 201 or response.status_code == 200:
                        data = response.json()
                        discovered_client_id = data.get("client_id")
                        client_secret = data.get("client_secret")
                        logger.info(f"Successfully registered OAuth client: {discovered_client_id}")
                        # Update redirect_uri to the one used for registration
                        redirect_uri = registration_redirect_uri
                    else:
                        logger.warning(
                            f"Client registration failed: {response.status_code} {response.text}"
                        )
            except Exception as e:
                logger.warning(f"Dynamic client registration failed: {e}")

    final_client_id = discovered_client_id or client_id

    if not final_client_id:
        raise RuntimeError(
            "client_id is required but could not be discovered. "
            "Please provide oauth_client_id in .mcp.json or contact the MCP server administrator."
        )

    if auth_endpoint is None or token_endpoint is None:
        raise ValueError(
            "OAuth discovery failed: missing authorization/token endpoint. "
            "The OAuth server did not provide the required endpoints."
        )

    config = OAuthConfig(
        authorization_endpoint=auth_endpoint,
        token_endpoint=token_endpoint,
        client_id=final_client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        client_secret=client_secret,
    )

    handler = OAuthHandler(config)
    return await handler.complete_flow(open_browser=open_browser)

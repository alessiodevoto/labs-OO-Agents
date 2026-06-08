# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""MCPRegistry — agent-facing MCP server management mirroring SkillRegistry."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from nemo_oo_agents.agentdoc import hidden, spec
from nemo_oo_agents.skill import Skill, slash_command

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_MAX_TOOL_NAMES = 8


def _to_attr_name(name: str) -> str:
    """Convert a hyphenated server name to a valid Python attribute name."""
    return name.replace("-", "_")


class MCPRegistry(Skill):
    """Connect to MCP servers and surface their tools to the agent.

    Mirrors :class:`SkillRegistry`. An MCP server is *configured* (in
    ``.mcp.json`` or the TUI ``[tui.mcp_servers.*]`` config), *connected* (an
    authenticated client that lists the server's tools), and *activated* (its
    tools are listed as callable free functions in the ``<mcp>`` context
    block). A connected-but-deactivated server keeps its authenticated client
    (cached OAuth token) alive without flooding the agent's context — same
    rationale as
    ``SkillRegistry.activate/deactivate``.

    **The ``<mcp>`` context block is the single source of truth for what is
    callable.** Each *active* server contributes one free-function line per
    tool (signature + first docstring line); the agent calls them as
    ``self.<server>.<tool>(...)``. The ``self.<server>`` attribute is always
    hidden from ``doc(self)`` so the top-level surface never bloats —
    activation changes the block, not attribute visibility.

    ## Lifecycle

    ::

        self.mcp.discovered()              # configured server names
        await self.mcp.connect(["maas"])   # open session(s), attach as self.maas
        self.mcp.connected()               # connected server names
        self.mcp.activate(["maas"])        # list maas tools in <mcp> as free functions
        self.mcp.deactivate(["maas"])      # drop from <mcp> (client stays cached)
        self.mcp.status()                  # render the <mcp> block

    ``connect``/``activate``/``deactivate`` take fnmatch globs
    (``"maas"``, ``"*"``, ``"conf-*"``).

    ## Adding a server

    Register an in-memory server entry, then connect it::

        self.mcp.register(
            "myserver",
            url="https://host/mcp",
            transport="streamable-http",
            headers={"Authorization": "Bearer ..."},
        )
        await self.mcp.connect(["myserver"])

    To persist a server, add a ``[tui.mcp_servers.<name>]`` block to
    ``.nemo_oo_agents/config.toml`` (see ``self.tui_config``) or a VS Code /
    Claude-style ``.mcp.json``; ``register`` is in-memory only.

    ## OAuth: what the AGENT can do vs. what the HUMAN must do

    HTTP servers that return 401 trigger an OAuth flow (RFC 9728 / dynamic
    client registration) on first connect. **OAuth consent is inherently a
    human action** — the agent cannot click "Approve" in a browser. Know which
    side of the line each step is on:

    **The agent CAN, unattended:**

    - ``register(...)`` a server and ``connect``/``activate``/``deactivate`` it.
    - Reconnect a server whose token is already **cached** (a prior successful
      auth in this environment) — no human needed; the cached token is reused.
    - Pass auth knobs: ``oauth_client_id``/``oauth_scope`` (pre-provisioned
      client), ``headers={"Authorization": ...}`` (static API key — no OAuth at
      all). A static-key or pre-authorized server connects fully unattended.

    **The HUMAN MUST do (agent cannot substitute):**

    - **Grant first-time OAuth consent.** On a 401 with no cached token, a real
      person has to open the consent URL, approve, and let the callback return.
      The agent can *surface* the URL (``oauth_manual=True`` prints a markdown
      link and reads the pasted code/callback) but cannot approve on the user's
      behalf. Prefer letting the human drive ``/mcp connect <name>`` so a single
      flow runs start-to-finish — OAuth codes are single-use, and a half-finished
      agent-driven flow burns the code (causing a confusing 401 on retry).
    - **Be present for the browser handoff.** ``oauth_open_browser`` (default
      True) opens the system browser; it falls back to manual when none exists.
      Either way a human completes consent.

    **Timeout / no-hang guarantee.** ``connect`` bounds the OAuth wait with
    ``oauth_timeout`` (default 180s) — a never-returning browser callback raises
    a clear ``TimeoutError`` telling the user to retry, rather than wedging the
    agent indefinitely. Pass ``on_connecting=cb`` to surface "launching browser
    for <name>..." feedback *before* the wait begins (the UI must not look
    frozen while the user is sent to a browser).

    Tokens are cached with the dynamically registered client credentials, so
    later connects in the same environment skip the prompt — the *second* connect
    is something the agent can do unattended.

    ## Developer API

    ``MCPManager`` (``nemo_oo_agents.mcp``) stays a stateless, dependency-free
    factory — library code calls ``MCPManager.create_from_server(...)``
    directly. This registry wraps it to hold connection/activation state for an
    agent.
    """

    __nosnapshot__ = True
    context_block = ("mcp", "self.mcp.status()")

    _agent: Annotated[Any, hidden] = None

    @slash_command(
        "mcp",
        argument_hint="<list|connect|disconnect> [server]",
        completions=("list", "connect", "disconnect"),
        output_to_agent=False,
    )
    async def mcp_command(
        self,
        action: Literal["list", "connect", "disconnect"] = "list",
        server: str = "",
    ) -> str:
        """Manage MCP servers: list / connect / disconnect.

        /mcp list               — show configured/connected/active servers
        /mcp connect <server>   — open a session (and activate) a server
        /mcp disconnect <server> — close a connected server

        Output goes to you (the user) only — it does not spend an agent turn.
        """
        if action == "list":
            return self.status()

        if not server:
            return f"Usage: /mcp {action} <server>"

        if action == "connect":
            if server not in self.discovered():
                return f"Server '{server}' not found. Try /mcp list."
            try:
                connected = await self.connect([server])
            except Exception as exc:
                return f"Failed to connect '{server}': {exc}"
            if not connected:
                return f"'{server}' is already connected.\n\n{self.status()}"
            return f"Connected '{server}'.\n\n{self.status()}"

        # disconnect
        if server not in self.connected():
            return f"'{server}' is not connected. Try /mcp list."
        try:
            await self.disconnect([server])
        except Exception as exc:
            return f"Failed to disconnect '{server}': {exc}"
        return f"Disconnected '{server}'.\n\n{self.status()}"

    @slash_command(
        "mcp-add",
        argument_hint="<server info: name, URL, transport, auth notes>",
        output_to_agent=True,
    )
    async def mcp_add_command(self, args: str) -> str:
        """Add a new MCP server: hand the details to the agent to wire it up.

        The user pastes whatever they have about a server — a name and URL, a
        ``claude mcp add ...`` line, a docs snippet, an OAuth client id, etc.
        This does NOT edit anything itself; it returns a task for the agent,
        which reads the details, writes the ``[tui.mcp_servers.<name>]`` block in
        ``.nemo_oo_agents/config.toml``, and guides the user through connecting
        (OAuth/host-browser handoff as needed).
        """
        details = args.strip()
        if not details:
            return (
                "Usage: /mcp-add <server info>\n"
                "Paste a name + URL (and transport/auth notes), or a "
                "`claude mcp add ...` line, e.g.:\n"
                "  /mcp-add maas-gdrive https://maas.prd.astra.nvidia.com/maas/gdrive/mcp "
                "streamable-http"
            )
        config_path = self._config_path()
        configured = ", ".join(self.discovered()) or "(none)"
        return (
            "The user wants to add a new MCP server. Here are the details they provided:\n\n"
            f"{details}\n\n"
            "Do the following:\n"
            "1. Parse the server name, URL, transport (default `streamable-http` for HTTP "
            "URLs), and any auth info (OAuth client_id, static API key/headers).\n"
            f"2. Add a `[tui.mcp_servers.<name>]` block to the TUI config at `{config_path}` "
            "(create the file/section if missing; do NOT clobber existing servers). Use "
            "`headers` for a static API key, or `oauth_client_id` for a pre-provisioned "
            "OAuth client. Don't set `oauth_manual` unless the server requires OOB.\n"
            "3. Tell the user to connect it with `/mcp connect <name>` (or do it via "
            "`self.mcp.register(...)` + `self.mcp.connect(['<name>'])` if testing now), and "
            "explain the OAuth step if the server needs it (browser consent / host-browser "
            "handoff for headless).\n"
            "4. Confirm what you wrote and show the resulting config block.\n\n"
            f"Currently configured servers: {configured}.\n"
            f"Config file: {config_path}."
        )

    def _config_path(self) -> Path:
        """Return the TUI config.toml path (project dir)."""
        from nemo_oo_agents.paths import get_project_dir

        return get_project_dir("config.toml")

    def __init__(
        self,
        mcp_file: Path | None = None,
        servers: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Initialize with optional config sources.

        Args:
            mcp_file: Path to a VS Code / Claude-style ``.mcp.json``.
            servers: Inline server config (from TUI ``[tui.mcp_servers.*]``).
        """
        self._mcp_file = mcp_file
        self._servers: dict[str, dict[str, Any]] = dict(servers or {})
        self._connected: dict[str, Any] = {}
        self._activated: set[str] = set()
        # Servers whose connect() is currently in-flight — guards against a
        # retry spawning a second concurrent create_from_server (and second
        # browser window / racing token-cache write) while the first to_thread
        # is still running after a wait_for timeout (the thread keeps going).
        self._pending: set[str] = set()
        super().__init__()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discovered(self) -> list[str]:
        """All configured server names (``.mcp.json`` + inline + registered).

        Returns an empty list if the ``mcp`` package is unavailable (the TUI
        package depends on it, but ``MCPManager`` lives in core where it stays
        optional), so the ``<mcp>`` context block renders cleanly instead of
        leaking an ``ImportError`` every turn.
        """
        try:
            from nemo_oo_agents.mcp import MCPManager
        except ImportError:
            return []
        return sorted(MCPManager.list_servers(self._mcp_file, servers=self._servers))

    def register(
        self,
        name: str,
        *,
        url: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        transport: Literal["stdio", "sse", "streamable-http"] | None = None,
        headers: dict[str, str] | None = None,
        oauth_client_id: str | None = None,
        oauth_scope: str | None = None,
        oauth_open_browser: bool | None = None,
        oauth_manual: bool | None = None,
    ) -> None:
        """Add an in-memory server entry (not persisted to config).

        Use ``url``/``headers``/``transport`` for HTTP servers, or
        ``command``/``args``/``env`` for stdio servers. To persist, add a
        ``[tui.mcp_servers.<name>]`` block to the TUI config instead.
        """
        entry: dict[str, Any] = {}
        for key, val in (
            ("url", url),
            ("command", command),
            ("args", args),
            ("env", env),
            ("transport", transport),
            ("headers", headers),
            ("oauth_client_id", oauth_client_id),
            ("oauth_scope", oauth_scope),
            ("oauth_open_browser", oauth_open_browser),
            ("oauth_manual", oauth_manual),
        ):
            if val is not None:
                entry[key] = val
        self._servers[name] = entry

    # ------------------------------------------------------------------
    # Connecting
    # ------------------------------------------------------------------

    def connected(self) -> list[str]:
        """Currently connected server names."""
        return sorted(self._connected)

    async def connect(
        self,
        patterns: list[str],
        *,
        oauth_code_prompt: Callable[[str], Awaitable[str]] | None = None,
        oauth_timeout: float = 180.0,
        on_connecting: Callable[[str], None] | None = None,
        activate: bool = True,
        **kwargs: Any,
    ) -> list[str]:
        """Connect to configured servers matching *patterns*.

        Each connected server is attached to the agent as ``self.<name>``
        (hyphens become underscores), hidden from ``doc(self)``. By default
        newly connected servers are also activated (listed in ``<mcp>``); pass
        ``activate=False`` to keep them connected but unlisted.

        Extra kwargs (``oauth_client_id``, ``oauth_manual``, ...) are forwarded
        to ``MCPManager.create_from_server``, preserving its OAuth behavior.

        Returns the list of server names connected by this call.
        """
        from nemo_oo_agents.mcp import MCPManager

        matched = self._match(patterns, set(self.discovered()))
        newly: list[str] = []
        for name in sorted(matched):
            if name in self._connected:
                continue
            # In-flight guard: prevent a retry from starting a SECOND concurrent
            # connect for the same server while a prior attempt is still waiting
            # on OAuth — that would race token-cache writes, duplicate client
            # registrations, or open two browser windows.
            if name in self._pending:
                raise RuntimeError(
                    f"Connect to {name!r} is already in progress (a prior attempt "
                    f"may still be waiting on OAuth). Wait for it to finish or time "
                    f"out before retrying."
                )
            # Feedback BEFORE the (possibly long, browser-launching) OAuth wait —
            # otherwise the UI looks frozen while the user is sent to a browser.
            if on_connecting is not None:
                on_connecting(name)
            self._pending.add(name)
            try:
                # Outer to_thread keeps the prompt_toolkit UI loop painting during
                # OAuth waits (see !373). The OAuth wait itself is bounded by a
                # SINGLE authoritative timeout owned by the OAuth layer: the local
                # callback server polls every 1s and exits on it, so there is no
                # orphaned thread — we pass oauth_timeout straight down rather than
                # stacking a second wait_for here.
                create_kwargs = dict(kwargs)
                if oauth_timeout is not None:
                    create_kwargs["oauth_timeout"] = oauth_timeout
                tool = await asyncio.to_thread(
                    MCPManager.create_from_server,
                    name,
                    mcp_file=self._mcp_file,
                    servers=self._servers,
                    oauth_code_prompt=oauth_code_prompt,
                    **create_kwargs,
                )
            finally:
                self._pending.discard(name)
            self._attach(name, tool)
            newly.append(name)
        if activate and newly:
            self.activate(newly)
        return newly

    async def disconnect(self, patterns: list[str]) -> list[str]:
        """Close connected servers matching *patterns* and detach them.

        Returns the list of server names disconnected.
        """
        matched = self._match(patterns, set(self._connected))
        for name in sorted(matched):
            self.deactivate([name])
            self._detach(name)
        return sorted(matched)

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def activated(self) -> list[str]:
        """Currently activated (listed in ``<mcp>``) server names."""
        return sorted(self._activated)

    def activate(self, patterns: list[str]) -> None:
        """List connected servers' tools as free functions in ``<mcp>``."""
        matched = self._match(patterns, set(self._connected))
        self._activated.update(matched)

    def deactivate(self, patterns: list[str]) -> None:
        """Drop a connected server's tools from ``<mcp>`` (session stays open)."""
        matched = self._match(patterns, set(self._activated))
        self._activated.difference_update(matched)

    # ------------------------------------------------------------------
    # Status / context block
    # ------------------------------------------------------------------

    def status(self) -> str:
        """Render the ``<mcp>`` block, mirroring ``SkillRegistry.status()``.

        Three sections of uniform ``self.<attr>   <one-line summary>`` rows so a
        server is never invisible while its client is still alive:

        * **Active** — connected and listed for the agent (callable now).
        * **Connected (inactive)** — session/client still open, hidden from the
          agent until re-activated.
        * **Configured** — known but not connected; how to connect.

        Matches the ``<skills>`` block so the two read identically.
        """
        configured = self.discovered()
        if not configured:
            return "No MCP servers configured."

        lines: list[str] = []

        active = [n for n in configured if n in self._activated]
        if active:
            lines.append(
                "Active MCP servers (use via self.<attr>, docs via doc(self.<attr>),"
                " deactivate via self.mcp.deactivate(['name'])):"
            )
            for name in active:
                attr = _to_attr_name(name)
                lines.append(f"  self.{attr:22s} {self._server_summary(name)}")

        inactive = [n for n in configured if n in self._connected and n not in self._activated]
        if inactive:
            if lines:
                lines.append("")
            lines.append(
                "Connected but inactive (client/token still live;"
                " activate with self.mcp.activate(['name'])):"
            )
            for name in inactive:
                attr = _to_attr_name(name)
                lines.append(f"  self.{attr:22s} {self._server_summary(name)}")

        available = [n for n in configured if n not in self._connected]
        if available:
            if lines:
                lines.append("")
            lines.append("Configured MCP servers (connect with self.mcp.connect(['name'])):")
            for name in available:
                lines.append(f"  {name:24s} {self._server_summary(name)}")

        return "\n".join(lines)

    def _server_summary(self, name: str) -> str:
        """One-line summary for a server row (tool names if connected, else endpoint)."""
        tool = self._connected.get(name)
        if tool is not None:
            method_names = sorted(getattr(type(tool), "_tool_method_names", ()) or [])
            if not method_names:
                method_names = sorted(
                    m
                    for m in dir(type(tool))
                    if not m.startswith("_")
                    and callable(getattr(tool, m, None))
                    and getattr(getattr(type(tool), m, None), "__qualname__", "").startswith(
                        type(tool).__name__
                    )
                )
            n = len(method_names)
            shown = ", ".join(method_names[:_MAX_TOOL_NAMES])
            extra = n - min(n, _MAX_TOOL_NAMES)
            tools = shown + (f", +{extra} more" if extra > 0 else "")
            return f"{tools} ({n} tool{'s' if n != 1 else ''})" if n else "(no tools)"
        entry = self._servers.get(name, {})
        url = entry.get("url")
        if url:
            transport = entry.get("transport", "streamable-http")
            return f"{url} ({transport})"
        command = entry.get("command")
        if command:
            return f"stdio: {command}"
        return "(from .mcp.json)"

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def __getitem__(self, name: str) -> Any:
        """Access a connected server's tool by name."""
        if name not in self._connected:
            raise KeyError(f"MCP server {name!r} is not connected")
        return self._connected[name]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _match(patterns: list[str], names: set[str]) -> set[str]:
        """fnmatch *patterns* against *names* (same semantics as SkillRegistry)."""
        matched: set[str] = set()
        for pat in patterns:
            matched.update(n for n in names if fnmatch.fnmatch(n, pat))
        return matched

    def _attach(self, name: str, tool: Any) -> None:
        """Attach a connected tool to the agent, hidden from doc(self)."""
        attr = _to_attr_name(name)
        self._connected[name] = tool
        if self._agent is not None:
            setattr(self._agent, attr, tool)
            try:
                spec(self._agent, attr, hidden=True)
            except Exception:
                logger.debug("Failed to hide MCP attr self.%s", attr, exc_info=True)

    def _detach(self, name: str) -> None:
        """Detach a connected tool from the agent."""
        attr = _to_attr_name(name)
        self._connected.pop(name, None)
        if self._agent is not None and hasattr(self._agent, attr):
            try:
                delattr(self._agent, attr)
            except AttributeError:
                pass

    def attach(self, agent: Any) -> None:
        """Wire into the agent and self-register the ``<mcp>`` context block."""
        self._agent = agent
        cm = getattr(agent, "context_manager", None)
        if cm is not None and self.context_block:
            key, expr = self.context_block
            if key not in cm.protected_keys:
                cm.set_dynamic(key, expr)

    def detach(self) -> None:
        """Disconnect all servers and remove the context block."""
        for name in list(self._connected):
            self.deactivate([name])
            self._detach(name)
        agent = self._agent
        if agent is not None and self.context_block:
            cm = getattr(agent, "context_manager", None)
            key = self.context_block[0]
            if cm is not None and key in cm and key not in cm.protected_keys:
                cm.pop(key, None)
        self._agent = None

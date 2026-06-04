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

    ## OAuth & headless

    HTTP servers that return 401 trigger an OAuth flow (RFC 9728 / dynamic
    client registration) on first connect. Pass auth knobs to ``register`` or
    ``connect``:

    - ``oauth_client_id`` / ``oauth_scope`` — explicit client credentials.
    - ``oauth_manual=True`` — out-of-band flow: print a markdown auth link and
      read the pasted authorization code or full callback URL. Use this on
      headless hosts with no system browser.
    - ``oauth_open_browser`` — auto-open the system browser (default True);
      falls back to manual when no browser is available.

    Tokens are cached with the dynamically registered client credentials, so
    later connects in the same environment skip the prompt.

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
            # Outer to_thread keeps the prompt_toolkit UI loop painting during
            # OAuth waits (see !373); create_from_server itself is sync and may
            # spawn its own executor when a loop is already running.
            tool = await asyncio.to_thread(
                MCPManager.create_from_server,
                name,
                mcp_file=self._mcp_file,
                servers=self._servers,
                oauth_code_prompt=oauth_code_prompt,
                **kwargs,
            )
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

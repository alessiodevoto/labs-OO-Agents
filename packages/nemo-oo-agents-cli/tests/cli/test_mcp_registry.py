# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for MCPRegistry — agent-facing MCP server management."""

import pytest
from nemo_oo_agents_cli.tui.mcp_registry import MCPRegistry

from nemo_oo_agents.agentdoc._visibility import is_hidden_field
from nemo_oo_agents.runtime.context_manager import ContextManager


class _FakeTool:
    """Stand-in for a dynamically generated MCPTool subclass."""

    _tool_method_names = frozenset({"search", "get_page"})

    async def search(self, query: str) -> object:
        """Search the knowledge base."""

    async def get_page(self, page_id: str) -> object:
        """Fetch a page by id."""

    async def _call_tool(self, *a, **k): ...


class _FakeAgent:
    def __init__(self):
        self.context_manager = ContextManager()


def _make(servers=None, mcp_file=None, attach=True):
    reg = MCPRegistry(mcp_file=mcp_file, servers=servers)
    agent = _FakeAgent()
    if attach:
        reg.attach(agent)
    return reg, agent


def _fake_create(monkeypatch, tool_factory=_FakeTool):
    """Patch MCPManager.create_from_server to return a fresh fake tool."""
    calls = []

    def _create(name, **kwargs):
        calls.append((name, kwargs))
        return tool_factory()

    import nemo_oo_agents.mcp as mcp_mod

    monkeypatch.setattr(mcp_mod.MCPManager, "create_from_server", staticmethod(_create))
    return calls


# ---------------------------------------------------------------------------
# Discovery / register
# ---------------------------------------------------------------------------


def test_discovered_unions_inline_and_file(tmp_path):
    mcp_file = tmp_path / ".mcp.json"
    mcp_file.write_text('{"mcpServers": {"fileserver": {"url": "https://f/mcp"}}}')
    reg, _ = _make(servers={"inline": {"url": "https://i/mcp"}}, mcp_file=mcp_file)
    assert reg.discovered() == ["fileserver", "inline"]


def test_discovered_dedups_name_collision(tmp_path):
    mcp_file = tmp_path / ".mcp.json"
    mcp_file.write_text('{"mcpServers": {"maas": {"url": "https://file/mcp"}}}')
    reg, _ = _make(servers={"maas": {"url": "https://inline/mcp"}}, mcp_file=mcp_file)
    assert reg.discovered() == ["maas"]


def test_register_adds_in_memory_entry():
    reg, _ = _make()
    assert reg.discovered() == []
    reg.register("foo", url="https://foo/mcp", transport="streamable-http")
    assert reg.discovered() == ["foo"]


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_attaches_and_activates(monkeypatch):
    calls = _fake_create(monkeypatch)
    reg, agent = _make(servers={"maas": {"url": "https://x/mcp"}})
    newly = await reg.connect(["maas"])
    assert newly == ["maas"]
    assert reg.connected() == ["maas"]
    assert reg.activated() == ["maas"]
    assert hasattr(agent, "maas")
    assert calls[0][0] == "maas"


@pytest.mark.asyncio
async def test_connect_without_activate(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "https://x/mcp"}})
    await reg.connect(["maas"], activate=False)
    assert reg.connected() == ["maas"]
    assert reg.activated() == []


@pytest.mark.asyncio
async def test_connect_is_idempotent(monkeypatch):
    calls = _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "https://x/mcp"}})
    await reg.connect(["maas"])
    again = await reg.connect(["maas"])
    assert again == []
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_connect_glob(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"conf-a": {"url": "a"}, "conf-b": {"url": "b"}, "other": {"url": "c"}})
    newly = await reg.connect(["conf-*"])
    assert newly == ["conf-a", "conf-b"]


@pytest.mark.asyncio
async def test_connect_forwards_oauth_kwargs(monkeypatch):
    calls = _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "https://x/mcp"}})

    async def prompt(url):
        return "code"

    await reg.connect(["maas"], oauth_code_prompt=prompt, oauth_manual=True)
    _, kwargs = calls[0]
    assert kwargs["oauth_code_prompt"] is prompt
    assert kwargs["oauth_manual"] is True
    assert kwargs["servers"] == {"maas": {"url": "https://x/mcp"}}


# ---------------------------------------------------------------------------
# Activate / deactivate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_deactivate_membership(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "x"}})
    await reg.connect(["maas"], activate=False)
    assert reg.activated() == []
    reg.activate(["maas"])
    assert reg.activated() == ["maas"]
    reg.deactivate(["maas"])
    assert reg.activated() == []


@pytest.mark.asyncio
async def test_deactivate_does_not_close_session(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "x"}})
    await reg.connect(["maas"])
    tool = reg["maas"]
    reg.deactivate(["maas"])
    reg.activate(["maas"])
    assert reg["maas"] is tool
    assert reg.connected() == ["maas"]


@pytest.mark.asyncio
async def test_activate_glob(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"a": {"url": "x"}, "b": {"url": "y"}})
    await reg.connect(["*"], activate=False)
    reg.activate(["*"])
    assert reg.activated() == ["a", "b"]


# ---------------------------------------------------------------------------
# Visibility — self.<server> stays hidden from doc(self) either way
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connected_attr_is_hidden_from_doc(monkeypatch):
    _fake_create(monkeypatch)
    reg, agent = _make(servers={"maas": {"url": "x"}})
    await reg.connect(["maas"])  # activated by default
    assert is_hidden_field(agent, "maas") is True
    reg.deactivate(["maas"])
    assert is_hidden_field(agent, "maas") is True


# ---------------------------------------------------------------------------
# Status / <mcp> block
# ---------------------------------------------------------------------------


def test_status_empty():
    reg, _ = _make()
    assert reg.status() == "No MCP servers configured."


def test_status_configured_only():
    reg, _ = _make(servers={"maas": {"url": "x"}})
    out = reg.status()
    assert "Configured MCP servers" in out
    assert "self.mcp.connect(['name'])" in out
    assert "  maas" in out


@pytest.mark.asyncio
async def test_status_connected_inactive(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "x"}})
    await reg.connect(["maas"], activate=False)
    out = reg.status()
    assert "Connected but inactive" in out
    assert "self.maas" in out
    assert "search" in out  # tool names summarized
    assert "search(" not in out  # but no full signatures


@pytest.mark.asyncio
async def test_status_active_lists_tools_as_free_functions(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "x"}})
    await reg.connect(["maas"])
    out = reg.status()
    assert "Active MCP servers" in out
    assert "self.maas" in out
    # tool names are summarized on the server row (docs via doc(self.maas))
    assert "search" in out
    assert "get_page" in out
    assert "(2 tools)" in out


@pytest.mark.asyncio
async def test_status_precedence_active_over_connected(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "x"}})
    await reg.connect(["maas"])
    out = reg.status()
    # an active server appears once, in the Active section (not Configured/inactive)
    assert "Active MCP servers" in out
    assert "self.maas" in out
    assert "Configured MCP servers" not in out
    assert "Connected but inactive" not in out


@pytest.mark.asyncio
async def test_status_truncates_many_tools(monkeypatch):
    many = {f"tool_{i}" for i in range(50)}

    class _BigTool:
        _tool_method_names = frozenset(many)

    def _make_methods():
        pass

    _fake_create(monkeypatch, tool_factory=_BigTool)
    reg, _ = _make(servers={"big": {"url": "x"}})
    await reg.connect(["big"])
    out = reg.status()
    assert "more" in out
    assert "(50 tools)" in out


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_removes_attr_and_state(monkeypatch):
    _fake_create(monkeypatch)
    reg, agent = _make(servers={"maas": {"url": "x"}})
    await reg.connect(["maas"])
    out = await reg.disconnect(["maas"])
    assert out == ["maas"]
    assert reg.connected() == []
    assert reg.activated() == []
    assert not hasattr(agent, "maas")


# ---------------------------------------------------------------------------
# attach / detach + context block
# ---------------------------------------------------------------------------


def test_attach_registers_context_block():
    reg, agent = _make(servers={"maas": {"url": "x"}})
    assert "mcp" in agent.context_manager


@pytest.mark.asyncio
async def test_detach_disconnects_and_removes_block(monkeypatch):
    _fake_create(monkeypatch)
    reg, agent = _make(servers={"maas": {"url": "x"}})
    await reg.connect(["maas"])
    reg.detach()
    assert "mcp" not in agent.context_manager
    assert reg.connected() == []


def test_getitem_raises_when_not_connected():
    reg, _ = _make(servers={"maas": {"url": "x"}})
    with pytest.raises(KeyError):
        reg["maas"]


@pytest.mark.asyncio
async def test_status_three_state_inactive_section(monkeypatch):
    """A deactivated-but-connected server stays visible in its own section."""
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "x"}, "jira": {"url": "y"}})
    await reg.connect(["maas"])  # active
    await reg.connect(["jira"], activate=False)  # connected, inactive
    out = reg.status()
    assert "Active MCP servers" in out
    assert "Connected but inactive" in out
    assert "self.maas" in out
    assert "self.jira" in out


@pytest.mark.asyncio
async def test_deactivate_moves_to_inactive_section(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "x"}})
    await reg.connect(["maas"])
    assert "Active MCP servers" in reg.status()
    reg.deactivate(["maas"])
    out = reg.status()
    assert "Connected but inactive" in out
    assert "Active MCP servers" not in out


@pytest.mark.asyncio
async def test_mcp_slash_command_list_returns_status(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "x"}})
    out = await reg.mcp_command("list")
    assert isinstance(out, str)
    assert "maas" in out


def test_mcp_slash_command_is_user_only():
    """The /mcp command is marked output_to_agent=False (output to user, no agent turn)."""
    from nemo_oo_agents.skill import get_slash_commands

    reg, _ = _make(servers={"maas": {"url": "x"}})
    meta = next(m for m, _ in get_slash_commands(reg) if m.name == "mcp")
    assert meta.output_to_agent is False


@pytest.mark.asyncio
async def test_mcp_slash_command_connect(monkeypatch):
    _fake_create(monkeypatch)
    reg, _ = _make(servers={"maas": {"url": "x"}})
    out = await reg.mcp_command("connect", "maas")
    assert "Connected 'maas'" in out
    assert reg.connected() == ["maas"]


@pytest.mark.asyncio
async def test_mcp_add_slash_command_returns_agent_task(monkeypatch, tmp_path):
    """/mcp-add hands the server details to the agent (output_to_agent=True)."""
    monkeypatch.setenv("NEMO_OO_PROJECT_DIR", str(tmp_path))
    reg, _ = _make(servers={"maas": {"url": "x"}})
    out = await reg.mcp_add_command(
        "maas-gdrive https://maas.prd.astra.nvidia.com/maas/gdrive/mcp streamable-http"
    )
    assert "maas-gdrive" in out
    assert "tui.mcp_servers" in out
    assert "config.toml" in out
    # Includes the currently-configured servers for context.
    assert "maas" in out


@pytest.mark.asyncio
async def test_mcp_add_slash_command_empty_shows_usage():
    reg, _ = _make(servers={})
    out = await reg.mcp_add_command("")
    assert "Usage: /mcp-add" in out


def test_mcp_add_slash_command_outputs_to_agent():
    from nemo_oo_agents.skill import get_slash_commands

    reg, _ = _make(servers={})
    meta = next(m for m, _ in get_slash_commands(reg) if m.name == "mcp-add")
    assert meta.output_to_agent is True

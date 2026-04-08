# ruff: noqa: F403,F405
"""Quickstart 11: MCP tools — connect external MCP servers as agent tools. Requires: uv sync --extra mcp

uv run python examples/quickstart/11_mcp.py
"""

try:
    from mcp_nemo_oo_agents import MCPManager
except ImportError as e:
    raise ImportError("mcp-nemo-oo-agents not installed. uv sync --extra mcp") from e

from nemo_oo_agents.util.quickstart import *


class ConfluenceAgent(Agent, llm=llm):
    """Agent with MCP tool access."""

    confluence_tool = MCPManager.create_from_server("maas-confluence-stg")

    async def respond(self, prompt: str) -> str:
        """Respond to a user message using the Confluence MCP tool."""
        ...


@autorun
async def main():
    agent = ConfluenceAgent()
    result = await agent.respond("What are the best practices for claude code?")
    print(result)

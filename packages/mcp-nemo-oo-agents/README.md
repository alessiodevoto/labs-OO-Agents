# mcp-nemo-oo-agents

MCP (Model Context Protocol) tool integration for Agent006.

## Overview

**mcp-nemo-oo-agents** connects Agent006 agents to MCP servers. It discovers the tools exposed by a server, generates typed Python methods for each one, and returns a ready-to-use object that agents can assign as a class attribute — no boilerplate required.

## Features

- **Dynamic method generation** — Python methods with type hints and docstrings are created automatically from each server's tool schema
- **Type safety** — JSON schema types and validation constraints (min/max, enums, patterns) are reflected in method signatures and docstrings
- **Multiple transports** — stdio, SSE, and streamable-http
- **OAuth support** — automatic interactive OAuth flow on 401 responses
- **Config-driven** — reads `.mcp.json` for zero-argument usage; all values can be overridden in code
- **Async-ready** — generated methods are `async`; `MCPManager.create_from_server()` is synchronous and safe to call at class definition time

## Installation

```bash
uv pip install mcp-nemo-oo-agents
```

Or from the nemo_oo_agents workspace:

```bash
uv sync --extra mcp
```

## Quick Start

```python
from nemo_oo_agents import Agent
from mcp_nemo_oo_agents import MCPManager
from unifiedllm.registry import get_llm_client

llm = get_llm_client("nvidia/nvidia/Nemotron-3-Nano-30B-A3B")

class MyAgent(Agent, llm=llm):
    # Connects to the server and generates methods at class definition time
    language_server = MCPManager.create_from_server("language-server")

    async def find_definition(self, filepath: str, line: int) -> str:
        """Find the definition of the symbol at the given location."""
        return await self.language_server.definition(filepath=filepath, line=line)
```

## MCPManager

`MCPManager` is the entry point for connecting to MCP servers.

### `MCPManager.create_from_server(server_name, ...)`

Connect to an MCP server and return a tool instance with one method per tool.

```python
# Load config from .mcp.json
tool = MCPManager.create_from_server("language-server")

# Override or provide config explicitly
tool = MCPManager.create_from_server(
    "maas-gitlab",
    url="https://maas.example.com/gitlab/mcp",
    transport="streamable-http",
    oauth_client_id="your-client-id",
)
```

**Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `server_name` | `str` | Server name (used to look up config in `.mcp.json`) |
| `command` | `str \| None` | Command to run (stdio transport) |
| `args` | `list[str] \| None` | Command arguments (stdio transport) |
| `env` | `dict[str, str] \| None` | Environment variables (stdio transport) |
| `url` | `str \| None` | Server URL (SSE / streamable-http transport) |
| `headers` | `dict[str, str] \| None` | HTTP headers (streamable-http transport) |
| `transport` | `"stdio" \| "sse" \| "streamable-http" \| None` | Transport type |
| `oauth_client_id` | `str \| None` | OAuth client ID |
| `oauth_redirect_uri` | `str` | OAuth redirect URI (default: `"http://localhost:8000/callback"`) |
| `oauth_scope` | `str \| None` | OAuth scope |
| `oauth_open_browser` | `bool` | Open browser automatically for OAuth (default: `True`) |
| `mcp_file` | `Path \| None` | Path to `.mcp.json` (default: `.mcp.json` in cwd) |

### `MCPManager.list_servers(mcp_file)`

Return the names of all servers defined in `.mcp.json`.

```python
servers = MCPManager.list_servers()
print(servers)  # ["maas-confluence-stg", "langfuse", "language-server"]
```

## Configuration

Define servers in a `.mcp.json` file at your project root:

```json
{
  "mcpServers": {
    "language-server": {
      "command": "node",
      "args": ["path/to/language-server.js"],
      "transport": "stdio"
    },
    "maas-confluence-stg": {
      "url": "https://maas.example.com/confluence/mcp",
      "transport": "streamable-http",
      "oauth_client_id": "your-client-id",
      "oauth_scope": "read write"
    }
  }
}
```

Parameters passed directly to `create_from_server()` take precedence over `.mcp.json` values.

## OAuth

OAuth is handled automatically. When the server returns a `401`, `MCPManager` triggers the interactive flow, obtains a token, and retries the connection — no additional code needed:

```python
agent.confluence = MCPManager.create_from_server(
    "maas-confluence-stg",
    oauth_open_browser=True,  # default
)
```

## MCPTool

`MCPTool` is the base class for all generated tool instances. You can also subclass it directly for custom implementations:

```python
from mcp_nemo_oo_agents import MCPTool

class LanguageServerTool(MCPTool):
    async def definition(self, filepath: str, line: int):
        return await self._call_tool("definition", {"filepath": filepath, "line": line})
```

## How It Works

1. `MCPManager.create_from_server()` reads `.mcp.json` and merges any explicit parameters
2. Creates an `MCPBaseClient` for the chosen transport (stdio / SSE / streamable-http)
3. Connects to the server and calls `list_tools()` (triggers OAuth if needed)
4. Parses JSON schema for each tool, extracting types and validation constraints
5. Generates a dynamic subclass of `MCPTool` with one `async` method per tool
6. Returns an instance ready to use as an agent attribute

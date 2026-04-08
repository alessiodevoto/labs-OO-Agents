# MCP Integration

## Goal

Add a reusable way for agents to use MCP (Model Context Protocol) tools. From an agent's standpoint, there should be no difference between using a coded tool or an MCP tool. Connection management is handled separately from tool usage.

## Architecture

### MCPTool: Base Class for Tool Instances

The `MCPTool` class (`packages/mcp-agent006/src/mcp_agent006/tool.py`) is the base class for MCP tool instances:

**Instance Management:**
- Each `MCPTool` instance manages its own MCP connection (1-1 mapping between tool and client)
- Instances are created via the `MCPManager` class
- Each instance holds a reference to its client, server name, and tool specifications

**Dynamic Class Generation:**
- When `MCPManager.create_from_server()` is called, it dynamically generates a class with one method per MCP tool
- Methods are properly typed with Python type hints extracted from JSON schema
- Method docstrings include parameter descriptions and validation constraints

**Two Usage Patterns:**
1. **Auto-generated class** (recommended): `tool = MCPManager.create_from_server("language-server")`
   - Dynamically creates methods for all tools on the server
   - Full type safety and documentation
   - Works when called on base `MCPTool` class

2. **Custom child class**: `class LanguageServerTool(MCPTool): ...`
   - Manually define specific methods you need
   - Useful when you want to wrap or customize specific tools
   - Create instances directly: `LanguageServerTool(client, server_name, tool_specs)`
   - Returns instance of the child class (not dynamically generated)

**Tool Instance Lifecycle:**
- Each tool instance manages its own connection
- Deleting a tool instance (`del agent.tool`) closes its connection
- Multiple tool instances for the same server create separate connections

### MCPManager: Manager for Tool Creation

The `MCPManager` class (`packages/mcp-agent006/src/mcp_agent006/tool.py`) handles connection and tool creation:

**Connection Lifecycle:**
- **Configuration**: Servers can be configured via `.mcp.json` file or passed directly to `create_from_server()`
- **Connection Creation**: Connections are created on-demand when `MCPManager.create_from_server()` is called
- **Session Management**: Each tool call creates a new session via `connect_to_server()` context manager
- **OAuth Handling**: OAuth flow is automatically triggered on 401 responses, tokens are included in headers

**Connection Process:**
1. `MCPManager.create_from_server()` loads configuration from `.mcp.json` (if server name provided)
2. Creates an `MCPBaseClient` (stdio, SSE, or streamable-http transport)
3. Tests connection by calling `list_tools()` (handles OAuth flow if 401)
4. Parses tool specifications and generates dynamic class
5. Returns `MCPTool` instance with dynamically generated methods

**Supported Transports:**
- **stdio**: Spawns local process, communicates via stdin/stdout
- **sse**: Server-Sent Events (legacy, no auth support)
- **streamable-http**: HTTP transport with OAuth support (recommended for MAAS MCPs)

**Configuration Sources:**
- `.mcp.json` file: Persistent configuration loaded automatically
- Direct parameters: Passed to `from_server()` method (takes precedence over config file)
- OAuth tokens: Obtained via interactive flow, included in request headers

## Usage

### Basic Usage

```python
from mcp_agent006 import MCPManager

# List available servers from .mcp.json
servers = MCPManager.list_servers()
print(servers)  # ["maas-confluence-stg", "langfuse", ...]

# Create tool instance (connects automatically, loads from .mcp.json)
agent.language_server = MCPManager.create_from_server("language-server")

# Use tool methods (dynamically generated from MCP server)
await agent.language_server.definition(filepath="src/main.py", line=10)
await agent.language_server.find_references(filepath="src/main.py", line=10)
```

### With Custom Configuration

```python
# Connect with explicit parameters (overrides .mcp.json)
agent.gitlab = MCPManager.create_from_server(
    "maas-gitlab",
    url="https://maas.example.com/gitlab/mcp",
    transport="streamable-http",
    oauth_client_id="your-client-id"
)
```

### With OAuth

```python
# OAuth is handled automatically on 401 responses
# Browser opens automatically (can be disabled with oauth_open_browser=False)
agent.confluence = MCPManager.create_from_server(
    "maas-confluence-stg",
    oauth_redirect_uri="http://localhost:8000/callback",
    oauth_open_browser=True  # Default: True
)
```

## Implementation Details

**Connection Management:**
- Each `MCPTool` instance manages its own connection
- Connections are created synchronously in `MCPManager.create_from_server()` (handles async internally)
- Each tool call creates a new session via `connect_to_server()` context manager
- OAuth tokens are obtained interactively and included in request headers

**Error Handling:**
- Connection errors (network, DNS, timeouts) propagate to caller
- OAuth flow automatically triggered on 401 responses
- Protocol errors (MCP initialization failures) raise `RuntimeError`
- Multiple exceptions are grouped into `ExceptionGroup` when appropriate

**Tool Discovery:**
- Tools are discovered by calling `list_tools()` on the MCP server
- Tool metadata (name, description, input schema) is parsed from MCP protocol
- JSON schema is converted to Python types and validation constraints
- Dynamic class generation creates one method per tool with proper type hints

**Synchronous Manager:**
- `MCPManager.create_from_server()` is synchronous (returns immediately)
- Internally handles async operations using thread pool or `asyncio.run()`
- Can be called from both sync and async contexts

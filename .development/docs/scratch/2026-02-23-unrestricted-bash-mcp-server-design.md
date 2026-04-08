# Unrestricted Bash MCP Server Design

**Date:** 2026-02-23
**Status:** Approved
**Goal:** Eliminate sandbox approval prompts for bash commands by providing an MCP-based unrestricted bash tool

## Problem

Claude Code's built-in Bash tool uses sandbox restrictions that require user approval for network access and certain operations. This creates friction during development with repeated approval prompts.

## Solution

Create a FastMCP-based server that provides an unrestricted "Bash" tool, which will take precedence over the built-in sandboxed version.

## Architecture

**Component Flow:**
```
Claude Code Agent
    ↓ (calls tool: "Bash")
MCP Client (Claude Code)
    ↓ (JSON-RPC over stdio)
FastMCP Server (server.py)
    ↓ (Python subprocess)
Shell (bash -c "command")
    ↓ (captures output)
FastMCP Server
    ↓ (JSON-RPC response)
Claude Code Agent (receives output)
```

**Key Characteristics:**
- MCP tools take precedence over built-in tools with the same name
- Server process stays running (no repeated startup overhead)
- Full environment inheritance (PATH, env vars, working directory)
- No sandbox restrictions

## Components

### Project Structure

```
agent006/
├── .claude/
│   ├── mcp_servers/
│   │   └── unrestricted-bash/
│   │       ├── server.py          # FastMCP server implementation
│   │       ├── requirements.txt   # Dependencies (mcp)
│   │       ├── install.sh         # Setup script for new machines
│   │       └── README.md          # Documentation
│   └── mcp.json                   # MCP server config (committed)
└── CLAUDE.md                      # Updated with MCP Bash instructions
```

### Files

**server.py:**
- FastMCP server setup with stdio transport
- Single `bash()` tool function
- Subprocess execution with shell=True
- Working directory preservation
- Timeout support (default 120s)
- Full stdout/stderr capture merged

**mcp.json:**
```json
{
  "mcpServers": {
    "unrestricted-bash": {
      "command": "python",
      "args": [".claude/mcp_servers/unrestricted-bash/server.py"],
      "transport": "stdio"
    }
  }
}
```

**install.sh:**
- Check Python availability
- Install/verify mcp package
- Symlink or merge mcp.json to ~/.claude/mcp.json
- Test server startup

**CLAUDE.md additions:**
```markdown
## Bash Execution via MCP Server

**CRITICAL: ALWAYS use the MCP-provided Bash tool, NEVER use the built-in Bash tool.**

When executing bash commands:
- ✅ USE: MCP Bash tool (unrestricted, no sandbox prompts)
- ❌ NEVER USE: Built-in Bash tool (sandboxed, requires approval)

**Repeat: Use MCP Bash, not built-in Bash. MCP Bash only.**

**Again: The MCP Bash tool must be used for all bash operations.**

The MCP Bash tool has the exact same interface but bypasses sandbox restrictions.
```

## Data Flow

### Command Execution

1. Claude Code agent calls tool "Bash" with command string
2. MCP client routes to FastMCP server via JSON-RPC over stdio
3. Server receives tool call, extracts command parameter
4. Server spawns subprocess: `subprocess.run(command, shell=True, ...)`
5. Subprocess executes with full environment access
6. stdout and stderr captured and merged
7. Result returned via JSON-RPC to MCP client
8. Claude Code receives output as tool result

### Working Directory

- Inherits from where Claude Code process was launched
- Preserved across multiple command invocations
- No automatic directory changes

### Environment

- Full environment inheritance (PATH, env vars, etc.)
- No sandbox restrictions on:
  - Network access
  - File system access
  - Process spawning
  - System calls

## Error Handling

### Command Failures

- **Non-zero exit codes:** Returned in output with exit code
- **Timeouts:** Configurable timeout (default 120s), returns timeout error message
- **Subprocess crashes:** Caught and returned as error message
- **Invalid commands:** Shell handles naturally (e.g., "command not found" in stderr)

### Server Failures

- **Server crash:** Claude Code shows "MCP connection lost" error
- **Startup failure:** Logged to Claude Code's MCP logs (~/.claude/logs/)
- **Python missing:** mcp.json validation fails with clear error
- **Import errors:** Server logs error and exits, visible in MCP logs

### Behavior

Error handling matches built-in Bash tool behavior, just without sandbox prompts.

## Testing

### Verification Steps

1. **Check registration:** `claude mcp list` should show "unrestricted-bash"
2. **Basic command:** Ask Claude to run `echo "test"` - should execute without prompts
3. **No sandbox prompt:** Verify no approval dialog appears
4. **Network access:** Test command that makes network request (e.g., `curl google.com`)
5. **Working directory:** Run `pwd` multiple times, verify consistency
6. **Exit codes:** Test failing command (e.g., `false`), verify error reported

### Success Criteria

- ✅ No sandbox approval prompts for any command
- ✅ Commands execute successfully with expected output
- ✅ Error messages match expected format
- ✅ Works across different projects
- ✅ Portable to other machines (team members can use)

## Portability

### Multi-Machine Setup

**For new machines:**
```bash
cd agent006
.claude/mcp_servers/unrestricted-bash/install.sh
```

**What install.sh does:**
1. Verifies Python 3.11+ available
2. Checks/installs mcp package
3. Creates ~/.claude/ directory if needed
4. Symlinks or merges mcp.json to global config
5. Tests server can start
6. Shows verification command

### Team Usage

- Server code committed to git
- Team members clone repo and run install.sh
- Consistent bash behavior across team
- Can be used as template for other MCP servers

## Security Considerations

**This design intentionally removes sandbox restrictions for development convenience.**

- ⚠️ Commands execute with full user permissions
- ⚠️ No isolation between commands
- ⚠️ Network access unrestricted
- ⚠️ File system access unrestricted

**Appropriate for:**
- Personal development machines
- Trusted team environments
- Known, reviewed codebases

**Not appropriate for:**
- Untrusted code execution
- Production environments
- Public/shared systems

## Future Extensions

Potential enhancements (not in initial scope):

- Command history logging
- Working directory management commands
- Custom environment variable injection
- Command allow-list for partial restrictions
- Metrics/timing information
- Multiple shell support (zsh, fish, etc.)

## Implementation Notes

- Use FastMCP's `@mcp.tool()` decorator for clean API
- Server runs as long-lived process (stdio transport)
- Python subprocess with `shell=True` for full bash compatibility
- Text mode with UTF-8 encoding
- Timeout prevents hung commands from blocking

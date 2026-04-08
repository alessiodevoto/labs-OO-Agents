# trace_explorer

Programmatic interface for exploring `.jsonl` files. Designed for agent-driven trace analysis.

## Installation

```bash
uv pip install -e packages/trace_explorer
```

## Usage

### As a Library

```python
from trace_explorer import TraceExplorer

# Load a trace file
trace = TraceExplorer.from_file("path/to/trace.jsonl")

# Get help
print(trace.help())

# Get overview
print(trace.get_overview())

# List sessions
sessions = trace.get_session_list()
print(sessions[0])

# Drill into a session
print(trace.get_session("abc123"))

# Get specific turn details
print(trace.get_turn("abc123", 0))
```

### Command Line

```bash
# Show overview
trace-explorer path/to/trace.jsonl

# Show help
trace-explorer --help

# Show specific session
trace-explorer path/to/trace.jsonl --session abc123

# Show specific turn
trace-explorer path/to/trace.jsonl --session abc123 --turn 0

# Show errors
trace-explorer path/to/trace.jsonl --errors

# Search for pattern
trace-explorer path/to/trace.jsonl --search "pattern"

# Compare traces (structured JSON)
trace-explorer path/to/trace.jsonl --diff other.jsonl --json

# Select root generation when multiple roots exist
trace-explorer path/to/trace.jsonl --root-generation 1

# Structured JSON output for any command
trace-explorer path/to/trace.jsonl --session abc123 --json
```

## API

See `TraceExplorer.help()` for the full API guide.

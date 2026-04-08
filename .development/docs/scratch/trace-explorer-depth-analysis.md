# Trace Explorer: Session Depth Analysis Feature

## Overview

Added session depth analysis to `trace_explorer` to detect recursive agent invocation loops and nested session patterns that may not be visible in parsed traces.

## Feature

### Command Usage

```bash
python -m e2e_optimization.trace_explorer <trace_file> --depth
```

Or using the short flag:
```bash
python -m e2e_optimization.trace_explorer <trace_file> -d
```

### What It Does

1. **Counts raw AGENT spans** in the trace file (from OpenTelemetry data)
2. **Counts parsed sessions** (from TraceExplorer)
3. **Detects mismatches** that indicate:
   - Recursive agent invocation loops
   - Deep nesting that's being hidden by parser limitations
   - Agent calling itself without proper termination

### Example Output

```
# Session Depth Analysis

**Total AGENT spans in trace**: 108
**Root AGENT spans**: 108
**Nested AGENT spans**: 0
**Parsed sessions**: 1

⚠️  **RECURSION WARNING**: 108.0x more AGENT spans than parsed sessions

This indicates:
- Deep nesting of agent calls (possible recursive invocation loop)
- Nested sessions are not being parsed (parser limitation)
- Agent may be recursively calling itself without proper termination
```

## Implementation

### TraceExplorer Enhancement

Added `get_session_depth_analysis()` method to [TraceExplorer](/Volumes/dev/dev/nemo_oo_agents/util/e2e_optimization/src/e2e_optimization/navigable_trace.py#L819):

```python
def get_session_depth_analysis(self) -> str:
    """Analyze session nesting depth and detect potential recursion issues."""
    # Counts raw AGENT spans in trace file
    # Compares to parsed session count
    # Returns formatted analysis with warnings
```

### Trace Explorer Integration

Added `--depth` / `-d` flag to [trace_explorer.py](/Volumes/dev/dev/nemo_oo_agents/util/e2e_optimization/src/e2e_optimization/trace_explorer.py#L59):

```python
parser.add_argument("--depth", "-d", action="store_true",
                   help="Analyze session nesting depth and recursion")
```

## Use Cases

### 1. Detecting Recursive Agent Loops

**Problem**: Agent recursively calls itself (e.g., `self.call_agent()` in a loop) without proper termination, causing timeouts and resource exhaustion.

**Detection**: Large ratio of AGENT spans to parsed sessions (e.g., 100:1)

**Example**: [NeedleTestWrapper trace](/Volumes/dev/dev/nemo_oo_agents/results/capability_20260108_102810/traces/NeedleTestWrapper_gemini-2.5-flash-lite_call_agent_needle_in_haystack_20260108_102810_03_000000.006trace.jsonl)
- 108 AGENT spans
- 1 parsed session
- 108x ratio → clear recursion

### 2. Understanding Parser Limitations

**Problem**: Trace parser only extracts root sessions (see [trace_to_markdown.py:220](/Volumes/dev/dev/nemo_oo_agents/util/e2e_optimization/src/e2e_optimization/trace_to_markdown.py#L220)), hiding nested agent calls.

**Detection**: Depth analysis reveals hidden nested sessions

**Impact**: Can miss important nested agent behavior in analysis

### 3. Diagnosing Execution Timeouts

**Problem**: Trace shows timeout error but root cause isn't clear

**Solution**: Run depth analysis to see if recursion is the culprit

## Related Documentation

- [Recursive Agent Invocation Loop Bug](/Volumes/dev/dev/nemo_oo_agents/docs/bugs/recursive-agent-invocation-loop.md)
- [Trace Explorer CLI](/Volumes/dev/dev/nemo_oo_agents/util/e2e_optimization/src/e2e_optimization/trace_explorer.py)
- [TraceExplorer API](/Volumes/dev/dev/nemo_oo_agents/util/e2e_optimization/src/e2e_optimization/navigable_trace.py)

## Future Enhancements

Potential improvements:
1. **Call depth histogram**: Show distribution of nesting levels
2. **Call chain visualization**: ASCII tree showing parent-child relationships
3. **Hotspot detection**: Identify which agents/methods are creating most nesting
4. **Mechanical check**: Auto-detect and flag in mechanical_checks framework

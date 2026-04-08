# RCA: repl_exploration Regression Analysis

**Date**: 2026-01-27
**Test Type**: repl_exploration
**Regression**: Pass rate dropped from 70.0% to 61.7% (-8.3%)

## Executive Summary

The regression is caused by the removal of semantic framing in execution output messages. The branch replaced the explicit "Execution successful.\nStdout:\n" prefix with structured fields rendered directly, losing critical context that helped LLMs understand they were in a multi-step exploration process.

## 1. Observed Behavior Changes

### Failure Pattern
- **Main (passing)**: 14 turns, calls `self.secret_message()` after getting 4th riddle answer
- **Branch (failing)**: 9 turns, returns 4th riddle answer ("English") directly without calling `self.secret_message()`

### Key Difference in LLM Decision
At the critical decision point (after receiving 4th riddle "What is the name of the language you are speaking?"):

**Main trace (turn 10)**: Model sees context with "Execution successful.\nStdout:\n" prefix and continues to call `self.secret_message(fourth_riddle_answer='English')`.

**Branch trace (turn 8)**: Model sees raw output without semantic framing and immediately calls `return_result('The secret message is: Hello Agent!')` - guessing instead of exploring further.

## 2. Code Changes Identified

### PR Changes: History Context Management (commits c609b4c7, c77c12f3, 537e64a2, 9a2a4478)

**Before (Main):**
```python
# events.py
class ExecutePythonData(BaseModel):
    content: str  # Pre-formatted: "Execution successful.\nStdout:\n{output}"

# codeact.py (formatting)
parts.append("Execution successful.")
output_text = result.format_output(fenced=False)  # "Stdout:\n{stdout}"
if output_text:
    parts.append(f"\n{output_text}")
```

**After (Branch):**
```python
# events.py
class ExecutePythonEvent(EventBase):
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    value: Any = None

    def render_spec(self) -> RenderSpec:
        # Renders stdout directly without prefix
        return RenderSpec(
            tag="execute_python",
            attrs=["tool_call_id", "execution_count", "status"],
            content=["stdout"],  # Just the raw stdout
            role=Role.USER,
        )
```

### Output Format Comparison

**Main:**
```xml
<execute_python expr="self.history.events[4].content" tool_call_id="...">
Execution successful.
Stdout:
What is the capital of France?
</execute_python>
```

**Branch:**
```xml
<execute_python expr="self.history[4].stdout" tool_call_id="..." execution_count="1" status="complete">
What is the capital of France?
</execute_python>
```

## 3. Five-Whys Analysis

### Why did the pass rate drop?
The LLM prematurely terminates exploration, returning intermediate results instead of completing the multi-step task.

### Why does the LLM terminate early?
It misinterprets the 4th riddle answer as the final result, failing to recognize that `self.secret_message()` must still be called.

### Why does it misinterpret the state?
The raw output format provides insufficient semantic context about task progression. The LLM sees just "What is the name of the language you are speaking?" without clear signals that this is an intermediate step.

### Why is semantic context missing?
The refactoring replaced the explicit "Execution successful." prefix with structured metadata (XML attributes like `status="complete"`), which is less salient to the LLM.

### Why was this change made?
The goal was to create a cleaner, more structured history representation with typed fields (`stdout`, `stderr`, `value`) instead of pre-formatted strings. This is architecturally sound but lost important prompt engineering that guided LLM behavior.

## 4. Context Window Verification

Token counts confirm the task is NOT scrolling off the context window:

| Turn | Branch Tokens | Main Tokens |
|------|---------------|-------------|
| 0    | 1,918         | 1,920       |
| 2    | 2,068         | ~2,070      |
| 4    | 2,226         | ~2,230      |
| 6    | 2,395         | ~2,400      |
| 8    | 2,562         | (main continues) |

At the critical decision point (turn 8), the context is only 2,562 tokens - far below the 65,384 token limit. The `<task>` block with "Retrieve the secret message" is present and visible in the failing trace's final turn.

**Conclusion**: The regression is not due to context overflow. The LLM has full visibility of the task but misinterprets the execution outputs without semantic framing.

## 5. Impact Analysis

### Models Affected
| Model | Main | Branch | Delta |
|-------|------|--------|-------|
| gemini-2.5-flash-lite | 60% (6/10) | 10% (1/10) | **-50.0%** |
| qwen3-80b | 90% (9/10) | 70% (7/10) | -20.0% |
| gpt-oss-120b | 50% (5/10) | 40% (4/10) | -10.0% |
| nemotron3-nano-30b | 40% (4/10) | 70% (7/10) | **+30.0%** |

**Note**: nemotron3-nano-30b improved, suggesting model-specific sensitivity to prompt format.

### Failure Modes
1. **Premature return with riddle answer** ("English") - most common
2. **Fabricated response** ("The secret message is: Hello Agent!") - occasional
3. **Incomplete exploration** - missing final `secret_message()` call

## 5. Recommended Fix

### Option A: Restore Semantic Prefix (Quick Fix)
Modify `render_spec()` to include execution status in content:
```python
def render_spec(self) -> RenderSpec:
    prefix = "Execution successful.\n" if self.status == "complete" else ""
    # Prefix the stdout with semantic context
```

### Option B: Enhanced Structured Format (Preferred)
Keep structured fields but make status more prominent:
```python
def render_spec(self) -> RenderSpec:
    return RenderSpec(
        tag="execute_python",
        attrs=["tool_call_id", "execution_count"],
        content=["status", "stdout", "stderr", "error", "value"],  # Status first
        role=Role.USER,
    )
```

This would render as:
```xml
<execute_python ...>
<status>complete</status>
<stdout>What is the capital of France?</stdout>
</execute_python>
```

### Option C: Restore Full Formatting in Content
Create a computed `formatted_output` property that includes the semantic prefix for rendering while keeping structured fields for programmatic access.

## 6. Trace Explorer Feedback

### Strengths
1. **Diff feature is excellent**: The `--diff` command with `--json` output clearly identified all prompt expression differences between traces
2. **Turn-by-turn navigation**: Easy to drill into specific turns to see exact LLM context
3. **Call graph visualization**: Quickly shows which methods were called (passing trace: 7 sessions including `secret_message`; failing: 5 sessions without)
4. **Eval context in overview**: Pass/fail status immediately visible

### Areas for Improvement
1. **Content diff needed**: Currently shows expression differences (`self.history.events[0].content` vs `self.history[0].prompt`) but not actual content differences. Would be helpful to see what the LLM actually saw differently.
2. **Automatic regression detection**: Would be useful to have a command like `trace-explorer --compare-dirs dir1 dir2 --filter repl_exploration` that summarizes pass/fail counts and identifies patterns.
3. **Decision point highlighting**: For multi-turn traces, ability to highlight where behavior diverged (e.g., "turn 8: main continued exploring, branch returned early").
4. **Prompt template diff**: Show side-by-side comparison of system prompts between traces.

### Usage Notes
- The tool was effective for understanding trace structure and flow
- JSON output mode was essential for programmatic analysis
- CLI commands were intuitive and well-documented in help

## 7. Conclusion

The regression is a classic case of **semantic context loss during refactoring**. The structured field approach is cleaner architecturally but inadvertently removed prompt engineering that was load-bearing for LLM behavior.

The fix should either restore the semantic prefix or enhance the structured format to make execution status more salient. Testing should include repl_exploration specifically since it's highly sensitive to multi-step exploration signals.

---

## Appendix: Trace Locations

- Main traces: `/Volumes/dev/dev/viewer/results/capability_optimization_20260127_142631/traces/`
- Branch traces: `/Volumes/dev/dev/nemo_oo_agents/results/capability_optimization_20260127_142636/traces/`
- Filter pattern: `ReplExplorationTestAgent_*_repl_exploration_*.006trace.jsonl`

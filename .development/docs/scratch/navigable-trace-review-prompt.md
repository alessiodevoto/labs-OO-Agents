# TraceExplorer API Review Prompt

Use this prompt to conduct a comprehensive review of the TraceExplorer API outputs.

---

## Context

TraceExplorer is a programmatic interface for exploring `.006trace.jsonl` files. It's designed for **agent-driven analysis** - meaning an AI agent will use these methods to navigate large agent traces and perform root cause analysis.

The goal is to evaluate whether the API outputs are:
1. **Understandable** - Can an agent quickly grasp what the output means?
2. **Self-documenting** - Does the output explain itself without needing external docs?
3. **Actionable** - Does it provide the information needed to drill down and root cause issues?

## Your Task

Review the TraceExplorer API by testing each public method against real trace files. Create detailed feedback that will be used to improve the API.

## Setup

1. Load the TraceExplorer module:
```python
import sys
from trace_explorer import TraceExplorer
```

2. Find test trace files in the journal:
```
docs/navigable-trace-journal.md
```

3. Load a trace:
```python
trace = TraceExplorer.from_file(trace_path)
```

## Methods to Review

For each method, test with different parameters and evaluate the output:

### 1. `get_overview(concise=True)` and `get_overview(concise=False)`
- Does the call graph make sense?
- Is the status clear (passed/failed, runtime errors)?
- Are inputs/outputs truncated appropriately?
- Does concise=False provide meaningfully more detail?

### 2. `get_session(session_id, concise=True)` and `get_session(session_id, concise=False)`
- Is there enough context to understand which trace/session this is?
- Are turn summaries useful in concise mode?
- Is the full content readable in verbose mode?

### 3. `get_turn(session_id, turn_index)`
- Does it show the full LLM context window?
- Is the LLM output clearly separated from execution results?
- Can you understand why the LLM made its decision?

### 4. `get_errors()`
- Are error messages actionable (not just "status_error")?
- Is the error chain clear for cascading failures?
- Can you navigate to the source of the error?

### 5. `get_eval_context()`
- Does it show input, expected output, and actual output?
- Are scorer results clear?
- Can you understand why the eval passed/failed?

### 6. `search(pattern)`
- Are matches centered with enough context?
- Can you navigate to the location of matches?

### 7. `help()`
- Is the usage guide clear and complete?
- Would an agent know how to use the API after reading it?

## Evaluation Process

For each trace file:

1. **Load the trace** and get basic info
2. **Test each method** with both concise=True and concise=False (where applicable)
3. **Judge each output** against the three criteria:
   - Do I understand it?
   - Is it self-documenting?
   - Does it help me drill down and root cause?
4. **Document specific issues** with snippets showing the problem
5. **Suggest improvements** where applicable

## Output Format

Create a feedback document at `docs/navigable-trace-feedback.md` with:

```markdown
# TraceExplorer API Review

## Executive Summary
[2-3 sentences on overall quality and key issues]

## Method-by-Method Analysis

### get_overview()
**Sample Output (concise=True):**
```
[paste actual output]
```

**Strengths:**
- [list what works well]

**Issues:**
1. [Issue description with specific example]
2. [Another issue]

**Recommendations:**
- [Specific fix suggestions]

### [repeat for each method]

## Cross-Cutting Issues
[Issues that affect multiple methods]

## Summary of Recommendations
[Prioritized list of fixes]
```

## Tips

- Test with **diverse traces**: successful, failed, different agent types, different models
- Pay attention to **edge cases**: empty results, very long content, nested agents
- Consider **context window limits**: Would this output fit in an agent's context?
- Think about **navigation flow**: Can you easily drill down from overview → session → turn?
- Check for **consistency**: Same terminology, same formatting across methods

## Use Todo Tool

Track your progress with todos:
```
- Todo for each trace file to review
- Todo for compiling feedback document
```

---

When you complete the review, the feedback will be used to improve the TraceExplorer API for agent-based trace analysis.

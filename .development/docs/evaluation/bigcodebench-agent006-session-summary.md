# BigCodeBench agent006 Improvement Session Summary

**Date**: 2026-01-08
**Branch**: `fix/bigcodebench-adapter-improvements`

## Goal

Investigate why agent006 (47.1% pass rate) underperforms compared to direct_llm (49.6%) and react_agent (48.2%) on BigCodeBench with Qwen model, and fix framework/tooling issues.

## Fixes Applied to `experiments/evaluation-ablations/agents/agent006_tools.py`

### 1. textwrap.dedent fix for PythonCode validator (lines 52-64)

**Problem**: Qwen returns code in triple-quoted strings (`code = '''...'''`) which preserves indentation, causing `SyntaxError: unexpected indent` during validation.

**Fix**: Added `code = textwrap.dedent(code)` before `ast.parse(code)`

**Impact**: +20 tasks passing (46 empty results → 20 now pass)

### 2. Common imports for REPL sandbox (lines 21-37)

**Problem**: REPL sandbox blocks imports not in agent module namespace (e.g., `import of 'pandas' is forbidden`)

**Fix**: Added common imports at module top level with BOTH module name and alias:

```python
import matplotlib
import matplotlib.pyplot as plt
import numpy
import numpy as np
import pandas
import pandas as pd
import zoneinfo  # Also the module, not just ZoneInfo class
from zoneinfo import ZoneInfo
import pytz
```

**Key insight**: Sandbox checks module NAME (`pandas`), not alias (`pd`), so both needed.

### 3. MANDATORY TESTING prompt addition (lines 235-246)

**Problem**: Agent returns code without testing it first, missing runtime errors.

**Fix**: Added explicit instructions to test function in REPL before returning.

**Impact**: Marginal improvement, but good practice.

## Benchmark Runs in Progress

Started 6 parallel runs (all BigCodeBench, 1140 tasks each):

| Run | Provider | Model | Config |
|-----|----------|-------|--------|
| qwen_agent006 | nvidia | qwen3-next-80b-a3b-instruct | agent006 |
| qwen_direct_llm | nvidia | qwen3-next-80b-a3b-instruct | direct_llm |
| qwen_react_agent | nvidia | qwen3-next-80b-a3b-instruct | react_agent |
| gpt_agent006 | openai | gpt-4o-mini | agent006 |
| gpt_direct_llm | openai | gpt-4o-mini | direct_llm |
| gpt_react_agent | openai | gpt-4o-mini | react_agent |

**Results directories**: `results/20260108_195051_*/`

**Log files**: `/tmp/{qwen,gpt}_{agent006,direct_llm,react_agent}.log`

**Monitor script**: `/tmp/monitor_runs.sh`

### Last Progress (22:07)

```
qwen_agent006:    300/1140 (50.7%)
qwen_direct_llm:   50/1140 (54.0%)
qwen_react_agent: Starting...
gpt_agent006:     200/1140 (29.5%)
gpt_direct_llm:   550/1140 (33.5%)
gpt_react_agent:  400/1140 (37.8%)
```

## To Resume Next Session

1. Check if runs completed: `/tmp/monitor_runs.sh`
2. Look for `Summary:` lines in logs
3. Results are in `results/20260108_195051_*/`
4. If any crashed, resume with: `--resume results/20260108_195051_<hash>`

## Key Files Modified

- `experiments/evaluation-ablations/agents/agent006_tools.py` - Main agent with all fixes

## Original 21 Import-Forbidden Tasks (Not Yet Fully Tested)

These tasks were identified as failing due to sandbox import restrictions:

```
BigCodeBench/13 BigCodeBench/15 BigCodeBench/23 BigCodeBench/27 BigCodeBench/28
BigCodeBench/62 BigCodeBench/84 BigCodeBench/160 BigCodeBench/172 BigCodeBench/327
BigCodeBench/375 BigCodeBench/418 BigCodeBench/469 BigCodeBench/490 BigCodeBench/609
BigCodeBench/664 BigCodeBench/803 BigCodeBench/868 BigCodeBench/888 BigCodeBench/1076
BigCodeBench/1138
```

Task 1076 (zoneinfo) was verified fixed. The full batch test was interrupted by NVIDIA 504 timeouts - results pending from the full benchmark run.

## Plan File Reference

Full analysis plan at: `~/.claude/plans/hazy-kindling-piglet.md`

## Notes

- NVIDIA endpoint had 504 timeout issues during testing
- Runs may need to be resumed if they crashed
- The "Progress" line in logs may not reflect actual completed tasks - check actual result files

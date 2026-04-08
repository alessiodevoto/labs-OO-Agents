# Agent Directory Structure Design

**Date:** 2026-01-21
**Status:** Implementation in progress

## Overview

This document defines the directory structure for organizing agent artifacts during e2e optimization. Each agent gets its own directory containing the source code, evaluation results, and lineage metadata.

## Directory Structure

```
results/dabstep/
├── agent000/
│   ├── agent000.py                    # The agent source code
│   ├── agent000.006opt.jsonl          # Append-only event log (Pydantic-backed)
│   ├── minibatch/                     # Targeted subset runs (overwritten)
│   │   ├── latest.006eval.jsonl       # Most recent minibatch result
│   │   └── traces/
│   ├── fullbatch/                     # Full eval runs (accumulated)
│   │   ├── 20260121_150000.006eval.jsonl
│   │   ├── 20260121_160000.006eval.jsonl
│   │   └── traces/
│   └── analysis/                      # Trace analyzer output (regenerated on source change)
│       └── trace_analysis.json
│
├── agent001/                          # Child of agent000
│   ├── agent001.py
│   ├── agent001.006opt.jsonl          # parents: ["agent000"], method: "mutation"
│   └── ...
│
├── agent008/                          # Merged from 006 + 007
│   ├── agent008.py
│   ├── agent008.006opt.jsonl          # parents: ["nemo_oo_agents", "agent007"], method: "merge"
│   └── ...
```

## Event Types for .006opt.jsonl

The `.006opt.jsonl` file is an append-only log of events for the agent. Each line is a JSON object backed by a Pydantic type.

### AgentCreated

First event when an agent directory is initialized.

```json
{
  "type": "created",
  "timestamp": "2026-01-21T14:00:00Z",
  "parents": ["nemo_oo_agents", "agent007"],
  "method": "merge",
  "description": "GEPA crossover of 006 (soft) + 007 (hard)"
}
```

Fields:
- `type`: Always `"created"`
- `timestamp`: ISO 8601 timestamp
- `parents`: List of parent agent IDs (empty for baselines)
- `method`: One of `"baseline"`, `"mutation"`, `"merge"`, `"manual"`
- `description`: Human-readable description of the agent

### AgentMutation

Logged when the agent source code is modified.

```json
{
  "type": "mutation",
  "timestamp": "2026-01-21T15:00:00Z",
  "description": "Added retry logic to phase 3",
  "diff_summary": "+15/-3 lines in execute()",
  "source_hash": "sha256:abc123..."
}
```

Fields:
- `type`: Always `"mutation"`
- `timestamp`: ISO 8601 timestamp
- `description`: What changed
- `diff_summary`: Optional short summary of the diff
- `source_hash`: SHA256 hash of the agent.py file for verification

### MinibatchEval

Quick validation run on a targeted subset of tests.

```json
{
  "type": "minibatch_eval",
  "timestamp": "2026-01-21T14:05:00Z",
  "tests": ["dabstep_1273_hard", "dabstep_1753_hard"],
  "passed": 2,
  "total": 2,
  "result_file": "minibatch/latest.006eval.jsonl"
}
```

Fields:
- `type`: Always `"minibatch_eval"`
- `timestamp`: ISO 8601 timestamp
- `tests`: List of test IDs in this minibatch
- `passed`: Number of tests passed
- `total`: Total tests in minibatch
- `result_file`: Relative path to the result file

### FullbatchEval

Complete evaluation on all tests.

```json
{
  "type": "fullbatch_eval",
  "timestamp": "2026-01-21T14:30:00Z",
  "passed": 7,
  "total": 10,
  "pass_rate": 0.7,
  "result_file": "fullbatch/20260121_143000.006eval.jsonl",
  "per_test": {
    "dabstep_5_easy": true,
    "dabstep_49_easy": true,
    "dabstep_70_easy": true,
    "dabstep_1273_hard": true,
    "dabstep_1305_hard": true,
    "dabstep_1464_hard": true,
    "dabstep_1681_hard": false,
    "dabstep_1753_hard": true,
    "dabstep_1871_hard": false,
    "dabstep_2697_hard": false
  }
}
```

Fields:
- `type`: Always `"fullbatch_eval"`
- `timestamp`: ISO 8601 timestamp
- `passed`: Number of tests passed
- `total`: Total tests
- `pass_rate`: Float 0.0-1.0
- `result_file`: Relative path to the result file
- `per_test`: Dict mapping test_id to pass/fail boolean

### TraceAnalysis

Logged when traces are analyzed for failures.

```json
{
  "type": "analysis",
  "timestamp": "2026-01-21T15:01:00Z",
  "source_hash": "sha256:abc123...",
  "failing_tests": ["dabstep_1871_hard", "dabstep_2697_hard"],
  "analysis_file": "analysis/trace_analysis.json",
  "summary": "Both fail on multi-step fee calculation requiring cross-reference"
}
```

Fields:
- `type`: Always `"analysis"`
- `timestamp`: ISO 8601 timestamp
- `source_hash`: Links to which version of agent.py was analyzed
- `failing_tests`: List of test IDs that failed
- `analysis_file`: Relative path to the full analysis output
- `summary`: Brief findings from the analysis

## Behavior Rules

### Minibatch Directory
- **Overwritten** on each new minibatch run
- Only the latest result matters for quick validation
- Use when targeting specific tests to improve

### Fullbatch Directory
- **Accumulated** - keep all historical runs
- Timestamped filenames for history
- One run per agent proposal, but keep for comparison

### Analysis Directory
- **Regenerated** on each source change
- Keyed by `source_hash` to track which version was analyzed
- Do NOT reuse analysis when source changes

## Example Timeline

```
# Day 1: Create agent008 by merging 006 and 007
{"type": "created", "timestamp": "2026-01-21T14:00:00Z", "parents": ["nemo_oo_agents", "agent007"], "method": "merge", "description": "GEPA crossover"}

# Run minibatch on the tests we're trying to improve
{"type": "minibatch_eval", "timestamp": "2026-01-21T14:05:00Z", "tests": ["dabstep_1273_hard", "dabstep_1753_hard"], "passed": 2, "total": 2, "result_file": "minibatch/latest.006eval.jsonl"}

# Minibatch passed! Run fullbatch
{"type": "fullbatch_eval", "timestamp": "2026-01-21T14:30:00Z", "passed": 7, "total": 10, "pass_rate": 0.7, "result_file": "fullbatch/20260121_143000.006eval.jsonl", "per_test": {...}}

# Analyze failing tests
{"type": "analysis", "timestamp": "2026-01-21T14:35:00Z", "source_hash": "sha256:abc123", "failing_tests": ["dabstep_1681_hard", "dabstep_1871_hard", "dabstep_2697_hard"], "analysis_file": "analysis/trace_analysis.json", "summary": "1681 fails on fee enumeration edge case"}

# Day 2: Mutate to fix 1681
{"type": "mutation", "timestamp": "2026-01-22T09:00:00Z", "description": "Fixed phase 4 edge case for fee enumeration", "diff_summary": "+15/-3 lines", "source_hash": "sha256:def456"}

# Re-analyze (source changed)
{"type": "analysis", "timestamp": "2026-01-22T09:05:00Z", "source_hash": "sha256:def456", "failing_tests": ["dabstep_1681_hard"], "analysis_file": "analysis/trace_analysis.json", "summary": "Still failing - different error path"}

# Run minibatch on 1681 specifically
{"type": "minibatch_eval", "timestamp": "2026-01-22T09:10:00Z", "tests": ["dabstep_1681_hard"], "passed": 1, "total": 1, "result_file": "minibatch/latest.006eval.jsonl"}

# Success! Run fullbatch
{"type": "fullbatch_eval", "timestamp": "2026-01-22T09:40:00Z", "passed": 8, "total": 10, "pass_rate": 0.8, "result_file": "fullbatch/20260122_094000.006eval.jsonl", "per_test": {...}}
```

## Implementation Location

- Pydantic types: `util/e2e_optimization/src/e2e_optimization/agent_types.py`
- Helper functions: `util/e2e_optimization/src/e2e_optimization/agent_directory.py`
- Integration with run_ablation.py: `experiments/evaluation-ablations/run_ablation.py`

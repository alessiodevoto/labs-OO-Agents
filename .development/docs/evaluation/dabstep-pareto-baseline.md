# DABStep Pareto Baseline Results

**Date:** 2026-01-20
**Model:** aws/anthropic/bedrock-claude-sonnet-4-5-v1 (reasoning_effort=high)
**Training samples:** 10 (DABStep dev set, first 10)

## Summary

| Rank | Agent | Architecture | Pass Rate | Notes |
|------|-------|-------------|-----------|-------|
| **1** | **007** | 8-phase hard decomposition | **6/10 (60%)** | **NEW LEADER** |
| 2 | 000 | Single agent, basic CodeAct | 5/10 (50%) | Previous best |
| 2 | 001 | Multi-step workflow | 5/10 (50%) | Tied |
| 2 | 003 | Single agent, 3p-style prompts | 5/10 (50%) | Tied |
| 2 | 006 | 8-phase soft decomposition | 5/10 (50%) | Tied |
| 6 | 002 | 3-subagent (RulesLawyer, Verifier) | 3/10 (30%) | |
| 6 | 005 | 3-subagent + improved prompts | 3/10 (30%) | |
| 8 | 004 | 3-subagent + regex | 2/10 (20%) | Worst performer |

## Per-Sample Results

| Sample | 000 | 001 | 002 | 003 | 004 | 005 | 006 | 007 | Notes |
|--------|-----|-----|-----|-----|-----|-----|-----|-----|-------|
| dabstep_5_easy | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | All pass |
| dabstep_49_easy | FAIL | PASS | PASS | FAIL | PASS | PASS | PASS | PASS | Differential |
| dabstep_70_easy | FAIL | FAIL | PASS | FAIL | FAIL | FAIL | **PASS** | **PASS** | 002, 006, 007 pass |
| dabstep_1273_hard | PASS | FAIL | FAIL | PASS | FAIL | PASS | FAIL | **PASS** | Differential |
| dabstep_1305_hard | PASS | PASS | FAIL | PASS | FAIL | FAIL | FAIL | **PASS** | Differential |
| dabstep_1464_hard | FAIL | PASS | FAIL | FAIL | FAIL | FAIL | **PASS** | **PASS** | 001, 006, 007 pass |
| dabstep_1681_hard | PASS | PASS | FAIL | PASS | FAIL | FAIL | FAIL | FAIL | Differential |
| dabstep_1753_hard | PASS | FAIL | FAIL | PASS | FAIL | FAIL | **PASS** | FAIL | Differential |
| dabstep_1871_hard | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | All fail |
| dabstep_2697_hard | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | All fail |

## Key Observations

### 1. 8-Phase Decomposition Works Best
Agent 007 (8-phase hard decomposition) achieves **60%** - the new leader! The 8-phase approach outperforms both simple single-agent and complex 3-subagent architectures.

### 2. Not Applicable Handling Improved
- dabstep_70_easy ("Not Applicable" question) now solved by 3 agents: 002, 006, 007
- The 8-phase decomposition agents (006, 007) both handle this correctly

### 3. Fee ID Question Breakthrough
- dabstep_1464_hard (448 fee IDs) now solved by 3 agents: 001, 006, 007
- Both 8-phase agents crack this difficult enumeration task

### 4. Agent 007's Unique Wins
Agent 007 passes 1273_hard and 1305_hard where 006 fails, while 006 passes 1753_hard where 007 fails.

### 5. Consistent Failures
- dabstep_1871_hard: All 8 agents fail
- dabstep_2697_hard: All 8 agents fail
- dabstep_1681_hard: Only original agents (000, 001, 003) pass; all newer agents fail

### 6. High-Value Differential Samples
Best samples for optimization are those where some agents pass and others fail:
- dabstep_49_easy: Most pass; 000, 003 fail
- dabstep_70_easy: 002, 006, 007 pass; others fail
- dabstep_1273_hard: 000, 003, 005, 007 pass; 001, 002, 004, 006 fail
- dabstep_1305_hard: 000, 001, 003, 007 pass; 002, 004, 005, 006 fail
- dabstep_1464_hard: 001, 006, 007 pass; others fail
- dabstep_1681_hard: 000, 001, 003 pass; others fail
- dabstep_1753_hard: 000, 003, 006 pass; others fail

## Recommendations for E2E Optimizer

1. **Start with Agent 007** as baseline - best performer at 60%
2. **Analyze why 007 fails 1681_hard and 1753_hard** - original agents pass these
3. **Combine strengths**: 006 passes 1753_hard, 007 passes 1273_hard/1305_hard
4. **Focus on consistently failing samples** (1871_hard, 2697_hard) - may need new approaches

## Agent Architectures

| Agent | Architecture Description |
|-------|-------------------------|
| 000 | Basic CodeActStrategy single agent |
| 001 | Multi-step workflow with rule finding and validation |
| 002 | 3-subagent: RulesLawyer + DABStepAgent + SolutionVerifier |
| 003 | Single agent with 3p data explorer style prompts |
| 004 | 3-subagent + regex-based section finding |
| 005 | 3-subagent + improved prompts from solution analysis |
| 006 | 8-phase soft decomposition (guidance-based) |
| 007 | 8-phase hard decomposition (Pydantic-enforced) |

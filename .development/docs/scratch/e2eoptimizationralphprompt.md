# Tau-Bench Ralph Loop Prompt

**Created:** 2026-02-19
**Purpose:** Ralph Loop prompt for iterative optimization of tau-bench retail agent

## Prompt

```
/ralph-loop "
You are optimizing an agent for the tau-bench retail benchmark. The goal is to maximize pass rate on tau-bench retail tasks.

## Current State
- Agent files: experiments/evaluation-ablations/agents/tau_bench_agent.py (baseline)
- Adapter: evaluation/adapters/tau_bench.py
- Environment: evaluation/environments/tau_bench.py
- Analysis doc: docs/benchmark-optimization-comparison.md
- Baseline: ~40% pass rate

## Iteration Naming
Each iteration gets its own file. Check which iterations already exist:
  ls experiments/evaluation-ablations/agents/tau_bench_opt*.py
Copy the LATEST opt file (or baseline if none exist) to the next number:
  cp agents/tau_bench_opt{N-1}.py agents/tau_bench_opt{N}.py
Then edit tau_bench_opt{N}.py with your change. NEVER modify previous iterations.

## What To Do Each Iteration

1. Read the latest agent file and any previous results in experiments/evaluation-ablations/results/
2. Read docs/tau-bench-ralph-loop-log.md for history of what worked and what didn't
3. Identify ONE targeted improvement based on failure patterns (do NOT make broad changes)
4. Copy latest agent to new opt file and apply your change
5. Run evaluation:
   cd experiments/evaluation-ablations && source ../../.venv/bin/activate && python run_ablation.py --benchmark tau_bench_retail --provider nvidia_internal --model aws/anthropic/bedrock-claude-sonnet-4-5-v1 --agent-file agents/tau_bench_opt{N}.py --limit 10 --runs 3
6. Record results: update docs/tau-bench-ralph-loop-log.md with iteration number, change made, score, and which tasks passed/failed
7. Git commit with message: 'opt(tau-bench): opt{N} - <description of change> - <score>%'

## Key Lessons From DABStep (DO NOT IGNORE)
- Make ONE small targeted change per iteration. Broad rewrites cause regressions.
- Model choice >> prompt engineering. If baseline is bad, try switching models first.
- Simplicity >> complexity. Don't add 8-phase decomposition. Minimal scaffolding.
- Track per-task scores. If a previously passing task fails, REVERT to previous opt file.
- Competing constraints cascade. Adding guidance for one task can break others.
- Fix infrastructure before prompts. If tools aren't working, no prompt will help.
- ALWAYS base your next iteration on the BEST scoring opt file, not necessarily the latest.

## Known Failure Modes To Address (from our analysis)
- Agent gathers info but doesn't execute final action (state transition failure)
- Authentication not done before actions (policy violation)
- Missing explicit user confirmation before DB modifications
- Tool called with wrong argument types or missing required args

## Output <promise>TAU BENCH OPTIMIZED</promise> when you achieve >= 55% pass rate sustained across 3 consecutive runs, OR after 15 iterations if you've plateaued.
" --max-iterations 15 --completion-promise "TAU BENCH OPTIMIZED"
```

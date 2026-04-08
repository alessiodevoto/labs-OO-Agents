# evaluation/ — Benchmark Evaluation Framework

Benchmark-agnostic framework for measuring agent performance. Adapters, environments, protocol, metrics.

## Running Evaluations

The main entry point is `experiments/evaluation-ablations/run_ablation.py` (not in this directory — see "Architecture Note" below).

```bash
cd experiments/evaluation-ablations
source ../../.venv/bin/activate

# Direct LLM baseline (no agent)
python run_ablation.py --provider nvidia_internal --model aws/anthropic/bedrock-claude-sonnet-4-5-v1 --benchmark bfcl --limit 10

# With an agent006 agent
python run_ablation.py --agent-file agents/dabstep_agent008.py --benchmark dabstep --limit 5

# DABStep (always use Claude via nvidia_internal)
python run_ablation.py --provider nvidia_internal --model aws/anthropic/bedrock-claude-sonnet-4-5-v1 --benchmark dabstep
```

## Key Commands

```bash
# Single test file
pytest evaluation/tests/ -v

# Type check
python -m mypy evaluation/ --ignore-missing-imports
```

## Structure

```
evaluation/
├── protocol.py          # Core: Task, EvalResult, BenchmarkAdapter ABC, BenchmarkEnvironment ABC
├── runner.py            # SelfImprovementRunner (iterative eval loop)
├── task_runner.py       # Per-task execution logic
├── metrics.py           # MetricsCalculator
├── concurrency.py       # Parallel execution (async + subprocess)
├── trace_analyzer.py    # Failure pattern extraction from traces
├── agent_adapter.py     # Bridges agent006 agents with the eval framework
├── environments/        # Interactive execution environments
│   ├── tau_bench.py     # TAU-bench (Docker, simulated user)
│   ├── intercode.py     # InterCode (Docker SQL/Bash)
│   ├── swebench.py      # SWE-bench (GitHub repos)
│   └── single_step.py   # Wrapper for single-shot benchmarks
└── adapters/            # One per benchmark
    ├── bfcl.py          # Berkeley Function Calling Leaderboard
    ├── dabstep.py       # DABStep data analysis
    ├── tau_bench.py     # TAU-bench tool-augmented tasks
    └── ...              # See README.md for full list
```

## Adding a New Benchmark

1. Create `adapters/new_benchmark.py`
2. Implement `BenchmarkAdapter`:
   - `get_tasks(split, limit)` — load from HuggingFace/GitHub
   - `format_for_agent(task)` — convert to agent input
   - `evaluate(task, agent_output)` — score the result
3. If interactive (multi-turn): also implement `BenchmarkEnvironment` in `environments/`
4. Register in `adapters/__init__.py:ADAPTER_REGISTRY`

## Adding a New Agent

Agent files go in `experiments/evaluation-ablations/agents/`:

```python
# agents/my_agent.py
from agent006 import Agent, strategy
from agent006.strategies import CodeActStrategy

class MyBenchmarkAgent(Agent, llm=llm):
    @strategy(CodeActStrategy(max_iterations=30))
    async def solve(self, question: str) -> Answer:
        """Solve {question}. ..."""
        ...
```

Run with: `python run_ablation.py --agent-file agents/my_agent.py --benchmark <name>`

## Architecture Note

`run_ablation.py` lives in `experiments/evaluation-ablations/` rather than `evaluation/` because it carries experiment-specific concerns (result directories, agent files, debug scripts, analysis tooling). The `evaluation/` package is the reusable framework; `experiments/evaluation-ablations/` is the experiment harness. A future refactor could move the runner into `evaluation/` with proper CLI entry point.

## Providers

| Provider | Endpoint | Key Env Var | Example Model |
|----------|----------|-------------|---------------|
| `openai` | OpenAI API | `OPENAI_API_KEY` | `gpt-4o-mini` |
| `nvidia` | integrate.api.nvidia.com | `NVIDIA_API_KEY` | `qwen/qwen3-next-80b-a3b-instruct` |
| `nvidia_internal` | inference-api.nvidia.com | `NVIDIA_INTERNAL_API_KEY` | `aws/anthropic/bedrock-claude-sonnet-4-5-v1` |

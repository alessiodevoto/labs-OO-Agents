# Evaluation Framework

Benchmark-agnostic evaluation framework for measuring agent performance across multiple LLM benchmarks.

## Architecture

```
evaluation/
├── __init__.py          # Public API exports
├── protocol.py          # Core data structures (Task, EvalResult, BenchmarkAdapter ABC)
├── runner.py            # SelfImprovementRunner for iterative evaluation
├── metrics.py           # MetricsCalculator for scoring and analysis
├── trace_analyzer.py    # TraceAnalyzer for failure pattern extraction
├── environments/        # Interactive execution environments
│   ├── intercode.py     # InterCode SQL/Bash Docker environments
│   ├── tau_bench.py     # TAU-bench simulated user environments
│   ├── swebench.py      # SWE-bench GitHub repository environments
│   └── single_step.py   # Wrapper for single-shot benchmarks
└── adapters/            # Benchmark-specific implementations
    ├── bfcl.py          # Berkeley Function Calling Leaderboard
    ├── bigcodebench.py  # BigCodeBench code generation
    ├── dabstep.py       # DABStep data analysis (Adyen)
    ├── gaia.py          # GAIA multi-step reasoning
    ├── intercode.py     # InterCode SQL/Bash interactive coding
    ├── livecodebench.py # LiveCodeBench competitive programming
    ├── locomo.py        # LoCoMo long-term conversational memory
    ├── longmemeval.py   # LongMemEval long-term interactive memory
    ├── swebench.py      # SWE-bench GitHub issues
    ├── tau_bench.py     # TAU-bench tool-augmented tasks
    └── terminal_bench.py # Terminal-Bench Docker sandbox tasks
```

## Core Components

### BenchmarkAdapter (protocol.py)

Abstract base class that all benchmark adapters implement:

```python
class BenchmarkAdapter(ABC):
    @abstractmethod
    def get_tasks(self, split: str, limit: int) -> List[Task]:
        """Load tasks from the benchmark dataset."""

    @abstractmethod
    def format_for_agent(self, task: Task) -> Dict[str, Any]:
        """Format task as agent input (system_prompt, user_message, etc.)"""

    @abstractmethod
    def evaluate(self, task: Task, agent_output: Any) -> EvalResult:
        """Evaluate agent output against expected result."""
```

### Data Structures (protocol.py)

- **Task**: Single benchmark task with `id`, `description`, `input_data`, `expected_output`, `metadata`
- **EvalResult**: Evaluation result with `success`, `score`, `error_category`, `error_message`
- **ErrorCategory**: Enum for categorizing failures (WRONG_TOOL, RUNTIME_ERROR, WRONG_OUTPUT, etc.)

### LLM Client (unifiedllm package)

For LLM clients, use the `unifiedllm` package directly:

```python
from unifiedllm import CompletionClient, RetryConfig

client = CompletionClient(
    model="gpt-4o-mini",
    api_key="...",
    retry_config=RetryConfig(
        max_retries=3,
        rate_limit_extra_retries=3,
    ),
)

# Use with agents
response = await client.acall(messages=[...])
```

The `RetryConfig` provides:
- Exponential backoff for transient errors
- Extra retries for rate limits (429)
- Configurable retry behavior for 500/502/503/504 errors
- Timeout handling

## Supported Benchmarks

| Benchmark | Data Source | Task Type | Evaluator |
|-----------|-------------|-----------|-----------|
| **BFCL** | gorilla-llm/berkeley-function-call-leaderboard | Function calling | AST comparison |
| **BigCodeBench** | bigcode/bigcodebench | Code generation | Test execution |
| **DABStep** | adyen/DABstep | Data analysis | Fuzzy matching |
| **GAIA** | gaia-benchmark/GAIA | Multi-step reasoning | Answer matching |
| **InterCode** | xlangai/spider (SQL), GitHub (Bash) | Interactive coding | Execution result |
| **LiveCodeBench** | livecodebench/code_generation_lite | Competitive programming | Test execution |
| **LoCoMo** | snap-research/locomo (GitHub) | Long-term memory QA | F1 score |
| **LongMemEval** | xiaowu0162/longmemeval-cleaned | Long-term memory | Answer matching |
| **SWE-bench** | princeton-nlp/SWE-bench_Lite | GitHub issues | Patch application |
| **TAU-bench** | GitHub tau-bench | Tool-augmented tasks | Consistency check |
| **Terminal-Bench** | laude-institute/terminal-bench | Terminal/shell tasks | Test scripts |

## Usage

### Running a Single Benchmark

```python
from evaluation.adapters import BFCLAdapter
from unifiedllm import CompletionClient, RetryConfig

# Create LLM client
client = CompletionClient(
    model="gpt-4o-mini",
    retry_config=RetryConfig(max_retries=3),
)

# Load adapter
adapter = BFCLAdapter()
tasks = adapter.get_tasks(split="test", limit=100)

# Format and run
for task in tasks:
    input_data = adapter.format_for_agent(task)
    output = await agent.run(input_data)
    result = adapter.evaluate(task, output)
    print(f"{task.id}: {'PASS' if result.success else 'FAIL'}")
```

### Using the Ablation Runner

See `experiments/evaluation-ablations/` for the full ablation study framework:

```bash
cd experiments/evaluation-ablations
python run_ablation.py --config direct_llm --benchmark bfcl --limit 100
```

Available configs:
- `direct_llm`: Raw LLM, single call, no tools
- `react_agent`: ReAct agent with tool calling
- `nemo_oo_agents`: NeMo OO Agents with code generation and tools

## Data Sources

All adapters load from real data sources (no builtin fallbacks):

- **HuggingFace**: BFCL, BigCodeBench, DABStep, GAIA, InterCode SQL, LiveCodeBench, LongMemEval, SWE-bench
- **GitHub**: TAU-bench (tau-bench repository), InterCode Bash, LoCoMo

Required environment variables:
- `HF_TOKEN`: HuggingFace token for gated datasets (GAIA, some SWE-bench)

## Long-Term Memory Benchmarks

LoCoMo and LongMemEval are specialized for evaluating long-term conversational memory:

### LoCoMo (Long-term Conversational Memory)

Tests memory over 600-turn conversations across up to 32 sessions (~16K tokens each):

```bash
# Run LoCoMo QA tasks
python run_ablation.py --config nemo_oo_agents --benchmark locomo --limit 50

# Include event summarization tasks
python run_ablation.py --config nemo_oo_agents --benchmark locomo_events --limit 50
```

**Question categories**: single-hop, multi-hop, temporal, open-domain, adversarial

### LongMemEval (Long-Term Interactive Memory)

500 questions testing five memory abilities across scalable context sizes:

```bash
# Oracle variant (evidence sessions only - good for debugging)
python run_ablation.py --config nemo_oo_agents --benchmark longmemeval_oracle --limit 100

# Small variant (~115K tokens, ~40 sessions)
python run_ablation.py --config nemo_oo_agents --benchmark longmemeval_small --limit 50

# Medium variant (~500 sessions, stress test)
python run_ablation.py --config nemo_oo_agents --benchmark longmemeval_medium --limit 20
```

**Memory abilities**: information extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention

### Using the Long Memory Agent

For context-efficient evaluation with retrieval:

```bash
python run_ablation.py \
    --agent-file agents/long_memory_agent.py \
    --benchmark locomo \
    --limit 50
```

See `docs/design/long-memory-benchmarks.md` for the full design document.

## Terminal-Bench (Docker Sandbox Tasks)

Terminal-Bench evaluates AI agents on real terminal tasks in Docker sandboxes:

```bash
# Run all Terminal-Bench tasks
python run_ablation.py --config nemo_oo_agents --benchmark terminal_bench --limit 20

# Filter by difficulty
python run_ablation.py --config nemo_oo_agents --benchmark terminal_bench_easy --limit 20
python run_ablation.py --config nemo_oo_agents --benchmark terminal_bench_hard --limit 10
```

**Task categories**: file-operations, system-administration, security, data-science, model-training, git, networking

**Requirements**: Docker must be installed for full evaluation. Tasks can be loaded without Docker for inspection.

**Note**: Full evaluation uses the Terminal-Bench harness (`tb run`). The adapter provides task loading and basic heuristic evaluation.

## Adding a New Benchmark

1. Create `adapters/new_benchmark.py`
2. Implement `BenchmarkAdapter` with:
   - `get_tasks()`: Load from HuggingFace/GitHub
   - `format_for_agent()`: Convert to agent input format
   - `evaluate()`: Check agent output against expected
3. Export in `adapters/__init__.py`
4. Add to registry in `adapters/__init__.py:BENCHMARK_REGISTRY`

# Evaluation Framework Setup Guide

This guide explains how to set up and run the benchmark evaluation framework, including TAU-bench, InterCode, and SWE-bench.

## Prerequisites

- Python 3.12+
- Docker Desktop (required for TAU-bench, InterCode, SWE-bench)
- `uv` package manager (recommended)

## Installation

### 1. Clone and Set Up Virtual Environment

```bash
git clone <repo-url> agent006
cd agent006

# Create virtual environment with uv
uv venv
source .venv/bin/activate

# Install all dependencies (including evaluation framework)
uv sync
```

### 2. Environment Variables

Create a `.env` file in the project root:

```bash
# Required for most benchmarks
OPENAI_API_KEY=sk-...

# Optional: NVIDIA API (if using NVIDIA models)
NVIDIA_API_KEY=nvapi-...

# Optional: HuggingFace (for gated datasets like GAIA)
HF_TOKEN=hf_...
```

### 3. Docker Setup (Required for TAU-bench, InterCode, SWE-bench)

Several benchmarks require Docker for isolated execution environments.

#### Install Docker Desktop

**macOS:**
```bash
# Install via Homebrew
brew install --cask docker

# Or download from https://www.docker.com/products/docker-desktop/
```

**Linux:**
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install docker.io docker-compose
sudo systemctl start docker
sudo usermod -aG docker $USER  # Add yourself to docker group
# Log out and back in for group changes to take effect
```

**Windows:**
- Download Docker Desktop from https://www.docker.com/products/docker-desktop/
- Enable WSL 2 backend during installation

**NVIDIA Employees:**
Docker Desktop requires a license for enterprise use. NVIDIA employees can request a license here:
https://nvidia.service-now.com/esc?id=kb_article&sysparm_article=KB0020364

#### Verify Docker is Running

```bash
# Check Docker is available
docker info

# Should show Docker version and system info
docker --version
```

### 4. Build Docker Images for Benchmarks

Each benchmark that requires Docker needs its image built **before** running evaluations.

#### TAU-bench Docker Setup

```bash
# Activate your virtual environment first
source .venv/bin/activate

# Build tau-bench Docker image (automatic on first run, but can pre-build)
python -c "from evaluation.environments.tau_bench import taubench_build_docker; taubench_build_docker()"
```

This builds the `taubench-env:latest` image with:
- Python 3.11
- tau-bench package from GitHub
- Patched for off-by-one bug fix

#### InterCode Docker Setup

InterCode requires the `intercode-bench` package and its Docker images:

```bash
# Install intercode-bench
pip install intercode-bench

# Build SQL environment image
python -c "from intercode.assets import sql_build_docker; sql_build_docker()"

# Build Bash environment image (if using intercode_bash benchmark)
python -c "from intercode.assets import bash_build_docker; bash_build_docker()"
```

#### SWE-bench Docker Setup

SWE-bench uses the Docker Python SDK to create containers dynamically. The base image is pulled automatically, but you can pre-pull it:

```bash
# Pre-pull the base Python image used by SWE-bench
docker pull python:3.11-slim
```

### 5. Verify Setup

Run a quick test to verify everything is working:

```bash
cd experiments/evaluation-ablations
source ../../.venv/bin/activate

# Test with a simple benchmark (no Docker required)
python run_ablation.py --config direct_llm --benchmark bfcl --limit 2

# Test TAU-bench (requires Docker)
python run_ablation.py --config direct_llm --benchmark tau_bench --limit 1
```

## Running Evaluations

### Quick Start

```bash
cd experiments/evaluation-ablations
source ../../.venv/bin/activate

# Run a single benchmark with a specific agent config
python run_ablation.py --config direct_llm --benchmark bfcl --limit 10

# Run TAU-bench specifically
python run_ablation.py --config agent006 --benchmark tau_bench --limit 5
```

### Available Configs

| Config | Description |
|--------|-------------|
| `direct_llm` | Raw LLM, single call, no agent framework |
| `react_agent` | ReAct-style agent with tool calling |
| `agent006` | Full agent006 with code generation and tools |

### Available Benchmarks

| Benchmark | Description | Requirements |
|-----------|-------------|--------------|
| `bfcl` | Berkeley Function Calling Leaderboard | None |
| `bigcodebench` | Code generation tasks | None |
| `livecodebench` | Competitive programming | None |
| `tau_bench` | Multi-turn tool-calling with simulated users | Docker |
| `intercode_sql` | Interactive SQL coding | Docker |
| `swebench` | GitHub issue resolution | Docker |
| `gaia` | Multi-step reasoning | HF_TOKEN |

### Command Options

```bash
python run_ablation.py \
  --config agent006 \           # Agent configuration
  --benchmark tau_bench \       # Benchmark to run
  --limit 50 \                  # Max tasks (default: 10)
  --concurrent-tasks 5 \        # Parallel task execution (default: 5)
  --concurrent-llm 10 \         # Parallel LLM calls (default: 10)
  --provider openai \           # LLM provider: openai, nvidia, nvidia_internal
  --model gpt-4o-mini           # Model override
```

### Resume Interrupted Runs

```bash
# Resume from existing results directory (skips completed tasks)
python run_ablation.py --resume results/20251226_231116
```

## TAU-bench Specifics

TAU-bench evaluates multi-turn conversations with domain-specific tools:

### Domains

- **retail**: Order management, returns, customer service
- **airline**: Flight bookings, reservations, changes

### How It Works

1. Agent receives a system prompt with domain policy
2. Simulated user (LLM-powered) sends requests
3. Agent uses domain tools to fulfill requests
4. Evaluation checks policy compliance and task completion

### Example TAU-bench Run

```bash
# Run retail domain tasks
python run_ablation.py --config agent006 --benchmark tau_bench --limit 10

# Results will show Pass^k metrics for consistency
```

### Using NVIDIA Internal API for TAU-bench

TAU-bench uses a **simulated user** powered by an LLM to generate realistic user responses during multi-turn conversations. By default, this uses OpenAI's API, but we've modified it to support NVIDIA's internal API endpoint.

#### Why Use NVIDIA Internal API?

- Access to models like `gpt-4o`, `gpt-5`, `o1`, `o3` via NVIDIA's internal endpoint
- Useful when you want to use the same API for both the agent and the simulated user
- Required if you don't have an OpenAI API key

#### Configuration

The `TauBenchEnvironment` accepts a `user_provider` parameter:

```python
# In evaluation/environments/tau_bench.py
TauBenchEnvironment(
    domain="retail",
    user_provider="nvidia_internal",  # or "openai" (default)
    user_model="azure/openai/gpt-4o",  # Model for simulated user
)
```

#### Environment Variables

```bash
# For OpenAI (default)
OPENAI_API_KEY=sk-...

# For NVIDIA internal API
NVIDIA_INTERNAL_API_KEY=...
```

#### How It Works

When `user_provider="nvidia_internal"`:
1. The simulated user LLM calls go to `https://inference-api.nvidia.com/v1`
2. The `NVIDIA_INTERNAL_API_KEY` is passed as `OPENAI_API_KEY` to the Docker container
3. The `OPENAI_API_BASE` environment variable is set to the NVIDIA endpoint
4. Default model is `azure/openai/gpt-4o` (since `gpt-4o-mini` isn't available on NVIDIA internal)

#### Running with NVIDIA Internal API

Currently, the `run_ablation.py` script uses the `--provider` flag for the **agent's** LLM, but the simulated user in TAU-bench defaults to OpenAI. To use NVIDIA internal for the simulated user, you need to modify `run_ablation.py`:

```python
# In get_environment_for_benchmark(), change:
return TauBenchEnvironment(domain="retail")

# To:
return TauBenchEnvironment(
    domain="retail",
    user_provider="nvidia_internal",  # Use NVIDIA internal for simulated user
)
```

Or pass it through the provider argument (requires code modification to wire through).

## Results

Results are saved to `experiments/evaluation-ablations/results/<timestamp>/`:

```
results/20251226_231116/
├── run_metadata.json           # Run configuration
├── agent006_tau_bench.006eval.json    # Detailed results
├── agent006_tau_bench.006eval.jsonl   # Per-task results
├── full_results.006eval.json   # Aggregated results
└── traces/                     # Execution traces
    └── agent006_tau_bench.006trace.jsonl
```

### Viewing Results

```bash
# Quick summary
cat results/*/full_results.006eval.json | jq '.results | to_entries[] | "\(.key): \(.value | to_entries[] | "\(.key): \(.value.pass_rate * 100)%")"'

# Detailed per-task results
cat results/*/*.006eval.jsonl | jq -s 'group_by(.task_id) | map({task: .[0].task_id, results: map({config: .config, success: .success})})'
```

## Troubleshooting

### Docker Issues

**Docker not running:**
```bash
# Check Docker is running
docker info

# macOS: Start Docker Desktop from Applications
# Linux: sudo systemctl start docker
```

**Permission denied:**
```bash
# Linux: Add yourself to docker group
sudo usermod -aG docker $USER
# Then log out and back in
```

**TAU-bench image build fails:**
```bash
# Remove broken image and rebuild
docker rmi taubench-env:latest
python -c "from evaluation.environments.tau_bench import taubench_build_docker; taubench_build_docker()"
```

**InterCode image not found:**
```bash
# Rebuild InterCode images
python -c "from intercode.assets import sql_build_docker; sql_build_docker()"
```

**Check existing Docker images:**
```bash
docker images | grep -E "taubench|intercode"
```

### Rate Limits

If you hit API rate limits:
- Reduce `--concurrent-llm` (e.g., `--concurrent-llm 2`)
- Use `--resume` to continue from where you left off

### Missing Modules (BigCodeBench)

Some BigCodeBench tasks require additional packages:
```bash
pip install matplotlib seaborn nltk wordcloud
```

### HuggingFace Token (GAIA)

GAIA requires a HuggingFace token:
```bash
export HF_TOKEN=hf_...
# Or add to .env file
```

### InterCode Not Installed

```bash
# InterCode is not included by default, install separately
pip install intercode-bench
```

## Adding Custom Agents

See `experiments/evaluation-ablations/agents/` for agent implementations:

- `direct_llm.py` - Simple LLM wrapper
- `react_agent.py` - ReAct agent with tools
- `agent006_tools.py` - Full agent006 agent

To add a custom agent:
1. Create `agents/my_agent.py`
2. Add config to `experiments/evaluation-ablations/configs.py`
3. Run with `--config my_agent`

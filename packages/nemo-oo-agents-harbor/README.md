# nemo-oo-agents-harbor

The code that runs **inside** a Harbor/Apptainer container when solving benchmark tasks.

---

## Architecture

There are two distinct layers separated by the container boundary.

### Outside the container — Harbor

[Harbor](https://github.com/harbor-framework/harbor) is an open-source framework for evaluating and optimizing AI agents and language models, built by the Terminal Bench authors. It runs arbitrary agents (Claude Code, OpenHands, Aider, etc.) against standard benchmarks (Terminal Bench, SWEBench, Aider Polyglot, ~50 others) in isolated containers, scores results, and supports parallel execution and RL rollout generation.

The NVIDIA fork adds Apptainer environment support and ECS Fargate — needed for the pre-built SWEBench SIF images on DFW Lustre. Our `NemoOoAgents` adapter lives there (see [Harbor MR !7](https://gitlab-master.nvidia.com/dl/JoC/competitive_evaluation/core_evals_frameworks/harbor/-/merge_requests/7)).

From this package's perspective, Harbor:

1. Pulls a per-task benchmark SIF image (a pre-configured container environment).
2. Starts it via Apptainer, mounts `/logs`, runs `setup.sh`.
3. Clones this repo into the container and installs it.
4. Fires `python -m nemo_oo_agents_harbor --instruction '...' --model '...'` and waits.

Harbor's HTTP sidecar lets the *orchestrator* (on the host) send exec and file commands into the container. That channel is host→container. The agent process running *inside* the container cannot use it.

This package contains only the code that runs **inside** the container.

### Inside the container — this package

Once Harbor fires up the container and calls `python -m nemo_oo_agents_harbor`, this package is on its own. Three files own the work:

| File | Responsibility |
|------|---------------|
| `runner.py` | Entry point. Wires the LLM client, picks the agent class, runs `_run_evaluation`, writes `result.json` to `/logs/agent/` and OTel traces to `/logs/artifacts/traces/`. |
| `tools.py` | Gives the agent shell access and direct file I/O against `/testbed`. Not duplicating Harbor's sidecar — that's host→container; this is agent→filesystem. |
| `agents/swebench_basic.py` | Single CodeAct loop (250 iterations). Simple and fast. |
| `agents/swebench_opt1.py` | Multi-phase pipeline: clarify → root-cause → implement → FeedbackAgent review loop (3×). Higher quality, more tokens. |

```
Harbor (host)
    │  clone + install nemo-oo-agents-harbor
    │  python -m nemo_oo_agents_harbor --instruction ... --model ...
    ▼
Container (/testbed = benchmark repo; path is benchmark-specific)
    runner.py
    ├── SWEBenchLocalTools   (shell + file I/O against /testbed)
    └── SWEBenchBasicAgent or SWEBenchOpt1Agent
            │  CodeAct loop — calls tools, edits files, runs tests
            ▼
        /logs/agent/result.json
        /logs/artifacts/traces/*.jsonl
```

---

## Installation

```bash
# From the nemo-oo-agents workspace root:
uv pip install -e packages/nemo-oo-agents-harbor
```

---

## Local development setup

### 1. Install Apptainer (system dependency)

```bash
sudo apt install apptainer
apptainer --version   # expect 1.4.x
```

For other distros see <https://apptainer.org/docs/admin/main/installation.html>.

### 2. Install Harbor

Use the `rcabral/apptainer-agent006-nemo` branch (adds Apptainer support + `NemoOoAgents` adapter; see [Harbor MR !7](https://gitlab-master.nvidia.com/dl/JoC/competitive_evaluation/core_evals_frameworks/harbor/-/merge_requests/7)):

```bash
mkdir -p 3p
git clone \
    --branch rcabral/apptainer-agent006-nemo \
    ssh://git@gitlab-master.nvidia.com:12051/rcabral/harbor.git \
    3p/harbor-nemo

uv pip install -e 3p/harbor-nemo
harbor --help
```

> **Note:** Access requires membership in the `dl/JoC` GitLab group.

### 3. Get benchmark SIF images

The example below uses SWEBench; substitute the appropriate image source for other benchmarks.

**Option A — copy from DFW Lustre** (fastest):

```bash
# SWEBench container map: 3p/swe/pfurgale/swe_instance_container_map.jsonl
scp <dfw-login>:/path/to/sympy__sympy-19346.sif ~/benchmark_images/
```

**Option B — pull from Docker Hub**:

```bash
mkdir -p ~/benchmark_images
apptainer build ~/benchmark_images/sympy__sympy-19346.sif \
    docker://swebench/sweb.eval.x86_64.sympy__sympy-19346:latest
```

> SIF images are 1–4 GB each. Cache them on fast local storage, not NFS.

### 4. Create task directories

Harbor expects one directory per benchmark task instance:

```
tasks/sympy__sympy-19346/
    instruction.md
    task.toml
    tests/
        test.sh
        config.json
    environment/
        files/
            setup.sh
```

**`task.toml`**:

```toml
[metadata]
difficulty = "hard"
category   = "debugging"
tags       = ["swe-bench"]

[verifier]
timeout_sec = 3000

[agent]
timeout_sec = 3000

[environment]
build_timeout_sec = 1800.0
cpus    = 1
memory  = "4G"
storage = "10G"
docker_image = "/absolute/path/to/sympy__sympy-19346.sif"
```

**`environment/files/setup.sh`** — installs the Harbor sidecar into the testbed env:

```bash
#!/bin/bash
source /opt/miniconda3/bin/activate testbed 2>/dev/null || true
pip install --quiet uvicorn fastapi
```

For SWEBench, task directories can be auto-generated from a JSONL + container map using `create_harbor_tasks.py` in `agent006/experiments/sft_datagen/generate/`. Harbor also has built-in adapters for many benchmarks (`harbor adapter run --adapter swebench`).

### 5. Run

```bash
export NEMO_OO_AGENTS_GIT_URL="https://oauth2:<PAT>@gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents.git"
export ANTHROPIC_API_KEY="sk-ant-..."

harbor run --config examples/harbor_local.yaml
```

Monitor logs while running:

```bash
tail -f /tmp/harbor_jobs/*/trials/*/logs/agent/nemo_oo_agents_harbor.log
```

---

## Agent types

| `--agent-type` | Description |
|----------------|-------------|
| `basic`        | Single CodeAct loop, 250 iterations. Simple and fast. |
| `opt1`         | Clarify → root-cause → implement → FeedbackAgent review (3×). Higher quality, more tokens. |

---

## Container path conventions

| Path | Purpose |
|------|---------|
| `/testbed` | Benchmark repository. SWEBench uses `/testbed` with a `testbed` conda env; other benchmarks may differ. |
| `/logs/agent/` | `nemo_oo_agents_harbor.log` + `result.json` |
| `/logs/artifacts/traces/` | OTel JSONL trace files |

---

## Related issues

- gl-5: Wire package into workspace root dependencies
- gl-8: Auto-detect and publish to OTLP endpoint when available
- gl-21: Run 5 SWEBench Verified tasks end-to-end

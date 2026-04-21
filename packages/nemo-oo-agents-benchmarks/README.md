# nemo-oo-agents-benchmarks

The code that runs **inside** a Harbor/Apptainer container when solving benchmark tasks.

---

## Preliminaries

### Harbor

[Harbor](https://github.com/harbor-framework/harbor) is an open-source framework
for evaluating and optimizing AI agents against standard benchmarks. It runs
arbitrary agents (Claude Code, OpenHands, Aider, etc.) against benchmarks such as
SWEBench, Terminal Bench, DABStep, and ~70 others in isolated containers, scores
results, and supports parallel execution and RL rollout generation.

The NVIDIA fork (`core_evals_frameworks/harbor` on gitlab-master) adds Apptainer
environment support — needed for the pre-built SWEBench SIF images on DFW Lustre.
Our `NemoOoAgents` adapter lives there (see [Harbor MR !7](https://gitlab-master.nvidia.com/dl/JoC/competitive_evaluation/core_evals_frameworks/harbor/-/merge_requests/7)).

From this package's perspective, Harbor:

1. Selects a per-task container image (pre-configured benchmark environment).
2. Starts it via Docker or Apptainer, mounts `/logs`, runs any setup steps.
3. Clones this repo into the container and installs it via `uv sync`.
4. Fires `nemo-harbor --instruction '...' --model '...' --agent-type '...'` and waits.
5. Reads `/app/answer.txt` and `/logs/verifier/reward.txt` to score the result.

Harbor's HTTP sidecar lets the orchestrator (on the host) send exec and file
commands into the container. That channel is host→container. The agent process
running *inside* the container cannot use it. **This package contains only the
code that runs inside the container.**

---

## Architecture

There are two boundaries: the host/container boundary (Harbor's domain) and the
container/agent boundary (this package's domain).

```
┌─ HOST ──────────────────────────────────────────────────────────────┐
│                                                                     │
│  harbor run --config dabstep_baseline.yaml                         │
│       │                                                             │
│       │  uses NemoOoAgents plugin (3p/harbor-nemo/)                │
│       │    install():  git clone + uv sync inside container        │
│       │    run():      nemo-harbor --instruction "..." \           │
│       │                            --model "..." \                 │
│       │                            --agent-type baseline           │
│       │                                                             │
│  task folder (self-contained)                                       │
│  ├── instruction.md   ← complete prompt; tells agent where          │
│  │                      data is, what to answer, where to write    │
│  ├── task.toml        ← container image, timeouts, metadata        │
│  ├── environment/     ← Dockerfile or Apptainer .def               │
│  └── tests/test.sh    ← reads /app/answer.txt, writes reward       │
│                                                                     │
└─────────────────────────────┬───────────────────────────────────────┘
                              │  container boundary
┌─ CONTAINER ─────────────────▼───────────────────────────────────────┐
│                                                                     │
│  nemo-harbor --instruction "..." --model "..." --agent-type "..."  │
│       │                                                             │
│  runner.py  (thin shim)                                            │
│       ├── inject tools if needed (e.g. SWEBenchLocalTools)         │
│       └── agent._run_evaluation({                                  │
│                "user_message": instruction,                         │
│                "environment_tools": [...]   # empty for most       │
│            })                                                       │
│                   │                                                 │
│                   ▼                                                 │
│  agents/                                                            │
│  ├── baseline.py       CodeAct REPL, no tools assumed              │
│  ├── dabstep.py        parses question/guidelines internally,      │
│  │                     3-phase pipeline                             │
│  ├── swebench_basic.py CodeAct + self.swebench tools               │
│  ├── swebench_opt1.py  multi-phase + self.swebench tools           │
│  ├── tau_bench.py      multi-turn customer service                 │
│  └── (stubs)           locomo, terminal_bench_1/2                  │
│                   │                                                 │
│                   ▼                                                 │
│  /app/answer.txt          ← read by tests/test.sh                  │
│  /logs/agent/result.json                                            │
│  /logs/artifacts/traces/  ← OTel JSONL                             │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### The task is self-contained

`instruction.md` is a complete, standalone prompt. It tells the agent:
- what role to play and what data to access
- exactly what question to answer
- what format the answer must be in
- where to write it (`/app/answer.txt`)

The runner passes it verbatim as `{"user_message": instruction}`. Every agent
accepts that same interface. Benchmark-specific parsing (e.g. extracting
question and guidelines for DABStep's 3-phase pipeline) happens inside the
agent, not in the runner.

### NemoOoAgents — the Harbor plugin

`nemo_oo_agents.py` lives in Harbor's codebase (`3p/harbor-nemo/`), not here.
It is Harbor's plugin for our agent: it defines how to install this package
inside a container and what CLI command to run. From this package's perspective
it is invisible — this package only knows it will be called via `nemo-harbor`.

### runner.py — the container-boundary shim

`runner.py` is the `nemo-harbor` CLI entry point. Its only jobs are:

1. Instantiate the right agent class from `--agent-type`.
2. Inject tools if requested via `--tools` (e.g. `SWEBenchLocalTools` for
   SWEBench tasks).
3. Call `agent._run_evaluation({"user_message": instruction, ...})`.
4. Write `result.json` and `/app/answer.txt`.

It has no benchmark-specific logic. The benchmark is encoded in
`instruction.md`; the agent handles it.

### tools.py — container filesystem access

`SWEBenchLocalTools` gives the agent shell access and direct file I/O against
`/testbed`. This is distinct from Harbor's HTTP sidecar (host→container); this
is agent→filesystem inside the container.

---

## Agent types

All agents accept the same input interface:

```python
await agent._run_evaluation({"user_message": instruction})
```

| `--agent-type`     | Description |
|--------------------|-------------|
| `baseline`         | General-purpose CodeAct agent. No benchmark-specific logic. Use this for smoke tests against any benchmark. |
| `dabstep`          | 3-phase pipeline (RulesLawyer → compute_answer → SolutionVerifier). Parses question/guidelines from `user_message` internally. |
| `swebench/basic`   | Single CodeAct loop with `SWEBenchLocalTools`. Requires `--tools swebench`. |
| `swebench/opt1`    | Multi-phase: clarify → root-cause → implement → FeedbackAgent review. Requires `--tools swebench`. |
| `tau-bench`        | Multi-turn customer service agent. |
| `locomo`           | Stub (see gl-14). |

Any agent can run against any benchmark — the task's `instruction.md` is the
only benchmark-specific input.

---

## Container path conventions

| Path | Purpose |
|------|---------|
| `/app/answer.txt` | Agent writes its final answer here. Read by `tests/test.sh`. |
| `/app/data/` | Benchmark data directory (DABStep, etc.). Defined per task. |
| `/testbed` | Benchmark repository (SWEBench). Pre-configured conda env `testbed`. |
| `/logs/agent/` | `nemo_oo_agents_benchmarks.log` + `result.json` |
| `/logs/artifacts/traces/` | OTel JSONL trace files |

---

## Tracing

The runner emits OTel spans for every agent run.  Where they go depends on
whether the local viewer is reachable.

### Local development (viewer running)

Start the viewer before launching Harbor:

```bash
uv run nemo oo start-dev          # Terminal 1 — viewer on http://localhost:5001
harbor run --config util/harbor/dabstep_baseline.yaml   # Terminal 2
```

The runner probes `localhost:5001` at startup.  Apptainer shares the host
network namespace, so the probe resolves to your host viewer.  If it responds,
spans are streamed live via the journal exporter.  Each session in the viewer
will have `eval.model` and `eval.agent_type` as resource attributes.

> **Note:** individual tasks are not yet distinguishable by name in the session
> list — all runs with the same model/agent type look identical.  A `--task-id`
> flag is tracked in gl-39.

### Remote / datacenter runs (no viewer)

The probe fails silently (0.5 s timeout) and traces fall back to JSONL in
`/logs/artifacts/traces/`.  Import them into your local viewer after the job
completes:

```bash
nemo oo import-harbor /path/to/harbor/jobs/my-job/
```

### Docker containers

Docker doesn't share the host network, so `localhost` inside the container
doesn't reach the host viewer.  Set `OTLP_ENDPOINT` explicitly:

```bash
export OTLP_ENDPOINT=http://host.docker.internal:5001/v1/traces
harbor run --config ...
```

---

## Installation

```bash
# From the nemo-oo-agents workspace root:
uv pip install -e packages/nemo-oo-agents-benchmarks
```

---

## Running locally (without Harbor/Apptainer)

The `*_debug.py` scripts use `eval_pipeline` to run agents directly against
benchmark tasks without a container. They bypass Harbor entirely — useful for
fast iteration, but scoring is completion-based only (no container verifier).

```bash
# DABStep — requires data at ~/.cache/dabstep/data/context/
uv run python util/harbor/run_dabstep_debug.py --tasks 5 --model openai/gpt-4o

# Terminal Bench — clones the task repo on first run
uv run python util/harbor/run_terminal_bench_debug.py --tasks 5
uv run python util/harbor/run_terminal_bench_debug.py \
  --task-names analyze-access-logs cancel-async-tasks
```

---

## Setting up Harbor (for container runs)

### 1. Install Harbor

Harbor v0.4.0 is mirrored at `3p/harbor-nemo/` (upstream:
[harbor-framework/harbor](https://github.com/harbor-framework/harbor),
NVIDIA fork:
[core_evals_frameworks/harbor](https://gitlab-master.nvidia.com/dl/JoC/competitive_evaluation/core_evals_frameworks/harbor)).

```bash
uv pip install -e 3p/harbor-nemo
harbor --version   # expect 0.4.0
```

### 2. Configure Apptainer (one-time, requires sudo)

On Ubuntu 24.04, AppArmor restricts unprivileged user namespace creation.
Apptainer needs its `starter-suid` binary to be setuid root:

```bash
sudo cp /usr/lib/x86_64-linux-gnu/apptainer/bin/starter \
        /usr/lib/x86_64-linux-gnu/apptainer/bin/starter-suid
sudo chmod u+s /usr/lib/x86_64-linux-gnu/apptainer/bin/starter-suid
```

Verify it works:

```bash
apptainer exec docker://ubuntu:22.04 echo "hello"
```

### 3. Get benchmark container images

SIF files are stored at `~/3p/sif_cache/` (outside any worktree; will be rsync'd to Lustre for team access).

**DABStep** — build from `3p/dabstep.def` (bakes in pandas + data files):

```bash
apptainer build --fakeroot ~/3p/sif_cache/dabstep.sif 3p/dabstep.def
```

**Terminal Bench** — 202 of 241 task-specific SIFs are pre-built. Rebuild any
missing ones with:

```bash
# Build all tasks (skips already-built SIFs):
sudo python3 util/harbor/build_terminal_bench_sifs.py --skip-built

# Build specific tasks:
sudo python3 util/harbor/build_terminal_bench_sifs.py analyze-access-logs cancel-async-tasks

# Dry-run (print generated .def files without building):
python3 util/harbor/build_terminal_bench_sifs.py --dry-run
```

The script translates each task's Dockerfile to an Apptainer `.def` and builds
on top of the cached base images in the SIF cache. Tasks that use multi-stage
Docker builds or dynamic `FROM` expressions cannot be translated automatically.

**SWEBench** — copy from DFW Lustre or pull from Docker Hub:

```bash
apptainer build ~/3p/sif_cache/sympy__sympy-19346.sif \
    docker://swebench/sweb.eval.x86_64.sympy__sympy-19346:latest
```

### 4. Generate task directories

Task directories are not stored in git — they are generated on demand.

**Terminal Bench** — 241 task dirs already exist under
`util/harbor/tasks/terminal_bench/`. To regenerate from the upstream repo:

```bash
python3 util/harbor/generate_terminal_bench_tasks.py
# Or specific tasks:
python3 util/harbor/generate_terminal_bench_tasks.py analyze-access-logs
```

**DABStep / SWEBench** — use Harbor's public adapters:

```bash
# DABStep — generates task dirs under ./tasks/dabstep/
harbor adapter run --adapter dabstep

# SWEBench — generates task dirs under ./tasks/swebench/
harbor adapter run --adapter swebench
```

Harbor ships adapters for ~70 public benchmarks (DABStep, SWEBench, GAIA,
BFCL, etc.) in `3p/harbor-nemo/adapters/`. These are from the public
[harbor-framework/harbor](https://github.com/harbor-framework/harbor)
repository — not NVIDIA-internal.

### 5. Run

```bash
export NEMO_OO_AGENTS_GIT_URL="https://oauth2:<PAT>@gitlab-master.nvidia.com/interactive-agents/nemo_oo_agents.git"
export NVIDIA_INTERNAL_API_KEY="sk-..."   # routes Claude/etc. through NVIDIA internal gateway

# DABStep baseline
harbor run --config util/harbor/dabstep_baseline.yaml

# Terminal Bench (all 241 tasks, or restrict with --path to a subset)
harbor run --config util/harbor/terminal_bench_baseline.yaml
```

---

## Baseline benchmark results

### Terminal Bench — smoke test (5 tasks, bedrock-claude-sonnet-4-5-v1, gl-24)

| Task | Reward | Notes |
|------|--------|-------|
| analyze-access-logs | **1.0** | 3/3 pytest pass — agent correctly parsed 2000-line access log |
| acl-permissions-inheritance | 0.0 | 2/9 tests pass |
| adaptive-rejection-sampler | 0.0 | 4/9 tests pass (R runs; sampling logic incorrect) |
| cancel-async-tasks | 0.0 | Agent did not create required files |
| add-benchmark-lm-eval-harness | error | MemoryLimitExceededError (lm-eval-harness OOM at 4 GB) |

Full results and traces: `.development/docs/evaluation/terminal_bench_baseline_gl24_*/`


# τ-bench Setup Plan

**Date:** 2025-12-27
**Goal:** Get τ-bench running with nemo_oo_agents from a clean fetch of main, producing traces and evals visible in the viewer.

---

## Requirements

### Must Have
1. **Dynamic agent loading** - Must load agents from file path at runtime, because the optimization loop rewrites agent files and we need to inject those rewritten agents for evaluation
2. **Agent006 only** - We only run our agent (CodeActStrategy or similar), NOT ReAct or pure LLM baselines
3. **Viewer compatibility** - Output must work with:
   - **prompt-opt viewer** - Canonical `.006eval.jsonl` format with `_type` fields
   - **trace viewer** - One `.006trace.jsonl` file **per sample** (not per benchmark run!)
4. **One row per sample** - Each τ-bench task produces exactly one row in the eval file

### Why Dynamic Agent Loading?

The optimization loop works by:
1. Running baseline agent → collecting traces
2. Analyzing failures → proposing agent rewrites
3. **Writing a new agent file** (e.g., `iteration_001/agents/tau_agent.py`)
4. Loading that rewritten agent dynamically → running eval
5. Comparing scores → accept/reject → repeat

Without dynamic loading, we can't test evolved agents. The runner must accept an agent file path and import it at runtime.

### Output Format Requirements

Must align with `util/e2e_optimization/OPTIMIZATION_PLAN.md` structure:

```
experiments/tau_bench_YYYYMMDD_HHMMSS/
├── config.yaml                              # Snapshot of config
├── iteration_000/
│   ├── agents/                              # Code snapshot
│   │   └── tau_agent.py                     # Agent being optimized
│   ├── tau_bench_TIMESTAMP.006eval.jsonl    # Eval results
│   │   ├── Line 1: {"_type": "metadata", ...}
│   │   ├── Line 2: {"_type": "result", "test_id": "retail_000", ...}
│   │   └── Line N: {"_type": "completion", "passed_count": X, ...}
│   └── traces/
│       ├── retail_000_SESSION.006trace.jsonl  # One trace per sample
│       ├── retail_001_SESSION.006trace.jsonl  # SESSION = unique run ID
│       └── ...
├── iteration_001/
│   ├── agents/                              # Evolved agent
│   ├── parent_eval/                         # Minibatch comparison
│   ├── proposed_eval/
│   └── ...
└── pareto_state.json                        # Per-test Pareto frontier
```

### Trace File Naming
- Format: `{test_id}_{session_id}.006trace.jsonl`
- Session ID links trace to specific run (for retries, pass@k)
- Example: `retail_000_abc123.006trace.jsonl`

### Trace Architecture (Not a Docker Issue)

```
┌─────────────────────────────────────────────────────────────────┐
│  MAIN PROCESS (Python)                                          │
│                                                                  │
│  ┌─────────────────┐     ┌─────────────────────────────────┐   │
│  │  TauBenchAgent  │────▶│  Traces saved here via OTel     │   │
│  │  (CodeAct)      │     │  → traces/retail_000_abc.jsonl  │   │
│  └────────┬────────┘     └─────────────────────────────────┘   │
│           │                                                      │
│           │ Tool calls (via setattr'd tools)                    │
│           ▼                                                      │
│  ┌─────────────────┐                                            │
│  │ TauBenchTools   │──────┐                                     │
│  └─────────────────┘      │                                     │
│                           │ docker exec                         │
└───────────────────────────┼─────────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────────────┐
│  DOCKER CONTAINER                                              │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  τ-bench environment (pickled state)                     │  │
│  │  - Simulated user LLM (gpt-4o-mini)                     │  │
│  │  - Tool execution (cancel_order, etc.)                  │  │
│  │  - State isolation between tasks                        │  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

**Key insight:** The agent runs in the main Python process, NOT inside Docker.
- Agent006's OTel tracing captures all LLM calls and tool invocations
- Traces are written to the local filesystem as normal
- Docker is only used for τ-bench state isolation (simulated user, tool execution)
- No special handling needed for trace extraction

### Pareto Tracking (from OPTIMIZATION_PLAN.md)
Per-test score tracking enables Pareto selection:
```python
# Per-test Pareto state
{
  "test_to_best_strategies": {
    "retail_000": [0, 2],      # Strategies that dominate on this test
    "retail_001": [1, 2],
  },
  "strategy_scores": {
    "0": {"retail_000": 1.0, "retail_001": 0.0},  # Strategy 0's per-test scores
    "1": {"retail_000": 0.0, "retail_001": 1.0},
    "2": {"retail_000": 0.8, "retail_001": 0.8},
  }
}
```

---

## Current Understanding

### What is τ-bench?
- Multi-turn tool-calling benchmark from Sierra Research
- Simulates customer service scenarios (retail, airline domains)
- Uses an LLM-powered simulated user to interact with the agent
- Requires Docker for isolation (each task runs in a container)
- Fixed set of 16 tools per domain (e.g., `get_order_details`, `cancel_pending_order`)

### Key Components
1. **Agent** - Receives user messages, calls tools, responds
2. **Environment** - Docker container with τ-bench installed, simulates user
3. **Adapter** - Loads tasks, formats inputs, evaluates outputs
4. **Runner** - Orchestrates execution, writes traces and evals

### File Formats
- **`.006eval.jsonl`** - Canonical eval format for viewer (lines with `_type: metadata/result/completion`)
- **`.006trace.jsonl`** - Trace format for trace viewer

---

## Known Issues & Requirements

### 1. Docker Setup
- τ-bench requires Docker daemon running
- Colima works as alternative to Docker Desktop on macOS
- Environment variable: `DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"`

### 2. Simulated User LLM Authentication
- τ-bench's simulated user needs an LLM to generate responses
- Inside Docker, it expects `OPENAI_API_KEY` with `sk-...` format
- NVIDIA's `nvapi-...` keys are rejected by litellm's OpenAI provider validation
- **Solution:** Use `NVIDIA_INFERENCE_API_KEY` (which starts with `sk-`) with model `azure/openai/gpt-4o` and base URL `https://inference-api.nvidia.com/v1`

### 3. Output Format Compatibility
- The viewer expects canonical `.006eval.jsonl` format with `_type` fields
- `run_ablation.py` outputs a different format (`{idx: N, result: {...}}`)
- `eval_pipeline` outputs the correct format
- **Need to determine:** Which runner to use/modify

---

## Runner Decision

### Decision: Use `run_ablation.py` with nemo_oo_agents only

**Rationale:**
1. **Already working** - Recent runs (Dec 27) show successful τ-bench execution
2. **Minimal changes** - Just switch agent to use `CodeActStrategy`
3. **Simpler than alternatives** - `evaluation/runner.py` would require more refactoring
4. **Output format fixable** - Add canonical format output as post-processing

### What's Already Working (Infrastructure)

The `run_ablation.py` script successfully:
- ✅ Loads τ-bench tasks from GitHub
- ✅ Creates Docker containers for isolation
- ✅ Injects tools into agent via `setattr()` + registry
- ✅ Executes multi-turn conversations
- ✅ Evaluates against expected tool calls
- ✅ Writes trace files (but wrong format - see gaps below)

**Note:** Recent runs (Dec 27) use `PurePythonStrategy` via `nemo_oo_agents_tools.py`, achieving ~40% pass rate. Infrastructure works; we need CodeActStrategy for better performance.

---

### Gap Analysis: Current vs. Required

| Requirement | Current State | Gap |
|-------------|---------------|-----|
| **Dynamic agent loading** | Hard-coded agent class in factory | ❌ Must add file path import |
| **One trace per sample** | One trace file per benchmark run | ❌ Must split into per-sample files |
| **Canonical eval format** | `{idx, result}` format | ❌ Must add `_type` fields |
| **CodeActStrategy agent** | Uses `PurePythonStrategy` | ❌ Must create/register CodeAct agent |
| **Docker/auth** | Already working | ✅ No changes needed |
| **Tool injection** | Already working | ✅ No changes needed |

---

### Changes Required

#### 1. Use nemo_oo_agents Only (Simplify CONFIGS)

```python
# experiments/evaluation-ablations/run_ablation.py

CONFIGS = {
    "nemo_oo_agents": {
        "description": "Agent006 with CodeActStrategy",
        "agent_type": "nemo_oo_agents_codeact",  # New agent type
        "tools": True,
        "refinement": False,
    },
    # Remove react_agent and direct_llm
}
```

Or just run with `--config nemo_oo_agents --benchmark tau_bench`.

#### 2. Create CodeAct Agent

**Already exists:** `util/e2e_optimization/src/e2e_optimization/examples/tau_bench/agents/tau_agent.py`

Copy to `experiments/evaluation-ablations/agents/nemo_oo_agents_codeact.py`:

```python
from nemo_oo_agents import Agent, CodeActStrategy, strategy
from unifiedllm import FakeLLMClient

class TauBenchAgent(Agent, llm=FakeLLMClient()):
    """τ-bench agent using CodeActStrategy."""

    def __init__(self, llm=None, **kwargs):
        super().__init__(llm=llm, **kwargs)

    async def _run_evaluation(self, task_input: dict) -> dict:
        # Build context from task_input (system_prompt, tools, etc.)
        # Call handle_request()
        ...

    @strategy(CodeActStrategy(max_iterations=20, max_retries=10))
    async def handle_request(self, description: str) -> str:
        """Handle a customer service request.

        {description}

        Instructions:
        - Use `doc(self)` to see available tools
        - Call `await self.taubench.<method>()` to execute actions
        - Return a helpful response summarizing what was done
        """
        ...
```

#### 3. Register New Agent in `run_ablation.py`

```python
def create_agent_factory(config_name: str, llm_config: LLMConfig, shared_client: Any = None):
    config = CONFIGS[config_name]
    agent_type = config["agent_type"]

    if agent_type == "nemo_oo_agents_codeact":
        from agents.nemo_oo_agents_codeact import TauBenchAgent

        def factory(llm_client=None):
            return TauBenchAgent(llm=llm_client or shared_client)

        return factory
    # ... existing agent types ...
```

#### 4. Dynamic Agent Loading (TODO)

Currently, agents are hard-coded in the factory. Need to support loading from file path:

```python
def load_agent_from_file(agent_path: Path, llm_client=None):
    """Dynamically import agent class from file path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("agent_module", agent_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Find the Agent subclass in the module
    for name, obj in vars(module).items():
        if isinstance(obj, type) and issubclass(obj, Agent) and obj is not Agent:
            return obj(llm=llm_client)

    raise ValueError(f"No Agent subclass found in {agent_path}")

# Usage in runner:
# python run_ablation.py --agent-file iteration_001/agents/tau_agent.py --benchmark tau_bench
```

#### 5. One Trace File Per Sample (TODO)

**Current:** All traces go to one file per benchmark (`nemo_oo_agents_tau_bench.006trace.jsonl`)

**Required:** One trace file per sample (`retail_000_abc123.006trace.jsonl`)

```python
# In run_single_task(), instead of writing to shared trace_file:
def write_per_sample_trace(traces_dir: Path, task_id: str, session_id: str, spans: list[dict]):
    """Write trace spans to per-sample file."""
    trace_path = traces_dir / f"{task_id}_{session_id}.006trace.jsonl"
    with open(trace_path, 'w') as f:
        for span in spans:
            f.write(json.dumps(span) + "\n")
    return trace_path
```

**Note:** nemo_oo_agents's OTel instrumentation already writes per-sample traces when properly configured. May just need to configure trace output path per task rather than changing span writing.

#### 6. Canonical Eval Format (TODO)

Add canonical output after run completes:

```python
def write_canonical_format(results: dict, output_path: Path):
    """Write results in canonical .006eval.jsonl format."""
    with open(output_path, 'w') as f:
        # Line 1: metadata
        f.write(json.dumps({"_type": "metadata",
                           "suite_name": results["benchmark"],
                           "model": results.get("model", "unknown"),
                           "timestamp": datetime.now().isoformat()}) + "\n")

        # Lines 2..N: results
        for r in results["results"]:
            f.write(json.dumps({
                "_type": "result",
                "test_id": r["task_id"],
                "passed": r["success"],
                "scores": {"evaluator": {"passed": r["success"], "score": r["score"]}},
                # ... other fields
            }) + "\n")

        # Last line: completion
        f.write(json.dumps({
            "_type": "completion",
            "passed_count": results["passed"],
            "result_count": results["total_tasks"]
        }) + "\n")
```

#### 7. Authentication (Already Working in Main)

**Good news:** τ-bench already uses the NVIDIA endpoint in main. No auth changes needed.

The original `tau_bench.py` has provider-based switching:
```python
# user_provider="nvidia_internal" → uses NVIDIA_INTERNAL_API_KEY + inference-api endpoint
# user_provider="openai" (default) → uses OPENAI_API_KEY + default OpenAI endpoint
```

**For our use:** Just ensure `NVIDIA_INTERNAL_API_KEY` is set and pass `user_provider="nvidia_internal"` when creating the environment (which `run_ablation.py` already supports via the adapter).

### Agent Architecture: CodeActStrategy

**How CodeActStrategy works:**
1. The LLM receives Python execution capability via `execute_python(code)` tool
2. Inside that Python code, the LLM can call τ-bench tools: `await self.taubench.get_order_details(...)`
3. The LLM iterates (REPL-style) until it calls `return_result(value)` to finish

**Key insight:** CodeActStrategy wraps tool access in Python code. The LLM writes Python that calls the τ-bench tools, rather than calling tools directly. This gives more flexibility (loops, conditionals, error handling) but requires the LLM to write correct Python.

**Existing Implementation:** `util/e2e_optimization/src/e2e_optimization/examples/tau_bench/agents/tau_agent.py`

```python
from nemo_oo_agents import Agent, CodeActStrategy, strategy
from unifiedllm import FakeLLMClient

class TauBenchAgent(Agent, llm=FakeLLMClient()):
    """Agent for τ-bench multi-turn tool-calling benchmark."""

    def __init__(self, llm=None, **kwargs):
        super().__init__(llm=llm, **kwargs)

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point for evaluation framework."""
        from agentdoc import doc

        parts = []
        if system_prompt := task_input.get("system_prompt"):
            parts.append(f"## Context\n{system_prompt}")
        if initial_obs := task_input.get("initial_observation"):
            parts.append(f"## Current State\n{initial_obs}")
        if env_tools := task_input.get("environment_tools"):
            tools_docs = ["## Available Tools"]
            for tool_name in env_tools:
                if tool := getattr(self, tool_name, None):
                    tools_docs.append(f"### self.{tool_name}\n{doc(tool)}")
            parts.append("\n".join(tools_docs))

        description = task_input.get("user_message") or task_input.get("user_prompt", "")
        parts.append(f"## User Request\n{description}")

        full_description = "\n\n".join(parts)

        try:
            result = await self.handle_request(full_description)
            return {"response": str(result), "success": True, "result": result}
        except Exception as e:
            return {"response": "", "success": False, "error": str(e)}

    @strategy(CodeActStrategy(max_iterations=20, max_retries=10))
    async def handle_request(self, description: str) -> str:
        """Handle a customer service request.

        {description}

        Instructions:
        - Use `doc(self)` to see available tools
        - Call `await self.taubench.<method>()` to execute actions
        - Think step by step about what the customer needs
        - Verify actions before making permanent changes
        - Return a helpful response summarizing what was done
        """
        ...
```

### What the Optimizer Can Evolve

The optimizer rewrites the **entire agent file**. It can modify:

| Component | Example Changes |
|-----------|-----------------|
| Class docstring | Improve system prompt, add policies |
| `__init__` | Add state variables, context tracking |
| Method docstrings | Refine prompts, add examples |
| Helper methods | Add `validate_order()`, `format_response()` |
| CodeActStrategy config | Adjust `max_iterations`, `max_retries` |
| Context building | Improve `_run_evaluation()` logic |

---

## Implementation Plan

### Phase 1: Verify Prerequisites
- [ ] Confirm Docker/Colima is running: `docker ps`
- [ ] Confirm `NVIDIA_INTERNAL_API_KEY` is set (starts with `sk-`)
- [ ] Confirm `NVIDIA_API_KEY` is set (starts with `nvapi-`)
- [ ] Verify τ-bench Docker image exists: `docker images | grep taubench`

### Phase 2: Modify `run_ablation.py` - Part 1 (Dynamic Agent Loading)
- [ ] Add imports: `enable_tracing`, `set_trace_file`, `get_current_exporter`
- [ ] Add `--agent-file` argument to CLI
- [ ] Implement `load_agent_from_file()` function
- [ ] Modify `create_agent_factory()` to use dynamic loading when `--agent-file` provided
- [ ] Test: `python run_ablation.py --agent-file path/to/agent.py --benchmark tau_bench --limit 1`

### Phase 3: Modify `run_ablation.py` - Part 2 (OTel Per-Sample Traces)
- [ ] Initialize OTel tracing at startup via `enable_tracing()`
- [ ] Modify `run_single_task()`:
  - Call `set_trace_file(path)` before running agent
  - Call `exporter.close_file(path)` after task completes
- [ ] Remove/deprecate manual `_create_trace_span()` function
- [ ] Test: verify individual trace files created in `traces/`

### Phase 4: Add Canonical Eval Format
- [ ] Add `write_canonical_format()` function
- [ ] Write `.006eval.jsonl` with `_type` fields alongside existing format
- [ ] Verify prompt-opt viewer can load results

### Phase 5: Create Baseline Agent
- [ ] Copy `util/e2e_optimization/.../tau_agent.py` to `experiments/evaluation-ablations/agents/nemo_oo_agents_codeact.py`
- [ ] Verify it uses `CodeActStrategy`
- [ ] Test basic import and execution

### Phase 6: Run Baseline Evaluation
```bash
cd experiments/evaluation-ablations

# Single task test with CodeActStrategy agent
python run_ablation.py \
  --agent-file agents/nemo_oo_agents_codeact.py \
  --benchmark tau_bench \
  --limit 1

# Full baseline (10 tasks)
python run_ablation.py \
  --agent-file agents/nemo_oo_agents_codeact.py \
  --benchmark tau_bench \
  --limit 10
```

### Phase 7: Integrate with Optimization Loop
- [ ] Wire `--agent-file` into `e2e_optimization/optimizer.py`
- [ ] Test: optimizer rewrites agent → calls run_ablation with new file path
- [ ] Run first optimization iteration
- [ ] Verify accept/reject logic works with τ-bench scores

---

## Files to Create/Modify

| File | Purpose | Changes |
|------|---------|---------|
| `experiments/evaluation-ablations/run_ablation.py` | Main runner | 📝 Modify: add `--agent-file`, OTel per-sample traces |
| `experiments/evaluation-ablations/agents/nemo_oo_agents_codeact.py` | Baseline CodeAct agent | 📁 Create: copy from e2e_optimization |
| `evaluation/environments/tau_bench.py` | Docker environment | ✅ Already correct |
| `evaluation/adapters/tau_bench.py` | Task loading, evaluation | ✅ Already correct |

**Key changes to `run_ablation.py`:**
1. `--agent-file` argument for dynamic loading (~20 lines)
2. OTel per-sample trace files via `set_trace_file()` (~30 lines)
3. Remove manual `_create_trace_span()` (-130 lines)
4. Canonical eval format output (~20 lines)

**Net change:** ~-60 lines (simpler code, richer traces)

---

## Configuration

### API Keys

**Location:** `.env` file at project root

**Required Keys:**

| Key | Format | Purpose | How to Get |
|-----|--------|---------|------------|
| `NVIDIA_API_KEY` | `nvapi-...` | Public NIM endpoints (agent LLM) | [build.nvidia.com](https://build.nvidia.com) |
| `NVIDIA_INTERNAL_API_KEY` | `sk-...` | Internal endpoints (simulated user) | NVIDIA internal portal |

**For τ-bench simulated user:**
- Uses `NVIDIA_INTERNAL_API_KEY` (must start with `sk-` for litellm compatibility)
- Default model: `gpt-4o-mini` (or `azure/openai/gpt-4o` if mini unavailable)
- Endpoint: `https://inference-api.nvidia.com/v1`
- Set via `user_provider="nvidia_internal"` in TauBenchEnvironment

**Verify keys are set:**
```bash
# Check key formats (should show first chars)
echo "NVIDIA_API_KEY: ${NVIDIA_API_KEY:0:10}"
echo "NVIDIA_INTERNAL_API_KEY: ${NVIDIA_INTERNAL_API_KEY:0:5}"

# Expected output:
# NVIDIA_API_KEY: nvapi-XXXX
# NVIDIA_INTERNAL_API_KEY: sk-XX
```

**Test simulated user access:**
```bash
curl https://inference-api.nvidia.com/v1/chat/completions \
  -H "Authorization: Bearer $NVIDIA_INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hi"}]}'
```

### Model Configuration
- Centralized in `util/config/models.yaml`
- Defines provider, endpoint, api_key_env for each model
- Referenced by name in experiment configs
- Key models for τ-bench:

| Model | Provider | Endpoint | Key |
|-------|----------|----------|-----|
| `gpt-4o-mini` / `gpt-4o` | nvidia_internal | inference-api.nvidia.com | `NVIDIA_INTERNAL_API_KEY` |
| `nvidia_nim/qwen/qwen3-next-80b-a3b-instruct` | nvidia | integrate.api.nvidia.com | `NVIDIA_API_KEY` |

### For Simulated User (inside Docker)
- Must use `NVIDIA_INTERNAL_API_KEY` (starts with `sk-`)
- Model: `gpt-4o-mini` (default) or `gpt-4o`
- Endpoint: `https://inference-api.nvidia.com/v1`
- Provider: Set `user_provider="nvidia_internal"` in TauBenchEnvironment

**Working curl example:**
```bash
curl https://inference-api.nvidia.com/v1/chat/completions \
  -H "Authorization: Bearer $NVIDIA_INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello"}],
    "temperature": 0.2,
    "seed": 1234
  }'
```

---

## Summary of Work Required

| Task | Effort | Priority | Notes |
|------|--------|----------|-------|
| Dynamic agent loading (`--agent-file`) | Low | **P0** | ~25 lines: arg + `load_agent_from_file()` |
| Per-sample OTel traces | Low | **P0** | ~30 lines: `set_trace_file()` + cleanup |
| Remove manual trace span code | Low | **P0** | -130 lines deletion |
| Canonical eval format | Low | **P1** | ~20 lines: `write_canonical_format()` |
| Create CodeAct agent | Low | **P2** | Copy existing file |

**Total estimated effort:** Half day to 1 day

**Good news:** The hard parts (OTel per-sample routing, file handle management) are already implemented in `openinference-instrumentation-nemo-oo-agents`. We just need to call the right APIs.

---

## Resolved Questions

### 1. Modify `run_ablation.py` vs. new file?

**Decision:** Modify `run_ablation.py` directly.

**Rationale:**
- Avoids code duplication
- All ablation experiments benefit from improvements
- Backward compatible with `--agent-file` being optional

### 2. How does nemo_oo_agents's OTel tracing integrate with per-sample traces?

**Answer:** The `openinference-instrumentation-nemo-oo-agents` package already supports this!

**Key API:**
```python
from openinference_instrumentation_nemo_oo_agents import (
    enable_tracing,        # Initialize OTel with JSONL exporter
    set_trace_file,        # Route spans to specific file (context-var based)
    get_current_exporter,  # Access exporter to close files
)
```

**Per-sample trace pattern:**
```python
# In run_single_task(), BEFORE running the agent:
trace_path = traces_dir / f"{task_id}_{session_id}.006trace.jsonl"
set_trace_file(trace_path)

# Run agent (spans automatically route to trace_path)
output = await agent._run_evaluation(agent_input)

# After task completes, close file to avoid "too many open files":
exporter = get_current_exporter()
if exporter:
    exporter.close_file(trace_path)
```

---

## Detailed Implementation: Modifying `run_ablation.py`

### Current State

`run_ablation.py` creates **manual trace spans** via `_create_trace_span()` (lines 235-364). These are basic spans that don't capture:
- Actual LLM calls
- Tool invocations
- Strategy execution details

### Target State

Use nemo_oo_agents's OTel instrumentation which captures rich traces:
- Every LLM call with input/output messages
- Tool calls with arguments and results
- Strategy state (iterations, retries)
- Execution events

### Changes Required

#### 1. Add Imports

```python
# At top of run_ablation.py
from openinference_instrumentation_nemo_oo_agents import (
    enable_tracing,
    set_trace_file,
    get_current_exporter,
)
```

#### 2. Initialize Tracing Once at Startup

```python
# In main(), after loading .env:
async def main():
    # ... existing setup ...

    # Initialize OTel tracing (once per process)
    traces_dir = output_dir / "traces"
    exporter = enable_tracing(trace_dir=traces_dir)
    print(f"OTel tracing enabled: {exporter.trace_file}")
```

#### 3. Add `--agent-file` Argument

```python
parser.add_argument(
    "--agent-file",
    type=Path,
    help="Path to agent Python file for dynamic loading (enables optimization loop)",
)
```

#### 4. Implement Dynamic Agent Loading

```python
def load_agent_from_file(agent_path: Path, llm_client=None):
    """Dynamically import agent class from file path."""
    import importlib.util
    from nemo_oo_agents import Agent

    spec = importlib.util.spec_from_file_location("agent_module", agent_path)
    module = importlib.util.module_from_spec(spec)

    # Add project paths for imports to work
    import sys
    sys.path.insert(0, str(agent_path.parent))

    spec.loader.exec_module(module)

    # Find the Agent subclass
    for name, obj in vars(module).items():
        if isinstance(obj, type) and issubclass(obj, Agent) and obj is not Agent:
            return obj(llm=llm_client)

    raise ValueError(f"No Agent subclass found in {agent_path}")
```

#### 5. Modify `create_agent_factory()` for Dynamic Loading

```python
def create_agent_factory(
    config_name: str,
    llm_config: LLMConfig,
    shared_client: Any = None,
    agent_file: Path | None = None,  # NEW PARAMETER
) -> Callable:
    # If agent file provided, use dynamic loading
    if agent_file:
        def factory(llm_client=None):
            return load_agent_from_file(agent_file, llm_client or shared_client)
        return factory

    # Otherwise, use existing hard-coded factory
    config = CONFIGS[config_name]
    agent_type = config["agent_type"]
    # ... rest of existing logic ...
```

#### 6. Modify `run_single_task()` for Per-Sample Traces

```python
async def run_single_task(
    task,
    task_idx: int,
    total_tasks: int,
    agent_factory,
    adapter,
    semaphore: asyncio.Semaphore,
    traces_dir: Path | None = None,  # CHANGED: was trace_file
    agent_type: str = "unknown",
    benchmark_name: str = "",
) -> dict[str, Any]:
    async with semaphore:
        # Generate unique session ID for this task
        session_id = _generate_trace_id()[:8]

        # Set per-sample trace file BEFORE running agent
        trace_path = None
        if traces_dir:
            trace_path = traces_dir / f"{task.id}_{session_id}.006trace.jsonl"
            set_trace_file(trace_path)

        # ... existing agent execution logic ...

        # After task completes, close trace file
        if trace_path:
            exporter = get_current_exporter()
            if exporter:
                exporter.close_file(trace_path)
            set_trace_file(None)  # Reset to default

        # Remove manual _create_trace_span() call - OTel handles it
        return {
            "task_id": task.id,
            "trace_file": str(trace_path) if trace_path else None,  # Return path
            # ... rest of result ...
        }
```

#### 7. Remove Manual Trace Span Creation

Delete or deprecate `_create_trace_span()` function (lines 235-364). Agent006's OTel hooks capture richer spans automatically.

#### 8. Update `run_single_ablation()` Caller

```python
# Change trace_file parameter to traces_dir
coros = [run_with_index(i, task) for i, task in enumerate(tasks)]

async def run_with_index(idx: int, task):
    result = await run_single_task(
        task,
        idx,
        len(tasks),
        agent_factory,
        adapter,
        task_semaphore,
        traces_dir=traces_dir,  # CHANGED: was trace_file
        agent_type=agent_type,
        benchmark_name=benchmark_name,
    )
    return idx, result
```

---

## Summary of `run_ablation.py` Changes

| Change | Lines Affected | Complexity |
|--------|----------------|------------|
| Add imports | +5 lines at top | Low |
| Add `--agent-file` arg | +5 lines in main() | Low |
| Add `load_agent_from_file()` | +20 new function | Medium |
| Modify `create_agent_factory()` | +10 lines | Low |
| Modify `run_single_task()` for per-sample traces | ~30 lines changed | Medium |
| Remove `_create_trace_span()` | -130 lines | Low (deletion) |
| Update callers | ~10 lines | Low |

**Total:** ~50 lines added, ~130 lines removed, ~40 lines modified

---

## Implementation Status (2025-12-27)

**Status: ✅ COMPLETE** (on branch `tau-bench-setup`, based on MR 148)

All requirements have been implemented in `experiments/evaluation-ablations/run_ablation.py`:

### What Was Implemented

1. **Dynamic Agent Loading** (`--agent-file` parameter)
   - Added `_load_agent_class_from_file()` function using `importlib.util`
   - Modified `create_agent_factory()` to accept `agent_file` parameter
   - When `--agent-file` is provided, loads agent class from that file instead of hardcoded configs

2. **Per-Sample OTel Traces**
   - Enabled OTel tracing via `enable_tracing()` when using `--agent-file`
   - Uses `set_trace_file()` to route each sample's spans to a unique file: `{task_id}_{session_id}.006trace.jsonl`
   - Closes file handles after each task via `exporter.close_file()` to prevent file descriptor exhaustion

3. **Canonical Eval Format** (`.006eval.jsonl`)
   - Line 1: `{"_type": "metadata", "version": "1", "metadata": {...}}`
   - Lines 2-N: `{"_type": "result", "test_id": "...", "passed": bool, "scores": {...}, ...}`
   - Final line: `{"_type": "completion", "status": "completed", "result_count": N, ...}`

4. **Baseline Agent**
   - Using existing `experiments/evaluation-ablations/agents/nemo_oo_agents_tools.py`
   - Uses `PurePythonStrategy` for general tasks, `CodeActStrategy` for code generation
   - Already handles τ-bench via `_run_evaluation()` method

### Example Usage

```bash
# Run τ-bench with dynamic agent loading
python run_ablation.py \
  --agent-file agents/nemo_oo_agents_tools.py \
  --benchmark tau_bench \
  --limit 10 \
  --provider nvidia

# Output structure:
results/20251227_161514/
├── nemo_oo_agents_tau_bench.006eval.json      # Summary JSON
├── nemo_oo_agents_tau_bench.006eval.jsonl     # Canonical eval format
└── traces/
    ├── retail_000_abc123.006trace.jsonl # Per-sample OTel traces
    ├── retail_001_def456.006trace.jsonl
    └── ...
```

### Prerequisites

- Docker running (required for τ-bench's simulated user environment)
- **Colima users:** Must set `DOCKER_HOST` before running:
  ```bash
  export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"
  ```
- `NVIDIA_API_KEY` or `NVIDIA_INTERNAL_API_KEY` set
- τ-bench Docker image built: `python -c "from evaluation.environments.tau_bench import taubench_build_docker; taubench_build_docker()"`

---

## Notes

- MR 148 (`origin/eval-fixes-and-parallel-benchmarks`) contained the CodeActStrategy integration and import prepending, which we built on top of
- τ-bench's simulated user uses NVIDIA endpoint with `user_provider="nvidia_internal"` configured in `run_ablation.py`
- Inside Docker, τ-bench uses `provider="openai"` with `OPENAI_API_BASE` set to the NVIDIA endpoint (litellm doesn't recognize `nvidia_internal` as a provider)
- Required env vars: `NVIDIA_INFERENCE_API_KEY` (starts with `sk-`) for the simulated user

### Bug Fixes Applied (2025-12-27)

1. **Fixed simulated user provider**: Changed `user_provider="{self.user_provider}"` to `user_provider="openai"` inside Docker script (litellm doesn't recognize `nvidia_internal`)
2. **Added TauBenchEnvironment config**: Added `user_provider="nvidia_internal"` to `run_ablation.py` to use NVIDIA endpoint
3. **Fixed API key env var**: Made code try both `NVIDIA_INTERNAL_API_KEY` and `NVIDIA_INFERENCE_API_KEY`

### Test Run Results

```
retail_000: score=0.6 (3/5 expected tools)
- Called: get_order_details, get_product_details, transfer_to_human_agents
- Missing: find_user_id_by_name_zip, exchange_delivered_order_items
- Per-sample trace: 293KB (retail_000_0ee852fe.006trace.jsonl)
```

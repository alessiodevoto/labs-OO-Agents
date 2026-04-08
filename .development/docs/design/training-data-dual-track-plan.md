# Agent006 Training Data for Ultra: SFT + RL Dual-Track Plan

**Date**: 2026-02-23
**Author**: Agent006 Team

---

## Executive Summary

The model training team needs Agent006 trajectories to teach the Ultra model how to use Agent006's tools and workflow. We propose two parallel tracks to maximize the chance of hitting the Ultra timeline:

- **Track 1 (SFT)**: Generate complete example trajectories using our harness, convert to NeMo RL's OpenAI format, hand off as training data
- **Track 2 (RL/GRPO)**: Implement a NeMo RL `EnvironmentInterface` that wraps Agent006's benchmark environments, so the model can practice and get scored during GRPO training

Both tracks use the same benchmark seed data (SWE-bench, Terminal-Bench tasks) but serve different purposes in the training pipeline.

---

## Background: SFT vs RL

### SFT (Supervised Fine-Tuning) = "Learn by watching"

You record an expert agent solving hundreds of tasks. Each recording is a complete conversation: system prompt, user task, every tool call, every result, final answer. The model studies these recordings and learns to imitate them.

**Analogy**: Watching cooking videos. You see every step the chef takes and learn to replicate it.

**What we deliver**: A JSONL dataset of multi-turn conversations with tool calls.

### RL / GRPO (Group Relative Policy Optimization) = "Learn by doing"

You give the model a task, let it try to solve it, then give it a grade (reward signal). In NeMo RL, this is done via GRPO: for each prompt, the model generates multiple attempts, each is scored by an environment, and the model learns from the relative quality of its own attempts.

**Analogy**: Cooking with a judge. You try the recipe 16 different ways, the judge scores each attempt, and you learn which approaches work best.

**What we deliver**: A NeMo RL `EnvironmentInterface` implementation (a Ray remote actor) that can score agent trajectories.

### Why Both?

SFT gives the model a strong baseline: "this is what good Agent006 usage looks like." GRPO then pushes beyond that baseline: "now get even better." Most state-of-the-art agent training uses SFT first, then RL on top.

### What Are Sub-Trajectories?

When an Agent006 agent spawns a child agent (e.g., a summarization subagent or a reflection subagent), that child produces its own trace -- a "sub-trajectory." These are tracked via OpenTelemetry parent-child span relationships.

**Why they matter for RL**: The RL training loop needs to know which decisions to credit or blame. If the model makes a tool call that internally triggers a subagent (which is also the model), the training loop needs to decide: do I train on the subagent's decisions too? Without sub-trajectories, you can only see the top-level actions, making credit assignment harder.

**"Approximate without sub-trajectories"**: Flatten the interaction to a single agent level -- no nested agents. The model interacts directly with the environment via code execution, similar to NeMo RL's existing `CodeEnvironment`. This loses multi-phase orchestration but preserves the core coding agent skill.

---

## Track 1: SFT Data Generation

### Target Format: NeMo RL OpenAI Format

NeMo RL's `OpenAIFormatDataset` (in `nemo_rl/data/datasets/response_datasets/oai_format_dataset.py`) expects JSONL with this exact structure:

```json
{
  "messages": [
    {"role": "system", "content": "You are an Agent006 coding agent..."},
    {"role": "user", "content": "Fix this Django bug: ..."},
    {
      "role": "assistant",
      "content": "Let me explore the codebase first.",
      "tool_calls": [
        {
          "id": "call_1",
          "type": "function",
          "function": {
            "name": "execute_python",
            "arguments": {"code": "import os\nos.listdir('.')"}
          }
        }
      ]
    },
    {"role": "tool", "content": "['setup.py', 'django', 'tests', ...]", "tool_call_id": "call_1"},
    {
      "role": "assistant",
      "content": "Now let me read the relevant file.",
      "tool_calls": [
        {
          "id": "call_2",
          "type": "function",
          "function": {
            "name": "execute_python",
            "arguments": {"code": "with open('django/db/models/query.py') as f:\n    print(f.read())"}
          }
        }
      ]
    },
    {"role": "tool", "content": "class QuerySet: ...", "tool_call_id": "call_2"},
    {"role": "assistant", "content": "I've identified the bug and applied the fix."}
  ],
  "tools": [
    {
      "name": "execute_python",
      "description": "Execute Python code in a sandboxed environment",
      "parameters": {
        "code": {"type": "string", "description": "Python code to execute"}
      }
    },
    {
      "name": "return_result",
      "description": "Submit the final result",
      "parameters": {
        "value": {"type": "string", "description": "The result value"}
      }
    }
  ]
}
```

**Key requirements from NeMo RL source code:**
- Last message MUST be from the assistant role (enforced in `oai_format_dataset.py` line 193)
- MUST use `use_preserving_dataset: true` in training config because Agent006 tool calls have heterogeneous argument structures (different tools have different parameter schemas). Without this, HuggingFace's `load_dataset` would fill missing keys with `None`, corrupting the data.
- The `tools` field is optional but recommended -- it gets passed through to the chat template via `tokenizer.apply_chat_template()`

**NeMo RL training config for our data:**
```yaml
data:
  train:
    dataset_name: openai_format
    data_path: /path/to/nemo_oo_agents_sft_data.jsonl
    chat_key: "messages"
    tool_key: "tools"
    use_preserving_dataset: true   # CRITICAL for heterogeneous tool arguments
```

### Current Agent006 Infrastructure

| Component | Status | Notes |
|-----------|--------|-------|
| SWE-bench adapter | Ready | 300 tasks, Docker-based environments |
| Terminal-Bench adapter | Ready | ~100+ terminal tasks |
| Batch runner with tracing | Ready | Concurrent execution with per-task trace files |
| JSONL trace exporter | Ready | OpenTelemetry spans in `.006trace.jsonl` format |
| Docker sandbox environments | Ready | Isolated execution for benchmark tasks |
| **Trace-to-SFT converter** | **Needs building** | Convert OTel spans to NeMo RL OpenAI format |
| **Quality filtering pipeline** | **Needs building** | Filter successful, well-formed trajectories |

### The Gap: Trace-to-SFT Converter

Our traces are OpenTelemetry spans (span_id, parent_span_id, attributes). We need a converter that:

1. Reads `.006trace.jsonl` files
2. Reconstructs the conversation by following span parent-child hierarchy
3. Maps span types to conversation roles:
   - `generation` spans (LLM kind) -> system prompt + assistant messages
   - `code_execution` spans (TOOL kind) -> tool_call + tool_response pairs
   - `tool_execution.*` spans -> named tool calls (BashTool, etc.)
4. Outputs NeMo RL's OpenAI format JSONL

The span attributes are defined in our instrumentation hooks (`packages/openinference-instrumentation-nemo-oo-agents/src/openinference_instrumentation_nemo_oo_agents/_hooks_impl.py`):
- Agent spans: `openinference.span.kind=AGENT`, `agent.name`, `agent.method`
- Generation spans: `openinference.span.kind=LLM`, `generation.strategy`, `generation.result`
- Code execution spans: `openinference.span.kind=TOOL`, `tool.name=python_executor`, `code`, `result`
- Tool execution spans: `openinference.span.kind=TOOL`, `tool.name=<tool_name>`, `tool.arguments`, `tool.result`

### Execution Steps

1. **Build trace converter** -- new utility module (`util/trace_converter/`)
2. **Small batch validation** -- run 10 SWE-bench tasks, convert, manually verify format
3. **Run SWE-bench generation** (300 tasks, ~5 concurrent, Claude Sonnet via nvidia_internal)
4. **Run Terminal-Bench generation** (~100 tasks, ~10 concurrent)
5. **Convert and filter** -- success-only, validate last message is assistant, check tool_calls structure
6. **Quality audit** -- manual inspection of 20-30 samples
7. **Deliver** -- JSONL dataset + training config yaml + documentation

### Compute Estimate

| Benchmark | Tasks | Time/task | Concurrency | Wall-clock | API Cost |
|-----------|-------|-----------|-------------|------------|----------|
| SWE-bench Lite | 300 | 5-20 min | 5 | 5-12 hrs | ~$1,000-1,500 |
| Terminal-Bench | 100 | 1-5 min | 10 | 10-50 min | ~$50-100 |

**Expected yield**: ~30-50% solve rate for SWE-bench = **90-150 successful trajectories**. Terminal-Bench expected higher.

**Machine requirements**: Docker, 8+ CPU cores, 32GB RAM, fast network. I/O-bound (not GPU-bound).

### Target Models for Trajectory Generation

We plan to generate SFT trajectories using these models (subject to confirmation with the training team):

| Model | Provider | HuggingFace | Notes |
|-------|----------|-------------|-------|
| Qwen3.5-397B-A17B | nvidia / nvidia_internal | [Qwen/Qwen3.5-397B-A17B](https://huggingface.co/Qwen/Qwen3.5-397B-A17B) | MoE, strong coding performance |
| MiniMax-M2.5 | nvidia_internal | [MiniMaxAI/MiniMax-M2.5](https://huggingface.co/MiniMaxAI/MiniMax-M2.5) | Large MoE model |
| Claude Sonnet | nvidia_internal | N/A | Via `aws/anthropic/bedrock-claude-sonnet-4-5-v1` |

Generating with multiple models produces diverse trajectories, which may improve SFT quality. The training team should confirm which models they want data from.

### Risks

| Risk | Mitigation |
|------|------------|
| Low solve rate reduces data volume | Use multiple models; run more tasks; include near-miss trajectories |
| Model choice encodes specific behavior into training data | Generate with multiple models for diversity; confirm priorities with training team |
| Trajectory quality varies (backtracking, wandering) | Quality filtering pipeline; manual audit |
| Tool argument heterogeneity corrupts training data | Use `use_preserving_dataset: true` (confirmed in NeMo RL source) |

---

## Track 2: RL Environment for GRPO Training

### NeMo RL's Environment Interface

NeMo RL environments are Ray remote actors that implement `EnvironmentInterface` (from `nemo_rl/environments/interfaces.py`). The interface requires two methods:

```python
class EnvironmentInterface(abc.ABC, Generic[MetadataT]):
    @abc.abstractmethod
    def step(
        self,
        message_log_batch: list[LLMMessageLogType],  # Batch of conversation histories
        metadata: list[MetadataT],                     # Per-sample environment state
    ) -> EnvironmentReturn[MetadataT]:
        """Score a batch of model responses."""

    @abc.abstractmethod
    def global_post_process_and_metrics(
        self, batch: BatchedDataDict
    ) -> tuple[BatchedDataDict, dict]:
        """Compute aggregate metrics after all rollouts."""
```

The `step()` method returns an `EnvironmentReturn`:

```python
class EnvironmentReturn(NamedTuple, Generic[MetadataT]):
    observations: list[dict[str, str]]           # Feedback to the model
    metadata: list[MetadataT]                     # Updated environment state
    next_stop_strings: list[list[str] | None]     # Stop strings for next generation
    rewards: Tensor                               # Reward scores
    terminateds: Tensor                           # Episode-done flags
    answers: list[str | None] | None              # Extracted answers (optional)
```

### How GRPO Uses Environments

The GRPO training loop (in `nemo_rl/experience/rollouts.py`) works like this:

```
For each training step:
  1. Sample prompts from dataset
  2. Generate N responses per prompt (e.g., 16) via vLLM
  3. For each response:
     a. Call environment.step(message_log, metadata) -> rewards
     b. If multi-turn: append observation to message_log, generate again
     c. Repeat until terminated or max_rollout_turns
  4. Compute advantages from relative rewards within the group
  5. Update policy weights
```

Multi-turn rollouts are supported via `run_multi_turn_rollout()`, which calls `calculate_rewards()` -> `env.step()` in a loop, appending observations back to the conversation.

### NeMo RL Already Has a CodeEnvironment

NeMo RL ships with `CodeEnvironment` (`nemo_rl/environments/code_environment.py`) that:
- Extracts code from `<code>...</code>` tags in model responses
- Executes Python in a sandboxed environment (restricted builtins, blocked modules)
- Returns execution results as observations

**This is close to what Agent006 does**, but with key differences:
- Agent006 uses Docker containers for SWE-bench (real repo environments, test suites)
- Agent006's CodeAct uses `execute_python` tool calls, not `<code>` tags
- Agent006 evaluates via benchmark-specific test suites, not just code execution

### What We Need to Build: Agent006Environment

A NeMo RL `EnvironmentInterface` implementation that wraps Agent006's benchmark environments:

```python
@ray.remote
class Agent006Environment(EnvironmentInterface[Agent006Metadata]):
    """NeMo RL environment for Agent006 SWE-bench/Terminal-Bench tasks."""

    def __init__(self, config: Agent006EnvConfig):
        self.benchmark = config["benchmark"]  # "swebench" or "terminal_bench"
        self.adapter = get_adapter(self.benchmark)
        # Pre-load tasks and create Docker environments

    def step(
        self,
        message_log_batch: list[LLMMessageLogType],
        metadata: list[Agent006Metadata],
    ) -> EnvironmentReturn[Agent006Metadata]:
        """Extract code from model response, execute in Docker, return result."""
        # 1. Extract tool calls from latest assistant message
        # 2. Execute in Docker sandbox (delegating to Agent006 BenchmarkEnvironment)
        # 3. Return observation (execution output) + reward (test results)

    def global_post_process_and_metrics(self, batch):
        """Compute pass@k, solve rate, etc."""
```

### What "Approximate Without Sub-Trajectories" Means Concretely

Instead of the full Agent006 stack, the environment presents a **flat interaction** to the model:

| Full Agent006 Stack | Agent006Environment (Flat) |
|---------------------|---------------------------|
| Multi-phase workflow (brainstorm -> plan -> implement -> verify) | Single-phase: just solve |
| Subagent spawning | No nested agents |
| Reflexion strategy (self-critique loops) | No reflection |
| Summarization subagents | No summarization |
| `execute_python` + `return_result` via CodeAct strategy | Same tools, exposed via NeMo RL environment step |

This preserves the **core coding agent skill** (iterative code execution to solve problems) while dropping framework-level features that can be layered back as prompt scaffolding after training.

### GRPO Configuration for Agent006

```yaml
grpo:
  num_prompts_per_step: 16
  num_generations_per_prompt: 8      # 8 attempts per task
  max_rollout_turns: 30              # Agent006 tasks need many turns
  normalize_rewards: true
  use_leave_one_out_baseline: true

data:
  train:
    dataset_name: openai_format
    data_path: /path/to/nemo_oo_agents_tasks.jsonl    # Pre-exported task prompts
    chat_key: "messages"
    tool_key: "tools"
    use_preserving_dataset: true
    env_name: "nemo_oo_agents"                         # Points to our environment
    task_name: "nemo_oo_agents_swebench"

env:
  nemo_oo_agents:
    benchmark: "swebench"
    num_workers: 4                               # Docker containers
    max_steps_per_episode: 30
    terminate_on_evaluation: false                # Multi-turn
```

Environments are registered via `register_env()` in `nemo_rl/environments/utils.py`.

### Execution Steps

1. **Implement `Agent006Environment`** -- implements `EnvironmentInterface`, wraps our Docker-based benchmark environments
2. **Register in NeMo RL** -- add to `ENV_REGISTRY` or use `register_env()`
3. **Export task dataset** -- convert SWE-bench/Terminal-Bench tasks to NeMo RL's OpenAI format JSONL (same format as SFT, but only the prompt turns, no assistant responses)
4. **Validate with dry run** -- spin up environment, feed it a known-good trajectory, verify reward
5. **Package and deliver** -- Python package with environment, config YAML, task data, Docker setup docs
6. **Integration support** -- work with training team to test within their GRPO pipeline

### Compute Note

RL compute is spent at **training time**, not generation time. The environment implementation itself is cheap to build. The expensive parts are:
- **Docker containers**: Each rollout episode requires a container. With `num_generations_per_prompt=8` and 300 tasks, that's 2,400 episodes per epoch.
- **GPU time**: vLLM generation + GRPO weight updates are on the training team's infrastructure.
- Our contribution is the environment (execution + scoring), not the model training itself.

### Risks

| Risk | Mitigation |
|------|------------|
| Docker on training cluster | SWE-bench requires Docker. Verify training infra supports it. |
| Environment latency | Docker adds seconds per step. Profile with NeMo RL's rollout loop. |
| `max_rollout_turns` interaction with NeMo RL | Agent006 tasks need 10-30+ turns. Confirm GRPO handles long multi-turn rollouts. |
| Binary reward (tests pass/fail) may be too sparse | Add intermediate rewards: partial test passage, code compiles, etc. |
| Flat CodeAct doesn't cover full Agent006 behavior | Acceptable for v1; framework features added as prompt scaffolding post-training. |

---

## Questions for Upcoming Meetings

### For Ricardo's Team (SFT Generation)

1. **Confirm NeMo RL OpenAI format** -- we've read the source code and believe the format above is correct. Can they confirm, or do they need something different?
2. **Which model should generate trajectories?** Claude Sonnet, GPT-4o, or a specific model?
3. **Success-only or include failures?** Failed trajectories can be used for DPO (they support DPO in NeMo RL too)
4. **Volume target?** How many trajectories are needed? (determines compute budget)
5. **What seed data is being shared?** Are they providing SWE-bench task subsets, or should we use the full benchmark?

### For the RL / Ultra Team

1. **Docker on training cluster?** SWE-bench evaluation requires Docker containers. Can their GRPO training nodes run Docker?
2. **NeMo RL version?** We need to target the correct `EnvironmentInterface` -- the API may have changed between versions.
3. **`max_rollout_turns`?** Agent006 tasks typically need 10-30 turns. Has their GRPO pipeline been tested with long multi-turn rollouts?
4. **Is flat CodeAct acceptable for v1?** No sub-trajectories, no multi-phase workflow -- just execute_python + return_result against a Docker sandbox.
5. **Reward granularity?** Binary (tests pass/fail), or should we implement step-level intermediate rewards?
6. **What's the exact Ultra deadline?** Drives prioritization between tracks.

---

## Deliverables and Timeline

| Track | Deliverable | Dev Effort | Compute Cost | Timeline |
|-------|-------------|------------|-------------|----------|
| **SFT** | Trace-to-SFT converter (OTel spans -> NeMo RL OpenAI format) | ~3 days | - | Week 1 |
| **SFT** | Generated trajectory dataset (SWE-bench + Terminal-Bench) | ~1 day monitoring | ~$1,000-1,600 | Week 1-2 |
| **SFT** | Filtered + formatted training data + NeMo RL config yaml | ~1 day | - | Week 2 |
| **RL** | `Agent006Environment` implementing `EnvironmentInterface` | ~3-4 days | - | Week 1-2 |
| **RL** | Task dataset export (prompts in NeMo RL format) | ~1 day | - | Week 1 |
| **RL** | Validation, NeMo RL registration, GRPO config yaml | ~1-2 days | - | Week 2 |

Both tracks can start in parallel. **Track 1 (SFT) is more self-contained** -- we generate data and hand it off. **Track 2 (RL) requires closer collaboration** with the training team for NeMo RL integration.

### Recommended Sequencing

```
Week 1:
  SFT: Build trace converter + validate on 10 tasks end-to-end
  RL:  Implement Agent006Environment + export task dataset

Week 2:
  SFT: Run full-scale generation (300 + 100 tasks) + convert + filter
  RL:  Register environment in NeMo RL + validate with dry run + package

Week 3 (buffer):
  SFT: Quality audit + deliver final dataset with config yaml
  RL:  Integration support with training team's GRPO pipeline
```

---

## Appendix A: Agent006 Architecture Summary

For context on what the training data encodes:

```
User Task (e.g., "Fix Django bug #12345")
    |
    v
Agent006 Agent (CodeAct Strategy)
    |
    |-- System Prompt: framework context, tool descriptions, self-documentation
    |-- Tools: execute_python(code), return_result(value)
    |-- Optional: BashTool, FileTool, WebSearchTool, etc.
    |
    |-- Generation Loop:
    |     LLM generates Python code with tool calls
    |     -> Code executed in sandbox
    |     -> Result fed back as observation
    |     -> Repeat until return_result() or max iterations
    |
    |-- Tracing: Every LLM call, code execution, and tool use
    |            captured as OpenTelemetry spans in .006trace.jsonl
    |
    v
Evaluation: Run test suite, compute score (0.0 - 1.0)
```

## Appendix B: NeMo RL Reference

Key source files in the NeMo RL codebase (cloned to `3p/nemo-rl/`):

| Component | Path |
|-----------|------|
| EnvironmentInterface protocol | `nemo_rl/environments/interfaces.py` |
| CodeEnvironment (existing) | `nemo_rl/environments/code_environment.py` |
| MathEnvironment (reference) | `nemo_rl/environments/math_environment.py` |
| Environment registry | `nemo_rl/environments/utils.py` |
| OpenAIFormatDataset | `nemo_rl/data/datasets/response_datasets/oai_format_dataset.py` |
| PreservingDataset (for heterogeneous tools) | Same file, lines 24-85 |
| SFT processor | `nemo_rl/data/processors.py` |
| Chat template handling | `nemo_rl/data/llm_message_utils.py` |
| Multi-turn rollout loop | `nemo_rl/experience/rollouts.py` |
| GRPO training algorithm | `nemo_rl/algorithms/grpo.py` |
| Example GRPO config | `examples/configs/grpo_math_1B.yaml` |
| Example SFT config | `examples/configs/sft.yaml` |
| Test with heterogeneous tools | `tests/unit/data/datasets/test_preserving_dataset.py` |

NeMo RL SFT Guide: https://docs.nvidia.com/nemo/rl/latest/guides/sft.html
NeMo RL GRPO Guide: https://docs.nvidia.com/nemo/rl/latest/guides/grpo.html

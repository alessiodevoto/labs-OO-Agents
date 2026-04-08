# SFT Data Generation for SWE-bench

**Date**: 2026-02-25
**Status**: Pipeline implemented, experimental protocol defined

---

## Goal

Demonstrate that SFT on curated SWE-bench trajectories from strong models improves
Nemotron Nano v3's coding performance, specifically on tasks it fails out-of-the-box.

---

## Background

- **Original design**: `docs/plans/2026-02-24-sft-data-generation-design.md` (DABStep-focused)
- **Training data plan**: `docs/training-data-dual-track-plan.md` (NeMo RL format spec)
- **Agent006 framework**: SWE-bench adapter + Docker environments + event store + OpenAI formatter
- **Target format**: NeMo RL OpenAI format with `use_preserving_dataset: true`

This design supersedes the DABStep-focused design, pivoting to SWE-bench tasks.

---

## Experimental Protocol

### Phase 1: Oracle Test (prove SFT works at all)

Train on the test data itself. If SFT on SWE-bench Verified solutions doesn't improve
Nano's performance on those same tasks, the whole approach needs rethinking.

1. Run all models on SWE-bench Verified
2. Collect passing trajectories
3. SFT Nano on those trajectories
4. Re-evaluate Nano on the same tasks
5. Measure delta

### Phase 2: Gap Analysis (targeted training)

Don't waste signal on tasks Nano already solves. Focus SFT on the gap.

1. Run Nano on SWE-bench Verified → identify failures
2. Run strong models (MiniMax, Qwen, Nemotron Super) on the same tasks
3. Collect strong model trajectories for tasks **Nano failed but strong models solved**
4. SFT Nano on those gap trajectories
5. Re-evaluate Nano on the gap tasks
6. Compare: which teacher model produces the best trajectories?

### Phase 3: Ablations & Benchmark Climbing (optional, if Phase 1-2 show promise)

- **Benchmark climbing**: iterate (run → find new failures → add trajectories → retrain)
- **System prompt modes**: original vs minimal vs custom
- **Doctoring**: raw trajectories vs doctored (error loops removed)
- **Teacher model comparison**: MiniMax vs Qwen vs Nemotron Super
- **Curriculum ordering**: easy-to-hard based on reward variance

---

## Pipeline Overview

```
Phase 0: Self-Host Models
  Deploy strong models (MiniMax-M2.5, Qwen3.5-397B-A17B) on DGX/Slurm
  Also need Nemotron Nano v3 and Nemotron Super endpoints
  Expose as OpenAI-compatible endpoints

Phase 1: Generate Trajectories (all models)
  Agent006 framework + SWE-bench adapter
  Run each model on SWE-bench Verified tasks
  Capture event store + system prompt per task
  Output: raw trajectory JSON per (model, task) pair

Phase 2: Filter & Doctor Trajectories
  Stage 1: Filter — keep only tasks that pass SWE-bench evaluation
  Stage 2: Doctor — remove error→retry loops from successful trajectories
  Stage 3: Quality scoring — multiple rollouts, pick cleanest trajectory
  Stage 4: Validate — well-formedness checks on event sequences
  Output: curated trajectories + quality report

Phase 3: Gap Analysis
  Compare model results: which tasks does Nano fail that others solve?
  Select SFT training data from strong model trajectories for gap tasks
  Output: targeted SFT dataset

Phase 4: Convert to NeMo RL Format
  Map events → OpenAI messages
  Add tool definitions, ensure last message is assistant role
  Output: NeMo RL SFT dataset JSONL

Phase 5: Fine-tune & Evaluate
  SFT on Nemotron Nano v3 via NeMo Framework on Slurm
  Evaluate on SWE-bench Verified
  Output: comparison report (base vs fine-tuned, per-task delta)
```

---

## Key Design Decisions

### Trajectory Capture: Event Store Approach

The Agent006 framework has an in-memory event store (`InMemoryBackend`) that records
all events during agent execution. After each task completes, we dump the event store
to get the full untruncated conversation.

**Capture point**: `agent.event_manager.backend.all_events()`

**System prompt**: Rendered once at task start from context blocks. The system prompt
may evolve during the task (via `self.context.set()`), but for the initial design we
capture the initial version only.

**Why not intercept at LLM call level**: Simpler. The event store has the full
untruncated sequence. LLM calls apply context truncation which we don't want.

**Why not use OTel traces**: Reconstruction from spans is lossy and complex.
The event store is the direct source.

### Nested Generation

Agent006 supports nested agent calls (agent A calls agent B's method, which runs
its own CodeAct loop). The event pattern is:

```
ToolCallEvent(agent_A)       ← agent A's tool call
  ToolCallEvent(agent_B)     ← nested: agent B starts
  PythonOutput(agent_B)      ← agent B's result
PythonOutput(agent_A)        ← agent A's result (deferred)
```

ToolCallEvent and PythonOutput MUST remain as separate events. The "deferred output"
pattern exists specifically to handle this nesting.

### Context Block Capture (Deferred)

Three cases for context block changes:

1. **LLM sets context** (`self.context["notes"] = "..."`): Already captured —
   the LLMOutput event contains the code that set it.
2. **Developer sets context** (in agent setup): Not captured as events. Part of
   initial system prompt.
3. **Dynamic context** (`DynamicContext("expr")`): Not captured. Resolved silently
   each turn.

**Decision**: Defer. May need new event types in the framework to capture cases 2 and 3.
For now, capture initial system prompt and rely on LLM-generated context changes being
in the code events.

---

## Raw Trajectory Format

One file per task, capturing everything needed for downstream processing:

```json
{
  "task_id": "django__django-16379",
  "model": "Qwen/Qwen3.5-397B-A17B",
  "system_prompt": "You are a software engineer...",
  "tools": [
    {"name": "execute_python", "description": "...", "parameters": {...}},
    {"name": "return_result", "description": "...", "parameters": {...}}
  ],
  "events": [
    {"event_type": "task", "prompt": "Fix issue..."},
    {"event_type": "llm_output", "content": "..."},
    {"event_type": "tool_call", "name": "execute_python", "arguments": {...}, "result": {...}},
    {"event_type": "python_output", "tool_call_id": "...", "stdout": "...", "stderr": "...", ...},
    ...
  ],
  "evaluation": {
    "passed": true,
    "patch": "diff --git a/...",
    "test_results": {...}
  },
  "metadata": {
    "timestamp": "2026-02-25T...",
    "total_turns": 15,
    "total_tokens": 45000
  }
}
```

---

## Trajectory Cleaning Pipeline

### Stage 1: Filter by evaluation result

Keep only trajectories where `evaluation.passed == true`.

### Stage 2: Doctor (remove error loops)

Detect patterns: `[ToolCallEvent → PythonOutput(error) → LLMOutput(retry)]` repeated N times.
Collapse to keep only the final successful attempt. Preserve the "think" before the
final attempt (may contain the insight that led to the fix).

```
Before: [think → call_A → error → think → call_B → error → think → call_C → success]
After:  [think → call_C → success]
```

### Stage 3: Quality scoring (multiple rollouts)

Run the same task N times (3-5). If multiple pass, pick based on:
- Fewest turns (most direct path)
- Fewest error cycles (cleanest execution)
- Shortest total token count

### Stage 4: Validation

- Event sequence well-formed (tool_call_ids match, roles alternate correctly)
- Last event maps to assistant role
- No orphaned tool calls or results

---

## NeMo RL Format Conversion

### Event → OpenAI message mapping

| Event Type | OpenAI Message |
|---|---|
| `Task` | `{"role": "user", "content": task.prompt}` |
| `LLMOutput` | `{"role": "assistant", "content": ...}` |
| `ToolCallEvent` | `{"role": "assistant", "content": null, "tool_calls": [...]}` |
| `ToolCallEvent.result` | `{"role": "tool", "tool_call_id": ..., "content": ...}` |
| `PythonOutput` | `{"role": "user", "content": formatted(stdout/stderr/error)}` |
| `Error` | `{"role": "user", "content": ...}` |
| `Feedback` | `{"role": "user", "content": ...}` |
| `Message` | `{"role": "assistant", "content": ...}` |
| `Reasoning` | `{"role": "assistant", "content": ...}` |
| `BeforeTurn`/`AfterTurn` | skip |
| `Summary` | skip |

### Output format (JSONL, one line per task)

```json
{
  "messages": [
    {"role": "system", "content": "<initial system prompt>"},
    {"role": "user", "content": "Fix issue #1234..."},
    {"role": "assistant", "content": "Let me examine..."},
    {"role": "assistant", "content": null, "tool_calls": [...]},
    {"role": "tool", "tool_call_id": "...", "content": "status: complete"},
    {"role": "user", "content": "Stdout:\n..."},
    ...
    {"role": "assistant", "content": "The fix has been applied."}
  ],
  "tools": [
    {"name": "execute_python", "description": "...", "parameters": {...}},
    {"name": "return_result", "description": "...", "parameters": {...}}
  ]
}
```

### System Prompt Strategy

The system prompt in the SFT data is **configurable at conversion time** (not baked in
during generation). The raw trajectory preserves the full original system prompt.
At conversion, choose a mode:

- **`original`**: Use the full Agent006 system prompt from generation. Good for
  training a model that will run inside Agent006.
- **`minimal`**: Strip to a short instruction ("You are a software engineer. Fix the
  described issue. Be concise."). This is the OpenCode/Nano team's approach — SFT bakes
  tool-use behavior into the model so the prompt can be minimal.
- **`custom`**: Provide an arbitrary system prompt string.

**Insight**: The purpose of SFT is to teach the model how to use tools and solve tasks.
A verbose system prompt is a crutch for base models that don't know the harness.
After SFT, the model should "just know" — so minimal prompts may produce better
generalization.

### Constraints

- Last message MUST be `role: assistant`
- `tool_calls` arguments serialized as JSON strings
- NeMo RL config: `use_preserving_dataset: true` (heterogeneous tool arguments)

---

## Models

### Models to run on SWE-bench Verified

| Role | Model | Notes |
|---|---|---|
| Teacher (strong) | MiniMax-M2.5 | Large MoE, vLLM with tool-call parser |
| Teacher (strong) | Qwen3.5-397B-A17B | MoE, strong coding |
| Baseline (student) | Nemotron Nano v3 | SFT target — measure before/after |
| Baseline (strong internal) | Nemotron Super | If available — additional teacher signal |

All deployed on DGX/Slurm, exposed as OpenAI-compatible endpoints.

### Fine-tuning target

Nemotron Nano v3 — SFT via NeMo Framework on Slurm.

### Data strategy

- **Phase 1 (oracle test)**: Train and test on SWE-bench Verified (intentional overfit)
- **Phase 2 (gap analysis)**: Train on strong model trajectories for Nano's failure cases
- **Phase 3 (production)**: Training tasks from training team, SWE-bench Verified for eval

---

## Directory Structure

```
experiments/sft-datagen/
  README.md                          # experiment docs
  journal.md                         # running notes & decisions

  # Phase 0: Model hosting
  model/
    deploy.sh                        # NIM deployment script
    test_endpoint.py                 # verify model is serving

  # Phase 1: Trajectory generation
  generate/
    run_generation.py                # orchestrates agent runs on task set
    trajectory_dumper.py             # hooks into agent, dumps event store after each task

  # Phase 2: Cleaning
  clean/
    filter_passed.py                 # Stage 1: keep passing trajectories
    doctor_trajectories.py           # Stage 2: remove error loops
    score_quality.py                 # Stage 3: rank/select best rollouts
    validate.py                      # Stage 4: well-formedness checks

  # Phase 3: Conversion
  convert/
    to_nemo_rl.py                    # event stream → NeMo RL OpenAI format

  # Phase 4: Training & eval
  train/
    sft_config.yaml                  # NeMo RL training config
    eval_finetuned.py                # run SWE-bench Verified on fine-tuned model

  # Data (gitignored, large files)
  data/
    tasks/                           # input task definitions
    trajectories/raw/                # Phase 1 output
    trajectories/cleaned/            # Phase 2 output
    datasets/                        # Phase 3 output (NeMo RL JSONL)

  # Reports
  reports/
    generation_report.md
    cleaning_report.md
    training_report.md
```

---

## NeMo RL Training Config

```yaml
data:
  train:
    dataset_name: openai_format
    data_path: /path/to/swebench_sft_dataset.jsonl
    chat_key: "messages"
    tool_key: "tools"
    use_preserving_dataset: true   # CRITICAL for heterogeneous tool arguments
```

---

## Open Questions

| Question | Status |
|---|---|
| Context block capture (developer-set, dynamic) | Deferred — may need new framework event types |
| Nemotron Super availability | TBD — check with team |
| Number of rollouts per task | TBD — start with 1, increase if yield is low |
| LLM judge for trajectory quality scoring | TBD — may not be needed |
| Consecutive same-role message merging | TBD — test with NeMo RL |
| NeMo Framework SFT config specifics | TBD — need to validate with training team |
| Which cluster for generation runs | Have access to one cluster now, DFW/LAX later |

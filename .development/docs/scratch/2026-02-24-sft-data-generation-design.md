# DABStep SFT Data Generation Design

**Date**: 2026-02-24
**Author**: rcabral
**Branch**: feat/sft-data-generation

---

## Goal

Generate a high-quality SFT (Supervised Fine-Tuning) dataset from DABStep trajectories using
opt63 agent running on an open-source model, for Ultra model training via NeMo RL.

---

## Background

- **MR 371** (`docs/training-data-dual-track-plan.md`): Defines NeMo RL OpenAI SFT format and converter architecture.
- **MR 376** (`docs/open-source-llm-benchmark-survey-2026-02.md`): Defines allowed open-source models for SFT data generation (permissive licenses).
- **opt63** (`agents/agent_opt63.py`): Best manual optimization agent, ~90% on training set.
- **`dabstep-full-solutions.jsonl`**: 450 correct answers (ground truth) for all DABStep tasks.

---

## Target SFT Format (NeMo RL OpenAI Format)

```json
{
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {
      "role": "assistant",
      "content": "...",
      "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "execute_python", "arguments": {"code": "..."}}}]
    },
    {"role": "tool", "content": "...", "tool_call_id": "call_1"},
    {"role": "assistant", "content": "Final answer."}
  ],
  "tools": [
    {"name": "execute_python", "description": "...", "parameters": {...}},
    {"name": "return_result", "description": "...", "parameters": {...}}
  ]
}
```

**Critical requirements** (from NeMo RL source):
- Last message MUST be `role: assistant`
- Use `use_preserving_dataset: true` in training config (heterogeneous tool args)

---

## Architecture

```
Step 0: ~/.claude/CLAUDE.md
        Move "always use mcp__unrestricted-bash__bash" rule to user-level CLAUDE.md
        so it persists across all worktrees

Step 1: Worktree setup
        .worktrees/ added to .gitignore
        New branch: feat/sft-data-generation
        Worktree at: .worktrees/sft-data-generation/

Step 2: Fix opt63 model
        File: agents/rsc_dab_agent_hard_opt63.py (evaluation-ablations)
        File: util/e2e_optimization/.../agents/agent_opt63.py
        Change: FakeLLMClient() → CompletionClient for DeepSeek V3.2 on NIM
        Model: deepseek-ai/deepseek-v3.2 (77.2% SWE-bench, MIT license, on NIM)
        NIM endpoint: https://integrate.api.nvidia.com/v1
        API key: NVIDIA_API_KEY

Step 3: Full dataset run
        Script: experiments/evaluation-ablations/run_ablation.py
        Command:
          python run_ablation.py \
            --agent-file agents/rsc_dab_agent_hard_opt63.py \
            --benchmark dabstep \
            --provider nvidia \
            --model deepseek-ai/deepseek-v3.2 \
            --concurrent-tasks 10 \
            --output-dir results/dabstep/opt63_deepseek_fullrun
        Output: results/dabstep/opt63_deepseek_fullrun/
                  nemo_oo_agents_dabstep.006eval.json  (answers per task)
                  traces/                          (per-task .006trace.jsonl files)

Step 4: build_sft_dataset.py
        Location: experiments/evaluation-ablations/build_sft_dataset.py
        Input:  --eval-json results/dabstep/opt63_deepseek_fullrun/nemo_oo_agents_dabstep.006eval.json
                --solutions dabstep-full-solutions.jsonl
                --traces-dir results/dabstep/opt63_deepseek_fullrun/traces/
                --output dabstep_sft_deepseek_v3.2_20260224.jsonl
        Logic:
          1. Load solutions → {task_id: correct_answer}
          2. Load eval results → {task_id: {answer, trace_file, ...}}
          3. For each task where answer matches correct_answer (ExactMatch, case-insensitive):
             a. Find the trace file
             b. Load all acompletion spans
             c. Sort by start_time
             d. Merge into single messages array (flatten all sub-agents chronologically)
             e. Write NeMo RL record to output JSONL
          4. Print statistics: total tasks / correct / converted
```

---

## Model: DeepSeek V3.2

| Field | Value |
|-------|-------|
| **NIM ID** | `deepseek-ai/deepseek-v3.2` |
| **Provider** | `nvidia` (integrate.api.nvidia.com) |
| **License** | MIT (outputs usable for training) |
| **SWE-bench Verified** | 77.2% |
| **LiveCodeBench** | 83.3% |
| **Parameters** | 685B total (MoE) |
| **Context** | 128K tokens |

---

## Answer Matching Logic

Exact string match after:
- Strip leading/trailing whitespace
- Lowercase comparison

Matches the `ExactMatchScorer` used in evaluation.

---

## SFT Conversation Flattening

For tasks with sub-agents (RulesLawyer, SolutionVerifier):
1. Collect ALL `acompletion` spans from the trace file
2. Sort by `start_time` (natural order: RulesLawyer → main → Verifier)
3. Use the FIRST system prompt as the conversation opener
4. For subsequent agents: skip their system prompt, merge messages in order
5. Result: single continuous conversation showing full reasoning across all agents

---

## Expected Output

- ~50-70% of 450 tasks expected correct (opt63 + DeepSeek V3.2)
- Output: ~225-315 SFT training examples
- Each example: ~20-80 message turns

---

## NeMo RL Training Config

```yaml
data:
  train:
    dataset_name: openai_format
    data_path: /path/to/dabstep_sft_deepseek_v3.2_20260224.jsonl
    chat_key: "messages"
    tool_key: "tools"
    use_preserving_dataset: true   # CRITICAL for heterogeneous tool arguments
```

---

## Files Created/Modified

| File | Action |
|------|--------|
| `~/.claude/CLAUDE.md` | CREATE — user-level bash MCP instruction |
| `CLAUDE.md` | MODIFY — remove bash MCP instruction (moved to user settings) |
| `.gitignore` | MODIFY — add `.worktrees/` |
| `agents/rsc_dab_agent_hard_opt63.py` | MODIFY — fix FakeLLMClient default |
| `util/e2e_optimization/.../agents/agent_opt63.py` | MODIFY — same fix |
| `experiments/evaluation-ablations/build_sft_dataset.py` | CREATE — SFT converter |
| `results/dabstep/opt63_deepseek_fullrun/` | RUNTIME — full run output |
| `dabstep_sft_deepseek_v3.2_YYYYMMDD.jsonl` | RUNTIME — SFT dataset |

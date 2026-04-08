# SFT Trajectory Generation — Project Plan

**Date**: 2026-02-25
**Status**: Approved
**Timeline**: This week (days, not weeks)

---

## Scope

### Our Responsibility

Generate SFT trajectory data from SWE-bench Verified using Harbor + Agent006, and
deliver NeMo RL JSONL to the SFT training colleague.

**Specifically:**
1. Run Harbor locally against a remote model endpoint
2. Convert `.006trace.jsonl` output → NeMo RL JSONL (via post-processing, no Harbor modifications)
3. Start with one model, expand to more if time permits

### Colleague Responsibilities

- **Colleague A**: Get Harbor running on Slurm (scaled generation)
- **Colleague B**: SFT training pipeline (NeMo Framework) + evaluation

### Deliverables

- Working `trace → NeMo RL JSONL` converter
- Actual trajectory data from at least one model on SWE-bench Verified
- Cleaning pipeline applied (filter passed, doctor error loops, validate)

---

## Experimental Protocol (from Ricardo)

### Phase 1: Oracle Test

Train on the test data itself. If SFT doesn't help, stop.

1. Run model(s) on SWE-bench Verified
2. Collect passing trajectories → NeMo RL JSONL
3. SFT Nano on those trajectories (colleague B)
4. Re-evaluate Nano on same tasks
5. Measure delta

### Phase 2: Gap Analysis

Focus SFT on tasks Nano fails out-of-the-box.

1. Run Nano on SWE-bench Verified → identify failures
2. Run strong models on same tasks
3. Collect strong model trajectories for Nano's failure cases only
4. SFT Nano on gap trajectories
5. Compare: which teacher model helps most?

### Phase 3: Ablations (if Phase 1-2 show promise)

Benchmark climbing, system prompt modes, doctoring, curriculum ordering.

---

## Architecture

### Trajectory Capture: OTel Trace Post-Processing

Harbor already produces `.006trace.jsonl` files with full OTel spans. The `acompletion`
spans contain complete OpenAI-format conversations:

- System prompt: `llm.input_messages.0.message.content`
- Full message history: `llm.input_messages.{i}.*`
- Tool definitions: `llm.tools.{i}.tool.json_schema`
- LLM output: `llm.output_messages.*`
- Model name, token counts, inference params

**No Harbor code changes needed.** We post-process the traces.

### Known Edge Cases (to validate)

1. **Summarization**: If conversation gets long, earlier turns may be collapsed.
   The last `acompletion` span would have summarized history, not the full original.
2. **Context block changes**: `self.context.set()` mid-task changes the system prompt
   in later spans. Each span captures what the LLM saw that turn.

**Decision**: Validate first on real traces, then decide conversion strategy.

### Pipeline

```
Harbor run on SWE-bench Verified
    ↓
.006trace.jsonl (one per task)
    ↓
trace_to_nemo_rl.py (extract acompletion spans → OpenAI messages)
    ↓
cleaning.py (filter passed, doctor error loops, validate)
    ↓
NeMo RL JSONL (hand to SFT colleague)
```

### Existing Code We Keep

- `sft_datagen/cleaning.py` — filter, doctor, validate (operates on messages)
- `sft_datagen/nemo_converter.py` — system prompt mode switching
- `convert/to_nemo_rl.py` — CLI for batch conversion
- `clean/run_cleaning.py` — CLI for batch cleaning

### Existing Code That Becomes Optional

- `sft_datagen/event_serializer.py` — EventManager path (not needed for trace path)
- `sft_datagen/trajectory_dumper.py` — EventManager path (not needed for trace path)

---

## Plan of Attack

### Step 1: Validate traces (~1 hour)

Run Harbor on 3-5 SWE-bench Verified tasks. Inspect the `.006trace.jsonl` files:
- How many `acompletion` spans per task?
- Did summarization happen?
- Did context blocks change?
- Is the last span's message history complete?

### Step 2: Build trace converter (2-4 hours)

Based on what we see in Step 1:
- **Simple case**: Last `acompletion` span has full conversation → extract directly
- **Complex case**: Summarization happened → stitch all spans turn-by-turn

Write `trace_to_nemo_rl.py` with tests.

### Step 3: Full generation run (~hours, depends on task count)

Run Harbor on all SWE-bench Verified tasks (500). Convert all passing traces.
Apply cleaning pipeline. Produce final NeMo RL JSONL.

### Step 4: Scale to more models (if time permits)

Repeat with additional models. Compare pass rates. Prepare gap analysis data
for Phase 2.

---

## Models

Starting with one model, expanding as time allows:

| Priority | Model | Notes |
|----------|-------|-------|
| 1st | TBD (whichever endpoint is ready) | Prove the pipeline works |
| 2nd+ | MiniMax-M2.5, Qwen3.5-397B-A17B, Nano, Super | Gap analysis |

---

## Dependencies

| Dependency | Owner | Status |
|------------|-------|--------|
| Model endpoint | Us (already have one) | Ready |
| Harbor running locally | Us | Working |
| Harbor on Slurm | Colleague A | In progress |
| NeMo Framework SFT pipeline | Colleague B | In progress |
| SWE-bench Verified task list | HuggingFace dataset | Available |

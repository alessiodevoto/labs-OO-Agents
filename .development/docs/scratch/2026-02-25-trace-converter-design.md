# Trace Converter Design

**Date**: 2026-02-25
**Status**: Approved

---

## Goal

Convert `.006trace.jsonl` files (OTel traces from Harbor+Agent006 runs) into NeMo RL
JSONL format for SFT training. No Harbor modifications needed — pure post-processing.

## Files

- `sft_datagen/trace_converter.py` — library with `extract_conversation()` and `to_nemo_rl()`
- `convert/trace_to_nemo_rl.py` — CLI that wires them together over a glob of trace files

## Pipeline

```
.006trace.jsonl
    │
    ▼
extract_conversation(trace_path) → Conversation dict
    │
    ▼
to_nemo_rl(conversation) → {"messages": [...], "tools": [...]}
    │
    ▼
NeMo RL JSONL (one line per task)
```

## `extract_conversation(trace_path) → dict`

**Input**: Path to a `.006trace.jsonl` file.

**Span filtering**: Agent006-instrumented spans have `llm.model_name` set and
`llm.input_messages.0.message.role` present. LiteLLM auto-instrumented spans have
neither — skip those.

**Message extraction**: From the last qualifying span (has the fullest conversation),
walk `llm.input_messages.{i}.message.*` attributes to reconstruct each OpenAI message
(role, content, tool_calls, tool_call_id). Append `llm.output_messages.0.*` as the
final assistant message.

**Tool extraction**: Parse `llm.invocation_parameters` JSON string, extract the `tools`
array (typically `execute_python` and `return_result`).

**Context diffs**: Compare system prompts (`llm.input_messages.0.message.content`)
across consecutive spans. Parse XML blocks (`<key expr="...">value</key>`). Detect
blocks that were added or changed between spans. Record each diff with the span index
mapped to a message index in the final conversation.

**Output**:

```python
{
    "messages": [...],          # OpenAI format message list
    "tools": [...],             # tool definitions from invocation_parameters
    "context_diffs": [          # detected context block changes
        {"after_index": 7, "key": "repo_overview", "value": "..."},
    ],
    "metadata": {
        "task_id": "sympy__sympy-19346",
        "model": "openai/nvidia/nvidia/nemotron-3-super-preview",
        "total_spans": 12,
        "total_messages": 39,
    }
}
```

## `to_nemo_rl(conversation) → dict`

**Input**: Conversation dict from `extract_conversation()`.

**Context diff injection**: Uses existing `_format_context_update()` from nemo_converter
to create XML-formatted messages for each context diff, inserts at the right position.

**Last message check**: If last message isn't assistant role, append
`{"role": "assistant", "content": "Task completed."}`.

**Output**: `{"messages": [...], "tools": [...]}` — one NeMo RL record.

## CLI: `convert/trace_to_nemo_rl.py`

```bash
python convert/trace_to_nemo_rl.py \
    --input "results/task_*.006trace.jsonl" \
    --output data/datasets/sft_dataset.jsonl
```

Globs input files. Calls `extract_conversation()` → `to_nemo_rl()` for each.
Writes one JSONL line per trace. Reports stats (converted, errors, total messages).

## Deferred

- **Cleaning** (`clean_messages()`): doctor error loops, validation. Add later.
- **System prompt modes**: minimal, custom. Just use original for now.
- **Multi-sample extraction**: one sample per turn instead of last-span-only. Add later.

## Trace Format Reference

OTel span attributes for `acompletion` spans:

| Attribute pattern | Example |
|---|---|
| `llm.input_messages.{i}.message.role` | `"system"`, `"user"`, `"assistant"`, `"tool"` |
| `llm.input_messages.{i}.message.content` | message text |
| `llm.input_messages.{i}.message.tool_calls.{j}.tool_call.function.name` | `"execute_python"` |
| `llm.input_messages.{i}.message.tool_calls.{j}.tool_call.function.arguments` | JSON string |
| `llm.input_messages.{i}.message.tool_calls.{j}.tool_call.id` | `"call-366f145"` |
| `llm.input_messages.{i}.message.tool_call_id` | `"call-366f145"` (for tool role) |
| `llm.output_messages.0.message.*` | same structure as input |
| `llm.invocation_parameters` | JSON with `tools`, `model`, `temperature`, etc. |
| `llm.model_name` | `"openai/nvidia/nvidia/nemotron-3-super-preview"` |

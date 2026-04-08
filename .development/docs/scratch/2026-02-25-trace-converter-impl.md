# Trace Converter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert `.006trace.jsonl` OTel trace files into NeMo RL JSONL format for SFT training, using pure post-processing (no Harbor modifications).

**Architecture:** Two functions in one library module: `extract_conversation()` reads a trace file, filters to agent006 spans (have `llm.model_name`), extracts OpenAI-format messages + tools + context diffs from the last span with the most messages. `to_nemo_rl()` takes that conversation dict and produces a NeMo RL record by injecting context diffs and ensuring the last message is assistant. A thin CLI wraps them for batch processing.

**Tech Stack:** Python 3.11+, pytest, json, re, pathlib. Reuses `_format_context_update()` and `inject_context_updates()` from `sft_datagen/nemo_converter.py`.

**Design doc:** `docs/plans/2026-02-25-trace-converter-design.md`

**Real trace for integration tests:** `results/task_b891437b_b891437b.006trace.jsonl`

---

## Background: OTel Trace Attribute Format

Spans in `.006trace.jsonl` are JSON objects with flat `attributes` dicts. Messages are stored as:

```
llm.input_messages.{i}.message.role          → "system" | "user" | "assistant" | "tool"
llm.input_messages.{i}.message.content       → string (may be absent on assistant with tool_calls)
llm.input_messages.{i}.message.tool_calls.{j}.tool_call.function.name       → "execute_python"
llm.input_messages.{i}.message.tool_calls.{j}.tool_call.function.arguments  → JSON string
llm.input_messages.{i}.message.tool_calls.{j}.tool_call.id                  → "call-abc123"
llm.input_messages.{i}.message.tool_call_id  → "call-abc123" (tool role only)
```

Output messages have the same structure under `llm.output_messages.{i}.*` but tool_calls lack `.id`.

Agent006-instrumented spans have `llm.model_name` set. LiteLLM auto-instrumented spans do NOT — skip those.

Tools are in `llm.invocation_parameters` (a JSON string containing a `tools` array).

---

## Task 1: Load and Filter Trace Spans

**Files:**
- Create: `experiments/sft_datagen/sft_datagen/trace_converter.py`
- Test: `experiments/sft_datagen/tests/test_trace_converter.py`

### Step 1: Write the failing test

```python
# experiments/sft_datagen/tests/test_trace_converter.py
"""Tests for trace_converter module."""

from __future__ import annotations
import json
import pytest
from pathlib import Path
from sft_datagen.trace_converter import load_agent_spans


def _make_span(name: str, attrs: dict) -> dict:
    """Helper to build a minimal OTel span."""
    return {
        "name": name,
        "context": {"trace_id": "abc", "span_id": "001"},
        "attributes": attrs,
    }


class TestLoadAgentSpans:
    def test_filters_to_acompletion_with_model_name(self):
        spans_jsonl = [
            # Agent006 acompletion span — should be kept
            _make_span("acompletion", {
                "llm.model_name": "openai/test-model",
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": "You are helpful.",
            }),
            # LiteLLM acompletion span — no model_name, skip
            _make_span("acompletion", {
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": "You are helpful.",
            }),
            # Non-acompletion span — skip
            _make_span("generation", {
                "llm.model_name": "openai/test-model",
            }),
        ]
        result = load_agent_spans(spans_jsonl)
        assert len(result) == 1
        assert result[0]["attributes"]["llm.model_name"] == "openai/test-model"

    def test_empty_input(self):
        assert load_agent_spans([]) == []
```

### Step 2: Run test to verify it fails

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestLoadAgentSpans -v`
Expected: FAIL with `ImportError: cannot import name 'load_agent_spans'`

### Step 3: Write minimal implementation

```python
# experiments/sft_datagen/sft_datagen/trace_converter.py
"""Convert .006trace.jsonl OTel traces to NeMo RL JSONL format."""

from __future__ import annotations
from typing import Any


def load_agent_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter spans to agent006-instrumented acompletion spans.

    Agent006 spans have `llm.model_name` set. LiteLLM auto-instrumented
    spans do not — skip those.
    """
    return [
        s for s in spans
        if s.get("name") == "acompletion"
        and "llm.model_name" in s.get("attributes", {})
    ]
```

### Step 4: Run test to verify it passes

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestLoadAgentSpans -v`
Expected: PASS

### Step 5: Commit

```bash
git add experiments/sft_datagen/sft_datagen/trace_converter.py experiments/sft_datagen/tests/test_trace_converter.py
git commit -m "feat(trace-converter): add load_agent_spans with span filtering"
```

---

## Task 2: Extract Messages from Span Attributes

**Files:**
- Modify: `experiments/sft_datagen/sft_datagen/trace_converter.py`
- Test: `experiments/sft_datagen/tests/test_trace_converter.py`

### Step 1: Write the failing test

Add to `test_trace_converter.py`:

```python
from sft_datagen.trace_converter import extract_messages_from_span


class TestExtractMessagesFromSpan:
    def test_system_and_user_messages(self):
        attrs = {
            "llm.input_messages.0.message.role": "system",
            "llm.input_messages.0.message.content": "You are helpful.",
            "llm.input_messages.1.message.role": "user",
            "llm.input_messages.1.message.content": "Fix the bug.",
        }
        messages = extract_messages_from_span(attrs)
        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "You are helpful."}
        assert messages[1] == {"role": "user", "content": "Fix the bug."}

    def test_assistant_with_tool_calls(self):
        attrs = {
            "llm.input_messages.0.message.role": "assistant",
            "llm.input_messages.0.message.tool_calls.0.tool_call.function.name": "execute_python",
            "llm.input_messages.0.message.tool_calls.0.tool_call.function.arguments": '{"code": "print(1)"}',
            "llm.input_messages.0.message.tool_calls.0.tool_call.id": "call-abc",
        }
        messages = extract_messages_from_span(attrs)
        assert len(messages) == 1
        msg = messages[0]
        assert msg["role"] == "assistant"
        assert msg.get("content") is None
        assert len(msg["tool_calls"]) == 1
        tc = msg["tool_calls"][0]
        assert tc["id"] == "call-abc"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "execute_python"
        assert tc["function"]["arguments"] == '{"code": "print(1)"}'

    def test_tool_message(self):
        attrs = {
            "llm.input_messages.0.message.role": "tool",
            "llm.input_messages.0.message.content": "status: complete",
            "llm.input_messages.0.message.tool_call_id": "call-abc",
        }
        messages = extract_messages_from_span(attrs)
        assert len(messages) == 1
        assert messages[0] == {
            "role": "tool",
            "content": "status: complete",
            "tool_call_id": "call-abc",
        }

    def test_assistant_with_content_no_tool_calls(self):
        attrs = {
            "llm.input_messages.0.message.role": "assistant",
            "llm.input_messages.0.message.content": "I'll help you.",
        }
        messages = extract_messages_from_span(attrs)
        assert messages[0] == {"role": "assistant", "content": "I'll help you."}

    def test_multiple_tool_calls_on_one_message(self):
        attrs = {
            "llm.input_messages.0.message.role": "assistant",
            "llm.input_messages.0.message.tool_calls.0.tool_call.function.name": "execute_python",
            "llm.input_messages.0.message.tool_calls.0.tool_call.function.arguments": '{"code": "x=1"}',
            "llm.input_messages.0.message.tool_calls.0.tool_call.id": "call-1",
            "llm.input_messages.0.message.tool_calls.1.tool_call.function.name": "return_result",
            "llm.input_messages.0.message.tool_calls.1.tool_call.function.arguments": '{"result": "done"}',
            "llm.input_messages.0.message.tool_calls.1.tool_call.id": "call-2",
        }
        messages = extract_messages_from_span(attrs)
        assert len(messages[0]["tool_calls"]) == 2
        assert messages[0]["tool_calls"][0]["function"]["name"] == "execute_python"
        assert messages[0]["tool_calls"][1]["function"]["name"] == "return_result"
```

### Step 2: Run test to verify it fails

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestExtractMessagesFromSpan -v`
Expected: FAIL with `ImportError: cannot import name 'extract_messages_from_span'`

### Step 3: Write minimal implementation

Add to `trace_converter.py`:

```python
def extract_messages_from_span(
    attrs: dict[str, Any],
    prefix: str = "llm.input_messages",
) -> list[dict[str, Any]]:
    """Extract OpenAI-format messages from span attributes.

    Walks llm.input_messages.{i}.message.* attributes sequentially (0, 1, 2...)
    and reconstructs each message dict with role, content, tool_calls, tool_call_id.
    """
    messages: list[dict[str, Any]] = []
    i = 0
    while True:
        role = attrs.get(f"{prefix}.{i}.message.role")
        if role is None:
            break

        msg: dict[str, Any] = {"role": role}

        # Content (may be absent on assistant messages with tool_calls)
        content = attrs.get(f"{prefix}.{i}.message.content")
        if content is not None:
            msg["content"] = content

        # Tool calls (assistant role)
        tool_calls = _extract_tool_calls(attrs, f"{prefix}.{i}.message.tool_calls")
        if tool_calls:
            msg["tool_calls"] = tool_calls
            # Omit content key entirely if absent (not even None)
            if content is None and "content" not in msg:
                pass  # already absent

        # tool_call_id (tool role)
        tool_call_id = attrs.get(f"{prefix}.{i}.message.tool_call_id")
        if tool_call_id is not None:
            msg["tool_call_id"] = tool_call_id

        messages.append(msg)
        i += 1

    return messages


def _extract_tool_calls(
    attrs: dict[str, Any],
    prefix: str,
) -> list[dict[str, Any]]:
    """Extract tool_calls array from span attributes at the given prefix."""
    tool_calls: list[dict[str, Any]] = []
    j = 0
    while True:
        name = attrs.get(f"{prefix}.{j}.tool_call.function.name")
        if name is None:
            break
        tc: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": name,
                "arguments": attrs.get(f"{prefix}.{j}.tool_call.function.arguments", ""),
            },
        }
        tc_id = attrs.get(f"{prefix}.{j}.tool_call.id")
        if tc_id is not None:
            tc["id"] = tc_id
        tool_calls.append(tc)
        j += 1
    return tool_calls
```

### Step 4: Run test to verify it passes

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestExtractMessagesFromSpan -v`
Expected: PASS

### Step 5: Commit

```bash
git add experiments/sft_datagen/sft_datagen/trace_converter.py experiments/sft_datagen/tests/test_trace_converter.py
git commit -m "feat(trace-converter): add extract_messages_from_span"
```

---

## Task 3: Extract Tools from Span

**Files:**
- Modify: `experiments/sft_datagen/sft_datagen/trace_converter.py`
- Test: `experiments/sft_datagen/tests/test_trace_converter.py`

### Step 1: Write the failing test

```python
from sft_datagen.trace_converter import extract_tools_from_span
import json


class TestExtractToolsFromSpan:
    def test_extracts_tools_from_invocation_parameters(self):
        tools = [
            {"type": "function", "function": {"name": "execute_python", "parameters": {}}},
            {"type": "function", "function": {"name": "return_result", "parameters": {}}},
        ]
        attrs = {
            "llm.invocation_parameters": json.dumps({"tools": tools, "model": "test"}),
        }
        result = extract_tools_from_span(attrs)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "execute_python"
        assert result[1]["function"]["name"] == "return_result"

    def test_no_invocation_parameters(self):
        assert extract_tools_from_span({}) == []

    def test_invocation_parameters_without_tools(self):
        attrs = {
            "llm.invocation_parameters": json.dumps({"model": "test"}),
        }
        assert extract_tools_from_span(attrs) == []
```

### Step 2: Run test to verify it fails

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestExtractToolsFromSpan -v`
Expected: FAIL with `ImportError`

### Step 3: Write minimal implementation

Add to `trace_converter.py`:

```python
import json


def extract_tools_from_span(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool definitions from llm.invocation_parameters."""
    params_str = attrs.get("llm.invocation_parameters")
    if not params_str:
        return []
    try:
        params = json.loads(params_str)
        return params.get("tools", [])
    except (json.JSONDecodeError, TypeError):
        return []
```

### Step 4: Run test to verify it passes

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestExtractToolsFromSpan -v`
Expected: PASS

### Step 5: Commit

```bash
git add experiments/sft_datagen/sft_datagen/trace_converter.py experiments/sft_datagen/tests/test_trace_converter.py
git commit -m "feat(trace-converter): add extract_tools_from_span"
```

---

## Task 4: Detect Context Diffs Across Spans

System prompts may change between spans when `self.context["key"] = value` is called.
The system prompt uses XMLBlockFormatter format: `<key expr="self.context['key']">\nvalue\n</key>`.
Compare consecutive spans' system prompts, parse XML blocks, find additions/changes.

**Files:**
- Modify: `experiments/sft_datagen/sft_datagen/trace_converter.py`
- Test: `experiments/sft_datagen/tests/test_trace_converter.py`

### Step 1: Write the failing test

```python
from sft_datagen.trace_converter import parse_context_blocks, detect_context_diffs


class TestParseContextBlocks:
    def test_parses_xml_blocks(self):
        text = (
            "Some preamble.\n"
            "<repo_overview expr=\"self.context['repo_overview']\">\n"
            "This is the repo overview.\n"
            "</repo_overview>\n"
            "More text.\n"
            "<test_results expr=\"self.context['test_results']\">\n"
            "All tests pass.\n"
            "</test_results>"
        )
        blocks = parse_context_blocks(text)
        assert blocks["repo_overview"] == "This is the repo overview."
        assert blocks["test_results"] == "All tests pass."

    def test_empty_string(self):
        assert parse_context_blocks("") == {}

    def test_no_blocks(self):
        assert parse_context_blocks("Just plain text.") == {}


class TestDetectContextDiffs:
    def test_detects_added_block(self):
        spans = [
            {"attributes": {
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": "Preamble.",
            }},
            {"attributes": {
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": (
                    "Preamble.\n"
                    "<repo_overview expr=\"self.context['repo_overview']\">\n"
                    "Overview content.\n"
                    "</repo_overview>"
                ),
            }},
        ]
        # The spans had 3 messages and 5 messages respectively.
        # We need message counts to map span index → message index.
        message_counts = [3, 5]
        diffs = detect_context_diffs(spans, message_counts)
        assert len(diffs) == 1
        assert diffs[0]["key"] == "repo_overview"
        assert diffs[0]["value"] == "Overview content."
        # after_index should be the last message index of span 0 = 3-1 = 2
        assert diffs[0]["after_index"] == 2

    def test_detects_changed_block(self):
        spans = [
            {"attributes": {
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": (
                    "<info expr=\"self.context['info']\">\n"
                    "old value\n"
                    "</info>"
                ),
            }},
            {"attributes": {
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": (
                    "<info expr=\"self.context['info']\">\n"
                    "new value\n"
                    "</info>"
                ),
            }},
        ]
        message_counts = [3, 6]
        diffs = detect_context_diffs(spans, message_counts)
        assert len(diffs) == 1
        assert diffs[0]["key"] == "info"
        assert diffs[0]["value"] == "new value"

    def test_no_diffs(self):
        spans = [
            {"attributes": {
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": "Same.",
            }},
            {"attributes": {
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": "Same.",
            }},
        ]
        assert detect_context_diffs(spans, [3, 5]) == []

    def test_single_span_no_diffs(self):
        spans = [{"attributes": {
            "llm.input_messages.0.message.role": "system",
            "llm.input_messages.0.message.content": "Hello.",
        }}]
        assert detect_context_diffs(spans, [3]) == []
```

### Step 2: Run test to verify it fails

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestParseContextBlocks tests/test_trace_converter.py::TestDetectContextDiffs -v`
Expected: FAIL with `ImportError`

### Step 3: Write minimal implementation

Add to `trace_converter.py`:

```python
import re


def parse_context_blocks(text: str) -> dict[str, str]:
    """Parse XML context blocks from a system prompt string.

    Blocks have the format:
        <key expr="self.context['key']">
        value
        </key>

    Returns dict mapping key → value (stripped).
    """
    pattern = r"<(\w+)\s+expr=\"[^\"]*\">\n(.*?)\n</\1>"
    return {m.group(1): m.group(2).strip() for m in re.finditer(pattern, text, re.DOTALL)}


def detect_context_diffs(
    spans: list[dict[str, Any]],
    message_counts: list[int],
) -> list[dict[str, Any]]:
    """Compare system prompts across consecutive spans and detect context block changes.

    Args:
        spans: Agent006-instrumented spans, ordered chronologically.
        message_counts: Number of input messages per span (same length as spans).

    Returns:
        List of diffs: {"after_index": int, "key": str, "value": str}
        where after_index is the message index in the FINAL span's conversation
        after which the context changed.
    """
    if len(spans) < 2:
        return []

    diffs: list[dict[str, Any]] = []
    prev_blocks = parse_context_blocks(
        spans[0]["attributes"].get("llm.input_messages.0.message.content", "")
    )

    for i in range(1, len(spans)):
        curr_blocks = parse_context_blocks(
            spans[i]["attributes"].get("llm.input_messages.0.message.content", "")
        )

        # Find added or changed blocks
        for key, value in curr_blocks.items():
            if key not in prev_blocks or prev_blocks[key] != value:
                # Map span boundary to message index: the last message
                # contributed by the previous span is message_counts[i-1] - 1
                diffs.append({
                    "after_index": message_counts[i - 1] - 1,
                    "key": key,
                    "value": value,
                })

        prev_blocks = curr_blocks

    return diffs
```

### Step 4: Run test to verify it passes

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestParseContextBlocks tests/test_trace_converter.py::TestDetectContextDiffs -v`
Expected: PASS

### Step 5: Commit

```bash
git add experiments/sft_datagen/sft_datagen/trace_converter.py experiments/sft_datagen/tests/test_trace_converter.py
git commit -m "feat(trace-converter): add context block parsing and diff detection"
```

---

## Task 5: `extract_conversation()` — Wire It All Together

This is the main function. Loads a trace file, filters spans, finds the one with the most messages, extracts everything.

**Files:**
- Modify: `experiments/sft_datagen/sft_datagen/trace_converter.py`
- Test: `experiments/sft_datagen/tests/test_trace_converter.py`

### Step 1: Write the failing test

```python
import tempfile
from sft_datagen.trace_converter import extract_conversation


def _make_agent_span(model: str, input_msgs: dict, output_msgs: dict = None,
                     invocation_params: dict = None) -> dict:
    """Build a realistic agent006 acompletion span."""
    attrs = {"llm.model_name": model}
    attrs.update(input_msgs)
    if output_msgs:
        attrs.update(output_msgs)
    if invocation_params:
        attrs["llm.invocation_parameters"] = json.dumps(invocation_params)
    return _make_span("acompletion", attrs)


class TestExtractConversation:
    def test_basic_extraction(self, tmp_path):
        tools = [{"type": "function", "function": {"name": "execute_python", "parameters": {}}}]

        # Span 1: 2 messages (system + user)
        span1 = _make_agent_span(
            "openai/test-model",
            {
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": "You are helpful.",
                "llm.input_messages.1.message.role": "user",
                "llm.input_messages.1.message.content": "Fix bug.",
            },
            invocation_params={"tools": tools},
        )

        # Span 2: 4 messages (system + user + assistant + tool) — more messages, use this one
        span2 = _make_agent_span(
            "openai/test-model",
            {
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": "You are helpful.",
                "llm.input_messages.1.message.role": "user",
                "llm.input_messages.1.message.content": "Fix bug.",
                "llm.input_messages.2.message.role": "assistant",
                "llm.input_messages.2.message.tool_calls.0.tool_call.function.name": "execute_python",
                "llm.input_messages.2.message.tool_calls.0.tool_call.function.arguments": '{"code": "x=1"}',
                "llm.input_messages.2.message.tool_calls.0.tool_call.id": "call-1",
                "llm.input_messages.3.message.role": "tool",
                "llm.input_messages.3.message.content": "done",
                "llm.input_messages.3.message.tool_call_id": "call-1",
            },
            output_msgs={
                "llm.output_messages.0.message.role": "assistant",
                "llm.output_messages.0.message.content": "Fixed it.",
            },
            invocation_params={"tools": tools},
        )

        # LiteLLM span — should be filtered out
        litellm_span = _make_span("acompletion", {
            "llm.input_messages.0.message.role": "system",
            "llm.input_messages.0.message.content": "You are helpful.",
        })

        trace_file = tmp_path / "test.006trace.jsonl"
        trace_file.write_text("\n".join(json.dumps(s) for s in [span1, litellm_span, span2]))

        result = extract_conversation(trace_file)

        # Messages: 4 input + 1 output = 5
        assert len(result["messages"]) == 5
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][-1]["role"] == "assistant"
        assert result["messages"][-1]["content"] == "Fixed it."

        # Tools
        assert len(result["tools"]) == 1
        assert result["tools"][0]["function"]["name"] == "execute_python"

        # Metadata
        assert result["metadata"]["model"] == "openai/test-model"
        assert result["metadata"]["total_messages"] == 5

    def test_raises_on_no_agent_spans(self, tmp_path):
        litellm_span = _make_span("acompletion", {
            "llm.input_messages.0.message.role": "system",
        })
        trace_file = tmp_path / "empty.006trace.jsonl"
        trace_file.write_text(json.dumps(litellm_span))

        with pytest.raises(ValueError, match="No agent006-instrumented spans"):
            extract_conversation(trace_file)
```

### Step 2: Run test to verify it fails

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestExtractConversation -v`
Expected: FAIL with `ImportError`

### Step 3: Write minimal implementation

Add to `trace_converter.py`:

```python
from pathlib import Path


def _count_input_messages(attrs: dict[str, Any]) -> int:
    """Count input messages in a span by walking sequential indices."""
    count = 0
    while attrs.get(f"llm.input_messages.{count}.message.role") is not None:
        count += 1
    return count


def extract_conversation(trace_path: str | Path) -> dict[str, Any]:
    """Extract a conversation from a .006trace.jsonl file.

    Loads all spans, filters to agent006-instrumented acompletion spans,
    selects the one with the most input messages, and extracts the full
    OpenAI-format conversation.

    Returns:
        {
            "messages": [...],
            "tools": [...],
            "context_diffs": [...],
            "metadata": {
                "task_id": str | None,
                "model": str,
                "total_spans": int,
                "total_messages": int,
            }
        }

    Raises:
        ValueError: If no agent006-instrumented spans found.
    """
    trace_path = Path(trace_path)

    # Load all spans
    all_spans = []
    with open(trace_path) as f:
        for line in f:
            line = line.strip()
            if line:
                all_spans.append(json.loads(line))

    # Filter to agent006 spans
    agent_spans = load_agent_spans(all_spans)
    if not agent_spans:
        raise ValueError(f"No agent006-instrumented spans found in {trace_path}")

    # Count messages per span, find the one with the most
    message_counts = [_count_input_messages(s["attributes"]) for s in agent_spans]
    best_idx = max(range(len(agent_spans)), key=lambda i: message_counts[i])
    best_span = agent_spans[best_idx]
    best_attrs = best_span["attributes"]

    # Extract input messages
    messages = extract_messages_from_span(best_attrs, prefix="llm.input_messages")

    # Append output messages
    output_messages = extract_messages_from_span(best_attrs, prefix="llm.output_messages")
    messages.extend(output_messages)

    # Extract tools
    tools = extract_tools_from_span(best_attrs)

    # Detect context diffs across all spans
    context_diffs = detect_context_diffs(agent_spans, message_counts)

    # Extract metadata
    model = best_attrs.get("llm.model_name", "")

    # Try to extract task_id from trace filename: task_<uuid>_<uuid>.006trace.jsonl
    task_id = None
    stem = trace_path.stem  # e.g., "task_b891437b_b891437b.006trace"
    if stem.endswith(".006trace"):
        stem = stem[:-len(".006trace")]
    # Could also be an actual task_id like "sympy__sympy-19346"
    if stem.startswith("task_"):
        task_id = stem
    else:
        task_id = stem

    return {
        "messages": messages,
        "tools": tools,
        "context_diffs": context_diffs,
        "metadata": {
            "task_id": task_id,
            "model": model,
            "total_spans": len(agent_spans),
            "total_messages": len(messages),
        },
    }
```

### Step 4: Run test to verify it passes

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestExtractConversation -v`
Expected: PASS

### Step 5: Commit

```bash
git add experiments/sft_datagen/sft_datagen/trace_converter.py experiments/sft_datagen/tests/test_trace_converter.py
git commit -m "feat(trace-converter): add extract_conversation main function"
```

---

## Task 6: `to_nemo_rl()` — Convert to NeMo RL Format

Takes the conversation dict from `extract_conversation()`, injects context diffs, ensures last message is assistant.

**Files:**
- Modify: `experiments/sft_datagen/sft_datagen/trace_converter.py`
- Test: `experiments/sft_datagen/tests/test_trace_converter.py`

### Step 1: Write the failing test

```python
from sft_datagen.trace_converter import to_nemo_rl


class TestToNemoRl:
    def test_basic_conversion(self):
        conversation = {
            "messages": [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Fix it."},
                {"role": "assistant", "content": "Done."},
            ],
            "tools": [{"type": "function", "function": {"name": "execute_python"}}],
            "context_diffs": [],
            "metadata": {"task_id": "test", "model": "m", "total_spans": 1, "total_messages": 3},
        }
        result = to_nemo_rl(conversation)
        assert result["messages"] == conversation["messages"]
        assert result["tools"] == conversation["tools"]

    def test_appends_assistant_if_last_is_not_assistant(self):
        conversation = {
            "messages": [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Fix it."},
            ],
            "tools": [],
            "context_diffs": [],
            "metadata": {},
        }
        result = to_nemo_rl(conversation)
        assert result["messages"][-1] == {"role": "assistant", "content": "Task completed."}

    def test_injects_context_diffs(self):
        conversation = {
            "messages": [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Fix it."},
                {"role": "assistant", "content": "Looking..."},
                {"role": "user", "content": "More info."},
                {"role": "assistant", "content": "Done."},
            ],
            "tools": [],
            "context_diffs": [
                {"after_index": 2, "key": "repo_overview", "value": "This is the repo."},
            ],
            "metadata": {},
        }
        result = to_nemo_rl(conversation)
        # Context diff injected after index 2 (the "Looking..." message)
        assert len(result["messages"]) == 6  # 5 original + 1 injected
        injected = result["messages"][3]
        assert injected["role"] == "system"
        assert "repo_overview" in injected["content"]
        assert "This is the repo." in injected["content"]

    def test_does_not_mutate_input(self):
        conversation = {
            "messages": [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Fix it."},
            ],
            "tools": [],
            "context_diffs": [{"after_index": 0, "key": "k", "value": "v"}],
            "metadata": {},
        }
        original_len = len(conversation["messages"])
        to_nemo_rl(conversation)
        assert len(conversation["messages"]) == original_len
```

### Step 2: Run test to verify it fails

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestToNemoRl -v`
Expected: FAIL with `ImportError`

### Step 3: Write minimal implementation

Add to `trace_converter.py`:

```python
from sft_datagen.nemo_converter import _format_context_update, inject_context_updates


def to_nemo_rl(conversation: dict[str, Any]) -> dict[str, Any]:
    """Convert an extracted conversation to NeMo RL format.

    Injects context diff messages at the right positions, and ensures the
    last message has role "assistant".
    """
    messages = list(conversation["messages"])

    # Inject context diffs
    if conversation.get("context_diffs"):
        messages = inject_context_updates(messages, conversation["context_diffs"])

    # Ensure last message is assistant
    if not messages or messages[-1]["role"] != "assistant":
        messages.append({"role": "assistant", "content": "Task completed."})

    return {
        "messages": messages,
        "tools": conversation.get("tools", []),
    }
```

### Step 4: Run test to verify it passes

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestToNemoRl -v`
Expected: PASS

### Step 5: Commit

```bash
git add experiments/sft_datagen/sft_datagen/trace_converter.py experiments/sft_datagen/tests/test_trace_converter.py
git commit -m "feat(trace-converter): add to_nemo_rl conversion function"
```

---

## Task 7: CLI `convert/trace_to_nemo_rl.py`

Thin CLI that globs input trace files, runs `extract_conversation()` → `to_nemo_rl()` for each, writes one JSONL line per trace.

**Files:**
- Create: `experiments/sft_datagen/convert/trace_to_nemo_rl.py`
- Test: `experiments/sft_datagen/tests/test_trace_converter.py` (add CLI test)

### Step 1: Write the failing test

```python
import subprocess


class TestCLI:
    def test_converts_trace_files(self, tmp_path):
        tools = [{"type": "function", "function": {"name": "execute_python", "parameters": {}}}]

        span = _make_agent_span(
            "openai/test-model",
            {
                "llm.input_messages.0.message.role": "system",
                "llm.input_messages.0.message.content": "Be helpful.",
                "llm.input_messages.1.message.role": "user",
                "llm.input_messages.1.message.content": "Fix it.",
            },
            output_msgs={
                "llm.output_messages.0.message.role": "assistant",
                "llm.output_messages.0.message.content": "Done.",
            },
            invocation_params={"tools": tools},
        )

        # Write two trace files
        for name in ["task_a.006trace.jsonl", "task_b.006trace.jsonl"]:
            (tmp_path / name).write_text(json.dumps(span))

        output_file = tmp_path / "output.jsonl"

        result = subprocess.run(
            [
                "python", "convert/trace_to_nemo_rl.py",
                "--input", str(tmp_path / "*.006trace.jsonl"),
                "--output", str(output_file),
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == 2

        for line in lines:
            record = json.loads(line)
            assert "messages" in record
            assert "tools" in record
            assert record["messages"][-1]["role"] == "assistant"
```

### Step 2: Run test to verify it fails

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestCLI -v`
Expected: FAIL (file doesn't exist)

### Step 3: Write minimal implementation

```python
# experiments/sft_datagen/convert/trace_to_nemo_rl.py
"""Convert .006trace.jsonl files to NeMo RL JSONL format.

Usage:
    python convert/trace_to_nemo_rl.py \
        --input "results/task_*.006trace.jsonl" \
        --output data/datasets/sft_dataset.jsonl
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sft_datagen.trace_converter import extract_conversation, to_nemo_rl


def main():
    parser = argparse.ArgumentParser(description="Convert OTel traces to NeMo RL format")
    parser.add_argument("--input", required=True,
                        help="Glob pattern for .006trace.jsonl files")
    parser.add_argument("--output", required=True,
                        help="Output JSONL file path")
    args = parser.parse_args()

    input_files = sorted(glob.glob(args.input))
    if not input_files:
        print(f"No files matched: {args.input}")
        sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    converted = 0
    errors = 0
    total_messages = 0

    with open(output_path, "w") as out:
        for trace_file in input_files:
            try:
                conversation = extract_conversation(trace_file)
                record = to_nemo_rl(conversation)
                out.write(json.dumps(record) + "\n")
                converted += 1
                total_messages += len(record["messages"])
            except Exception as e:
                print(f"  ERROR {Path(trace_file).name}: {e}")
                errors += 1

    print(f"Converted {converted}/{converted + errors} traces")
    print(f"  Total messages: {total_messages}")
    if errors:
        print(f"  Errors: {errors}")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()
```

### Step 4: Run test to verify it passes

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestCLI -v`
Expected: PASS

### Step 5: Commit

```bash
git add experiments/sft_datagen/convert/trace_to_nemo_rl.py experiments/sft_datagen/tests/test_trace_converter.py
git commit -m "feat(trace-converter): add trace_to_nemo_rl CLI"
```

---

## Task 8: Integration Test with Real Trace

Run against the real trace file to validate end-to-end.

**Files:**
- Test: `experiments/sft_datagen/tests/test_trace_converter.py`

### Step 1: Write the integration test

```python
REAL_TRACE = Path(__file__).parent.parent.parent.parent / "results" / "task_b891437b_b891437b.006trace.jsonl"


@pytest.mark.skipif(not REAL_TRACE.exists(), reason="Real trace file not available")
class TestIntegrationRealTrace:
    def test_extract_conversation_from_real_trace(self):
        conv = extract_conversation(REAL_TRACE)

        # Should have messages
        assert len(conv["messages"]) > 10
        assert conv["messages"][0]["role"] == "system"

        # Should have tools
        assert len(conv["tools"]) >= 2
        tool_names = {t["function"]["name"] for t in conv["tools"]}
        assert "execute_python" in tool_names

        # Metadata
        assert conv["metadata"]["model"] == "openai/nvidia/nvidia/nemotron-3-super-preview"
        assert conv["metadata"]["total_spans"] > 0

    def test_to_nemo_rl_from_real_trace(self):
        conv = extract_conversation(REAL_TRACE)
        record = to_nemo_rl(conv)

        assert record["messages"][-1]["role"] == "assistant"
        assert len(record["tools"]) >= 2

        # Validate message structure
        for msg in record["messages"]:
            assert "role" in msg
            if msg["role"] == "tool":
                assert "tool_call_id" in msg
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    assert "function" in tc
                    assert "name" in tc["function"]
```

### Step 2: Run integration test

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/test_trace_converter.py::TestIntegrationRealTrace -v`
Expected: PASS (trace file exists in results/)

### Step 3: Commit

```bash
git add experiments/sft_datagen/tests/test_trace_converter.py
git commit -m "test(trace-converter): add integration test with real trace"
```

---

## Task 9: Run All Tests

### Step 1: Run the full test suite

Run: `cd /Volumes/dev/dev/cleanup/experiments/sft_datagen && python -m pytest tests/ -v`
Expected: All tests PASS (existing tests + new trace converter tests)

### Step 2: Verify no regressions in existing tests

Check that `test_cleaning.py`, `test_nemo_converter.py`, `test_event_serializer.py`, `test_trajectory_dumper.py`, and `test_e2e.py` all still pass.

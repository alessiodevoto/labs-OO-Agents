# SFT Data Generation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an end-to-end pipeline that generates SFT trajectories from SWE-bench tasks using Agent006, cleans them, and converts to NeMo RL format.

**Architecture:** Event store dump approach — after each agent task, serialize `InMemoryBackend.all_events()` to a raw trajectory file. Post-processing pipeline filters, doctors, and converts to NeMo RL OpenAI format. Everything self-contained in `experiments/sft-datagen/`.

**Tech Stack:** Python 3.11+, Agent006 framework (events, event_manager), Pydantic (event serialization), pytest. Uses existing SWE-bench adapter and evaluation infrastructure.

**Design doc:** `docs/plans/2026-02-25-sft-data-generation-swebench.md`

---

## Task 1: Directory Scaffold

**Files:**
- Create: `experiments/sft-datagen/README.md`
- Create: `experiments/sft-datagen/generate/__init__.py`
- Create: `experiments/sft-datagen/clean/__init__.py`
- Create: `experiments/sft-datagen/convert/__init__.py`
- Create: `experiments/sft-datagen/tests/__init__.py`

**Step 1: Create directory structure**

```bash
mkdir -p experiments/sft-datagen/{generate,clean,convert,train,model,data/{tasks,trajectories/{raw,cleaned},datasets},reports,tests}
```

**Step 2: Create `__init__.py` files**

Create empty `__init__.py` in `generate/`, `clean/`, `convert/`, `tests/`.

**Step 3: Create README**

Create `experiments/sft-datagen/README.md`:

```markdown
# SFT Data Generation for SWE-bench

## Research Question

Can we fine-tune Nemotron Nano v3 on curated SWE-bench trajectories from a
strong open-source model (Qwen3.5-397B-A17B) and improve its coding performance?

## Pipeline

1. **Generate** — Run Agent006 on SWE-bench tasks, capture event store as raw trajectories
2. **Clean** — Filter passing, doctor error loops, validate well-formedness
3. **Convert** — Map events to NeMo RL OpenAI format
4. **Train** — SFT on Nemotron Nano v3 via NeMo Framework

## How to Run

```bash
source ../../.venv/bin/activate

# Generate trajectories (requires model endpoint)
python generate/run_generation.py --endpoint http://localhost:8000/v1 --model Qwen/Qwen3.5-397B-A17B --limit 10

# Clean trajectories
python clean/filter_passed.py data/trajectories/raw/ data/trajectories/cleaned/
python clean/doctor_trajectories.py data/trajectories/cleaned/
python clean/validate.py data/trajectories/cleaned/

# Convert to NeMo RL format
python convert/to_nemo_rl.py data/trajectories/cleaned/ data/datasets/sft_dataset.jsonl

# Run tests
pytest tests/ -v
```

## Design Doc

See `docs/plans/2026-02-25-sft-data-generation-swebench.md`
```

**Step 4: Commit**

```bash
git add experiments/sft-datagen/
git commit -m "feat(sft-datagen): scaffold directory structure and README"
```

---

## Task 2: Event Serializer — Tests

Serialize Agent006 events to JSON-safe dicts for the raw trajectory format.
Events are Pydantic models — `model_dump()` gives us most of what we need,
but we need to handle filtering (skip runtime events) and normalization.

**Files:**
- Create: `experiments/sft-datagen/tests/test_event_serializer.py`

**Step 1: Write failing tests**

```python
"""Tests for event serializer — maps Agent006 events to JSON-safe dicts."""

import pytest

from nemo_oo_agents.events import (
    AfterTurn,
    BeforeTurn,
    Error,
    Feedback,
    LLMOutput,
    Message,
    PythonOutput,
    Reasoning,
    Summary,
    Task,
)
from context_blocks.events import ToolCallEvent, ToolResult

# Import will fail until we create the module
from sft_datagen.event_serializer import serialize_event, serialize_events


class TestSerializeEvent:
    """Test individual event serialization."""

    def test_task_event(self):
        event = Task(prompt="Fix the bug in django/db/models.py")
        result = serialize_event(event)
        assert result == {
            "event_type": "task",
            "role": "user",
            "prompt": "Fix the bug in django/db/models.py",
        }

    def test_llm_output_event(self):
        event = LLMOutput(content="Let me look at the code...")
        result = serialize_event(event)
        assert result == {
            "event_type": "llm_output",
            "role": "assistant",
            "content": "Let me look at the code...",
        }

    def test_tool_call_event_without_result(self):
        event = ToolCallEvent(
            tool_call_id="call_abc123",
            name="execute_python",
            arguments={"code": "print('hello')"},
        )
        result = serialize_event(event)
        assert result == {
            "event_type": "tool_call",
            "role": "assistant",
            "tool_call_id": "call_abc123",
            "name": "execute_python",
            "arguments": {"code": "print('hello')"},
            "result": None,
        }

    def test_tool_call_event_with_result(self):
        event = ToolCallEvent(
            tool_call_id="call_abc123",
            name="execute_python",
            arguments={"code": "print('hello')"},
            result=ToolResult(
                tool_call_id="call_abc123",
                content="status: complete",
            ),
        )
        result = serialize_event(event)
        assert result["result"] == {
            "tool_call_id": "call_abc123",
            "content": "status: complete",
            "result_status": "complete",
        }

    def test_python_output_event(self):
        event = PythonOutput(
            tool_call_id="call_abc123",
            status="complete",
            execution_count=1,
            stdout="hello\n",
            stderr="",
            error="",
        )
        result = serialize_event(event)
        assert result["event_type"] == "python_output"
        assert result["role"] == "user"
        assert result["tool_call_id"] == "call_abc123"
        assert result["stdout"] == "hello\n"

    def test_error_event(self):
        event = Error(content="NameError: name 'foo' is not defined")
        result = serialize_event(event)
        assert result == {
            "event_type": "error",
            "role": "user",
            "content": "NameError: name 'foo' is not defined",
        }

    def test_feedback_event(self):
        event = Feedback(content="Method not defined yet")
        result = serialize_event(event)
        assert result["event_type"] == "feedback"
        assert result["role"] == "user"

    def test_message_event(self):
        event = Message(content="I found the bug")
        result = serialize_event(event)
        assert result["event_type"] == "message"
        assert result["role"] == "assistant"

    def test_reasoning_event(self):
        event = Reasoning(content="The issue is in the QuerySet filter method")
        result = serialize_event(event)
        assert result["event_type"] == "reasoning"
        assert result["role"] == "assistant"

    def test_runtime_events_return_none(self):
        """BeforeTurn and AfterTurn are runtime events — should be skipped."""
        bt = BeforeTurn(
            method_name="solve",
            strategy="codeact",
            generation_id="gen_1",
            turn_number=1,
        )
        assert serialize_event(bt) is None

        at = AfterTurn(
            method_name="solve",
            strategy="codeact",
            generation_id="gen_1",
            turn_number=1,
            is_final=False,
        )
        assert serialize_event(at) is None

    def test_summary_events_return_none(self):
        """Summary events are collapsed events — should be skipped."""
        s = Summary(
            summary_tag="2..40",
            replaced_range=(2, 40),
        )
        assert serialize_event(s) is None


class TestSerializeEvents:
    """Test batch serialization with filtering."""

    def test_filters_out_runtime_events(self):
        events = [
            Task(prompt="Fix bug"),
            BeforeTurn(
                method_name="solve",
                strategy="codeact",
                generation_id="gen_1",
                turn_number=1,
            ),
            LLMOutput(content="Looking at code..."),
            AfterTurn(
                method_name="solve",
                strategy="codeact",
                generation_id="gen_1",
                turn_number=1,
                is_final=True,
            ),
        ]
        result = serialize_events(events)
        assert len(result) == 2
        assert result[0]["event_type"] == "task"
        assert result[1]["event_type"] == "llm_output"

    def test_preserves_order(self):
        events = [
            Task(prompt="Fix bug"),
            LLMOutput(content="code..."),
            ToolCallEvent(
                tool_call_id="c1",
                name="execute_python",
                arguments={"code": "x=1"},
            ),
            PythonOutput(
                tool_call_id="c1",
                status="complete",
                execution_count=1,
                stdout="",
            ),
        ]
        result = serialize_events(events)
        assert [e["event_type"] for e in result] == [
            "task",
            "llm_output",
            "tool_call",
            "python_output",
        ]

    def test_empty_list(self):
        assert serialize_events([]) == []
```

**Step 2: Run tests to verify they fail**

```bash
cd experiments/sft-datagen
source ../../.venv/bin/activate
pytest tests/test_event_serializer.py -v
```

Expected: `ModuleNotFoundError: No module named 'sft_datagen'`

**Step 3: Commit test file**

```bash
git add tests/test_event_serializer.py
git commit -m "test(sft-datagen): add event serializer tests (red)"
```

---

## Task 3: Event Serializer — Implementation

**Files:**
- Create: `experiments/sft-datagen/sft_datagen/__init__.py`
- Create: `experiments/sft-datagen/sft_datagen/event_serializer.py`

**Step 1: Create the package**

Create `experiments/sft-datagen/sft_datagen/__init__.py` (empty).

**Step 2: Implement the serializer**

Create `experiments/sft-datagen/sft_datagen/event_serializer.py`:

```python
"""Serialize Agent006 events to JSON-safe dicts for raw trajectory format.

Maps each event type to a minimal dict with event_type, role, and type-specific fields.
Runtime events (BeforeTurn, AfterTurn) and Summary events are filtered out.
"""

from __future__ import annotations

from typing import Any

from context_blocks.events import EventBase, ToolCallEvent
from context_blocks.models import Role

from nemo_oo_agents.events import (
    AfterTurn,
    BeforeTurn,
    Error,
    Feedback,
    LLMOutput,
    Message,
    PythonOutput,
    Reasoning,
    Summary,
    Task,
)

# Event types to skip (runtime-only, not part of the conversation)
_SKIP_TYPES = (BeforeTurn, AfterTurn, Summary)

# Role mapping from ClassVar _role
_ROLE_MAP = {
    Role.USER: "user",
    Role.ASSISTANT: "assistant",
    Role.TOOL: "tool",
}


def serialize_event(event: EventBase) -> dict[str, Any] | None:
    """Serialize a single event to a JSON-safe dict.

    Returns None for events that should be skipped (runtime, summary).
    """
    if isinstance(event, _SKIP_TYPES):
        return None

    role = _ROLE_MAP.get(event._role, "user")

    if isinstance(event, Task):
        return {"event_type": "task", "role": role, "prompt": event.prompt}

    if isinstance(event, (LLMOutput, Message, Reasoning)):
        return {"event_type": event.event_type, "role": role, "content": event.content}

    if isinstance(event, (Error, Feedback)):
        return {"event_type": event.event_type, "role": role, "content": event.content}

    if isinstance(event, ToolCallEvent):
        result_dict = None
        if event.result is not None:
            result_dict = {
                "tool_call_id": event.result.tool_call_id,
                "content": event.result.content,
                "result_status": event.result.result_status.value
                if hasattr(event.result.result_status, "value")
                else str(event.result.result_status),
            }
        return {
            "event_type": "tool_call",
            "role": role,
            "tool_call_id": event.tool_call_id,
            "name": event.name,
            "arguments": event.arguments,
            "result": result_dict,
        }

    if isinstance(event, PythonOutput):
        return {
            "event_type": "python_output",
            "role": role,
            "tool_call_id": event.tool_call_id,
            "status": event.status.value if hasattr(event.status, "value") else str(event.status),
            "execution_count": event.execution_count,
            "stdout": event.stdout,
            "stderr": event.stderr,
            "error": event.error,
        }

    # Fallback: use model_dump for unknown event types
    data = event.model_dump(exclude={"id", "metadata", "status", "tag", "timestamp"})
    data["role"] = role
    return data


def serialize_events(events: list[EventBase]) -> list[dict[str, Any]]:
    """Serialize a list of events, filtering out runtime events."""
    result = []
    for event in events:
        serialized = serialize_event(event)
        if serialized is not None:
            result.append(serialized)
    return result
```

**Step 3: Run tests to verify they pass**

```bash
cd experiments/sft-datagen
PYTHONPATH=. pytest tests/test_event_serializer.py -v
```

Expected: All tests PASS.

Note: You may need `PYTHONPATH=.` or to install the package in dev mode. If imports fail, check that `.venv` has nemo_oo_agents and context-blocks installed.

**Step 4: Commit**

```bash
git add sft_datagen/
git commit -m "feat(sft-datagen): implement event serializer"
```

---

## Task 4: NeMo RL Converter — Tests

Convert serialized events to NeMo RL OpenAI message format.

**Files:**
- Create: `experiments/sft-datagen/tests/test_nemo_converter.py`

**Step 1: Write failing tests**

```python
"""Tests for NeMo RL converter — maps serialized events to OpenAI messages."""

import json

import pytest

from sft_datagen.nemo_converter import events_to_nemo_rl, event_to_messages


class TestEventToMessages:
    """Test individual event → message conversion."""

    def test_task_event(self):
        event = {"event_type": "task", "role": "user", "prompt": "Fix the bug"}
        messages = event_to_messages(event)
        assert messages == [{"role": "user", "content": "Fix the bug"}]

    def test_llm_output_event(self):
        event = {"event_type": "llm_output", "role": "assistant", "content": "Looking at code..."}
        messages = event_to_messages(event)
        assert messages == [{"role": "assistant", "content": "Looking at code..."}]

    def test_tool_call_event_with_result(self):
        event = {
            "event_type": "tool_call",
            "role": "assistant",
            "tool_call_id": "call_1",
            "name": "execute_python",
            "arguments": {"code": "print('hello')"},
            "result": {
                "tool_call_id": "call_1",
                "content": "status: complete",
                "result_status": "complete",
            },
        }
        messages = event_to_messages(event)
        assert len(messages) == 2
        # First: assistant with tool_calls
        assert messages[0]["role"] == "assistant"
        assert messages[0]["content"] is None
        assert len(messages[0]["tool_calls"]) == 1
        tc = messages[0]["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["function"]["name"] == "execute_python"
        assert json.loads(tc["function"]["arguments"]) == {"code": "print('hello')"}
        # Second: tool result
        assert messages[1] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "status: complete",
        }

    def test_tool_call_event_without_result(self):
        event = {
            "event_type": "tool_call",
            "role": "assistant",
            "tool_call_id": "call_1",
            "name": "execute_python",
            "arguments": {"code": "x=1"},
            "result": None,
        }
        messages = event_to_messages(event)
        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"

    def test_python_output_event(self):
        event = {
            "event_type": "python_output",
            "role": "user",
            "tool_call_id": "call_1",
            "stdout": "hello\nworld\n",
            "stderr": "",
            "error": "",
            "status": "complete",
            "execution_count": 1,
        }
        messages = event_to_messages(event)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "hello\nworld" in messages[0]["content"]

    def test_python_output_with_error(self):
        event = {
            "event_type": "python_output",
            "role": "user",
            "tool_call_id": "call_1",
            "stdout": "",
            "stderr": "Traceback...",
            "error": "NameError: name 'foo' is not defined",
            "status": "error",
            "execution_count": 1,
        }
        messages = event_to_messages(event)
        assert "NameError" in messages[0]["content"]

    def test_error_event(self):
        event = {"event_type": "error", "role": "user", "content": "Something failed"}
        messages = event_to_messages(event)
        assert messages == [{"role": "user", "content": "Something failed"}]

    def test_message_event(self):
        event = {"event_type": "message", "role": "assistant", "content": "Done"}
        messages = event_to_messages(event)
        assert messages == [{"role": "assistant", "content": "Done"}]


class TestEventsToNemoRL:
    """Test full trajectory conversion to NeMo RL format."""

    def test_basic_trajectory(self):
        trajectory = {
            "system_prompt": "You are a coding assistant.",
            "tools": [{"name": "execute_python", "description": "Run code", "parameters": {}}],
            "events": [
                {"event_type": "task", "role": "user", "prompt": "Fix the bug"},
                {"event_type": "llm_output", "role": "assistant", "content": "I'll fix it."},
            ],
        }
        result = events_to_nemo_rl(trajectory)
        assert result["messages"][0] == {"role": "system", "content": "You are a coding assistant."}
        assert result["messages"][1] == {"role": "user", "content": "Fix the bug"}
        assert result["messages"][2] == {"role": "assistant", "content": "I'll fix it."}
        assert result["tools"] == trajectory["tools"]

    def test_last_message_must_be_assistant(self):
        trajectory = {
            "system_prompt": "System",
            "tools": [],
            "events": [
                {"event_type": "task", "role": "user", "prompt": "Fix bug"},
                {"event_type": "llm_output", "role": "assistant", "content": "Done"},
                {
                    "event_type": "python_output",
                    "role": "user",
                    "tool_call_id": "c1",
                    "stdout": "ok",
                    "stderr": "",
                    "error": "",
                    "status": "complete",
                    "execution_count": 1,
                },
            ],
        }
        result = events_to_nemo_rl(trajectory)
        # Should append a synthetic assistant message or the conversion should flag this
        assert result["messages"][-1]["role"] == "assistant"

    def test_empty_events_raises(self):
        trajectory = {"system_prompt": "System", "tools": [], "events": []}
        with pytest.raises(ValueError, match="empty"):
            events_to_nemo_rl(trajectory)
```

**Step 2: Run tests to verify they fail**

```bash
cd experiments/sft-datagen
PYTHONPATH=. pytest tests/test_nemo_converter.py -v
```

Expected: `ModuleNotFoundError: No module named 'sft_datagen.nemo_converter'`

**Step 3: Commit**

```bash
git add tests/test_nemo_converter.py
git commit -m "test(sft-datagen): add NeMo RL converter tests (red)"
```

---

## Task 5: NeMo RL Converter — Implementation

**Files:**
- Create: `experiments/sft-datagen/sft_datagen/nemo_converter.py`

**Step 1: Implement the converter**

```python
"""Convert serialized event trajectories to NeMo RL OpenAI format.

Input: raw trajectory dict with system_prompt, tools, events
Output: NeMo RL record with messages[] and tools[]
"""

from __future__ import annotations

import json
from typing import Any


def _format_python_output(event: dict[str, Any]) -> str:
    """Format PythonOutput fields into a readable string."""
    parts = []
    if event.get("stdout"):
        parts.append(f"Stdout:\n{event['stdout']}")
    if event.get("stderr"):
        parts.append(f"Stderr:\n{event['stderr']}")
    if event.get("error"):
        parts.append(f"Error:\n{event['error']}")
    return "\n\n".join(parts) if parts else "(no output)"


def event_to_messages(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a single serialized event to one or more OpenAI messages."""
    etype = event["event_type"]

    if etype == "task":
        return [{"role": "user", "content": event["prompt"]}]

    if etype in ("llm_output", "message", "reasoning"):
        return [{"role": "assistant", "content": event["content"]}]

    if etype in ("error", "feedback"):
        return [{"role": "user", "content": event["content"]}]

    if etype == "tool_call":
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": event["tool_call_id"],
                        "type": "function",
                        "function": {
                            "name": event["name"],
                            "arguments": json.dumps(event["arguments"]),
                        },
                    }
                ],
            }
        ]
        if event.get("result") is not None:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": event["result"]["tool_call_id"],
                    "content": event["result"]["content"],
                }
            )
        return messages

    if etype == "python_output":
        return [{"role": "user", "content": _format_python_output(event)}]

    # Fallback
    return [{"role": event.get("role", "user"), "content": str(event)}]


MINIMAL_SYSTEM_PROMPT = (
    "You are a software engineer. Fix the described issue. "
    "Be concise and direct. Do not add comments unless asked."
)


def events_to_nemo_rl(
    trajectory: dict[str, Any],
    *,
    system_prompt_mode: str = "original",
    custom_system_prompt: str | None = None,
) -> dict[str, Any]:
    """Convert a full trajectory to NeMo RL OpenAI format.

    Args:
        trajectory: Raw trajectory dict with system_prompt, tools, events.
        system_prompt_mode: One of "original", "minimal", "custom".
            - "original": Use the system_prompt from the trajectory.
            - "minimal": Use a short generic prompt (post-SFT style).
            - "custom": Use custom_system_prompt.
        custom_system_prompt: Used when system_prompt_mode="custom".

    Returns:
        Dict with "messages" and "tools" keys in NeMo RL format.

    Raises:
        ValueError: If events list is empty.
    """
    events = trajectory.get("events", [])
    if not events:
        raise ValueError("Cannot convert trajectory with empty events")

    # Select system prompt based on mode
    if system_prompt_mode == "minimal":
        system_prompt = MINIMAL_SYSTEM_PROMPT
    elif system_prompt_mode == "custom":
        system_prompt = custom_system_prompt or ""
    else:  # "original"
        system_prompt = trajectory.get("system_prompt", "")

    # Start with system message
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]

    # Convert each event to messages
    for event in events:
        messages.extend(event_to_messages(event))

    # Ensure last message is assistant (NeMo RL requirement)
    if messages[-1]["role"] != "assistant":
        messages.append({"role": "assistant", "content": "Task completed."})

    return {
        "messages": messages,
        "tools": trajectory.get("tools", []),
    }
```

**Step 2: Run tests to verify they pass**

```bash
cd experiments/sft-datagen
PYTHONPATH=. pytest tests/test_nemo_converter.py -v
```

Expected: All tests PASS.

**Step 3: Commit**

```bash
git add sft_datagen/nemo_converter.py
git commit -m "feat(sft-datagen): implement NeMo RL converter"
```

---

## Task 6: Cleaning Pipeline — Filter & Validate Tests

**Files:**
- Create: `experiments/sft-datagen/tests/test_cleaning.py`

**Step 1: Write failing tests**

```python
"""Tests for trajectory cleaning pipeline — filter, doctor, validate."""

import json
import pytest
from pathlib import Path

from sft_datagen.cleaning import (
    filter_passed,
    doctor_trajectory,
    validate_trajectory,
)


class TestFilterPassed:
    """Filter trajectories by evaluation result."""

    def test_keeps_passed(self):
        trajectories = [
            {"task_id": "t1", "evaluation": {"passed": True}},
            {"task_id": "t2", "evaluation": {"passed": False}},
            {"task_id": "t3", "evaluation": {"passed": True}},
        ]
        result = filter_passed(trajectories)
        assert len(result) == 2
        assert [t["task_id"] for t in result] == ["t1", "t3"]

    def test_empty_list(self):
        assert filter_passed([]) == []

    def test_none_passed(self):
        trajectories = [
            {"task_id": "t1", "evaluation": {"passed": False}},
        ]
        assert filter_passed(trajectories) == []


class TestDoctorTrajectory:
    """Remove error→retry loops from trajectories."""

    def test_removes_single_error_loop(self):
        events = [
            {"event_type": "task", "role": "user", "prompt": "Fix bug"},
            # Error loop: attempt 1 fails
            {"event_type": "llm_output", "role": "assistant", "content": "Try A"},
            {"event_type": "tool_call", "role": "assistant", "tool_call_id": "c1",
             "name": "execute_python", "arguments": {"code": "bad"}, "result": {
                 "tool_call_id": "c1", "content": "status: error", "result_status": "error"}},
            {"event_type": "python_output", "role": "user", "tool_call_id": "c1",
             "stdout": "", "stderr": "", "error": "NameError", "status": "error",
             "execution_count": 1},
            # Successful attempt
            {"event_type": "llm_output", "role": "assistant", "content": "Try B"},
            {"event_type": "tool_call", "role": "assistant", "tool_call_id": "c2",
             "name": "execute_python", "arguments": {"code": "good"}, "result": {
                 "tool_call_id": "c2", "content": "status: complete", "result_status": "complete"}},
            {"event_type": "python_output", "role": "user", "tool_call_id": "c2",
             "stdout": "ok", "stderr": "", "error": "", "status": "complete",
             "execution_count": 2},
        ]
        doctored = doctor_trajectory(events)
        # Should have removed the error loop (attempt 1)
        assert len(doctored) < len(events)
        # Task event preserved
        assert doctored[0]["event_type"] == "task"
        # The successful attempt is kept
        tool_calls = [e for e in doctored if e["event_type"] == "tool_call"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["tool_call_id"] == "c2"

    def test_no_error_loops_unchanged(self):
        events = [
            {"event_type": "task", "role": "user", "prompt": "Fix bug"},
            {"event_type": "llm_output", "role": "assistant", "content": "Code"},
            {"event_type": "tool_call", "role": "assistant", "tool_call_id": "c1",
             "name": "execute_python", "arguments": {"code": "good"}, "result": {
                 "tool_call_id": "c1", "content": "status: complete", "result_status": "complete"}},
            {"event_type": "python_output", "role": "user", "tool_call_id": "c1",
             "stdout": "ok", "stderr": "", "error": "", "status": "complete",
             "execution_count": 1},
        ]
        doctored = doctor_trajectory(events)
        assert len(doctored) == len(events)

    def test_empty_events(self):
        assert doctor_trajectory([]) == []


class TestValidateTrajectory:
    """Validate well-formedness of trajectory event sequences."""

    def test_valid_trajectory(self):
        trajectory = {
            "task_id": "t1",
            "system_prompt": "You are...",
            "tools": [{"name": "execute_python"}],
            "events": [
                {"event_type": "task", "role": "user", "prompt": "Fix"},
                {"event_type": "llm_output", "role": "assistant", "content": "Done"},
            ],
            "evaluation": {"passed": True},
        }
        errors = validate_trajectory(trajectory)
        assert errors == []

    def test_missing_events(self):
        trajectory = {"task_id": "t1", "events": []}
        errors = validate_trajectory(trajectory)
        assert any("empty" in e.lower() for e in errors)

    def test_missing_task_id(self):
        trajectory = {
            "events": [
                {"event_type": "task", "role": "user", "prompt": "Fix"},
            ],
        }
        errors = validate_trajectory(trajectory)
        assert any("task_id" in e.lower() for e in errors)

    def test_orphaned_tool_call_id(self):
        """PythonOutput references a tool_call_id that doesn't exist."""
        trajectory = {
            "task_id": "t1",
            "system_prompt": "...",
            "tools": [],
            "events": [
                {"event_type": "task", "role": "user", "prompt": "Fix"},
                {"event_type": "python_output", "role": "user", "tool_call_id": "orphan",
                 "stdout": "", "stderr": "", "error": "", "status": "complete",
                 "execution_count": 1},
            ],
            "evaluation": {"passed": True},
        }
        errors = validate_trajectory(trajectory)
        assert any("orphan" in e.lower() for e in errors)
```

**Step 2: Run tests to verify they fail**

```bash
cd experiments/sft-datagen
PYTHONPATH=. pytest tests/test_cleaning.py -v
```

Expected: `ModuleNotFoundError: No module named 'sft_datagen.cleaning'`

**Step 3: Commit**

```bash
git add tests/test_cleaning.py
git commit -m "test(sft-datagen): add cleaning pipeline tests (red)"
```

---

## Task 7: Cleaning Pipeline — Implementation

**Files:**
- Create: `experiments/sft-datagen/sft_datagen/cleaning.py`

**Step 1: Implement cleaning functions**

```python
"""Trajectory cleaning pipeline — filter, doctor, validate.

Stages:
1. filter_passed: Keep only trajectories where evaluation passed
2. doctor_trajectory: Remove error→retry loops
3. validate_trajectory: Check well-formedness
"""

from __future__ import annotations

from typing import Any


def filter_passed(trajectories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stage 1: Keep only trajectories where evaluation passed."""
    return [t for t in trajectories if t.get("evaluation", {}).get("passed", False)]


def _is_error_output(event: dict[str, Any]) -> bool:
    """Check if an event represents an error output."""
    if event["event_type"] == "python_output":
        return event.get("status") == "error" or bool(event.get("error"))
    if event["event_type"] == "tool_call" and event.get("result"):
        return event["result"].get("result_status") == "error"
    return False


def doctor_trajectory(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stage 2: Remove error→retry loops from event sequences.

    Detects pattern: [llm_output → tool_call → error_output] and removes
    these failed attempts, keeping only the final successful attempt.

    Preserves the LLM output (think step) before the final successful attempt
    since it may contain the insight that led to the fix.
    """
    if not events:
        return []

    # Identify error cycles: sequences of (llm_output, tool_call, python_output[error])
    # Walk backwards from each error to find the start of the cycle
    error_ranges: list[tuple[int, int]] = []  # (start_idx, end_idx) inclusive
    i = 0
    while i < len(events):
        # Look for: llm_output → tool_call → python_output(error)
        if (
            i + 2 < len(events)
            and events[i]["event_type"] == "llm_output"
            and events[i + 1]["event_type"] == "tool_call"
            and events[i + 2]["event_type"] == "python_output"
            and _is_error_output(events[i + 2])
        ):
            error_ranges.append((i, i + 2))
            i += 3
        else:
            i += 1

    if not error_ranges:
        return events

    # Remove error ranges
    remove_indices = set()
    for start, end in error_ranges:
        for idx in range(start, end + 1):
            remove_indices.add(idx)

    return [e for idx, e in enumerate(events) if idx not in remove_indices]


def validate_trajectory(trajectory: dict[str, Any]) -> list[str]:
    """Stage 4: Check well-formedness of a trajectory.

    Returns list of error strings. Empty list = valid.
    """
    errors = []

    if not trajectory.get("task_id"):
        errors.append("Missing task_id")

    events = trajectory.get("events", [])
    if not events:
        errors.append("Empty events list")
        return errors

    # Check tool_call_id consistency
    tool_call_ids = set()
    for event in events:
        if event["event_type"] == "tool_call":
            tool_call_ids.add(event["tool_call_id"])

    for event in events:
        if event["event_type"] == "python_output":
            tcid = event.get("tool_call_id")
            if tcid and tcid not in tool_call_ids:
                errors.append(f"Orphaned tool_call_id: {tcid}")

    return errors
```

**Step 2: Run tests to verify they pass**

```bash
cd experiments/sft-datagen
PYTHONPATH=. pytest tests/test_cleaning.py -v
```

Expected: All tests PASS.

**Step 3: Commit**

```bash
git add sft_datagen/cleaning.py
git commit -m "feat(sft-datagen): implement cleaning pipeline (filter, doctor, validate)"
```

---

## Task 8: Trajectory Dumper

The core integration piece — hooks into an Agent006 agent after task completion
and dumps the event store to the raw trajectory format.

**Files:**
- Create: `experiments/sft-datagen/sft_datagen/trajectory_dumper.py`
- Create: `experiments/sft-datagen/tests/test_trajectory_dumper.py`

**Step 1: Write failing test**

```python
"""Tests for trajectory dumper — dumps agent event store to raw trajectory file."""

import json
from pathlib import Path

import pytest

from nemo_oo_agents.events import LLMOutput, Task
from nemo_oo_agents.runtime.event_backend import InMemoryBackend
from nemo_oo_agents.runtime.event_manager import EventManager
from context_blocks.events import ToolCallEvent, ToolResult

from sft_datagen.trajectory_dumper import dump_trajectory


class TestDumpTrajectory:
    """Test dumping event manager contents to trajectory dict."""

    def _make_event_manager(self, events):
        """Helper: create EventManager and add events."""
        em = EventManager()
        for event in events:
            em.add(event)
        return em

    def test_basic_dump(self):
        em = self._make_event_manager([
            Task(prompt="Fix the bug"),
            LLMOutput(content="Let me look..."),
        ])
        result = dump_trajectory(
            event_manager=em,
            task_id="django__django-16379",
            model="Qwen/Qwen3.5-397B-A17B",
            system_prompt="You are a coding assistant.",
            tools=[{"name": "execute_python", "description": "Run code", "parameters": {}}],
            evaluation={"passed": True, "patch": "diff..."},
        )
        assert result["task_id"] == "django__django-16379"
        assert result["model"] == "Qwen/Qwen3.5-397B-A17B"
        assert result["system_prompt"] == "You are a coding assistant."
        assert len(result["events"]) == 2
        assert result["events"][0]["event_type"] == "task"
        assert result["events"][1]["event_type"] == "llm_output"
        assert result["evaluation"]["passed"] is True

    def test_dump_to_file(self, tmp_path):
        em = self._make_event_manager([
            Task(prompt="Fix bug"),
            LLMOutput(content="Done"),
        ])
        output_file = tmp_path / "trajectory.json"
        result = dump_trajectory(
            event_manager=em,
            task_id="test-1",
            model="test-model",
            system_prompt="System",
            tools=[],
            evaluation={"passed": True},
            output_path=output_file,
        )
        assert output_file.exists()
        loaded = json.loads(output_file.read_text())
        assert loaded["task_id"] == "test-1"
        assert len(loaded["events"]) == 2
```

**Step 2: Run test to verify it fails**

```bash
cd experiments/sft-datagen
PYTHONPATH=. pytest tests/test_trajectory_dumper.py -v
```

Expected: FAIL (module not found)

**Step 3: Implement**

Create `experiments/sft-datagen/sft_datagen/trajectory_dumper.py`:

```python
"""Dump agent event store to raw trajectory format.

After an Agent006 agent completes a task, call dump_trajectory() with the
agent's event_manager to serialize the full event sequence to a trajectory file.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from nemo_oo_agents.runtime.event_manager import EventManager

from sft_datagen.event_serializer import serialize_events


def dump_trajectory(
    *,
    event_manager: EventManager,
    task_id: str,
    model: str,
    system_prompt: str,
    tools: list[dict[str, Any]],
    evaluation: dict[str, Any],
    output_path: Path | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dump the event manager's events to a raw trajectory dict.

    Args:
        event_manager: The agent's EventManager (has InMemoryBackend).
        task_id: SWE-bench task identifier (e.g., "django__django-16379").
        model: Model name used for generation.
        system_prompt: The initial system prompt (rendered from context blocks).
        tools: Tool definitions as dicts (OpenAI tool format).
        evaluation: Evaluation result dict (passed, patch, test_results, etc.).
        output_path: If provided, write JSON to this file.
        metadata: Additional metadata to include.

    Returns:
        The raw trajectory dict.
    """
    # Get all events from the backend
    all_events = list(event_manager.backend.all_events())
    serialized = serialize_events(all_events)

    trajectory = {
        "task_id": task_id,
        "model": model,
        "system_prompt": system_prompt,
        "tools": tools,
        "events": serialized,
        "evaluation": evaluation,
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "total_events": len(serialized),
            **(metadata or {}),
        },
    }

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(trajectory, indent=2, default=str))

    return trajectory
```

**Step 4: Run tests to verify they pass**

```bash
cd experiments/sft-datagen
PYTHONPATH=. pytest tests/test_trajectory_dumper.py -v
```

Expected: All tests PASS.

**Step 5: Commit**

```bash
git add sft_datagen/trajectory_dumper.py tests/test_trajectory_dumper.py
git commit -m "feat(sft-datagen): implement trajectory dumper with tests"
```

---

## Task 9: CLI Scripts — Generate, Clean, Convert

Wire up the library code into CLI scripts that operate on files.

**Files:**
- Create: `experiments/sft-datagen/generate/run_generation.py`
- Create: `experiments/sft-datagen/clean/run_cleaning.py`
- Create: `experiments/sft-datagen/convert/to_nemo_rl.py`

**Step 1: Create generation runner stub**

Create `experiments/sft-datagen/generate/run_generation.py`:

```python
"""Run Agent006 on SWE-bench tasks and dump trajectories.

Usage:
    python generate/run_generation.py \
        --endpoint http://localhost:8000/v1 \
        --model Qwen/Qwen3.5-397B-A17B \
        --output-dir data/trajectories/raw \
        --limit 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Generate SFT trajectories from SWE-bench")
    parser.add_argument("--endpoint", required=True, help="OpenAI-compatible model endpoint URL")
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument("--output-dir", default="data/trajectories/raw", help="Output directory")
    parser.add_argument("--limit", type=int, default=None, help="Max tasks to run")
    parser.add_argument("--dataset", default="princeton-nlp/SWE-bench_Verified",
                       help="HuggingFace dataset name")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # TODO: Integration with Agent006 framework
    # 1. Load SWE-bench tasks from HuggingFace
    # 2. For each task:
    #    a. Create Agent006 agent with SWE-bench tools
    #    b. Configure LLM client to point at --endpoint/--model
    #    c. Run agent on task
    #    d. Capture system prompt from initial context render
    #    e. Run SWE-bench evaluation (pass/fail + patch)
    #    f. Call dump_trajectory() to save raw trajectory
    # 3. Print summary statistics

    print(f"Generation runner stub")
    print(f"  Endpoint: {args.endpoint}")
    print(f"  Model: {args.model}")
    print(f"  Output: {output_dir}")
    print(f"  Limit: {args.limit}")
    print(f"  Dataset: {args.dataset}")
    print()
    print("TODO: Wire up Agent006 SWE-bench integration")
    print("See: evaluation/adapters/swebench.py")
    print("See: evaluation/environments/swebench.py")
    print("See: evaluation/runner.py")


if __name__ == "__main__":
    main()
```

**Step 2: Create cleaning CLI**

Create `experiments/sft-datagen/clean/run_cleaning.py`:

```python
"""Run cleaning pipeline on raw trajectories.

Usage:
    python clean/run_cleaning.py \
        --input-dir data/trajectories/raw \
        --output-dir data/trajectories/cleaned \
        --doctor
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Add parent dir to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from sft_datagen.cleaning import filter_passed, doctor_trajectory, validate_trajectory


def main():
    parser = argparse.ArgumentParser(description="Clean SFT trajectories")
    parser.add_argument("--input-dir", required=True, help="Directory with raw trajectory JSON files")
    parser.add_argument("--output-dir", required=True, help="Directory for cleaned trajectories")
    parser.add_argument("--doctor", action="store_true", help="Remove error→retry loops")
    parser.add_argument("--report", default=None, help="Path for cleaning report (markdown)")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load all trajectories
    trajectories = []
    for f in sorted(input_dir.glob("*.json")):
        trajectories.append(json.loads(f.read_text()))

    print(f"Loaded {len(trajectories)} raw trajectories")

    # Stage 1: Filter
    passed = filter_passed(trajectories)
    print(f"After filter_passed: {len(passed)} / {len(trajectories)}")

    # Stage 2: Doctor (optional)
    if args.doctor:
        for t in passed:
            original_count = len(t["events"])
            t["events"] = doctor_trajectory(t["events"])
            removed = original_count - len(t["events"])
            if removed > 0:
                print(f"  {t['task_id']}: removed {removed} events (error loops)")

    # Stage 3: Validate
    valid = []
    for t in passed:
        errors = validate_trajectory(t)
        if errors:
            print(f"  INVALID {t['task_id']}: {errors}")
        else:
            valid.append(t)
    print(f"After validation: {len(valid)} / {len(passed)}")

    # Write cleaned trajectories
    for t in valid:
        output_file = output_dir / f"{t['task_id']}.json"
        output_file.write_text(json.dumps(t, indent=2, default=str))

    print(f"Wrote {len(valid)} cleaned trajectories to {output_dir}")

    # Generate report
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = f"""# Cleaning Report

- **Input**: {len(trajectories)} raw trajectories from `{input_dir}`
- **After filter (passed)**: {len(passed)}
- **After validation**: {len(valid)}
- **Doctor mode**: {'enabled' if args.doctor else 'disabled'}
- **Output**: `{output_dir}`
"""
        report_path.write_text(report)
        print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
```

**Step 3: Create NeMo RL conversion CLI**

Create `experiments/sft-datagen/convert/to_nemo_rl.py`:

```python
"""Convert cleaned trajectories to NeMo RL OpenAI format JSONL.

Usage:
    python convert/to_nemo_rl.py \
        --input-dir data/trajectories/cleaned \
        --output data/datasets/sft_dataset.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from sft_datagen.nemo_converter import events_to_nemo_rl


def main():
    parser = argparse.ArgumentParser(description="Convert trajectories to NeMo RL format")
    parser.add_argument("--input-dir", required=True, help="Directory with cleaned trajectory JSONs")
    parser.add_argument("--output", required=True, help="Output JSONL file path")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    converted = 0
    errors = 0

    with open(output_path, "w") as out:
        for f in sorted(input_dir.glob("*.json")):
            trajectory = json.loads(f.read_text())
            try:
                nemo_record = events_to_nemo_rl(trajectory)
                out.write(json.dumps(nemo_record) + "\n")
                converted += 1
            except Exception as e:
                print(f"  ERROR {f.name}: {e}")
                errors += 1

    print(f"Converted {converted} trajectories to NeMo RL format")
    if errors:
        print(f"  {errors} errors")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
```

**Step 4: Commit**

```bash
git add generate/run_generation.py clean/run_cleaning.py convert/to_nemo_rl.py
git commit -m "feat(sft-datagen): add CLI scripts for generate, clean, convert"
```

---

## Task 10: Model Hosting Scripts

**Files:**
- Create: `experiments/sft-datagen/model/test_endpoint.py`

**Step 1: Create endpoint test script**

```python
"""Test that a model endpoint is serving and responds correctly.

Usage:
    python model/test_endpoint.py --endpoint http://localhost:8000/v1 --model Qwen/Qwen3.5-397B-A17B
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)


def test_endpoint(endpoint: str, model: str) -> bool:
    """Send a simple chat completion request and check the response."""
    url = f"{endpoint}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'hello' and nothing else."},
        ],
        "max_tokens": 10,
        "temperature": 0,
    }

    print(f"Testing endpoint: {url}")
    print(f"Model: {model}")

    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        print(f"Response: {content}")
        print("Endpoint is working!")
        return True
    except Exception as e:
        print(f"ERROR: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test model endpoint")
    parser.add_argument("--endpoint", required=True, help="OpenAI-compatible endpoint URL")
    parser.add_argument("--model", required=True, help="Model name")
    args = parser.parse_args()

    success = test_endpoint(args.endpoint, args.model)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
```

**Step 2: Commit**

```bash
git add model/test_endpoint.py
git commit -m "feat(sft-datagen): add model endpoint test script"
```

---

## Task 11: End-to-End Integration Test

Create a test with mock data that exercises the full pipeline:
serialize events → dump trajectory → clean → convert to NeMo RL.

**Files:**
- Create: `experiments/sft-datagen/tests/test_e2e.py`

**Step 1: Write the test**

```python
"""End-to-end test: event store → raw trajectory → clean → NeMo RL format."""

import json
from pathlib import Path

from nemo_oo_agents.events import Error, LLMOutput, PythonOutput, Task
from nemo_oo_agents.runtime.event_manager import EventManager
from context_blocks.events import ToolCallEvent, ToolResult

from sft_datagen.trajectory_dumper import dump_trajectory
from sft_datagen.cleaning import filter_passed, doctor_trajectory, validate_trajectory
from sft_datagen.nemo_converter import events_to_nemo_rl


def _build_event_manager():
    """Build an EventManager with a realistic SWE-bench-like conversation."""
    em = EventManager()

    # Task
    em.add(Task(prompt="Fix the bug in django/db/models/query.py where filter() ignores Q objects"))

    # First attempt (error)
    em.add(LLMOutput(content="Let me look at the QuerySet implementation."))
    em.add(ToolCallEvent(
        tool_call_id="call_1",
        name="execute_python",
        arguments={"code": "import django\nprint(django.get_version())"},
        result=ToolResult(tool_call_id="call_1", content="status: error", result_status="error"),
    ))
    em.add(PythonOutput(
        tool_call_id="call_1",
        status="error",
        execution_count=1,
        stdout="",
        stderr="",
        error="ModuleNotFoundError: No module named 'django'",
    ))

    # Second attempt (success)
    em.add(LLMOutput(content="I need to read the file directly."))
    em.add(ToolCallEvent(
        tool_call_id="call_2",
        name="execute_python",
        arguments={"code": "with open('django/db/models/query.py') as f:\n    print(f.read()[:500])"},
        result=ToolResult(tool_call_id="call_2", content="status: complete"),
    ))
    em.add(PythonOutput(
        tool_call_id="call_2",
        status="complete",
        execution_count=2,
        stdout="class QuerySet:\n    def filter(self, *args, **kwargs):\n        ...",
    ))

    # Fix
    em.add(LLMOutput(content="I see the issue. The filter method needs to handle Q objects."))
    em.add(ToolCallEvent(
        tool_call_id="call_3",
        name="execute_python",
        arguments={"code": "# Apply the fix\nprint('Fix applied')"},
        result=ToolResult(tool_call_id="call_3", content="status: complete"),
    ))
    em.add(PythonOutput(
        tool_call_id="call_3",
        status="complete",
        execution_count=3,
        stdout="Fix applied",
    ))

    return em


class TestEndToEnd:
    """Full pipeline: event store → trajectory → clean → NeMo RL."""

    def test_full_pipeline(self, tmp_path):
        # Phase 1: Dump trajectory
        em = _build_event_manager()
        trajectory = dump_trajectory(
            event_manager=em,
            task_id="django__django-16379",
            model="Qwen/Qwen3.5-397B-A17B",
            system_prompt="You are a software engineer. Fix the described issue.",
            tools=[
                {"name": "execute_python", "description": "Execute Python code", "parameters": {"type": "object", "properties": {"code": {"type": "string"}}}},
            ],
            evaluation={"passed": True, "patch": "diff --git a/django/db/models/query.py ..."},
            output_path=tmp_path / "raw" / "django__django-16379.json",
        )

        # Verify raw trajectory
        assert trajectory["task_id"] == "django__django-16379"
        assert len(trajectory["events"]) == 10  # 1 task + 3*(llm + tool_call + python_output)

        # Phase 2: Clean
        passed = filter_passed([trajectory])
        assert len(passed) == 1

        doctored_events = doctor_trajectory(passed[0]["events"])
        # Should remove the error loop (first attempt: llm + tool_call + python_output = 3 events)
        assert len(doctored_events) < len(trajectory["events"])
        passed[0]["events"] = doctored_events

        errors = validate_trajectory(passed[0])
        assert errors == []

        # Phase 3: Convert to NeMo RL
        nemo_record = events_to_nemo_rl(passed[0])

        # Verify NeMo RL format
        messages = nemo_record["messages"]
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "assistant"
        assert nemo_record["tools"][0]["name"] == "execute_python"

        # Verify it's valid JSON
        json_str = json.dumps(nemo_record)
        reloaded = json.loads(json_str)
        assert reloaded["messages"][0]["role"] == "system"

    def test_failed_trajectory_filtered_out(self):
        em = EventManager()
        em.add(Task(prompt="Fix bug"))
        em.add(LLMOutput(content="I can't fix this"))

        trajectory = dump_trajectory(
            event_manager=em,
            task_id="failed-task",
            model="test",
            system_prompt="System",
            tools=[],
            evaluation={"passed": False},
        )

        passed = filter_passed([trajectory])
        assert len(passed) == 0
```

**Step 2: Run the test**

```bash
cd experiments/sft-datagen
PYTHONPATH=. pytest tests/test_e2e.py -v
```

Expected: All tests PASS.

**Step 3: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test(sft-datagen): add end-to-end pipeline integration test"
```

---

## Summary

| Task | Description | Dependencies |
|------|-------------|-------------|
| 1 | Directory scaffold + README | None |
| 2 | Event serializer tests (red) | Task 1 |
| 3 | Event serializer implementation (green) | Task 2 |
| 4 | NeMo RL converter tests (red) | Task 3 |
| 5 | NeMo RL converter implementation (green) | Task 4 |
| 6 | Cleaning pipeline tests (red) | Task 3 |
| 7 | Cleaning pipeline implementation (green) | Task 6 |
| 8 | Trajectory dumper + tests | Task 3 |
| 9 | CLI scripts (generate, clean, convert) | Tasks 5, 7, 8 |
| 10 | Model endpoint test script | None |
| 11 | End-to-end integration test | Tasks 5, 7, 8 |

**Parallelizable:** Tasks 4-5 (converter) and 6-7 (cleaning) can run in parallel after Task 3.

**Not yet implemented (requires exploration):**
- `run_generation.py` integration with Agent006 SWE-bench adapter (Task 9 is a stub)
- NIM deployment script for DGX/Slurm (Task 10 is endpoint test only)
- NeMo RL training config validation
- Multiple rollouts and quality scoring

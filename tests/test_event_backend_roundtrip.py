"""Backend round-trip tests — parametrized over InMemoryBackend and SQLiteEventBackend.

Every event type that can be stored in the event manager must survive a
serialize → deserialize round-trip with:
  - correct Python type (e.g. ToolCallEvent, not Metadata)
  - correct _role ClassVar
  - correct event_type string
  - key field values intact

These tests will FAIL for SQLiteEventBackend until ToolCallEvent (and any other
context_blocks event types) are added to the SQLiteEventBackend type registry
(_CORE_TYPES in storage/sqlite.py).

Why this matters: when a type is missing from the registry, SQLiteEventBackend falls
back to deserializing as Metadata (Role.METADATA). The provider formatter silently
skips Metadata-role blocks, so ToolCallEvents simply vanish from LLM context —
the LLM never sees the code it wrote.
"""

from __future__ import annotations

import sqlite3

import pytest

from context_blocks import ToolCallEvent
from context_blocks.events import AssistantEvent, UserEvent
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
from nemo_oo_agents.runtime.event_backend import InMemoryBackend
from nemo_oo_agents.storage.sqlite import SQLiteEventBackend

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=["memory", "sqlite"])
def backend(request):
    """Parametrized fixture: yields either InMemoryBackend or SQLiteEventBackend."""
    if request.param == "memory":
        yield InMemoryBackend()
    else:
        conn = sqlite3.connect(":memory:")
        # Create the events table the backend needs
        conn.executescript("""\
            CREATE TABLE IF NOT EXISTS events (
                tag TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                data TEXT NOT NULL,
                insertion_order INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS active_tags (
                position INTEGER NOT NULL,
                tag TEXT NOT NULL UNIQUE
            );
        """)
        yield SQLiteEventBackend(conn)
        conn.close()


# ---------------------------------------------------------------------------
# Canonical event instances — one per storable type
# ---------------------------------------------------------------------------

# (tag, event_instance, expected_type, expected_role)
_ALL_EVENTS = [
    # --- context_blocks types ---
    (
        "1",
        UserEvent(content="hello from user"),
        UserEvent,
        Role.USER,
    ),
    (
        "2",
        AssistantEvent(content="hello from assistant"),
        AssistantEvent,
        Role.ASSISTANT,
    ),
    (
        "3",
        ToolCallEvent(
            tool_call_id="tc-001",
            name="execute_python",
            arguments={"code": "x = 42"},
        ),
        ToolCallEvent,
        Role.ASSISTANT,
    ),
    # --- nemo_oo_agents types ---
    (
        "4",
        Task(prompt="Do the thing"),
        Task,
        Role.USER,
    ),
    (
        "5",
        Message(content="Here is the answer"),
        Message,
        Role.ASSISTANT,
    ),
    (
        "6",
        Reasoning(content="Let me think step by step"),
        Reasoning,
        Role.ASSISTANT,
    ),
    (
        "7",
        Error(content="NameError: name 'x' is not defined"),
        Error,
        Role.USER,
    ),
    (
        "8",
        Feedback(content="Code executed successfully"),
        Feedback,
        Role.USER,
    ),
    (
        "9",
        LLMOutput(content="result = compute()"),
        LLMOutput,
        Role.ASSISTANT,
    ),
    (
        "10",
        PythonOutput(
            tool_call_id="tc-001",
            execution_status="complete",
            execution_count=1,
            stdout="42\n",
        ),
        PythonOutput,
        Role.USER,
    ),
    (
        "11",
        Summary(
            summary_tag="2..10",
            replaced_range=(2, 10),
            summary_text="Agent explored the filesystem.",
        ),
        Summary,
        Role.ASSISTANT,
    ),
    (
        "12",
        BeforeTurn(
            method_name="respond",
            strategy="CodeActStrategy",
            generation_id="gen-001",
            turn_number=1,
        ),
        BeforeTurn,
        Role.RUNTIME_EVENT,
    ),
    (
        "13",
        AfterTurn(
            method_name="respond",
            strategy="CodeActStrategy",
            generation_id="gen-001",
            turn_number=1,
            is_final=False,
        ),
        AfterTurn,
        Role.RUNTIME_EVENT,
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag,event,expected_type,expected_role", _ALL_EVENTS,
                         ids=[e[0] for e in _ALL_EVENTS])
def test_event_roundtrip_type_preserved(backend, tag, event, expected_type, expected_role):
    """store() then get() returns the correct concrete type, not Metadata."""
    backend.store(tag, event)
    retrieved = backend.get(tag)

    assert retrieved is not None, f"get({tag!r}) returned None"
    assert type(retrieved) is expected_type, (
        f"Expected {expected_type.__name__}, got {type(retrieved).__name__}. "
        f"event_type={event.event_type!r} was not registered in the backend's type registry."
    )


@pytest.mark.parametrize("tag,event,expected_type,expected_role", _ALL_EVENTS,
                         ids=[e[0] for e in _ALL_EVENTS])
def test_event_roundtrip_role_preserved(backend, tag, event, expected_type, expected_role):
    """store() then get() returns an event whose _role ClassVar is correct.

    This is critical: if a ToolCallEvent deserializes as Metadata, _role becomes
    Role.METADATA and the formatter silently skips it — the LLM loses the tool call history.
    """
    backend.store(tag, event)
    retrieved = backend.get(tag)

    assert retrieved is not None
    actual_role = getattr(retrieved, "_role", None)
    assert actual_role == expected_role, (
        f"After round-trip, {expected_type.__name__}._role is {actual_role!r}, "
        f"expected {expected_role!r}. "
        f"Type was likely deserialized as {type(retrieved).__name__}."
    )


@pytest.mark.parametrize("tag,event,expected_type,expected_role", _ALL_EVENTS,
                         ids=[e[0] for e in _ALL_EVENTS])
def test_event_roundtrip_via_all_events(backend, tag, event, expected_type, expected_role):
    """all_events() (used by EventManager.filter()) also returns correct types.

    This is the actual code path used at context-building time.
    """
    backend.store(tag, event)
    all_ev = list(backend.all_events())

    assert len(all_ev) == 1
    retrieved = all_ev[0]
    assert type(retrieved) is expected_type, (
        f"all_events() returned {type(retrieved).__name__}, expected {expected_type.__name__}. "
        f"event_type={event.event_type!r}"
    )


def test_tool_call_event_result_preserved_after_update(backend):
    """ToolCallEvent.result (added via update) survives a round-trip.

    In production, ToolCallEvent is stored first (no result), then updated
    with the execution result. Both the initial store and the update must
    serialize/deserialize correctly.
    """
    from context_blocks import ResultStatus, ToolResult

    event = ToolCallEvent(
        tool_call_id="tc-upd",
        name="execute_python",
        arguments={"code": "x = 1"},
    )
    backend.store("upd", event)

    # Simulate the update that CodeActStrategy does after execution
    result = ToolResult(
        tool_call_id="tc-upd",
        content="",
        result_status=ResultStatus.COMPLETE,
    )
    backend.update("upd", result=result)

    retrieved = backend.get("upd")
    assert type(retrieved) is ToolCallEvent, (
        f"After update, expected ToolCallEvent but got {type(retrieved).__name__}"
    )
    assert retrieved.result is not None, "result should be set after update"
    assert retrieved.result.result_status == ResultStatus.COMPLETE


def test_all_registered_types_have_unique_event_type_keys(backend):
    """Sanity check: no two event types share the same event_type string.

    If two types have the same event_type, only one would be in the registry —
    a silent collision causing wrong-type deserialization.
    """
    event_type_to_cls: dict[str, type] = {}
    for _tag, event, expected_type, _role in _ALL_EVENTS:
        et = event.event_type
        if et in event_type_to_cls:
            existing = event_type_to_cls[et]
            if existing is not expected_type:
                pytest.fail(
                    f"event_type {et!r} is shared by {existing.__name__} and {expected_type.__name__}"
                )
        event_type_to_cls[et] = expected_type

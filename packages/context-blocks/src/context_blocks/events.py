"""Typed event models with discriminated unions.

Events represent conversation items: user messages, assistant responses,
tool calls, and tool results.

Rendering: Events are rendered using pformat(event). Fields with repr=False
are excluded from LLM context.

Each event class has:
- event_type: Literal field for discriminator (repr=False, excluded from display)
- id: Unique identifier (repr=False, excluded from display)
- metadata: Arbitrary metadata dict (repr=False, excluded from display)
- _role: ClassVar for provider role (USER/ASSISTANT/TOOL)
- tag: Event position (e.g., '5' or '2..40'), set by EventManager
- timestamp: Creation time
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from context_blocks.roles import Role

# === Enums ===


class EventStatus(StrEnum):
    """Status of an event in the event manager."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ResultStatus(StrEnum):
    """Status of a tool result."""

    COMPLETE = "complete"
    ERROR = "error"


# === Base Event ===


class EventBase(BaseModel):
    """Base class for all events.

    Subclasses define:
    - event_type: Literal field for union discriminator (repr=False)
    - _role: ClassVar for provider role
    - Public fields which are rendered via pformat()
    """

    _role: ClassVar[Role] = Role.USER

    # Discriminator field - excluded from repr
    event_type: str = Field(default="event", repr=False, description="Event type discriminator")

    # Fields excluded from repr (not shown to LLM)
    id: str = Field(
        default_factory=lambda: str(uuid4()),
        repr=False,
        description="Unique UUID for this event",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        repr=False,
        description="Arbitrary metadata (call_id, source, etc.)",
    )
    status: EventStatus = Field(default=EventStatus.ACTIVE, repr=False, description="Active or archived")
    tag: str | None = Field(
        default=None,
        repr=False,
        description="Positional tag assigned by EventManager ('1', '2', '2..40')",
    )
    timestamp: datetime = Field(default_factory=datetime.now, repr=False, description="Creation time")


# === Typed Events ===


class UserEvent(EventBase):
    """User message event."""

    event_type: Literal["user_message"] = Field(default="user_message", repr=False)
    _role: ClassVar[Role] = Role.USER

    content: Annotated[str, Field(description="Message content")]


class AssistantEvent(EventBase):
    """Assistant response event."""

    event_type: Literal["assistant_message"] = Field(default="assistant_message", repr=False)
    _role: ClassVar[Role] = Role.ASSISTANT

    content: Annotated[str, Field(description="Response content from the assistant")]


class ToolResult(BaseModel):
    """Result of a tool call, stored as child of ToolCallEvent.

    This is a nested model (not an event) used for the unified tool call pattern.
    One ToolCallEvent with a nested ToolResult provides both call and result in a
    single event.
    """

    tool_call_id: Annotated[str, Field(description="ID of the tool call this is a result for")]
    content: Annotated[str, Field(description="Result content from the tool")]
    result_status: ResultStatus = Field(default=ResultStatus.COMPLETE, description="Execution status")


class ToolCallEvent(EventBase):
    """Tool invocation event with optional nested result.

    The nested `result` field enables a unified tool call pattern where one
    event represents both the call and its result.

    Benefits:
    - Structurally impossible to orphan call or result
    - Archive one event = archive the whole interaction
    - Access result via self.events[n].result
    """

    event_type: Literal["tool_call"] = Field(default="tool_call", repr=False)
    _role: ClassVar[Role] = Role.ASSISTANT

    tool_call_id: Annotated[str, Field(description="Unique identifier for this tool call")]
    name: Annotated[str, Field(description="Name of the tool being called")]
    arguments: Annotated[dict[str, Any], Field(description="Arguments passed to the tool")]

    # Nested result (filled after execution via EventManager.update())
    result: ToolResult | None = Field(default=None, description="Result of the tool call, added after execution")


# === Metadata Extension Point ===


class Metadata(EventBase):
    """Base class for stored metadata events.

    Subclass to attach arbitrary structured data to a session that
    persists to storage but is never included in LLM context.

    The core ``Event`` union does not include ``Metadata`` subtypes.
    Each consumer registers their subtypes with the event manager via
    ``event_manager.register_event_type(MyMetadata)``.  The event manager
    then uses registered types for deserialization; unknown subtypes fall
    back to plain ``Metadata`` (fields preserved in the raw JSON column).

    Example::

        class TUISessionStart(Metadata):
            event_type: Literal["tui_session_start"] = "tui_session_start"
            model: str = ""
            working_dir: str = ""
    """

    model_config = {"extra": "allow"}

    event_type: str = Field(default="metadata", repr=False)
    _role: ClassVar[Role] = Role.METADATA


# === Discriminated Union ===

Event = Annotated[
    UserEvent | AssistantEvent | ToolCallEvent,
    Field(discriminator="event_type"),
]

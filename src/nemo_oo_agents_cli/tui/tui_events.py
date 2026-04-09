"""TUI-specific metadata events stored in the per-session SQLite DB.

These are ``Metadata`` subclasses — persisted to storage but never shown
to the LLM.  Register them with the event manager at startup:

    agent.event_manager.register_event_type(TUISessionStart)
    agent.event_manager.register_event_type(TUISessionRename)
    agent.event_manager.register_event_type(TUIUserInput)
"""


from typing import ClassVar, Literal

from pydantic import Field

from context_blocks import Metadata
from context_blocks.roles import Role


class TUISessionStart(Metadata):
    """Session metadata written once when the TUI starts."""

    event_type: Literal["tui_session_start"] = Field(default="tui_session_start", repr=False)
    _role: ClassVar[Role] = Role.METADATA

    model: str = ""
    agent_cls: str = ""
    working_dir: str = ""


class TUISessionRename(Metadata):
    """Written when a session is renamed (auto or user)."""

    event_type: Literal["tui_session_rename"] = Field(default="tui_session_rename", repr=False)
    _role: ClassVar[Role] = Role.METADATA

    name: str = ""
    user_named: bool = False


class TUIUserInput(Metadata):
    """The raw text the user typed at the TUI prompt."""

    event_type: Literal["tui_user_input"] = Field(default="tui_user_input", repr=False)
    _role: ClassVar[Role] = Role.METADATA

    text: str = ""


class TUIAgentMessage(Metadata):
    """A Markdown message sent by the agent to the user via self.message()."""

    event_type: Literal["tui_agent_message"] = Field(default="tui_agent_message", repr=False)
    _role: ClassVar[Role] = Role.METADATA

    content: str = ""


# All TUI event types — pass each to event_manager.register_event_type()
TUI_EVENT_TYPES: tuple[type[Metadata], ...] = (
    TUISessionStart,
    TUISessionRename,
    TUIUserInput,
    TUIAgentMessage,
)

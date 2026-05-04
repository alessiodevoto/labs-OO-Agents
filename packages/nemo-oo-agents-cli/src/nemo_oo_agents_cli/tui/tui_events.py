# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""TUI-specific metadata events stored in the per-session SQLite DB.

These are ``Metadata`` subclasses — persisted to storage but never shown
to the LLM.  Each type is **auto-registered** in the global
``_EVENT_REGISTRY`` via ``EventBase.__pydantic_init_subclass__``, so
manual ``register_event_type()`` calls are no longer needed.
"""

from typing import ClassVar

from nemo_oo_agents.context_blocks import Metadata
from nemo_oo_agents.context_blocks.roles import Role


class TUISessionStart(Metadata):
    """Session metadata written once when the TUI starts."""

    _role: ClassVar[Role] = Role.METADATA

    model: str = ""
    agent_cls: str = ""
    working_dir: str = ""


class TUISessionRename(Metadata):
    """Written when a session is renamed (auto or user)."""

    _role: ClassVar[Role] = Role.METADATA

    name: str = ""
    user_named: bool = False


class TUIUserInput(Metadata):
    """The raw text the user typed at the TUI prompt."""

    _role: ClassVar[Role] = Role.METADATA

    text: str = ""


class TUIAgentMessage(Metadata):
    """A Markdown message sent by the agent to the user via self.message()."""

    _role: ClassVar[Role] = Role.METADATA

    content: str = ""


# All TUI event types (auto-registered; kept as a convenience tuple)
TUI_EVENT_TYPES: tuple[type[Metadata], ...] = (
    TUISessionStart,
    TUISessionRename,
    TUIUserInput,
    TUIAgentMessage,
)

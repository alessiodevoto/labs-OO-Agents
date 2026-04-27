# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Agent snapshot — intermediate representation of serializable agent state.

``AgentSnapshot`` captures everything needed to save/restore an agent.
Pydantic models provide validation and JSON serialization out of the box.
"""

import logging
from typing import Any, Final, Literal

from pydantic import BaseModel, field_serializer, field_validator

from context_blocks import DynamicContext
from nemo_oo_agents.errors.storage import SerializationError
from nemo_oo_agents.storage.markers import is_nosnapshot_field, is_nosnapshot_value
from nemo_oo_agents.storage.serialization import SKIP, deserialize, serialize

SNAPSHOT_VERSION: Final = 2

logger = logging.getLogger(__name__)


class StaticContextBlock(BaseModel):
    """A static context block with a JSON-serializable value."""

    key: str
    type: Literal["static"] = "static"
    value: Any = None


class DynamicContextBlock(BaseModel):
    """A dynamic context block with a Python expression string."""

    key: str
    type: Literal["dynamic"] = "dynamic"
    expr: str


class EventManagerState(BaseModel):
    """Serializable subset of EventManager state."""

    next_tag_num: int = 1


class AgentSnapshot(BaseModel):
    """Intermediate representation of serializable agent state.

    Captures everything needed to restore an agent to a prior state.
    Uses Pydantic for validation and JSON serialization.
    """

    version: int = SNAPSHOT_VERSION
    context: list[StaticContextBlock | DynamicContextBlock] = []
    event_manager: EventManagerState = EventManagerState()
    attributes: dict[str, Any] = {}
    type_allowlist: set[str] = set()

    @field_serializer("type_allowlist")
    @classmethod
    def _serialize_allowlist(cls, v: set[str]) -> list[str]:
        return sorted(v)

    @field_validator("type_allowlist", mode="before")
    @classmethod
    def _validate_allowlist(cls, v: Any) -> set[str]:
        if isinstance(v, (list, tuple)):
            return set(v)
        return v

    @staticmethod
    def from_agent(agent: Any) -> "AgentSnapshot":
        """Extract serializable state from an agent.

        Args:
            agent: An Agent instance.

        Returns:
            An AgentSnapshot capturing the agent's current state.

        Raises:
            SerializationError: If a context block value or user attribute is
                not JSON-serializable.
        """
        all_allowlist: set[str] = set()

        context_blocks: list[StaticContextBlock | DynamicContextBlock] = []
        protected = agent.context_manager.protected_keys
        for key, value in agent.context_manager._raw_items():
            if key in protected:
                continue  # Framework blocks are recreated by __init__
            if isinstance(value, DynamicContext):
                context_blocks.append(DynamicContextBlock(key=key, expr=value.expr))
            else:
                try:
                    serialized, allowlist = serialize(value)
                    all_allowlist |= allowlist
                except SerializationError as exc:
                    raise SerializationError(
                        f"Context block {key!r} is not serializable: {exc}"
                    ) from exc
                context_blocks.append(StaticContextBlock(key=key, value=serialized))

        # Tag allocation lives on the backend now, so the snapshot's
        # next_tag_num is informational only — restore reads the canonical
        # value from the backend itself. We record max_tag_num()+1 here
        # so older readers still see a sensible value.
        em_state = EventManagerState(
            next_tag_num=agent.event_manager._backend.max_tag_num() + 1,
        )

        attributes: dict[str, Any] = {}
        agent_cls = type(agent)
        for attr_name, attr_value in agent.__dict__.items():
            if attr_name.startswith("_agentdoc_"):
                continue
            if is_nosnapshot_field(agent_cls, attr_name):
                continue
            if is_nosnapshot_value(attr_value):
                continue
            if callable(attr_value):
                continue
            try:
                serialized, allowlist = serialize(attr_value)
                all_allowlist |= allowlist
            except SerializationError as exc:
                raise SerializationError(
                    f"Attribute {attr_name!r} is not serializable: {exc}"
                ) from exc
            if serialized is SKIP:
                continue
            attributes[attr_name] = serialized

        return AgentSnapshot(
            version=SNAPSHOT_VERSION,
            context=context_blocks,
            event_manager=em_state,
            attributes=attributes,
            type_allowlist=all_allowlist,
        )

    def restore(self, agent: Any) -> None:
        """Restore this snapshot's state into an agent, mutating it in place.

        Note: this performs additive restoration — it does not clear
        pre-existing context blocks or attributes on the target agent.
        The expected usage is with a freshly constructed agent (via
        ``Agent.load()``), not an agent with in-progress state.

        Args:
            agent: A freshly constructed Agent instance to restore into.

        Raises:
            SerializationError: If the snapshot version doesn't match.
        """
        if self.version != SNAPSHOT_VERSION:
            raise SerializationError(
                f"Snapshot version mismatch: expected {SNAPSHOT_VERSION}, got {self.version}"
            )

        for block in self.context:
            is_protected = block.key in agent.context_manager.protected_keys
            if isinstance(block, DynamicContextBlock):
                if is_protected:
                    agent.context_manager.set_dynamic_protected(block.key, block.expr)
                else:
                    agent.context_manager.set_dynamic(block.key, block.expr)
            else:
                value = deserialize(block.value, self.type_allowlist)
                if is_protected:
                    agent.context_manager.set_protected(block.key, value)
                else:
                    agent.context_manager[block.key] = value

        # Tag allocation lives on the backend; the backend re-derives its
        # next-tag counter lazily from max_tag_num() of the stored events,
        # so a resumed session always picks up correctly. Nothing to do
        # here — the snapshot's recorded next_tag_num is informational.

        for attr_name, attr_value in self.attributes.items():
            setattr(agent, attr_name, deserialize(attr_value, self.type_allowlist))

# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Agent snapshot — intermediate representation of serializable agent state.

``AgentSnapshot`` captures everything needed to save/restore an agent.
Pydantic models provide validation and JSON serialization out of the box.
"""

from __future__ import annotations

import json
import logging
import types
from typing import Any, Final, Literal

from pydantic import BaseModel

from context_blocks import DynamicContext
from nemo_oo_agents.errors.storage import SerializationError
from nemo_oo_agents.storage.markers import is_nosnapshot_field

SNAPSHOT_VERSION: Final = 1

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
    methods: dict[str, str] = {}
    attributes: dict[str, Any] = {}

    @staticmethod
    def from_agent(agent: Any) -> AgentSnapshot:
        """Extract serializable state from an agent.

        Args:
            agent: An Agent instance.

        Returns:
            An AgentSnapshot capturing the agent's current state.

        Raises:
            SerializationError: If a context block value or user attribute is
                not JSON-serializable.
        """
        context_blocks: list[StaticContextBlock | DynamicContextBlock] = []
        for key, value in agent.context_manager._raw_items():
            if isinstance(value, DynamicContext):
                context_blocks.append(DynamicContextBlock(key=key, expr=value.expr))
            else:
                try:
                    json.dumps(value)
                except (TypeError, ValueError) as exc:
                    raise SerializationError(
                        f"Context block {key!r} is not JSON-serializable: {exc}"
                    ) from exc
                context_blocks.append(StaticContextBlock(key=key, value=value))

        em_state = EventManagerState(
            next_tag_num=agent.event_manager._next_tag_num,
        )

        # NOTE: Only source code is captured. If a method carried decorator metadata
        # (e.g. _plan_strategy via define_method), it would be lost on restore.
        # Not an issue today — registered methods come from CodeAct code cells and
        # HelperMethodManager, which produce plain undecorated functions.
        methods = dict(getattr(agent, "_defined_methods_registry", {}))

        attributes: dict[str, Any] = {}
        agent_cls = type(agent)
        for attr_name, attr_value in agent.__dict__.items():
            if attr_name.startswith("_agentdoc_"):
                continue
            if is_nosnapshot_field(agent_cls, attr_name):
                continue
            if callable(attr_value):
                continue
            try:
                json.dumps(attr_value)
            except (TypeError, ValueError) as exc:
                raise SerializationError(
                    f"Attribute {attr_name!r} is not JSON-serializable: {exc}"
                ) from exc
            attributes[attr_name] = attr_value

        return AgentSnapshot(
            version=SNAPSHOT_VERSION,
            context=context_blocks,
            event_manager=em_state,
            methods=methods,
            attributes=attributes,
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
            if isinstance(block, DynamicContextBlock):
                agent.context_manager.set_dynamic(block.key, block.expr)
            else:
                agent.context_manager[block.key] = block.value

        # Use the higher of the snapshot value and the backend's actual max tag,
        # because events may have been added after the snapshot was saved (e.g.
        # TUI session metadata events written during session close).
        agent.event_manager._next_tag_num = max(
            self.event_manager.next_tag_num,
            agent.event_manager._next_tag_num,
        )

        if self.methods:
            # SECURITY: exec() of stored source code means loading a snapshot is
            # equivalent to arbitrary code execution. This is safe when snapshots
            # come from the same process (InMemoryStorageManager) but if snapshots
            # are ever persisted to disk or transferred over the network, the source
            # must be treated as untrusted and signed/validated before restore.
            from nemo_oo_agents.strategies.generated_code import ExecutionNamespaceBuilder

            namespace = ExecutionNamespaceBuilder.build(agent)
            for method_name, method_code in self.methods.items():
                exec(  # noqa: S102
                    compile(method_code, f"<snapshot:{method_name}>", "exec"),
                    namespace,
                )
                func = namespace.get(method_name)
                if callable(func):
                    bound = types.MethodType(func, agent)
                    setattr(agent, method_name, bound)
                else:
                    logger.warning(
                        "Snapshot restore: method %r did not produce a callable", method_name
                    )
            agent._defined_methods_registry = dict(self.methods)

        for attr_name, attr_value in self.attributes.items():
            setattr(agent, attr_name, attr_value)

# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for AgentSnapshot edge cases.

Covers:
- Line 101: ``_agentdoc_``-prefixed fields are skipped in from_agent()
- Line 105: callable attributes are skipped in from_agent() removed dynamic method restoration from AgentSnapshot — methods live on
the class and are never reattached via a snapshot. Tests for the old
``snap.methods`` / ``restore()`` method-warning paths have been removed.
"""

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.errors.storage import SerializationError
from nemo_oo_agents.storage.snapshot import AgentSnapshot
from unifiedllm import FakeLLMClient


class _SimpleAgent(Agent, llm=FakeLLMClient()):
    value: int = 0


class TestAgentSnapshotFromAgent:
    """Tests for AgentSnapshot.from_agent() field filtering."""

    def test_agentdoc_prefix_fields_are_skipped(self):
        """from_agent() skips attributes whose name starts with '_agentdoc_' (line 101)."""
        agent = _SimpleAgent()
        agent.__dict__["_agentdoc_hidden"] = "should be excluded"
        agent.value = 5

        snap = AgentSnapshot.from_agent(agent)

        assert "_agentdoc_hidden" not in snap.attributes
        assert snap.attributes["value"] == 5

    def test_callable_attributes_are_skipped(self):
        """from_agent() skips callable attributes set on the instance (line 105).: direct ``agent.my_fn = lambda: None`` is now blocked by the
        Agent guard; route the callable straight into ``__dict__`` so we still
        exercise the from_agent() filter.
        """
        agent = _SimpleAgent()
        agent.__dict__["my_fn"] = lambda: None
        agent.value = 7

        snap = AgentSnapshot.from_agent(agent)

        assert "my_fn" not in snap.attributes
        assert snap.attributes["value"] == 7


class _UnsupportedThing:
    """A plain class with no serialization support."""

    pass


class TestFromAgentAttributeErrorContext:
    """Test that attribute serialization errors include the attribute name."""

    def test_attribute_serialization_error_includes_name(self):
        """from_agent() wraps attribute serialization errors with the attribute name."""
        agent = _SimpleAgent()
        agent.bad_attr = _UnsupportedThing()  # type: ignore[attr-defined]

        with pytest.raises(SerializationError, match="Attribute 'bad_attr'.*not serializable"):
            AgentSnapshot.from_agent(agent)

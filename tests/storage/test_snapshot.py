# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for AgentSnapshot edge cases.

Covers:
- Line 101: _agentdoc_ prefix fields are skipped in from_agent()
- Line 105: callable attributes are skipped in from_agent()
- Line 168: logger.warning when restored method code doesn't define a callable
"""

import logging

from nemo_oo_agents import Agent
from nemo_oo_agents.storage.snapshot import AgentSnapshot
from unifiedllm import FakeLLMClient


class _SimpleAgent(Agent, llm=FakeLLMClient()):
    value: int = 0


class TestAgentSnapshotFromAgent:
    def test_agentdoc_prefix_fields_are_skipped(self):
        """from_agent() skips attributes whose name starts with '_agentdoc_' (line 101)."""
        agent = _SimpleAgent()
        agent.__dict__["_agentdoc_hidden"] = "should be excluded"
        agent.value = 5

        snap = AgentSnapshot.from_agent(agent)

        assert "_agentdoc_hidden" not in snap.attributes
        assert snap.attributes["value"] == 5

    def test_callable_attributes_are_skipped(self):
        """from_agent() skips callable attributes set on the instance (line 105)."""
        agent = _SimpleAgent()
        agent.my_fn = lambda: None  # type: ignore[attr-defined]
        agent.value = 7

        snap = AgentSnapshot.from_agent(agent)

        assert "my_fn" not in snap.attributes
        assert snap.attributes["value"] == 7


class TestAgentSnapshotRestore:
    def test_restore_warns_when_method_code_does_not_define_callable(self, caplog):
        """restore() emits a warning when exec'd code doesn't define the expected name (line 168)."""
        agent = _SimpleAgent()

        # 'process' is the method name but the code defines 'y', not 'process'
        snap = AgentSnapshot(methods={"process": "y = 1"})

        with caplog.at_level(logging.WARNING, logger="nemo_oo_agents.storage.snapshot"):
            snap.restore(agent)

        assert any("process" in record.message for record in caplog.records)
        assert any("did not produce a callable" in record.message for record in caplog.records)

    def test_restore_does_not_set_non_callable_as_method(self):
        """After a failed restore, the method is NOT set on the agent."""
        agent = _SimpleAgent()
        snap = AgentSnapshot(methods={"process": "y = 1"})
        snap.restore(agent)

        assert not hasattr(agent, "process")

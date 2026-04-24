# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test agent with from asyncio import."""

from asyncio import gather, sleep  # noqa: F401

from nemo_oo_agents import Agent
from unifiedllm import FakeLLMClient


class AgentWithFromAsyncio(Agent, llm=FakeLLMClient()):
    """Agent with from asyncio import."""

    async def run(self):
        await sleep(0.1)
        return "done"

"""Test agent with asyncio alias."""

import asyncio as aio

from nemo_oo_agents import Agent
from unifiedllm import FakeLLMClient


class AgentAsyncioAlias(Agent, llm=FakeLLMClient()):
    """Agent with asyncio alias."""

    async def run(self):
        await aio.sleep(0.1)
        return "done"

"""Test agent with asyncio alias."""

import asyncio as aio

from agent006 import Agent
from unifiedllm import FakeLLMClient


class AgentAsyncioAlias(Agent, llm=FakeLLMClient()):
    """Agent with asyncio alias."""

    async def run(self):
        await aio.sleep(0.1)
        return "done"

"""Test agent with explicit import asyncio."""

import asyncio

from agent006 import Agent
from unifiedllm import FakeLLMClient


class AgentWithImportAsyncio(Agent, llm=FakeLLMClient()):
    """Agent with explicit import asyncio."""

    async def run(self):
        await asyncio.sleep(0.1)
        return "done"

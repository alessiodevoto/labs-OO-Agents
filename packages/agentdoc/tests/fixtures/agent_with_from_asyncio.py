"""Test agent with from asyncio import."""

from asyncio import gather, sleep  # noqa: F401

from agent006 import Agent
from unifiedllm import FakeLLMClient


class AgentWithFromAsyncio(Agent, llm=FakeLLMClient()):
    """Agent with from asyncio import."""

    async def run(self):
        await sleep(0.1)
        return "done"

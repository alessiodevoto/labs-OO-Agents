"""Test agent without any asyncio imports."""

from nemo_oo_agents import Agent
from unifiedllm import FakeLLMClient


class AgentNoAsyncioImport(Agent, llm=FakeLLMClient()):
    """Agent without any asyncio imports."""

    def run(self):
        return "done"

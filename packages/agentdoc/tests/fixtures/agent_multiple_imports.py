"""Test agent with multiple imports."""

import json  # noqa: F401
from asyncio import sleep  # noqa: F401
from datetime import datetime  # noqa: F401

from agent006 import Agent
from unifiedllm import FakeLLMClient


class AgentMultipleImports(Agent, llm=FakeLLMClient()):
    """Agent with multiple imports."""

    pass

"""Test agent with inline hide comment that shouldn't affect next import."""

import math  # noqa: F401
import sys  # agentdoc: hide  # noqa: F401

from agent006 import Agent
from unifiedllm import FakeLLMClient


class AgentHideInlineComment(Agent, llm=FakeLLMClient()):
    """Agent with inline hide comment."""

    def run(self):
        return math.sqrt(16)
